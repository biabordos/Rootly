"""
Node functions for the Rootly diagnosis graph, plus the helpers they share.

Each node takes the current RootlyState and returns a partial update:

  orchestrator      one model turn that calls route_to_specialist to pick the next agent
  guardrail         validate submit_diagnosis against the CMDB (incl. the origin check and
                    the transitive reachability check)
  escalation_policy deterministic escalation decision, no LLM call
  human_approval    interrupt() until a human approves or downgrades an urgent escalation

The three specialist nodes live in src/agent/specialists/; they are thin wrappers over
run_specialist() below, which holds the model loop the former single agent_node used to
own (retry/backoff, truncation recovery, tool-call id dedup, tool dispatch).

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
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt
# aliased: `trace` is a local variable in six nodes below, so the bare name would shadow it.
from opentelemetry import trace as otel_trace
from pydantic import ValidationError

from src.agent.escalation_policy import HUMAN_APPROVE, HUMAN_DECISIONS, apply_human_decision, decide_escalation
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.tool_registry import ToolCallRecord, ToolRegistry
from src.data_loader import get_alert, load_cmdb, load_incidents, load_logs
from src.models.schemas import Alert, DiagnosisPackage, Severity
from src.tools.cmdb_lookup import cmdb_lookup

DEFAULT_MODEL = "codestral-latest"
# One model turn per orchestrator routing decision plus up to SPECIALIST_MAX_TURNS per
# specialist run. A clean investigation costs roughly 12 turns (orchestrator → cmdb →
# orchestrator → log → orchestrator → synthesis, two turns per specialist); the rest of
# the budget covers walking further upstream and one guardrail rejection round.
DEFAULT_MAX_STEPS = 32
# A specialist searches in its first turn and reports its analysis in the second. The
# third exists so a malformed or truncated tool call can be retried without ending the run.
SPECIALIST_MAX_TURNS = 3
# How many times the orchestrator may answer without calling route_to_specialist before
# the node falls back to the deterministic next step.
ORCHESTRATOR_ROUTE_ATTEMPTS = 2
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

SPECIALISTS = ("cmdb", "log", "synthesis")

ROUTE_TOOL_NAME = "route_to_specialist"
ROUTE_TOOL = {
    "name": ROUTE_TOOL_NAME,
    "description": (
        "Hand the investigation to one specialist. Call this exactly once per turn. "
        "Use 'cmdb' to learn a component's dependencies and blast radius, 'log' to search "
        "the logs of one or more services in a time window, and 'synthesis' to write and "
        "submit the final diagnosis package once the evidence is sufficient."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "next": {
                "type": "string",
                "enum": list(SPECIALISTS),
                "description": "Which specialist runs next",
            },
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "CMDB component names for 'cmdb', service names for 'log'. "
                    "Leave empty for 'synthesis'."
                ),
            },
            "start_time": {
                "type": "string",
                "description": "For 'log': start of the window, ISO 8601 (e.g. '2026-08-18T08:55:00Z')",
            },
            "end_time": {
                "type": "string",
                "description": "For 'log': end of the window, ISO 8601 (e.g. '2026-08-18T09:15:00Z')",
            },
            "reason": {
                "type": "string",
                "description": "One sentence: why this specialist on these targets, now",
            },
        },
        "required": ["next"],
    },
}

BUDGET_EXHAUSTED_NOTE = (
    "Step budget nearly exhausted: stop investigating. Route to 'synthesis' now so the "
    "best diagnosis your evidence supports is submitted, with confidence lowered to "
    "reflect any gaps."
)
SPECIALIST_BUDGET_NOTE = (
    "Step budget nearly exhausted: do not call any more tools. Report what your evidence "
    f"already shows, and if you hold the final package call {SUBMIT_TOOL_NAME} now."
)
MISSING_ROUTE_NOTE = (
    f"You ended your turn without calling {ROUTE_TOOL_NAME}. The investigation only moves "
    f"forward through that tool: call {ROUTE_TOOL_NAME} now with your chosen specialist."
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
    kind: str  # thought | routing | action | observation | guardrail | note | escalation | human_decision
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


def _unreachable_investigated_path(alert: Alert, affected: str, investigated_services: set[str]) -> list[str]:
    """
    Transitive guardrail: affected_component must be reachable from alert.service by walking
    the CMDB depends_on graph through components the agent has actually investigated (log_search
    called on them). The two endpoints -- alert.service and affected -- don't need their own
    investigation to count, since the origin check above and the log_evidence requirement already
    cover the final component; this only closes the gap in between. Without it, the agent could
    guess a component 3+ hops away (e.g. payments-db from a web-frontend alert) whose own logs
    happen to look guilty, without ever investigating the intermediate hops that would confirm --
    or rule out -- that the failure actually propagated that way. See docs/MOCK_DATA_README.md,
    section "Guardrail-ul tranzitiv".
    """
    start = alert.service.strip().lower()
    if start == affected:
        return []
    deps_by_name = {c.name: [_component_name(ref) for ref in c.depends_on] for c in load_cmdb()}
    visited = {start}
    frontier = [start]
    blocked: set[str] = set()
    while frontier:
        node = frontier.pop()
        for dependency in deps_by_name.get(node, []):
            if dependency == affected:
                return []
            if dependency in visited:
                continue
            if dependency in investigated_services:
                visited.add(dependency)
                frontier.append(dependency)
            else:
                blocked.add(dependency)
    hops = ", ".join(sorted(blocked - visited)) or "the components between them"
    return [
        f"No fully-investigated path from {alert.service} to {affected} in the CMDB dependency graph: "
        f"you have not run log_search on {hops}. Investigate the chain from {alert.service} down to "
        f"{affected} before submitting, or pick the component where your investigation actually stopped."
    ]


def _alerted_service_with_no_investigated_dependency(
    alert: Alert, affected: str, investigated_services: set[str]
) -> list[str]:
    """
    Stop-too-early check: when the agent blames the very service that fired the alert,
    require that it searched the logs of at least one of that service's CMDB dependencies.

    This complements the origin check above, which only fires when the component's own
    ERROR/FATAL logs name a dependency. Observed on ALRT-005: web-frontend's logs describe
    only its own symptoms ("Login page returning connection refused errors") and name no
    dependency, so the origin check stays silent while the real cause sits two hops
    upstream in auth-service.
    """
    if affected != alert.service.strip().lower():
        return []
    component = next((c for c in load_cmdb() if c.name == affected), None)
    if component is None:
        return []
    dependencies = [_component_name(ref) for ref in component.depends_on]
    if not dependencies or any(d in investigated_services for d in dependencies):
        return []
    return [
        f"affected_component is the alerted service itself ({affected}), but it has "
        f"{len(dependencies)} dependencies you have not investigated "
        f"({', '.join(sorted(dependencies))}). Search their logs before concluding the "
        f"problem originates here."
    ]


# Minimum length of a quoted fragment that still counts as traceable to a real log line.
# Calibrated against the log_evidence of real agent runs (exports/*_diagnosis.json) and the
# test fixtures: at 8 characters every genuine citation is accepted, including deliberately
# short ones like "OOMKilled", while every fabricated line tried is still rejected.
MIN_EVIDENCE_QUOTE = 8
# Models routinely substitute an em dash for the hyphen in a log message, so dashes are
# folded before comparing; otherwise a byte-perfect quote would be rejected over punctuation.
_EVIDENCE_DASHES = {"—": "-", "–": "-", "−": "-"}
_EVIDENCE_PREFIXES = ("[error]", "[fatal]", "[warn]", "[info]", "error:", "fatal:", "warn:", "info:")


def _normalise_evidence(value: Any) -> str:
    text = str(value)
    for dash, plain in _EVIDENCE_DASHES.items():
        text = text.replace(dash, plain)
    return re.sub(r"\s+", " ", text.strip().strip("\"'“”")).strip().lower()


def _matches_a_log_entry(text: str, entry) -> bool:
    """
    Whether one cited line is traceable to this log entry.

    The entry's service must be named in the citation -- that is what stops a real message
    being attributed to a component that never logged it. Beyond that, either the entry's
    whole message appears in the citation, or the citation's message part is a fragment of
    it, since models shorten long lines. Timestamps are deliberately not compared: models
    round them, and a wrong timestamp on a real message is a citation slip, not an invention.
    """
    if entry.service not in text:
        return False
    message = _normalise_evidence(entry.message)
    if not message:
        return False
    if message in text:
        return True
    marker = f"{entry.service}:"
    quoted = text.split(marker, 1)[-1] if marker in text else text
    quoted = quoted.strip().rstrip(".").strip()
    for prefix in _EVIDENCE_PREFIXES:
        if quoted.startswith(prefix):
            quoted = quoted[len(prefix):].strip()
            break
    return len(quoted) >= MIN_EVIDENCE_QUOTE and quoted in message


def _fabricated_log_evidence(cited: list[Any]) -> list[str]:
    """
    Cited log lines that match no entry in the log corpus.

    The prompt tells every agent never to invent a log line, but a prompt is not a
    guarantee: on a live ALRT-005 run log_search answered "Unknown service" and the log
    agent still reported a fabricated line for it, which then became the diagnosis's
    evidence. Checking the corpus here makes the whole package unacceptable instead.
    """
    entries = load_logs()
    return [
        str(raw)
        for raw in cited
        if not (text := _normalise_evidence(raw)) or not any(_matches_a_log_entry(text, e) for e in entries)
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
    investigated_services is given, the origin check and the transitive reachability check
    above are applied as well; when searched_similar_incidents is given, similar_incidents_search
    must have been called.
    """
    component_names = {c.name for c in load_cmdb()}
    incident_ids = {i.id for i in load_incidents()}
    problems: list[str] = []

    names_a_bad_component = False

    affected = _component_name(str(package_input.get("affected_component", "")))
    if affected not in component_names:
        problems.append(f"affected_component '{package_input.get('affected_component')}' is not a CMDB component.")
        names_a_bad_component = True

    dependencies = [_component_name(str(d)) for d in package_input.get("critical_dependencies", [])]
    unknown_dependencies = [d for d in dependencies if d not in component_names]
    if unknown_dependencies:
        problems.append(f"critical_dependencies contain unknown components: {', '.join(unknown_dependencies)}.")
        names_a_bad_component = True

    unknown_incidents = [i for i in package_input.get("similar_incidents", []) if i not in incident_ids]
    if unknown_incidents:
        problems.append(f"similar_incidents contain unknown incident IDs: {', '.join(unknown_incidents)}.")

    if not package_input.get("log_evidence"):
        problems.append("log_evidence is empty; quote the log lines that support the hypothesis.")
    else:
        fabricated = _fabricated_log_evidence(package_input["log_evidence"])
        if fabricated:
            problems.append(
                "log_evidence contains lines that match no entry in the log corpus: "
                + "; ".join(f'"{line}"' for line in fabricated)
                + ". Quote log lines exactly as log_search returned them, attributed to the "
                "service that actually logged them. Never write a line you did not retrieve."
            )

    if not problems:
        # Checked here, not only in the prompt: after a "call submit_diagnosis now" nudge the
        # model was observed skipping the historical search (live ALRT-001 and ALRT-004).
        workflow_problems = []
        if investigated_services is not None:
            workflow_problems += _unexamined_blamed_dependencies(alert, affected, investigated_services)
            workflow_problems += _unreachable_investigated_path(alert, affected, investigated_services)
            workflow_problems += _alerted_service_with_no_investigated_dependency(alert, affected, investigated_services)
        if searched_similar_incidents is False:
            workflow_problems.append(
                "You have not searched for similar historical incidents. Call similar_incidents_search "
                "with your root-cause hypothesis first, then submit again with the matching incident IDs "
                "(or an empty list if none match)."
            )
        if workflow_problems:
            return None, workflow_problems

    if problems:
        # The component list only helps when a component name was actually wrong; appending
        # it to, say, a fabricated-evidence rejection would point the agent at the wrong fix.
        if names_a_bad_component:
            problems.append(f"Valid CMDB components: {', '.join(sorted(component_names))}.")
        return None, problems

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


