"""Unit tests for :mod:`src.tool_executor` — pure partition / guard logic.

QA focus: the executor decides which tool calls can run concurrently and
guards against two edits to the same file in one turn. These decisions are
the deterministic core of the agent loop; we unit-test them directly without
spinning up any real tool handler (the heavy ``execute_tool_call`` surface is
covered by integration/behavioral tests elsewhere).
"""
import json

import pytest

from src import tool_executor
from src.tool_executor import (
    MAX_TOOL_CONCURRENCY,
    PARALLEL_SAFE_TOOLS,
    _check_same_file_edit_guard,
    _get_max_tool_concurrency,
    partition_tool_calls,
)


def _tc(name, args=None):
    """Build an OpenAI-shaped tool_call dict."""
    return {"function": {"name": name, "arguments": json.dumps(args or {})}}


# --------------------------------------------------------------------------- partitioning
def test_partition_empty_input():
    assert partition_tool_calls([]) == []


def test_partition_all_parallel_safe_collapses_into_one_batch():
    safe = PARALLEL_SAFE_TOOLS
    assert safe, "expected at least one parallel-safe tool"
    calls = [_tc(name) for name in safe]
    batches = partition_tool_calls(calls)
    assert len(batches) == 1
    parallel, group = batches[0]
    assert parallel is True
    assert len(group) == len(safe)


def test_partition_all_serial_still_one_batch_marked_serial():
    # edit_file is not in PARALLEL_SAFE_TOOLS
    assert "edit_file" not in PARALLEL_SAFE_TOOLS
    calls = [_tc("edit_file", {"file": "a.py"})]
    batches = partition_tool_calls(calls)
    assert len(batches) == 1
    parallel, group = batches[0]
    assert parallel is False and len(group) == 1


def test_partition_switches_batch_on_first_serial_call():
    # two safe, then one serial, then one safe -> [safe,safe] / [serial] / [safe]
    calls = [_tc("read_file"), _tc("google_search"),
             _tc("edit_file", {"file": "z.py"}),
             _tc("read_file")]
    batches = partition_tool_calls(calls)
    flags = [par for par, _ in batches]
    assert flags == [True, False, True]
    _, g0 = batches[0]
    _, g1 = batches[1]
    _, g2 = batches[2]
    assert [c["function"]["name"] for c in g0] == ["read_file", "google_search"]
    assert [c["function"]["name"] for c in g1] == ["edit_file"]
    assert [c["function"]["name"] for c in g2] == ["read_file"]


def test_partition_consecutive_serial_calls_share_one_batch():
    # consecutive serial calls are grouped into a single serial batch
    calls = [_tc("edit_file", {"file": "a.py"}),
             _tc("edit_file", {"file": "b.py"})]
    batches = partition_tool_calls(calls)
    assert len(batches) == 1
    parallel, group = batches[0]
    assert parallel is False
    assert [c["function"]["name"] for c in group] == ["edit_file", "edit_file"]


# --------------------------------------------------------------------------- same-file guard
def test_guard_non_edit_tool_is_permissive():
    tc = _tc("read_file", {"file": "a.py"})
    files = set()
    reason = _check_same_file_edit_guard(tc, files)
    assert reason is None
    # non-edit must not register any file
    assert files == set()


def test_guard_register_new_edit_file_returns_none():
    tc = _tc("edit_file", {"file": "src/a.py"})
    files = set()
    assert _check_same_file_edit_guard(tc, files) is None
    assert files == {"src/a.py"}


def test_guard_blocks_repeat_edit_of_same_file():
    tc = _tc("edit_file", {"file": "a.py"})
    files = {"a.py"}
    reason = _check_same_file_edit_guard(tc, files)
    assert reason is not None and "a.py" in reason


def test_guard_tolerates_missing_file_argument():
    tc = _tc("edit_file")  # no arguments dict / no file
    files = {"a.py"}
    # No file key -> nothing to guard against; permissive, no registration
    assert _check_same_file_edit_guard(tc, files) is None
    assert files == {"a.py"}


def test_guard_ignores_unrecognized_file_keys():
    # Guard reads only the "file" key; alternative spellings are permissive
    # and register nothing (no false rejection, no spurious capture).
    tc = _tc("edit_file", {"target_file": "c.py"})
    files = set()
    assert _check_same_file_edit_guard(tc, files) is None
    assert files == set()


def test_guard_swallows_malformed_arguments_json():
    tc = {"function": {"name": "edit_file", "arguments": "{ not json "}}
    files = {"a.py"}
    # Malformed args -> can't parse -> permissive, must not raise
    assert _check_same_file_edit_guard(tc, files) is None
    assert files == {"a.py"}


# --------------------------------------------------------------------------- concurrency env knob
def test_max_concurrency_defaults_to_constant(monkeypatch):
    monkeypatch.delenv("MAX_TOOL_CONCURRENCY", raising=False)
    assert _get_max_tool_concurrency() == MAX_TOOL_CONCURRENCY


def test_max_concurrency_respects_env(monkeypatch):
    monkeypatch.setenv("MAX_TOOL_CONCURRENCY", "3")
    assert _get_max_tool_concurrency() == 3


def test_max_concurrency_ignores_invalid_env(monkeypatch):
    for bad in ("0", "-2", "abc", ""):
        monkeypatch.setenv("MAX_TOOL_CONCURRENCY", bad)
        assert _get_max_tool_concurrency() == MAX_TOOL_CONCURRENCY, bad