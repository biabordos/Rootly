"""
CMDB analysis specialist: given component names, look them up and read the dependency
chain (depends_on = candidate upstream causes) and the blast radius (depended_by).

Writes one entry per run to state["cmdb_context"], holding the raw CMDB records plus the
specialist's own reading of them. The orchestrator and the synthesis agent both read that
field; neither ever sees this agent's conversation.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from src.agent.graph_nodes import run_specialist
from src.agent.graph_state import RootlyContext, RootlyState
from src.agent.system_prompt import CMDB_AGENT_PROMPT, format_alert, format_assignment
from src.data_loader import get_alert

ALLOWED_TOOLS = {"cmdb_lookup"}


def _components(outcome) -> list[dict]:
    """The CMDB records this run retrieved, as plain data for the checkpoint."""
    components: list[dict] = []
    for record in outcome.records:
        if record.name != "cmdb_lookup":
            continue
        if record.result.get("found"):
            components.append(record.result["component"])
        else:
            components.append({
                "name": str(record.input.get("component_name", "?")).strip().lower(),
                "found": False,
            })
    return components


def cmdb_agent_node(state: RootlyState, runtime: Runtime[RootlyContext]) -> dict:
    alert = get_alert(state["alert_id"])
    assignment = state.get("assignment") or {}
    briefing = "\n\n".join([
        format_alert(alert),
        format_assignment(assignment),
        "Look up each assigned component with cmdb_lookup, then report your analysis.",
    ])

    outcome = run_specialist(
        state,
        runtime,
        llm=runtime.context.cmdb_llm,
        system_prompt=CMDB_AGENT_PROMPT,
        briefing=briefing,
        allowed_tools=ALLOWED_TOOLS,
        label="CMDB",
    )

    entry = {
        "targets": assignment.get("targets") or [],
        "components": _components(outcome),
        "analysis": outcome.analysis,
    }
    return {**outcome.update, "cmdb_context": [entry]}
