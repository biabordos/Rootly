"""Rootly FastAPI — expune run_diagnosis / resume_diagnosis ca endpoint-uri HTTP."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from src.agent import DiagnosisError, resume_diagnosis, run_diagnosis  # noqa: E402
from src.agent.observability import setup_phoenix  # noqa: E402
from src.agent.report import diagnosis_dict  # noqa: E402
from src.data_loader import get_alert, load_alerts  # noqa: E402

logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    NEEDS_APPROVAL = "needs_approval"
    FAILED = "failed"


@dataclass
class RunEntry:
    alert_id: str
    status: RunStatus = RunStatus.RUNNING
    result: Any = None
    error: str | None = None


_runs: dict[str, RunEntry] = {}


def _get_or_recover(thread_id: str) -> RunEntry | None:
    """In-process registry first, then the SQLite checkpoint (survives an API restart)."""
    entry = _runs.get(thread_id)
    if entry:
        return entry
    try:
        from src.agent.graph import _result, get_graph

        result = _result(get_graph(), thread_id)
        entry = RunEntry(
            alert_id=result.alert.id,
            status=RunStatus.NEEDS_APPROVAL if result.needs_approval else RunStatus.COMPLETE,
            result=result,
        )
        _runs[thread_id] = entry
        return entry
    except Exception:
        return None


def _run_background(alert_id: str, thread_id: str) -> None:
    entry = _runs[thread_id]
    try:
        alert = get_alert(alert_id)
        result = run_diagnosis(alert, thread_id=thread_id)
        entry.result = result
        entry.status = RunStatus.NEEDS_APPROVAL if result.needs_approval else RunStatus.COMPLETE
    except Exception as exc:
        logger.exception("Diagnosis failed for %s", thread_id)
        entry.error = str(exc)
        entry.status = RunStatus.FAILED


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_phoenix()
    yield


app = FastAPI(title="Rootly — AI Incident Triage", lifespan=lifespan)


class DiagnoseRequest(BaseModel):
    alert_id: str = Field(..., examples=["ALRT-001"])


class ResumeRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve|downgrade)$")
    note: str | None = None


@app.get("/alerts")
def list_alerts():
    return [
        {
            "id": a.id,
            "service": a.service,
            "alert_type": a.alert_type.value,
            "severity": a.severity_reported.value,
            "description": a.description,
        }
        for a in load_alerts()
    ]


@app.post("/diagnose", status_code=202)
def start_diagnosis(req: DiagnoseRequest, background: BackgroundTasks):
    try:
        alert = get_alert(req.alert_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    thread_id = f"{alert.id}-{uuid.uuid4().hex[:8]}"
    _runs[thread_id] = RunEntry(alert_id=req.alert_id)
    background.add_task(_run_background, req.alert_id, thread_id)
    return {"thread_id": thread_id, "status": "running"}


@app.get("/diagnose/{thread_id}")
def get_diagnosis(thread_id: str):
    entry = _get_or_recover(thread_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No run found: '{thread_id}'.")
    if entry.status == RunStatus.RUNNING:
        return {"thread_id": thread_id, "status": "running"}
    if entry.status == RunStatus.FAILED:
        raise HTTPException(status_code=500, detail=entry.error)
    return {
        "thread_id": thread_id,
        "status": entry.status.value,
        "diagnosis": diagnosis_dict(entry.result),
        "approval_request": entry.result.approval_request if entry.result.needs_approval else None,
    }


@app.post("/diagnose/{thread_id}/resume")
def resume(thread_id: str, req: ResumeRequest):
    entry = _get_or_recover(thread_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No run found: '{thread_id}'.")
    try:
        result = resume_diagnosis(thread_id, req.decision, note=req.note)
    except DiagnosisError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    entry.result = result
    entry.status = RunStatus.COMPLETE
    _runs[thread_id] = entry
    return {
        "thread_id": thread_id,
        "status": "complete",
        "diagnosis": diagnosis_dict(result),
    }