def _schemas(*names: str) -> list[dict]:
    """Tool schemas by name, in the order given, so each agent binds only its own tools."""
    definitions = {d["name"]: d for d in _registry.tool_definitions()}
    definitions[SUBMIT_TOOL_NAME] = SUBMIT_TOOL
    definitions[ROUTE_TOOL_NAME] = ROUTE_TOOL
    return [to_openai_tool(definitions[name]) for name in names]


def orchestrator_tool_schemas() -> list[dict]:
    """The orchestrator has no investigation tools: it only hands off."""
    return _schemas(ROUTE_TOOL_NAME)


def cmdb_tool_schemas() -> list[dict]:
    return _schemas("cmdb_lookup")


def log_tool_schemas() -> list[dict]:
    return _schemas("log_search")


def synthesis_tool_schemas() -> list[dict]:
    return _schemas("similar_incidents_search", SUBMIT_TOOL_NAME)


def all_tool_schemas() -> list[dict]:
    """Every tool in the system. Kept for tooling/docs; no agent binds this set."""
    return _schemas("cmdb_lookup", "log_search", "similar_incidents_search", SUBMIT_TOOL_NAME, ROUTE_TOOL_NAME)


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

@dataclass
class SpecialistOutcome:
    """What one specialist run produced, for the node function to fold into state."""

    analysis: str  # the specialist's closing text, after its tools came back
    records: list[ToolCallRecord]  # every tool call it executed, in order
    submit_calls: list[dict]  # submit_diagnosis calls, deliberately left for the guardrail
    update: dict  # the shared state update (messages, trace, counters, usage)


