# Rootly — Evaluation Results

Generated 2026-09-15 14:12 UTC by `python evaluate.py`.

**Root component correctly identified:** 5/5

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |
|---|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 21.4s ✅ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `auto_resolved` | 4 steps ✅ · 21.5s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 6 steps ✅ · 16.3s ✅ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | ✅ | ✅ | ✅ | ❌ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 9.6s ✅ |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 9.5s ✅ |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** The payments-db connection pool is exhausted due to insufficient connections for the current load, causing all new payment requests to timeout and the circuit breaker to open.

**Escalation:** Page payments-team to increase the connection pool size in payments-db and implement a circuit breaker pattern in checkout-api to handle connection timeouts gracefully.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The deployment of v2.5.0 introduced a memory leak, causing the service to consume excessive memory and eventually be terminated by the kernel.

**Escalation:** Page the identity-team to investigate and optimize memory usage in the user-service, particularly focusing on the deployment of v2.5.0.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The Redis cache has crashed due to memory pressure, causing the auth-service to fail all authentication requests. This is a known issue that has occurred before, and the solution involves increasing the Redis memory limit and implementing a fallback mechanism for the auth-service.

**Escalation:** Page the platform-team to investigate the Redis cache crash and implement a fallback mechanism for the auth-service.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** A configuration change reduced the timeout for queries to the payments-db, causing complex queries to time out and leading to a cascade of errors.

**Escalation:** Page the orders-team to investigate and potentially revert the configuration change.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** The auth-service internal TLS certificate has expired, causing all inter-service HTTPS communication to fail.

**Escalation:** Page the security team to renew the auth-service internal TLS certificate and implement automated renewal. The frontend-team should monitor the service while the security team works on the fix.
