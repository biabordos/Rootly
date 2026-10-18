"""
Rootly Streamlit UI.

    streamlit run src/ui/streamlit_app.py
"""

from __future__ import annotations

import base64
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
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "rootly-mark.png"
CORNER_LOGO_PATH = ROOT / "Material Design Ripple Effect" / "public" / "rootly-corner-logo.jpeg"
ARCHITECTURE_PATH = ROOT / "docs" / "diagrams" / "architecture.png"

st.set_page_config(
    page_title="Rootly | Incident Triage",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Data ────────────────────────────────────────────────────────────────────
ESCALATION_COLOR = {
    "auto_resolved": "#7ee2a8",
    "escalate_normal": "#f2c66d",
    "escalate_urgent_needs_approval": "#ff6b6b",
}
alerts = {a.id: a for a in load_alerts()}
incidents = {i.id: i for i in load_incidents()}
results: dict = st.session_state.setdefault("results", {})
errors: dict = st.session_state.setdefault("errors", {})
alert_statuses: dict[str, str] = st.session_state.setdefault(
    "alert_statuses", {aid: "unresolved" for aid in alerts},
)

# ── Routing via query params ────────────────────────────────────────────────
page = st.query_params.get("page", "landing")
if page not in {"landing", "alerts", "about", "map"}:
    page = "alerts"
selected_alert = st.query_params.get("alert")
if selected_alert and selected_alert not in alerts:
    selected_alert = None

# ── Assets ──────────────────────────────────────────────────────────────────
_logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
_corner_b64 = ""
if CORNER_LOGO_PATH.is_file():
    _corner_b64 = base64.b64encode(CORNER_LOGO_PATH.read_bytes()).decode()

# ── SVG icons ───────────────────────────────────────────────────────────────
_ICON_PATHS = {
    "arrow": '<path d="m9 18 6-6-6-6M4 12h11"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "download": '<path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8v.01"/>',
    "map": '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z"/><path d="M9 3v15m6-12v15"/>',
    "pulse": '<path d="M3 12h4l2.5-6 4 12 2.5-6h5"/>',
    "spark": '<path d="m12 2 1.5 5.2L18 10l-4.5 2.8L12 18l-1.5-5.2L6 10l4.5-2.8ZM19 3v4M21 5h-4"/>',
    "users": '<circle cx="9" cy="8" r="3"/><path d="M3 20c.3-4 2.3-6 6-6s5.7 2 6 6m1-9a3 3 0 1 0 0-6m1 9c2.5.6 3.8 2.6 4 6"/>',
    "warning": '<path d="M10.3 3.7 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 3v.01"/>',
}


def _icon(name: str, size: int = 18) -> str:
    return (
        f'<svg aria-hidden="true" class="icon" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24">{_ICON_PATHS[name]}</svg>'
    )


# ── Fonts ───────────────────────────────────────────────────────────────────
st.markdown(
    '<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500'
    "&family=Manrope:wght@400;500;600;700&display=swap\" rel=\"stylesheet\">",
    unsafe_allow_html=True,
)

# ── CSS ─────────────────────────────────────────────────────────────────────
_TEXTURE_URL = (
    "https://images.unsplash.com/photo-1692520883599-d543cfe6d43d"
    "?crop=entropy&cs=tinysrgb&fit=max&fm=jpg"
    "&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwzfHxkYXJrJTIwZ3JlZW4lMjBsZWF2ZXMl"
    "MjBzaGFkb3clMjBib3RhbmljYWwlMjB0ZXh0dXJlfGVufDF8fHx8MTc5MjE5MDE3NHww"
    "&ixlib=rb-4.1.0&q=80&w=1600"
)

APP_CSS = f"""<style>
/* ── Streamlit chrome ───────────────────────────────────────────── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] {{ display:none!important; }}
[data-testid="stHeader"] {{ background:transparent!important; backdrop-filter:none!important; }}
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stBottom"] {{ display:none!important; }}
.stMainBlockContainer {{ max-width:100%!important; }}
.stMainBlockContainer [data-testid="stVerticalBlock"] {{ gap:0!important; }}
.stMain {{ background:var(--bg)!important; }}
.stApp {{ background:var(--bg); color:var(--text); font-family:var(--sans); }}

/* ── Design tokens ──────────────────────────────────────────────── */
:root {{
    --bg:#050505; --panel:#0c0c0c; --elevated:#121212; --border:#252525;
    --text:#f5f5f2; --muted:#8e8e8e; --accent:#d9ff62;
    --low:#7ee2a8; --medium:#f2c66d; --high:#f19a66; --critical:#ff6b6b;
    --mono:"DM Mono",Consolas,monospace;
    --sans:"Manrope","Helvetica Neue",Arial,sans-serif;
}}
* {{ box-sizing:border-box; }}
a {{ text-decoration:none; color:inherit; }}
h1,h2,h3,h4,p,label {{ font-family:var(--sans); }}
h1 {{ letter-spacing:-.04em; font-weight:600; }}
h2,h3 {{ letter-spacing:-.025em; font-weight:500; }}
code,pre {{ font-family:var(--mono); }}

/* ── Icon ───────────────────────────────────────────────────────── */
.icon {{
    display:block; flex:none; fill:none;
    stroke:currentColor; stroke-linecap:round; stroke-linejoin:round; stroke-width:1.7;
}}

/* ── Logo ───────────────────────────────────────────────────────── */
.logo {{
    display:flex; align-items:center; gap:14px;
    color:var(--text); font-size:31px; font-weight:600; letter-spacing:-0.05em;
}}
.logo-mark {{
    position:relative; display:block; width:31px; height:31px; transform:rotate(-8deg);
}}
.logo-mark span {{
    position:absolute; width:15px; height:8px;
    border:2px solid var(--accent); border-radius:100% 10% 100% 10%;
    transform:rotate(-38deg);
}}
.logo-mark span:first-child {{ top:2px; left:12px; }}
.logo-mark span:nth-child(2) {{ top:12px; left:3px; transform:rotate(30deg); }}
.logo-mark span:last-child {{ right:0; bottom:1px; transform:rotate(-18deg); }}

/* ── Landing page ───────────────────────────────────────────────── */
.landing-page {{
    position:relative; min-height:100vh; overflow:hidden; background:#050505;
}}
.leaf-texture {{
    position:absolute; inset:-5%; opacity:0.19;
    background-image:url("{_TEXTURE_URL}");
    background-position:center; background-size:cover;
    filter:grayscale(35%) contrast(120%);
    animation:living-texture 18s ease-in-out infinite alternate;
    transform-origin:center;
}}
@keyframes living-texture {{
    0%   {{ transform:scale(1.02) translate3d(-1%,-1%,0); }}
    50%  {{ transform:scale(1.08) translate3d(1%,0,0); }}
    100% {{ transform:scale(1.04) translate3d(0,1%,0); }}
}}
.landing-vignette {{
    position:absolute; inset:0;
    background:
        radial-gradient(circle at center,transparent 0%,rgb(5 5 5 / 35%) 45%,#050505 100%),
        linear-gradient(180deg,rgb(5 5 5 / 20%),rgb(5 5 5 / 72%));
}}
.corner-logo {{
    position:absolute; z-index:2;
    top:clamp(30px,4vw,58px); right:clamp(30px,5.5vw,84px);
    display:block; width:clamp(56px,5vw,78px); aspect-ratio:1;
    overflow:hidden; opacity:.58; mix-blend-mode:screen; pointer-events:none;
}}
.corner-logo img {{
    width:100%; height:100%; object-fit:cover;
    filter:hue-rotate(-24deg) saturate(75%) brightness(1.35);
    transform:scale(2.35);
}}
.landing-content {{
    position:relative; z-index:1; display:flex;
    width:100%; min-height:100vh; flex-direction:column;
    justify-content:space-between; padding:clamp(36px,5.5vw,84px);
}}
.hero-copy {{ max-width:1160px; }}
.hero-label {{
    display:block; margin-bottom:clamp(32px,8vh,86px);
    color:var(--accent); font-family:"DM Mono",monospace;
    font-size:10px; font-weight:500; letter-spacing:0.19em; text-transform:uppercase;
}}
.hero-title-row {{
    display:flex; align-items:flex-start; gap:clamp(16px,2vw,32px);
}}
.hero-title-row h1 {{
    margin:0; color:#fafafa;
    font-size:clamp(120px,15vw,220px); font-weight:700;
    letter-spacing:-0.085em; line-height:0.8;
}}
.hero-leaf {{
    position:relative; display:block;
    width:clamp(34px,5vw,70px); height:clamp(34px,5vw,70px);
    margin-top:2px; transform:rotate(-12deg);
}}
.hero-leaf span {{
    position:absolute; width:58%; height:34%;
    border:3px solid var(--accent); border-radius:100% 10% 100% 10%;
    transform:rotate(-38deg);
}}
.hero-leaf span:first-child {{ top:4%; right:2%; }}
.hero-leaf span:last-child  {{ bottom:12%; left:2%; transform:rotate(30deg); }}
.hero-title-row + p {{
    max-width:760px; margin:clamp(36px,6vh,66px) 0 0;
    color:#a0a0a0; font-size:clamp(15px,1.4vw,20px); line-height:1.65;
}}

/* landing nav – uses <a> tags */
.landing-nav {{
    display:grid; width:100%;
    grid-template-columns:repeat(3,1fr); gap:clamp(12px,2vw,28px);
    padding-top:48px;
}}
.landing-nav a {{
    display:flex; width:100%; align-items:center; justify-content:space-between;
    min-height:66px; padding:0 24px;
    border:1px solid #fafafa; border-radius:0; background:transparent;
    cursor:pointer; font-size:16px; font-weight:500; transition:180ms ease;
}}
.landing-nav a:hover {{
    border-color:var(--accent); color:var(--accent);
    background:rgb(217 255 98 / 5%); transform:translateY(-3px);
}}
.landing-nav a.launch-button {{
    border-color:var(--accent); color:#050505; background:var(--accent);
}}
.landing-nav a.launch-button:hover {{ color:#050505; background:#e4ff8e; }}
.landing-nav a .icon {{ transition:transform 180ms ease; }}
.landing-nav a:hover .icon {{ transform:translateX(3px); }}

/* ── Side nav ───────────────────────────────────────────────────── */
.side-nav {{
    position:fixed; z-index:20; top:0; bottom:0; left:0;
    display:flex; width:84px; flex-direction:column; align-items:center;
    border-right:1px solid var(--border); background:#080808;
}}
.side-logo {{
    display:grid; width:100%; height:83px; place-items:center;
    border:0; border-bottom:1px solid var(--border);
    background:transparent; cursor:pointer;
}}
.sidebar-custom-logo {{
    display:block; width:42px; aspect-ratio:1;
    overflow:hidden; opacity:.72; mix-blend-mode:screen;
}}
.sidebar-custom-logo img {{
    width:100%; height:100%; object-fit:cover;
    filter:hue-rotate(-24deg) saturate(90%) brightness(1.55);
    transform:scale(2.35);
}}
.side-nav nav {{
    display:flex; width:100%; flex:1; flex-direction:column;
    align-items:center; gap:10px; padding-top:26px;
}}
.side-nav nav a {{
    position:relative; display:flex; width:66px; height:58px;
    flex-direction:column; align-items:center; justify-content:center;
    gap:6px; border:0; border-radius:10px;
    color:#777; background:transparent; cursor:pointer;
}}
.side-nav nav a span {{
    font-family:"DM Mono",monospace; font-size:9px;
}}
.side-nav nav a:hover,
.side-nav nav a.active {{
    color:var(--accent); background:rgb(217 255 98 / 6%);
}}
.side-nav nav a.active::before {{
    position:absolute; left:-9px; width:2px; height:24px;
    border-radius:2px; background:var(--accent); content:"";
}}

/* ── Workspace ──────────────────────────────────────────────────── */
.kicker {{
    color:#777; font-family:"DM Mono",monospace;
    font-size:10px; font-weight:500; letter-spacing:0.12em; text-transform:uppercase;
}}
.workspace-header {{
    display:flex; min-height:132px; align-items:flex-end;
    justify-content:space-between; padding:34px 46px 27px;
    border-bottom:1px solid var(--border);
    width:100%; box-sizing:border-box;
}}
.workspace-header h1 {{
    margin:7px 0 0; font-size:27px; font-weight:500; letter-spacing:-0.035em;
}}
.agent-status {{
    display:flex; align-items:center; gap:9px;
    padding:8px 11px; border:1px solid var(--border); border-radius:8px;
    color:var(--muted); font-family:"DM Mono",monospace; font-size:10px;
}}
.agent-status span {{
    width:6px; height:6px; border-radius:50%;
    background:var(--low); box-shadow:0 0 8px rgb(126 226 168 / 60%);
}}

/* ── Alert strip ────────────────────────────────────────────────── */
.alert-strip {{
    display:flex; gap:10px; padding:20px 46px;
    overflow-x:auto; border-bottom:1px solid var(--border);
    width:100%; box-sizing:border-box;
    scrollbar-color:#333 transparent;
}}
.alert-card {{
    display:flex; min-width:194px; align-items:flex-start; gap:12px;
    padding:15px; border:1px solid var(--border); border-radius:11px;
    background:var(--panel); cursor:pointer; text-align:left; transition:160ms ease;
    text-decoration:none!important; color:inherit!important;
}}
.alert-card:hover {{ background:var(--elevated); }}
.alert-card.selected {{
    border-color:var(--accent);
    box-shadow:inset 0 0 0 1px rgb(217 255 98 / 12%);
}}
.status-dot {{
    width:7px; height:7px; margin-top:5px; flex:none;
    border-radius:50%; background:#606060;
}}
.status-dot.resolved {{ background:var(--low); }}
.status-dot.urgent {{
    background:var(--critical); box-shadow:0 0 7px rgb(255 107 107 / 50%);
}}
.alert-card > span:last-child {{
    display:flex; flex-direction:column; gap:5px; line-height:1.45;
}}
.alert-card strong {{
    font-family:"DM Mono",monospace; font-size:11px; font-weight:500; line-height:1.4;
}}
.alert-card small {{ color:var(--muted); font-size:12px; line-height:1.4; margin-top:2px; }}

/* ── Context panel ──────────────────────────────────────────────── */
.panel {{
    border:1px solid var(--border); border-radius:14px; background:var(--panel);
}}
.context-panel {{ padding:26px 28px 28px; }}
.section-heading {{
    display:flex; align-items:center; justify-content:space-between;
}}
.section-heading h2, .approval-heading h2 {{
    margin:5px 0 0; font-size:18px; font-weight:500; letter-spacing:-0.025em;
}}
.severity-badge {{
    padding:6px 9px; border:1px solid currentColor; border-radius:999px;
    font-family:"DM Mono",monospace; font-size:9px; text-transform:uppercase;
    background:transparent!important;
}}
.severity-badge.low    {{ color:var(--low); }}
.severity-badge.medium {{ color:var(--medium); }}
.severity-badge.high   {{ color:var(--high); }}
.severity-badge.critical {{ color:var(--critical); }}
.context-grid {{
    display:grid; grid-template-columns:repeat(4,1fr); gap:0;
    margin-top:25px; border:1px solid #202020; border-radius:10px; overflow:hidden;
}}
.context-grid > div {{
    display:flex; min-width:0; flex-direction:column; gap:8px;
    padding:18px; border-right:1px solid #202020; background:#0a0a0a;
}}
.context-grid > div:last-child {{ border-right:0; }}
.context-grid span {{
    color:#717171; font-family:"DM Mono",monospace;
    font-size:9px; letter-spacing:0.04em; text-transform:uppercase;
}}
.context-grid strong {{
    overflow:hidden; font-family:"DM Mono",monospace;
    font-size:11px; font-weight:500; text-overflow:ellipsis; white-space:nowrap;
}}
.severity-value {{
    display:flex; align-items:center; gap:8px; text-transform:capitalize;
}}
.severity-dot {{
    width:6px; height:6px; border-radius:50%;
}}
.severity-dot.low {{ background:var(--low); }}
.severity-dot.medium {{ background:var(--medium); }}
.severity-dot.high {{ background:var(--high); }}
.severity-dot.critical {{ background:var(--critical); }}

/* ── Run button ─────────────────────────────────────────────────── */
.run-button {{
    display:inline-flex; min-height:38px; align-items:center; justify-content:center;
    gap:8px; padding:0 17px; margin-top:14px;
    border:1px solid var(--accent); border-radius:8px;
    color:#111; background:var(--accent);
    cursor:pointer; font-size:12px; font-weight:700; transition:150ms ease;
}}
.run-button:hover {{
    background:#e4ff8e; box-shadow:0 0 22px rgb(217 255 98 / 15%);
}}

/* ── Empty state ────────────────────────────────────────────────── */
.empty-state {{
    display:flex; min-height:250px; flex-direction:column;
    align-items:center; justify-content:center; color:var(--muted); text-align:center;
}}
.empty-state > span {{
    display:grid; width:46px; height:46px; place-items:center;
    border:1px solid var(--border); border-radius:50%;
    color:var(--accent); background:var(--panel);
}}
.empty-state p {{ margin:15px 0 5px; color:#ccc; font-size:13px; }}
.empty-state small {{ max-width:380px; font-size:11px; line-height:1.6; }}

/* ── Investigation ──────────────────────────────────────────────── */
.investigation {{ margin-top:48px; }}
.investigation-title {{ margin-bottom:25px; }}
.complete-label {{
    display:flex; align-items:center; gap:6px;
    color:var(--low); font-family:"DM Mono",monospace; font-size:10px;
}}
.progress-track {{
    display:grid; margin-bottom:25px;
}}
.progress-item {{
    position:relative; display:flex; flex-direction:column;
    align-items:center; gap:9px; color:#777; text-align:center;
}}
.progress-item::before {{
    position:absolute; z-index:0; top:13px; right:50%;
    width:100%; height:1px; background:var(--accent); content:"";
}}
.progress-item:first-child::before {{ display:none; }}
.progress-item > span {{
    position:relative; z-index:1; display:grid;
    width:27px; height:27px; place-items:center;
    border:1px solid #444; border-radius:50%; background:var(--bg);
    font-family:"DM Mono",monospace; font-size:10px;
}}
.progress-item > span.complete {{
    border-color:var(--accent); color:#111; background:var(--accent);
}}
.progress-item small {{ font-family:"DM Mono",monospace; font-size:9px; }}

/* ── Trace blocks ───────────────────────────────────────────────── */
.trace-block {{
    padding:13px 15px; border:1px solid #232323; border-radius:8px;
    background:#090909; margin-bottom:8px;
}}
.trace-block > span {{
    display:flex; justify-content:space-between; margin-bottom:7px;
    color:#696969; font-family:"DM Mono",monospace;
    font-size:9px; letter-spacing:0.08em; text-transform:uppercase;
}}
.trace-block em {{ color:#626262; font-style:normal; }}
.trace-block p {{ margin:0; color:#b6b6b3; font-size:12px; line-height:1.65; }}
.trace-block code {{
    color:#b9d96c; font-family:"DM Mono",monospace;
    font-size:10px; line-height:1.6; overflow-wrap:anywhere;
}}
.trace-block.thought {{
    border-color:rgb(217 255 98 / 14%); background:rgb(217 255 98 / 3%);
}}

/* ── Approval ───────────────────────────────────────────────────── */
.approval-panel {{
    margin-top:34px; padding:26px;
    border:1px solid rgb(255 107 107 / 35%); border-radius:14px;
    background:rgb(255 107 107 / 5%);
}}
.approval-heading {{
    display:flex; align-items:center; gap:13px;
}}
.warning-icon {{
    display:grid; width:38px; height:38px; place-items:center;
    border-radius:9px; color:var(--critical); background:rgb(255 107 107 / 10%);
}}
.approval-description {{
    margin:20px 0 16px; color:#bcbcb8; font-size:13px; line-height:1.7;
}}
.approval-description strong {{ color:var(--text); font-weight:600; }}
.warning-box {{
    display:flex; align-items:flex-start; gap:11px;
    padding:13px 14px; border:1px solid rgb(255 107 107 / 18%);
    border-radius:8px; color:#dd8585; background:rgb(255 107 107 / 5%);
}}
.warning-box p {{ margin:0; font-size:11px; line-height:1.6; }}

/* ── Decision complete ──────────────────────────────────────────── */
.decision-complete {{
    display:flex; margin-top:34px; align-items:center; gap:13px; padding:20px;
    border:1px solid rgb(126 226 168 / 26%); border-radius:12px;
    background:rgb(126 226 168 / 5%);
}}
.decision-icon {{
    display:grid; width:34px; height:34px; place-items:center;
    border-radius:50%; color:#111; background:var(--low);
}}
.decision-complete strong {{ font-size:13px; }}
.decision-complete p {{ margin:4px 0 0; color:var(--muted); font-size:11px; }}

/* ── Export bar ──────────────────────────────────────────────────── */
.export-bar {{
    display:flex; margin-top:34px; align-items:center;
    justify-content:space-between; padding-top:20px;
    border-top:1px solid var(--border);
}}
.export-bar > span {{
    color:#696969; font-family:"DM Mono",monospace;
    font-size:9px; letter-spacing:0.08em; text-transform:uppercase;
}}

/* ── About page ─────────────────────────────────────────────────── */
.about-page {{
    display:grid; width:100%; min-height:100vh; place-items:center;
    padding:70px 8vw;
    background:
        radial-gradient(circle at 80% 20%,rgb(217 255 98 / 4%),transparent 25%),
        var(--bg);
}}
.about-content {{ width:min(100%,810px); }}
.about-content > .logo {{ margin-bottom:90px; }}
.about-content h1 {{
    max-width:720px; margin:18px 0 43px;
    font-size:clamp(38px,6vw,66px); font-weight:500;
    letter-spacing:-0.06em; line-height:1.08;
}}
.about-copy {{
    display:grid; grid-template-columns:repeat(2,1fr); gap:48px;
    padding-top:30px; border-top:1px solid var(--border);
}}
.about-copy p {{ margin:0; color:#9a9a97; font-size:14px; line-height:1.9; }}
.about-principles {{
    display:grid; margin-top:55px; grid-template-columns:repeat(3,1fr);
    border:1px solid var(--border); border-radius:12px; overflow:hidden;
}}
.about-principles div {{
    display:flex; min-height:100px; flex-direction:column;
    justify-content:space-between; padding:18px;
    border-right:1px solid var(--border); background:var(--panel);
}}
.about-principles div:last-child {{ border-right:0; }}
.about-principles span {{
    color:var(--accent); font-family:"DM Mono",monospace; font-size:9px;
}}
.about-principles strong {{ font-size:13px; font-weight:500; }}

/* ── Map page ───────────────────────────────────────────────────── */
.map-header {{
    display:flex; min-height:132px; align-items:center;
    justify-content:space-between; padding:27px 46px;
    border-bottom:1px solid var(--border);
    width:100%; box-sizing:border-box;
}}
.map-header h1 {{
    margin:7px 0 0; font-size:27px; font-weight:500; letter-spacing:-0.035em;
}}
.map-meta {{
    display:flex; gap:20px; color:#727272;
    font-family:"DM Mono",monospace; font-size:9px;
}}
.map-meta span {{ display:flex; align-items:center; gap:7px; }}
.map-meta i {{
    width:6px; height:6px; border-radius:50%;
    background:var(--low); box-shadow:0 0 8px rgb(126 226 168 / 60%);
}}

/* ── Workspace content container ────────────────────────────────── */
.st-key-workspace-content {{
    width:min(100% - 64px,1040px)!important;
    margin:0 auto!important;
    padding:40px 0 64px!important;
}}
.st-key-workspace-content [data-testid="stVerticalBlock"] {{ gap:1rem!important; }}
.st-key-map-content [data-testid="stVerticalBlock"] {{ gap:1rem!important; }}

/* ── Streamlit component overrides ──────────────────────────────── */
.st-key-run-btn .stButton button {{
    width:auto!important; min-height:38px!important;
    padding:0 17px!important;
    border:1px solid var(--accent)!important; border-radius:8px!important;
    color:#111!important; background:var(--accent)!important;
    font-size:12px!important; font-weight:700!important;
    box-shadow:none!important; transition:150ms ease;
}}
.st-key-run-btn .stButton button:hover {{
    background:#e4ff8e!important;
    box-shadow:0 0 22px rgb(217 255 98 / 15%)!important;
}}
.st-key-run-btn .stButton button p {{ color:#111!important; }}

.st-key-approve-btn .stButton button {{
    min-height:38px!important; padding:0 17px!important;
    border:1px solid var(--accent)!important; border-radius:8px!important;
    color:#111!important; background:var(--accent)!important;
    font-size:11px!important; font-weight:700!important;
}}
.st-key-approve-btn .stButton button:hover {{ background:#e4ff8e!important; }}
.st-key-approve-btn .stButton button p {{ color:#111!important; }}

.st-key-downgrade-btn .stButton button {{
    min-height:38px!important; padding:0 17px!important;
    border:1px solid #343434!important; border-radius:8px!important;
    color:var(--text)!important; background:#111!important;
    font-size:11px!important;
}}
.st-key-downgrade-btn .stButton button:hover {{ border-color:#555!important; }}

.st-key-export-wrap .stDownloadButton button {{
    height:34px!important; padding:0 11px!important;
    border:1px solid #2d2d2d!important; border-radius:7px!important;
    color:#9b9b9b!important; background:#0a0a0a!important;
    font-family:"DM Mono",monospace!important; font-size:9px!important;
}}
.st-key-export-wrap .stDownloadButton button:hover {{
    border-color:#555!important; color:var(--text)!important;
}}

[data-testid="stMetric"] {{
    background:var(--panel); border:1px solid var(--border);
    padding:1rem; border-radius:11px;
}}
[data-testid="stMetricValue"] {{ color:var(--text); font-family:var(--mono); }}
[data-testid="stMetricLabel"] {{
    font-family:var(--mono); font-size:.65rem;
    letter-spacing:.04em; text-transform:uppercase; color:var(--muted);
}}
[data-testid="stExpander"],[data-testid="stForm"] {{
    border-color:var(--border)!important; background:var(--panel)!important;
    border-radius:14px!important;
}}
[data-testid="stExpander"] summary {{
    font-family:var(--sans); font-weight:500;
}}
[data-testid="stTabs"] button {{ color:var(--muted); font-family:var(--mono); }}
[data-testid="stTabs"] button[aria-selected="true"] {{ color:var(--accent); }}
[data-testid="stProgress"] > div {{ border-radius:999px; }}

.stButton > button,.stDownloadButton > button {{
    border-radius:8px; border:1px solid #3b3b3b;
    background:transparent; color:var(--text); font-family:var(--sans);
    transition:border-color 180ms ease,background 180ms ease;
}}
.stButton > button:hover,.stDownloadButton > button:hover {{
    border-color:var(--accent); background:#171b0d; color:var(--text);
}}

.escalation-tag {{
    display:inline-block; padding:4px 10px; border-radius:999px;
    font:600 .6rem var(--mono); text-transform:uppercase; letter-spacing:.04em;
}}

/* ── Responsive ─────────────────────────────────────────────────── */
@media (max-width:1000px) {{
    .context-grid {{ grid-template-columns:repeat(2,1fr); }}
    .context-grid > div:nth-child(2) {{ border-right:0; }}
    .context-grid > div:nth-child(-n+2) {{ border-bottom:1px solid #202020; }}
}}
@media (max-width:720px) {{
    .corner-logo {{ top:24px; right:20px; width:48px; }}
    .landing-content {{ min-height:100svh; padding:30px 20px; }}
    .hero-label {{ max-width:280px; margin-bottom:52px; line-height:1.7; }}
    .hero-title-row {{ gap:12px; }}
    .hero-title-row h1 {{ font-size:clamp(72px,24vw,110px); }}
    .hero-leaf {{ width:34px; height:34px; }}
    .hero-leaf span {{ border-width:2px; }}
    .hero-title-row + p {{ margin-top:30px; font-size:14px; }}
    .landing-nav {{ gap:8px; padding-top:40px; }}
    .landing-nav a {{ min-height:58px; padding:0 13px; font-size:12px; }}
    .landing-nav a .icon {{ display:none; }}
    .side-nav {{
        top:auto; right:0; width:auto; height:66px;
        flex-direction:row; border-top:1px solid var(--border); border-right:0;
    }}
    .side-logo {{ display:none; }}
    .side-nav nav {{
        flex:1; flex-direction:row; justify-content:space-around;
        gap:0; padding:0;
    }}
    .side-nav nav a {{ height:54px; }}
    .side-nav nav a.active::before {{
        top:-6px; left:auto; width:24px; height:2px;
    }}
    .stMainBlockContainer {{ margin-left:0!important; padding-bottom:66px!important; }}
    .workspace-header {{ min-height:110px; padding:25px 20px 20px; }}
    .alert-strip {{ padding:15px 20px; }}
    .st-key-workspace-content {{ width:calc(100% - 40px)!important; padding-top:28px!important; }}
    .context-grid {{ grid-template-columns:1fr; }}
    .context-grid > div {{ border-right:0; border-bottom:1px solid #202020; }}
    .context-grid > div:last-child {{ border-bottom:0; }}
    .about-page {{ display:block; padding:45px 24px 100px; }}
    .about-content > .logo {{ margin-bottom:70px; }}
    .about-copy, .about-principles {{ grid-template-columns:1fr; }}
    .about-principles div {{ border-right:0; border-bottom:1px solid var(--border); }}
    .about-principles div:last-child {{ border-bottom:0; }}
    .map-meta {{ display:none; }}
}}
@media (prefers-reduced-motion:reduce) {{
    .leaf-texture {{ animation:none; }}
    * {{ scroll-behavior:auto!important; transition-duration:.01ms!important; }}
}}
</style>"""

st.markdown(APP_CSS, unsafe_allow_html=True)

# Non-landing pages get sidebar offset + padding
if page == "landing":
    st.markdown(
        "<style>.stMainBlockContainer{padding:0!important}</style>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        "<style>"
        ".stMainBlockContainer{max-width:100%!important;padding:0 0 0 84px!important}"
        ".block-container{max-width:100%!important;padding:0!important}"
        "</style>",
        unsafe_allow_html=True,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────
def _get_alert_status(alert_id: str) -> str:
    return alert_statuses.get(alert_id, "unresolved")


def _render_sidebar(active: str) -> None:
    logo_src = (
        f"data:image/jpeg;base64,{_corner_b64}"
        if _corner_b64
        else f"data:image/png;base64,{_logo_b64}"
    )
    st.markdown(
        f'<aside class="side-nav">'
        f'<a class="side-logo" href="/" target="_top" aria-label="Back to home">'
        f'<span class="sidebar-custom-logo" aria-hidden="true">'
        f'<img src="{logo_src}" alt="">'
        f"</span></a>"
        f'<nav aria-label="Workspace navigation">'
        f'<a class="{"active" if active == "alerts" else ""}" href="?page=alerts" target="_top" title="Progress">'
        f'{_icon("pulse", 19)}<span>Progress</span></a>'
        f'<a class="{"active" if active == "map" else ""}" href="?page=map" target="_top" title="Map">'
        f'{_icon("map", 19)}<span>Map</span></a>'
        f'<a class="{"active" if active == "about" else ""}" href="?page=about" target="_top" title="About us">'
        f'{_icon("users", 19)}<span>About us</span></a>'
        f"</nav></aside>",
        unsafe_allow_html=True,
    )


def _render_event(event: dict) -> None:
    kind = event["kind"]
    if kind == "thought":
        st.markdown(
            f'<div class="trace-block thought"><span>Thought</span>'
            f'<p>{event["content"]}</p></div>',
            unsafe_allow_html=True,
        )
    elif kind == "routing":
        st.markdown(
            f'<div class="trace-block"><span>Handoff → {event["tool"]} agent</span>'
            f'<p>{event["content"]}</p></div>',
            unsafe_allow_html=True,
        )
    elif kind == "action":
        args = ", ".join(f"{k}={v!r}" for k, v in event["input"].items())
        st.markdown(
            f'<div class="trace-block"><span>Action</span>'
            f'<code>{event["tool"]}({args})</code></div>',
            unsafe_allow_html=True,
        )
    elif kind == "observation":
        duration = event.get("duration_ms", 0)
        st.markdown(
            f'<div class="trace-block"><span><span>Observation / {event["tool"]}</span>'
            f"<em>{duration:.0f} ms</em></span></div>",
            unsafe_allow_html=True,
        )
        with st.expander("Result data", expanded=False):
            st.json(event["result"], expanded=False)
    elif kind == "guardrail":
        (st.error if event.get("is_error") else st.success)(f"Guardrail: {event['content']}")
    elif kind == "escalation":
        st.info(f"Escalation policy: {event['content']}")
    elif kind == "human_decision":
        st.success(f"Human decision: {event['content']}")
    elif kind == "note":
        st.warning(event["content"])


def _step_title(step: int, events: list[dict]) -> str:
    tools = [f"→ {e['tool']} agent" for e in events if e["kind"] == "routing"]
    tools += [e["tool"] for e in events if e["kind"] == "action"]
    if any(e["kind"] == "guardrail" for e in events):
        tools.append("submit_diagnosis")
    if any(e["kind"] == "escalation" for e in events):
        tools.append("escalation_policy")
    if any(e["kind"] == "human_decision" for e in events):
        tools.append("human_approval")
    return f"Step {step}: " + (" → ".join(tools) if tools else "reasoning")


# ── Page renderers ──────────────────────────────────────────────────────────

def _render_landing() -> None:
    corner_img = ""
    if _corner_b64:
        corner_img = (
            '<span class="corner-logo" aria-hidden="true">'
            f'<img src="data:image/jpeg;base64,{_corner_b64}" alt="">'
            "</span>"
        )
    st.markdown(
        f'<main class="landing-page">'
        f'<div class="leaf-texture" aria-hidden="true"></div>'
        f'<div class="landing-vignette" aria-hidden="true"></div>'
        f"{corner_img}"
        f'<div class="landing-content">'
        f'<div class="hero-copy">'
        f'<span class="hero-label">AI incident triage / ReAct orchestration</span>'
        f'<div class="hero-title-row">'
        f"<h1>Rootly</h1>"
        f'<span class="hero-leaf" aria-hidden="true"><span></span><span></span></span>'
        f"</div>"
        f"<p>A smart diagnosis orchestrator for IT incidents, correlating CMDB "
        f"context, log evidence, and historical incidents into a clear path to resolution.</p>"
        f"</div>"
        f'<nav class="landing-nav" aria-label="Main navigation">'
        f'<a class="launch-button" href="?page=alerts" target="_top">'
        f'<span>Launch Rootly</span>{_icon("arrow")}</a>'
        f'<a href="?page=about" target="_top">'
        f'<span>About Rootly</span>{_icon("arrow")}</a>'
        f'<a href="?page=map" target="_top">'
        f'<span>System Map</span>{_icon("arrow")}</a>'
        f"</nav></div></main>",
        unsafe_allow_html=True,
    )


def _render_about() -> None:
    _render_sidebar("about")
    st.markdown(
        '<main class="about-page"><div class="about-content">'
        '<div class="logo">'
        '<span class="logo-mark"><span></span><span></span><span></span></span>'
        "<span>Rootly</span></div>"
        '<span class="kicker">About the project</span>'
        "<h1>Calm thinking for your most critical moments.</h1>"
        '<div class="about-copy">'
        "<p>Rootly is an AI-powered incident triage agent built for modern IT "
        "operations teams. It connects fragmented operational signals, investigates "
        "likely causes, and turns noisy alerts into clear, auditable decisions.</p>"
        "<p>We are a small team of engineers, operators, and designers who believe "
        "incident response should feel less like searching in the dark. Rootly works "
        "alongside people—not around them—surfacing its reasoning and "
        "keeping humans in control of high-impact actions.</p></div>"
        '<div class="about-principles">'
        "<div><span>01</span><strong>Evidence first</strong></div>"
        "<div><span>02</span><strong>Human control</strong></div>"
        "<div><span>03</span><strong>Built for clarity</strong></div>"
        "</div></div></main>",
        unsafe_allow_html=True,
    )


def _render_map() -> None:
    _render_sidebar("map")
    alert_id = selected_alert or next(iter(alerts))
    alert = alerts[alert_id]
    st.markdown(
        f'<div class="map-header">'
        f'<div><span class="kicker">Live topology</span>'
        f"<h1>System architecture</h1></div>"
        f'<div class="map-meta">'
        f"<span><i></i>{len(alerts)} alerts tracked</span>"
        f"<span>Alert: {alert_id}</span></div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    with st.container(key="map-content"):
        if ARCHITECTURE_PATH.is_file():
            st.image(str(ARCHITECTURE_PATH), use_container_width=True)
            st.caption("Rootly system architecture: investigation orchestration, tools, and observability.")
        st.caption("Live dependency map for the selected alert scenario, built from CMDB and same-day log evidence.")
        render_system_map(alert)


def _render_alerts() -> None:
    _render_sidebar("alerts")

    # Workspace header
    st.markdown(
        '<div class="workspace-header">'
        '<div><span class="kicker">Operations center</span>'
        "<h1>Incident triage</h1></div>"
        '<div class="agent-status"><span></span>Agent online</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # Alert strip
    cards_html = []
    for aid, alert in alerts.items():
        status = _get_alert_status(aid)
        sel = " selected" if aid == selected_alert else ""
        cards_html.append(
            f'<a class="alert-card{sel}" href="?page=alerts&alert={aid}" target="_top">'
            f'<span class="status-dot {status}"></span>'
            f"<span><strong>{aid}</strong>"
            f"<small>{alert.service}</small></span></a>"
        )
    st.markdown(
        f'<div class="alert-strip" aria-label="Active alerts">'
        f'{"".join(cards_html)}</div>',
        unsafe_allow_html=True,
    )

    # Workspace content
    with st.container(key="workspace-content"):
        if not selected_alert:
            st.markdown(
                '<div class="empty-state">'
                f'<span>{_icon("spark", 20)}</span>'
                "<p>Ready to investigate</p>"
                "<small>Select an alert from the strip, then run the agent to "
                "correlate services, logs, and incident history.</small>"
                "</div>",
                unsafe_allow_html=True,
            )
            return

        alert = alerts[selected_alert]
        severity = alert.severity_reported.value

        # Context panel
        st.markdown(
            f'<section class="panel context-panel">'
            f'<div class="section-heading">'
            f'<div><span class="kicker">Selected signal</span>'
            f"<h2>Alert context</h2></div>"
            f'<span class="severity-badge {severity}">{severity}</span></div>'
            f'<div class="context-grid">'
            f"<div><span>Alert type</span><strong>{alert.alert_type.value}</strong></div>"
            f'<div><span>Reported severity</span><strong class="severity-value">'
            f'<i class="severity-dot {severity}"></i>{severity}</strong></div>'
            f"<div><span>Metric value</span><strong>{alert.metric_value:g}</strong></div>"
            f"<div><span>Fired at · UTC</span>"
            f'<strong>{alert.timestamp.strftime("%Y-%m-%d %H:%M")}</strong></div>'
            f"</div></section>",
            unsafe_allow_html=True,
        )

        # Run button
        with st.container(key="run-btn"):
            run_clicked = st.button(
                "Run" if selected_alert not in results else "Run again",
                key="run-diagnosis",
            )

        # Execute diagnosis
        if run_clicked:
            errors.pop(selected_alert, None)
            st.markdown(
                '<div class="section-heading investigation-title">'
                '<div><span class="kicker">Agent trace</span>'
                "<h2>Orchestrator trace</h2></div></div>",
                unsafe_allow_html=True,
            )
            with st.status("Investigating…", expanded=True) as status:
                current_step = {"n": 0}

                def on_event(event: dict) -> None:
                    if event["step"] != current_step["n"]:
                        current_step["n"] = event["step"]
                        st.markdown(f"#### Step {event['step']}")
                        status.update(label=f"Investigating… step {event['step']}")
                    _render_event(event)

                try:
                    diagnosis_result = run_diagnosis(alert, on_event=on_event)
                    results[selected_alert] = diagnosis_result
                    esc = diagnosis_result.diagnosis.escalation_decision
                    if diagnosis_result.needs_approval or (
                        esc and esc.value == "escalate_urgent_needs_approval"
                    ):
                        alert_statuses[selected_alert] = "urgent"
                    elif esc and esc.value in {"auto_resolved", "escalate_normal"}:
                        alert_statuses[selected_alert] = "resolved"
                    else:
                        alert_statuses[selected_alert] = "unresolved"
                    status.update(label="Diagnosis complete", state="complete", expanded=False)
                except DiagnosisError as exc:
                    errors[selected_alert] = f"Diagnosis failed: {exc}"
                if selected_alert in errors:
                    status.update(label="Diagnosis failed", state="error", expanded=True)
            st.rerun()

        if selected_alert in errors:
            st.error(errors[selected_alert])

        result = results.get(selected_alert)

        if not result:
            st.markdown(
                '<div class="empty-state">'
                f'<span>{_icon("spark", 20)}</span>'
                "<p>Ready to investigate</p>"
                "<small>Run the triage agent to correlate services, logs, and "
                "incident history.</small></div>",
                unsafe_allow_html=True,
            )
            return

        d = result.diagnosis

        # ── Investigation trace ─────────────────────────────────────
        by_step: dict[int, list[dict]] = {}
        for event in result.trace:
            by_step.setdefault(event["step"], []).append(event)

        # Progress track
        step_labels = []
        for sn, evts in by_step.items():
            routing = [e for e in evts if e["kind"] == "routing"]
            actions = [e for e in evts if e["kind"] == "action"]
            if routing:
                step_labels.append(routing[0]["tool"])
            elif actions:
                step_labels.append(actions[0]["tool"])
            else:
                step_labels.append(f"Step {sn}")

        cols = len(step_labels) or 1
        track_items = "".join(
            f'<div class="progress-item">'
            f'<span class="complete">{_icon("check", 13)}</span>'
            f"<small>{lbl}</small></div>"
            for lbl in step_labels
        )
        st.markdown(
            f'<section class="investigation">'
            f'<div class="section-heading investigation-title">'
            f'<div><span class="kicker">Agent trace</span>'
            f"<h2>Investigation steps</h2></div>"
            f'<span class="complete-label">{_icon("check", 14)} Complete</span>'
            f"</div>"
            f'<div class="progress-track" style="grid-template-columns:repeat({cols},1fr)">'
            f"{track_items}</div></section>",
            unsafe_allow_html=True,
        )

        # Step list
        for step_num, events in by_step.items():
            with st.expander(_step_title(step_num, events), expanded=False):
                for event in events:
                    _render_event(event)

        # ── Human approval ──────────────────────────────────────────
        if result.needs_approval:
            request = result.approval_request or {}
            component = request.get("component", "unknown")
            owner = request.get("owner_team", "unknown")
            st.markdown(
                f'<section class="approval-panel">'
                f'<div class="approval-heading">'
                f'<span class="warning-icon">{_icon("warning")}</span>'
                f'<div><span class="kicker">Action gate</span>'
                f"<h2>Human approval required</h2></div></div>"
                f'<p class="approval-description">'
                f"Escalate <strong>{component}</strong> to the "
                f"<strong>{owner}</strong> on-call team as an urgent production incident.</p>"
                f'<div class="warning-box">{_icon("info", 17)}'
                f"<p>Urgent escalation pages two engineers and starts a coordinated "
                f"restart. Approval is required because this action may briefly "
                f"invalidate active sessions.</p></div></section>",
                unsafe_allow_html=True,
            )
            note = st.text_input("Audit trail note (optional)", key=f"note-{result.thread_id}")
            col_a, col_d = st.columns(2)
            decision = None
            with col_a:
                with st.container(key="approve-btn"):
                    if st.button("Approve urgent escalation", use_container_width=True):
                        decision = HUMAN_APPROVE
            with col_d:
                with st.container(key="downgrade-btn"):
                    if st.button("Downgrade, not urgent", use_container_width=True):
                        decision = HUMAN_DOWNGRADE
            if decision:
                try:
                    results[selected_alert] = resume_diagnosis(result.thread_id, decision, note=note)
                except DiagnosisError as exc:
                    errors[selected_alert] = f"Resume failed: {exc}"
                st.rerun()

        elif d.human_decision:
            label = "approved" if d.human_decision == HUMAN_APPROVE else "downgraded"
            st.markdown(
                f'<section class="decision-complete">'
                f'<span class="decision-icon">{_icon("check")}</span>'
                f"<div><strong>Urgent escalation {label}</strong>"
                f"<p>Decision recorded in the incident audit trail.</p></div></section>",
                unsafe_allow_html=True,
            )

        # ── Export bar ──────────────────────────────────────────────
        st.markdown(
            '<div class="export-bar"><span>Export artifacts</span></div>',
            unsafe_allow_html=True,
        )
        with st.container(key="export-wrap"):
            ecol1, ecol2, ecol3 = st.columns(3)
            ecol1.download_button(
                "Diagnosis JSON",
                json.dumps(diagnosis_dict(result), indent=2, ensure_ascii=False),
                file_name=f"{selected_alert}_diagnosis.json",
                mime="application/json",
                use_container_width=True,
            )
            ecol2.download_button(
                "Diagnosis Markdown",
                to_markdown(result),
                file_name=f"{selected_alert}_diagnosis.md",
                mime="text/markdown",
                use_container_width=True,
            )
            ecol3.download_button(
                "Trace JSON",
                json.dumps(result.trace, indent=2, ensure_ascii=False),
                file_name=f"{selected_alert}_trace.json",
                mime="application/json",
                use_container_width=True,
            )

        # ── Diagnosis package ───────────────────────────────────────
        st.markdown(
            '<div class="section-heading" style="margin-top:40px">'
            '<div><span class="kicker">Output</span>'
            "<h2>Diagnosis package</h2></div></div>",
            unsafe_allow_html=True,
        )
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
                dv = d.escalation_decision.value
                color = ESCALATION_COLOR[dv]
                st.markdown("**Escalation decision**")
                st.markdown(
                    f'<span class="escalation-tag" style="color:{color};border:1px solid {color}">'
                    f"{ESCALATION_LABEL[dv]}</span> &nbsp; owner team <code>{d.owner_team}</code>",
                    unsafe_allow_html=True,
                )
                st.caption(d.escalation_reason)
                if d.human_decision:
                    st.write(
                        f"Human {HUMAN_DECISION_LABEL.get(d.human_decision, d.human_decision)}"
                        + (f" — _{d.human_decision_note}_" if d.human_decision_note else "")
                    )

            left, right = st.columns(2)
            with left:
                st.markdown("**Critical dependencies**")
                st.write(" ".join(f"`{dep}`" for dep in d.critical_dependencies) or "—")
                st.markdown("**Similar historical incidents**")
                for inc_id in d.similar_incidents:
                    inc = incidents[inc_id]
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


# ── Page routing ────────────────────────────────────────────────────────────
if page == "landing":
    _render_landing()
elif page == "about":
    _render_about()
elif page == "map":
    _render_map()
else:
    _render_alerts()
