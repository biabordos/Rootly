"""
Evaluate the agent against every alert scenario and write EVAL_RESULTS.md.

    python evaluate.py                          # smart retry (up to 3 retries)
    python evaluate.py ALRT-005                 # evaluate one scenario
    python evaluate.py --max-retries 5          # configure smart retry
    python evaluate.py --runs 10                # statistical mode
    python evaluate.py --runs 10 ALRT-005       # statistical mode, one scenario
    python evaluate.py --runs 5 --json report.json

Automatic checks compare each diagnosis with the scenario's ground truth (from
MOCK_DATA_README.md). Root-cause plausibility remains a manual review.
Requires MISTRAL_API_KEY; each diagnosis makes several API calls.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.agent import DiagnosisError, DiagnosisResult, resume_diagnosis, run_diagnosis
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
    """One diagnosis run for one scenario."""

    result: DiagnosisResult | None = None
    error: str = ""
    component: str = ""
    component_ok: bool = False
    deps_ok: bool = False
    evidence_ok: bool = False
    incident_ok: bool = False
    steps: int = 0
    seconds: float = 0.0
    needed_approval: bool = False

    @property
    def completed(self) -> bool:
        return self.result is not None


@dataclass
class Scenario:
    alert_id: str
    service: str
    expected: str
    attempts: list[Attempt] = field(default_factory=list)
    status: str = ""

    @property
    def completed(self) -> list[Attempt]:
        return [attempt for attempt in self.attempts if attempt.completed]

    @property
    def correct(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.component_ok)


def score(alert_id: str, result: DiagnosisResult, *, needed_approval: bool = False) -> Attempt:
    truth = GROUND_TRUTH[alert_id]
    diagnosis = result.diagnosis
    mentioned = {diagnosis.affected_component, *diagnosis.critical_dependencies}
    return Attempt(
        result=result,
        component=diagnosis.affected_component,
        component_ok=diagnosis.affected_component == truth["root"],
        deps_ok=truth["involved"] <= mentioned,
        evidence_ok=bool(diagnosis.log_evidence),
        incident_ok=truth["incident"] in diagnosis.similar_incidents,
        steps=result.steps,
        seconds=result.elapsed_seconds,
        needed_approval=needed_approval,
    )


def run_one(alert_id: str) -> Attempt:
    """Run a diagnosis and resolve any human-approval pause automatically."""
    result = run_diagnosis(get_alert(alert_id))
    needed_approval = result.needs_approval
    if needed_approval:
        print("  urgent escalation paused; auto-approving...", flush=True)
        result = resume_diagnosis(
            result.thread_id,
            "approve",
            note="auto-approved by evaluate.py",
        )
    return score(alert_id, result, needed_approval=needed_approval)


def run_with_retry(alert_id: str, truth: dict, max_retries: int) -> dict:
    """Retry failed diagnoses until the root component is correct or retries run out."""
    attempts: list[Attempt] = []
    for attempt_number in range(1, max_retries + 2):
        print(f"  attempt {attempt_number}/{max_retries + 1}...", flush=True)
        try:
            attempt = run_one(alert_id)
        except DiagnosisError as exc:
            attempts.append(Attempt(error=str(exc)))
            if attempt_number <= max_retries:
                print(f"  attempt {attempt_number} error: {exc}, retrying...", flush=True)
                continue
            return {"status": "error", "error": str(exc), "attempts": attempt_number,
                    "history": attempts}

        attempts.append(attempt)
        if attempt.component_ok:
            return {
                "status": "pass",
                "result": attempt.result,
                "attempts": attempt_number,
                "component_ok": True,
                "got_component": attempt.component,
                "history": attempts,
            }
        if attempt_number <= max_retries:
            print(
                f"  attempt {attempt_number}: got {attempt.component}, "
                f"expected {truth['root']}, retrying...",
                flush=True,
            )

    final = attempts[-1]
    return {
        "status": "fail",
        "result": final.result,
        "attempts": len(attempts),
        "component_ok": False,
        "got_component": final.component or None,
        "history": attempts,
    }


def run_scenario(
    alert_id: str,
    runs: int = 1,
    max_retries: int = 3,
    *,
    statistical: bool = False,
) -> Scenario:
    alert = get_alert(alert_id)
    scenario = Scenario(alert_id, alert.service, GROUND_TRUTH[alert_id]["root"])

    if runs == 1 and not statistical:
        retry_result = run_with_retry(alert_id, GROUND_TRUTH[alert_id], max_retries)
        scenario.attempts = retry_result["history"]
        scenario.status = retry_result["status"]
        return scenario

    for run_number in range(1, runs + 1):
        print(f"  run {run_number}/{runs}...", end=" ", flush=True)
        try:
            attempt = run_one(alert_id)
            scenario.attempts.append(attempt)
            print(f"{'PASS' if attempt.component_ok else 'FAIL'} ({attempt.component})", flush=True)
        except DiagnosisError as exc:
            scenario.attempts.append(Attempt(error=str(exc)))
            print(f"ERROR: {exc}", flush=True)
    return scenario


def kpi_section(scenarios: list[Scenario], total_runs: int, total_correct: int) -> list[str]:
    completed = [attempt for scenario in scenarios for attempt in scenario.completed]
    if not completed:
        return []

    run_count = len(completed)
    total_agent_seconds = sum(attempt.seconds for attempt in completed)
    matched_manual_seconds = sum(
        MANUAL_BASELINE_MINUTES.get(attempt.result.alert.id, 20) * 60 for attempt in completed
    )
    auto_resolved = sum(
        1 for attempt in completed
        if attempt.result.diagnosis.escalation_decision
        and attempt.result.diagnosis.escalation_decision.value == "auto_resolved"
    )
    urgent = sum(1 for attempt in completed if attempt.needed_approval)
    return [
        "## KPI Summary — Manual vs. Rootly",
        "",
        f"Aggregated over the {run_count} run(s) that produced a diagnosis.",
        "",
        "| Metrica | Manual (estimat) | Rootly | Diferenta |",
        "|---|---|---|---|",
        f"| Timp mediu pana la diagnostic | {matched_manual_seconds / run_count / 60:.0f} min "
        f"| {total_agent_seconds / run_count:.1f}s "
        f"| {(1 - total_agent_seconds / matched_manual_seconds) * 100:.0f}% mai rapid |",
        f"| Cazuri auto-rezolvate | 0% | {auto_resolved}/{run_count} "
        f"({auto_resolved / run_count * 100:.0f}%) | +{auto_resolved / run_count * 100:.0f}pp |",
        f"| Cazuri care ajung la un om | 100% | {urgent}/{run_count} "
        f"({urgent / run_count * 100:.0f}%) | {(1 - urgent / run_count) * 100:.0f}pp mai putin |",
        f"| Acuratete componenta afectata | variabila | {total_correct}/{total_runs} "
        f"({total_correct / total_runs * 100:.0f}%) | — |",
        "",
    ]


def _average(values: list[float], precision: int = 1) -> str:
    return f"{statistics.mean(values):.{precision}f}" if values else "—"


def _failure_components(scenario: Scenario) -> Counter[str]:
    return Counter(
        attempt.component
        for attempt in scenario.completed
        if not attempt.component_ok and attempt.component
    )


def _failure_component_text(scenario: Scenario) -> str:
    counts = _failure_components(scenario)
    if not counts:
        return "—"
    return ", ".join(
        f"`{component}` ({count}x)" for component, count in sorted(counts.items())
    )


def _retry_report(scenarios: list[Scenario], max_retries: int, command: str) -> list[str]:
    statuses = {
        scenario.alert_id: (
            scenario.status
            or ("pass" if scenario.attempts[-1].component_ok else
                "fail" if scenario.attempts[-1].completed else "error")
        )
        for scenario in scenarios
    }
    passed = sum(1 for scenario in scenarios if statuses[scenario.alert_id] == "pass")
    rows = []
    for scenario in scenarios:
        final = scenario.attempts[-1]
        if final.completed:
            diagnosis = final.result.diagnosis
            status = statuses[scenario.alert_id]
            if status == "pass" and len(scenario.attempts) > 1:
                status = f"pass (retry {len(scenario.attempts) - 1})"
            component = f"`{final.component}` (expected `{scenario.expected}`)"
            checks = (
                f"| {len(scenario.attempts)} | {mark(final.deps_ok)} "
                f"| {mark(final.evidence_ok)} | {mark(final.incident_ok)} "
                f"| {diagnosis.confidence:.2f} | "
                f"{diagnosis.escalation_decision.value if diagnosis.escalation_decision else '—'} "
                f"| {final.steps} steps {mark(final.steps < 15)} · "
                f"{final.seconds:.1f}s {mark(final.seconds < 30)} |"
            )
        else:
            status = statuses[scenario.alert_id]
            component = f"— (expected `{scenario.expected}`)"
            checks = f"| {len(scenario.attempts)} | — | — | — | — | — | failed: {final.error} |"
        rows.append(
            f"| {scenario.alert_id} | {scenario.service} | {component} | {status} "
            f"{checks}"
        )

    total_attempts = sum(len(scenario.attempts) for scenario in scenarios)
    completed_correct = sum(scenario.correct for scenario in scenarios)
    return [
        "# Rootly — Evaluation Results",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `{command}`.",
        "",
        f"**Root component correctly identified:** {passed}/{len(scenarios)} scenarios",
        "",
        "| Alert | Service | Affected component | Correct | Attempts | Dependencies "
        "| Log evidence | Similar incident | Confidence | Escalation | Steps / Time |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        f"Smart retry stops at the first correct root component; up to {max_retries} retries "
        "follow the initial attempt. Dependencies, evidence and incident are reported for the final run.",
        "",
        *kpi_section(scenarios, total_attempts, completed_correct),
    ]


def _statistical_report(scenarios: list[Scenario], runs: int, command: str) -> list[str]:
    total_runs = sum(len(scenario.attempts) for scenario in scenarios)
    total_correct = sum(scenario.correct for scenario in scenarios)
    rows: list[str] = []
    failure_analysis: list[str] = ["## Failure analysis", ""]

    for scenario in scenarios:
        completed = scenario.completed
        failures = len(scenario.attempts) - scenario.correct
        confidences = [attempt.result.diagnosis.confidence for attempt in completed]
        rows.append(
            f"| {scenario.alert_id} | {scenario.service} | "
            f"{scenario.correct}/{len(scenario.attempts)} "
            f"({scenario.correct / len(scenario.attempts) * 100:.0f}%) "
            f"| {_average([float(attempt.steps) for attempt in completed])} "
            f"| {_average([attempt.seconds for attempt in completed])}s "
            f"| {_average(confidences, 2)} | {_failure_component_text(scenario)} |"
        )

        if not failures:
            continue
        failed_runs = [
            attempt for attempt in scenario.attempts
            if not attempt.completed or not attempt.component_ok
        ]
        component_counts = _failure_components(scenario)
        reasons = [
            f"{count}x stopped at `{component}`"
            for component, count in sorted(component_counts.items())
        ]
        error_count = sum(1 for attempt in failed_runs if not attempt.completed)
        if error_count:
            reasons.append(f"{error_count} run{'s' if error_count != 1 else ''} produced no package")
        failure_analysis.extend([
            f"### {scenario.alert_id} — {failures} failure(s) in {len(scenario.attempts)} runs",
            "",
            "- " + ("; ".join(reasons) if reasons else "No diagnosis matched the expected component."),
            "",
        ])

    perfect = sum(
        1 for scenario in scenarios
        if scenario.attempts and scenario.correct == len(scenario.attempts)
    )
    review: list[str] = ["## Per-scenario review", ""]
    for scenario in scenarios:
        best = next((attempt for attempt in scenario.attempts if attempt.component_ok), None)
        if best is None:
            best = next(iter(scenario.completed), None)
        review.extend([
            f"### {scenario.alert_id} — {scenario.service}",
            "",
            "- [ ] Root cause plausible (manual review)",
            "",
        ])
        if best is None:
            review.extend([
                "**No run produced a diagnosis.** "
                f"Last error: {scenario.attempts[-1].error}",
                "",
            ])
            continue
        review.extend([
            f"*Shown: {'a correct run' if best.component_ok else 'an incorrect run; no run'} "
            f"out of {len(scenario.attempts)}"
            f"{'' if best.component_ok else ' found the expected root' }.*",
            "",
            f"**Hypothesis:** {best.result.diagnosis.root_cause_hypothesis}",
            "",
            f"**Escalation:** {best.result.diagnosis.escalation_recommendation}",
            "",
        ])
    return [
        f"# Rootly — Statistical Evaluation (N={runs})",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `{command}`.",
        "",
        "## Success rates",
        "",
        "| Alert | Service | Success rate | Avg steps | Avg time | Avg confidence | Failure components |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        f"**Root component correctly identified:** {total_correct}/{total_runs} runs "
        f"({total_correct / total_runs * 100:.0f}%) over {len(scenarios)} scenarios × {runs} runs. "
        f"{perfect}/{len(scenarios)} scenarios were correct on every run.",
        "",
        *failure_analysis,
        *review,
        *kpi_section(scenarios, total_runs, total_correct),
    ]


def build_report(scenarios: list[Scenario], runs: int | None, max_retries: int = 3,
                 command: str | None = None, *, statistical: bool | None = None) -> list[str]:
    """Build the Markdown output for either evaluation mode."""
    if command is None:
        command = "python evaluate.py" + (f" --runs {runs}" if runs is not None else "")
    if statistical is None:
        statistical = runs is not None and runs > 1
    if not statistical:
        return _retry_report(scenarios, max_retries, command)
    if runs is None:
        raise ValueError("statistical mode requires a run count")
    return _statistical_report(scenarios, runs, command)


def _scenario_json(scenario: Scenario, statistical_runs: int | None) -> dict:
    if statistical_runs is None:
        final = scenario.attempts[-1]
        report = {
            "status": scenario.status,
            "attempts": len(scenario.attempts),
            "component": final.component or None,
            "expected": scenario.expected,
        }
        if final.error:
            report["error"] = final.error
        return report

    completed = scenario.completed
    failure_components = [
        attempt.component for attempt in scenario.attempts
        if not attempt.component_ok and attempt.component
    ]
    report = {
        "success_rate": scenario.correct / len(scenario.attempts),
        "successes": scenario.correct,
        "total": len(scenario.attempts),
        "avg_steps": statistics.mean(attempt.steps for attempt in completed) if completed else 0,
        "avg_elapsed": statistics.mean(attempt.seconds for attempt in completed) if completed else 0,
        "avg_confidence": (
            statistics.mean(attempt.result.diagnosis.confidence for attempt in completed)
            if completed else 0
        ),
        "failure_components": failure_components,
        "runs": [
            {
                "component": attempt.component or None,
                "correct": attempt.component_ok,
                **({
                    "steps": attempt.steps,
                    "elapsed": attempt.seconds,
                    "confidence": attempt.result.diagnosis.confidence,
                    "incident_cited": attempt.incident_ok,
                } if attempt.completed else {"error": attempt.error}),
            }
            for attempt in scenario.attempts
        ],
    }
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Rootly evaluation")
    parser.add_argument("alert_ids", nargs="*", help="Specific alerts (default: all)")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Smart retry: max retries per failed scenario (default 3)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Statistical mode: run each scenario N times",
    )
    parser.add_argument("--json", metavar="PATH", help="Write machine-readable results to JSON")
    args = parser.parse_args(argv)
    if args.max_retries < 0:
        parser.error("--max-retries must be zero or greater")
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be at least 1")

    load_dotenv()
    setup_phoenix()
    alert_ids = args.alert_ids or [alert.id for alert in load_alerts()]
    unknown = [alert_id for alert_id in alert_ids if alert_id not in GROUND_TRUTH]
    if unknown:
        parser.error(f"unknown alert id(s): {', '.join(unknown)}")

    scenarios = []
    if args.runs is None:
        for alert_id in alert_ids:
            if args.max_retries == 3:
                scenarios.append(run_scenario(alert_id, 1))
            else:
                scenarios.append(run_scenario(alert_id, 1, args.max_retries))
    elif args.runs == 1:
        scenarios = [
            run_scenario(alert_id, 1, args.max_retries, statistical=True)
            for alert_id in alert_ids
        ]
    else:
        scenarios = [run_scenario(alert_id, args.runs) for alert_id in alert_ids]
    command = "python evaluate.py"
    if args.runs is not None:
        command += f" --runs {args.runs}"
    elif args.max_retries != 3:
        command += f" --max-retries {args.max_retries}"
    if alert_ids != [alert.id for alert in load_alerts()]:
        command += " " + " ".join(alert_ids)

    OUTPUT.write_text(
        "\n".join(build_report(
            scenarios,
            args.runs,
            args.max_retries,
            command,
            statistical=args.runs is not None,
        )),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")

    if args.json:
        report = {
            "mode": "statistical" if args.runs is not None else "retry",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scenarios": {
                scenario.alert_id: _scenario_json(scenario, args.runs)
                for scenario in scenarios
            },
        }
        if args.runs is None:
            report["max_retries"] = args.max_retries
        else:
            report["runs_per_scenario"] = args.runs
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
