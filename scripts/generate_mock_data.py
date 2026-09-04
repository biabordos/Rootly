"""
Generate Rootly's mock data (CMDB, alerts, logs, historical incidents).

Faza 1 of the implementation plan (see ROADMAP.md / Rootly_Plan_Implementare.txt).
Run:
    python scripts/generate_mock_data.py
Writes data/cmdb.json, data/alerts.json, data/logs.json, data/incidents.json.

Design notes
------------
- The CMDB graph and the checkout-api / payments-db example are kept consistent
  with the sample entities already documented in README.md section 5.2.
- Three alert scenarios are defined, each with a distinct root cause, and each
  root cause has a matching historical incident in incidents.json so that
  similar_incidents_search() has something real to retrieve.
- Logs are split into "scenario logs" (hand-written, causally consistent with
  the CMDB dependency chain for each alert) and "background noise" (generated,
  spread across the same date range on unrelated services) so that log_search's
  time-window filtering is actually meaningful instead of every log being
  relevant.
"""

import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
random.seed(42)

# ---------------------------------------------------------------------------
# 1. CMDB
# ---------------------------------------------------------------------------

CMDB = [
    {
        "id": "CI-010",
        "name": "web-frontend",
        "type": "frontend",
        "owner_team": "web-team",
        "depends_on": ["CI-014 (checkout-api)", "CI-030 (auth-service)"],
        "depended_by": [],
    },
    {
        "id": "CI-014",
        "name": "checkout-api",
        "type": "microservice",
        "owner_team": "payments-team",
        "depends_on": [
            "CI-021 (payments-db)",
            "CI-030 (auth-service)",
            "CI-050 (inventory-service)",
            "CI-070 (order-service)",
        ],
        "depended_by": ["CI-010 (web-frontend)"],
    },
    {
        "id": "CI-021",
        "name": "payments-db",
        "type": "database",
        "owner_team": "payments-team",
        "depends_on": [],
        "depended_by": ["CI-014 (checkout-api)", "CI-070 (order-service)"],
    },
    {
        "id": "CI-030",
        "name": "auth-service",
        "type": "microservice",
        "owner_team": "platform-team",
        "depends_on": ["CI-031 (user-db)"],
        "depended_by": ["CI-014 (checkout-api)", "CI-010 (web-frontend)"],
    },
    {
        "id": "CI-031",
        "name": "user-db",
        "type": "database",
        "owner_team": "platform-team",
        "depends_on": [],
        "depended_by": ["CI-030 (auth-service)"],
    },
    {
        "id": "CI-050",
        "name": "inventory-service",
        "type": "microservice",
        "owner_team": "catalog-team",
        "depends_on": ["CI-051 (inventory-db)", "CI-060 (cache-redis)"],
        "depended_by": ["CI-014 (checkout-api)"],
    },
    {
        "id": "CI-051",
        "name": "inventory-db",
        "type": "database",
        "owner_team": "catalog-team",
        "depends_on": [],
        "depended_by": ["CI-050 (inventory-service)"],
    },
    {
        "id": "CI-060",
        "name": "cache-redis",
        "type": "cache",
        "owner_team": "platform-team",
        "depends_on": [],
        "depended_by": ["CI-050 (inventory-service)", "CI-070 (order-service)"],
    },
    {
        "id": "CI-070",
        "name": "order-service",
        "type": "microservice",
        "owner_team": "orders-team",
        "depends_on": [
            "CI-021 (payments-db)",
            "CI-060 (cache-redis)",
            "CI-080 (notification-service)",
        ],
        "depended_by": ["CI-014 (checkout-api)"],
    },
    {
        "id": "CI-080",
        "name": "notification-service",
        "type": "microservice",
        "owner_team": "platform-team",
        "depends_on": [],
        "depended_by": ["CI-070 (order-service)"],
    },
]

ALL_SERVICES = [c["name"] for c in CMDB]

# ---------------------------------------------------------------------------
# 2. Alerts (3 scenarios, 3 distinct root causes)
# ---------------------------------------------------------------------------