def _run_tool_calls(
    response: AIMessage,
    step: int,
    runtime: Runtime[RootlyContext],
    allowed: set[str],
) -> tuple[list[BaseMessage], list[dict], list[ToolCallRecord], set[str]]:
    """
    Execute one turn's tool calls. This is the former tools_node, scoped to a single
    specialist: a call to a tool the specialist does not own is refused as an error
    observation rather than executed, so a confused model cannot reach around its role.
    """
    messages: list[BaseMessage] = []
    trace: list[dict] = []
    records: list[ToolCallRecord] = []
    investigated: set[str] = set()

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
        if name not in allowed:
            trace += _emit(runtime, TraceEvent(step, "note", content=f"{name} is not one of this specialist's tools.", is_error=True))
            messages.append(ToolMessage(
                content=f"Error: {name} is not available to you. Your tools are: {', '.join(sorted(allowed))}.",
                tool_call_id=call_id, name=name, status="error",
            ))
            continue
        trace += _emit(runtime, TraceEvent(step, "action", tool=name, input=dict(args)))
        record = _registry.execute(name, dict(args))
        records.append(record)
        if name == "log_search" and not record.is_error:
            investigated.add(str(args.get("service", "")).strip().lower())
        trace += _emit(runtime, TraceEvent(step, "observation", tool=name, result=record.result,
                                           duration_ms=round(record.duration_ms, 1), is_error=record.is_error))
        messages.append(ToolMessage(
            content=json.dumps(record.result, ensure_ascii=False),
            tool_call_id=call_id, name=name, status="error" if record.is_error else "success",
        ))

    return messages, trace, records, investigated


