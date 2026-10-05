"""
Tests for the evaluation harness's reporting, driven by stub results so no API key and
no model call is involved.

The point of --runs N is that the orchestrator's routing is LLM-decided, so a scenario can
pass on one run and exhaust its budget on the next. These tests pin the two report shapes
and the arithmetic behind the success rates, which is what the presentation numbers rest on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import evaluate
from src.eval_ground_truth import GROUND_TRUTH
from src.models.schemas import EscalationDecision


def stub_result(alert_id: str, component: str, steps: int, seconds: float, *,
                needs_approval: bool = False, auto_resolved: bool = False):
    diagnosis = SimpleNamespace(
        affected_component=component,
        critical_dependencies=sorted(GROUND_TRUTH[alert_id]["involved"]),
        log_evidence=["2026-08-18T09:09:15Z payments-db: Max connections reached (20/20)"],
        similar_incidents=[GROUND_TRUTH[alert_id]["incident"]],
        confidence=0.9,
        escalation_decision=EscalationDecision.AUTO_RESOLVED if auto_resolved else EscalationDecision.ESCALATE_NORMAL,
        root_cause_hypothesis=f"hypothesis for {alert_id}",
        escalation_recommendation=f"page someone about {alert_id}",
    )
    return SimpleNamespace(
        diagnosis=diagnosis, steps=steps, elapsed_seconds=seconds,
        needs_approval=needs_approval, alert=SimpleNamespace(id=alert_id),
    )


def scenario(alert_id: str, outcomes: list) -> evaluate.Scenario:
    """outcomes: (component, steps, seconds) per run, or None for a run that produced nothing."""
    built = evaluate.Scenario(alert_id, evaluate.get_alert(alert_id).service, GROUND_TRUTH[alert_id]["root"])
    for outcome in outcomes:
        if outcome is None:
            built.attempts.append(evaluate.Attempt(error="No valid diagnosis package after 32 steps."))
        else:
            built.attempts.append(evaluate.score(alert_id, stub_result(alert_id, *outcome)))
    return built


def test_a_correct_run_scores_every_check():
    attempt = evaluate.score("ALRT-001", stub_result("ALRT-001", "payments-db", 9, 11.0))
    assert attempt.completed
    assert attempt.component_ok and attempt.deps_ok and attempt.evidence_ok and attempt.incident_ok


def test_a_wrong_component_fails_only_the_component_check():
    attempt = evaluate.score("ALRT-001", stub_result("ALRT-001", "checkout-api", 9, 11.0))
    assert not attempt.component_ok
    assert attempt.evidence_ok and attempt.incident_ok


def test_single_run_report_keeps_the_original_table():
    report = "\n".join(evaluate.build_report([
        scenario("ALRT-001", [("payments-db", 12, 37.2)]),
        scenario("ALRT-004", [None]),
    ], runs=1))

    assert "**Root component correctly identified:** 1/2" in report
    assert "| Alert | Service | Affected component | Correct |" in report
    assert "`payments-db` (expected `payments-db`)" in report
    assert "12 steps ✅ · 37.2s ❌" in report  # over the 30s target, under the 15-step one
    assert "failed: No valid diagnosis package after 32 steps." in report
    assert "Success rate" not in report  # the statistical table belongs to --runs N only


def test_multi_run_report_shows_success_rates_and_medians():
    report = "\n".join(evaluate.build_report([
        scenario("ALRT-001", [("payments-db", 9, 11.0), ("payments-db", 12, 14.0), ("payments-db", 9, 10.0)]),
        scenario("ALRT-004", [("order-service", 15, 21.4), None, ("payments-db", 20, 25.0)]),
    ], runs=3))

    assert "4/6 runs (67%) over 2 scenarios × 3 runs" in report
    assert "1/2 scenarios were correct on every run" in report
    assert "| ALRT-001 | checkout-api | `payments-db` | 3/3 (100%) | 9 | 11.0s | — |" in report
    # The failed run is excluded from the medians but named in the notes, and the wrong
    # answer is surfaced rather than hidden behind the rate.
    assert "| 1/3 (33%) | 18 | 23.2s | 1 run produced no package; also answered `payments-db` |" in report


def test_a_scenario_that_never_succeeds_is_reported_as_such():
    report = "\n".join(evaluate.build_report([scenario("ALRT-005", [None, None])], runs=2))

    assert "0/2 (0%)" in report
    assert "2 runs produced no package" in report
    assert "**No run produced a diagnosis.**" in report
    assert "0/2 runs (0%)" in report


def test_the_review_section_prefers_a_correct_run():
    report = "\n".join(evaluate.build_report([
        scenario("ALRT-004", [("payments-db", 20, 25.0), ("order-service", 15, 21.4)]),
    ], runs=2))

    assert "*Shown: a correct run out of 2.*" in report
    assert "hypothesis for ALRT-004" in report


def test_the_review_section_says_so_when_no_run_was_correct():
    report = "\n".join(evaluate.build_report([
        scenario("ALRT-004", [("payments-db", 20, 25.0), ("payments-db", 18, 22.0)]),
    ], runs=2))

    assert "no run out of 2 found the expected root" in report


def test_kpi_summary_aggregates_only_the_runs_that_produced_a_package():
    report = "\n".join(evaluate.build_report([
        scenario("ALRT-002", [("user-service", 9, 12.0), None]),
    ], runs=2))

    assert "Aggregated over the 1 run(s) that produced a diagnosis." in report
    assert "| Acuratete componenta afectata | variabila | 1/2 (50%) | — |" in report


@pytest.mark.parametrize("argv, expected_runs", [([], 1), (["--runs", "3"], 3)])
def test_runs_argument_is_parsed(argv, expected_runs, monkeypatch, tmp_path):
    seen: list[int] = []
    output = tmp_path / "EVAL_RESULTS.md"
    monkeypatch.setattr(evaluate, "load_dotenv", lambda: None)
    monkeypatch.setattr(evaluate, "setup_phoenix", lambda: None)
    monkeypatch.setattr(evaluate, "OUTPUT", output)
    monkeypatch.setattr(evaluate, "run_scenario",
                        lambda alert_id, runs: seen.append(runs) or scenario(alert_id, [("payments-db", 9, 11.0)]))

    assert evaluate.main(["ALRT-001", *argv]) == 0
    assert seen == [expected_runs]
    assert "ALRT-001" in output.read_text(encoding="utf-8")


def test_an_unknown_alert_id_is_rejected_before_any_run():
    with pytest.raises(SystemExit):
        evaluate.main(["ALRT-999"])


def test_runs_must_be_positive():
    with pytest.raises(SystemExit):
        evaluate.main(["--runs", "0"])
