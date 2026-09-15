"""
Rootly Streamlit UI.

    streamlit run src/ui/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
st.set_page_config(page_title="Rootly | Incident Triage", layout="wide", initial_sidebar_state="expanded")

SEVERITY_COLOR = {"low": "#7ee2a8", "medium": "#f2c66d", "high": "#f19a66", "critical": "#ff6b6b"}
ESCALATION_COLOR = {"auto_resolved": "#7ee2a8", "escalate_normal": "#f2c66d", "escalate_urgent_needs_approval": "#ff6b6b"}
alerts = {a.id: a for a in load_alerts()}
incidents = {i.id: i for i in load_incidents()}
results: dict = st.session_state.setdefault("results", {})
errors: dict = st.session_state.setdefault("errors", {})
view = st.session_state.setdefault("view", "landing")
st.session_state.setdefault("selected_alert", None)

st.markdown(
    """
    <style>
        :root { --black:#050505; --panel:#0c0c0c; --raised:#121212; --line:#252525; --muted:#8e8e8e; --white:#f5f5f2; --accent:#d9ff62; }
        .stApp { background:var(--black); color:var(--white); }
        [data-testid="stHeader"] { background:transparent; }
        [data-testid="stSidebar"] { background:#090909; border-right:1px solid var(--line); }
        [data-testid="stSidebar"] > div:first-child { padding:2.4rem 1.25rem; }
        .block-container { max-width:1280px; padding:3.5rem 4rem 5rem; }
        h1,h2,h3,h4,p,label,[data-testid="stMetricLabel"],[data-testid="stMetricValue"] { font-family:Helvetica,Arial,sans-serif; }
        h1 { letter-spacing:-.04em; font-weight:500; } h2,h3 { letter-spacing:-.025em; font-weight:500; }
        p,li,label { color:#d0d0cd; } code,pre,[data-testid="stCode"] { font-family:Consolas,monospace; }
        .rootly-eyebrow { color:var(--accent); font:500 .7rem Consolas,monospace; letter-spacing:.12em; text-transform:uppercase; }
        .rootly-muted { color:var(--muted); }
        .rootly-hero { animation:rootly-fade 900ms ease both; padding:12vh 0 8vh; max-width:820px; }
        .rootly-hero h1 { font-size:clamp(4rem,11vw,9rem); line-height:.9; margin:1rem 0 2rem; }
        .rootly-hero p { font-size:1.25rem; line-height:1.55; max-width:590px; }
        .rootly-rule { border-top:1px solid var(--line); margin:2rem 0; }
        .rootly-section { animation:rootly-rise 500ms ease both; }
        .rootly-about-card,.rootly-alert-card { border:1px solid var(--line); background:var(--panel); padding:1.5rem; border-radius:2px; }
        .rootly-about-card p { line-height:1.7; }
        .rootly-alert-dot { width:8px; height:8px; border-radius:50%; margin:.7rem auto 0; }
        .rootly-alert-dot.resolved { background:#7ee2a8; } .rootly-alert-dot.unresolved { background:#ff6b6b; }
        .rootly-alert-row { border:1px solid var(--line); border-radius:30px; padding:.2rem; }
        .rootly-alert-row + .rootly-alert-row { margin-top:.35rem; }
        [class*="st-key-alert-"] button { border-radius:30px !important; }
        .rootly-kicker { color:var(--muted); font:500 .72rem Consolas,monospace; letter-spacing:.08em; text-transform:uppercase; }
        .stButton > button,.stDownloadButton > button { border-radius:2px; border:1px solid #3b3b3b; background:transparent; color:var(--white); transition:border-color 180ms ease,background 180ms ease,transform 180ms ease; }
        .stButton > button:hover,.stDownloadButton > button:hover { border-color:var(--accent); background:#171b0d; color:var(--white); transform:translateY(-1px); }
        .stButton > button[kind="primary"] { background:var(--accent); border-color:var(--accent); color:#000 !important; font-weight:600; }
        .stButton > button[kind="primary"] p,.stButton > button[kind="primary"] span,
        .stButton > button[kind="primary"]:hover,.stButton > button[kind="primary"]:hover p,.stButton > button[kind="primary"]:hover span { color:#000 !important; }
        [data-testid="stMetric"] { background:var(--panel); border:1px solid var(--line); padding:1rem; }
        [data-testid="stMetricValue"] { color:var(--white); }
        [data-testid="stExpander"],[data-testid="stForm"] { border-color:var(--line); background:var(--panel); }
        [data-testid="stTabs"] button { color:var(--muted); } [data-testid="stTabs"] button[aria-selected="true"] { color:var(--accent); }
        @keyframes rootly-fade { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:translateY(0); } }
        @keyframes rootly-rise { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
    </style>
    """, unsafe_allow_html=True,
)


def is_resolved(alert_id: str) -> bool:
    # A diagnosis awaiting human approval is still unresolved; completed diagnoses are resolved.
    result = results.get(alert_id)
    return bool(result and result.diagnosis and not result.needs_approval)


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
    current = st.session_state.selected_alert or next(iter(alerts))
    with st.sidebar:
        st.markdown('<div class="rootly-eyebrow">Rootly / system map</div>', unsafe_allow_html=True)
        st.markdown("# System Map")
        selected = st.selectbox(
            "Alert scenario",
            list(alerts),
            index=list(alerts).index(current),
            format_func=lambda alert_id: f"{alert_id} / {alerts[alert_id].service}",
        )
        st.session_state.selected_alert = selected
        st.markdown('<div class="rootly-rule"></div>', unsafe_allow_html=True)
        st.caption("Map data comes from the CMDB, alerts, and logs.")
        if st.button("Back to launch", width="stretch"):
            st.session_state.view = "landing"
            st.rerun()
    st.markdown('<div class="rootly-eyebrow">Operational topology</div>', unsafe_allow_html=True)
    st.title("System Map")
    st.caption("A live view of dependency relationships and same-scenario log timing.")
    render_system_map(alerts[selected])


def render_event(event: dict) -> None:
    kind = event["kind"]
    if kind == "thought":
        st.markdown(f"**Thought**  \n{event['content']}")
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
    tools = [e["tool"] for e in events if e["kind"] == "action"]
    if any(e["kind"] == "guardrail" for e in events):
        tools.append("submit_diagnosis")
    if any(e["kind"] == "escalation" for e in events):
        tools.append("escalation_policy")
    if any(e["kind"] == "human_decision" for e in events):
        tools.append("human_approval")
    return f"Step {step}: " + (" → ".join(tools) if tools else "reasoning")


# ── View routing and sidebar ────────────────────────────────────────────
if view == "landing":
    render_landing()
    st.stop()
if view == "about":
    render_about()
    st.stop()
if view == "system_map":
    render_system_map_view()
    st.stop()

with st.sidebar:
    st.markdown('<div class="rootly-eyebrow">Rootly / workspace</div>', unsafe_allow_html=True)
    st.markdown("# Incident triage")
    if st.button("About Rootly", width="stretch"):
        st.session_state.view = "about"
        st.rerun()
    st.markdown('<div class="rootly-rule"></div>', unsafe_allow_html=True)
    st.markdown('<div class="rootly-kicker">Alerts</div>', unsafe_allow_html=True)
    st.caption("Red = unresolved, green = resolved")
    for alert_id, item in alerts.items():
        status = "resolved" if is_resolved(alert_id) else "unresolved"
        st.markdown('<div class="rootly-alert-row">', unsafe_allow_html=True)
        dot, alert_button = st.columns([0.12, 0.88], vertical_alignment="center")
        dot.markdown(f'<div class="rootly-alert-dot {status}"></div>', unsafe_allow_html=True)
        if alert_button.button(f"{alert_id} / {item.service}", key=f"alert-{alert_id}", width="stretch"):
            st.session_state.selected_alert = alert_id
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div class="rootly-rule"></div>', unsafe_allow_html=True)
    sidebar_status = st.container()

selected = st.session_state.selected_alert
if not selected:
    st.markdown('<div class="rootly-section"><div class="rootly-eyebrow">Workspace ready</div>', unsafe_allow_html=True)
    st.title("Select an alert to begin.")
    st.write("Choose an incident from the alert list to open its context, run the orchestrator, and review the diagnosis package.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

alert = alerts[selected]
run_clicked = st.button("Run diagnosis", type="primary", width="content")

# ── Header & alert card ──────────────────────────────────────────────────
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
            results[selected] = run_diagnosis(alert, on_event=on_event)
            status.update(label="Diagnosis complete", state="complete", expanded=False)
        except DiagnosisError as exc:
            errors[selected] = f"Diagnosis failed: {exc}"
        if selected in errors:
            status.update(label="Diagnosis failed", state="error", expanded=True)
    st.rerun()

if selected in errors:
    st.error(errors[selected])

result = results.get(selected)

with sidebar_status:
    if result and result.needs_approval:
        st.warning("Waiting for approval")
    elif result:
        st.success("Complete")
    if result:
        m1, m2 = st.columns(2)
        m1.metric("Steps", result.steps)
        m2.metric("Time", f"{result.elapsed_seconds:.0f}s")
        m3, m4 = st.columns(2)
        m3.metric("Tool calls", result.tool_calls)
        m4.metric("Confidence", f"{result.diagnosis.confidence:.2f}")
        st.caption(f"Thread: `{result.thread_id}`")
    else:
        st.info("Not run yet. Run the diagnosis when you are ready.")

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