def run_specialist(
    state: RootlyState,
    runtime: Runtime[RootlyContext],
    *,
    llm,
    system_prompt: str,
    briefing: str,
    allowed_tools: set[str],
    label: str,
    max_turns: int = SPECIALIST_MAX_TURNS,
    stop_on_submit: bool = False,
) -> SpecialistOutcome:
    """
    Run one specialist to completion: call the model, execute the tool calls it asks for,
    feed the results back, and stop once it answers with text instead of tools.

    The specialist's conversation is local to this call -- its system prompt, the briefing
    it was handed, and its own tool results. It never sees the shared `messages` log, which
    is what keeps each agent's context small and role-specific. The messages it produced
    are still appended to that log, for the audit trail and for the guardrail to read the
    synthesis agent's submit_diagnosis call off.

    This is the loop the former single agent_node ran across graph edges: rate-limit
    backoff (via _invoke), truncation recovery, tool-call id dedup, and the step budget.
    """
    messages: list[BaseMessage] = [SystemMessage(system_prompt), HumanMessage(briefing)]
    audit: list[BaseMessage] = []
    trace: list[dict] = []
    records: list[ToolCallRecord] = []
    investigated = set(state["investigated_services"])
    used_ids = set(state["used_tool_call_ids"])
    usage = dict(state["usage"])
    step = state["step_count"]
    max_steps = state["max_steps"]
    truncated_total = state["truncated_responses"]
    tool_calls = state["tool_calls"]
    model = state["model"]
    analysis = ""
    submit_calls: list[dict] = []

    for turn_index in range(max_turns):
        step += 1
        if step > max_steps:
            raise DiagnosisError(f"No valid diagnosis package after {max_steps} steps.")
        if step == max_steps:
            messages, _ = _with_note(messages, SPECIALIST_BUDGET_NOTE)
            trace += _emit(runtime, TraceEvent(step, "note", content=f"Step budget reached — asking the {label} agent to wrap up."))

        response = _invoke(llm, messages)
        for key in ("input_tokens", "output_tokens"):
            usage[key] = usage.get(key, 0) + (response.usage_metadata or {}).get(key, 0)
        model = response.response_metadata.get("model_name", model)

        if response.response_metadata.get("finish_reason") == "length":
            # A cut-off response may hold partial tool calls, so it is never added to the
            # history. The note changes the next input, which breaks a deterministic loop.
            truncated_total += 1
            if truncated_total > MAX_TRUNCATED_RESPONSES:
                raise DiagnosisError(
                    f"Model output was cut off (finish_reason=length) {truncated_total} times; giving up at step {step}."
                )
            messages, _ = _with_note(messages, TRUNCATED_NOTE)
            trace += _emit(runtime, TraceEvent(step, "note", content="Model response was too long and got cut off — asking for a shorter one.", is_error=True))
            continue

        _dedupe_tool_call_ids(response, used_ids)
        thought = _text(response)
        if thought:
            analysis = thought
            trace += _emit(runtime, TraceEvent(step, "thought", content=thought))
        messages.append(response)
        audit.append(response)

        if stop_on_submit:
            submit_calls = [c for c in response.tool_calls if c["name"] == SUBMIT_TOOL_NAME]
            if submit_calls:
                break  # the guardrail node answers this call

        if not response.tool_calls and not response.invalid_tool_calls:
            break  # the specialist is done: its text is the analysis

        turn_messages, turn_trace, turn_records, turn_investigated = _run_tool_calls(
            response, step, runtime, allowed_tools
        )
        messages += turn_messages
        audit += turn_messages
        trace += turn_trace
        records += turn_records
        investigated |= turn_investigated
        tool_calls += len(turn_records)

        if stop_on_submit and turn_index == max_turns - 1:
            # Out of turns without a package: nudge now so the final turn can still submit.
            messages, _ = _with_note(messages, MISSING_SUBMIT_NOTE)

    update = {
        "messages": audit,
        "trace": trace,
        "step_count": step,
        "usage": usage,
        "truncated_responses": truncated_total,
        "used_tool_call_ids": sorted(used_ids),
        "investigated_services": sorted(investigated),
        "tool_calls": tool_calls,
        "model": model,
    }
    return SpecialistOutcome(analysis=analysis, records=records, submit_calls=submit_calls, update=update)


