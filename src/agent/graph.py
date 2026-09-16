"""
The Rootly diagnosis graph (LangGraph StateGraph) and its public entry points.

    START → agent ─┬─ tool calls ──→ tools ─┬─ submit_diagnosis? no ──→ agent
                   ├─ no tool calls → nudge → agent
                   └─ truncated ───→ agent  └─ yes → guardrail ─┬─ rejected → agent
                                                                 └─ accepted → escalation_policy
    escalation_policy ─┬─ urgent → human_approval (interrupt) → END
                       └─ otherwise → END

run_diagnosis() starts a run under a thread_id. When the escalation policy asks for human
approval the graph pauses at interrupt() with its state checkpointed to SQLite, so
resume_diagnosis(thread_id, ...) can continue it later, from any process.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.agent.escalation_policy import HUMAN_DECISIONS
from src.agent.graph_nodes import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    SUBMIT_TOOL_NAME,
    DiagnosisError,
    agent_node,
    all_tool_schemas,
    build_llm,
    escalation_policy_node,
    guardrail_node,
    human_approval_node,
    nudge_node,
    tools_node,
)
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.system_prompt import SYSTEM_PROMPT, format_alert
from src.data_loader import get_alert
from src.models.schemas import Alert, DiagnosisPackage, EscalationDecision

CHECKPOINT_DB = Path(__file__).resolve().parents[2] / ".checkpoints" / "rootly.sqlite"


@dataclass
class DiagnosisResult:
    alert: Alert
    diagnosis: DiagnosisPackage
    trace: list[dict]
    steps: int
    tool_calls: int
    elapsed_seconds: float
    model: str
    thread_id: str
    usage: dict[str, int] = field(default_factory=dict)
    needs_approval: bool = False
    approval_request: dict | None = None  # the interrupt() payload while paused


# ── Routing ──────────────────────────────────────────────────────────────

def route_after_agent(state: RootlyState) -> str:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        return "agent"  # the response was truncated and discarded; a note was added instead
    if last.tool_calls or last.invalid_tool_calls:
        return "tools"
    return "nudge"


def route_after_tools(state: RootlyState) -> str:
    last_ai = next(m for m in reversed(state["messages"]) if isinstance(m, AIMessage))
    return "guardrail" if any(c["name"] == SUBMIT_TOOL_NAME for c in last_ai.tool_calls) else "agent"


def route_after_guardrail(state: RootlyState) -> str:
    return "escalation_policy" if state.get("diagnosis") else "agent"


def route_after_escalation(state: RootlyState) -> str:
    urgent = EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL.value
    return "human_approval" if state["diagnosis"]["escalation_decision"] == urgent else END


def build_graph(checkpointer):
    builder = StateGraph(RootlyState, context_schema=RootlyContext)
    builder.add_node("agent", agent_node)
    builder.add_node("nudge", nudge_node)
    builder.add_node("tools", tools_node)
    builder.add_node("guardrail", guardrail_node)
    builder.add_node("escalation_policy", escalation_policy_node)
    builder.add_node("human_approval", human_approval_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"agent": "agent", "tools": "tools", "nudge": "nudge"})
    builder.add_edge("nudge", "agent")
    builder.add_conditional_edges("tools", route_after_tools, {"guardrail": "guardrail", "agent": "agent"})
    builder.add_conditional_edges("guardrail", route_after_guardrail, {"escalation_policy": "escalation_policy", "agent": "agent"})
    builder.add_conditional_edges("escalation_policy", route_after_escalation, {"human_approval": "human_approval", END: END})
    builder.add_edge("human_approval", END)
    return builder.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def get_graph():
    """The app-wide graph, checkpointed to SQLite so paused runs survive a restart."""
    CHECKPOINT_DB.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    return build_graph(SqliteSaver(connection))


# ── Public API ───────────────────────────────────────────────────────────

def _config(
    thread_id: str,
    max_steps: int | None = None,
    *,
    run_name: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    config: dict = {"configurable": {"thread_id": thread_id}}
    if max_steps:
        # Each step visits at most agent → tools → guardrail; the extra room covers nudges and escalation.
        config["recursion_limit"] = max_steps * 4 + 10
    # run_name/tags/metadata are standard LangChain config fields: if LangSmith tracing is
    # on (see src/agent/observability.py) they show up on the run with no further wiring;
    # if tracing is off they're simply ignored.
    if run_name:
        config["run_name"] = run_name
    if tags:
        config["tags"] = tags
    if metadata:
        config["metadata"] = metadata
    return config


def _result(graph, thread_id: str) -> DiagnosisResult:
    snapshot = graph.get_state(_config(thread_id))
    values = snapshot.values
    if not values.get("diagnosis"):
        raise DiagnosisError(f"Run '{thread_id}' has no accepted diagnosis.")
    pending = snapshot.interrupts[0].value if snapshot.interrupts else None
    return DiagnosisResult(
        alert=get_alert(values["alert_id"]),
        diagnosis=DiagnosisPackage.model_validate(values["diagnosis"]),
        trace=list(values.get("trace", [])),
        steps=values["step_count"],
        tool_calls=values["tool_calls"],
        elapsed_seconds=values["elapsed_seconds"],
        model=values["model"],
        thread_id=thread_id,
        usage=dict(values["usage"]),
        needs_approval=pending is not None,
        approval_request=pending,
    )


def run_diagnosis(
    alert: Alert,
    *,
    llm=None,
    on_event=None,
    model: str | None = None,
    max_steps: int | None = None,
    thread_id: str | None = None,
) -> DiagnosisResult:
    """
    Run the investigation for one alert. on_event receives each trace event as it happens.
    If the result has needs_approval, continue it with resume_diagnosis(result.thread_id, ...).
    """
    model = model or os.getenv("MISTRAL_MODEL", DEFAULT_MODEL)
    max_steps = max_steps or int(os.getenv("MAX_REACT_STEPS", DEFAULT_MAX_STEPS))
    llm = llm or build_llm(model)
    thread_id = thread_id or f"{alert.id}-{uuid.uuid4().hex[:8]}"

    graph = get_graph()
    config = _config(
        thread_id,
        max_steps,
        run_name=f"rootly-{alert.id}",
        tags=["rootly", alert.alert_type.value, model],
        metadata={
            "alert_id": alert.id,
            "service": alert.service,
            "severity_reported": alert.severity_reported.value,
            "model": model,
        },
    )
    if graph.get_state(config).values:
        raise DiagnosisError(f"Run '{thread_id}' already exists; use resume_diagnosis or a new thread_id.")

    initial: RootlyState = {
        "messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(format_alert(alert))],
        "alert_id": alert.id,
        "max_steps": max_steps,
        "step_count": 0,
        "tool_calls": 0,
        "truncated_responses": 0,
        "used_tool_call_ids": [],
        "investigated_services": [],
        "started_at": time.time(),
        "model": model,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "diagnosis": None,
        "trace": [],
    }
    context = RootlyContext(llm=llm.bind_tools(all_tool_schemas()), on_event=on_event)
    try:
        graph.invoke(initial, config, context=context)
    except GraphRecursionError as exc:
        raise DiagnosisError(f"No valid diagnosis package after {max_steps} steps.") from exc
    return _result(graph, thread_id)


def resume_diagnosis(thread_id: str, decision: str, *, note: str | None = None, on_event=None) -> DiagnosisResult:
    """Answer a paused run's approval request: decision is 'approve' or 'downgrade'."""
    if decision not in HUMAN_DECISIONS:
        raise DiagnosisError(f"Unknown decision '{decision}'. Use one of: {', '.join(HUMAN_DECISIONS)}.")
    graph = get_graph()
    config = _config(thread_id)
    snapshot = graph.get_state(config)
    if not snapshot.values:
        raise DiagnosisError(f"No run found with thread id '{thread_id}'.")
    if not snapshot.interrupts:
        raise DiagnosisError(f"Run '{thread_id}' is not waiting for approval.")

    graph.invoke(Command(resume={"decision": decision, "note": note}), config, context=RootlyContext(on_event=on_event))
    return _result(graph, thread_id)
