import { useEffect, useState } from "react";

type Page = "landing" | "alerts" | "about" | "map";
type Severity = "low" | "medium" | "high" | "critical";
type IconName =
  | "arrow"
  | "check"
  | "download"
  | "info"
  | "map"
  | "pulse"
  | "spark"
  | "users"
  | "warning";

type Alert = {
  id: string;
  service: string;
  type: string;
  severity: Severity;
  metric: string;
  firedAt: string;
  status: "unresolved" | "resolved" | "urgent";
};

const alerts: Alert[] = [
  {
    id: "ALRT-001",
    service: "checkout-api",
    type: "Latency threshold",
    severity: "medium",
    metric: "p95 1,842 ms",
    firedAt: "2025-05-24 14:32:08",
    status: "unresolved",
  },
  {
    id: "ALRT-002",
    service: "payment-worker",
    type: "Error rate",
    severity: "low",
    metric: "0.42% errors",
    firedAt: "2025-05-24 14:18:42",
    status: "resolved",
  },
  {
    id: "ALRT-003",
    service: "inventory-sync",
    type: "Queue depth",
    severity: "high",
    metric: "8,482 messages",
    firedAt: "2025-05-24 14:11:26",
    status: "unresolved",
  },
  {
    id: "ALRT-004",
    service: "auth-gateway",
    type: "Availability drop",
    severity: "critical",
    metric: "82.4% availability",
    firedAt: "2025-05-24 14:04:11",
    status: "urgent",
  },
  {
    id: "ALRT-005",
    service: "user-profile",
    type: "Memory saturation",
    severity: "medium",
    metric: "91.2% utilization",
    firedAt: "2025-05-24 13:52:53",
    status: "unresolved",
  },
];

const steps = [
  {
    name: "CMDB lookup",
    tool: "service_catalog",
    thought:
      "Identify upstream dependencies and the owning team for auth-gateway before querying runtime data.",
    action: 'lookup_service(name="auth-gateway", include="dependencies,owners")',
    observation:
      "Owned by Identity Platform. Depends on session-store and token-validator; checkout-api is a downstream consumer.",
    duration: "0.8s",
  },
  {
    name: "Log analysis",
    tool: "log_search",
    thought:
      "Correlate the availability drop with repeated errors across the gateway and its direct dependencies.",
    action:
      'search_logs(service=["auth-gateway","session-store"], window="-20m", level="error")',
    observation:
      "3,821 token refresh failures. session-store reports connection pool exhaustion beginning at 14:03 UTC.",
    duration: "2.4s",
  },
  {
    name: "Historical incidents",
    tool: "incident_memory",
    thought:
      "Compare the error signature against resolved incidents to determine whether a safe runbook exists.",
    action:
      'find_similar(signature="session pool exhausted + token refresh", limit=5)',
    observation:
      "92% match with INC-219. Previous mitigation required a coordinated session-store pool restart.",
    duration: "1.1s",
  },
  {
    name: "Diagnosis",
    tool: "reasoning_engine",
    thought:
      "Synthesize the dependency, telemetry, and historical evidence; determine impact and escalation path.",
    action: 'classify_impact(alert="ALRT-004", policy="production-v3")',
    observation:
      "Root cause: exhausted session-store connection pool. Critical authentication path affected. Urgent escalation recommended.",
    duration: "0.6s",
  },
];

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    arrow: <path d="m9 18 6-6-6-6M4 12h11" />,
    check: <path d="m5 12 4 4L19 6" />,
    download: <path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" />,
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v5m0-8v.01" />
      </>
    ),
    map: (
      <>
        <path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z" />
        <path d="M9 3v15m6-12v15" />
      </>
    ),
    pulse: <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />,
    spark: (
      <path d="m12 2 1.5 5.2L18 10l-4.5 2.8L12 18l-1.5-5.2L6 10l4.5-2.8ZM19 3v4M21 5h-4" />
    ),
    users: (
      <>
        <circle cx="9" cy="8" r="3" />
        <path d="M3 20c.3-4 2.3-6 6-6s5.7 2 6 6m1-9a3 3 0 1 0 0-6m1 9c2.5.6 3.8 2.6 4 6" />
      </>
    ),
    warning: (
      <>
        <path d="M10.3 3.7 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z" />
        <path d="M12 9v4m0 3v.01" />
      </>
    ),
  };

  return (
    <svg
      aria-hidden="true"
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
    >
      {paths[name]}
    </svg>
  );
}

