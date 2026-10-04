"""
Run the agent on every alert scenario and write EVAL_RESULTS.md.

    python evaluate.py              # all scenarios
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
from src.agent.observability import setup_phoenix
from src.data_loader import get_alert, load_alerts
from src.eval_ground_truth import GROUND_TRUTH

OUTPUT = Path(__file__).resolve().parent / "EVAL_RESULTS.md"


def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def main(argv: list[str]) -> int:
    load_dotenv()
    setup_phoenix()
    alert_ids = argv or [a.id for a in load_alerts()]
    rows, details = [], []
    results_list: list = []
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
        results_list.append(result)

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

    # Manual baseline per scenario: README §1.1 cites 15-30 min of manual context
    # gathering; the harder multi-hop scenarios (8, 9) sit at the top of that range.
    MANUAL_BASELINE_MINUTES = {
        "ALRT-001": 20, "ALRT-002": 15, "ALRT-003": 25, "ALRT-004": 15, "ALRT-005": 20,
        "ALRT-006": 20, "ALRT-007": 15, "ALRT-008": 25, "ALRT-009": 30, "ALRT-010": 20,
    }
    kpi_lines: list[str] = []
    if results_list:
        n = len(results_list)
        total_agent_s = sum(r.elapsed_seconds for r in results_list)
        matched_manual_s = sum(
            MANUAL_BASELINE_MINUTES.get(r.alert.id, 20) * 60 for r in results_list
        )
        auto = sum(
            1 for r in results_list
            if r.diagnosis.escalation_decision
            and r.diagnosis.escalation_decision.value == "auto_resolved"
        )
        urgent = sum(1 for r in results_list if r.needs_approval)
        avg_agent = total_agent_s / n
        avg_manual = matched_manual_s / n / 60

        kpi_lines = [
            "## KPI Summary — Manual vs. Rootly",
            "",
            f"Aggregated over the {n} scenario(s) that produced a diagnosis.",
            "",
            "| Metrica | Manual (estimat) | Rootly | Diferenta |",
            "|---|---|---|---|",
            f"| Timp mediu pana la diagnostic | {avg_manual:.0f} min | {avg_agent:.0f}s | {(1 - total_agent_s / matched_manual_s) * 100:.0f}% mai rapid |",
            f"| Cazuri auto-rezolvate | 0% | {auto}/{n} ({auto / n * 100:.0f}%) | +{auto / n * 100:.0f}pp |",
            f"| Cazuri care ajung la un om | 100% | {urgent}/{n} ({urgent / n * 100:.0f}%) | {(1 - urgent / n) * 100:.0f}pp mai putin |",
            f"| Acuratete componenta afectata | variabila | {correct}/{len(alert_ids)} ({correct / len(alert_ids) * 100:.0f}%) | — |",
            "",
        ]

    report = [
        "# Rootly — Evaluation Results (multi-agent)",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python evaluate.py`.",
        "",
        "Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent"
        " reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).",
        "",
        f"**Root component correctly identified:** {correct}/{len(alert_ids)}",
        "",
        "| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.",
        "",
        *kpi_lines,
        "## Per-scenario review",
        "",
        *details,
    ]
    OUTPUT.write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
