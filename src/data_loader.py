"""
Load and validate the mock datasets in data/.

Every loader parses the JSON once (cached), skips "_comment" separator
entries, and validates each record against its Pydantic schema so that a
malformed dataset fails loudly at startup instead of mid-investigation.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from src.models.schemas import Alert, CMDBComponent, HistoricalIncident, LogEntry

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read(name: str) -> list[dict]:
    with (DATA_DIR / name).open(encoding="utf-8") as f:
        records = json.load(f)
    return [r for r in records if "_comment" not in r]


@lru_cache(maxsize=1)
def load_alerts() -> tuple[Alert, ...]:
    return tuple(Alert.model_validate(r) for r in _read("alerts.json"))


@lru_cache(maxsize=1)
def load_cmdb() -> tuple[CMDBComponent, ...]:
    return tuple(CMDBComponent.model_validate(r) for r in _read("cmdb.json"))


@lru_cache(maxsize=1)
def load_logs() -> tuple[LogEntry, ...]:
    logs = (LogEntry.model_validate(r) for r in _read("logs.json"))
    return tuple(sorted(logs, key=lambda e: e.timestamp))


@lru_cache(maxsize=1)
def load_incidents() -> tuple[HistoricalIncident, ...]:
    return tuple(HistoricalIncident.model_validate(r) for r in _read("incidents.json"))


def get_alert(alert_id: str) -> Alert:
    for alert in load_alerts():
        if alert.id == alert_id.strip().upper():
            return alert
    known = ", ".join(a.id for a in load_alerts())
    raise KeyError(f"Unknown alert '{alert_id}'. Available: {known}")
