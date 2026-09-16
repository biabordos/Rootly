"""
Run the agent on every alert scenario and write EVAL_RESULTS.md.

    python evaluate.py              # all 5 scenarios
    python evaluate.py ALRT-003     # a subset

Automatic checks compare each diagnosis with the scenario's ground truth (from
MOCK_DATA_README.md). Root-cause plausibility is left as a manual checkbox.
Requires MISTRAL_API_KEY; each scenario makes several API calls.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.agent import DiagnosisError, run_diagnosis
from src.data_loader import get_alert, load_alerts
from src.eval_ground_truth import GROUND_TRUTH

OUTPUT = Path(__file__).resolve().parent / "EVAL_RESULTS.md"


def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def main(argv: list[str]) -> int:
    load_dotenv()
    alert_ids = argv or [a.id for a in load_alerts()]
    rows, details = [], []
    correct = 0

    for alert_id in alert_ids:
        alert, truth = get_alert(alert_id), GROUND_TRUTH[alert_id]
        print(f"Running {alert_id}…", flush=True)
        try:
            result = run_diagnosis(alert)
        except DiagnosisError as exc:
            rows.append(f"| {alert_id} | {alert.service} | — | ❌ | — | — | — | — | — | failed: {exc} |")
            continue

        d = result.diagnosis
        mentioned = {d.affected_component, *d.critical_dependencies}
        component_ok = d.affected_component == truth["root"]
        deps_ok = truth["involved"] <= mentioned
        incident_ok = truth["incident"] in d.similar_incidents
        evidence_ok = bool(d.log_evidence)
        steps_ok = result.steps < 15
        time_ok = result.elapsed_seconds < 30
        correct += component_ok

        rows.append(
            f"| {alert_id} | {alert.service} | `{d.affected_component}` (expected `{truth['root']}`) | {mark(component_ok)} "
            f"| {mark(deps_ok)} | {mark(evidence_ok)} | {mark(incident_ok)} | {d.confidence:.2f} "
            f"| `{d.escalation_decision.value if d.escalation_decision else '—'}` "
            f"| {result.steps} steps {mark(steps_ok)} · {result.elapsed_seconds:.1f}s {mark(time_ok)} |"
        )
        details += [
            f"### {alert_id} — {alert.service}",
            "",
            "- [ ] Root cause plausible (manual review)",
            "",
            f"**Hypothesis:** {d.root_cause_hypothesis}",
            "",
            f"**Escalation:** {d.escalation_recommendation}",
            "",
        ]

    report = [
        "# Rootly — Evaluation Results",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python evaluate.py`.",
        "",
        f"**Root component correctly identified:** {correct}/{len(alert_ids)}",
        "",
        "| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.",
        "",
        "## Per-scenario review",
        "",
        *details,
    ]
    OUTPUT.write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
