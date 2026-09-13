"""
Agent tests.

Offline tests drive the real ReAct loop, real tools and real guardrail with a scripted
fake chat model, so the orchestration logic is verified without an API key.
The live test calls the real Mistral API and is skipped unless MISTRAL_API_KEY is set.
"""

from __future__ import annotations

import itertools
import os

import httpx
import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from src.agent import DiagnosisError, run_diagnosis
from src.agent import react_loop
from src.agent.react_loop import SUBMIT_TOOL_NAME
from src.agent.system_prompt import format_alert
from src.data_loader import get_alert

_ids = itertools.count(1)


def turn(text: str = "", *calls: tuple[str, dict], finish_reason: str = "tool_calls") -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[{"name": name, "args": args, "id": f"call{next(_ids):05d}", "type": "tool_call"} for name, args in calls],
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        response_metadata={"finish_reason": finish_reason, "model_name": "mistral-large-latest"},
    )


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


def scripted_alrt_001():
    return [
        turn("Start with the CMDB entry for checkout-api.", ("cmdb_lookup", {"component_name": "checkout-api"})),
        turn("It depends on payments-db; check its errors.",
             ("log_search", {"service": "payments-db", "start_time": "2026-08-18T08:55:00Z",
                             "end_time": "2026-08-18T09:14:00Z", "level": "ERROR"})),
        turn("", ("similar_incidents_search", {"query": "checkout-api timeouts, payments-db connection pool exhausted"})),
        turn("Submitting.", (SUBMIT_TOOL_NAME, {**VALID_PACKAGE, "affected_component": "payments-database"})),
        turn("Fixing the component name.", (SUBMIT_TOOL_NAME, VALID_PACKAGE)),
    ]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(react_loop, "RETRY_BASE_SECONDS", 0)


def test_react_loop_runs_tools_applies_guardrail_and_returns_package():
    llm = FakeLLM(scripted_alrt_001())
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llm=llm, on_event=events.append)

    assert result.steps == 5
    assert result.tool_calls == 3
    assert result.model == "mistral-large-latest"
    assert result.diagnosis.alert_id == "ALRT-001"
    assert result.diagnosis.affected_component == "payments-db"
    assert result.diagnosis.critical_dependencies == ["payments-db", "order-service"]
    assert result.usage["input_tokens"] == 500

    observations = [e for e in events if e["kind"] == "observation"]
    assert [o["tool"] for o in observations] == ["cmdb_lookup", "log_search", "similar_incidents_search"]
    assert observations[1]["result"]["total_matches"] == 3  # real log data flowed back to the agent

    guardrail = [e for e in events if e["kind"] == "guardrail"]
    assert guardrail[0]["is_error"] and "payments-database" in guardrail[0]["content"]
    assert not guardrail[1]["is_error"]

    # The rejected submit was reported back to the model as an error ToolMessage.
    rejections = [m for m in llm.calls[-1] if isinstance(m, ToolMessage) and m.status == "error"]
    assert rejections and "rejected" in rejections[-1].content


def test_tools_are_bound_in_function_format_and_system_prompt_comes_first():
    llm = FakeLLM(scripted_alrt_001())
    run_diagnosis(get_alert("ALRT-001"), llm=llm)

    assert [t["function"]["name"] for t in llm.bound_tools] == [
        "cmdb_lookup", "log_search", "similar_incidents_search", SUBMIT_TOOL_NAME,
    ]
    assert all(t["type"] == "function" and "parameters" in t["function"] for t in llm.bound_tools)
    assert isinstance(llm.calls[0][0], SystemMessage)


def test_origin_check_requires_searching_dependencies_the_logs_blame():
    # Reproduces the live ALRT-003 failure: the agent read auth-service's logs ("Redis
    # connection refused (redis-cache:6379)") but submitted auth-service as the origin
    # without ever looking at redis-cache, where the actual OOM crash is logged.
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
    llm = FakeLLM([
        turn("", ("log_search", {"service": "auth-service", **window})),
        turn("", (SUBMIT_TOOL_NAME, stops_one_hop_short)),
        turn("", ("log_search", {"service": "redis-cache", **window}), ("log_search", {"service": "user-db", **window})),
        turn("", (SUBMIT_TOOL_NAME, {**stops_one_hop_short, "affected_component": "redis-cache",
                                     "critical_dependencies": ["auth-service", "api-gateway"]})),
    ])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-003"), llm=llm, on_event=events.append)

    rejection = next(e for e in events if e["kind"] == "guardrail")
    assert rejection["is_error"]
    assert "redis-cache" in rejection["content"] and "user-db" in rejection["content"]
    assert result.diagnosis.affected_component == "redis-cache"


def test_origin_check_does_not_fire_when_the_origin_blames_no_dependency():
    # payments-db has no upstream dependencies, so ALRT-001's normal flow is unaffected.
    result = run_diagnosis(get_alert("ALRT-001"), llm=FakeLLM(scripted_alrt_001()))
    assert result.steps == 5