function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "logo compact" : "logo"}>
      <span className="logo-mark">
        <span />
        <span />
        <span />
      </span>
      <span>Rootly</span>
    </div>
  );
}

function goTo(page: Page) {
  window.location.hash = page === "landing" ? "" : page;
}

function LandingPage() {
  return (
    <main className="landing-page">
      <div className="leaf-texture" aria-hidden="true" />
      <div className="landing-vignette" aria-hidden="true" />
      <span className="corner-logo" aria-hidden="true">
        <img src="/rootly-corner-logo.jpeg" alt="" />
      </span>
      <div className="landing-content">
        <div className="hero-copy">
          <span className="hero-label">
            AI incident triage / React orchestration
          </span>
          <div className="hero-title-row">
            <h1>Rootly</h1>
            <span className="hero-leaf" aria-hidden="true">
              <span />
              <span />
            </span>
          </div>
          <p>
            A smart diagnosis orchestrator for IT incidents, correlating CMDB
            context, log evidence, and historical incidents into a clear path
            to resolution.
          </p>
        </div>
        <nav className="landing-nav" aria-label="Main navigation">
          <button className="launch-button" onClick={() => goTo("alerts")}>
            <span>Launch Rootly</span>
            <Icon name="arrow" />
          </button>
          <button onClick={() => goTo("about")}>
            <span>About Rootly</span>
            <Icon name="arrow" />
          </button>
          <button onClick={() => goTo("map")}>
            <span>System Map</span>
            <Icon name="arrow" />
          </button>
        </nav>
      </div>
    </main>
  );
}

