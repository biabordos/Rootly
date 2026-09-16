"""
LangSmith tracing status.

Tracing itself needs no code here: ChatMistralAI and the compiled graph are both
LangChain Runnables, so LangSmith attaches automatically through callbacks once
LANGSMITH_TRACING=true and LANGSMITH_API_KEY are set (see .env.example). This
module only reports whether that's actually wired up, for a startup banner.
"""

from __future__ import annotations

import os


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
