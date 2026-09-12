"""
Registry for the agent's data tools: exposes their JSON schemas to the LLM and
dispatches tool calls to the matching Python function, timing each call.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.tools.cmdb_lookup import TOOL_DEFINITION as CMDB_LOOKUP_TOOL, cmdb_lookup
from src.tools.incident_search import TOOL_DEFINITION as INCIDENT_SEARCH_TOOL, similar_incidents_search
from src.tools.log_search import TOOL_DEFINITION as LOG_SEARCH_TOOL, log_search

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    input: dict
    result: dict
    duration_ms: float
    is_error: bool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[dict, Callable[..., dict]]] = {}
        self.register(CMDB_LOOKUP_TOOL, cmdb_lookup)
        self.register(LOG_SEARCH_TOOL, log_search)
        self.register(INCIDENT_SEARCH_TOOL, similar_incidents_search)

    def register(self, definition: dict, func: Callable[..., dict]) -> None:
        self._tools[definition["name"]] = (definition, func)

    def tool_definitions(self) -> list[dict]:
        # Stable order keeps the tools prefix byte-identical across requests (prompt caching).
        return [definition for definition, _ in self._tools.values()]

    def execute(self, name: str, tool_input: dict[str, Any]) -> ToolCallRecord:
        started = time.perf_counter()
        if name not in self._tools:
            result = {"error": f"Unknown tool '{name}'. Available: {', '.join(self._tools)}"}
        else:
            _, func = self._tools[name]
            try:
                result = func(**tool_input)
            except TypeError as exc:
                result = {"error": f"Invalid arguments for {name}: {exc}"}
            except Exception as exc:  # a tool bug should become an observation, not crash the run
                logger.exception("Tool %s failed", name)
                result = {"error": f"{name} failed: {exc}"}
        duration_ms = (time.perf_counter() - started) * 1000
        is_error = "error" in result
        logger.info("tool=%s input=%s duration_ms=%.1f error=%s", name, json.dumps(tool_input), duration_ms, is_error)
        return ToolCallRecord(name, tool_input, result, duration_ms, is_error)
