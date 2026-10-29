"""
Observability setup: LangSmith, Phoenix, and AI Observability Platform.

LangSmith needs no code: ChatMistralAI and the compiled graph are LangChain
Runnables, so it attaches automatically once LANGSMITH_TRACING=true and
LANGSMITH_API_KEY are set.  Phoenix and the AI Observability SDK are set up
explicitly below.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def tracing_status() -> str:
    """One line describing whether this run will be traced to LangSmith, and where."""
    tracing_on = os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2")
    if (tracing_on or "").lower() != "true":
        return "LangSmith tracing: OFF"
    api_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    if not api_key:
        return "LangSmith tracing: ON but LANGSMITH_API_KEY is missing -- nothing will be sent"
    project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "default"
    return f"LangSmith tracing: ON -> project '{project}'"


_PHOENIX_INITIALIZED = False


def setup_phoenix() -> None:
    """Send OpenTelemetry traces to a Phoenix collector, if one is configured."""
    global _PHOENIX_INITIALIZED
    if _PHOENIX_INITIALIZED:
        return
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
    if not endpoint:
        return
    try:
        from phoenix.otel import register

        register(
            project_name=os.getenv("PHOENIX_PROJECT_NAME", "rootly"),
            endpoint=endpoint,
            auto_instrument=True,
        )
        _PHOENIX_INITIALIZED = True
        logger.info("Phoenix tracing enabled -> %s", endpoint)
    except Exception as exc:
        logger.warning("Phoenix setup failed: %s", exc)


# ── AI Observability SDK ────────────────────────────────────────────────

_AIOBS_INITIALIZED = False


def init_observability() -> None:
    """Initialize the ai-observability-sdk if an API key is configured."""
    global _AIOBS_INITIALIZED
    if _AIOBS_INITIALIZED:
        return
    api_key = os.getenv("AI_OBSERVABILITY_API_KEY")
    if not api_key:
        logger.debug("AI Observability SDK: skipped (no AI_OBSERVABILITY_API_KEY)")
        return
    try:
        import ai_observability

        ai_observability.init(
            service_name="rootly",
            service_version="1.0.0",
            deployment_environment=os.getenv("DEPLOYMENT_ENV", "dev"),
            capture_prompts=True,
        )
        _AIOBS_INITIALIZED = True
        logger.info("AI Observability SDK initialized -> %s",
                     os.getenv("AI_OBSERVABILITY_ENDPOINT", "(default)"))
    except Exception as exc:
        logger.warning("AI Observability SDK init failed: %s", exc)


def flush_observability(timeout_millis: int = 5000) -> None:
    """Flush pending spans (call before a CLI process exits)."""
    if not _AIOBS_INITIALIZED:
        return
    try:
        import ai_observability
        ai_observability.flush(timeout_millis=timeout_millis)
    except Exception as exc:
        logger.warning("AI Observability SDK flush failed: %s", exc)


def shutdown_observability() -> None:
    """Flush and tear down the SDK (call on server shutdown)."""
    if not _AIOBS_INITIALIZED:
        return
    try:
        import ai_observability
        ai_observability.shutdown()
        logger.info("AI Observability SDK shut down")
    except Exception as exc:
        logger.warning("AI Observability SDK shutdown failed: %s", exc)
