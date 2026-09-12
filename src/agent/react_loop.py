"""
The ReAct loop: Thought → Action → Observation, driven by Mistral tool calling via LangChain.

Flow per step:
  1. Send the conversation (alert + all prior tool calls/results) to the model.
  2. Record the model's visible text as the agent's Thought.
  3. Execute every tool call locally and append one ToolMessage per call.
  4. Stop when the agent calls submit_diagnosis with a package that passes the
     CMDB guardrail, or fail after MAX_REACT_STEPS model turns.

The final package arrives as the arguments of a submit_diagnosis tool call, so it is
structured JSON rather than free text, and is then validated with DiagnosisPackage.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from src.agent.system_prompt import SYSTEM_PROMPT, format_alert
from src.agent.tool_registry import ToolRegistry
from src.data_loader import load_cmdb, load_incidents
from src.models.schemas import Alert, DiagnosisPackage, Severity

DEFAULT_MODEL = "mistral-large-latest"
DEFAULT_MAX_STEPS = 15
RATE_LIMIT_RETRIES = 4
RETRY_BASE_SECONDS = 2.0  # backoff: 2s, 4s, 8s, 16s — the free tier allows roughly 1 request/second

SUBMIT_TOOL_NAME = "submit_diagnosis"
SUBMIT_TOOL = {
    "name": SUBMIT_TOOL_NAME,
    "description": (
        "Deliver the final diagnosis package to L2. Call this exactly once, after the "
        "investigation has gathered enough evidence. The package is checked against the "
        "CMDB; if it is rejected, fix the listed problems and call it again."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Two or three sentence incident summary"},
            "affected_component": {"type": "string", "description": "CMDB name of the component where the root cause originates"},
            "severity_assessed": {"type": "string", "enum": [s.value for s in Severity]},
            "critical_dependencies": {"type": "array", "items": {"type": "string"}, "description": "CMDB component names involved or at risk"},
            "log_evidence": {"type": "array", "items": {"type": "string"}, "description": "Real log lines quoted as '<timestamp> <service>: <message>'"},
            "root_cause_hypothesis": {"type": "string"},
            "confidence": {"type": "number", "description": "0.0 to 1.0"},
            "escalation_recommendation": {"type": "string", "description": "Team to page and concrete next actions"},
            "similar_incidents": {"type": "array", "items": {"type": "string"}, "description": "Matching historical incident IDs"},
        },
        "required": [
            "summary", "affected_component", "severity_assessed", "critical_dependencies",
            "log_evidence", "root_cause_hypothesis", "confidence",
            "escalation_recommendation", "similar_incidents",
        ],
    },
}

BUDGET_EXHAUSTED_NOTE = (
    "Step budget nearly exhausted: do not call any more investigation tools. "
    f"Call {SUBMIT_TOOL_NAME} now with the best diagnosis your evidence supports, "
    "lowering confidence to reflect any gaps."
)
MISSING_SUBMIT_NOTE = (
    f"You ended your turn without calling {SUBMIT_TOOL_NAME}. The diagnosis is only "
    f"delivered through that tool: call {SUBMIT_TOOL_NAME} now."
)


class DiagnosisError(RuntimeError):
    """The agent could not produce a valid diagnosis package (including model/API failures)."""


@dataclass
class TraceEvent:
    step: int
    kind: str  # thought | action | observation | guardrail | note
    content: str | None = None
    tool: str | None = None
    input: dict | None = None
    result: dict | None = None
    duration_ms: float | None = None
    is_error: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class DiagnosisResult:
    alert: Alert
    diagnosis: DiagnosisPackage
    trace: list[dict]
    steps: int
    tool_calls: int
    elapsed_seconds: float
    model: str
    usage: dict[str, int] = field(default_factory=dict)


def _component_name(reference: str) -> str:
    """Accept 'payments-db' or the CMDB reference form 'CI-006 (payments-db)'."""
    match = re.search(r"\(([^)]+)\)", reference)
    return (match.group(1) if match else reference).strip().lower()


def check_package(alert: Alert, package_input: dict[str, Any], steps: int, elapsed: float) -> tuple[DiagnosisPackage | None, list[str]]:
    """
    Guardrail: the package may only reference components that exist in the CMDB and
    historical incidents that exist in the corpus, and must cite log evidence.
    """
    component_names = {c.name for c in load_cmdb()}
    incident_ids = {i.id for i in load_incidents()}
    problems: list[str] = []

    affected = _component_name(str(package_input.get("affected_component", "")))
    if affected not in component_names:
        problems.append(f"affected_component '{package_input.get('affected_component')}' is not a CMDB component.")

    dependencies = [_component_name(str(d)) for d in package_input.get("critical_dependencies", [])]
    unknown_dependencies = [d for d in dependencies if d not in component_names]
    if unknown_dependencies:
        problems.append(f"critical_dependencies contain unknown components: {', '.join(unknown_dependencies)}.")

    unknown_incidents = [i for i in package_input.get("similar_incidents", []) if i not in incident_ids]
    if unknown_incidents:
        problems.append(f"similar_incidents contain unknown incident IDs: {', '.join(unknown_incidents)}.")

    if not package_input.get("log_evidence"):
        problems.append("log_evidence is empty; quote the log lines that support the hypothesis.")

    if problems:
        return None, problems + [f"Valid CMDB components: {', '.join(sorted(component_names))}."]

    try:
        package = DiagnosisPackage(
            **{
                **package_input,
                "affected_component": affected,
                "critical_dependencies": dependencies,
                "confidence": min(max(float(package_input.get("confidence", 0.0)), 0.0), 1.0),
            },
            alert_id=alert.id,
            investigation_steps=steps,
            time_to_diagnosis_seconds=round(elapsed, 2),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        return None, [f"Package failed validation: {exc}"]
    return package, []


def to_openai_tool(definition: dict) -> dict:
    """Tool modules define schemas as {name, description, input_schema}; Mistral expects function tools."""
    return {
        "type": "function",
        "function": {
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["input_schema"],
        },
    }


def build_llm(model: str):
    """Create the Mistral chat model. Kept in one place so another LangChain provider can be swapped in."""
    if not os.getenv("MISTRAL_API_KEY"):
        raise DiagnosisError(
            "No Mistral API key found. Create a free key at https://console.mistral.ai "
            "(Experiment plan), copy .env.example to .env and set MISTRAL_API_KEY."
        )
    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(model=model, temperature=0, max_retries=2, timeout=120)


def _invoke(llm, messages: list[BaseMessage]) -> AIMessage:
    """Call the model, backing off on rate limits and turning provider errors into DiagnosisError."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return llm.invoke(messages)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (429, 500, 502, 503) and attempt < RATE_LIMIT_RETRIES:
                time.sleep(RETRY_BASE_SECONDS * 2**attempt)
                continue
            if status == 401:
                raise DiagnosisError("Mistral rejected the API key (401). Check MISTRAL_API_KEY in .env.") from exc
            if status == 429:
                raise DiagnosisError("Mistral rate limit reached (the free tier allows about 1 request/second). Wait a minute and retry.") from exc
            raise DiagnosisError(f"Mistral API error {status}: {exc.response.text[:300]}") from exc
        except httpx.RequestError as exc:
            raise DiagnosisError(f"Could not reach the Mistral API: {exc}") from exc
    raise AssertionError("unreachable")


