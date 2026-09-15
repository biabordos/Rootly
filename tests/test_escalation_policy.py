"""Exhaustive tests for the deterministic escalation policy (no API key, no LLM)."""

from __future__ import annotations

import pytest

from src.agent.escalation_policy import (
    CONFIDENCE_THRESHOLD,
    HUMAN_APPROVE,
    HUMAN_DOWNGRADE,
    apply_human_decision,
    decide_escalation,
)
from src.models.schemas import DiagnosisPackage, EscalationDecision, Severity

URGENT = EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL
CONFIDENCES = [0.0, 0.3, 0.5, 0.69, 0.7, 0.85, 0.99, 1.0]
LOW_CONFIDENCES = [c for c in CONFIDENCES if c < CONFIDENCE_THRESHOLD]
GOOD_CONFIDENCES = [c for c in CONFIDENCES if c >= CONFIDENCE_THRESHOLD]
DEPENDENTS = [0, 1, 2, 3, 10]


def package(severity: Severity, confidence: float, **extra) -> DiagnosisPackage:
    return DiagnosisPackage(
        alert_id="ALRT-TEST",
        summary="summary",
        affected_component="payments-db",
        severity_assessed=severity,
        root_cause_hypothesis="hypothesis",
        confidence=confidence,
        escalation_recommendation="recommendation",
        **extra,
    )


@pytest.mark.parametrize("dependents", DEPENDENTS)
@pytest.mark.parametrize("confidence", CONFIDENCES)
def test_critical_is_never_auto_resolved(confidence, dependents):
    decision, reason = decide_escalation(package(Severity.CRITICAL, confidence), dependents)
    assert decision == URGENT
    assert "critical" in reason


@pytest.mark.parametrize("dependents", [2, 3, 10])
@pytest.mark.parametrize("severity", [Severity.LOW, Severity.MEDIUM, Severity.HIGH])
def test_blast_radius_forces_approval_even_with_high_confidence(severity, dependents):
    decision, reason = decide_escalation(package(severity, 0.95), dependents)
    assert decision == URGENT
    assert str(dependents) in reason


@pytest.mark.parametrize("dependents", [0, 1])
@pytest.mark.parametrize("confidence", LOW_CONFIDENCES)
@pytest.mark.parametrize("severity", [Severity.LOW, Severity.MEDIUM, Severity.HIGH])
def test_low_confidence_alone_forces_approval(severity, confidence, dependents):
    decision, reason = decide_escalation(package(severity, confidence), dependents)
    assert decision == URGENT
    assert "Confidence" in reason


@pytest.mark.parametrize("dependents", [0, 1])
@pytest.mark.parametrize("confidence", GOOD_CONFIDENCES)
@pytest.mark.parametrize("severity", [Severity.LOW, Severity.MEDIUM])
def test_confident_low_or_medium_is_auto_resolved(severity, confidence, dependents):
    decision, _ = decide_escalation(package(severity, confidence), dependents)
    assert decision == EscalationDecision.AUTO_RESOLVED


@pytest.mark.parametrize("dependents", [0, 1])
@pytest.mark.parametrize("confidence", GOOD_CONFIDENCES)
def test_confident_high_is_normal_escalation(confidence, dependents):
    decision, _ = decide_escalation(package(Severity.HIGH, confidence), dependents)
    assert decision == EscalationDecision.ESCALATE_NORMAL


def test_confidence_threshold_boundary():
    assert decide_escalation(package(Severity.HIGH, 0.69), 0)[0] == URGENT
    assert decide_escalation(package(Severity.HIGH, 0.70), 0)[0] == EscalationDecision.ESCALATE_NORMAL


@pytest.mark.parametrize("dependents", DEPENDENTS)
@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("severity", list(Severity))
def test_every_decision_comes_with_a_reason(severity, confidence, dependents):
    _, reason = decide_escalation(package(severity, confidence), dependents)
    assert reason.strip()


# ── Human decision ───────────────────────────────────────────────────────

def urgent_package() -> DiagnosisPackage:
    return package(Severity.CRITICAL, 0.9, escalation_decision=URGENT, escalation_reason="policy reason")


def test_approve_keeps_escalation_urgent():
    approved = apply_human_decision(urgent_package(), HUMAN_APPROVE, "  paging now ")
    assert approved.escalation_decision == URGENT
    assert approved.human_decision == HUMAN_APPROVE
    assert approved.human_decision_note == "paging now"


def test_downgrade_changes_only_the_urgency():
    original = urgent_package()
    downgraded = apply_human_decision(original, HUMAN_DOWNGRADE, "known maintenance window")
    assert downgraded.escalation_decision == EscalationDecision.ESCALATE_NORMAL
    assert downgraded.human_decision == HUMAN_DOWNGRADE
    assert downgraded.escalation_reason == "policy reason"
    assert downgraded.root_cause_hypothesis == original.root_cause_hypothesis
    assert downgraded.affected_component == original.affected_component


def test_empty_note_is_stored_as_none():
    assert apply_human_decision(urgent_package(), HUMAN_APPROVE, "   ").human_decision_note is None


def test_unknown_human_decision_is_rejected():
    with pytest.raises(ValueError):
        apply_human_decision(urgent_package(), "reject")


def test_non_urgent_package_cannot_be_approved():
    normal = package(Severity.HIGH, 0.9, escalation_decision=EscalationDecision.ESCALATE_NORMAL)
    with pytest.raises(ValueError):
        apply_human_decision(normal, HUMAN_APPROVE)
