# Rootly — Evaluation Results

Generated 2026-09-13 16:20 UTC by `python evaluate.py`.

**Root component correctly identified:** 4/5

| Alert | Service | Affected component | Correct | Dependencies | Log evidence | Similar incident | Confidence | Steps · Time |
|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | ✅ | ✅ | ✅ | ❌ | 0.90 | 4 steps ✅ · 13.2s ✅ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | ✅ | ✅ | ✅ | ❌ | 0.90 | 3 steps ✅ · 10.2s ✅ |
| ALRT-003 | api-gateway | `auth-service` (expected `redis-cache`) | ❌ | ❌ | ✅ | ✅ | 0.90 | 4 steps ✅ · 71.6s ❌ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | 4 steps ✅ · 8.7s ✅ |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | ✅ | ✅ | ✅ | ✅ | 0.90 | 4 steps ✅ · 8.7s ✅ |

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## Per-scenario review

### ALRT-001 — checkout-api

- [ ] Root cause plausible (manual review)

**Hypothesis:** The payments-db connection pool has been exhausted due to high load, causing connection timeouts and failures when the checkout-api attempts to connect.

**Escalation:** The payments-team should investigate the root cause of the high load on the payments-db and take steps to mitigate the issue, such as increasing the connection pool size or optimizing the database queries. The checkout-api should implement proper error handling and retry mechanisms to handle connection timeouts and failures more gracefully.

### ALRT-002 — user-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The user-service is suffering from memory exhaustion and excessive garbage collection pauses, likely due to a memory leak or inefficient memory usage in the new version v2.5.0, which was deployed at 2026-08-19T14:25:00Z.

**Escalation:** Page the identity-team to investigate the memory issues and potential memory leak in the user-service, focusing on the v2.5.0 deployment and the associated garbage collection behaviour.

### ALRT-003 — api-gateway

- [ ] Root cause plausible (manual review)

**Hypothesis:** The auth-service is unable to connect to its Redis cache, causing it to fail all authentication requests. This is likely due to a Redis outage or a network issue between the auth-service and Redis.

**Escalation:** Page the platform-team to investigate the Redis outage and ensure the auth-service can recover.

### ALRT-004 — order-service

- [ ] Root cause plausible (manual review)

**Hypothesis:** The recent configuration change in order-service that reduced the payments-db query timeout from 5000ms to 50ms is too aggressive for complex queries, causing timeouts and a high error rate.

**Escalation:** Page the orders-team to investigate and revert the configuration change to a more appropriate timeout value.

### ALRT-005 — web-frontend

- [ ] Root cause plausible (manual review)

**Hypothesis:** The auth-service's internal TLS certificate expired, causing api-gateway to fail all requests to auth-service over HTTPS. This broke all auth-dependent routes in web-frontend, resulting in a complete outage.

**Escalation:** Page platform-team to renew the auth-service's internal TLS certificate and implement automated renewal. Also, add monitoring for internal certificate expiry dates.
