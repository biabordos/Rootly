"""
Agent tests.

Offline tests drive the real LangGraph graph, real tools, real guardrail and real
escalation policy with a scripted fake chat model per agent, so the orchestration logic
is verified without an API key. The live test calls the real Mistral API and is skipped
unless MISTRAL_API_KEY is set.

Rootly runs four agents (orchestrator + cmdb/log/synthesis specialists), each with its own
model handle, so each one is scripted independently via run_diagnosis(llms={...}). That
also means a specialist's script is read across *all* of its runs in order: a log agent
called in two investigation rounds consumes the first round's turns, then the second's.
"""

from __future__ import annotations

import itertools
import os
import sqlite3

import httpx
import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from src.agent import DiagnosisError, resume_diagnosis, run_diagnosis
from src.agent import graph as rootly_graph
from src.agent import graph_nodes
from src.agent.graph_nodes import ROUTE_TOOL_NAME, SUBMIT_TOOL_NAME
from src.agent.system_prompt import format_alert
from src.data_loader import get_alert
from src.models.schemas import EscalationDecision

_ids = itertools.count(1)


def turn(text: str = "", *calls: tuple[str, dict], finish_reason: str = "tool_calls") -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[{"name": name, "args": args, "id": f"call{next(_ids):05d}", "type": "tool_call"} for name, args in calls],
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        response_metadata={"finish_reason": finish_reason, "model_name": "mistral-large-latest"},
    )


def route(specialist: str, *targets: str, window: dict | None = None, reason: str = "next step", text: str = "") -> AIMessage:
    """One orchestrator turn: hand off to `specialist` on `targets`."""
    args = {"next": specialist, "targets": list(targets), "reason": reason, **(window or {})}
    return turn(text or f"Routing to the {specialist} agent.", (ROUTE_TOOL_NAME, args))


def report(text: str) -> AIMessage:
    """A specialist's closing turn: plain text, no tool calls, so its run ends."""
    return turn(text, finish_reason="stop")


class FakeLLM:
    """Mimics a LangChain chat model: bind_tools() then invoke(), replaying scripted turns or raising errors."""

    def __init__(self, turns):
        self._turns = iter(turns)
        self.calls: list[list] = []
        self.bound_tools: list[dict] | None = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        item = next(self._turns)
        if isinstance(item, Exception):
            raise item
        return item


def agents(*, orchestrator=(), cmdb=(), log=(), synthesis=()) -> dict[str, FakeLLM]:
    """A FakeLLM per agent, in the shape run_diagnosis(llms=...) expects."""
    return {
        "orchestrator": FakeLLM(list(orchestrator)),
        "cmdb": FakeLLM(list(cmdb)),
        "log": FakeLLM(list(log)),
        "synthesis": FakeLLM(list(synthesis)),
    }


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
    return httpx.HTTPStatusError(str(status), request=request, response=httpx.Response(status, request=request))


VALID_PACKAGE = {
    "summary": "checkout-api is failing payments because payments-db has exhausted its connection pool.",
    "affected_component": "payments-db",
    "severity_assessed": "high",
    "critical_dependencies": ["CI-006 (payments-db)", "order-service"],
    "log_evidence": ["2026-08-18T09:09:15Z payments-db: Max connections reached (20/20), rejecting new connection requests"],
    "root_cause_hypothesis": "payments-db max_connections=20 is saturated, so checkout-api connections time out after 5000ms.",
    "confidence": 0.9,
    "escalation_recommendation": "Page payments-team: raise the pool size and add PgBouncer, as in INC-2025-114.",
    "similar_incidents": ["INC-2025-114"],
}

WINDOW_001 = {"start_time": "2026-08-18T08:55:00Z", "end_time": "2026-08-18T09:14:00Z"}


def scripted_alrt_001(*, submit_turns=None) -> dict[str, FakeLLM]:
    """
    The canonical happy path: CMDB on the alerted service, logs on the dependency it
    points at, then synthesis. payments-db has no dependencies of its own and sits one
    hop from checkout-api, so no guardrail check fires.
    """
    submit_turns = submit_turns or [turn("Submitting.", (SUBMIT_TOOL_NAME, VALID_PACKAGE))]
    return agents(
        orchestrator=[
            route("cmdb", "checkout-api", text="Start with the CMDB entry for checkout-api."),
            route("log", "payments-db", window=WINDOW_001, text="It depends on payments-db; check its errors."),
            route("synthesis", text="Evidence is in; synthesize."),
        ],
        cmdb=[
            turn("Looking up the alerted service.", ("cmdb_lookup", {"component_name": "checkout-api"})),
            report("checkout-api (service, payments-team) depends_on payments-db, auth-service, order-service.\n"
                   "INVESTIGATE NEXT: payments-db"),
        ],
        log=[
            turn("Searching payments-db errors.",
                 ("log_search", {"service": "payments-db", **WINDOW_001, "level": "ERROR"})),
            report("SERVICE: payments-db\nVERDICT: LOCAL\n"
                   "EVIDENCE: 2026-08-18T09:09:15Z payments-db: Max connections reached (20/20)\n"
                   "BLAMES: -\nINVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("Looking for the same pattern historically.",
                 ("similar_incidents_search", {"query": "checkout-api timeouts, payments-db connection pool exhausted"})),
            *submit_turns,
        ],
    )


