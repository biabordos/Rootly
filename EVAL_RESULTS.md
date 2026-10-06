# Rootly — Statistical Evaluation (multi-agent, N=3)

Generated 2026-10-06 10:21 UTC by `python evaluate.py --runs 3`.

Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).

## Success rates

| Alert | Service | Success rate | Avg steps | Avg time | Avg confidence | Failure components |
|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | 3/3 (100%) | 12.0 | 40.8s | 0.88 | — |
| ALRT-002 | user-service | 3/3 (100%) | 12.3 | 27.4s | 0.80 | — |
| ALRT-003 | api-gateway | 2/3 (67%) | 16.7 | 36.5s | 0.90 | `auth-service` (1x) |
| ALRT-004 | order-service | 3/3 (100%) | 20.0 | 33.0s | 0.80 | — |
| ALRT-005 | web-frontend | 3/3 (100%) | 12.0 | 22.5s | 0.90 | — |
| ALRT-006 | order-service | 3/3 (100%) | 9.0 | 18.2s | 0.87 | — |
| ALRT-007 | user-service | 3/3 (100%) | 16.0 | 29.7s | 0.77 | — |
| ALRT-008 | api-gateway | 3/3 (100%) | 10.0 | 20.2s | 0.85 | — |
| ALRT-009 | web-frontend | 3/3 (100%) | 18.3 | 30.1s | 0.90 | — |
| ALRT-010 | user-service | 1/3 (33%) | 9.0 | 16.2s | 0.80 | — |

**Root component correctly identified:** 27/30 runs (90%) over 10 scenarios × 3 runs. 8/10 scenarios were correct on every run.

Routing is decided by an LLM, so the same scenario can pass on one run and exhaust its step budget on the next. A success rate over several runs is the honest measure; a single pass is one sample. Targets: steps < 15, time < 30 s.

## Failure analysis

### ALRT-003 — 1 failure(s) in 3 runs

- 1x stopped at `auth-service`

### ALRT-010 — 2 failure(s) in 3 runs

- 2 runs produced no package

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The payments-db connection pool was exhausted due to insufficient max_connections setting, causing all new payment requests from checkout-api to timeout. This was exacerbated by the sustained high-throughput checkout flow during the peak period.

**Escalation:** Page payments-team to increase the payments-db connection pool size and implement a connection pooler middleware to handle the increased load.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The new deployment introduced a memory leak, causing the user-service to consume excessive memory and eventually be OOMKilled.

**Escalation:** Page the identity-team to roll back the deployment and investigate the memory leak.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The redis-cache ran out of memory and was terminated by the OOM killer, causing the auth-service to fail and the api-gateway to experience a critical outage.

**Escalation:** Page the platform-team to increase the Redis maxmemory to 8GB and implement database-backed session fallback in the auth-service.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The configuration change reduced the query timeout from 5000ms to 50ms, which was too aggressive for complex payment verification queries that legitimately take longer under load.

**Escalation:** Page the orders-team to revert the timeout configuration to a more appropriate value and implement a review process for future timeout changes.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The auth-service TLS certificate expired at 16:00:00Z, causing all inter-service HTTPS communication to fail and making the web-frontend completely unavailable.

**Escalation:** Page the security-team to renew the certificate and implement automated renewal.

### ALRT-006 — order-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** Monthly reconciliation batch job BATCH-2026-08 is consuming 90% of payments-db connections, starving real-time payment verification queries from order-service

**Escalation:** Page payments-team to investigate connection pool partitioning or dedicated read replica for batch processing

### ALRT-007 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The new search endpoint in the user-service is causing full table scans on the user-db, leading to high CPU usage and degraded performance. This is due to the lack of proper indexing on the database columns used in the search queries.

**Escalation:** Page the identity-team to investigate and optimize the search endpoint and database queries.

### ALRT-008 — api-gateway

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** cdn-provider experienced routing issues in EU-West region, causing 40% of static asset requests to timeout. api-gateway health checks were passing (they hit /health, not CDN-dependent paths) masking the issue.

**Escalation:** Page cdn-provider support team to investigate and resolve the routing anomaly in the EU-West region. Implement multi-CDN failover and add synthetic monitoring that tests CDN-dependent paths in api-gateway.

### ALRT-009 — web-frontend

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** The payments-db failover was delayed due to high replication lag, causing checkout-api to fail and web-frontend to show elevated 5xx errors.

**Escalation:** Page the platform-team to implement streaming replication monitoring and add a pre-maintenance replication lag check.

### ALRT-010 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 3.*

**Hypothesis:** Security team updated IAM policies to enforce least-privilege OAuth scopes. The change inadvertently removed the 'user:read' scope from service-to-service tokens used by user-service to call auth-service, causing 403 Forbidden errors.

**Escalation:** Page identity-team to add missing 'user:read' scope back to service account svc-user-service and implement policy-as-code review process requiring automated scope validation before IAM changes are applied.

## KPI Summary — Manual vs. Rootly

Aggregated over the 28 run(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 21 min | 28.2s | 98% mai rapid |
| Cazuri auto-rezolvate | 0% | 3/28 (11%) | +11pp |
| Cazuri care ajung la un om | 100% | 19/28 (68%) | 32pp mai putin |
| Acuratete componenta afectata | variabila | 27/30 (90%) | — |