ALERTS = [
    {
        "id": "ALRT-001",
        "service": "checkout-api",
        "alert_type": "error_rate_high",
        "severity_reported": "high",
        "timestamp": "2026-08-18T09:12:00Z",
        "metric_value": 7.4,
    },
    {
        "id": "ALRT-002",
        "service": "inventory-service",
        "alert_type": "latency_high",
        "severity_reported": "medium",
        "timestamp": "2026-08-20T14:30:00Z",
        "metric_value": 2450.0,
    },
    {
        "id": "ALRT-003",
        "service": "auth-service",
        "alert_type": "unavailability",
        "severity_reported": "critical",
        "timestamp": "2026-08-22T03:45:40Z",
        "metric_value": 100.0,
    },
]

# ---------------------------------------------------------------------------
# 3. Historical incidents (RAG corpus)
# ---------------------------------------------------------------------------

INCIDENTS = [
    {
        "id": "INC-2025-114",
        "description": "checkout-api errors caused by payments-db connection pool exhaustion",
        "root_cause": "DB connection pool misconfigured after a scale-up event",
        "resolution": "Increased pool size and added a circuit breaker",
        "tags": ["checkout-api", "payments-db", "timeout"],
    },
    {
        "id": "INC-2025-098",
        "description": "inventory-service latency spike caused by a cache-redis eviction storm after the memory limit was reached",
        "root_cause": "Redis maxmemory too low for the working set, triggering aggressive eviction under load",
        "resolution": "Increased maxmemory and tuned the eviction policy; added a memory-usage alert",
        "tags": ["inventory-service", "cache-redis", "latency"],
    },
    {
        "id": "INC-2025-076",
        "description": "auth-service crash loop after user-db exhausted its connection limit",
        "root_cause": "auth-service was autoscaled to handle a traffic spike without raising user-db's max_connections, exhausting the pool",
        "resolution": "Added a pgbouncer connection pooler in front of user-db",
        "tags": ["auth-service", "user-db", "crash"],
    },
    {
        "id": "INC-2025-055",
        "description": "web-frontend returning intermittent 502s during high traffic",
        "root_cause": "auth-service token validation calls timing out under load",
        "resolution": "Added a short-lived cache for token validation results",
        "tags": ["web-frontend", "auth-service", "latency"],
    },
    {
        "id": "INC-2025-041",
        "description": "order-service occasionally processing orders with stale payment status",
        "root_cause": "payments-db read replica lag under heavy write load",
        "resolution": "Routed payment-status reads for in-flight orders to the primary",
        "tags": ["order-service", "payments-db", "data-consistency"],
    },
    {
        "id": "INC-2024-233",
        "description": "notification-service silently dropping order-confirmation messages",
        "root_cause": "Unhandled exception on a malformed message payload",
        "resolution": "Added schema validation and a dead-letter queue for failed messages",
        "tags": ["notification-service", "error-handling"],
    },
    {
        "id": "INC-2024-198",
        "description": "inventory-service write failures caused by inventory-db running out of disk space",
        "root_cause": "Disk usage grew past capacity with no autoscaling or alerting in place",
        "resolution": "Enabled storage autoscaling and added a disk-usage alert",
        "tags": ["inventory-db", "inventory-service", "disk"],
    },
    {
        "id": "INC-2024-150",
        "description": "checkout-api elevated error rate right after a deployment",
        "root_cause": "A bad deployment shipped with a missing environment variable, causing a crash loop",
        "resolution": "Rolled back the deployment and added pre-deploy config validation",
        "tags": ["checkout-api", "deployment"],
    },
]

# ---------------------------------------------------------------------------
# 4. Logs
# ---------------------------------------------------------------------------