def scripted_alrt_002() -> dict[str, FakeLLM]:
    # user-service has one dependent (api-gateway); a confident medium diagnosis is auto-resolved.
    window = {"start_time": "2026-08-19T14:15:00Z", "end_time": "2026-08-19T14:40:00Z"}
    package = {
        "summary": "user-service latency is high after the v2.5.0 deploy introduced a memory leak.",
        "affected_component": "user-service",
        "severity_assessed": "medium",
        "critical_dependencies": ["api-gateway"],
        "log_evidence": ["2026-08-19T14:33:00Z user-service: OOMKilled"],
        "root_cause_hypothesis": "A memory leak in v2.5.0 drives GC pauses and OOM kills.",
        "confidence": 0.85,
        "escalation_recommendation": "identity-team: roll back v2.5.0.",
        "similar_incidents": ["INC-2025-203"],
    }
    return agents(
        orchestrator=[
            route("log", "user-service", "user-db", "auth-service", window=window),
            route("synthesis"),
        ],
        log=[
            turn("", ("log_search", {"service": "user-service", **window}),
                 ("log_search", {"service": "user-db", **window}),
                 ("log_search", {"service": "auth-service", **window})),
            report("user-service: LOCAL (OOMKilled after the v2.5.0 deploy). user-db and auth-service: HEALTHY.\n"
                   "INVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "memory leak after deployment, OOMKilled"})),
            turn("", (SUBMIT_TOOL_NAME, package)),
        ],
    )


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(graph_nodes, "RETRY_BASE_SECONDS", 0)


@pytest.fixture(autouse=True)
def in_memory_graph(monkeypatch):
    """One in-memory checkpointed graph per test, so tests never touch .checkpoints/."""
    compiled = rootly_graph.build_graph(InMemorySaver())
    monkeypatch.setattr(rootly_graph, "get_graph", lambda: compiled)
    return compiled


# ── The multi-agent flow ─────────────────────────────────────────────────

def test_graph_routes_specialists_applies_guardrail_and_returns_package():
    llms = scripted_alrt_001()
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    # 3 orchestrator turns + 2 per specialist.
    assert result.steps == 9
    assert result.tool_calls == 3
    assert result.model == "mistral-large-latest"
    assert result.diagnosis.alert_id == "ALRT-001"
    assert result.diagnosis.affected_component == "payments-db"
    assert result.diagnosis.critical_dependencies == ["payments-db", "order-service"]
    assert result.usage["input_tokens"] == 900

    routing = [e for e in events if e["kind"] == "routing"]
    assert [e["tool"] for e in routing] == ["cmdb", "log", "synthesis"]
    assert routing[0]["content"] == "cmdb agent → checkout-api"
    assert routing[2]["content"] == "synthesis agent → —"

    observations = [e for e in events if e["kind"] == "observation"]
    assert [o["tool"] for o in observations] == ["cmdb_lookup", "log_search", "similar_incidents_search"]
    assert observations[1]["result"]["total_matches"] == 3  # real log data flowed back to the agent

    guardrail = [e for e in events if e["kind"] == "guardrail"]
    assert len(guardrail) == 1 and not guardrail[0]["is_error"]

    # Streamed events and the trace stored in state are the same.
    assert result.trace == events


def test_each_agent_binds_only_its_own_tools_and_gets_its_own_system_prompt():
    llms = scripted_alrt_001()
    run_diagnosis(get_alert("ALRT-001"), llms=llms)

    bound = {name: [t["function"]["name"] for t in fake.bound_tools] for name, fake in llms.items()}
    assert bound["orchestrator"] == [ROUTE_TOOL_NAME]
    assert bound["cmdb"] == ["cmdb_lookup"]
    assert bound["log"] == ["log_search"]
    assert bound["synthesis"] == ["similar_incidents_search", SUBMIT_TOOL_NAME]
    assert all(t["type"] == "function" and "parameters" in t["function"]
               for fake in llms.values() for t in fake.bound_tools)

    # Every agent opens its own conversation with its own role prompt, and never receives
    # the shared message log.
    for name, fake in llms.items():
        first = fake.calls[0]
        assert isinstance(first[0], SystemMessage), name
        assert len(first) == 2, name  # system prompt + briefing, nothing else
    assert "orchestrator of Rootly's investigation" in llms["orchestrator"].calls[0][0].content
    assert "CMDB analysis specialist" in llms["cmdb"].calls[0][0].content
    assert "log analysis specialist" in llms["log"].calls[0][0].content
    assert "diagnosis synthesis specialist" in llms["synthesis"].calls[0][0].content


def test_specialist_findings_are_handed_on_as_structured_context(in_memory_graph):
    llms = scripted_alrt_001()
    result = run_diagnosis(get_alert("ALRT-001"), llms=llms)

    values = in_memory_graph.get_state({"configurable": {"thread_id": result.thread_id}}).values
    cmdb_entry = values["cmdb_context"][0]
    assert cmdb_entry["targets"] == ["checkout-api"]
    assert [c["name"] for c in cmdb_entry["components"]] == ["checkout-api"]
    assert "INVESTIGATE NEXT: payments-db" in cmdb_entry["analysis"]

    log_entry = values["log_evidence_gathered"][0]
    assert log_entry["services"] == ["payments-db"]
    assert log_entry["start_time"] == WINDOW_001["start_time"]
    assert any("Max connections reached" in line for line in log_entry["entries"])
    assert "VERDICT: LOCAL" in log_entry["analysis"]

    # The synthesis agent was briefed with both, not with the raw conversation.
    briefing = llms["synthesis"].calls[0][1].content
    assert "CMDB context gathered so far" in briefing
    assert "Log evidence gathered so far" in briefing
    assert "Max connections reached" in briefing


def test_a_rejected_package_goes_back_through_the_orchestrator():
    # The guardrail's reasons name what is still missing, and only the log agent can close
    # that gap, so a rejection must re-enter routing rather than loop inside synthesis.
    llms = scripted_alrt_001(submit_turns=[
        turn("Submitting.", (SUBMIT_TOOL_NAME, {**VALID_PACKAGE, "affected_component": "payments-database"})),
        turn("Fixing the component name.", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
    ])
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
        route("synthesis", text="The package was rejected over a bad component name; resubmit."),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    guardrail = [e for e in events if e["kind"] == "guardrail"]
    assert guardrail[0]["is_error"] and "payments-database" in guardrail[0]["content"]
    assert not guardrail[1]["is_error"]
    assert [e["tool"] for e in events if e["kind"] == "routing"] == ["cmdb", "log", "synthesis", "synthesis"]

    # The rejection reasons reached the orchestrator and then the synthesis agent again.
    replan_briefing = llms["synthesis"].calls[-1][1].content
    assert "REJECTED by the guardrail" in replan_briefing
    assert "payments-database" in replan_briefing
    assert result.diagnosis.affected_component == "payments-db"


def test_agent_never_sees_evaluation_taxonomy():
    prompt = format_alert(get_alert("ALRT-003"))
    assert "RC-06" not in prompt and "FM-01" not in prompt and "root_cause_category" not in prompt


# ── Orchestrator routing ─────────────────────────────────────────────────

def test_orchestrator_is_reminded_then_falls_back_when_it_will_not_hand_off():
    # The orchestrator must end every turn with route_to_specialist. If it answers with
    # prose instead, it is reminded once and then routed deterministically, so a confused
    # routing turn costs steps rather than the whole run.
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        report("I think we should look into the database."),
        report("Still thinking about it."),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    notes = [e["content"] for e in events if e["kind"] == "note"]
    assert any("did not hand off" in n for n in notes)
    assert any("falling back to the cmdb agent" in n for n in notes)
    # The fallback mirrors the prompt's order: CMDB first, on the alerted service.
    assert [e["tool"] for e in events if e["kind"] == "routing"] == ["cmdb", "log", "synthesis"]
    assert llms["cmdb"].calls, "the fallback still ran the CMDB specialist"
    assert result.diagnosis.affected_component == "payments-db"


def test_unusable_routing_arguments_are_rejected_and_asked_again():
    # A 'log' handoff with no service is not actionable, and an unknown specialist name is
    # not a route at all: both are answered as a tool error so the model can correct itself.
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("log"),  # no targets
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    notes = [e["content"] for e in events if e["kind"] == "note"]
    assert any("routing arguments were unusable" in n for n in notes)
    answer = [m for m in llms["orchestrator"].calls[1] if isinstance(m, ToolMessage)][-1]
    assert answer.status == "error" and "call the tool again" in answer.content.lower()
    assert result.diagnosis.affected_component == "payments-db"


def test_a_second_cmdb_lookup_of_the_same_component_is_refused():
    # Observed on a live ALRT-001 run: the orchestrator routed to the CMDB agent twice for
    # checkout-api, spending a whole round to learn nothing. CMDB records are static, so the
    # repeat handoff is refused and the orchestrator has to pick something new.
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("cmdb", "checkout-api", text="Let me check the CMDB again."),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    assert [e["tool"] for e in events if e["kind"] == "routing"] == ["cmdb", "log", "synthesis"]
    assert len(llms["cmdb"].calls) == 2, "the CMDB specialist ran once, not twice"
    refusal = [m for m in llms["orchestrator"].calls[2] if isinstance(m, ToolMessage)][-1]
    assert refusal.status == "error" and "has not been looked up already" in refusal.content
    # The briefing names what is already held, so the model can avoid the repeat itself.
    assert "Components already looked up in the CMDB: checkout-api" in llms["orchestrator"].calls[1][1].content
    assert result.diagnosis.affected_component == "payments-db"


def test_a_cmdb_handoff_keeps_only_the_components_not_yet_held():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("cmdb", "checkout-api", "payments-db", text="checkout-api is known; payments-db is not."),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
    ])
    llms["cmdb"] = FakeLLM([
        turn("", ("cmdb_lookup", {"component_name": "checkout-api"})),
        report("INVESTIGATE NEXT: payments-db"),
        turn("", ("cmdb_lookup", {"component_name": "payments-db"})),
        report("payments-db has no dependencies.\nINVESTIGATE NEXT: none"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    second = [e for e in events if e["kind"] == "routing" and e["tool"] == "cmdb"][1]
    assert second["input"]["targets"] == ["payments-db"]  # checkout-api dropped
    assert result.diagnosis.affected_component == "payments-db"


def test_a_log_handoff_to_an_invented_service_is_refused():
    # Observed on a live ALRT-005 run: the orchestrator invented "authentication-service"
    # (for auth-service), log_search returned "Unknown service", and the log agent then
    # reported a *fabricated* log line for it. Refusing the handoff stops that at the source.
    window = {"start_time": "2026-08-22T15:45:00Z", "end_time": "2026-08-22T16:15:00Z"}
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("log", "authentication-service", window=window),
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    assert [e["tool"] for e in events if e["kind"] == "routing"] == ["cmdb", "log", "synthesis"]
    refusal = [m for m in llms["orchestrator"].calls[1] if isinstance(m, ToolMessage)][-1]
    assert refusal.status == "error"
    assert "auth-service" in refusal.content  # the valid names are offered back
    # The log specialist was never handed the invented service.
    assert all("authentication-service" not in str(e.get("input", ""))
               for e in events if e["kind"] == "action")
    assert result.diagnosis.affected_component == "payments-db"


def test_a_log_handoff_keeps_only_the_services_that_exist():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("log", "payments-db", "payments-database", window=WINDOW_001),
        route("synthesis"),
    ])
    events: list[dict] = []

    run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    log_routing = next(e for e in events if e["kind"] == "routing" and e["tool"] == "log")
    assert log_routing["input"]["targets"] == ["payments-db"]  # the typo is dropped


def test_a_log_handoff_without_a_window_gets_the_alert_window():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("log", "payments-db"),  # no start_time/end_time
        route("synthesis"),
    ])
    llms["log"] = FakeLLM([
        turn("", ("log_search", {"service": "payments-db", **WINDOW_001, "level": "ERROR"})),
        report("payments-db: LOCAL. INVESTIGATE NEXT: none"),
    ])
    events: list[dict] = []

    run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    # ALRT-001 fired at 09:12; the default window is 30 minutes before to 10 after.
    log_routing = next(e for e in events if e["kind"] == "routing" and e["tool"] == "log")
    assert log_routing["input"]["start_time"] == "2026-08-18T08:42:00Z"
    assert log_routing["input"]["end_time"] == "2026-08-18T09:22:00Z"
    assert "2026-08-18T08:42:00Z" in llms["log"].calls[0][1].content


