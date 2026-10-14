"""
Rootly Streamlit UI.

    streamlit run src/ui/streamlit_app.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from dotenv import load_dotenv

from src.agent import DiagnosisError, resume_diagnosis, run_diagnosis
from src.agent.escalation_policy import HUMAN_APPROVE, HUMAN_DOWNGRADE
from src.agent.report import ESCALATION_LABEL, HUMAN_DECISION_LABEL, diagnosis_dict, to_markdown
from src.data_loader import load_alerts, load_incidents
from src.ui.system_map import render_system_map

load_dotenv(ROOT / ".env")
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "rootly-mark.png"
ARCHITECTURE_PATH = ROOT / "docs" / "diagrams" / "architecture.png"
st.set_page_config(
    page_title="Rootly | Incident Triage",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)

SEVERITY_COLOR = {"low": "#7ee2a8", "medium": "#f2c66d", "high": "#f19a66", "critical": "#ff6b6b"}
ESCALATION_COLOR = {"auto_resolved": "#7ee2a8", "escalate_normal": "#f2c66d", "escalate_urgent_needs_approval": "#ff6b6b"}
alerts = {a.id: a for a in load_alerts()}
incidents = {i.id: i for i in load_incidents()}
results: dict = st.session_state.setdefault("results", {})
errors: dict = st.session_state.setdefault("errors", {})
alert_statuses: dict[str, str] = st.session_state.setdefault(
    "alert_statuses", {alert_id: "unresolved" for alert_id in alerts}
)
view = st.session_state.setdefault("view", "diagnosis")
if view not in {"diagnosis", "system_map"}:
    view = "diagnosis"
    st.session_state.view = view
st.session_state.setdefault("selected_alert", None)

APP_CSS = dedent("""
    <style>
        :root {
            --black:#050505; --panel:#0c0c0c; --raised:#121212; --line:#252525;
            --muted:#8e8e8e; --white:#f5f5f2; --accent:#d9ff62;
            --alert-unresolved:#f5f5f2; --alert-resolved:#7ee2a8; --alert-urgent:#ff6b6b;
        }
        .stApp { background:var(--black); color:var(--white); }
        [data-testid="stHeader"] { background:transparent; }
        [data-testid="stSidebar"] { background:#090909; border-right:1px solid var(--line); }
        [data-testid="stSidebar"] > div:first-child { padding:2.4rem 1.25rem; }
        [data-testid="stSidebar"] [data-testid="stSidebarContent"] { overflow-y:auto; }
        .block-container { max-width:1280px; padding:3rem 4rem 5rem; }
        h1,h2,h3,h4,p,label,[data-testid="stMetricLabel"],[data-testid="stMetricValue"] { font-family:Helvetica,Arial,sans-serif; }
        h1 { letter-spacing:-.04em; font-weight:500; } h2,h3 { letter-spacing:-.025em; font-weight:500; }
        p,li,label { color:#d0d0cd; line-height:1.6; } code,pre,[data-testid="stCode"] { font-family:Consolas,monospace; }
        .rootly-eyebrow { color:var(--accent); font:500 .7rem Consolas,monospace; letter-spacing:.12em; text-transform:uppercase; }
        .rootly-brand { display:flex; align-items:center; gap:.65rem; width:fit-content; margin:0 0 1.75rem; color:var(--white); font-size:1.05rem; font-weight:600; letter-spacing:-.03em; }
        .rootly-brand img { display:block; width:32px; height:32px; object-fit:contain; }
        .rootly-muted { color:var(--muted); }
        .rootly-hero { animation:rootly-fade 900ms ease both; padding:12vh 0 8vh; max-width:820px; }
        .rootly-hero h1 { font-size:clamp(4rem,11vw,9rem); line-height:.9; margin:1rem 0 2rem; }
        .rootly-hero p { font-size:1.25rem; line-height:1.55; max-width:590px; }
        .rootly-rule { border-top:1px solid var(--line); margin:2rem 0; }
        .rootly-section { animation:rootly-rise 500ms ease both; }
        .rootly-about-card,.rootly-alert-card { border:1px solid var(--line); background:var(--panel); padding:1.5rem; border-radius:2px; }
        .rootly-about-card p { line-height:1.7; }
        .rootly-alert-dot {
            display:block; width:14px; height:14px; flex:0 0 14px; margin:0;
            border:1px solid var(--alert-unresolved); border-radius:50%; background:var(--alert-unresolved);
        }
        .rootly-alert-dot.resolved { border-color:var(--alert-resolved); background:var(--alert-resolved); }
        .rootly-alert-dot.urgent { border-color:var(--alert-urgent); background:var(--alert-urgent); }
        .st-key-triage-panel {
            padding:1.65rem; border:1px solid #252a20; border-radius:16px;
            background:linear-gradient(145deg,rgba(16,18,13,.96),rgba(9,9,9,.98));
            box-shadow:0 16px 42px rgba(0,0,0,.18);
        }
        .rootly-triage-heading {
            padding-bottom:.8rem; border-bottom:1px solid rgba(255,255,255,.07);
            margin-bottom:1.4rem;
        }
        .rootly-triage-heading .rootly-eyebrow { letter-spacing:.16em; }
        .st-key-alert-bar [data-testid="stHorizontalBlock"] {
            display:grid !important; grid-template-columns:repeat(10,minmax(0,1fr)); gap:.65rem;
            width:100%; align-items:stretch;
        }
        .st-key-alert-bar [data-testid="stColumn"] {
            width:auto !important; min-width:0 !important; flex:none !important;
        }
        .st-key-alert-bar [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
            display:flex; flex-direction:row; align-items:center; gap:.5rem; min-width:0;
            min-height:54px; padding:0 .45rem; border:1px solid #30352a; border-radius:12px;
            background:#11130f; transition:background 180ms ease,border-color 180ms ease,box-shadow 180ms ease,transform 180ms ease;
        }
        .st-key-alert-bar [data-testid="stColumn"] > [data-testid="stVerticalBlock"]:hover {
            transform:translateY(-2px); border-color:rgba(217,255,98,.48);
            background:#15190d; box-shadow:0 8px 20px rgba(217,255,98,.1);
        }
        .st-key-alert-bar [data-testid="stColumn"] > [data-testid="stVerticalBlock"]:has(button[kind="primary"]) {
            border-color:var(--accent); background:rgba(217,255,98,.12);
            box-shadow:0 0 0 1px rgba(217,255,98,.18),0 5px 18px rgba(217,255,98,.1);
        }
        .st-key-alert-bar [data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:first-child {
            width:auto !important; flex:0 0 auto !important;
        }
        .st-key-alert-bar [class*="st-key-alert-top-"] {
            width:auto !important; min-width:0 !important; flex:1 1 auto !important;
        }
        .st-key-alert-bar [class*="st-key-alert-top-"] button {
            width:100% !important; min-width:0 !important; min-height:50px !important; padding:0 !important;
            justify-content:flex-start !important; border:0 !important;
            border-radius:0 !important; background:transparent !important; color:var(--white) !important;
            box-shadow:none !important; font-weight:600 !important; transform:none !important;
            transition:background 180ms ease,border-color 180ms ease,box-shadow 180ms ease,transform 180ms ease;
            white-space:nowrap !important; font-size:.8rem !important; letter-spacing:.02em !important;
        }
        .st-key-alert-bar [class*="st-key-alert-top-"] button p {
            margin:0 !important; font-size:.8rem !important; line-height:1.1 !important;
        }
        .st-key-alert-bar [class*="st-key-alert-top-"] button:hover {
            transform:none !important; border:0 !important; background:transparent !important; box-shadow:none !important;
        }
        .st-key-alert-bar [class*="st-key-alert-top-"] button[kind="primary"] {
            border:0 !important; background:transparent !important; box-shadow:none !important;
            color:var(--white) !important;
        }
        .rootly-alert-legend {
            display:flex; align-items:center; gap:1.8rem; margin:0;
            color:var(--muted); font-size:.75rem; line-height:1.2; white-space:nowrap;
        }
        .rootly-alert-legend-item { display:inline-flex; align-items:center; gap:.4rem; }
        .rootly-alert-legend .rootly-alert-dot { width:12px; height:12px; flex-basis:12px; }
        .st-key-diagnosis-run { width:66%; margin:0 auto; padding-top:1.75rem; }
        .st-key-diagnosis-run [data-testid="stButton"] button {
            width:100% !important; min-height:58px !important; border:1px solid var(--accent) !important;
            border-radius:12px !important; background:var(--accent) !important; color:#050505 !important;
            font-size:1.05rem !important; font-weight:700 !important; letter-spacing:.01em !important;
            box-shadow:0 6px 20px rgba(217,255,98,.12) !important;
            transition:background 180ms ease,border-color 180ms ease,box-shadow 180ms ease,transform 180ms ease;
        }
        .st-key-diagnosis-run [data-testid="stButton"] button p {
            margin:0 !important; color:#050505 !important; font-size:1.05rem !important; font-weight:700 !important;
        }
        .st-key-diagnosis-run [data-testid="stButton"] button:hover {
            transform:translateY(-2px); background:#e4ff88 !important;
            box-shadow:0 10px 26px rgba(217,255,98,.22) !important;
        }
        .st-key-diagnosis-run [data-testid="stButton"] button:active {
            transform:translateY(0); box-shadow:0 3px 10px rgba(217,255,98,.14) !important;
        }
        .st-key-diagnosis-run [data-testid="stButton"] button:disabled {
            opacity:.42; cursor:not-allowed; box-shadow:none !important; transform:none;
        }
        .rootly-sidebar-category {
            margin:1.25rem 0 .5rem; color:var(--accent);
            font:500 .68rem Consolas,monospace; letter-spacing:.1em; text-transform:uppercase;
        }
        .rootly-sidebar-list { display:flex; flex-direction:column; gap:.6rem; }
        .rootly-sidebar-item { display:flex; flex-direction:column; gap:.1rem; }
        .rootly-sidebar-item strong { color:#deded9; font-size:.76rem; font-weight:600; line-height:1.25; }
        .rootly-sidebar-item span { color:var(--muted); font-size:.68rem; line-height:1.35; }
        .st-key-sidebar-diagnosis button,.st-key-sidebar-system-map button {
            width:100%; min-height:2.2rem; padding:.45rem .65rem; border-radius:8px;
            justify-content:flex-start; font-size:.76rem; font-weight:600;
        }
        .st-key-sidebar-diagnosis button[kind="primary"],.st-key-sidebar-system-map button[kind="primary"] {
            background:rgba(217,255,98,.1); border-color:rgba(217,255,98,.48); color:var(--accent) !important;
        }
        .rootly-sidebar-view-description { margin:.1rem 0 .65rem; color:var(--muted); font-size:.68rem; line-height:1.35; }
        .rootly-system-architecture { margin:1.2rem 0 2rem; padding:1rem; border:1px solid var(--line); border-radius:12px; background:#090909; }
        .rootly-system-architecture img { border-radius:6px; }
        .rootly-kicker { color:var(--muted); font:500 .72rem Consolas,monospace; letter-spacing:.08em; text-transform:uppercase; }
        .stButton > button,.stDownloadButton > button { border-radius:2px; border:1px solid #3b3b3b; background:transparent; color:var(--white); transition:border-color 180ms ease,background 180ms ease,transform 180ms ease; }
        .stButton > button:hover,.stDownloadButton > button:hover { border-color:var(--accent); background:#171b0d; color:var(--white); transform:translateY(-1px); }
        .stButton > button[kind="primary"] { background:var(--accent); border-color:var(--accent); color:#000 !important; font-weight:600; }
        .stButton > button[kind="primary"] p,.stButton > button[kind="primary"] span,
        .stButton > button[kind="primary"]:hover,.stButton > button[kind="primary"]:hover p,.stButton > button[kind="primary"]:hover span { color:#000 !important; }
        .st-key-alert-bar [class*="st-key-alert-top-"] button[kind="primary"] p,
        .st-key-alert-bar [class*="st-key-alert-top-"] button[kind="primary"] span,
        .st-key-alert-bar [class*="st-key-alert-top-"] button[kind="primary"]:hover p,
        .st-key-alert-bar [class*="st-key-alert-top-"] button[kind="primary"]:hover span { color:var(--white) !important; }
        [data-testid="stMetric"] { background:var(--panel); border:1px solid var(--line); padding:1rem; }
        [data-testid="stMetricValue"] { color:var(--white); }
        [data-testid="stExpander"],[data-testid="stForm"] { border-color:var(--line); background:var(--panel); }
        [data-testid="stTabs"] button { color:var(--muted); } [data-testid="stTabs"] button[aria-selected="true"] { color:var(--accent); }
        @media (max-width:1200px) {
            .st-key-alert-bar [data-testid="stHorizontalBlock"] { grid-template-columns:repeat(5,minmax(0,1fr)); }
        }
        @media (max-width:520px) {
            .block-container { padding:1.5rem 1rem 3rem; }
            .st-key-triage-panel { padding:1rem; border-radius:14px; }
            .st-key-alert-bar [data-testid="stHorizontalBlock"] { grid-template-columns:repeat(2,minmax(0,1fr)); gap:.55rem; }
            .st-key-alert-bar [data-testid="stColumn"] > [data-testid="stVerticalBlock"] { gap:.5rem; padding:0 .65rem; }
            .st-key-diagnosis-run { width:100%; }
            .rootly-alert-legend { gap:1rem; font-size:.7rem; }
        }
        @keyframes rootly-fade { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:translateY(0); } }
        @keyframes rootly-rise { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
    </style>
""")
st.markdown(APP_CSS, unsafe_allow_html=True)

logo_data = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
st.markdown(
    f'<div class="rootly-brand" aria-label="Rootly"><img src="data:image/png;base64,{logo_data}" alt=""><span>Rootly</span></div>',
    unsafe_allow_html=True,
)


def get_alert_status(alert_id: str) -> str:
    return alert_statuses.get(alert_id, "unresolved")


def render_landing() -> None:
    st.markdown(
        '<div class="rootly-hero"><div class="rootly-eyebrow">AI incident triage / ReAct orchestration</div><h1>Rootly</h1><p>A smart diagnosis orchestrator for IT incidents, correlating CMDB context, log evidence, and historical incidents into a clear path to resolution.</p></div>',
        unsafe_allow_html=True,
    )
    launch, about, system_map = st.columns([1, 1, 1])
    if launch.button("Launch Rootly", type="primary", width="stretch"):
        st.session_state.view = "dashboard"
        st.rerun()
    if about.button("About Rootly", width="stretch"):
        st.session_state.view = "about"
        st.rerun()
    if system_map.button("System Map", width="stretch"):
        st.session_state.view = "system_map"
        st.rerun()


def render_about() -> None:
    st.markdown('<div class="rootly-eyebrow">About the orchestrator</div>', unsafe_allow_html=True)
    st.title("Diagnosis, assembled with context.")
    st.markdown(
        '<div class="rootly-about-card"><h2>Rootly is an AI Diagnosis Orchestrator for IT incident triage.</h2><p>It follows a ReAct-style reasoning loop to automate initial triage and preliminary diagnosis. The agent investigates simulated alerts through CMDB lookup, log search, and optional historical-incident retrieval.</p><p>Historical incidents can use ChromaDB with a BM25 keyword fallback. The result is a structured diagnosis package containing severity, affected components, critical dependencies, log evidence, a root-cause hypothesis, confidence, and an escalation recommendation.</p><p>Urgent escalations pause for human-in-the-loop approval before the run can continue. The MVP does not perform automated remediation, connect to real production systems, create real escalations, or provide multi-tenant support.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Back to launch", width="content"):
        st.session_state.view = "landing"
        st.rerun()


def render_system_map_view() -> None:
    if st.button("Back to diagnosis", key="system-map-back"):
        st.session_state.view = "diagnosis"
        st.rerun()

    st.markdown('<div class="rootly-eyebrow">Operational topology</div>', unsafe_allow_html=True)
    st.title("System Map")
    selected = st.session_state.selected_alert or next(iter(alerts))
    if ARCHITECTURE_PATH.is_file():
        st.markdown('<div class="rootly-system-architecture">', unsafe_allow_html=True)
        st.image(str(ARCHITECTURE_PATH), width="stretch")
        st.caption("Rootly system architecture: investigation orchestration, tools, and observability.")
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("The architecture diagram is not available yet.")
    st.caption("Live dependency map for the selected alert scenario, built from CMDB and same-day log evidence.")
    render_system_map(alerts[selected])


def render_event(event: dict) -> None:
    kind = event["kind"]
    if kind == "thought":
        st.markdown(f"**Thought**  \n{event['content']}")
    elif kind == "routing":
        st.markdown(f"**Handoff** → `{event['tool']}` agent  \n{event['content']}")
    elif kind == "action":
        args = ", ".join(f"{k}={v!r}" for k, v in event["input"].items())
        st.code(f"{event['tool']}({args})", language="python")
    elif kind == "observation":
        label = f"Observation / {event['tool']} / {event.get('duration_ms', 0):.0f} ms"
        with st.expander(label, expanded=False):
            st.json(event["result"], expanded=False)
    elif kind == "guardrail":
        (st.error if event.get("is_error") else st.success)(f"Guardrail: {event['content']}")
    elif kind == "escalation":
        st.info(f"Escalation policy: {event['content']}")
    elif kind == "human_decision":
        st.success(f"Human decision: {event['content']}")
    elif kind == "note":
        st.warning(event["content"])


def step_title(step: int, events: list[dict]) -> str:
    tools = [f"→ {e['tool']} agent" for e in events if e["kind"] == "routing"]
    tools += [e["tool"] for e in events if e["kind"] == "action"]
    if any(e["kind"] == "guardrail" for e in events):
        tools.append("submit_diagnosis")
    if any(e["kind"] == "escalation" for e in events):
        tools.append("escalation_policy")
    if any(e["kind"] == "human_decision" for e in events):
        tools.append("human_approval")
    return f"Step {step}: " + (" → ".join(tools) if tools else "reasoning")


# ── Sidebar navigation ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="rootly-eyebrow">Rootly / workspace</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="rootly-sidebar-category">Investigation</div>
        <div class="rootly-sidebar-list">
          <div class="rootly-sidebar-item"><strong>Alert intake</strong><span>Loads one of 10 mock alert scenarios</span></div>
          <div class="rootly-sidebar-item"><strong>CMDB lookup</strong><span>Component details, dependency chain, blast radius, owner team</span></div>
          <div class="rootly-sidebar-item"><strong>Log search</strong><span>Filters logs by service, time window, and level</span></div>
          <div class="rootly-sidebar-item"><strong>Similar incidents (RAG)</strong><span>ChromaDB with BM25 fallback over past incidents</span></div>
        </div>
        <div class="rootly-sidebar-category">Multi-agent orchestration</div>
        <div class="rootly-sidebar-list">
          <div class="rootly-sidebar-item"><strong>Orchestrator</strong><span>Routes the investigation to specialists</span></div>
          <div class="rootly-sidebar-item"><strong>CMDB / Log / Synthesis agents</strong><span>Each owns one part of the toolset</span></div>
        </div>
        <div class="rootly-sidebar-category">Safety &amp; decisions</div>
        <div class="rootly-sidebar-list">
          <div class="rootly-sidebar-item"><strong>Guardrail</strong><span>Validates components, incidents, and log evidence against real data</span></div>
          <div class="rootly-sidebar-item"><strong>Escalation policy</strong><span>Auto-resolved / normal / urgent</span></div>
          <div class="rootly-sidebar-item"><strong>Human approval</strong><span>Approve or downgrade urgent escalations</span></div>
        </div>
        <div class="rootly-sidebar-category">Output</div>
        <div class="rootly-sidebar-list">
          <div class="rootly-sidebar-item"><strong>Diagnosis package</strong><span>Root cause, evidence, confidence, escalation recommendation</span></div>
          <div class="rootly-sidebar-item"><strong>Reasoning trace</strong><span>Thought → Action → Observation steps</span></div>
          <div class="rootly-sidebar-item"><strong>Export</strong><span>JSON and Markdown</span></div>
        </div>
        <div class="rootly-sidebar-category">Observability &amp; quality</div>
        <div class="rootly-sidebar-list">
          <div class="rootly-sidebar-item"><strong>Phoenix tracing</strong><span>Agent tracing and observability</span></div>
          <div class="rootly-sidebar-item"><strong>Statistical &amp; RAG evaluation</strong><span>Evaluation with RAGAS</span></div>
        </div>
        <div class="rootly-sidebar-category">Views</div>
        """,
        unsafe_allow_html=True,
    )
    if st.button(
        "Diagnosis",
        key="sidebar-diagnosis",
        type="primary" if view == "diagnosis" else "secondary",
        use_container_width=True,
    ):
        st.session_state.view = "diagnosis"
        st.rerun()
    st.markdown('<div class="rootly-sidebar-view-description">Default incident triage workspace</div>', unsafe_allow_html=True)
    if st.button(
        "System map",
        key="sidebar-system-map",
        type="primary" if view == "system_map" else "secondary",
        use_container_width=True,
    ):
        st.session_state.view = "system_map"
        st.rerun()
    st.markdown('<div class="rootly-sidebar-view-description">Architecture and live CMDB dependencies</div>', unsafe_allow_html=True)

if view == "system_map":
    render_system_map_view()
    st.stop()

selected = st.session_state.selected_alert

# ── Header & alert card ──────────────────────────────────────────────────
with st.container(key="triage-panel"):
    st.markdown(
        '<div class="rootly-triage-heading"><div class="rootly-eyebrow">Incident triage</div></div>',
        unsafe_allow_html=True,
    )
    alert_strip = st.container(key="alert-bar")
    with alert_strip:
        alert_cols = st.columns(len(alerts))
        for index, (alert_id, item) in enumerate(alerts.items()):
            with alert_cols[index]:
                status = get_alert_status(alert_id)
                button_type = "primary" if alert_id == selected else "secondary"
                status_label = {
                    "unresolved": "Unresolved",
                    "resolved": "Resolved",
                    "urgent": "Urgent",
                }[status]
                st.markdown(
                    f'<span class="rootly-alert-dot {status}" title="{status_label}" '
                    f'aria-label="{status_label}" role="img"></span>',
                    unsafe_allow_html=True,
                )
                if st.button(f"{alert_id}", key=f"alert-top-{alert_id}", type=button_type, use_container_width=True):
                    st.session_state.selected_alert = alert_id
                    st.rerun()

    st.markdown(
        '<div class="rootly-alert-legend">'
        '<span class="rootly-alert-legend-item"><span class="rootly-alert-dot unresolved" '
        'title="Unresolved" aria-label="Unresolved" role="img"></span><span>= unresolved</span></span>'
        '<span class="rootly-alert-legend-item"><span class="rootly-alert-dot resolved" '
        'title="Resolved" aria-label="Resolved" role="img"></span><span>= resolved</span></span>'
        '<span class="rootly-alert-legend-item"><span class="rootly-alert-dot urgent" '
        'title="Urgent" aria-label="Urgent" role="img"></span><span>= urgent</span></span>'
        '</div>',
        unsafe_allow_html=True,
    )
    if selected:
        with st.container(key="diagnosis-run"):
            run_clicked = st.button("Run diagnosis", type="primary", width="stretch")

if not selected:
    st.markdown('<div class="rootly-section"><div class="rootly-eyebrow">Workspace ready</div>', unsafe_allow_html=True)
    st.title("Select an alert to begin.")
    st.write("Choose an incident from the alert list to open its context, run the orchestrator, and review the diagnosis package.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

alert = alerts[selected]

st.markdown('<div class="rootly-eyebrow">Active investigation</div>', unsafe_allow_html=True)
st.title(f"{alert.id} / {alert.service}")
st.caption("Thought / Action / Observation over CMDB, logs, and historical incidents, with human approval for urgent escalations.")

with st.container(border=True):
    st.subheader("Alert context")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alert type", alert.alert_type.value)
    c2.metric("Reported severity", alert.severity_reported.value)
    c3.metric("Metric value", f"{alert.metric_value:g}")
    c4.metric("Fired at (UTC)", alert.timestamp.strftime("%m-%d %H:%M"))
    st.write(alert.description)

# ── Run ──────────────────────────────────────────────────────────────────
if run_clicked:
    errors.pop(selected, None)
    st.subheader("Orchestrator trace")
    with st.status("Investigating…", expanded=True) as status:
        current_step = {"n": 0}

        def on_event(event: dict) -> None:
            if event["step"] != current_step["n"]:
                current_step["n"] = event["step"]
                st.markdown(f"#### Step {event['step']}")
                status.update(label=f"Investigating… step {event['step']}")
            render_event(event)

        try:
            diagnosis_result = run_diagnosis(alert, on_event=on_event)
            results[selected] = diagnosis_result
            escalation_decision = diagnosis_result.diagnosis.escalation_decision
            if diagnosis_result.needs_approval or (
                escalation_decision
                and escalation_decision.value == "escalate_urgent_needs_approval"
            ):
                alert_statuses[selected] = "urgent"
            elif escalation_decision and escalation_decision.value in {"auto_resolved", "escalate_normal"}:
                alert_statuses[selected] = "resolved"
            else:
                alert_statuses[selected] = "unresolved"
            status.update(label="Diagnosis complete", state="complete", expanded=False)
        except DiagnosisError as exc:
            errors[selected] = f"Diagnosis failed: {exc}"
        if selected in errors:
            status.update(label="Diagnosis failed", state="error", expanded=True)
    st.rerun()

if selected in errors:
    st.error(errors[selected])

result = results.get(selected)

if not result:
    st.stop()

d = result.diagnosis

# Export stays above the operational detail so a completed package is immediately actionable.
st.markdown('<div class="rootly-section">', unsafe_allow_html=True)
st.subheader("Export your data")
export_json, export_markdown, export_trace = st.columns(3)
export_json.download_button("Diagnosis JSON", json.dumps(diagnosis_dict(result), indent=2, ensure_ascii=False),
                            file_name=f"{selected}_diagnosis.json", mime="application/json", width="stretch")
export_markdown.download_button("Diagnosis Markdown", to_markdown(result),
                                 file_name=f"{selected}_diagnosis.md", mime="text/markdown", width="stretch")
export_trace.download_button("Trace JSON", json.dumps(result.trace, indent=2, ensure_ascii=False),
                              file_name=f"{selected}_trace.json", mime="application/json", width="stretch")
st.markdown('</div>', unsafe_allow_html=True)

# ── Human approval ───────────────────────────────────────────────────────
if result.needs_approval:
    request = result.approval_request or {}
    with st.container(border=True):
        st.subheader("Human approval required")
        st.write(f"Urgent escalation of **{request.get('component')}** to **{request.get('owner_team')}**.")
        st.warning(request.get("reason"))
        note = st.text_input("Note for the audit trail (optional)", key=f"note-{result.thread_id}")
        approve_col, downgrade_col = st.columns(2)
        decision = None
        if approve_col.button("Approve urgent escalation", type="primary", width="stretch"):
            decision = HUMAN_APPROVE
        if downgrade_col.button("Downgrade / not urgent", width="stretch"):
            decision = HUMAN_DOWNGRADE
        if decision:
            try:
                results[selected] = resume_diagnosis(result.thread_id, decision, note=note)
            except DiagnosisError as exc:
                errors[selected] = f"Resume failed: {exc}"
            st.rerun()

# ── Trace ────────────────────────────────────────────────────────────────
st.subheader("Orchestrator trace")
by_step: dict[int, list[dict]] = {}
for event in result.trace:
    by_step.setdefault(event["step"], []).append(event)
for step, events in by_step.items():
    with st.expander(step_title(step, events), expanded=False):
        for event in events:
            render_event(event)

# ── Diagnosis package ────────────────────────────────────────────────────
st.subheader("Diagnosis package")
with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    c1.metric("Affected component", d.affected_component)
    c2.metric("Assessed severity", d.severity_assessed.value)
    c3.metric("Confidence", f"{d.confidence:.0%}")
    st.progress(d.confidence)

    st.markdown("**Summary**")
    st.write(d.summary)
    st.markdown("**Root cause hypothesis**")
    st.write(d.root_cause_hypothesis)
    st.markdown("**Escalation recommendation**")
    st.info(d.escalation_recommendation)

    if d.escalation_decision:
        decision_value = d.escalation_decision.value
        st.markdown("**Escalation decision**")
        st.markdown(f'<span style="color:{ESCALATION_COLOR[decision_value]}">{ESCALATION_LABEL[decision_value]}</span> · owner team `{d.owner_team}`', unsafe_allow_html=True)
        st.caption(d.escalation_reason)
        if d.human_decision:
            st.write(f"Human {HUMAN_DECISION_LABEL.get(d.human_decision, d.human_decision)}"
                     + (f" — _{d.human_decision_note}_" if d.human_decision_note else ""))

    left, right = st.columns(2)
    with left:
        st.markdown("**Critical dependencies**")
        st.write(" ".join(f"`{dep}`" for dep in d.critical_dependencies) or "—")
        st.markdown("**Similar historical incidents**")
        for incident_id in d.similar_incidents:
            inc = incidents[incident_id]
            st.markdown(f"- **{inc.id}** ({inc.duration_minutes} min) — {inc.description}")
        if not d.similar_incidents:
            st.write("—")
    with right:
        st.markdown("**Log evidence**")
        st.code("\n".join(d.log_evidence), language="log")

tab_json, tab_md = st.tabs(["JSON", "Markdown"])
with tab_json:
    st.json(diagnosis_dict(result))
with tab_md:
    st.markdown(to_markdown(result))

with st.expander("Evaluation ground truth (hidden from the agent)"):
    st.write(f"Root-cause category: `{alert.root_cause_category.value if alert.root_cause_category else '—'}`")
    st.write(f"Failure mode: `{alert.failure_mode.value if alert.failure_mode else '—'}`")