function SideNav({ active }: { active: Page }) {
  const links: { page: Page; label: string; icon: IconName }[] = [
    { page: "alerts", label: "Progress", icon: "pulse" },
    { page: "map", label: "Map", icon: "map" },
    { page: "about", label: "About us", icon: "users" },
  ];

  return (
    <aside className="side-nav">
      <button
        className="side-logo"
        aria-label="Back to home"
        onClick={() => goTo("landing")}
      >
        <span className="sidebar-custom-logo" aria-hidden="true">
          <img src="/rootly-corner-logo.jpeg" alt="" />
        </span>
      </button>
      <nav aria-label="Workspace navigation">
        {links.map((link) => (
          <button
            className={active === link.page ? "active" : ""}
            key={link.page}
            onClick={() => goTo(link.page)}
            title={link.label}
          >
            <Icon name={link.icon} size={19} />
            <span>{link.label}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}

function AlertContext({ alert }: { alert: Alert }) {
  return (
    <section className="panel context-panel">
      <div className="section-heading">
        <div>
          <span className="kicker">Selected signal</span>
          <h2>Alert context</h2>
        </div>
        <span className={`severity-badge ${alert.severity}`}>
          {alert.severity}
        </span>
      </div>
      <div className="context-grid">
        <div>
          <span>Alert type</span>
          <strong>{alert.type}</strong>
        </div>
        <div>
          <span>Reported severity</span>
          <strong className="severity-value">
            <i className={`severity-dot ${alert.severity}`} />
            {alert.severity}
          </strong>
        </div>
        <div>
          <span>Metric value</span>
          <strong>{alert.metric}</strong>
        </div>
        <div>
          <span>Fired at · UTC</span>
          <strong>{alert.firedAt}</strong>
        </div>
      </div>
    </section>
  );
}

function Investigation() {
  const [expanded, setExpanded] = useState(3);

  return (
    <section className="investigation">
      <div className="section-heading investigation-title">
        <div>
          <span className="kicker">Agent trace · 4.9s</span>
          <h2>Investigation steps</h2>
        </div>
        <span className="complete-label">
          <Icon name="check" size={14} /> Complete
        </span>
      </div>
      <div className="progress-track" aria-label="Investigation complete">
        {steps.map((step, index) => (
          <div className="progress-item" key={step.name}>
            <span className={index < 4 ? "complete" : ""}>
              {index < 4 ? <Icon name="check" size={13} /> : index + 1}
            </span>
            <small>{step.name}</small>
          </div>
        ))}
      </div>
      <div className="step-list">
        {steps.map((step, index) => {
          const isOpen = expanded === index;
          return (
            <article className={`step ${isOpen ? "open" : ""}`} key={step.name}>
              <button
                className="step-header"
                aria-expanded={isOpen}
                onClick={() => setExpanded(isOpen ? -1 : index)}
              >
                <span className="step-number">{index + 1}</span>
                <span className="step-name">{step.name}</span>
                <span className="step-tool">{step.tool}</span>
                <span className="chevron">⌄</span>
              </button>
              {isOpen && (
                <div className="step-body">
                  <div className="trace-block thought">
                    <span>Thought</span>
                    <p>{step.thought}</p>
                  </div>
                  <div className="trace-block">
                    <span>Action</span>
                    <code>{step.action}</code>
                  </div>
                  <div className="trace-block observation">
                    <span>
                      Observation <em>{step.duration}</em>
                    </span>
                    <p>{step.observation}</p>
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ApprovalBanner() {
  const [decision, setDecision] = useState<"idle" | "approved" | "downgraded">(
    "idle",
  );

  if (decision !== "idle") {
    return (
      <section className="decision-complete">
        <span className="decision-icon">
          <Icon name="check" />
        </span>
        <div>
          <strong>
            {decision === "approved"
              ? "Urgent escalation approved"
              : "Escalation downgraded"}
          </strong>
          <p>Decision recorded in the incident audit trail.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="approval-panel">
      <div className="approval-heading">
        <span className="warning-icon">
          <Icon name="warning" />
        </span>
        <div>
          <span className="kicker">Action gate</span>
          <h2>Human approval required</h2>
        </div>
      </div>
      <p className="approval-description">
        Escalate <strong>session-store</strong> to the{" "}
        <strong>Identity Platform</strong> on-call team as an urgent production
        incident.
      </p>
      <div className="warning-box">
        <Icon name="info" size={17} />
        <p>
          Urgent escalation pages two engineers and starts a coordinated
          restart. Approval is required because this action may briefly
          invalidate active sessions.
        </p>
      </div>
      <label className="audit-field">
        <span>Audit trail note <em>Optional</em></span>
        <input placeholder="Add context for the on-call team…" />
      </label>
      <div className="approval-actions">
        <button className="primary-button" onClick={() => setDecision("approved")}>
          Approve urgent escalation
        </button>
        <button
          className="secondary-button"
          onClick={() => setDecision("downgraded")}
        >
          Downgrade, not urgent
        </button>
      </div>
    </section>
  );
}

function ExportBar() {
  return (
    <footer className="export-bar">
      <span>Export artifacts</span>
      <div>
        {["Diagnosis JSON", "Diagnosis Markdown", "Trace JSON"].map((label) => (
          <button key={label}>
            <Icon name="download" size={15} />
            {label}
          </button>
        ))}
      </div>
    </footer>
  );
}

function AlertsPage() {
  const [selectedId, setSelectedId] = useState("ALRT-004");
  const [hasRun, setHasRun] = useState(false);
  const selected = alerts.find((alert) => alert.id === selectedId) ?? alerts[0];

  function selectAlert(id: string) {
    setSelectedId(id);
    setHasRun(false);
  }

  return (
    <div className="app-layout">
      <SideNav active="alerts" />
      <main className="workspace">
        <header className="workspace-header">
          <div>
            <span className="kicker">Operations center</span>
            <h1>Incident triage</h1>
          </div>
          <div className="agent-status">
            <span />
            Agent online
          </div>
        </header>
        <section className="alert-strip" aria-label="Active alerts">
          {alerts.map((alert) => (
            <button
              className={`alert-card ${selectedId === alert.id ? "selected" : ""}`}
              key={alert.id}
              onClick={() => selectAlert(alert.id)}
            >
              <span className={`status-dot ${alert.status}`} />
              <span>
                <strong>{alert.id}</strong>
                <small>{alert.service}</small>
              </span>
            </button>
          ))}
        </section>
        <div className="workspace-content">
          <AlertContext alert={selected} />
          <button className="run-button" onClick={() => setHasRun(true)}>
            <Icon name="spark" size={16} />
            {hasRun ? "Run again" : "Run"}
          </button>
          {hasRun && (
            <>
              <Investigation />
              {selected.status === "urgent" && <ApprovalBanner />}
              <ExportBar />
            </>
          )}
          {!hasRun && (
            <div className="empty-state">
              <span>
                <Icon name="spark" size={20} />
              </span>
              <p>Ready to investigate</p>
              <small>
                Run the triage agent to correlate services, logs, and incident
                history.
              </small>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function AboutPage() {
  return (
    <div className="app-layout">
      <SideNav active="about" />
      <main className="about-page">
        <div className="about-content">
          <Logo />
          <span className="kicker">About the project</span>
          <h1>Calm thinking for your most critical moments.</h1>
          <div className="about-copy">
            <p>
              Rootly is an AI-powered incident triage agent built for modern IT
              operations teams. It connects fragmented operational signals,
              investigates likely causes, and turns noisy alerts into clear,
              auditable decisions.
            </p>
            <p>
              We are a small team of engineers, operators, and designers who
              believe incident response should feel less like searching in the
              dark. Rootly works alongside people—not around them—surfacing its
              reasoning and keeping humans in control of high-impact actions.
            </p>
          </div>
          <div className="about-principles">
            <div>
              <span>01</span>
              <strong>Evidence first</strong>
            </div>
            <div>
              <span>02</span>
              <strong>Human control</strong>
            </div>
            <div>
              <span>03</span>
              <strong>Built for clarity</strong>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

const graphNodes = [
  { id: "client", label: "Web client", type: "Frontend", x: 5, y: 42, status: "healthy" },
  { id: "gateway", label: "API gateway", type: "Edge service", x: 27, y: 42, status: "healthy" },
  { id: "auth", label: "auth-gateway", type: "Service", x: 49, y: 18, status: "investigating" },
  { id: "checkout", label: "checkout-api", type: "Service", x: 49, y: 66, status: "degraded" },
  { id: "session", label: "session-store", type: "Redis cluster", x: 73, y: 11, status: "critical" },
  { id: "token", label: "token-validator", type: "Service", x: 73, y: 38, status: "healthy" },
  { id: "payment", label: "payment-worker", type: "Worker", x: 73, y: 72, status: "healthy" },
];

function MapPage() {
  return (
    <div className="app-layout">
      <SideNav active="map" />
      <main className="map-page">
        <header className="map-header">
          <div>
            <span className="kicker">Live topology</span>
            <h1>System architecture</h1>
          </div>
          <div className="map-meta">
            <span><i /> 7 components</span>
            <span>Updated 8s ago</span>
          </div>
        </header>
        <section className="graph" aria-label="Service dependency graph">
          <svg className="graph-lines" viewBox="0 0 1000 600" preserveAspectRatio="none">
            <path d="M135 300H315" />
            <path d="M405 300C455 300 450 155 535 155" />
            <path d="M405 300C455 300 450 445 535 445" />
            <path className="active-line" d="M625 155H775" />
            <path d="M625 155C690 155 690 300 775 300" />
            <path d="M625 445C690 445 690 300 775 300" />
            <path d="M625 445H775V470" />
          </svg>
          {graphNodes.map((node) => (
            <article
              className={`graph-node ${node.status}`}
              key={node.id}
              style={{ left: `${node.x}%`, top: `${node.y}%` }}
            >
              <span className="node-status" />
              <div>
                <strong>{node.label}</strong>
                <small>{node.type}</small>
              </div>
              {node.status === "investigating" && <em>Investigating</em>}
              {node.status === "critical" && <em>Root cause</em>}
            </article>
          ))}
          <div className="graph-note">
            <Icon name="spark" size={15} />
            Agent path
            <span>auth-gateway → session-store</span>
          </div>
          <div className="legend">
            <strong>Component health</strong>
            <span><i className="healthy" /> Healthy</span>
            <span><i className="degraded" /> Degraded</span>
            <span><i className="critical" /> Critical</span>
            <span><i className="investigating" /> Investigating</span>
          </div>
        </section>
      </main>
    </div>
  );
}

function getPage(): Page {
  const hash = window.location.hash.replace("#", "");
  return hash === "alerts" || hash === "about" || hash === "map"
    ? hash
    : "landing";
}

export default function App() {
  const [page, setPage] = useState<Page>(getPage);

  useEffect(() => {
    const updatePage = () => setPage(getPage());
    window.addEventListener("hashchange", updatePage);
    return () => window.removeEventListener("hashchange", updatePage);
  }, []);

  if (page === "alerts") return <AlertsPage />;
  if (page === "about") return <AboutPage />;
  if (page === "map") return <MapPage />;
  return <LandingPage />;
}
