"""Data preparation and rendering for Rootly's CMDB system map."""

from __future__ import annotations

import html
import json
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import streamlit.components.v1 as components

from src.data_loader import load_alerts, load_cmdb, load_logs
from src.models.schemas import Alert, CMDBComponent, LogEntry


def _component_id(reference: str) -> str:
    return reference.split(" ", 1)[0]


def _seconds_label(delta: timedelta) -> str:
    seconds = abs(delta.total_seconds())
    if seconds < 60:
        value = f"{seconds:.0f}s"
    elif seconds < 3600:
        value = f"{seconds / 60:.1f}m"
    else:
        value = f"{seconds / 3600:.1f}h"
    return value


def _format_log(log: LogEntry) -> str:
    return f"{log.timestamp.strftime('%H:%M:%S')} · {log.level.value} · {log.message}"


def _connected_component_ids(root_id: str, components_by_id: dict[str, CMDBComponent]) -> dict[str, int]:
    distances = {root_id: 0}
    queue = deque([root_id])
    while queue:
        current = queue.popleft()
        component = components_by_id[current]
        neighbors = [_component_id(ref) for ref in component.depends_on + component.depended_by]
        for neighbor in neighbors:
            if neighbor in components_by_id and neighbor not in distances:
                distances[neighbor] = distances[current] + 1
                queue.append(neighbor)
    return distances


def build_system_map(alert: Alert) -> dict[str, Any]:
    components = load_cmdb()
    components_by_id = {component.id: component for component in components}
    root = next((component for component in components if component.name == alert.service), None)
    root_id = root.id if root else ""
    distances = _connected_component_ids(root_id, components_by_id) if root_id else {}

    # Scenario evidence is limited to the alert's real UTC calendar date; no time window is invented.
    scenario_logs = [log for log in load_logs() if log.timestamp.date() == alert.timestamp.date()]
    logs_by_service: dict[str, list[LogEntry]] = {}
    for log in scenario_logs:
        logs_by_service.setdefault(log.service, []).append(log)

    nodes = []
    for component in components:
        evidence = logs_by_service.get(component.name, [])
        nodes.append({
            "id": component.id,
            "name": component.name,
            "type": component.type.value,
            "owner": component.owner_team,
            "criticality": component.criticality.value,
            "layer": distances.get(component.id),
            "root": component.id == root_id,
            "logs": [_format_log(log) for log in evidence],
            "earliest": min((log.timestamp for log in evidence), default=None).isoformat() if evidence else None,
        })

    edges = []
    seen: set[tuple[str, str]] = set()
    for component in components:
        for reference in component.depends_on:
            dependency_id = _component_id(reference)
            if dependency_id not in components_by_id:
                continue
            pair = tuple(sorted((component.id, dependency_id)))
            if pair in seen:
                continue
            seen.add(pair)
            left_logs = logs_by_service.get(component.name, [])
            right_logs = logs_by_service.get(components_by_id[dependency_id].name, [])
            left_earliest = min((log.timestamp for log in left_logs), default=None)
            right_earliest = min((log.timestamp for log in right_logs), default=None)
            delta = None
            if left_earliest and right_earliest:
                delta = {
                    "earlier": component.name if left_earliest < right_earliest else components_by_id[dependency_id].name,
                    "later": components_by_id[dependency_id].name if left_earliest < right_earliest else component.name,
                    "label": _seconds_label(right_earliest - left_earliest),
                }
            edges.append({"source": component.id, "target": dependency_id, "delta": delta})

    return {"alert": {"id": alert.id, "service": alert.service, "timestamp": alert.timestamp.isoformat()}, "nodes": nodes, "edges": edges}


