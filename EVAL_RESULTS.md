# Rootly — Evaluation Results

Generated 2026-10-03 08:46 UTC by `python evaluate.py`.

**Root component correctly identified:** 9/10

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |
|---|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 29.5s ✅ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `auto_resolved` | 4 steps ✅ · 7.7s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 6 steps ✅ · 13.7s ✅ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_urgent_needs_approval` | 4 steps ✅ · 6.9s ✅ |
| ALRT-005 | web-frontend | `web-frontend` (expected `auth-service`) | ❌ | ❌ | ✅ | ✅ | 1.00 | `escalate_urgent_needs_approval` | 7 steps ✅ · 10.9s ✅ |
| ALRT-006 | order-service | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 6.1s ✅ |
| ALRT-007 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `auto_resolved` | 5 steps ✅ · 13.6s ✅ |
| ALRT-008 | api-gateway | `cdn-provider` (expected `cdn-provider`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_normal` | 4 steps ✅ · 6.2s ✅ |
| ALRT-009 | web-frontend | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ❌ | 0.95 | `escalate_urgent_needs_approval` | 8 steps ✅ · 13.2s ✅ |
| ALRT-010 | user-service | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 7.3s ✅ |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## KPI Summary — Manual vs. Rootly

Aggregated over the 10 scenario(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 20 min | 12s | 99% mai rapid |
| Cazuri auto-rezolvate | 0% | 2/10 (20%) | +20pp |
| Cazuri care ajung la un om | 100% | 7/10 (70%) | 30pp mai putin |
| Acuratete componenta afectata | variabila | 9/10 (90%) | — |

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** payments-db connection pool exhausted due to insufficient connections for current load, causing checkout-api to open its circuit breaker and fail all payment requests.

**Escalation:** Page payments-team to increase payments-db connection pool size and implement connection pool partitioning to separate batch and real-time workloads.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** Memory pressure and GC pauses in user-service after deployment, leading to OOM kills and high latency.

**Escalation:** Page identity-team to investigate memory management and GC tuning in user-service.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The redis-cache is out of memory, causing the auth-service to be unable to validate user tokens, which has cascaded to all authenticated endpoints, resulting in a complete outage for users attempting to authenticate.

**Escalation:** Page the platform-team to investigate the redis-cache outage and increase its memory allocation.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** A configuration change reduced the query timeout to 500ms, which is too aggressive for complex payment verification queries that legitimately take 200-800ms under load.

**Escalation:** Page the orders-team to revert the query timeout to 3000ms and implement a more robust configuration change review process.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** The web-frontend service is completely down, with no successful requests being processed.

**Escalation:** Page the web team immediately to investigate and restore the service.

### ALRT-006 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** Long-running batch job BATCH-2026-08 is consuming most of the payments-db connection pool, starving real-time queries and causing timeouts in order-service and checkout-api.

**Escalation:** Page payments-team to investigate and implement connection pool partitioning to reserve connections for real-time queries, similar to INC-2025-089.

### ALRT-007 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The new search endpoint in v2.6.0 performs full table scans instead of using indexed queries, causing CPU exhaustion and thread pool exhaustion under normal traffic.

**Escalation:** Page identity-team to optimize the search query and add appropriate indexes to user-db.

### ALRT-008 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The CDN provider experienced a routing anomaly in the EU-West region, causing elevated origin fetch latency and intermittent 504 errors for static asset requests. The api-gateway health check endpoint is not affected, masking the issue.

**Escalation:** Page the platform-team to investigate the CDN provider routing anomaly and implement multi-CDN failover and synthetic monitoring that tests CDN-dependent paths.

### ALRT-009 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** payments-db max_connections set to 20, insufficient for peak load. Connection pool depleted under sustained high-throughput checkout flow, causing all new payment requests to timeout.

**Escalation:** Page payments-team to increase payments-db connection pool from 20 to 50, add PgBouncer connection pooler as middleware, implement circuit breaker pattern on checkout-api side.

### ALRT-010 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The security team updated IAM policies to enforce least-privilege OAuth scopes. The change inadvertently removed the 'user:read' scope from service-to-service tokens used by user-service to call auth-service, causing sporadic 403 Forbidden errors.

**Escalation:** Page the security-team to add the missing 'user:read' scope back to the service account svc-user-service and implement a policy-as-code review process requiring automated scope validation before IAM changes are applied.
