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

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="Rootly — AI Incident Triage", page_icon="🔍", layout="wide")

SEVERITY_ICON = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
ESCALATION_ICON = {"auto_resolved": "🟢", "escalate_normal": "🟠", "escalate_urgent_needs_approval": "🔴"}
alerts = {a.id: a for a in load_alerts()}
incidents = {i.id: i for i in load_incidents()}
results: dict = st.session_state.setdefault("results", {})
errors: dict = st.session_state.setdefault("errors", {})


def render_event(event: dict) -> None:
    kind = event["kind"]
    if kind == "thought":
        st.markdown(f"💭 **Thought:** {event['content']}")
    elif kind == "action":
        args = ", ".join(f"{k}={v!r}" for k, v in event["input"].items())
        st.code(f"{event['tool']}({args})", language="python")
    elif kind == "observation":
        label = f"📋 Observation · {event['tool']} · {event.get('duration_ms', 0):.0f} ms"
        with st.expander(label, expanded=False):
            st.json(event["result"], expanded=False)
    elif kind == "guardrail":
        (st.error if event.get("is_error") else st.success)(f"🛡️ Guardrail: {event['content']}")
    elif kind == "escalation":
        st.info(f"🚦 Escalation policy: {event['content']}")
    elif kind == "human_decision":
        st.success(f"🧑 Human decision: {event['content']}")
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


# ── Sidebar ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Scenario")
    selected = st.radio(
        "Select an alert",
        list(alerts),
        format_func=lambda alert_id: f"{alert_id} · {alerts[alert_id].service}",
        label_visibility="collapsed",
    )
    run_clicked = st.button("▶ Run diagnosis", type="primary", width="stretch")
    st.divider()
    sidebar_status = st.container()

alert = alerts[selected]

# ── Header & alert card ──────────────────────────────────────────────────
st.title("🔍 Rootly — AI Incident Triage Agent")
st.caption("LangGraph agent: Thought → Action → Observation over CMDB, logs and historical incidents, "
           "with human approval for urgent escalations.")

with st.container(border=True):
    st.subheader(f"{SEVERITY_ICON[alert.severity_reported.value]} {alert.id} — {alert.service}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alert type", alert.alert_type.value)
    c2.metric("Reported severity", alert.severity_reported.value)
    c3.metric("Metric value", f"{alert.metric_value:g}")
    c4.metric("Fired at (UTC)", alert.timestamp.strftime("%m-%d %H:%M"))
    st.write(alert.description)

# ── Run ──────────────────────────────────────────────────────────────────
if run_clicked:
    errors.pop(selected, None)
    st.subheader("Agent reasoning trace")
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
        st.warning("⏸ Waiting for approval")
    elif result:
        st.success("✅ Complete")
    if result:
        m1, m2 = st.columns(2)
        m1.metric("Steps", result.steps)
        m2.metric("Time", f"{result.elapsed_seconds:.0f}s")
        m3, m4 = st.columns(2)
        m3.metric("Tool calls", result.tool_calls)
        m4.metric("Confidence", f"{result.diagnosis.confidence:.2f}")
        st.caption(f"Thread: `{result.thread_id}`")
        st.divider()
        st.subheader("Export")
        st.download_button("⬇ Diagnosis JSON", json.dumps(diagnosis_dict(result), indent=2, ensure_ascii=False),
                           file_name=f"{selected}_diagnosis.json", mime="application/json", width="stretch")
        st.download_button("⬇ Diagnosis Markdown", to_markdown(result),
                           file_name=f"{selected}_diagnosis.md", mime="text/markdown", width="stretch")
        st.download_button("⬇ Trace JSON", json.dumps(result.trace, indent=2, ensure_ascii=False),
                           file_name=f"{selected}_trace.json", mime="application/json", width="stretch")
    else:
        st.info("Not run yet. Press ▶ Run diagnosis.")

if not result:
    st.stop()

d = result.diagnosis

# ── Human approval ───────────────────────────────────────────────────────
if result.needs_approval:
    request = result.approval_request or {}
    with st.container(border=True):
        st.subheader("⏸ Human approval required")
        st.write(f"Urgent escalation of **{request.get('component')}** to **{request.get('owner_team')}**.")
        st.warning(request.get("reason"))
        note = st.text_input("Note for the audit trail (optional)", key=f"note-{result.thread_id}")
        approve_col, downgrade_col = st.columns(2)
        decision = None
        if approve_col.button("✅ Approve urgent escalation", type="primary", width="stretch"):
            decision = HUMAN_APPROVE
        if downgrade_col.button("⬇ Downgrade — not urgent", width="stretch"):
            decision = HUMAN_DOWNGRADE
        if decision:
            try:
                results[selected] = resume_diagnosis(result.thread_id, decision, note=note)
            except DiagnosisError as exc:
                errors[selected] = f"Resume failed: {exc}"
            st.rerun()

# ── Trace ────────────────────────────────────────────────────────────────
st.subheader("Agent reasoning trace")
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
    c2.metric("Assessed severity", f"{SEVERITY_ICON[d.severity_assessed.value]} {d.severity_assessed.value}")
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
        st.write(f"{ESCALATION_ICON[decision_value]} {ESCALATION_LABEL[decision_value]} · owner team `{d.owner_team}`")
        st.caption(d.escalation_reason)
        if d.human_decision:
            st.write(f"🧑 Human {HUMAN_DECISION_LABEL.get(d.human_decision, d.human_decision)}"
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
