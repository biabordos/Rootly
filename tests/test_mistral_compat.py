"""
Regression test for the langchain-mistralai 0.2.12 serializer bug worked around
in src/agent/mistral_compat.py: an assistant message with N tool calls was
serialized with each call duplicated N times, which Mistral's API rejects with
400 "Duplicate tool call id in assistant message" as soon as N > 1. This test
calls the real (patched) serializer function directly — no network needed.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from src.agent import mistral_compat


def make_ai_message(n: int) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "log_search", "args": {"service": f"svc-{i}"}, "id": f"id{i:07d}", "type": "tool_call"}
            for i in range(n)
        ],
    )


def test_multi_tool_call_message_is_not_duplicated():
    mistral_compat.apply()
    message = make_ai_message(4)

    serialized = mistral_compat._convert_message_to_mistral_chat_message_fixed(message)

    assert len(serialized["tool_calls"]) == 4
    assert {tc["id"] for tc in serialized["tool_calls"]} == {"id0000000", "id0000001", "id0000002", "id0000003"}


def test_single_tool_call_message_still_works():
    mistral_compat.apply()
    message = make_ai_message(1)

    serialized = mistral_compat._convert_message_to_mistral_chat_message_fixed(message)

    assert len(serialized["tool_calls"]) == 1


def test_apply_is_idempotent_and_patches_the_module():
    import langchain_mistralai.chat_models as chat_models

    mistral_compat.apply()
    mistral_compat.apply()  # must not double-wrap or raise
    assert chat_models._convert_message_to_mistral_chat_message is (
        mistral_compat._convert_message_to_mistral_chat_message_fixed
    )
