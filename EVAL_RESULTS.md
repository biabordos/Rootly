# Rootly — Evaluation Results (multi-agent)

Generated 2026-10-04 20:57 UTC by `python evaluate.py`.

Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).

**Root component correctly identified:** 8/10

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps · Time |
|---|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.85 | `escalate_urgent_needs_approval` | 12 steps ✅ · 35.0s ❌ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_normal` | 9 steps ✅ · 12.1s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 15 steps ❌ · 16.5s ✅ |
| ALRT-004 | order-service | — | ❌ | — | — | — | — | — | failed: No valid diagnosis package after 32 steps. |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 16 steps ❌ · 15.8s ✅ |
| ALRT-006 | order-service | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 9 steps ✅ · 10.9s ✅ |
| ALRT-007 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_normal` | 9 steps ✅ · 12.8s ✅ |
| ALRT-008 | api-gateway | `cdn-provider` (expected `cdn-provider`) | ✅ | ✅ | ✅ | ✅ | 0.80 | `escalate_normal` | 12 steps ✅ · 14.3s ✅ |
| ALRT-009 | web-frontend | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ✅ | 0.90 | `escalate_urgent_needs_approval` | 25 steps ❌ · 42.5s ❌ |
| ALRT-010 | user-service | — | ❌ | — | — | — | — | — | failed: No valid diagnosis package after 32 steps. |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## KPI Summary — Manual vs. Rootly

Aggregated over the 8 scenario(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 21 min | 20s | 98% mai rapid |
| Cazuri auto-rezolvate | 0% | 0/8 (0%) | +0pp |
| Cazuri care ajung la un om | 100% | 5/8 (62%) | 38pp mai putin |
| Acuratete componenta afectata | variabila | 8/10 (80%) | — |

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** payments-db max_connections set to 20 is insufficient for current load. Connection pool depleted under sustained high-throughput checkout flow, causing all new payment requests to timeout.

**Escalation:** Page payments-team to increase payments-db connection pool size and implement connection pool partitioning to separate batch and real-time workloads.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The user-service was OOMKilled due to high memory usage and severe GC pauses, which were caused by the recent deployment of v2.5.0. The high memory usage and GC pauses led to increased latency, which exceeded the SLO.

**Escalation:** Page the identity-team to investigate the recent deployment of v2.5.0 and address the memory issues and GC pauses.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The redis-cache service is out of memory and has been terminated by the OOM killer. This is causing the auth-service to fail, which in turn is causing the api-gateway to fail.

**Escalation:** Page the platform-team to investigate and resolve the redis-cache memory issue.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** The auth-service TLS certificate expired at 16:00:00Z, causing TLS handshake failures and a complete outage of the web-frontend.

**Escalation:** Page the security-team to renew the certificate and implement automated renewal.

### ALRT-006 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The batch job BATCH-2026-08 in payments-db is holding 18 of 20 connections for an extended period, starving real-time queries including order-service payment verifications. This is a classic connection pool exhaustion scenario where batch and OLTP workloads are competing for the same resources.

**Escalation:** Page the orders-team to investigate the batch job BATCH-2026-08 in payments-db and consider connection pool partitioning to reserve connections for real-time queries.

### ALRT-007 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The new user search feature in v2.6.0 uses a full table scan instead of an indexed query for the /api/users/search endpoint, causing high CPU usage and degraded response times under normal traffic.

**Escalation:** Page the identity-team to investigate and optimize the database query for the /api/users/search endpoint.

### ALRT-008 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The cdn-provider experienced a routing anomaly in the EU-West region, causing 40% of static asset requests to timeout. The api-gateway health checks were passing (they hit /health, not CDN-dependent paths) masking the issue.

**Escalation:** Page the platform-team to investigate and resolve the routing anomaly in the cdn-provider, specifically in the EU-West region. Implement multi-CDN failover and add synthetic monitoring that tests CDN-dependent paths to prevent similar issues in the future.

### ALRT-009 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** The payments-db failover took longer than expected due to replication lag, causing the checkout-api to fail and resulting in elevated 5xx errors from the api-gateway and web-frontend.

**Escalation:** Page the database-team to implement streaming replication monitoring and add a pre-maintenance replication lag check.