# ── Orchestrator ─────────────────────────────────────────────────────────

def _fallback_route(state: RootlyState) -> dict:
    """
    Where to go when the orchestrator's model would not call route_to_specialist.

    This is error recovery, not a second routing policy: it mirrors the order the
    orchestrator prompt asks for, so a malformed turn costs a step instead of the run.
    """
    alert = get_alert(state["alert_id"])
    window = _default_window(alert)
    if not state.get("cmdb_context"):
        return {"next": "cmdb", "targets": [alert.service.strip().lower()], "reason": "fallback: CMDB first"}
    if not state.get("log_evidence_gathered"):
        return {"next": "log", "targets": [alert.service.strip().lower()], **window, "reason": "fallback: logs next"}
    pending = _uninvestigated_from_reasons(state)
    if pending:
        return {"next": "log", "targets": pending, **window, "reason": "fallback: the guardrail asked for these logs"}
    return {"next": "synthesis", "targets": [], "reason": "fallback: evidence gathered"}


def _default_window(alert: Alert) -> dict:
    start = alert.timestamp - ORIGIN_CHECK_BEFORE_ALERT
    end = alert.timestamp + ORIGIN_CHECK_AFTER_ALERT
    return {
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "end_time": end.isoformat().replace("+00:00", "Z"),
    }


def _uninvestigated_from_reasons(state: RootlyState) -> list[str]:
    """CMDB components named in the guardrail's rejection reasons that still lack a log_search."""
    reasons = " ".join(state.get("replan_reasons") or [])
    if not reasons:
        return []
    investigated = set(state["investigated_services"])
    return [c.name for c in load_cmdb() if c.name in reasons and c.name not in investigated]


def _looked_up(state: RootlyState) -> set[str]:
    """Components the CMDB agent has already retrieved in this run."""
    return {
        str(component.get("name", "")).strip().lower()
        for entry in state.get("cmdb_context") or []
        for component in entry.get("components", [])
    }


