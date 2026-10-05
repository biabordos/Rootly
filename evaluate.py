"""
Run the agent on every alert scenario and write EVAL_RESULTS.md.

    python evaluate.py              # all scenarios, one run each
    python evaluate.py ALRT-003     # a subset
    python evaluate.py --runs 3     # three runs per scenario, reported as success rates

Automatic checks compare each diagnosis with the scenario's ground truth (from
MOCK_DATA_README.md). Root-cause plausibility is left as a manual checkbox.
Requires MISTRAL_API_KEY; each scenario makes several API calls.

Why --runs exists: the orchestrator's routing is decided by an LLM, so a scenario can
pass on one run and exhaust its step budget on the next. A single pass therefore measures
one sample, not the architecture. With --runs N each scenario is run N times and reported
as a success rate with median cost, which is the honest number.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.agent import DiagnosisError, run_diagnosis
from src.agent.observability import setup_phoenix
from src.data_loader import get_alert, load_alerts
from src.eval_ground_truth import GROUND_TRUTH

OUTPUT = Path(__file__).resolve().parent / "EVAL_RESULTS.md"

# Manual baseline per scenario: README §1.1 cites 15-30 min of manual context
# gathering; the harder multi-hop scenarios (8, 9) sit at the top of that range.
MANUAL_BASELINE_MINUTES = {
    "ALRT-001": 20, "ALRT-002": 15, "ALRT-003": 25, "ALRT-004": 15, "ALRT-005": 20,
    "ALRT-006": 20, "ALRT-007": 15, "ALRT-008": 25, "ALRT-009": 30, "ALRT-010": 20,
}


def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


@dataclass
class Attempt:
    """One run of one scenario, scored against the ground truth."""

    result: object | None = None  # DiagnosisResult, or None when the run failed
    error: str = ""
    component: str = ""
    component_ok: bool = False
    deps_ok: bool = False
    evidence_ok: bool = False
    incident_ok: bool = False
    steps: int = 0
    seconds: float = 0.0

    @property
    def completed(self) -> bool:
        return self.result is not None


@dataclass
class Scenario:
    alert_id: str
    service: str
    expected: str
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def completed(self) -> list[Attempt]:
        return [a for a in self.attempts if a.completed]

    @property
    def correct(self) -> int:
        return sum(1 for a in self.attempts if a.component_ok)


def score(alert_id: str, result) -> Attempt:
    truth = GROUND_TRUTH[alert_id]
    d = result.diagnosis
    mentioned = {d.affected_component, *d.critical_dependencies}
    return Attempt(
        result=result,
        component=d.affected_component,
        component_ok=d.affected_component == truth["root"],
        deps_ok=truth["involved"] <= mentioned,
        evidence_ok=bool(d.log_evidence),
        incident_ok=truth["incident"] in d.similar_incidents,
        steps=result.steps,
        seconds=result.elapsed_seconds,
    )


def run_scenario(alert_id: str, runs: int) -> Scenario:
    alert = get_alert(alert_id)
    scenario = Scenario(alert_id, alert.service, GROUND_TRUTH[alert_id]["root"])
    for attempt_index in range(runs):
        label = f"{alert_id}" if runs == 1 else f"{alert_id} ({attempt_index + 1}/{runs})"
        print(f"Running {label}…", flush=True)
        try:
            scenario.attempts.append(score(alert_id, run_diagnosis(alert)))
        except DiagnosisError as exc:
            scenario.attempts.append(Attempt(error=str(exc)))
        except Exception as exc:  # a crash must not discard the runs already paid for
            print(f"  unexpected {type(exc).__name__}: {exc}", flush=True)
            scenario.attempts.append(Attempt(error=f"{type(exc).__name__}: {exc}"))
    return scenario


def single_run_row(scenario: Scenario) -> str:
    attempt = scenario.attempts[0]
    if not attempt.completed:
        return f"| {scenario.alert_id} | {scenario.service} | — | ❌ | — | — | — | — | — | failed: {attempt.error} |"
    d = attempt.result.diagnosis
    return (
        f"| {scenario.alert_id} | {scenario.service} | `{attempt.component}` (expected `{scenario.expected}`) "
        f"| {mark(attempt.component_ok)} | {mark(attempt.deps_ok)} | {mark(attempt.evidence_ok)} "
        f"| {mark(attempt.incident_ok)} | {d.confidence:.2f} "
        f"| `{d.escalation_decision.value if d.escalation_decision else '—'}` "
        f"| {attempt.steps} steps {mark(attempt.steps < 15)} · {attempt.seconds:.1f}s {mark(attempt.seconds < 30)} |"
    )


def multi_run_row(scenario: Scenario) -> str:
    runs = len(scenario.attempts)
    done = scenario.completed
    rate = scenario.correct / runs * 100
    steps = f"{statistics.median(a.steps for a in done):.0f}" if done else "—"
    seconds = f"{statistics.median(a.seconds for a in done):.1f}s" if done else "—"
    wrong = sorted({a.component for a in done if not a.component_ok})
    failed = runs - len(done)
    notes = []
    if failed:
        notes.append(f"{failed} run{'s' if failed > 1 else ''} produced no package")
    if wrong:
        notes.append("also answered " + ", ".join(f"`{w}`" for w in wrong))
    return (
        f"| {scenario.alert_id} | {scenario.service} | `{scenario.expected}` "
        f"| {scenario.correct}/{runs} ({rate:.0f}%) | {steps} | {seconds} "
        f"| {'; '.join(notes) or '—'} |"
    )


def kpi_section(scenarios: list[Scenario], total_runs: int, total_correct: int) -> list[str]:
    done = [a for s in scenarios for a in s.completed]
    if not done:
        return []
    n = len(done)
    total_agent_s = sum(a.seconds for a in done)
    matched_manual_s = sum(MANUAL_BASELINE_MINUTES.get(a.result.alert.id, 20) * 60 for a in done)
    auto = sum(
        1 for a in done
        if a.result.diagnosis.escalation_decision
        and a.result.diagnosis.escalation_decision.value == "auto_resolved"
    )
    urgent = sum(1 for a in done if a.result.needs_approval)
    return [
        "## KPI Summary — Manual vs. Rootly",
        "",
        f"Aggregated over the {n} run(s) that produced a diagnosis.",
        "",
        "| Metrica | Manual (estimat) | Rootly | Diferenta |",
        "|---|---|---|---|",
        f"| Timp mediu pana la diagnostic | {matched_manual_s / n / 60:.0f} min | {total_agent_s / n:.0f}s "
        f"| {(1 - total_agent_s / matched_manual_s) * 100:.0f}% mai rapid |",
        f"| Cazuri auto-rezolvate | 0% | {auto}/{n} ({auto / n * 100:.0f}%) | +{auto / n * 100:.0f}pp |",
        f"| Cazuri care ajung la un om | 100% | {urgent}/{n} ({urgent / n * 100:.0f}%) "
        f"| {(1 - urgent / n) * 100:.0f}pp mai putin |",
        f"| Acuratete componenta afectata | variabila | {total_correct}/{total_runs} "
        f"({total_correct / total_runs * 100:.0f}%) | — |",
        "",
    ]


def build_report(scenarios: list[Scenario], runs: int) -> list[str]:
    total_runs = sum(len(s.attempts) for s in scenarios)
    total_correct = sum(s.correct for s in scenarios)

    if runs == 1:
        headline = f"**Root component correctly identified:** {total_correct}/{len(scenarios)}"
        header = ("| Alert | Service | Affected component | Correct | Dependencies | Log evidence "
                  "| Similar incident | Confidence | Escalation | Steps · Time |")
        divider = "|---|---|---|---|---|---|---|---|---|---|"
        rows = [single_run_row(s) for s in scenarios]
        legend = ("Targets: steps < 15, time < 30 s. Similar incident = the direct match from "
                  "MOCK_DATA_README.md was cited.")
    else:
        perfect = sum(1 for s in scenarios if s.correct == len(s.attempts))
        headline = (
            f"**Root component correctly identified:** {total_correct}/{total_runs} runs "
            f"({total_correct / total_runs * 100:.0f}%) over {len(scenarios)} scenarios × {runs} runs. "
            f"{perfect}/{len(scenarios)} scenarios were correct on every run."
        )
        header = "| Alert | Service | Expected root | Success rate | Steps (median) | Time (median) | Notes |"
        divider = "|---|---|---|---|---|---|---|"
        rows = [multi_run_row(s) for s in scenarios]
        legend = (
            "Routing is decided by an LLM, so the same scenario can pass on one run and exhaust its "
            "step budget on the next. A success rate over several runs is the honest measure; a single "
            "pass is one sample. Targets: steps < 15, time < 30 s."
        )

    details: list[str] = []
    for scenario in scenarios:
        best = next((a for a in scenario.attempts if a.component_ok), None) or next(iter(scenario.completed), None)
        details += [f"### {scenario.alert_id} — {scenario.service}", "", "- [ ] Root cause plausible (manual review)", ""]
        if best is None:
            details += [f"**No run produced a diagnosis.** Last error: {scenario.attempts[-1].error}", ""]
            continue
        if runs > 1:
            details += [f"*Shown: a correct run out of {len(scenario.attempts)}.*"
                        if best.component_ok else
                        f"*Shown: an incorrect run; no run out of {len(scenario.attempts)} found the expected root.*", ""]
        details += [
            f"**Hypothesis:** {best.result.diagnosis.root_cause_hypothesis}", "",
            f"**Escalation:** {best.result.diagnosis.escalation_recommendation}", "",
        ]

    return [
        "# Rootly — Evaluation Results (multi-agent)",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by "
        f"`python evaluate.py{'' if runs == 1 else f' --runs {runs}'}`.",
        "",
        "Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent"
        " reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).",
        "",
        headline,
        "",
        header,
        divider,
        *rows,
        "",
        legend,
        "",
        *kpi_section(scenarios, total_runs, total_correct),
        "## Per-scenario review",
        "",
        *details,
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run Rootly against the alert scenarios and write EVAL_RESULTS.md")
    parser.add_argument("alert_ids", nargs="*", help="Scenarios to run, e.g. ALRT-001 (default: all)")
    parser.add_argument("--runs", type=int, default=1, metavar="N",
                        help="Run each scenario N times and report success rates (default: 1)")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    load_dotenv()
    setup_phoenix()
    alert_ids = args.alert_ids or [a.id for a in load_alerts()]
    unknown = [a for a in alert_ids if a not in GROUND_TRUTH]
    if unknown:
        parser.error(f"unknown alert id(s): {', '.join(unknown)}")

    scenarios = [run_scenario(alert_id, args.runs) for alert_id in alert_ids]
    OUTPUT.write_text("\n".join(build_report(scenarios, args.runs)), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
