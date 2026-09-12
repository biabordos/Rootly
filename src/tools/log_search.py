"""Tool 2 — Log search: filter logs by service, time window and optional level."""

from __future__ import annotations

from datetime import datetime

from src.data_loader import load_cmdb, load_logs

MAX_RESULTS = 30
LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]

TOOL_DEFINITION = {
    "name": "log_search",
    "description": (
        "Search application logs for a specific service within a time window. "
        "Optionally filter by log level (INFO, WARN, ERROR, FATAL). Returns up to "
        f"{MAX_RESULTS} matching log entries sorted by timestamp; if the result is "
        "truncated, narrow the window or add a level filter. Healthy-looking logs are "
        "evidence too: they can rule a component out."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "Service name to search logs for (e.g. 'payments-db')",
            },
            "start_time": {
                "type": "string",
                "description": "Start of time window in ISO 8601 format (e.g. '2026-08-18T09:00:00Z')",
            },
            "end_time": {
                "type": "string",
                "description": "End of time window in ISO 8601 format (e.g. '2026-08-18T09:15:00Z')",
            },
            "level": {
                "type": "string",
                "enum": ["INFO", "WARN", "ERROR", "FATAL"],
                "description": "Optional: only return entries with exactly this log level",
            },
        },
        "required": ["service", "start_time", "end_time"],
        "additionalProperties": False,
    },
}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def log_search(service: str, start_time: str, end_time: str, level: str | None = None) -> dict:
    service_name = service.strip().lower()
    known_services = {c.name for c in load_cmdb()}
    if service_name not in known_services:
        return {
            "error": f"Unknown service '{service}'.",
            "available_services": sorted(known_services),
        }

    try:
        start, end = _parse_time(start_time), _parse_time(end_time)
    except ValueError:
        return {"error": "start_time and end_time must be ISO 8601 timestamps, e.g. '2026-08-18T09:00:00Z'."}
    if start > end:
        return {"error": f"start_time ({start_time}) is after end_time ({end_time})."}

    level_filter = None
    if level:
        level_filter = level.strip().upper()
        if level_filter == "WARNING":
            level_filter = "WARN"
        if level_filter not in LEVELS:
            return {"error": f"Invalid level '{level}'. Use one of: {', '.join(LEVELS)}."}

    matches = [
        entry
        for entry in load_logs()
        if entry.service == service_name
        and start <= entry.timestamp <= end
        and (level_filter is None or entry.level.value == level_filter)
    ]
    return {
        "service": service_name,
        "window": {"start": start_time, "end": end_time},
        "level": level_filter,
        "total_matches": len(matches),
        "truncated": len(matches) > MAX_RESULTS,
        "logs": [
            {
                "timestamp": entry.timestamp.isoformat().replace("+00:00", "Z"),
                "level": entry.level.value,
                "event_type": entry.event_type.value,
                "message": entry.message,
                "trace_id": entry.trace_id,
            }
            for entry in matches[:MAX_RESULTS]
        ],
    }
