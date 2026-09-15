"""LangGraph state schema and runtime context for the Rootly diagnosis graph."""

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
