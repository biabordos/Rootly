# Rootly — Evaluation Results

Generated 2026-09-13 16:59 UTC by `python evaluate.py`.

**Root component correctly identified:** 5/5

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Steps · Time |
|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | 4 steps ✅ · 39.3s ❌ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ❌ | 0.90 | 6 steps ✅ · 10.1s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | ✅ | ✅ | ✅ | ✅ | 0.95 | 6 steps ✅ · 15.9s ✅ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | 4 steps ✅ · 9.8s ✅ |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | 6 steps ✅ · 8.8s ✅ |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** The payments-db connection pool is exhausted due to insufficient connections to handle the current load, causing connection timeouts and failures in checkout-api.

**Escalation:** Page payments-team to increase the connection pool size in payments-db and implement a circuit breaker pattern in checkout-api to handle connection timeouts gracefully.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** A memory leak in the new version of user-service caused excessive memory usage and GC pauses, leading to the pod being OOMKilled and high latency.

**Escalation:** Page the identity-team to investigate and fix the memory leak in the user-service deployment.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** redis-cache crashed due to OOM after memory usage exceeded its 4GB limit, causing auth-service to fail user token validation and api-gateway to fail all authenticated requests.

**Escalation:** Page platform-team to investigate redis-cache OOM and implement memory alerting at 70% and 85% thresholds.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The order-service is experiencing timeouts when querying payments-db due to complex queries that are taking longer than the configured query timeout of 50ms. This is causing the circuit breaker to open, leading to a high error rate.

**Escalation:** Page the orders-team to investigate and potentially adjust the query timeout configuration in the order-service to accommodate the complex queries.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** The auth-service internal TLS certificate expired, causing all inter-service HTTPS communication to fail, which in turn made the web-frontend service completely unavailable.

**Escalation:** Page the platform-team to renew the TLS certificate and implement automated renewal for the auth-service.