def _normalise_route(args: dict, state: RootlyState) -> dict:
    """Validate the model's routing arguments, filling in a window for a log assignment."""
    choice = str(args.get("next", "")).strip().lower()
    if choice not in SPECIALISTS:
        return {}
    targets = [str(t).strip().lower() for t in (args.get("targets") or []) if str(t).strip()]
    assignment: dict = {"next": choice, "targets": targets, "reason": str(args.get("reason") or "").strip()}
    if choice == "log":
        window = _default_window(get_alert(state["alert_id"]))
        assignment["start_time"] = str(args.get("start_time") or window["start_time"])
        assignment["end_time"] = str(args.get("end_time") or window["end_time"])
        # Only real services, checked here rather than left to log_search. Observed live on
        # ALRT-005: the orchestrator invented "authentication-service" (for auth-service),
        # log_search returned "Unknown service", and the log agent then reported a *fabricated*
        # log line for it. Refusing the handoff stops that cascade at the source.
        assignment["targets"] = [t for t in targets if t in {c.name for c in load_cmdb()}]
        if not assignment["targets"]:
            return {}  # a log assignment without a known service is not actionable
    if choice == "cmdb":
        # CMDB records are static, so looking the same component up twice cannot add
        # evidence. Observed live on ALRT-001: the orchestrator routed to the CMDB agent
        # twice for checkout-api, spending a whole round to learn nothing. Drop the
        # components already held and the ones that do not exist, and refuse the handoff
        # if that leaves nothing to do. Both names and CI ids are accepted, as cmdb_lookup does.
        known = {c.name for c in load_cmdb()} | {c.id.lower() for c in load_cmdb()}
        assignment["targets"] = [t for t in targets if t in known and t not in _looked_up(state)]
        if not assignment["targets"]:
            return {}
    return assignment


def orchestrator_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    """
    One routing turn. The orchestrator reads the alert and everything the specialists have
    found so far, then calls route_to_specialist to pick who runs next. It holds no
    investigation tools of its own.
    """
    from src.agent.system_prompt import (
        ORCHESTRATOR_PROMPT,
        format_alert,
        format_cmdb_context,
        format_log_evidence,
        format_replan_reasons,
    )

    alert = get_alert(state["alert_id"])
    step = state["step_count"]
    max_steps = state["max_steps"]
    briefing_parts = [
        format_alert(alert),
        "",
        format_cmdb_context(state.get("cmdb_context") or []),
        "",
        format_log_evidence(state.get("log_evidence_gathered") or []),
        "",
        f"Components already looked up in the CMDB: {', '.join(sorted(_looked_up(state))) or 'none yet'}. "
        "Looking one of them up again cannot add evidence, so do not.",
        f"Services whose logs have been searched: {', '.join(state['investigated_services']) or 'none yet'}.",
        f"Model turns used: {step} of {max_steps}.",
    ]
    replan = format_replan_reasons(state.get("replan_reasons"))
    if replan:
        briefing_parts += ["", replan]
    if max_steps - step <= SPECIALIST_MAX_TURNS:
        briefing_parts += ["", BUDGET_EXHAUSTED_NOTE]

    messages: list[BaseMessage] = [
        SystemMessage(ORCHESTRATOR_PROMPT),
        HumanMessage("\n".join(briefing_parts)),
    ]
    audit: list[BaseMessage] = []
    trace: list[dict] = []
    usage = dict(state["usage"])
    used_ids = set(state["used_tool_call_ids"])
    truncated_total = state["truncated_responses"]
    model = state["model"]
    assignment: dict = {}

    for _ in range(ORCHESTRATOR_ROUTE_ATTEMPTS):
        step += 1
        if step > max_steps:
            raise DiagnosisError(f"No valid diagnosis package after {max_steps} steps.")

        response = _invoke(runtime.context.orchestrator_llm, messages)
        for key in ("input_tokens", "output_tokens"):
            usage[key] = usage.get(key, 0) + (response.usage_metadata or {}).get(key, 0)
        model = response.response_metadata.get("model_name", model)

        if response.response_metadata.get("finish_reason") == "length":
            truncated_total += 1
            if truncated_total > MAX_TRUNCATED_RESPONSES:
                raise DiagnosisError(
                    f"Model output was cut off (finish_reason=length) {truncated_total} times; giving up at step {step}."
                )
            messages, _ = _with_note(messages, TRUNCATED_NOTE)
            trace += _emit(runtime, TraceEvent(step, "note", content="Model response was too long and got cut off — asking for a shorter one.", is_error=True))
            continue

        _dedupe_tool_call_ids(response, used_ids)
        thought = _text(response)
        if thought:
            trace += _emit(runtime, TraceEvent(step, "thought", content=thought))
        messages.append(response)
        audit.append(response)

        route_calls = [c for c in response.tool_calls if c["name"] == ROUTE_TOOL_NAME]
        if route_calls:
            assignment = _normalise_route(dict(route_calls[-1]["args"]), state)
            # Every routing call is answered, so the audit log keeps valid tool pairs.
            refusal = (
                "Unusable routing arguments: 'next' must be cmdb, log or synthesis; 'log' needs "
                "at least one real service; 'cmdb' needs at least one component that exists and "
                "has not been looked up already. Valid component names: "
                f"{', '.join(sorted(c.name for c in load_cmdb()))}. Call the tool again."
            )
            for call in route_calls:
                messages.append(ToolMessage(
                    content="Routed." if assignment else refusal,
                    tool_call_id=call["id"], name=ROUTE_TOOL_NAME,
                    status="success" if assignment else "error",
                ))
                audit.append(messages[-1])
            if assignment:
                break
            trace += _emit(runtime, TraceEvent(step, "note", content="Orchestrator's routing arguments were unusable — asking again.", is_error=True))
            continue

        messages, _ = _with_note(messages, MISSING_ROUTE_NOTE)
        trace += _emit(runtime, TraceEvent(step, "note", content="Orchestrator did not hand off — reminding it to call route_to_specialist.", is_error=True))

    if not assignment:
        assignment = _fallback_route(state)
        trace += _emit(runtime, TraceEvent(step, "note", content=f"Orchestrator did not route; falling back to the {assignment['next']} agent.", is_error=True))

    choice = assignment.pop("next")
    targets = ", ".join(assignment.get("targets") or []) or "—"
    trace += _emit(runtime, TraceEvent(step, "routing", content=f"{choice} agent → {targets}", tool=choice,
                                       input={k: v for k, v in assignment.items() if v}))
    return {
        "messages": audit,
        "trace": trace,
        "step_count": step,
        "usage": usage,
        "truncated_responses": truncated_total,
        "used_tool_call_ids": sorted(used_ids),
        "model": model,
        "next_specialist": choice,
        "assignment": assignment,
    }