def _node_position(nodes: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    layers: dict[int, list[dict[str, Any]]] = {}
    max_layer = max((node["layer"] for node in nodes if node["layer"] is not None), default=0)
    for node in nodes:
        layer = node["layer"] if node["layer"] is not None else max_layer + 1
        layers.setdefault(layer, []).append(node)
    max_rows = max((len(layer_nodes) for layer_nodes in layers.values()), default=1)
    positions = {}
    for layer, layer_nodes in layers.items():
        x = 140 + layer * 320
        vertical_offset = (max_rows - len(layer_nodes)) / 2
        for index, node in enumerate(layer_nodes):
            positions[node["id"]] = (x, 110 + (vertical_offset + index) * 150)
    return positions


def render_system_map(alert: Alert) -> None:
    data = build_system_map(alert)
    positions = _node_position(data["nodes"])
    for node in data["nodes"]:
        node["x"], node["y"] = positions[node["id"]]
    by_id = {node["id"]: node for node in data["nodes"]}
    width = max((node["x"] for node in data["nodes"]), default=1100) + 300
    height = max((node["y"] for node in data["nodes"]), default=600) + 130

    svg_edges = []
    for edge in data["edges"]:
        source = by_id[edge["source"]]
        target = by_id[edge["target"]]
        active = source["layer"] is not None and target["layer"] is not None
        delta = edge["delta"]
        delta_text = ""
        if delta:
            delta_text = f"{delta['earlier']} appeared {delta['label']} before {delta['later']}"
        svg_edges.append(
            f'<g class="edge {"active" if active else "dim"}">'
            f'<line x1="{source["x"]}" y1="{source["y"]}" x2="{target["x"]}" y2="{target["y"]}" '
            f'data-tooltip="{html.escape(delta_text or "Dependency relationship", quote=True)}" /></g>'
        )

    svg_nodes = []
    for node in data["nodes"]:
        evidence = "<br>".join(html.escape(log) for log in node["logs"]) or "No same-day log evidence"
        details = f"{node['id']} · {node['name']}<br>Type: {node['type']}<br>Owner: {node['owner']}<br>{evidence}"
        state = "root" if node["root"] else "active" if node["layer"] is not None else "dim"
        svg_nodes.append(
            f'<g class="station {state}" transform="translate({node["x"]} {node["y"]})" '
            f'data-tooltip="{html.escape(details, quote=True)}">'
            f'<circle r="15"></circle><circle class="station-core" r="5"></circle>'
            f'<text x="24" y="-3">{html.escape(node["name"])}</text>'
            f'<text x="24" y="14" class="node-meta">{html.escape(node["id"])} · {html.escape(node["type"])}</text></g>'
        )

    payload = json.dumps(data, separators=(",", ":"))
    document = f"""
    <style>
      * {{ box-sizing: border-box; }} body {{ margin:0; background:#050505; color:#f5f5f2; font-family:Helvetica,Arial,sans-serif; }}
      .map-shell {{ border:1px solid #252525; background:#090909; padding:18px; overflow:hidden; }}
      .map-header {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:12px; }}
      .eyebrow {{ color:#d9ff62; font:500 11px Consolas,monospace; letter-spacing:.12em; text-transform:uppercase; }}
      .map-title {{ margin:5px 0 0; font-size:21px; font-weight:500; }} .map-subtitle {{ color:#8e8e8e; font-size:12px; line-height:1.45; max-width:550px; }}
      .legend {{ color:#8e8e8e; font:11px Consolas,monospace; white-space:nowrap; }} .legend i {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; }}
      .legend .green {{ background:#d9ff62; }} .legend .gray {{ background:#555; }} .legend .line {{ width:20px; height:2px; border-radius:0; background:#d9ff62; vertical-align:middle; }}
      svg {{ width:100%; min-width:920px; display:block; }} .edge line {{ stroke:#353535; stroke-width:3; }} .edge.active line {{ stroke:#d9ff62; stroke-width:4; stroke-dasharray:8 10; animation:flow 2.5s linear infinite; }}
    .station circle {{ fill:#555; stroke:#050505; stroke-width:4; }} .station .station-core {{ fill:#555; stroke:none; }}
      .station.active circle {{ fill:#d9ff62; }} .station.active .station-core,.station.root .station-core {{ fill:#050505; }} .station.root circle {{ fill:#ff6b6b; }}
      .station text {{ fill:#f5f5f2; font-size:14px; }} .station .node-meta {{ fill:#777; font:10px Consolas,monospace; }} .station.dim {{ opacity:.35; }}
      .station:hover circle {{ stroke:#fff; stroke-width:5; }} .tooltip {{ position:fixed; display:none; max-width:360px; padding:10px 12px; background:#171717; border:1px solid #454545; color:#f5f5f2; font:11px Consolas,monospace; line-height:1.5; pointer-events:none; z-index:2; }}
      @keyframes flow {{ to {{ stroke-dashoffset:-36; }} }}
    </style>
    <div class="map-shell"><div class="map-header"><div><div class="eyebrow">Live incident map / {html.escape(alert.id)}</div><div class="map-title">{html.escape(alert.service)} dependency transit</div><div class="map-subtitle">Stations are real CMDB components. The highlighted route follows the selected alert's connected dependency graph; animated lines indicate dependency flow.</div></div><div class="legend"><span><i class="green"></i>investigation chain</span><br><span><i class="gray"></i>unaffected</span><br><span><i class="line"></i>dependency</span></div></div><svg viewBox="0 0 {width} {height}" role="img" aria-label="CMDB dependency map">{''.join(svg_edges)}{''.join(svg_nodes)}</svg><div class="tooltip" id="tooltip"></div></div>
    <script>
      const tooltip = document.getElementById('tooltip');
      document.querySelectorAll('[data-tooltip]').forEach((item) => {{
        item.addEventListener('mousemove', (event) => {{ tooltip.innerHTML = item.dataset.tooltip; tooltip.style.display = 'block'; tooltip.style.left = `${{event.clientX + 14}}px`; tooltip.style.top = `${{event.clientY + 14}}px`; }});
        item.addEventListener('mouseleave', () => {{ tooltip.style.display = 'none'; }});
      }});
    </script>
    """
    components.html(document, height=min(max(int(height + 80), 560), 900), scrolling=True)
