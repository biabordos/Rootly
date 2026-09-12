"""Serialize a DiagnosisResult to JSON-ready dicts and human-readable Markdown."""

from __future__ import annotations

from src.agent.react_loop import DiagnosisResult


def diagnosis_dict(result: DiagnosisResult) -> dict:
    return {
        **result.diagnosis.model_dump(mode="json"),
        "run": {
            "model": result.model,
            "steps": result.steps,
            "tool_calls": result.tool_calls,
            "elapsed_seconds": result.elapsed_seconds,
            "usage": result.usage,
        },
    }


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
    ]
    return "\n".join(lines)
