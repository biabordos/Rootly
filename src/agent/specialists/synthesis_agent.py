"""
Diagnosis synthesis specialist: the last agent on the investigation.

It receives the CMDB context and the log evidence the other two gathered, searches the
historical incident corpus for a matching pattern, and calls submit_diagnosis. That call
is deliberately *not* executed here -- run_specialist leaves it in the shared message log
for guardrail_node to validate, so the whole existing guardrail (CMDB existence, log
evidence, origin check, transitive reachability, historical-search check) applies
unchanged to the multi-agent flow.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from src.agent.graph_nodes import run_specialist
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.system_prompt import (
    SYNTHESIS_AGENT_PROMPT,
    format_alert,
    format_cmdb_context,
    format_log_evidence,
    format_replan_reasons,
)
from src.data_loader import get_alert

ALLOWED_TOOLS = {"similar_incidents_search"}


def synthesis_agent_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    alert = get_alert(state["alert_id"])
    briefing_parts = [
        format_alert(alert),
        format_cmdb_context(state.get("cmdb_context") or []),
        format_log_evidence(state.get("log_evidence_gathered") or []),
        f"Services whose logs were searched: {', '.join(state['investigated_services']) or 'none'}.",
    ]
    replan = format_replan_reasons(state.get("replan_reasons"))
    if replan:
        briefing_parts.append(
            replan + "\n\nFix exactly these problems in the package you submit now."
        )
    briefing = "\n\n".join(briefing_parts)

    outcome = run_specialist(
        state,
        runtime,
        llm=runtime.context.synthesis_llm,
        system_prompt=SYNTHESIS_AGENT_PROMPT,
        briefing=briefing,
        allowed_tools=ALLOWED_TOOLS,
        label="synthesis",
        stop_on_submit=True,
    )
    # No field of its own: the package travels as the submit_diagnosis call inside
    # outcome.update["messages"], which guardrail_node reads off the last AIMessage.
    return outcome.update