def guardrail_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    step = state["step_count"]
    alert = get_alert(state["alert_id"])
    elapsed = time.time() - state["started_at"]
    messages: list[BaseMessage] = []
    trace: list[dict] = []
    accepted: DiagnosisPackage | None = None
    rejected_reasons: list[str] = []
    searched = any(e["kind"] == "action" and e.get("tool") == "similar_incidents_search" for e in state["trace"])

    for call in _submit_calls(state):
        package, problems = check_package(alert, call["args"], step, elapsed, set(state["investigated_services"]), searched)
        if package:
            accepted = package
            trace += _emit(runtime, TraceEvent(step, "guardrail", content="Diagnosis package passed the CMDB guardrail."))
            messages.append(ToolMessage(content="Diagnosis accepted.", tool_call_id=call["id"], name=SUBMIT_TOOL_NAME))
        else:
            rejected_reasons += problems
            trace += _emit(runtime, TraceEvent(step, "guardrail", content=" ".join(problems), is_error=True))
            messages.append(ToolMessage(
                content="Diagnosis rejected:\n- " + "\n- ".join(problems),
                tool_call_id=call["id"], name=SUBMIT_TOOL_NAME, status="error",
            ))

    update: dict = {"messages": messages, "trace": trace}
    if accepted:
        update["diagnosis"] = accepted.model_dump(mode="json")
        update["elapsed_seconds"] = round(elapsed, 2)
        update["replan_reasons"] = []
    else:
        # The orchestrator reads these to decide which logs still have to be searched
        # before the synthesis agent is allowed to submit again.
        update["replan_reasons"] = rejected_reasons
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

    # Audit attributes for Phoenix. A no-op when no tracer provider is registered
    # (see src/agent/observability.setup_phoenix), e.g. in the offline tests.
    _tracer = otel_trace.get_tracer("rootly")
    with _tracer.start_as_current_span("human_approval_decision") as _span:
        _span.set_attribute("rootly.human_decision", decision)
        _span.set_attribute("rootly.owner_team", updated.owner_team or "")
        if updated.human_decision_note:
            _span.set_attribute("rootly.human_note", updated.human_decision_note)

    trace = _emit(runtime, TraceEvent(state["step_count"], "human_decision", content=content))
    return {"diagnosis": updated.model_dump(mode="json"), "trace": trace}
