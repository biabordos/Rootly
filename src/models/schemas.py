"""
Rootly — Pydantic schemas for all data entities.

Mock data scenarios inspired by the StackGen State of Reliability 2026 taxonomy
(178,000+ incidents, 360+ services, 16 root-cause categories, 30 failure modes).

Sources:
  - stackgen.com/blog/sre-root-cause-taxonomy-online-services
  - stackgen.com/blog/sre-failure-mode-taxonomy
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────

class AlertType(str, Enum):
    ERROR_RATE_HIGH = "error_rate_high"
    LATENCY_HIGH = "latency_high"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


class EventType(str, Enum):
    METRIC = "metric"
    ERROR = "error"
    DEPLOY = "deploy"
    CONFIG_CHANGE = "config_change"
    HEALTH_CHECK = "health_check"
    ALERT = "alert"


class ComponentType(str, Enum):
    WEB_APP = "web-app"
    API_GATEWAY = "api-gateway"
    MICROSERVICE = "microservice"
    DATABASE = "database"
    CACHE = "cache"


class Environment(str, Enum):
    PRODUCTION = "production"
    STAGING = "staging"


class Criticality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Root-cause taxonomy (StackGen-inspired) ──────────────────────────────

class RootCauseCategory(str, Enum):
    """Mapped from StackGen's 16 root-cause categories."""
    RC_01_CODE_DEFECT = "RC-01_code_defect"
    RC_02_CONFIG_CHANGE = "RC-02_config_change"
    RC_04_CAPACITY_EXHAUSTION = "RC-04_capacity_exhaustion"
    RC_06_NETWORK_DNS = "RC-06_network_dns"
    RC_07_AUTH = "RC-07_auth"
    RC_08_THIRD_PARTY = "RC-08_third_party"
    RC_09_DATA_PIPELINE = "RC-09_data_pipeline"


class FailureMode(str, Enum):
    """Mapped from StackGen's 30 failure-mode patterns."""
    FM_01_CROSS_ORG_CASCADE = "FM-01_cross_org_cascade"
    FM_09_DEPLOY_REGRESSION = "FM-09_deploy_induced_regression"
    FM_10_CONFIG_INDUCED = "FM-10_config_induced_failure"
    FM_13_RESOURCE_EXHAUSTION = "FM-13_resource_exhaustion"
    FM_23_HIDDEN_COUPLING = "FM-23_hidden_internal_coupling"


# ── Data entities ────────────────────────────────────────────────────────

class Alert(BaseModel):
    """
    A monitoring alert that triggers the agent investigation.
    Corresponds to README §5.2 — Alert.
    """
    id: str = Field(..., examples=["ALRT-001"])
    service: str = Field(..., description="Name of the affected service")
    alert_type: AlertType
    severity_reported: Severity
    timestamp: datetime
    metric_value: float = Field(..., description="The metric that triggered the alert")
    description: str = Field("", description="Human-readable alert summary")
    root_cause_category: RootCauseCategory | None = Field(
        None, description="StackGen taxonomy — used for evaluation, NOT shown to agent"
    )
    failure_mode: FailureMode | None = Field(
        None, description="StackGen taxonomy — used for evaluation, NOT shown to agent"
    )


class CMDBComponent(BaseModel):
    """
    An IT component in the Configuration Management Database.
    Corresponds to README §5.2 — CMDB Component.
    """
    id: str = Field(..., examples=["CI-001"])
    name: str = Field(..., description="Unique component name")
    type: ComponentType
    owner_team: str
    depends_on: list[str] = Field(
        default_factory=list,
        description='Upstream dependencies, format: "CI-XXX (name)"'
    )
    depended_by: list[str] = Field(
        default_factory=list,
        description='Downstream dependents, format: "CI-XXX (name)"'
    )
    environment: Environment = Environment.PRODUCTION
    criticality: Criticality = Criticality.HIGH
    last_deploy: str | None = Field(
        None, description="Last deployment timestamp and version"
    )
    config_version: str | None = Field(
        None, description="Current configuration version"
    )


class LogEntry(BaseModel):
    """
    A log event from a service.
    Corresponds to README §5.2 — Log Entry.
    """
    timestamp: datetime
    service: str
    level: LogLevel
    message: str
    trace_id: str = Field(..., description="Trace ID for correlating related events")
    event_type: EventType = EventType.ERROR


class HistoricalIncident(BaseModel):
    """
    A previously resolved incident for RAG retrieval.
    Corresponds to README §5.2 — Historical Incident.
    """
    id: str = Field(..., examples=["INC-2025-114"])
    description: str
    root_cause: str
    resolution: str
    tags: list[str] = Field(default_factory=list)
    severity: Severity = Severity.HIGH
    date_resolved: str = Field(..., description="ISO date of resolution")
    duration_minutes: int = Field(..., description="Time from detection to resolution")
    root_cause_category: RootCauseCategory | None = Field(
        None, description="StackGen taxonomy tag"
    )


# ── Diagnosis output (agent produces this) ───────────────────────────────

class DiagnosisPackage(BaseModel):
    """
    The structured output the agent produces after investigation.
    Corresponds to README §3.2 step 6.
    """
    alert_id: str
    summary: str
    affected_component: str
    severity_assessed: Severity
    critical_dependencies: list[str] = Field(default_factory=list)
    log_evidence: list[str] = Field(default_factory=list)
    root_cause_hypothesis: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    escalation_recommendation: str
    similar_incidents: list[str] = Field(default_factory=list)
    investigation_steps: int = Field(0, description="Number of ReAct steps taken")
    time_to_diagnosis_seconds: float = Field(0.0)
