"""Tests for src.agent.graph_state.assert_state_invariants (see the "who writes what"
table and RootlyState docstring in src/agent/graph_state.py)."""

from __future__ import annotations

import pytest

from src.agent.graph_state import assert_state_invariants

BASE_STATE = {
    "step_count": 3,
    "max_steps": 15,
    "tool_calls": 2,
    "used_tool_call_ids": ["a1b2c3d4e", "f6g7h8i9j"],
    "diagnosis": None,
    "elapsed_seconds": None,
}


def test_valid_in_progress_state_passes():
    assert_state_invariants(BASE_STATE)


def test_valid_completed_state_passes():
    completed = {**BASE_STATE, "diagnosis": {"affected_component": "redis-cache"}, "elapsed_seconds": 12.3}
    assert_state_invariants(completed)


def test_step_count_over_max_steps_fails():
    with pytest.raises(AssertionError, match="step_count"):
        assert_state_invariants({**BASE_STATE, "step_count": 16})


def test_duplicate_tool_call_ids_fail():
    with pytest.raises(AssertionError, match="used_tool_call_ids"):
        assert_state_invariants({**BASE_STATE, "used_tool_call_ids": ["dup00001", "dup00001"]})


def test_diagnosis_without_frozen_elapsed_seconds_fails():
    with pytest.raises(AssertionError, match="elapsed_seconds"):
        assert_state_invariants({**BASE_STATE, "diagnosis": {"affected_component": "redis-cache"}, "elapsed_seconds": None})


def test_negative_tool_calls_fails():
    with pytest.raises(AssertionError, match="tool_calls"):
        assert_state_invariants({**BASE_STATE, "tool_calls": -1})


def test_empty_state_passes():
    # A freshly-initialized state before any node has run yet.
    assert_state_invariants({})
