# Rootly — Evaluation Results (SINGLE-AGENT BASELINE)

> Reference run of the **single-agent** architecture (one `agent` node doing routing,
> interpretation and synthesis), kept for comparison against the multi-agent results in
> `EVAL_RESULTS.md`. Produced on commit `813b374`, before `feat/multi-agent`.

Generated 2026-10-04 09:53 UTC by `python evaluate.py`.

**Root component correctly identified:** 10/10

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |
|---|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 5 steps ✅ · 23.9s ✅ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `auto_resolved` | 4 steps ✅ · 9.6s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | ✅ | ✅ | ✅ | ✅ | 0.95 | `escalate_urgent_needs_approval` | 6 steps ✅ · 13.1s ✅ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 4 steps ✅ · 9.3s ✅ |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 7 steps ✅ · 26.6s ✅ |
| ALRT-006 | order-service | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 5 steps ✅ · 8.4s ✅ |
| ALRT-007 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `auto_resolved` | 4 steps ✅ · 7.6s ✅ |
| ALRT-008 | api-gateway | `cdn-provider` (expected `cdn-provider`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_normal` | 4 steps ✅ · 5.3s ✅ |
| ALRT-009 | web-frontend | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ❌ | 0.95 | `escalate_urgent_needs_approval` | 8 steps ✅ · 18.7s ✅ |
| ALRT-010 | user-service | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_urgent_needs_approval` | 5 steps ✅ · 22.9s ✅ |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## KPI Summary — Manual vs. Rootly

Aggregated over the 10 scenario(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 20 min | 15s | 99% mai rapid |
| Cazuri auto-rezolvate | 0% | 2/10 (20%) | +20pp |
| Cazuri care ajung la un om | 100% | 7/10 (70%) | 30pp mai putin |
| Acuratete componenta afectata | variabila | 10/10 (100%) | — |

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** payments-db connection pool exhausted due to insufficient max_connections setting (20) for current traffic load, causing checkout-api to open its circuit breaker and fail all payment requests.

**Escalation:** Page payments-team to increase payments-db max_connections setting and implement connection pool partitioning to separate batch and real-time workloads.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The deployment of v2.5.0 introduced memory issues and GC pauses, leading to OOMKilled restarts and high latency.

**Escalation:** Page the identity-team to investigate and optimize memory usage and GC behavior in the user-service.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The redis-cache crashed due to OOM, causing auth-service to fail user token validation. The api-gateway circuit breaker then opened, causing all authenticated requests to fail.

**Escalation:** Page the platform-team to investigate the redis-cache OOM issue and implement a fallback mechanism for auth-service.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The configuration change reduced the payments-db query timeout from 5000ms to 50ms, which was too aggressive for complex payment verification queries that legitimately take 200-800ms under load.

**Escalation:** Page orders-team to revert the timeout to 3000ms and implement a more robust configuration change review process.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** Internal TLS certificate for auth-service.internal expired after 1-year validity period. Certificate renewal was manual and no automated alerting existed for internal cert expiry. All services communicating with auth-service over mTLS immediately failed.

**Escalation:** Page platform-team to renew certificate manually (15 min to generate + deploy), implement cert-manager for automatic renewal 30 days before expiry, and add monitoring for all internal certificate expiry dates.

### ALRT-006 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The payments-db connection pool is exhausted by a long-running batch job, preventing real-time payment verification queries from order-service from executing.

**Escalation:** Page the payments-team to investigate the long-running batch job BATCH-2026-08 and consider separating batch processing to a read replica or increasing the connection pool size.

### ALRT-007 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The new search endpoint in v2.6.0 performs full table scans instead of using indexed queries, causing CPU exhaustion under normal traffic.

**Escalation:** Page identity-team to optimize the search query and add appropriate indexes to user-db.

### ALRT-008 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** CDN provider experienced routing issues in EU-West region, causing 40% of static asset requests to timeout. api-gateway health checks were passing (they hit /health, not CDN-dependent paths) masking the issue.

**Escalation:** Page the platform-team to implement multi-CDN failover and add synthetic monitoring that tests CDN-dependent paths.

### ALRT-009 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** payments-db max_connections set to 20, insufficient for peak load. Connection pool depleted under sustained high-throughput checkout flow, causing all new payment requests to timeout.

**Escalation:** Page payments-team to increase payments-db connection pool from 20 to 50, add PgBouncer connection pooler as middleware, implement circuit breaker pattern on checkout-api side.

### ALRT-010 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** Security team updated IAM policies to enforce least-privilege OAuth scopes. The change inadvertently removed the 'user:read' scope from service-to-service tokens used by user-service to call auth-service, causing sporadic 403 Forbidden errors.

**Escalation:** Page identity-team to add missing 'user:read' scope back to service account svc-user-service and implement policy-as-code review process requiring automated scope validation before IAM changes are applied.