# 4a. Scenario logs — hand-written so they causally match each alert's root
# cause and follow the CMDB dependency chain (see incidents above).
SCENARIO_LOGS = [
    # --- ALRT-001: checkout-api error_rate_high -> payments-db pool exhaustion
    {"timestamp": "2026-08-18T09:05:11Z", "service": "checkout-api", "level": "INFO",
     "message": "Payment request received for order ORD-88213", "trace_id": "abc118"},
    {"timestamp": "2026-08-18T09:06:47Z", "service": "payments-db", "level": "WARN",
     "message": "Connection pool at 95% capacity (190/200 active connections)", "trace_id": "abc118"},
    {"timestamp": "2026-08-18T09:08:02Z", "service": "checkout-api", "level": "WARN",
     "message": "Payment request queued, pool near capacity", "trace_id": "abc119"},
    {"timestamp": "2026-08-18T09:09:15Z", "service": "payments-db", "level": "ERROR",
     "message": "Max connections reached, rejecting new connection", "trace_id": "abc119"},
    {"timestamp": "2026-08-18T09:10:32Z", "service": "checkout-api", "level": "ERROR",
     "message": "Connection timeout to payments-db", "trace_id": "abc123"},
    {"timestamp": "2026-08-18T09:10:58Z", "service": "checkout-api", "level": "ERROR",
     "message": "Failed to process checkout for order ORD-88214: upstream timeout", "trace_id": "abc123"},
    {"timestamp": "2026-08-18T09:11:20Z", "service": "payments-db", "level": "ERROR",
     "message": "Max connections reached, rejecting new connection", "trace_id": "abc124"},
    {"timestamp": "2026-08-18T09:11:45Z", "service": "web-frontend", "level": "WARN",
     "message": "Upstream checkout-api returned 503 for /checkout", "trace_id": "abc124"},
    {"timestamp": "2026-08-18T09:12:00Z", "service": "checkout-api", "level": "ERROR",
     "message": "Error rate threshold exceeded: 7.4% over last 5 minutes", "trace_id": "abc125"},

    # --- ALRT-002: inventory-service latency_high -> cache-redis eviction storm
    {"timestamp": "2026-08-20T14:20:03Z", "service": "cache-redis", "level": "WARN",
     "message": "Memory usage at 92% of maxmemory, evictions increasing", "trace_id": "def201"},
    {"timestamp": "2026-08-20T14:22:41Z", "service": "cache-redis", "level": "WARN",
     "message": "Evicted 1200 keys in the last 60s (allkeys-lru policy)", "trace_id": "def201"},
    {"timestamp": "2026-08-20T14:24:19Z", "service": "inventory-service", "level": "WARN",
     "message": "Cache miss rate elevated: 68% (baseline 12%)", "trace_id": "def202"},
    {"timestamp": "2026-08-20T14:25:37Z", "service": "inventory-service", "level": "ERROR",
     "message": "Redis GET timeout for key inventory:sku:SKU-4471", "trace_id": "def203"},
    {"timestamp": "2026-08-20T14:27:02Z", "service": "inventory-db", "level": "WARN",
     "message": "Query queue depth increasing (120 pending reads)", "trace_id": "def203"},
    {"timestamp": "2026-08-20T14:28:14Z", "service": "inventory-service", "level": "ERROR",
     "message": "Redis GET timeout for key inventory:sku:SKU-5502", "trace_id": "def204"},
    {"timestamp": "2026-08-20T14:29:50Z", "service": "checkout-api", "level": "WARN",
     "message": "Upstream inventory-service latency high, slow checkout for order ORD-88401", "trace_id": "def205"},
    {"timestamp": "2026-08-20T14:30:00Z", "service": "inventory-service", "level": "ERROR",
     "message": "p95 latency 2450ms for GET /inventory/{sku} (SLO: 300ms)", "trace_id": "def205"},

    # --- ALRT-003: auth-service unavailability -> user-db connection exhaustion
    {"timestamp": "2026-08-22T03:38:02Z", "service": "auth-service", "level": "INFO",
     "message": "Autoscaler increased replicas from 4 to 12 (CPU > 80%)", "trace_id": "ghi301"},
    {"timestamp": "2026-08-22T03:40:19Z", "service": "user-db", "level": "WARN",
     "message": "Active connections 180/200", "trace_id": "ghi301"},
    {"timestamp": "2026-08-22T03:42:07Z", "service": "user-db", "level": "ERROR",
     "message": "FATAL: remaining connection slots reserved for non-replication superuser connections", "trace_id": "ghi302"},
    {"timestamp": "2026-08-22T03:42:33Z", "service": "auth-service", "level": "ERROR",
     "message": "Failed to acquire DB connection from pool: pool exhausted", "trace_id": "ghi302"},
    {"timestamp": "2026-08-22T03:43:51Z", "service": "auth-service", "level": "ERROR",
     "message": "Failed to acquire DB connection from pool: pool exhausted", "trace_id": "ghi303"},
    {"timestamp": "2026-08-22T03:44:10Z", "service": "auth-service", "level": "ERROR",
     "message": "Health check failed on /healthz, restarting pod auth-service-7f9c-x2k1", "trace_id": "ghi304"},
    {"timestamp": "2026-08-22T03:44:45Z", "service": "checkout-api", "level": "WARN",
     "message": "Auth token validation failing intermittently for upstream auth-service", "trace_id": "ghi304"},
    {"timestamp": "2026-08-22T03:45:10Z", "service": "web-frontend", "level": "ERROR",
     "message": "Login requests failing: upstream auth-service unavailable (503)", "trace_id": "ghi305"},
    {"timestamp": "2026-08-22T03:45:40Z", "service": "auth-service", "level": "ERROR",
     "message": "CrashLoopBackOff: 5 restarts in the last 5 minutes", "trace_id": "ghi306"},
]

