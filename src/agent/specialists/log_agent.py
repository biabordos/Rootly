"""
Log analysis specialist: search the assigned services' logs in the assigned window and
classify what they show as LOCAL (the service itself broke) or RELAY (it is reporting
someone else's failure).

Writes one entry per run to state["log_evidence_gathered"], holding the retrieved log
lines in the exact form the diagnosis package must quote them, plus the specialist's
classification. Running log_search is also what fills state["investigated_services"],
which the guardrail's origin and reachability checks are built on -- so this agent is the
only one that can satisfy them.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from src.agent.graph_nodes import run_specialist
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.system_prompt import LOG_AGENT_PROMPT, format_alert, format_assignment, format_replan_reasons
from src.data_loader import get_alert

ALLOWED_TOOLS = {"log_search"}
# Each log_search already returns at most 30 entries; this keeps one agent run's slice of
# the shared state (and so every later briefing built from it) bounded.
MAX_LINES_PER_SERVICE = 15


def _quoted_lines(record) -> list[str]:
    """
    Log lines in the exact shape the diagnosis package must cite:
    "<timestamp> <service>: <message>". ERROR and FATAL entries come first, so the cap
    never drops the telling lines in favour of routine INFO ones.
    """
    logs = record.result.get("logs") or []
    service = record.result.get("service", "?")
    ranked = sorted(logs, key=lambda entry: entry.get("level") not in ("ERROR", "FATAL"))
    return [
        f"{entry.get('timestamp', '?')} {service}: {entry.get('message', '')}"
        for entry in ranked[:MAX_LINES_PER_SERVICE]
    ]


def log_agent_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    alert = get_alert(state["alert_id"])
    assignment = state.get("assignment") or {}
    briefing_parts = [
        format_alert(alert),
        format_assignment(assignment),
        "Search each assigned service with log_search over that window, then classify what "
        "you found. Services already searched in earlier rounds: "
        f"{', '.join(state['investigated_services']) or 'none'}.",
    ]
    replan = format_replan_reasons(state.get("replan_reasons"))
    if replan:
        briefing_parts.append(
            replan + "\n\nYour searches are what clears those reasons: cover every service they name."
        )
    briefing = "\n\n".join(briefing_parts)

    outcome = run_specialist(
        state,
        runtime,
        llm=runtime.context.log_llm,
        system_prompt=LOG_AGENT_PROMPT,
        briefing=briefing,
        allowed_tools=ALLOWED_TOOLS,
        label="log",
    )

    searched: list[str] = []
    lines: list[str] = []
    for record in outcome.records:
        if record.name != "log_search" or record.result.get("error"):
            continue
        searched.append(record.result.get("service", "?"))
        lines += _quoted_lines(record)

    entry = {
        "services": searched or (assignment.get("targets") or []),
        "start_time": assignment.get("start_time", ""),
        "end_time": assignment.get("end_time", ""),
        "entries": lines,
        "analysis": outcome.analysis,
    }
    return {**outcome.update, "log_evidence_gathered": [entry]}
