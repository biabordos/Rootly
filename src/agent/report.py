"""Serialize a DiagnosisResult to JSON-ready dicts and human-readable Markdown."""

from __future__ import annotations

from src.agent.graph import DiagnosisResult

ESCALATION_LABEL = {
    "auto_resolved": "Auto-resolved",
    "escalate_normal": "Normal escalation to L2",
    "escalate_urgent_needs_approval": "Urgent escalation (needs human approval)",
}
HUMAN_DECISION_LABEL = {
    "approve": "approved the urgent escalation",
    "downgrade": "downgraded to normal — not urgent",
}


def diagnosis_dict(result: DiagnosisResult) -> dict:
    return {
        **result.diagnosis.model_dump(mode="json"),
        "run": {
            "thread_id": result.thread_id,
            "needs_approval": result.needs_approval,
            "model": result.model,
            "steps": result.steps,
            "tool_calls": result.tool_calls,
            "elapsed_seconds": result.elapsed_seconds,
            "usage": result.usage,
        },
    }


def escalation_lines(result: DiagnosisResult) -> list[str]:
    d = result.diagnosis
    if not d.escalation_decision:
        return []
    lines = [
        "## Escalation",
        f"**Decision:** {ESCALATION_LABEL[d.escalation_decision.value]} · **Owner team:** {d.owner_team or '—'}",
        "",
        d.escalation_reason or "",
        "",
    ]
    if result.needs_approval:
        lines += ["**Status:** ⏸ paused, waiting for human approval", ""]
    if d.human_decision:
        lines += [f"**Human decision:** {HUMAN_DECISION_LABEL.get(d.human_decision, d.human_decision)}"]
        if d.human_decision_note:
            lines += [f"**Note:** {d.human_decision_note}"]
        lines += [""]
    return lines


def to_markdown(result: DiagnosisResult) -> str:
    d = result.diagnosis
    alert = result.alert
    lines = [
        f"# Diagnosis — {alert.id}",
        "",
        f"**Alert:** {alert.service} · {alert.alert_type.value} · reported {alert.severity_reported.value} · "
        f"{alert.timestamp.isoformat().replace('+00:00', 'Z')}",
        f"**Assessed severity:** {d.severity_assessed.value} · **Confidence:** {d.confidence:.2f}",
        f"**Investigation:** {result.steps} steps, {result.tool_calls} tool calls, {result.elapsed_seconds:.1f}s ({result.model})",
        "",
        "## Summary",
        d.summary,
        "",
        "## Root cause hypothesis",
        f"**Affected component:** `{d.affected_component}`",
        "",
        d.root_cause_hypothesis,
        "",
        "## Critical dependencies",
        *([f"- `{dep}`" for dep in d.critical_dependencies] or ["- none identified"]),
        "",
        "## Log evidence",
        *[f"- `{line}`" for line in d.log_evidence],
        "",
        "## Similar historical incidents",
        *([f"- {inc}" for inc in d.similar_incidents] or ["- none"]),
        "",
        "## Escalation recommendation",
        d.escalation_recommendation,
        "",
        *escalation_lines(result),
    ]
    return "\n".join(lines)
