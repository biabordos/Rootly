"""Data preparation and rendering for Rootly's CMDB system map.

Renders the dependency graph as status cards positioned in layered
columns (one column per BFS hop from the alerted component), matching
the "Material Design Ripple Effect" reference's .graph-node visual
language. Node health (healthy / degraded / critical / investigating)
and the single highlighted "agent path" are derived from the same-day
log evidence actually attached to each component — not hardcoded.
"""

from __future__ import annotations

import html
import json
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import streamlit.components.v1 as components

from src.data_loader import load_alerts, load_cmdb, load_logs
from src.models.schemas import Alert, CMDBComponent, LogEntry

ERROR_LEVELS = {"ERROR", "FATAL"}


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
        error_evidence = [log for log in evidence if log.level.value in ERROR_LEVELS]
        nodes.append({
            "id": component.id,
            "name": component.name,
            "type": component.type.value,
            "owner": component.owner_team,
            "criticality": component.criticality.value,
            "layer": distances.get(component.id),
            "root": component.id == root_id,
            "logs": [_format_log(log) for log in evidence],
            "earliest": min((log.timestamp for log in evidence), default=None),
            "earliest_error": min((log.timestamp for log in error_evidence), default=None),
            "has_warn": any(log.level.value == "WARN" for log in evidence),
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

    return {"alert": {"id": alert.id, "service": alert.service, "timestamp": alert.timestamp.isoformat()}, "nodes": nodes, "edges": edges, "root_id": root_id}


def _root_cause_path(data: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Pick the most likely upstream origin and the chain of component ids
    from the alerted component to it, using same-day ERROR/FATAL evidence.

    A candidate must show error-level evidence at or before the alerted
    component's own earliest error (an effect can't precede its cause),
    which keeps routine same-day noise (daily backups, health checks —
    always INFO/WARN) from being mistaken for the origin.
    """
    nodes = data["nodes"]
    root_id = data["root_id"]
    by_id = {n["id"]: n for n in nodes}
    root = by_id.get(root_id)
    if not root:
        return None, []

    baseline = root["earliest_error"]
    candidates = [
        n for n in nodes
        if n["id"] != root_id
        and n["layer"] is not None
        and n["earliest_error"] is not None
        and (baseline is None or n["earliest_error"] <= baseline)
    ]
    if not candidates:
        return None, []
    origin = min(candidates, key=lambda n: n["earliest_error"])

    # BFS parent pointers over the (undirected) edge list to recover the
    # shortest chain from the alerted component to the origin.
    adjacency: dict[str, list[str]] = {}
    for edge in data["edges"]:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
        adjacency.setdefault(edge["target"], []).append(edge["source"])
    parents: dict[str, str | None] = {root_id: None}
    queue = deque([root_id])
    while queue:
        current = queue.popleft()
        if current == origin["id"]:
            break
        for neighbor in adjacency.get(current, []):
            if neighbor not in parents:
                parents[neighbor] = current
                queue.append(neighbor)

    path = [origin["id"]]
    cursor = parents.get(origin["id"])
    while cursor is not None:
        path.append(cursor)
        cursor = parents.get(cursor)
    path.reverse()
    return origin["id"], path


def _classify_health(data: dict[str, Any]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Return {node_id: {"status": ..., "badge": ...}} plus the ordered
    chain of node ids along the highlighted agent path."""
    origin_id, path = _root_cause_path(data)
    statuses: dict[str, dict[str, str]] = {}
    for node in data["nodes"]:
        if node["root"]:
            statuses[node["id"]] = {"status": "investigating", "badge": "Investigating"}
        elif node["id"] == origin_id:
            statuses[node["id"]] = {"status": "critical", "badge": "Root cause"}
        elif node["layer"] is None:
            statuses[node["id"]] = {"status": "healthy", "badge": ""}
        elif node["earliest_error"] is not None:
            statuses[node["id"]] = {"status": "degraded", "badge": ""}
        elif node["has_warn"]:
            statuses[node["id"]] = {"status": "degraded", "badge": ""}
        else:
            statuses[node["id"]] = {"status": "healthy", "badge": ""}
    return statuses, path


def _node_positions(nodes: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """Layered left-to-right layout, expressed as percentages (0-100) so
    it can be paired with a preserveAspectRatio="none" SVG overlay for
    the connecting lines and scale with any container size."""
    layers: dict[int, list[dict[str, Any]]] = {}
    max_layer = max((n["layer"] for n in nodes if n["layer"] is not None), default=0)
    for node in nodes:
        layer = node["layer"] if node["layer"] is not None else max_layer + 1
        layers.setdefault(layer, []).append(node)
    num_layers = max(layers) + 1 if layers else 1

    positions: dict[str, tuple[float, float]] = {}
    for layer, layer_nodes in sorted(layers.items()):
        x = 50.0 if num_layers <= 1 else 6 + layer * (88 / (num_layers - 1))
        count = len(layer_nodes)
        layer_nodes = sorted(layer_nodes, key=lambda n: (not n["root"], n["id"]))
        for index, node in enumerate(layer_nodes):
            y = 50.0 if count <= 1 else 12 + (index + 0.5) * (76 / count)
            positions[node["id"]] = (x, y)
    return positions


def render_system_map(alert: Alert) -> None:
    data = build_system_map(alert)
    positions = _node_positions(data["nodes"])
    statuses, active_path = _classify_health(data)
    active_pairs = {tuple(sorted(pair)) for pair in zip(active_path, active_path[1:])}
    by_id = {node["id"]: node for node in data["nodes"]}
    layer_counts: dict[int, int] = {}
    for node in data["nodes"]:
        layer_counts[node["layer"] if node["layer"] is not None else -1] = (
            layer_counts.get(node["layer"] if node["layer"] is not None else -1, 0) + 1
        )
    max_per_layer = max(layer_counts.values(), default=1)
    height_px = max(620, min(900, 40 + max_per_layer * 95))

    svg_edges = []
    for edge in data["edges"]:
        source_id, target_id = edge["source"], edge["target"]
        sx, sy = positions[source_id]
        tx, ty = positions[target_id]
        active = tuple(sorted((source_id, target_id))) in active_pairs
        delta = edge["delta"]
        delta_text = f"{delta['earlier']} appeared {delta['label']} before {delta['later']}" if delta else "Dependency relationship"
        mx = (sx + tx) / 2
        svg_edges.append(
            f'<path class="edge {"active" if active else ""}" '
            f'd="M{sx} {sy} C {mx} {sy}, {mx} {ty}, {tx} {ty}" '
            f'data-tooltip="{html.escape(delta_text, quote=True)}" />'
        )

    origin_id = active_path[-1] if len(active_path) > 1 else None
    svg_nodes = []
    for node in data["nodes"]:
        x, y = positions[node["id"]]
        meta = statuses[node["id"]]
        evidence = "<br>".join(html.escape(log) for log in node["logs"]) or "No same-day log evidence"
        details = f"{node['id']} · {node['name']}<br>Type: {node['type']}<br>Owner: {node['owner']}<br>{evidence}"
        badge_html = f'<em>{html.escape(meta["badge"])}</em>' if meta["badge"] else ""
        svg_nodes.append(
            f'<article class="graph-node {meta["status"]}" '
            f'style="left:{x}%;top:{y}%" data-tooltip="{html.escape(details, quote=True)}">'
            f'<span class="node-status"></span>'
            f'<div><strong>{html.escape(node["name"])}</strong>'
            f'<small>{html.escape(node["type"])}</small></div>'
            f'{badge_html}</article>'
        )

    if origin_id and origin_id in by_id:
        path_label = f'{html.escape(data["alert"]["service"])} → {html.escape(by_id[origin_id]["name"])}'
    else:
        path_label = f'Investigating {html.escape(data["alert"]["service"])}'

    document = f"""
    <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing: border-box; }}
      body {{ margin:0; background:#050505; color:#f5f5f2; font-family:"Manrope",sans-serif; }}
      :root {{
        --bg:#050505; --panel:#0c0c0c; --border:#252525; --muted:#8e8e8e; --accent:#d9ff62;
        --low:#7ee2a8; --high:#f19a66; --critical:#ff6b6b;
      }}
      .graph {{
        position:relative; height:{height_px}px; overflow:hidden; border:1px solid var(--border);
        border-radius:14px; background-image:radial-gradient(#202020 .7px, transparent .7px);
        background-size:22px 22px; background-color:#070707;
      }}
      .graph::after {{
        content:""; position:absolute; inset:0; pointer-events:none;
        background:radial-gradient(circle at center, transparent 30%, #050505 100%);
      }}
      .graph-lines {{ position:absolute; inset:0; width:100%; height:100%; z-index:1; }}
      .edge {{ fill:none; stroke:#393939; stroke-width:1.2; vector-effect:non-scaling-stroke; }}
      .edge.active {{ stroke:var(--accent); stroke-dasharray:5 5; animation:dash 10s linear infinite; }}
      @keyframes dash {{ to {{ stroke-dashoffset:-100; }} }}
      .graph-node {{
        position:absolute; z-index:3; display:flex; width:180px; min-height:68px;
        align-items:center; gap:11px; padding:13px 14px; border:1px solid #333; border-radius:10px;
        background:#0d0d0d; box-shadow:0 10px 25px rgba(0,0,0,.32); transform:translateY(-50%);
        transition:box-shadow 160ms ease;
      }}
      .graph-node .node-status {{ width:8px; height:8px; flex:none; border-radius:50%; background:var(--low); }}
      .graph-node > div {{ display:flex; min-width:0; flex-direction:column; gap:5px; }}
      .graph-node strong {{
        overflow:hidden; font-family:"DM Mono",monospace; font-size:11px; font-weight:500; text-overflow:ellipsis;
      }}
      .graph-node small {{ color:#717171; font-size:9px; }}
      .graph-node em {{
        position:absolute; top:-9px; right:9px; padding:3px 6px; border-radius:4px; color:#111;
        background:var(--accent); font-family:"DM Mono",monospace; font-size:7px; font-style:normal;
        font-weight:600; letter-spacing:.04em; text-transform:uppercase;
      }}
      .graph-node.degraded .node-status {{ background:var(--high); }}
      .graph-node.critical {{ border-color:rgba(255,107,107,.7); box-shadow:0 0 25px rgba(255,107,107,.08); }}
      .graph-node.critical .node-status {{ background:var(--critical); }}
      .graph-node.critical em {{ color:#fff; background:var(--critical); }}
      .graph-node.investigating {{ border-color:var(--accent); box-shadow:0 0 28px rgba(217,255,98,.10); }}
      .graph-node.investigating .node-status {{ background:var(--accent); }}
      .graph-node:hover {{ box-shadow:0 0 0 1px #555; }}
      .legend, .graph-note {{
        position:absolute; z-index:4; border:1px solid var(--border); border-radius:10px;
        background:rgba(12,12,12,.92); backdrop-filter:blur(8px);
      }}
      .legend {{ right:18px; bottom:18px; display:grid; min-width:155px; gap:10px; padding:15px; }}
      .legend strong {{
        margin-bottom:3px; color:#777; font-family:"DM Mono",monospace; font-size:8px; font-weight:500;
        letter-spacing:.08em; text-transform:uppercase;
      }}
      .legend span {{ display:flex; align-items:center; gap:8px; color:#aaa; font-size:10px; }}
      .legend i {{ width:6px; height:6px; border-radius:50%; background:var(--low); }}
      .legend i.degraded {{ background:var(--high); }}
      .legend i.critical {{ background:var(--critical); }}
      .legend i.investigating {{ background:var(--accent); }}
      .graph-note {{
        bottom:18px; left:18px; display:flex; align-items:center; gap:8px; padding:11px 13px;
        color:var(--accent); font-family:"DM Mono",monospace; font-size:9px; max-width:calc(100% - 200px);
      }}
      .graph-note span {{ margin-left:10px; color:#777; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .tooltip {{
        position:fixed; display:none; max-width:360px; padding:12px 14px; background:rgba(12,12,12,.95);
        border:1px solid #353535; border-radius:10px; color:#f5f5f2; font:11px "DM Mono",monospace;
        line-height:1.55; pointer-events:none; z-index:10; backdrop-filter:blur(8px);
        box-shadow:0 8px 24px rgba(0,0,0,.4);
      }}
      @media (max-width:720px) {{
        .graph-node {{ width:145px; }}
        .graph-note span {{ display:none; }}
      }}
    </style>
    <div class="graph">
      <svg class="graph-lines" viewBox="0 0 100 100" preserveAspectRatio="none">{''.join(svg_edges)}</svg>
      {''.join(svg_nodes)}
      <div class="graph-note">⟡ Agent path<span>{path_label}</span></div>
      <div class="legend">
        <strong>Component health</strong>
        <span><i></i> Healthy</span>
        <span><i class="degraded"></i> Degraded</span>
        <span><i class="critical"></i> Critical</span>
        <span><i class="investigating"></i> Investigating</span>
      </div>
      <div class="tooltip" id="tooltip"></div>
    </div>
    <script>
      const tooltip = document.getElementById('tooltip');
      document.querySelectorAll('[data-tooltip]').forEach((item) => {{
        item.addEventListener('mousemove', (event) => {{ tooltip.innerHTML = item.dataset.tooltip; tooltip.style.display = 'block'; tooltip.style.left = `${{event.clientX + 14}}px`; tooltip.style.top = `${{event.clientY + 14}}px`; }});
        item.addEventListener('mouseleave', () => {{ tooltip.style.display = 'none'; }});
      }});
    </script>
    """
    components.html(document, height=height_px + 20, scrolling=False)
