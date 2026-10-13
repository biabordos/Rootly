# Rootly — Statistical Evaluation (multi-agent, N=10)

Generated 2026-10-13 22:34 UTC by `python evaluate.py --runs 10`.

Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).

## Success rates

| Alert | Service | Success rate | Avg steps | Avg time | Avg confidence | Failure components |
|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | 10/10 (100%) | 12.0 | 17.2s | 0.90 | — |
| ALRT-002 | user-service | 9/10 (90%) | 14.3 | 20.3s | 0.82 | — |
| ALRT-003 | api-gateway | 8/10 (80%) | 19.6 | 28.6s | 0.93 | — |
| ALRT-004 | order-service | 7/10 (70%) | 19.3 | 28.1s | 0.84 | — |
| ALRT-005 | web-frontend | 9/10 (90%) | 23.3 | 37.1s | 0.93 | — |
| ALRT-006 | order-service | 10/10 (100%) | 13.8 | 23.0s | 0.84 | — |
| ALRT-007 | user-service | 10/10 (100%) | 12.8 | 20.7s | 0.79 | — |
| ALRT-008 | api-gateway | 10/10 (100%) | 12.4 | 18.9s | 0.86 | — |
| ALRT-009 | web-frontend | 9/10 (90%) | 21.2 | 28.6s | 0.87 | `checkout-api` (1x) |
| ALRT-010 | user-service | 9/10 (90%) | 13.1 | 16.5s | 0.80 | — |

**Root component correctly identified:** 91/100 runs (91%) over 10 scenarios × 10 runs. 4/10 scenarios were correct on every run.

Routing is decided by an LLM, so the same scenario can pass on one run and exhaust its step budget on the next. A success rate over several runs is the honest measure; a single pass is one sample. Targets: steps < 15, time < 30 s.

## Failure analysis

### ALRT-002 — 1 failure(s) in 10 runs

- 1 run produced no package

### ALRT-003 — 2 failure(s) in 10 runs

- 2 runs produced no package

### ALRT-004 — 3 failure(s) in 10 runs

- 3 runs produced no package

### ALRT-005 — 1 failure(s) in 10 runs

- 1 run produced no package

### ALRT-009 — 1 failure(s) in 10 runs

- 1x stopped at `checkout-api`

### ALRT-010 — 1 failure(s) in 10 runs

- 1 run produced no package

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The payments-db connection pool was exhausted due to insufficient connections (20) for the current load. This caused checkout-api to timeout when trying to connect to payments-db, leading to a high error rate.

**Escalation:** Page payments-team to increase the connection pool size in payments-db and implement a circuit breaker pattern in checkout-api.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The recent deployment of v2.5.0 in the user-service caused memory leaks and increased GC pauses, leading to high memory usage and eventual OOMKilled restarts.

**Escalation:** Page the identity-team to investigate the recent deployment of v2.5.0 and address the memory leaks and GC issues causing the high latency and OOMKilled restarts in the user-service.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The redis-cache crashed due to OOM, causing the auth-service to become unavailable. The api-gateway then started returning 502 errors for all authenticated requests, leading to a high error rate.

**Escalation:** Page the platform-team to increase the Redis maxmemory limit and implement a database-backed session fallback in the auth-service.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** A config change reduced the payments-db query timeout from 5000ms to 50ms, which was too aggressive for complex payment verification queries that legitimately take longer than 50ms under load.

**Escalation:** Page the orders-team to revert the timeout change to 5000ms and implement a circuit breaker with a 50% error threshold to fast-fail instead of queueing retries.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The auth-service TLS certificate expired at 16:00:00Z, causing it to reject all inbound HTTPS connections. This broke the auth-service health checks, opened the circuit breaker in the api-gateway, and made the web-frontend completely unavailable.

**Escalation:** Page the security-team to renew the auth-service TLS certificate and implement automated renewal. The frontend-team should monitor the web-frontend recovery and the api-gateway error rate.

### ALRT-006 — order-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The batch job BATCH-2026-08 in payments-db is consuming a significant portion of the connection pool, leaving insufficient connections for real-time payment verification queries from the order-service. This is causing the queries to time out and the order-service to experience a backup of real-time orders.

**Escalation:** Page the orders-team to investigate and potentially separate the batch processing to a read replica or adjust the connection pool settings to reserve connections for real-time queries.

### ALRT-007 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The new search feature in v2.6.0 uses a full table scan instead of an indexed query for the /api/users/search endpoint. This causes CPU to spike to 95% under normal traffic as the query is called on every keystroke, leading to thread pool exhaustion and degraded response times.

**Escalation:** Page identity-team to investigate and optimize the search query in v2.6.0, focusing on adding a composite index on (last_name, first_name, email) and implementing a query timeout.

### ALRT-008 — api-gateway

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** cdn-provider experienced routing issues in EU-West region, causing 40% of static asset requests to timeout. api-gateway health checks were passing (they hit /health, not CDN-dependent paths) masking the issue.

**Escalation:** Page platform-team to implement multi-CDN failover and add synthetic monitoring that tests CDN-dependent paths.

### ALRT-009 — web-frontend

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** The payments-db failover took longer than expected due to replication lag, causing checkout-api to fail and api-gateway to return 502 errors.

**Escalation:** Page the platform-team to investigate the failover process and ensure it meets the expected timeframe.

### ALRT-010 — user-service

- [ ] Root cause plausible (manual review)

*Shown: a correct run out of 10.*

**Hypothesis:** Security team updated IAM policies to enforce least-privilege OAuth scopes. The change inadvertently removed the 'user:read' scope from service-to-service tokens used by user-service to call auth-service, causing sporadic 403 Forbidden errors.

**Escalation:** Page the identity-team to add missing 'user:read' scope back to service accounts and implement policy-as-code review process requiring automated scope validation before IAM changes are applied

## KPI Summary — Manual vs. Rootly

Aggregated over the 92 run(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 21 min | 23.6s | 98% mai rapid |
| Cazuri auto-rezolvate | 0% | 10/92 (11%) | +11pp |
| Cazuri care ajung la un om | 100% | 63/92 (68%) | 32pp mai putin |
| Acuratete componenta afectata | variabila | 91/100 (91%) | — |
