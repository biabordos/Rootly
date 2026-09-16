"""
Ground truth for the alert scenarios in data/alerts.json, used by both evaluate.py
and e2e.py so there is a single source of truth instead of two copies that could
drift apart. Source: MOCK_DATA_README.md.
"""

from __future__ import annotations

# Ground truth per scenario: where the root cause originates, the components that
# must appear somewhere in the package, and the directly matching past incident.
GROUND_TRUTH = {
    "ALRT-001": {"root": "payments-db", "involved": {"payments-db", "checkout-api"}, "incident": "INC-2025-114"},
    "ALRT-002": {"root": "user-service", "involved": {"user-service"}, "incident": "INC-2025-203"},
    "ALRT-003": {"root": "redis-cache", "involved": {"redis-cache", "auth-service"}, "incident": "INC-2025-156"},
    "ALRT-004": {"root": "order-service", "involved": {"order-service", "payments-db"}, "incident": "INC-2025-278"},
    "ALRT-005": {"root": "auth-service", "involved": {"auth-service"}, "incident": "INC-2025-341"},
    "ALRT-006": {"root": "payments-db", "involved": {"payments-db", "order-service"}, "incident": "INC-2025-089"},
    "ALRT-007": {"root": "user-service", "involved": {"user-service"}, "incident": "INC-2025-067"},
    "ALRT-008": {"root": "cdn-provider", "involved": {"cdn-provider", "api-gateway"}, "incident": "INC-2024-301"},
    # Scenario 9 has two valid investigation paths (via checkout-api or via order-service);
    # only assert what both paths share instead of picking one.
    "ALRT-009": {"root": "payments-db", "involved": {"payments-db", "api-gateway"}, "incident": "INC-2024-445"},
    "ALRT-010": {"root": "auth-service", "involved": {"auth-service"}, "incident": "INC-2024-512"},
}
