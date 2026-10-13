"""
Deterministic escalation policy, applied once the guardrail has accepted a diagnosis.

Three independent signals can each force human approval on their own:
  - risk: critical severity, or a wide blast radius (many CMDB dependents);
  - business criticality: a high-severity fault on a component the CMDB marks critical,
    even when nothing downstream depends on it;
  - uncertainty: confidence below CONFIDENCE_THRESHOLD, whatever the severity.
Only a confident low/medium-severity diagnosis is auto-resolved. The LLM never picks
the escalation path; it is computed here from the validated package, at no API cost.
"""

from __future__ import annotations

from src.models.schemas import Criticality, DiagnosisPackage, EscalationDecision, Severity

CONFIDENCE_THRESHOLD = 0.70
HIGH_BLAST_RADIUS_DEPENDENTS = 2

HUMAN_APPROVE = "approve"
HUMAN_DOWNGRADE = "downgrade"
HUMAN_DECISIONS = (HUMAN_APPROVE, HUMAN_DOWNGRADE)


def decide_escalation(
    diagnosis: DiagnosisPackage,
    component_depended_by_count: int,
    component_criticality: Criticality | None = None,
) -> tuple[EscalationDecision, str]:
    """
    component_criticality is the CMDB criticality tier of the affected component. It is
    optional so the policy stays callable from a package alone; when it is not supplied
    the business-criticality rule is simply skipped.
    """
    component = diagnosis.affected_component
    severity = diagnosis.severity_assessed
    confidence = diagnosis.confidence

    # 1. Hard override: critical is always urgent, whatever the confidence.
    if severity == Severity.CRITICAL:
        return (
            EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL,
            f"Severity is critical on {component}: critical incidents always need human approval "
            f"before an urgent escalation.",
        )

    # 2. Wide blast radius is urgent, even when the diagnosis is confident.
    if component_depended_by_count >= HIGH_BLAST_RADIUS_DEPENDENTS:
        return (
            EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL,
            f"{component} has {component_depended_by_count} dependent components in the CMDB "
            f"(threshold {HIGH_BLAST_RADIUS_DEPENDENTS}): the blast radius needs human approval.",
        )

    # 3. A business-critical component carries risk its dependent count does not show: a
    #    customer-facing entry point can have no dependents and still warrant a page.
    #    Checked after the blast radius so a wide-radius component keeps that reason.
    if severity == Severity.HIGH and component_criticality == Criticality.CRITICAL:
        return (
            EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL,
            f"{component} is a business-critical component in the CMDB and the assessed severity "
            f"is high: a human has to approve before this is treated as routine.",
        )

    # 4. Low confidence is an independent signal, regardless of severity.
    if confidence < CONFIDENCE_THRESHOLD:
        return (
            EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL,
            f"Confidence {confidence:.2f} is below {CONFIDENCE_THRESHOLD:.2f}: an uncertain diagnosis "
            f"needs a human check.",
        )

    # 5. Confident and low/medium severity.
    if severity in (Severity.LOW, Severity.MEDIUM):
        return (
            EscalationDecision.AUTO_RESOLVED,
            f"Confident diagnosis ({confidence:.2f}) of a {severity.value}-severity issue with "
            f"{component_depended_by_count} dependent component(s): no urgent L2 page needed.",
        )

    # 6. Everything else.
    return (
        EscalationDecision.ESCALATE_NORMAL,
        f"{severity.value.capitalize()} severity with confidence {confidence:.2f} and "
        f"{component_depended_by_count} dependent component(s): standard escalation to L2.",
    )


def apply_human_decision(
    diagnosis: DiagnosisPackage, decision: str, note: str | None = None
) -> DiagnosisPackage:
    """
    Record the human's answer to an urgent escalation. 'approve' keeps it urgent;
    'downgrade' contests the urgency (not the diagnosis) and makes it escalate_normal.
    The policy's escalation_reason is kept as-is for the audit trail.
    """
    if decision not in HUMAN_DECISIONS:
        raise ValueError(f"Unknown decision '{decision}'. Use one of: {', '.join(HUMAN_DECISIONS)}.")
    if diagnosis.escalation_decision != EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL:
        raise ValueError("Only urgent escalations wait for human approval.")

    update: dict = {"human_decision": decision, "human_decision_note": (note or "").strip() or None}
    if decision == HUMAN_DOWNGRADE:
        update["escalation_decision"] = EscalationDecision.ESCALATE_NORMAL
    return diagnosis.model_copy(update=update)