# 4b. Background noise — normal operational logs spread across the same date
# range on services/times unrelated to the three incidents above, so that a
# time-window log_search actually needs to filter something out.
NOISE_TEMPLATES = {
    "INFO": [
        "Health check OK",
        "Request processed successfully in {ms}ms",
        "Scheduled job '{job}' completed",
        "Cache hit for key {key}",
        "Deployment finished successfully (v{ver})",
        "Config reloaded without restart",
    ],
    "DEBUG": [
        "Incoming request GET /status",
        "Connection pool stats: {active}/{total} active",
        "Background sync tick completed",
    ],
}
JOBS = ["daily-report", "inventory-reconcile", "session-cleanup", "metrics-flush"]

NOISE_DAYS = ["15", "16", "17", "19", "21", "23", "24", "25"]  # avoid incident days on purpose
NOISE_HOURS = range(0, 24)


def _generate_noise(n: int) -> list[dict]:
    entries = []
    for _ in range(n):
        service = random.choice(ALL_SERVICES)
        level = random.choices(["INFO", "DEBUG"], weights=[0.7, 0.3])[0]
        template = random.choice(NOISE_TEMPLATES[level])
        message = template.format(
            ms=random.randint(20, 180),
            job=random.choice(JOBS),
            key=f"cache:{random.choice(['user', 'sku', 'session'])}:{random.randint(1000, 9999)}",
            ver=f"{random.randint(1, 3)}.{random.randint(0, 9)}.{random.randint(0, 9)}",
            active=random.randint(10, 60),
            total=100,
        )
        day = random.choice(NOISE_DAYS)
        hour = random.choice(list(NOISE_HOURS))
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        entries.append({
            "timestamp": f"2026-08-{day}T{hour:02d}:{minute:02d}:{second:02d}Z",
            "service": service,
            "level": level,
            "message": message,
            "trace_id": f"noise-{random.randint(100000, 999999):06d}",
        })
    return entries


def build_logs() -> list[dict]:
    logs = list(SCENARIO_LOGS) + _generate_noise(45)
    logs.sort(key=lambda e: e["timestamp"])
    return logs


# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------

def write_json(name: str, data) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {path} ({len(data)} entries)")


def main() -> None:
    write_json("cmdb.json", CMDB)
    write_json("alerts.json", ALERTS)
    write_json("incidents.json", INCIDENTS)
    write_json("logs.json", build_logs())


if __name__ == "__main__":
    main()
