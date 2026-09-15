"""
Workaround for a bug in langchain-mistralai 0.2.12's message serializer.

`_convert_message_to_mistral_chat_message` (in `langchain_mistralai.chat_models`)
builds the `tool_calls` list for an assistant message with:

    for tool_call in message.tool_calls:
        tool_calls.extend(
            [_format_tool_call_for_mistral(tool_call) for tool_call in message.tool_calls]
        )

The inner list comprehension reuses the outer loop variable name and re-iterates
the *same* `message.tool_calls` list on every outer iteration, instead of
formatting each call once. For a message with N tool calls this emits N*N
entries, with each real tool call duplicated N times — and Mistral's API then
rejects the whole request with 400 "Duplicate tool call id in assistant
message" as soon as any turn makes more than one tool call at once (a normal
thing for this agent: cmdb_lookup often triggers several log_search calls in
parallel). The same shadowing bug affects `invalid_tool_calls`.

This has been confirmed against a real turn with 4 tool calls (verified with
the ids already deduplicated on our side — see graph_nodes._dedupe_tool_call_ids
— so this is not a duplicate-id issue on our end).

Monkeypatching one function is preferable to vendoring the whole 600-line
`chat_models.py` module or pinning to an unreleased fix. Safe to delete once
the upstream bug is fixed in a released version — check
https://github.com/langchain-ai/langchain-mistralai/issues before removing.
"""

from __future__ import annotations

import langchain_mistralai.chat_models as _mistralai_chat_models
from langchain_core.messages import AIMessage, BaseMessage

_original_convert = _mistralai_chat_models._convert_message_to_mistral_chat_message
_format_tool_call = _mistralai_chat_models._format_tool_call_for_mistral
_format_invalid_tool_call = _mistralai_chat_models._format_invalid_tool_call_for_mistral

_PATCHED = False


def _convert_message_to_mistral_chat_message_fixed(message: BaseMessage) -> dict:
    if not (isinstance(message, AIMessage) and (message.tool_calls or message.invalid_tool_calls)):
        return _original_convert(message)

    tool_calls = [_format_tool_call(tc) for tc in message.tool_calls]
    tool_calls += [_format_invalid_tool_call(tc) for tc in message.invalid_tool_calls]

    message_dict: dict = {"role": "assistant", "tool_calls": tool_calls}
    # Assistant message must have either content or tool_calls, not both (see original).
    message_dict["content"] = "" if message.content else message.content
    if "prefix" in message.additional_kwargs:
        message_dict["prefix"] = message.additional_kwargs["prefix"]
    return message_dict


def apply() -> None:
    """Idempotent: safe to call from multiple import sites."""
    global _PATCHED
    if _PATCHED:
        return
    _mistralai_chat_models._convert_message_to_mistral_chat_message = (
        _convert_message_to_mistral_chat_message_fixed
    )
    _PATCHED = True
