"""Tool 1 — CMDB lookup: component metadata plus direct dependencies/dependents."""

from __future__ import annotations

from src.data_loader import load_cmdb

TOOL_DEFINITION = {
    "name": "cmdb_lookup",
    "description": (
        "Look up a component in the CMDB to find its type, owner team, criticality, "
        "last deployment, config version, dependencies (depends_on) and dependents "
        "(depended_by). Use this to understand what a service depends on and what "
        "depends on it, so you can decide which neighbouring components to investigate next."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "component_name": {
                "type": "string",
                "description": "Name of the component to look up (e.g. 'checkout-api')",
            }
        },
        "required": ["component_name"],
        "additionalProperties": False,
    },
}


def cmdb_lookup(component_name: str) -> dict:
    wanted = component_name.strip().lower()
    components = load_cmdb()
    for component in components:
        if component.name.lower() == wanted or component.id.lower() == wanted:
            return {"found": True, "component": component.model_dump(mode="json")}
    return {
        "found": False,
        "error": f"Component '{component_name}' not found in CMDB.",
        "available_components": sorted(c.name for c in components),
    }
