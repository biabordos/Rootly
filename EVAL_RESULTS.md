# Rootly — Evaluation Results (multi-agent)

Generated 2026-10-06 10:05 UTC by `python evaluate.py`.

Architecture: orchestrator + CMDB/log/synthesis specialists. The single-agent reference run is kept in [`EVAL_RESULTS_SINGLE_AGENT_BASELINE.md`](./EVAL_RESULTS_SINGLE_AGENT_BASELINE.md).

**Root component correctly identified:** 10/10 scenarios

| Alert | Service | Affected component | Correct | Attempts | Dependencies | Log evidence | Similar incident | Confidence | Escalation | Steps / Time |
|---|---|---|---|---|---|---|---|---|---|---|
| ALRT-001 | checkout-api | `payments-db` (expected `payments-db`) | pass | 1 | ✅ | ✅ | ✅ | 0.90 | escalate_urgent_needs_approval | 12 steps ✅ · 38.1s ❌ |
| ALRT-002 | user-service | `user-service` (expected `user-service`) | pass | 1 | ✅ | ✅ | ✅ | 0.80 | escalate_normal | 9 steps ✅ · 18.8s ✅ |
| ALRT-003 | api-gateway | `redis-cache` (expected `redis-cache`) | pass | 1 | ✅ | ✅ | ✅ | 0.90 | escalate_urgent_needs_approval | 18 steps ❌ · 24.4s ✅ |
| ALRT-004 | order-service | `order-service` (expected `order-service`) | pass | 1 | ✅ | ✅ | ✅ | 0.80 | escalate_urgent_needs_approval | 19 steps ❌ · 33.7s ❌ |
| ALRT-005 | web-frontend | `auth-service` (expected `auth-service`) | pass | 1 | ✅ | ✅ | ✅ | 0.90 | escalate_urgent_needs_approval | 15 steps ❌ · 26.1s ✅ |
| ALRT-006 | order-service | `payments-db` (expected `payments-db`) | pass | 1 | ✅ | ✅ | ✅ | 0.80 | escalate_urgent_needs_approval | 12 steps ✅ · 18.4s ✅ |
| ALRT-007 | user-service | `user-service` (expected `user-service`) | pass | 1 | ✅ | ✅ | ✅ | 0.80 | auto_resolved | 16 steps ❌ · 46.5s ❌ |
| ALRT-008 | api-gateway | `cdn-provider` (expected `cdn-provider`) | pass (retry 1) | 2 | ✅ | ✅ | ✅ | 0.80 | escalate_normal | 9 steps ✅ · 19.1s ✅ |
| ALRT-009 | web-frontend | `payments-db` (expected `payments-db`) | pass | 1 | ✅ | ✅ | ✅ | 0.90 | escalate_urgent_needs_approval | 25 steps ❌ · 58.4s ❌ |
| ALRT-010 | user-service | `auth-service` (expected `auth-service`) | pass | 1 | ✅ | ✅ | ✅ | 0.80 | escalate_urgent_needs_approval | 9 steps ✅ · 13.4s ✅ |

Smart retry stops at the first correct root component; up to 3 retries follow the initial attempt. Dependencies, evidence and incident are reported for the final run.

Targets: steps < 15, time < 30 s. Similar incident = the direct match from MOCK_DATA_README.md was cited.

## KPI Summary — Manual vs. Rootly

Aggregated over the 10 run(s) that produced a diagnosis.

| Metrica | Manual (estimat) | Rootly | Diferenta |
|---|---|---|---|
| Timp mediu pana la diagnostic | 20 min | 29.7s | 98% mai rapid |
| Cazuri auto-rezolvate | 0% | 1/10 (10%) | +10pp |
| Cazuri care ajung la un om | 100% | 7/10 (70%) | 30pp mai putin |
| Acuratete componenta afectata | variabila | 10/10 (100%) | — |
