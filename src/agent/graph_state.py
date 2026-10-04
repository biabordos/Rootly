"""
LangGraph state schema and runtime context for the Rootly diagnosis graph.

RootlyState is the single state that flows through every node of the graph
(orchestrator -> cmdb_agent / log_agent / synthesis_agent -> guardrail ->
escalation_policy -> human_approval, see src/agent/graph.py). Each node receives the
full state and returns a partial update (a dict with only the keys it changed), which
LangGraph merges in. "Who writes what":

    Field                                   Written by                           Role
    messages                                orchestrator, specialists,           shared audit log of the run
                                              guardrail
    step_count, usage, model                orchestrator, specialists            model turn counting + tokens
    truncated_responses                     orchestrator, specialists            recovery from a cut-off response
    tool_calls, investigated_services       cmdb_agent, log_agent,               which investigation tools ran
                                              synthesis_agent
    next_specialist, assignment             orchestrator                         the routing decision it just made
    cmdb_context                            cmdb_agent                           dependency/blast-radius findings
    log_evidence_gathered                   log_agent                            per-service LOCAL/RELAY verdicts
    replan_reasons                          guardrail (on rejection)             what the next round must fix
    diagnosis                               guardrail (creates);                 the final package
                                              escalation_policy, human_approval
                                              (enrich)
    elapsed_seconds                         guardrail                           freezes the clock on acceptance
    trace                                   every node (via operator.add)       the running reasoning trace

Unlike the former single-agent graph, no node resends `messages` to the model. Each
agent builds its own focused message list from its system prompt plus the structured
context above (see src/agent/system_prompt.py); `messages` is kept because it is the
run's audit trail and because the guardrail reads the synthesis agent's
submit_diagnosis call off the last AIMessage in it.

RootlyContext, below, is intentionally kept separate: RootlyState is checkpointed
(serialized to SQLite) so a run can be resumed from another process, but an LLM
instance and a Python callback are not serializable. Do not fold RootlyContext's
fields into RootlyState.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RootlyState(TypedDict, total=False):
    # Audit log of the run: every agent appends its own AIMessage and the ToolMessages
    # its tool calls produced. add_messages appends, or replaces a message with the same
    # id (used to fold notes into a tool result).
    messages: Annotated[list[BaseMessage], add_messages]
    alert_id: str
    max_steps: int
    step_count: int  # model turns across all four agents, including discarded truncated ones
    tool_calls: int  # investigation tool calls, submit_diagnosis excluded
    truncated_responses: int
    used_tool_call_ids: list[str]
    investigated_services: list[str]  # services searched with log_search (origin check)
    started_at: float  # wall-clock epoch seconds, so it survives a checkpoint
    elapsed_seconds: float  # frozen when the guardrail accepts; excludes human wait time
    model: str
    usage: dict[str, int]

    # ── Multi-agent coordination ──────────────────────────────────────────
    # The orchestrator's latest routing decision and the arguments it handed the
    # specialist: {"targets": [...], "start_time": ..., "end_time": ..., "reason": ...}.
    next_specialist: str | None  # "cmdb" | "log" | "synthesis"
    assignment: dict | None
    # What the specialists found, as plain data so it survives a checkpoint.
    cmdb_context: Annotated[list[dict], operator.add]
    log_evidence_gathered: Annotated[list[dict], operator.add]
    # Guardrail rejection reasons the orchestrator must act on in the next round.
    replan_reasons: list[str]

    # DiagnosisPackage.model_dump(mode="json"): plain data keeps checkpoints portable.
    diagnosis: dict | None
    # Kept in state (not only streamed) so a run resumed in another process still has its full trace.
    trace: Annotated[list[dict], operator.add]


@dataclass
class RootlyContext:
    """
    Per-invocation dependencies, passed as LangGraph runtime context and never checkpointed.

    Each agent gets its own chat model handle, with only its own tools bound. In
    production all four are the same Mistral model (see graph.run_diagnosis); keeping
    them separate is what lets the offline tests script each agent independently.
    """

    orchestrator_llm: Any = None
    cmdb_llm: Any = None
    log_llm: Any = None
    synthesis_llm: Any = None
    on_event: Callable[[dict], None] | None = None


def assert_state_invariants(state: RootlyState) -> None:
    """
    Sanity checks a RootlyState snapshot should always satisfy, regardless of which node
    produced it -- not called by the graph itself, so it never changes runtime behavior.
    Run it against a snapshot (e.g. graph.get_state(config).values after a run) to catch a
    state-shape regression early instead of only trusting the field docs above.
    """
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps")
    if max_steps is not None and step_count > max_steps:
        raise AssertionError(f"step_count ({step_count}) exceeds max_steps ({max_steps}).")

    used_ids = state.get("used_tool_call_ids") or []
    if len(used_ids) != len(set(used_ids)):
        raise AssertionError("used_tool_call_ids contains duplicates.")

    if state.get("diagnosis") is not None:
        elapsed = state.get("elapsed_seconds")
        if not elapsed or elapsed <= 0:
            raise AssertionError("diagnosis is set but elapsed_seconds was not frozen (must be > 0).")

    tool_calls = state.get("tool_calls", 0)
    if tool_calls < 0:
        raise AssertionError(f"tool_calls is negative ({tool_calls}).")

    specialist = state.get("next_specialist")
    if specialist is not None and specialist not in ("cmdb", "log", "synthesis"):
        raise AssertionError(f"next_specialist is '{specialist}', not one of cmdb/log/synthesis.")
