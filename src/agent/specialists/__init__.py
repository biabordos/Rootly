"""
Rootly's three specialist agents, each a plain LangGraph node function.

    cmdb_agent_node       cmdb_lookup only      -> cmdb_context
    log_agent_node        log_search only       -> log_evidence_gathered
    synthesis_agent_node  similar_incidents_search + submit_diagnosis -> the guardrail

Each one is a thin wrapper: it renders its briefing from shared state, hands it to
graph_nodes.run_specialist() with its own prompt and tool subset, and folds the result
back into state as plain data. The orchestrator decides which one runs (see
graph_nodes.orchestrator_node) and control returns to it afterwards.
"""

from src.agent.specialists.cmdb_agent import cmdb_agent_node
from src.agent.specialists.log_agent import log_agent_node
from src.agent.specialists.synthesis_agent import synthesis_agent_node

__all__ = ["cmdb_agent_node", "log_agent_node", "synthesis_agent_node"]
