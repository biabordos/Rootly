"""
Rootly end-to-end orchestrator.

    python e2e.py                     # run every scenario, validate, summary + exit code
    python e2e.py ALRT-001            # a single alert
    python e2e.py --auto-approve      # auto-approve urgent escalations (demo mode)
    python e2e.py --json report.json  # also write a machine-readable report

Coordinates the existing graph (src/agent/graph.py) end to end: preflight checks,
run_diagnosis, the human-in-the-loop pause/resume, and validation against
src/eval_ground_truth.GROUND_TRUTH. It does not reimplement any agent logic --
all of that stays in src/agent/. Requires MISTRAL_API_KEY; each scenario makes
several API calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agent import DiagnosisError, DiagnosisResult, resume_diagnosis, run_diagnosis
from src.agent.observability import tracing_status
from src.data_loader import get_alert, load_alerts, load_cmdb, load_incidents, load_logs
from src.eval_ground_truth import GROUND_TRUTH


def preflight() -> list[str]:
    """Environment checks before touching the model. Returns the list of problems found."""
    problems: list[str] = []
    if not os.getenv("MISTRAL_API_KEY"):
        problems.append("MISTRAL_API_KEY is not set (copy .env.example to .env and fill it in).")
    try:
        assert load_alerts(), "alerts.json is empty"
        assert load_cmdb(), "cmdb.json is empty"
        assert load_logs(), "logs.json is empty"
        assert load_incidents(), "incidents.json is empty"
    except Exception as exc:  # noqa: BLE001 -- want a friendly message here, not a traceback
        problems.append(f"Mock data failed to load/validate: {exc}")
    return problems


def validate(result: DiagnosisResult) -> dict:
    """Compare the result against the scenario's ground truth (same rules as evaluate.py)."""
    truth = GROUND_TRUTH[result.alert.id]
    d = result.diagnosis
    mentioned = {d.affected_component, *d.critical_dependencies}
    checks = {
        "component_correct": d.affected_component == truth["root"],
        "dependencies_covered": truth["involved"] <= mentioned,
        "has_log_evidence": bool(d.log_evidence),
        "similar_incident_cited": truth["incident"] in d.similar_incidents,
        "steps_under_limit": result.steps < 15,
        "time_under_30s": result.elapsed_seconds < 30,
    }
    checks["passed"] = all(
        checks[k] for k in ("component_correct", "dependencies_covered", "has_log_evidence", "similar_incident_cited")
    )
    return checks


def run_one(alert_id: str, auto_approve: bool) -> tuple[DiagnosisResult | None, dict, str | None]:
    """Run one scenario through the graph, resolving human-in-the-loop pauses if asked to."""
    alert = get_alert(alert_id)
    try:
        result = run_diagnosis(alert)
    except DiagnosisError as exc:
        return None, {}, f"run failed: {exc}"

    if result.needs_approval:
        if not auto_approve:
            return result, {}, f"paused for approval (thread_id={result.thread_id})"
        try:
            result = resume_diagnosis(result.thread_id, "approve", note="auto-approved by e2e.py (demo mode)")
        except DiagnosisError as exc:
            return result, {}, f"resume failed: {exc}"

    return result, validate(result), None


def main(argv: list[str]) -> int:
    load_dotenv()
    print(tracing_status())

    parser = argparse.ArgumentParser(description="Rootly end-to-end orchestrator")
    parser.add_argument("alert_id", nargs="?", help="A single alert; default: every scenario")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve urgent escalations (demo mode)")
    parser.add_argument("--json", metavar="PATH", help="Also write a JSON report")
    args = parser.parse_args(argv)

    problems = preflight()
    if problems:
        for problem in problems:
            print(f"[preflight] {problem}", file=sys.stderr)
        return 2

    alert_ids = [args.alert_id] if args.alert_id else [a.id for a in load_alerts()]
    report, all_passed = [], True

    for alert_id in alert_ids:
        print(f"-> {alert_id} ...", flush=True)
        result, checks, err = run_one(alert_id, args.auto_approve)
        if err:
            print(f"   ERROR: {err}")
            all_passed = False
            report.append({"alert": alert_id, "error": err})
            continue
        ok = checks["passed"]
        all_passed &= ok
        print(
            f"   [{'PASS' if ok else 'FAIL'}] {result.diagnosis.affected_component} "
            f"| confidence={result.diagnosis.confidence:.2f} "
            f"| {result.steps} steps | {result.elapsed_seconds:.1f}s "
            f"| {result.diagnosis.escalation_decision.value}"
        )
        report.append({
            "alert": alert_id,
            "checks": checks,
            "component": result.diagnosis.affected_component,
            "elapsed_seconds": result.elapsed_seconds,
        })

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON report written to {args.json}")

    print(f"\nResult: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
