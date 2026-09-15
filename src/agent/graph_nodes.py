"""
Node functions for the Rootly diagnosis graph, plus the helpers they share.

Each node takes the current RootlyState and returns a partial update. The logic is
the former hand-written ReAct loop, split along the graph's edges:

  agent             one model turn (retry/backoff, truncation recovery, tool-call id dedup)
  nudge             the model ended its turn without calling submit_diagnosis
  tools             run investigation tool calls
  guardrail         validate submit_diagnosis against the CMDB (incl. the origin check)
  escalation_policy deterministic escalation decision, no LLM call
  human_approval    interrupt() until a human approves or downgrades an urgent escalation

Trace events are streamed live through the runtime context's on_event callback and
also stored in state, so a resumed run keeps its complete trace.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from src.agent.escalation_policy import HUMAN_APPROVE, HUMAN_DECISIONS, apply_human_decision, decide_escalation
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.tool_registry import ToolRegistry
from src.data_loader import get_alert, load_cmdb, load_incidents, load_logs
from src.models.schemas import Alert, DiagnosisPackage, Severity
from src.tools.cmdb_lookup import cmdb_lookup

DEFAULT_MODEL = "codestral-latest"
DEFAULT_MAX_STEPS = 15
# Bounds each model response. A tool call or the full diagnosis package needs well under
# 1k tokens; without a cap codestral occasionally generates without end until the read
# timeout (observed: 120s+ hangs, reproduced on every retry because temperature is 0).
MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 60
MAX_TRUNCATED_RESPONSES = 3
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
# Only these keys come from the model; escalation fields are computed after the guardrail.
SUBMIT_FIELDS = tuple(SUBMIT_TOOL["input_schema"]["properties"])

BUDGET_EXHAUSTED_NOTE = (
    "Step budget nearly exhausted: do not call any more investigation tools. "
    f"Call {SUBMIT_TOOL_NAME} now with the best diagnosis your evidence supports, "
    "lowering confidence to reflect any gaps."
)
MISSING_SUBMIT_NOTE = (
    f"You ended your turn without calling {SUBMIT_TOOL_NAME}. The diagnosis is only "
    f"delivered through that tool: call {SUBMIT_TOOL_NAME} now."
)
TRUNCATED_NOTE = (
    "Your previous response was too long and was cut off, so it was discarded. "
    "Reply with at most two short sentences of reasoning followed by the next tool call."
)

_registry = ToolRegistry()


class DiagnosisError(RuntimeError):
    """The agent could not produce a valid diagnosis package (including model/API failures)."""


@dataclass
class TraceEvent:
    step: int
    kind: str  # thought | action | observation | guardrail | note | escalation | human_decision
    content: str | None = None
    tool: str | None = None
    input: dict | None = None
    result: dict | None = None
    duration_ms: float | None = None
    is_error: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ── Guardrail ────────────────────────────────────────────────────────────

def _component_name(reference: str) -> str:
    """Accept 'payments-db' or the CMDB reference form 'CI-006 (payments-db)'."""
    match = re.search(r"\(([^)]+)\)", reference)
    return (match.group(1) if match else reference).strip().lower()


ORIGIN_CHECK_BEFORE_ALERT = timedelta(minutes=30)
ORIGIN_CHECK_AFTER_ALERT = timedelta(minutes=10)


def _unexamined_blamed_dependencies(alert: Alert, affected: str, investigated_services: set[str]) -> list[str]:
    """
    Origin check: a component is only accepted as where the failure originates once the
    agent has searched the logs of every upstream dependency (CMDB depends_on) that the
    component's own ERROR/FATAL logs around the alert name. Otherwise the agent may be
    stopping at a component that is only relaying an upstream failure.
    """
    component = next(c for c in load_cmdb() if c.name == affected)
    dependencies = [_component_name(ref) for ref in component.depends_on]
    start, end = alert.timestamp - ORIGIN_CHECK_BEFORE_ALERT, alert.timestamp + ORIGIN_CHECK_AFTER_ALERT
    blamed: dict[str, str] = {}
    for entry in load_logs():
        if entry.service != affected or entry.level.value not in ("ERROR", "FATAL"):
            continue
        if not start <= entry.timestamp <= end:
            continue
        for dependency in dependencies:
            if dependency not in investigated_services and dependency not in blamed and dependency in entry.message.lower():
                blamed[dependency] = entry.message
    if not blamed:
        return []
    quoted = "; ".join(f'{dependency} ("{message}")' for dependency, message in blamed.items())
    return [
        f"{affected}'s own error logs around the alert point at its dependencies {quoted}, but you have "
        f"not searched those dependencies' logs. Run log_search on them first. If one of them shows its "
        f"own failure, that dependency is where the problem originates and belongs in affected_component."
    ]


def check_package(
    alert: Alert,
    package_input: dict[str, Any],
    steps: int,
    elapsed: float,
    investigated_services: set[str] | None = None,
    searched_similar_incidents: bool | None = None,
) -> tuple[DiagnosisPackage | None, list[str]]:
    """
    Guardrail: the package may only reference components that exist in the CMDB and
    historical incidents that exist in the corpus, and must cite log evidence. When
    investigated_services is given, the origin check above is applied as well; when
    searched_similar_incidents is given, similar_incidents_search must have been called.
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

    if not problems:
        # Checked here, not only in the prompt: after a "call submit_diagnosis now" nudge the
        # model was observed skipping the historical search (live ALRT-001 and ALRT-004).
        workflow_problems = []
        if investigated_services is not None:
            workflow_problems += _unexamined_blamed_dependencies(alert, affected, investigated_services)
        if searched_similar_incidents is False:
            workflow_problems.append(
                "You have not searched for similar historical incidents. Call similar_incidents_search "
                "with your root-cause hypothesis first, then submit again with the matching incident IDs "
                "(or an empty list if none match)."
            )
        if workflow_problems:
            return None, workflow_problems

    if problems:
        return None, problems + [f"Valid CMDB components: {', '.join(sorted(component_names))}."]

    try:
        package = DiagnosisPackage(
            **{
                **{k: v for k, v in package_input.items() if k in SUBMIT_FIELDS},
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


# ── Model helpers ────────────────────────────────────────────────────────

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


def all_tool_schemas() -> list[dict]:
    return [to_openai_tool(t) for t in _registry.tool_definitions() + [SUBMIT_TOOL]]


def build_llm(model: str):
    """Create the Mistral chat model. Kept in one place so another LangChain provider can be swapped in."""
    if not os.getenv("MISTRAL_API_KEY"):
        raise DiagnosisError(
            "No Mistral API key found. Create a free key at https://console.mistral.ai "
            "(Experiment plan), copy .env.example to .env and set MISTRAL_API_KEY."
        )
    from langchain_mistralai import ChatMistralAI

    from src.agent import mistral_compat

    mistral_compat.apply()
    return ChatMistralAI(
        model=model,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )


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


_TOOL_CALL_ID_ALPHABET = string.ascii_letters + string.digits


def _fresh_tool_call_id() -> str:
    # Mistral requires exactly 9 alphanumeric characters.
    return "".join(secrets.choice(_TOOL_CALL_ID_ALPHABET) for _ in range(9))


def _dedupe_tool_call_ids(response: AIMessage, used_ids: set[str]) -> None:
    """
    Some models (observed with codestral-latest) reuse the same short tool_call id
    across calls — within one turn's parallel calls, or across separate turns, since
    the full conversation (all prior AIMessages) is resent every request. Either way
    Mistral's API rejects the message outright with 'Duplicate tool call id in
    assistant message'. Track every id used so far in this run and reassign a fresh
    one to any repeat, so each call is always matched to its own tool result.
    """
    for call in [*response.tool_calls, *response.invalid_tool_calls]:
        call_id = call.get("id")
        if not call_id or call_id in used_ids:
            call_id = _fresh_tool_call_id()
            while call_id in used_ids:
                call_id = _fresh_tool_call_id()
            call["id"] = call_id
        used_ids.add(call_id)


def _text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    return "\n".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in message.content
    ).strip()


def _with_note(messages: list[BaseMessage], note: str) -> tuple[list[BaseMessage], BaseMessage]:
    """
    Mistral rejects a user message directly after tool messages, so fold the note into the
    last tool result (a copy with the same id, which add_messages swaps in). Returns the
    messages to send and the message to write back to state.
    """
    last = messages[-1]
    if isinstance(last, ToolMessage):
        noted = last.model_copy(update={"content": f"{last.content}\n\n{note}"})
        return [*messages[:-1], noted], noted
    noted = HumanMessage(note, id=str(uuid.uuid4()))
    return [*messages, noted], noted


def _merge_by_id(updates: list[BaseMessage], message: BaseMessage) -> None:
    """Keep only the latest version of a message within one node's update."""
    for index, existing in enumerate(updates):
        if existing.id is not None and existing.id == message.id:
            updates[index] = message
            return
    updates.append(message)


def _emit(runtime: Runtime[RootlyContext], *events: TraceEvent) -> list[dict]:
    records = [event.to_dict() for event in events]
    on_event = runtime.context.on_event if runtime.context else None
    if on_event:
        for record in records:
            on_event(record)
    return records


def _last_ai(messages: list[BaseMessage]) -> AIMessage:
    return next(m for m in reversed(messages) if isinstance(m, AIMessage))


def _submit_calls(state: RootlyState) -> list[dict]:
    return [c for c in _last_ai(state["messages"]).tool_calls if c["name"] == SUBMIT_TOOL_NAME]


# ── Nodes ────────────────────────────────────────────────────────────────

def agent_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    step, max_steps = state["step_count"] + 1, state["max_steps"]
    if step > max_steps:
        raise DiagnosisError(f"No valid diagnosis package after {max_steps} steps.")

    messages = list(state["messages"])
    updates: list[BaseMessage] = []
    trace: list[dict] = []
    if step == max_steps:
        messages, noted = _with_note(messages, BUDGET_EXHAUSTED_NOTE)
        _merge_by_id(updates, noted)
        trace += _emit(runtime, TraceEvent(step, "note", content="Step budget reached — asking the agent to submit."))

    response = _invoke(runtime.context.llm, messages)
    usage = {
        key: state["usage"].get(key, 0) + (response.usage_metadata or {}).get(key, 0)
        for key in ("input_tokens", "output_tokens")
    }
    update: dict = {"step_count": step, "usage": usage}

    if response.response_metadata.get("finish_reason") == "length":
        # A cut-off response may hold partial tool calls, so it is never added to the
        # history. The note changes the next input, which breaks a deterministic loop.
        truncated = state["truncated_responses"] + 1
        if truncated > MAX_TRUNCATED_RESPONSES:
            raise DiagnosisError(
                f"Model output was cut off (finish_reason=length) {truncated} times; giving up at step {step}."
            )
        messages, noted = _with_note(messages, TRUNCATED_NOTE)
        _merge_by_id(updates, noted)
        trace += _emit(runtime, TraceEvent(step, "note", content="Model response was too long and got cut off — asking for a shorter one.", is_error=True))
        return {**update, "messages": updates, "trace": trace, "truncated_responses": truncated}

    used_ids = set(state["used_tool_call_ids"])
    _dedupe_tool_call_ids(response, used_ids)
    thought = _text(response)
    if thought:
        trace += _emit(runtime, TraceEvent(step, "thought", content=thought))
    updates.append(response)
    return {
        **update,
        "messages": updates,
        "trace": trace,
        "used_tool_call_ids": sorted(used_ids),
        "model": response.response_metadata.get("model_name", state["model"]),
    }


def nudge_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    _, noted = _with_note(list(state["messages"]), MISSING_SUBMIT_NOTE)
    trace = _emit(runtime, TraceEvent(state["step_count"], "note", content="Agent stopped without submitting — reminding it to call submit_diagnosis."))
    return {"messages": [noted], "trace": trace}


def tools_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    step = state["step_count"]
    response = _last_ai(state["messages"])
    messages: list[BaseMessage] = []
    trace: list[dict] = []
    investigated = set(state["investigated_services"])
    tool_calls = state["tool_calls"]

    for call in response.invalid_tool_calls:
        trace += _emit(runtime, TraceEvent(step, "note", content=f"Malformed arguments for {call.get('name')}: {call.get('error')}", is_error=True))
        messages.append(ToolMessage(
            content=f"Error: the arguments for {call.get('name')} were not valid JSON. Call the tool again.",
            tool_call_id=call["id"], name=call.get("name") or "unknown", status="error",
        ))

    for call in response.tool_calls:
        name, args, call_id = call["name"], call["args"], call["id"]
        if name == SUBMIT_TOOL_NAME:
            continue  # answered by the guardrail node
        tool_calls += 1
        trace += _emit(runtime, TraceEvent(step, "action", tool=name, input=dict(args)))
        record = _registry.execute(name, dict(args))
        if name == "log_search" and not record.is_error:
            investigated.add(str(args.get("service", "")).strip().lower())
        trace += _emit(runtime, TraceEvent(step, "observation", tool=name, result=record.result,
                                           duration_ms=round(record.duration_ms, 1), is_error=record.is_error))
        messages.append(ToolMessage(
            content=json.dumps(record.result, ensure_ascii=False),
            tool_call_id=call_id, name=name, status="error" if record.is_error else "success",
        ))

    return {"messages": messages, "trace": trace, "tool_calls": tool_calls, "investigated_services": sorted(investigated)}


def guardrail_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    step = state["step_count"]
    alert = get_alert(state["alert_id"])
    elapsed = time.time() - state["started_at"]
    messages: list[BaseMessage] = []
    trace: list[dict] = []
    accepted: DiagnosisPackage | None = None
    searched = any(e["kind"] == "action" and e.get("tool") == "similar_incidents_search" for e in state["trace"])

    for call in _submit_calls(state):
        package, problems = check_package(alert, call["args"], step, elapsed, set(state["investigated_services"]), searched)
        if package:
            accepted = package
            trace += _emit(runtime, TraceEvent(step, "guardrail", content="Diagnosis package passed the CMDB guardrail."))
            messages.append(ToolMessage(content="Diagnosis accepted.", tool_call_id=call["id"], name=SUBMIT_TOOL_NAME))
        else:
            trace += _emit(runtime, TraceEvent(step, "guardrail", content=" ".join(problems), is_error=True))
            messages.append(ToolMessage(
                content="Diagnosis rejected:\n- " + "\n- ".join(problems),
                tool_call_id=call["id"], name=SUBMIT_TOOL_NAME, status="error",
            ))

    update: dict = {"messages": messages, "trace": trace}
    if accepted:
        update["diagnosis"] = accepted.model_dump(mode="json")
        update["elapsed_seconds"] = round(elapsed, 2)
    return update


def escalation_policy_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    diagnosis = DiagnosisPackage.model_validate(state["diagnosis"])
    component = cmdb_lookup(diagnosis.affected_component)["component"]
    decision, reason = decide_escalation(diagnosis, len(component["depended_by"]))
    diagnosis = diagnosis.model_copy(update={
        "escalation_decision": decision,
        "escalation_reason": reason,
        "owner_team": component["owner_team"],
    })
    trace = _emit(runtime, TraceEvent(state["step_count"], "escalation", content=f"{decision.value}: {reason}"))
    return {"diagnosis": diagnosis.model_dump(mode="json"), "trace": trace}


def human_approval_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    diagnosis = DiagnosisPackage.model_validate(state["diagnosis"])
    # Nothing with side effects before interrupt(): on resume this node runs again from the top.
    answer = interrupt({
        "type": "escalation_approval",
        "alert_id": state["alert_id"],
        "component": diagnosis.affected_component,
        "owner_team": diagnosis.owner_team,
        "severity": diagnosis.severity_assessed.value,
        "confidence": diagnosis.confidence,
        "reason": diagnosis.escalation_reason,
        "options": list(HUMAN_DECISIONS),
    })
    decision, note = (answer.get("decision"), answer.get("note")) if isinstance(answer, dict) else (answer, None)
    updated = apply_human_decision(diagnosis, decision, note)

    if decision == HUMAN_APPROVE:
        content = f"Human approved the urgent escalation to {updated.owner_team}."
    else:
        content = "Human downgraded the escalation to escalate_normal (not urgent)."
    if updated.human_decision_note:
        content += f" Note: {updated.human_decision_note}"
    trace = _emit(runtime, TraceEvent(state["step_count"], "human_decision", content=content))
    return {"diagnosis": updated.model_dump(mode="json"), "trace": trace}
