# Rootly — Evaluation Results

Generated 2026-09-15 14:48 UTC by `python evaluate.py`.

**Root component correctly identified:** 5/5

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |
|---|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 40.9s ❌ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `auto_resolved` | 4 steps ✅ · 9.3s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | ✅ | ✅ | ✅ | ✅ | 0.95 | `escalate_urgent_needs_approval` | 6 steps ✅ · 22.7s ✅ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_urgent_needs_approval` | 4 steps ✅ · 9.0s ✅ |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 7 steps ✅ · 38.7s ❌ |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** payments-db connection pool exhausted due to insufficient capacity for current load, causing checkout-api to timeout and eventually open its circuit breaker.

**Escalation:** Page payments-team to increase payments-db connection pool size and implement connection pool partitioning to separate batch and real-time workloads.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** A memory leak in user-service after deployment, causing increasing memory usage and eventual OOM kills.

**Escalation:** Page identity-team to investigate and potentially roll back to the previous version (v2.4.1) while a fix is developed.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** auth-service failed to validate user tokens after Redis cache crashed due to memory exhaustion, causing api-gateway to return 502 errors for all authenticated requests. The auth-service has no fallback mechanism for session validation when Redis is down.

**Escalation:** Page platform-team to increase Redis maxmemory to 8GB, implement a database-backed session fallback in auth-service, and add Redis memory alerting at 70% and 85% thresholds.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The root cause is a configuration change that reduced the query timeout to 500ms, which is too aggressive for complex payment verification queries.

**Escalation:** Page the orders-team to revert the query timeout configuration to a more appropriate value, such as 3000ms, and implement a configuration change review process to prevent similar issues in the future.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** Internal TLS certificate for auth-service.internal expired, breaking all inter-service HTTPS communication.

**Escalation:** Page platform-team to renew auth-service certificate and implement cert-manager for automatic renewal.
