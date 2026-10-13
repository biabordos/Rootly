"""
Ground truth for the alert scenarios in data/alerts.json, used by both evaluate.py
and e2e.py so there is a single source of truth instead of two copies that could
drift apart. Source: MOCK_DATA_README.md.
"""

from __future__ import annotations

# Ground truth per scenario: where the root cause originates, the components that
# must appear somewhere in the package, and the directly matching past incident.
#
# "escalation" is the escalation decision the deterministic policy must reach, and it is
# only set where the CMDB makes it structural. A root component with
# HIGH_BLAST_RADIUS_DEPENDENTS (2) or more dependents hits the blast-radius rule, which
# returns urgent whatever severity and confidence the model assessed -- so the expectation
# holds on every run. The four scenarios whose root has a single dependent
# (ALRT-002/003/007 user-service and redis-cache, ALRT-008 cdn-provider) are deliberately
# left without one: their decision follows the model's severity_assessed and confidence,
# which vary run to run, so any fixed value would be a flaky assertion rather than a check
# of the policy. Verified by brute-forcing every severity x confidence pair against
# decide_escalation().
GROUND_TRUTH = {
    "ALRT-001": {"root": "payments-db", "involved": {"payments-db", "checkout-api"}, "incident": "INC-2025-114",
                 "escalation": "escalate_urgent_needs_approval"},
    "ALRT-002": {"root": "user-service", "involved": {"user-service"}, "incident": "INC-2025-203"},
    "ALRT-003": {"root": "redis-cache", "involved": {"redis-cache", "auth-service"}, "incident": "INC-2025-156"},
    "ALRT-004": {"root": "order-service", "involved": {"order-service", "payments-db"}, "incident": "INC-2025-278",
                 "escalation": "escalate_urgent_needs_approval"},
    "ALRT-005": {"root": "auth-service", "involved": {"auth-service"}, "incident": "INC-2025-341",
                 "escalation": "escalate_urgent_needs_approval"},
    "ALRT-006": {"root": "payments-db", "involved": {"payments-db", "order-service"}, "incident": "INC-2025-089",
                 "escalation": "escalate_urgent_needs_approval"},
    "ALRT-007": {"root": "user-service", "involved": {"user-service"}, "incident": "INC-2025-067"},
    "ALRT-008": {"root": "cdn-provider", "involved": {"cdn-provider", "api-gateway"}, "incident": "INC-2024-301"},
    # Scenario 9 has two valid investigation paths (via checkout-api or via order-service).
    # api-gateway is the common ancestor of both, but it's just a pass-through hop on either
    # path -- the diagnosis package isn't expected to cite every hop walked, only the ones
    # that actually carried evidence, so it doesn't belong in involved (confirmed live: the
    # agent correctly investigates through api-gateway but doesn't always list it as a
    # critical_dependency, which isn't wrong).
    "ALRT-009": {"root": "payments-db", "involved": {"payments-db"}, "incident": "INC-2024-445",
                 "escalation": "escalate_urgent_needs_approval"},
    "ALRT-010": {"root": "auth-service", "involved": {"auth-service"}, "incident": "INC-2024-512",
                 "escalation": "escalate_urgent_needs_approval"},
}
