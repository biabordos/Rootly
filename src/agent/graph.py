"""
The Rootly diagnosis graph (LangGraph StateGraph) and its public entry points.

Four agents share the graph: an orchestrator that only routes, and three specialists that
each own one tool. Control returns to the orchestrator after every specialist, so it can
decide whether to keep investigating or synthesize.

    START → orchestrator ─┬─ "cmdb"      → cmdb_agent ──────→ orchestrator
                          ├─ "log"       → log_agent ───────→ orchestrator
                          └─ "synthesis" → synthesis_agent ─┬─ no package → orchestrator
                                                             └─ submit_diagnosis
                                                                    ↓
                              orchestrator ←─ rejected ─┬─ guardrail
                                                         └─ accepted → escalation_policy
    escalation_policy ─┬─ urgent → human_approval (interrupt) → END
                       └─ otherwise → END

A rejected package goes back to the *orchestrator*, not straight to the synthesis agent:
the guardrail's reasons name the services whose logs are still missing, and only the log
agent can close that gap, so the orchestrator has to dispatch another investigation round
first. This is what makes the multi-hop scenarios (ALRT-003/005/009) reachable.

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

from langchain_core.messages import AIMessage, HumanMessage
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
    build_llm,
    cmdb_tool_schemas,
    escalation_policy_node,
    guardrail_node,
    human_approval_node,
    log_tool_schemas,
    orchestrator_node,
    orchestrator_tool_schemas,
    synthesis_tool_schemas,
)
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.specialists import cmdb_agent_node, log_agent_node, synthesis_agent_node
from src.agent.system_prompt import format_alert
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

SPECIALIST_NODES = {"cmdb": "cmdb_agent", "log": "log_agent", "synthesis": "synthesis_agent"}


def route_orchestrator(state: RootlyState) -> str:
    """Dispatch to the specialist the orchestrator picked in next_specialist."""
    return state["next_specialist"]  # mapped to a node name by SPECIALIST_NODES


def route_after_synthesis(state: RootlyState) -> str:
    """A package goes to the guardrail; a synthesis agent that produced none goes back to routing."""
    last_ai = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
    if last_ai and any(c["name"] == SUBMIT_TOOL_NAME for c in last_ai.tool_calls):
        return "guardrail"
    return "orchestrator"


def route_after_guardrail(state: RootlyState) -> str:
    # A rejection returns to the orchestrator, which reads replan_reasons and dispatches
    # the investigation the guardrail asked for before synthesis is tried again.
    return "escalation_policy" if state.get("diagnosis") else "orchestrator"


def route_after_escalation(state: RootlyState) -> str:
    urgent = EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL.value
    return "human_approval" if state["diagnosis"]["escalation_decision"] == urgent else END


def build_graph(checkpointer):
    builder = StateGraph(RootlyState, context_schema=RootlyContext)
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("cmdb_agent", cmdb_agent_node)
    builder.add_node("log_agent", log_agent_node)
    builder.add_node("synthesis_agent", synthesis_agent_node)
    builder.add_node("guardrail", guardrail_node)
    builder.add_node("escalation_policy", escalation_policy_node)
    builder.add_node("human_approval", human_approval_node)

    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges("orchestrator", route_orchestrator, SPECIALIST_NODES)
    builder.add_edge("cmdb_agent", "orchestrator")
    builder.add_edge("log_agent", "orchestrator")
    builder.add_conditional_edges("synthesis_agent", route_after_synthesis,
                                  {"guardrail": "guardrail", "orchestrator": "orchestrator"})
    builder.add_conditional_edges("guardrail", route_after_guardrail,
                                  {"escalation_policy": "escalation_policy", "orchestrator": "orchestrator"})
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
        # One investigation round is two node visits (orchestrator → specialist) and at
        # least two model turns, so node visits stay under max_steps; the extra room covers
        # the guardrail/escalation/approval tail and a replanning round.
        config["recursion_limit"] = max_steps + 20
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


AGENT_TOOL_SCHEMAS = {
    "orchestrator": orchestrator_tool_schemas,
    "cmdb": cmdb_tool_schemas,
    "log": log_tool_schemas,
    "synthesis": synthesis_tool_schemas,
}


def _build_context(llm, llms: dict | None, on_event) -> RootlyContext:
    """
    Give each agent its own model handle with only its own tools bound.

    In production all four are the same Mistral model; `llms` exists so the offline tests
    can script each agent independently (a FakeLLM per agent) instead of interleaving every
    turn of the run into one fragile script.
    """
    llms = llms or {}
    unknown = set(llms) - set(AGENT_TOOL_SCHEMAS)
    if unknown:
        raise DiagnosisError(f"Unknown agent(s) in llms: {', '.join(sorted(unknown))}. "
                             f"Use {', '.join(AGENT_TOOL_SCHEMAS)}.")
    bound = {}
    for agent, schemas in AGENT_TOOL_SCHEMAS.items():
        handle = llms.get(agent, llm)
        if handle is None:
            raise DiagnosisError(f"No chat model supplied for the {agent} agent.")
        bound[f"{agent}_llm"] = handle.bind_tools(schemas())
    return RootlyContext(**bound, on_event=on_event)


def run_diagnosis(
    alert: Alert,
    *,
    llm=None,
    llms: dict | None = None,
    on_event=None,
    model: str | None = None,
    max_steps: int | None = None,
    thread_id: str | None = None,
) -> DiagnosisResult:
    """
    Run the investigation for one alert. on_event receives each trace event as it happens.
    If the result has needs_approval, continue it with resume_diagnosis(result.thread_id, ...).

    `llm` is used by all four agents. `llms` overrides individual ones, keyed
    "orchestrator" / "cmdb" / "log" / "synthesis".
    """
    model = model or os.getenv("MISTRAL_MODEL", DEFAULT_MODEL)
    max_steps = max_steps or int(os.getenv("MAX_REACT_STEPS", DEFAULT_MAX_STEPS))
    if llm is None and set(llms or {}) != set(AGENT_TOOL_SCHEMAS):
        llm = build_llm(model)  # raises before any call if the API key is missing
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
        # The alert opens the shared audit log; each agent's own system prompt is local to
        # its run (see graph_nodes.run_specialist), not part of this conversation.
        "messages": [HumanMessage(format_alert(alert))],
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
        "next_specialist": None,
        "assignment": None,
        "cmdb_context": [],
        "log_evidence_gathered": [],
        "replan_reasons": [],
        "diagnosis": None,
        "trace": [],
    }
    context = _build_context(llm, llms, on_event)
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
