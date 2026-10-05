# Rootly — Evaluation Results (multi-agent)

Generated 2026-10-05 08:24 UTC by `python evaluate.py --runs 3`.

Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).

**Root component correctly identified:** 29/30 runs (97%) over 10 scenarios × 3 runs. 9/10 scenarios were correct on every run.

| Alert | Service | Expected root | Success rate | Steps (median) | Time (median) | Notes |
|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` | 3/3 (100%) | 12 | 17.7s | — |
| ALRT-002 | user-service | `user-service` | 3/3 (100%) | 16 | 23.4s | — |
| ALRT-003 | api-gateway | `redis-cache` | 3/3 (100%) | 15 | 18.6s | — |
| ALRT-004 | order-service | `order-service` | 3/3 (100%) | 13 | 17.5s | — |
| ALRT-005 | web-frontend | `auth-service` | 3/3 (100%) | 19 | 22.6s | — |
| ALRT-006 | order-service | `payments-db` | 3/3 (100%) | 9 | 15.2s | — |
| ALRT-007 | user-service | `user-service` | 3/3 (100%) | 16 | 28.1s | — |
| ALRT-008 | api-gateway | `cdn-provider` | 3/3 (100%) | 9 | 16.1s | — |
| ALRT-009 | web-frontend | `payments-db` | 2/3 (67%) | 18 | 21.6s | also answered `api-gateway` |
| ALRT-010 | user-service | `auth-service` | 3/3 (100%) | 9 | 16.0s | — |

Routing is decided by an LLM, so the same scenario can pass on one run and exhaust its step budget on the next. A success rate over several runs is the honest measure; a single pass is one sample. Targets: steps < 15, time < 30 s.

## KPI Summary — Manual vs. Rootly

Aggregated over the 30 run(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 20 min | 21s | 98% mai rapid |
| Cazuri auto-rezolvate | 0% | 3/30 (10%) | +10pp |
| Cazuri care ajung la un om | 100% | 20/30 (67%) | 33pp mai putin |
| Acuratete componenta afectata | variabila | 29/30 (97%) | — |

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** payments-db max_connections set to 20 is insufficient for current load. Connection pool depleted under sustained high-throughput checkout flow, causing all new payment requests to timeout and checkout-api to open its circuit breaker.

**Escalation:** Page payments-team to increase payments-db connection pool size and implement connection pool partitioning to separate batch and real-time workloads.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The recent deployment of v2.5.0 in the user-service caused memory usage to spike, leading to severe GC pauses and ultimately an OOMKilled event. This suggests that the new version might have introduced a memory leak or inefficient memory usage patterns.

**Escalation:** Page the identity-team to investigate the recent deployment of v2.5.0 and assess the memory usage patterns and potential memory leaks.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The redis-cache service crashed due to an out of memory error, which caused the auth-service to fail and subsequently affected the api-gateway.

**Escalation:** Page the platform-team to investigate and resolve the redis-cache out of memory issue.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The configuration change reduced the payments-db query timeout from 5000ms to 50ms, which was too aggressive for complex join queries that legitimately take longer to execute. This caused the order-service to experience a high error rate and affected its dependent services.

**Escalation:** Page the orders-team to revert the configuration change and adjust the timeout value to an appropriate level. Monitor the order-service and its dependent services for any further issues.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The auth-service TLS certificate expired at 16:00:00Z, causing all downstream services to fail. The web-frontend became completely unavailable at 16:08:00Z.

**Escalation:** Page the security-team to renew the certificate and implement automated renewal. The frontend-team should monitor the service for recovery.

### ALRT-006 — order-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The batch job BATCH-2026-08 is consuming most of the payments-db connection pool, leaving insufficient connections for real-time order-service queries, which are timing out after 5000ms.

**Escalation:** Page the database-team to investigate the batch job's connection pool usage and consider separating batch processing to a read replica or adjusting the connection pool settings.

### ALRT-007 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The new search endpoint in v2.6.0 performed full table scans instead of using indexed queries, causing CPU exhaustion and degraded performance. This was exacerbated by the thread pool being exhausted, leading to blocked worker threads and increased response times.

**Escalation:** Page the identity-team to investigate and optimize the new search endpoint in the user-service, focusing on query optimization and potential indexing improvements.

### ALRT-008 — api-gateway

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** cdn-provider experienced routing issues in EU-West region, causing 40% of static asset requests to timeout. api-gateway health checks were passing (they hit /health, not CDN-dependent paths) masking the issue.

**Escalation:** Page platform-team to investigate and resolve the routing anomaly in cdn-provider's EU-West region.

### ALRT-009 — web-frontend

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The payments-db failover took longer than expected due to replication lag, causing checkout-api and api-gateway to see elevated 5xx errors during the maintenance window.

**Escalation:** Page the database-team to implement streaming replication monitoring and add a pre-maintenance replication lag check to ensure it's below 100ms before proceeding with the failover.

### ALRT-010 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** Security team updated IAM policies to enforce least-privilege OAuth scopes. The change inadvertently removed the 'user:read' scope from service-to-service tokens used by user-service to call auth-service, causing sporadic 403 Forbidden errors.

**Escalation:** Page identity-team to add missing 'user:read' scope back to service account svc-user-service and implement policy-as-code review process requiring automated scope validation before IAM changes are applied