def test_a_specialist_cannot_reach_around_its_role():
    # The log agent only owns log_search. A call to another tool is refused as an error
    # observation instead of being executed, so the specialisation is enforced, not advisory.
    llms = scripted_alrt_001()
    llms["log"] = FakeLLM([
        turn("Let me check the CMDB too.", ("cmdb_lookup", {"component_name": "payments-db"}),
             ("log_search", {"service": "payments-db", **WINDOW_001, "level": "ERROR"})),
        report("payments-db: LOCAL. INVESTIGATE NEXT: none"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    notes = [e["content"] for e in events if e["kind"] == "note"]
    assert any("cmdb_lookup is not one of this specialist's tools" in n for n in notes)
    # Only log_search actually ran for the log agent.
    assert [e["tool"] for e in events if e["kind"] == "observation"] == [
        "cmdb_lookup", "log_search", "similar_incidents_search",
    ]
    refusal = [m for m in llms["log"].calls[1] if isinstance(m, ToolMessage) and m.status == "error"]
    assert refusal and "not available to you" in refusal[0].content
    assert result.diagnosis.affected_component == "payments-db"
    assert result.tool_calls == 3  # the refused call is not counted as an investigation


def test_synthesis_without_a_package_returns_to_the_orchestrator():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
        route("synthesis", text="It did not submit; try again."),
    ])
    llms["synthesis"] = FakeLLM([
        turn("", ("similar_incidents_search", {"query": "payments-db connection pool exhausted"})),
        report("I am not confident enough yet."),  # ends its run with no submit_diagnosis
        turn("Submitting now.", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
    ])

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms)

    assert result.diagnosis.affected_component == "payments-db"


# ── Guardrail checks under the multi-agent flow ──────────────────────────

def test_origin_check_requires_searching_dependencies_the_logs_blame():
    # Reproduces the live ALRT-003 failure: the log agent read auth-service's logs ("Redis
    # connection refused (redis-cache:6379)") and the synthesis agent submitted auth-service
    # as the origin, without anyone looking at redis-cache, where the actual OOM crash is.
    window = {"start_time": "2026-08-20T03:00:00Z", "end_time": "2026-08-20T03:30:00Z"}
    stops_one_hop_short = {
        "summary": "Authentication is down across api-gateway.",
        "affected_component": "auth-service",
        "severity_assessed": "critical",
        "critical_dependencies": ["api-gateway"],
        "log_evidence": ["2026-08-20T03:15:10Z auth-service: Redis connection refused"],
        "root_cause_hypothesis": "auth-service cannot reach Redis.",
        "confidence": 0.9,
        "escalation_recommendation": "Page security-team.",
        "similar_incidents": ["INC-2025-156"],
    }
    llms = agents(
        orchestrator=[
            route("log", "auth-service", window=window),
            route("synthesis"),
            route("log", "redis-cache", "user-db", window=window,
                  text="The guardrail says redis-cache and user-db are unsearched; do that."),
            route("synthesis"),
        ],
        log=[
            turn("", ("log_search", {"service": "auth-service", **window})),
            report("SERVICE: auth-service\nVERDICT: RELAY\nBLAMES: redis-cache\nINVESTIGATE NEXT: redis-cache, user-db"),
            turn("", ("log_search", {"service": "redis-cache", **window}),
                 ("log_search", {"service": "user-db", **window})),
            report("SERVICE: redis-cache\nVERDICT: LOCAL (OOM crash)\nuser-db: HEALTHY\nINVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "redis-cache OOM crash, auth-service outage"})),
            turn("", (SUBMIT_TOOL_NAME, stops_one_hop_short)),
            turn("", (SUBMIT_TOOL_NAME, {**stops_one_hop_short, "affected_component": "redis-cache",
                                         "critical_dependencies": ["auth-service", "api-gateway"]})),
        ],
    )
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-003"), llms=llms, on_event=events.append)

    rejection = next(e for e in events if e["kind"] == "guardrail")
    assert rejection["is_error"]
    assert "redis-cache" in rejection["content"] and "user-db" in rejection["content"]
    assert result.diagnosis.affected_component == "redis-cache"


def test_origin_check_does_not_fire_when_the_origin_blames_no_dependency():
    # payments-db has no upstream dependencies, so ALRT-001's normal flow is unaffected.
    result = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001())
    assert result.steps == 9


def test_submit_without_similar_incidents_search_is_rejected():
    # Reproduces live ALRT-001/ALRT-004: the synthesis agent submitted without ever calling
    # similar_incidents_search, so the matching past incident was never cited.
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
        route("synthesis"),
    ])
    llms["synthesis"] = FakeLLM([
        turn("Submitting straight away.", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
        turn("", ("similar_incidents_search", {"query": "payments-db connection pool exhausted"})),
        turn("Resubmitting.", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    guardrail = [e for e in events if e["kind"] == "guardrail"]
    assert guardrail[0]["is_error"] and "similar_incidents_search" in guardrail[0]["content"]
    assert not guardrail[1]["is_error"]
    assert result.diagnosis.similar_incidents == ["INC-2025-114"]


def test_alerted_service_is_rejected_until_a_dependency_is_investigated():
    # Reproduces the live ALRT-005 failure: web-frontend (the service that fired the alert)
    # is blamed after reading only its own logs, which describe symptoms without naming any
    # dependency -- so the origin check stays silent. The real cause is auth-service, two
    # hops upstream via api-gateway.
    window = {"start_time": "2026-08-21T07:30:00Z", "end_time": "2026-08-21T08:10:00Z"}
    blames_itself = {
        "summary": "web-frontend is completely unavailable for authenticated users.",
        "affected_component": "web-frontend",
        "severity_assessed": "critical",
        "critical_dependencies": [],
        "log_evidence": ["2026-08-21T07:55:00Z web-frontend: Site completely unavailable"],
        "root_cause_hypothesis": "The web-frontend service is down.",
        "confidence": 1.0,
        "escalation_recommendation": "Page the web team.",
        "similar_incidents": ["INC-2025-341"],
    }
    reaches_the_origin = {
        **blames_itself,
        "affected_component": "auth-service",
        "critical_dependencies": ["web-frontend", "api-gateway"],
        "root_cause_hypothesis": "auth-service's TLS certificate expired.",
    }
    llms = agents(
        orchestrator=[
            route("log", "web-frontend", window=window),
            route("synthesis"),
            route("log", "api-gateway", "auth-service", window=window,
                  text="The guardrail wants the dependency chain walked."),
            route("synthesis"),
        ],
        log=[
            turn("", ("log_search", {"service": "web-frontend", **window})),
            report("SERVICE: web-frontend\nVERDICT: RELAY\nBLAMES: -\nINVESTIGATE NEXT: api-gateway"),
            turn("", ("log_search", {"service": "api-gateway", **window}),
                 ("log_search", {"service": "auth-service", **window})),
            report("SERVICE: auth-service\nVERDICT: LOCAL (TLS certificate expired)\nINVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "TLS certificate expired, SSL handshake failures"})),
            turn("", (SUBMIT_TOOL_NAME, blames_itself)),
            turn("", (SUBMIT_TOOL_NAME, reaches_the_origin)),
        ],
    )
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-005"), llms=llms, on_event=events.append)

    rejection = next(e for e in events if e["kind"] == "guardrail")
    assert rejection["is_error"]
    assert "alerted service itself (web-frontend)" in rejection["content"]
    assert "api-gateway" in rejection["content"]
    assert result.diagnosis.affected_component == "auth-service"


def test_fabricated_log_evidence_is_rejected():
    # A prompt rule is not a guarantee: on a live ALRT-005 run log_search answered "Unknown
    # service" and the log agent still reported an invented line, which became the
    # diagnosis's evidence. check_package now checks each quote against the log corpus.
    llms = scripted_alrt_001(submit_turns=[
        turn("Submitting.", (SUBMIT_TOOL_NAME, {
            **VALID_PACKAGE,
            "log_evidence": ["2026-08-18T09:09:15Z payments-db: Database exploded spectacularly"],
        })),
        turn("Quoting the real line.", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
    ])
    llms["orchestrator"] = FakeLLM([
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
        route("synthesis"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    rejection = next(e for e in events if e["kind"] == "guardrail" and e["is_error"])
    assert "match no entry in the log corpus" in rejection["content"]
    assert "Database exploded spectacularly" in rejection["content"]
    # A fabricated quote is not a component problem, so the CMDB list is not offered back.
    assert "Valid CMDB components" not in rejection["content"]
    assert result.diagnosis.log_evidence == VALID_PACKAGE["log_evidence"]


def test_a_real_log_line_attributed_to_the_wrong_service_is_rejected():
    # The subtle fabrication: the message is real, but payments-db logged it, not redis-cache.
    # Catching this is why the check requires the entry's own service to be named in the quote.
    package = {**VALID_PACKAGE, "log_evidence": [
        "2026-08-18T09:09:15Z redis-cache: Max connections reached (20/20), rejecting new connection requests",
    ]}
    alert = get_alert("ALRT-001")
    accepted, problems = graph_nodes.check_package(alert, package, steps=5, elapsed=1.0)

    assert accepted is None
    assert any("match no entry in the log corpus" in p for p in problems)


@pytest.mark.parametrize("quote", [
    # Exactly as log_search returns it.
    "2026-08-18T09:09:15Z payments-db: Max connections reached (20/20), rejecting new connection requests",
    # A shortened fragment of a real line.
    "2026-08-20T03:15:10Z auth-service: Redis connection refused",
    # A level tag the prompt never asked for, and a rounded timestamp.
    "2026-08-19T14:33:00Z user-service: [ERROR] OOMKilled",
    # An em dash where the corpus has a hyphen, which models substitute routinely.
    "2026-08-27T01:56:00Z payments-db: Replication lag detected: 4200ms, above 100ms threshold — failover delayed",
])
def test_genuine_citations_are_accepted_in_the_forms_models_actually_produce(quote):
    assert graph_nodes._fabricated_log_evidence([quote]) == []


def test_alerted_service_is_accepted_once_a_dependency_was_investigated():
    # The guard must not fire when the agents did look at a dependency and still, correctly,
    # concluded the alerted service is the origin -- the case for ALRT-002, 004 and 007.
    result = run_diagnosis(get_alert("ALRT-002"), llms=scripted_alrt_002())
    assert result.diagnosis.affected_component == "user-service"
    assert result.diagnosis.escalation_decision == EscalationDecision.AUTO_RESOLVED


# ── Model-level recovery, now inside the specialists ─────────────────────

def test_missing_submit_is_nudged_and_budget_exhaustion_raises():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([route("synthesis"), route("synthesis")])
    llms["synthesis"] = FakeLLM([
        report("I think it is the database."),  # no submit_diagnosis
        report("Still the database."),
    ])
    events: list[dict] = []

    with pytest.raises(DiagnosisError, match="No valid diagnosis package"):
        run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append, max_steps=4)

    notes = [e["content"] for e in events if e["kind"] == "note"]
    assert any("budget" in n for n in notes)


def test_duplicate_tool_call_ids_within_one_turn_are_deduplicated():
    # Reproduces an observed codestral-latest quirk: two parallel tool calls in the same
    # turn sharing one id. Mistral's API rejects that message outright, so the specialist
    # must repair it before its next request goes out.
    clash = turn(
        "Checking two services at once.",
        ("log_search", {"service": "payments-db", **WINDOW_001}),
        ("log_search", {"service": "order-service", **WINDOW_001}),
    )
    clash.tool_calls[1]["id"] = clash.tool_calls[0]["id"]

    llms = scripted_alrt_001()
    llms["log"] = FakeLLM([clash, report("payments-db: LOCAL. INVESTIGATE NEXT: none")])

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms)

    second_request = llms["log"].calls[1]
    tool_messages = [m for m in second_request if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2
    assert len({m.tool_call_id for m in tool_messages}) == 2  # no longer clashing
    assert result.diagnosis.affected_component == "payments-db"


def test_duplicate_tool_call_ids_across_agents_are_deduplicated():
    # used_tool_call_ids is shared across the whole run, so an id the CMDB agent already
    # used cannot be reused by the log agent either.
    llms = scripted_alrt_001()
    cmdb_first = turn("Start.", ("cmdb_lookup", {"component_name": "checkout-api"}))
    log_first = turn("Next.", ("log_search", {"service": "payments-db", **WINDOW_001}))
    log_first.tool_calls[0]["id"] = cmdb_first.tool_calls[0]["id"]  # reuse across agents
    llms["cmdb"] = FakeLLM([cmdb_first, report("INVESTIGATE NEXT: payments-db")])
    llms["log"] = FakeLLM([log_first, report("payments-db: LOCAL. INVESTIGATE NEXT: none")])

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms)

    cmdb_id = [m for m in llms["cmdb"].calls[1] if isinstance(m, ToolMessage)][0].tool_call_id
    log_id = [m for m in llms["log"].calls[1] if isinstance(m, ToolMessage)][0].tool_call_id
    assert cmdb_id != log_id
    assert result.diagnosis.affected_component == "payments-db"


def test_rate_limit_is_retried_then_succeeds():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([
        http_error(429),
        route("cmdb", "checkout-api"),
        route("log", "payments-db", window=WINDOW_001),
        route("synthesis"),
    ])
    result = run_diagnosis(get_alert("ALRT-001"), llms=llms)
    assert result.diagnosis.affected_component == "payments-db"


def test_invalid_api_key_becomes_diagnosis_error():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([http_error(401)])
    with pytest.raises(DiagnosisError, match="API key"):
        run_diagnosis(get_alert("ALRT-001"), llms=llms)


def test_missing_api_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(DiagnosisError, match="MISTRAL_API_KEY"):
        run_diagnosis(get_alert("ALRT-001"))


def test_a_partial_set_of_agent_models_still_needs_a_default(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(DiagnosisError, match="MISTRAL_API_KEY"):
        run_diagnosis(get_alert("ALRT-001"), llms={"orchestrator": FakeLLM([])})


def test_unknown_agent_name_in_llms_is_rejected():
    with pytest.raises(DiagnosisError, match="Unknown agent"):
        run_diagnosis(get_alert("ALRT-001"), llm=FakeLLM([]), llms={"triage": FakeLLM([])})


def test_one_model_can_serve_every_agent():
    # How production runs: a single Mistral handle, bound four times with four tool sets.
    # The turns then interleave in graph order.
    single = FakeLLM([
        route("cmdb", "checkout-api"),
        turn("", ("cmdb_lookup", {"component_name": "checkout-api"})),
        report("INVESTIGATE NEXT: payments-db"),
        route("log", "payments-db", window=WINDOW_001),
        turn("", ("log_search", {"service": "payments-db", **WINDOW_001, "level": "ERROR"})),
        report("payments-db: LOCAL. INVESTIGATE NEXT: none"),
        route("synthesis"),
        turn("", ("similar_incidents_search", {"query": "payments-db connection pool exhausted"})),
        turn("", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
    ])

    result = run_diagnosis(get_alert("ALRT-001"), llm=single)

    assert result.diagnosis.affected_component == "payments-db"
    assert result.steps == 9
    # Bound last by the synthesis agent, since all four share the handle.
    assert [t["function"]["name"] for t in single.bound_tools] == ["similar_incidents_search", SUBMIT_TOOL_NAME]


def test_truncated_response_is_discarded_and_the_run_recovers():
    # Reproduces the codestral runaway generation seen on ALRT-005: a response cut off at
    # max_tokens must not end the run or pollute the history with partial tool calls.
    runaway = turn("x" * 50, ("cmdb_lookup", {"component_name": "api-ga"}), finish_reason="length")
    llms = scripted_alrt_001()
    llms["cmdb"] = FakeLLM([
        runaway,
        turn("Looking up the alerted service.", ("cmdb_lookup", {"component_name": "checkout-api"})),
        report("INVESTIGATE NEXT: payments-db"),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llms=llms, on_event=events.append)

    assert result.diagnosis.affected_component == "payments-db"
    assert any("cut off" in e.get("content", "") for e in events if e["kind"] == "note")
    second_request = llms["cmdb"].calls[1]
    assert runaway not in second_request
    assert "too long" in str(second_request[-1].content)


def test_repeated_truncation_gives_up():
    llms = scripted_alrt_001()
    llms["orchestrator"] = FakeLLM([turn("partial", finish_reason="length") for _ in range(4)])
    with pytest.raises(DiagnosisError, match="cut off"):
        run_diagnosis(get_alert("ALRT-001"), llms=llms)


# ── Escalation + human-in-the-loop ───────────────────────────────────────

def test_urgent_escalation_pauses_the_graph_for_approval(in_memory_graph):
    # payments-db has two dependents (checkout-api, order-service): blast radius → urgent.
    events: list[dict] = []
    result = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001(), on_event=events.append)

    d = result.diagnosis
    assert result.needs_approval
    assert d.escalation_decision == EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL
    assert d.owner_team == "payments-team"
    assert d.human_decision is None
    assert result.approval_request["component"] == "payments-db"
    assert result.approval_request["reason"] == d.escalation_reason
    assert any(e["kind"] == "escalation" for e in events)
    assert in_memory_graph.get_state({"configurable": {"thread_id": result.thread_id}}).next == ("human_approval",)


def test_approve_resumes_and_keeps_the_escalation_urgent():
    paused = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001())
    events: list[dict] = []

    result = resume_diagnosis(paused.thread_id, "approve", note="Paging payments-team now", on_event=events.append)

    d = result.diagnosis
    assert not result.needs_approval
    assert d.escalation_decision == EscalationDecision.ESCALATE_URGENT_NEEDS_APPROVAL
    assert d.human_decision == "approve"
    assert d.human_decision_note == "Paging payments-team now"
    assert [e["kind"] for e in events] == ["human_decision"]
    assert result.trace[:-1] == paused.trace  # the pre-pause trace is kept
    assert result.steps == paused.steps and result.elapsed_seconds == paused.elapsed_seconds


def test_downgrade_resumes_as_normal_escalation_without_touching_the_diagnosis():
    paused = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001())

    result = resume_diagnosis(paused.thread_id, "downgrade", note="Known load test")

    d = result.diagnosis
    assert d.escalation_decision == EscalationDecision.ESCALATE_NORMAL
    assert d.human_decision == "downgrade"
    assert d.escalation_reason == paused.diagnosis.escalation_reason
    assert d.root_cause_hypothesis == paused.diagnosis.root_cause_hypothesis
    assert d.affected_component == paused.diagnosis.affected_component


def test_resume_rejects_unknown_finished_or_invalid_runs():
    with pytest.raises(DiagnosisError, match="No run found"):
        resume_diagnosis("ALRT-001-missing", "approve")

    paused = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001())
    with pytest.raises(DiagnosisError, match="Unknown decision"):
        resume_diagnosis(paused.thread_id, "reject")

    resume_diagnosis(paused.thread_id, "approve")
    with pytest.raises(DiagnosisError, match="not waiting"):
        resume_diagnosis(paused.thread_id, "approve")


def test_non_urgent_diagnosis_finishes_without_pausing(in_memory_graph):
    result = run_diagnosis(get_alert("ALRT-002"), llms=scripted_alrt_002())

    assert not result.needs_approval
    assert result.diagnosis.escalation_decision == EscalationDecision.AUTO_RESOLVED
    assert result.diagnosis.owner_team == "identity-team"
    assert in_memory_graph.get_state({"configurable": {"thread_id": result.thread_id}}).next == ()


def test_paused_run_resumes_from_sqlite_in_a_fresh_graph(tmp_path, monkeypatch):
    # Simulates `run_cli.py ALRT-001` pausing, then `run_cli.py --resume ...` in a new process.
    db = tmp_path / "rootly.sqlite"
    first = rootly_graph.build_graph(SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
    monkeypatch.setattr(rootly_graph, "get_graph", lambda: first)
    paused = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001())
    assert paused.needs_approval

    second = rootly_graph.build_graph(SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
    monkeypatch.setattr(rootly_graph, "get_graph", lambda: second)
    result = resume_diagnosis(paused.thread_id, "downgrade")

    assert result.diagnosis.escalation_decision == EscalationDecision.ESCALATE_NORMAL
    assert result.diagnosis.affected_component == "payments-db"
    assert len(result.trace) == len(paused.trace) + 1


def test_existing_thread_id_cannot_be_restarted():
    paused = run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001())
    with pytest.raises(DiagnosisError, match="already exists"):
        run_diagnosis(get_alert("ALRT-001"), llms=scripted_alrt_001(), thread_id=paused.thread_id)


# ── Transitive guardrail (docs/MOCK_DATA_README.md, "Guardrail-ul tranzitiv") ────────────
#
# _unexamined_blamed_dependencies only checks whether the *submitted* component's own logs
# blame an uninvestigated dependency. That leaves a gap: the agents can jump straight to a
# leaf component several hops from the alert (e.g. payments-db from a web-frontend alert)
# and, if that leaf has no dependencies of its own to blame, the origin check never fires.
# _unreachable_investigated_path closes it by requiring a fully-investigated path from
# alert.service to affected_component through the CMDB depends_on graph.

def test_transitive_guardrail_leaves_the_original_scenarios_unaffected():
    # ALRT-001/002/003 already run unchanged in the tests above (1 hop and 0 hops, both ends
    # exempt from the reachability check). This covers the remaining two: another 0-hop
    # self-origin (ALRT-004) and a 2-hop chain through a single intermediate (ALRT-005).
    window_004 = {"start_time": "2026-08-21T11:15:00Z", "end_time": "2026-08-21T11:55:00Z"}
    package_004 = {
        "summary": "order-service payment verification queries are timing out after a config "
                   "change lowered the payments-db query timeout to 50ms.",
        "affected_component": "order-service",
        "severity_assessed": "high",
        "critical_dependencies": ["payments-db"],
        "log_evidence": ["2026-08-21T11:39:00Z order-service: Query timeout to payments-db after 50ms for order ORD-9103 (complex join query)"],
        "root_cause_hypothesis": "payments_db_timeout_ms was dropped from 5000 to 50, too aggressive for "
                                  "complex join queries; payments-db itself is healthy.",
        "confidence": 0.85,
        "escalation_recommendation": "Page order-team: revert payments_db_timeout_ms to 5000.",
        "similar_incidents": ["INC-2025-278"],
    }
    llms_004 = agents(
        orchestrator=[
            route("log", "order-service", window=window_004),
            route("log", "payments-db", window=window_004),
            route("synthesis"),
        ],
        log=[
            turn("", ("log_search", {"service": "order-service", **window_004, "level": "ERROR"})),
            report("SERVICE: order-service\nVERDICT: LOCAL (config change)\nINVESTIGATE NEXT: payments-db"),
            turn("", ("log_search", {"service": "payments-db", **window_004})),
            report("SERVICE: payments-db\nVERDICT: HEALTHY\nINVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "order-service payments-db timeout after config change"})),
            turn("", (SUBMIT_TOOL_NAME, package_004)),
        ],
    )
    result_004 = run_diagnosis(get_alert("ALRT-004"), llms=llms_004)
    assert result_004.diagnosis.affected_component == "order-service"

    window_005 = {"start_time": "2026-08-22T15:45:00Z", "end_time": "2026-08-22T16:15:00Z"}
    package_005 = {
        "summary": "auth-service's TLS certificate expired, rejecting all inbound HTTPS connections "
                   "and cascading through api-gateway to web-frontend.",
        "affected_component": "auth-service",
        "severity_assessed": "critical",
        "critical_dependencies": ["api-gateway"],
        "log_evidence": ["2026-08-22T16:05:30Z auth-service: TLS certificate expired at 16:00:00Z, all inbound HTTPS connections rejected"],
        "root_cause_hypothesis": "auth-service's TLS certificate expired at 16:00:00Z; every inbound HTTPS "
                                  "connection is rejected, cascading to a full outage.",
        "confidence": 0.9,
        "escalation_recommendation": "Page security-team: renew the auth-service TLS certificate immediately.",
        "similar_incidents": ["INC-2025-341"],
    }
    llms_005 = agents(
        orchestrator=[
            route("cmdb", "web-frontend"),
            route("log", "api-gateway", "auth-service", window=window_005),
            route("synthesis"),
        ],
        cmdb=[
            turn("", ("cmdb_lookup", {"component_name": "web-frontend"})),
            report("web-frontend depends_on api-gateway.\nINVESTIGATE NEXT: api-gateway"),
        ],
        log=[
            turn("", ("log_search", {"service": "api-gateway", **window_005}),
                 ("log_search", {"service": "auth-service", **window_005})),
            report("api-gateway: RELAY. auth-service: LOCAL (TLS certificate expired).\nINVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "TLS certificate expired, auth-service outage"})),
            turn("", (SUBMIT_TOOL_NAME, package_005)),
        ],
    )
    result_005 = run_diagnosis(get_alert("ALRT-005"), llms=llms_005)
    assert result_005.diagnosis.affected_component == "auth-service"


def test_transitive_guardrail_rejects_a_shortcut_then_accepts_the_order_service_path():
    # ALRT-009 (web-frontend, 3 hops to payments-db) is the scenario built specifically for
    # this guardrail. A submit that jumps straight to payments-db without anyone investigating
    # the intermediate hop (api-gateway) must be rejected, even though payments-db's own logs
    # look guilty on their own (it has no dependencies to blame, so the origin check alone
    # would let it through).
    window = {"start_time": "2026-08-27T01:45:00Z", "end_time": "2026-08-27T02:10:00Z"}
    shortcut_package = {
        "summary": "payments-db failover took 12 minutes instead of the expected 30 seconds during a "
                   "scheduled maintenance window.",
        "affected_component": "payments-db",
        "severity_assessed": "high",
        "critical_dependencies": ["web-frontend"],
        "log_evidence": ["2026-08-27T01:56:00Z payments-db: Replication lag detected: 4200ms, above 100ms threshold - failover delayed"],
        "root_cause_hypothesis": "Scheduled payments-db failover suffered abnormal replication lag.",
        "confidence": 0.9,
        "escalation_recommendation": "Page payments-team: investigate replication lag during failovers.",
        "similar_incidents": ["INC-2024-445"],
    }
    via_order_service = {
        **shortcut_package,
        "critical_dependencies": ["api-gateway", "order-service"],
        "summary": "payments-db failover took 12 minutes instead of the expected 30 seconds; the same "
                   "failure propagated to web-frontend through api-gateway and order-service.",
    }
    llms = agents(
        orchestrator=[
            route("log", "web-frontend", "payments-db", window=window),
            route("synthesis"),
            route("log", "api-gateway", "order-service", window=window,
                  text="The guardrail wants the intermediate hops investigated."),
            route("synthesis"),
        ],
        log=[
            turn("", ("log_search", {"service": "web-frontend", **window}),
                 ("log_search", {"service": "payments-db", **window})),
            report("web-frontend: RELAY. payments-db: LOCAL (replication lag).\nINVESTIGATE NEXT: none"),
            turn("", ("log_search", {"service": "api-gateway", **window}),
                 ("log_search", {"service": "order-service", **window})),
            report("api-gateway and order-service both RELAY the payments-db failover.\nINVESTIGATE NEXT: none"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "payments-db failover replication lag"})),
            turn("Submitting straight to payments-db.", (SUBMIT_TOOL_NAME, shortcut_package)),
            turn("Resubmitting with the investigated path.", (SUBMIT_TOOL_NAME, via_order_service)),
        ],
    )
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-009"), llms=llms, on_event=events.append)

    rejections = [e for e in events if e["kind"] == "guardrail" and e["is_error"]]
    assert len(rejections) == 1
    assert "No fully-investigated path" in rejections[0]["content"]
    assert "api-gateway" in rejections[0]["content"]
    assert result.diagnosis.affected_component == "payments-db"
    assert result.diagnosis.critical_dependencies == ["api-gateway", "order-service"]


def test_transitive_guardrail_accepts_the_checkout_api_path():
    # The other of the two valid investigated paths to payments-db (via checkout-api instead
    # of order-service) must also be accepted: the guardrail checks reachability, not one
    # specific "correct" hop.
    window = {"start_time": "2026-08-27T01:45:00Z", "end_time": "2026-08-27T02:10:00Z"}
    package = {
        "summary": "payments-db failover took 12 minutes instead of the expected 30 seconds; "
                   "checkout-api's writes failed during the window.",
        "affected_component": "payments-db",
        "severity_assessed": "high",
        "critical_dependencies": ["api-gateway", "checkout-api"],
        "log_evidence": ["2026-08-27T01:57:00Z checkout-api: Write query to payments-db failed: no primary available during failover"],
        "root_cause_hypothesis": "Scheduled payments-db failover suffered abnormal replication lag, breaking checkout-api's writes.",
        "confidence": 0.9,
        "escalation_recommendation": "Page payments-team: investigate replication lag during failovers.",
        "similar_incidents": ["INC-2024-445"],
    }
    llms = agents(
        orchestrator=[
            route("log", "api-gateway", "checkout-api", window=window),
            route("synthesis"),
        ],
        log=[
            turn("", ("log_search", {"service": "api-gateway", **window}),
                 ("log_search", {"service": "checkout-api", **window})),
            report("Both RELAY a payments-db failover.\nINVESTIGATE NEXT: payments-db"),
        ],
        synthesis=[
            turn("", ("similar_incidents_search", {"query": "payments-db failover replication lag"})),
            turn("", (SUBMIT_TOOL_NAME, package)),
        ],
    )

    result = run_diagnosis(get_alert("ALRT-009"), llms=llms)

    assert result.diagnosis.affected_component == "payments-db"
    assert result.diagnosis.critical_dependencies == ["api-gateway", "checkout-api"]


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("MISTRAL_API_KEY"), reason="requires MISTRAL_API_KEY")
def test_scenario_1_end_to_end():
    result = run_diagnosis(get_alert("ALRT-001"))
    d = result.diagnosis
    assert d.affected_component in {"payments-db", "checkout-api"}
    assert d.confidence > 0.5
    assert d.log_evidence
    assert "INC-2025-114" in d.similar_incidents
    assert d.escalation_decision is not None
    assert result.steps < graph_nodes.DEFAULT_MAX_STEPS
