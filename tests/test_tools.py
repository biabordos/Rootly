"""Unit tests for the three deterministic tools (no API key, no LLM)."""

from src.data_loader import load_alerts, load_cmdb, load_incidents, load_logs
from src.tools import cmdb_lookup, log_search, similar_incidents_search


# ── Dataset integrity ────────────────────────────────────────────────────

def test_datasets_load_and_validate():
    assert len(load_alerts()) == 5
    assert len(load_cmdb()) == 10
    assert len(load_logs()) == 124
    assert len(load_incidents()) == 10


def test_every_referenced_service_exists_in_cmdb():
    names = {c.name for c in load_cmdb()}
    assert {a.service for a in load_alerts()} <= names
    assert {entry.service for entry in load_logs()} <= names


def test_cmdb_dependencies_are_bidirectional():
    by_name = {c.name: c for c in load_cmdb()}
    for component in by_name.values():
        for ref in component.depends_on:
            target = by_name[ref.split("(")[1].rstrip(")")]
            assert f"{component.id} ({component.name})" in target.depended_by


# ── cmdb_lookup ──────────────────────────────────────────────────────────

def test_cmdb_lookup_existing():
    result = cmdb_lookup("checkout-api")
    assert result["found"] is True
    component = result["component"]
    assert component["id"] == "CI-003"
    assert "CI-006 (payments-db)" in component["depends_on"]
    assert "CI-002 (api-gateway)" in component["depended_by"]
    assert component["criticality"] == "critical"


def test_cmdb_lookup_is_case_insensitive():
    assert cmdb_lookup("  Checkout-API ")["found"] is True


def test_cmdb_lookup_missing():
    result = cmdb_lookup("nonexistent")
    assert result["found"] is False
    assert "checkout-api" in result["available_components"]


# ── log_search ───────────────────────────────────────────────────────────

def test_log_search_with_level():
    result = log_search("payments-db", "2026-08-18T09:00:00Z", "2026-08-18T09:15:00Z", level="ERROR")
    assert result["total_matches"] == 3
    assert all(entry["level"] == "ERROR" for entry in result["logs"])
    assert any("Max connections reached" in entry["message"] for entry in result["logs"])


def test_log_search_results_are_chronological():
    result = log_search("auth-service", "2026-08-20T03:00:00Z", "2026-08-20T03:30:00Z")
    timestamps = [entry["timestamp"] for entry in result["logs"]]
    assert timestamps == sorted(timestamps)
    assert result["total_matches"] == 7


def test_log_search_empty_window():
    result = log_search("checkout-api", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")
    assert result["total_matches"] == 0
    assert result["logs"] == []


def test_log_search_unknown_service():
    assert "error" in log_search("mystery-service", "2026-08-18T09:00:00Z", "2026-08-18T09:15:00Z")


def test_log_search_rejects_inverted_window():
    assert "error" in log_search("checkout-api", "2026-08-18T10:00:00Z", "2026-08-18T09:00:00Z")


def test_log_search_truncates_to_max_results():
    result = log_search("api-gateway", "2026-08-01T00:00:00Z", "2026-08-31T00:00:00Z")
    assert result["total_matches"] > len(result["logs"]) or not result["truncated"]
    assert len(result["logs"]) <= 30


# ── similar_incidents_search ─────────────────────────────────────────────

def test_incident_search():
    result = similar_incidents_search("connection pool exhaustion")
    ids = [r["id"] for r in result["results"]]
    assert len(ids) == 3
    assert "INC-2025-114" in ids


def test_incident_search_ranks_direct_match_first_for_each_scenario():
    queries = {
        "checkout-api connection timeouts, payments-db max connections reached": "INC-2025-114",
        "memory leak after deployment, OOMKilled pod restarts": "INC-2025-203",
        "redis-cache OOM crash, auth-service outage cascading to api-gateway": "INC-2025-156",
        "config change reduced database query timeout, queries timing out": "INC-2025-278",
        "auth-service TLS certificate expired, SSL handshake failures": "INC-2025-341",
    }
    for query, expected in queries.items():
        assert similar_incidents_search(query)["results"][0]["id"] == expected, query


def test_incident_search_hides_evaluation_metadata():
    result = similar_incidents_search("certificate expired")
    assert all("root_cause_category" not in r for r in result["results"])


def test_incident_search_rejects_empty_query():
    assert "error" in similar_incidents_search("   ")