def test_agent_never_sees_evaluation_taxonomy():
    prompt = format_alert(get_alert("ALRT-003"))
    assert "RC-06" not in prompt and "FM-01" not in prompt and "root_cause_category" not in prompt


def test_missing_submit_is_nudged_and_budget_exhaustion_raises():
    llm = FakeLLM([
        turn("I think it is the database.", finish_reason="stop"),
        turn("", ("cmdb_lookup", {"component_name": "checkout-api"})),
    ])
    events: list[dict] = []
    with pytest.raises(DiagnosisError):
        run_diagnosis(get_alert("ALRT-001"), llm=llm, on_event=events.append, max_steps=2)
    notes = [e["content"] for e in events if e["kind"] == "note"]
    assert any("without submitting" in n for n in notes)
    assert any("budget" in n for n in notes)


def test_duplicate_tool_call_ids_within_one_turn_are_deduplicated():
    # Reproduces an observed codestral-latest quirk: two parallel tool calls in the
    # same turn sharing one id. Mistral's API rejects that message outright, so the
    # loop must repair it before the next request goes out.
    clash = turn(
        "Checking two dependencies at once.",
        ("cmdb_lookup", {"component_name": "payments-db"}),
        ("cmdb_lookup", {"component_name": "auth-service"}),
    )
    clash.tool_calls[1]["id"] = clash.tool_calls[0]["id"]

    llm = FakeLLM([clash, *scripted_alrt_001()[1:]])
    result = run_diagnosis(get_alert("ALRT-001"), llm=llm)

    first_request_after = llm.calls[1]
    tool_messages = [m for m in first_request_after if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2
    assert len({m.tool_call_id for m in tool_messages}) == 2  # no longer clashing
    assert result.diagnosis.affected_component == "payments-db"


def test_duplicate_tool_call_ids_across_turns_are_deduplicated():
    # The full conversation (every prior AIMessage) is resent each request, so a model
    # that reuses the same id in a later turn is just as fatal as reusing it within one.
    step1 = turn("Start.", ("cmdb_lookup", {"component_name": "checkout-api"}))
    step2 = turn("Next.", ("log_search", {"service": "payments-db", "start_time": "2026-08-18T08:55:00Z", "end_time": "2026-08-18T09:14:00Z"}))
    step2.tool_calls[0]["id"] = step1.tool_calls[0]["id"]  # reuse across turns, not within one

    llm = FakeLLM([step1, step2, *scripted_alrt_001()[2:]])
    result = run_diagnosis(get_alert("ALRT-001"), llm=llm)

    # The full history is resent every request, so check the final snapshot (every
    # AIMessage + ToolMessage the model ever saw) rather than concatenating requests.
    final_history = llm.calls[-1]
    tool_call_ids = [m.tool_call_id for m in final_history if isinstance(m, ToolMessage)]
    assert len(tool_call_ids) == len(set(tool_call_ids))  # never collided
    assert result.diagnosis.affected_component == "payments-db"


def test_rate_limit_is_retried_then_succeeds():
    llm = FakeLLM([http_error(429), *scripted_alrt_001()])
    result = run_diagnosis(get_alert("ALRT-001"), llm=llm)
    assert result.diagnosis.affected_component == "payments-db"


def test_invalid_api_key_becomes_diagnosis_error():
    with pytest.raises(DiagnosisError, match="API key"):
        run_diagnosis(get_alert("ALRT-001"), llm=FakeLLM([http_error(401)]))


def test_missing_api_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(DiagnosisError, match="MISTRAL_API_KEY"):
        run_diagnosis(get_alert("ALRT-001"))


def test_truncated_response_is_discarded_and_the_run_recovers():
    # Reproduces the codestral runaway generation seen on ALRT-005: a response cut off at
    # max_tokens must not end the run or pollute the history with partial tool calls.
    runaway = turn("x" * 50, ("cmdb_lookup", {"component_name": "api-ga"}), finish_reason="length")
    llm = FakeLLM([runaway, *scripted_alrt_001()])
    events: list[dict] = []

    result = run_diagnosis(get_alert("ALRT-001"), llm=llm, on_event=events.append)

    assert result.diagnosis.affected_component == "payments-db"
    assert any("cut off" in e.get("content", "") for e in events if e["kind"] == "note")
    second_request = llm.calls[1]
    assert runaway not in second_request
    assert "too long" in str(second_request[-1].content)


def test_repeated_truncation_gives_up():
    llm = FakeLLM([turn("partial", finish_reason="length") for _ in range(4)])
    with pytest.raises(DiagnosisError, match="cut off"):
        run_diagnosis(get_alert("ALRT-001"), llm=llm)


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("MISTRAL_API_KEY"), reason="requires MISTRAL_API_KEY")
def test_scenario_1_end_to_end():
    result = run_diagnosis(get_alert("ALRT-001"))
    d = result.diagnosis
    assert d.affected_component in {"payments-db", "checkout-api"}
    assert d.confidence > 0.5
    assert d.log_evidence
    assert "INC-2025-114" in d.similar_incidents
    assert result.steps < 15
