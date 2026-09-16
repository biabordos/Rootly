"""
LangGraph state schema and runtime context for the Rootly diagnosis graph.

RootlyState is the single state that flows through every node of the graph
(agent -> nudge -> tools -> guardrail -> escalation_policy -> human_approval, see
src/agent/graph.py). Each node receives the full state and returns a partial update
(a dict with only the keys it changed), which LangGraph merges in. "Who writes what":

    Field                                   Written by                           Role
    messages                                agent, nudge, tools, guardrail       conversation sent to the model
    step_count, usage, model                agent                                turn counting + token usage
    truncated_responses                     agent                                recovery from a cut-off response
    tool_calls, investigated_services       tools                                which investigation tools ran
    diagnosis                               guardrail (creates);                 the final package
                                             escalation_policy, human_approval
                                             (enrich)
    elapsed_seconds                         guardrail                           freezes the clock on acceptance
    trace                                   every node (via operator.add)       the running reasoning trace

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
    # Full conversation, resent to the model every turn; add_messages appends,
    # or replaces a message with the same id (used to fold notes into a tool result).
    messages: Annotated[list[BaseMessage], add_messages]
    alert_id: str
    max_steps: int
    step_count: int  # model turns, including discarded truncated ones
    tool_calls: int  # investigation tool calls, submit_diagnosis excluded
    truncated_responses: int
    used_tool_call_ids: list[str]
    investigated_services: list[str]  # services searched with log_search (origin check)
    started_at: float  # wall-clock epoch seconds, so it survives a checkpoint
    elapsed_seconds: float  # frozen when the guardrail accepts; excludes human wait time
    model: str
    usage: dict[str, int]
    # DiagnosisPackage.model_dump(mode="json"): plain data keeps checkpoints portable.
    diagnosis: dict | None
    # Kept in state (not only streamed) so a run resumed in another process still has its full trace.
    trace: Annotated[list[dict], operator.add]


@dataclass
class RootlyContext:
    """Per-invocation dependencies, passed as LangGraph runtime context and never checkpointed."""

    llm: Any = None  # chat model with the tools already bound; not needed to resume an approval
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