def _text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    return "\n".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in message.content
    ).strip()


def _append_note(messages: list[BaseMessage], note: str) -> None:
    # Mistral rejects a user message directly after tool messages, so fold the note into the last tool result.
    last = messages[-1]
    if isinstance(last, ToolMessage):
        last.content = f"{last.content}\n\n{note}"
    else:
        messages.append(HumanMessage(note))


def run_diagnosis(
    alert: Alert,
    *,
    llm=None,
    on_event: Callable[[dict], None] | None = None,
    model: str | None = None,
    max_steps: int | None = None,
) -> DiagnosisResult:
    """Run the ReAct investigation for one alert. on_event receives each trace event as it happens."""
    model = model or os.getenv("MISTRAL_MODEL", DEFAULT_MODEL)
    max_steps = max_steps or int(os.getenv("MAX_REACT_STEPS", DEFAULT_MAX_STEPS))
    llm = llm or build_llm(model)

    registry = ToolRegistry()
    tools = [to_openai_tool(t) for t in registry.tool_definitions() + [SUBMIT_TOOL]]
    bound_llm = llm.bind_tools(tools)
    messages: list[BaseMessage] = [SystemMessage(SYSTEM_PROMPT), HumanMessage(format_alert(alert))]
    trace: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    tool_calls = 0
    started = time.perf_counter()

    def emit(event: TraceEvent) -> None:
        record = event.to_dict()
        trace.append(record)
        if on_event:
            on_event(record)

    for step in range(1, max_steps + 1):
        if step == max_steps:
            _append_note(messages, BUDGET_EXHAUSTED_NOTE)
            emit(TraceEvent(step, "note", content="Step budget reached — asking the agent to submit."))

        response = _invoke(bound_llm, messages)
        for key in usage:
            usage[key] += (response.usage_metadata or {}).get(key, 0)
        if response.response_metadata.get("finish_reason") == "length":
            raise DiagnosisError(f"Model output was cut off (finish_reason=length) at step {step}.")

        thought = _text(response)
        if thought:
            emit(TraceEvent(step, "thought", content=thought))
        messages.append(response)

        if not response.tool_calls and not response.invalid_tool_calls:
            _append_note(messages, MISSING_SUBMIT_NOTE)
            emit(TraceEvent(step, "note", content="Agent stopped without submitting — reminding it to call submit_diagnosis."))
            continue

        accepted: DiagnosisPackage | None = None
        for call in response.invalid_tool_calls:
            emit(TraceEvent(step, "note", content=f"Malformed arguments for {call.get('name')}: {call.get('error')}", is_error=True))
            messages.append(ToolMessage(
                content=f"Error: the arguments for {call.get('name')} were not valid JSON. Call the tool again.",
                tool_call_id=call["id"], name=call.get("name") or "unknown", status="error",
            ))

        for call in response.tool_calls:
            name, args, call_id = call["name"], call["args"], call["id"]
            if name == SUBMIT_TOOL_NAME:
                package, problems = check_package(alert, args, step, time.perf_counter() - started)
                if package:
                    accepted = package
                    emit(TraceEvent(step, "guardrail", content="Diagnosis package passed the CMDB guardrail."))
                    messages.append(ToolMessage(content="Diagnosis accepted.", tool_call_id=call_id, name=name))
                else:
                    emit(TraceEvent(step, "guardrail", content=" ".join(problems), is_error=True))
                    messages.append(ToolMessage(
                        content="Diagnosis rejected:\n- " + "\n- ".join(problems),
                        tool_call_id=call_id, name=name, status="error",
                    ))
                continue

            tool_calls += 1
            emit(TraceEvent(step, "action", tool=name, input=dict(args)))
            record = registry.execute(name, dict(args))
            emit(TraceEvent(step, "observation", tool=name, result=record.result,
                            duration_ms=round(record.duration_ms, 1), is_error=record.is_error))
            messages.append(ToolMessage(
                content=json.dumps(record.result, ensure_ascii=False),
                tool_call_id=call_id, name=name, status="error" if record.is_error else "success",
            ))

        if accepted:
            return DiagnosisResult(
                alert=alert,
                diagnosis=accepted,
                trace=trace,
                steps=step,
                tool_calls=tool_calls,
                elapsed_seconds=round(time.perf_counter() - started, 2),
                model=response.response_metadata.get("model_name", model),
                usage=usage,
            )

    raise DiagnosisError(f"No valid diagnosis package after {max_steps} steps.")
