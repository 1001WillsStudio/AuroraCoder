"""Unit tests for :mod:`src.code_tools.grep_search`.

QA focus: a thin subprocess wrapper around GNU grep. We verify the exact
command construction (security-relevant: it scopes the search to
``WORKSPACE`` and flags cannot inject positional args) and the tool's
contract for success/empty/timeout/missing-binary/truncation paths — all
hermetically through the conftest ``FakeSubprocess`` seam (no real exec).
"""
import subprocess

import pytest

from src.code_tools import grep_search


@pytest.fixture
def ws(tmp_workspace, monkeypatch):
    """Redirect the module-level WORKSPACE constant into the tmp workspace."""
    monkeypatch.setattr(grep_search, "WORKSPACE", tmp_workspace)
    return tmp_workspace


# --------------------------------------------------------------------------- command building
def test_build_cmd_default_case_sensitive(ws):
    cmd = grep_search._build_grep_cmd("foo", include_pattern="*.py",
                                      exclude_pattern="tests", case_sensitive=True)
    assert cmd == ["grep", "-rnI", "--color=never",
                   "--include", "*.py", "--exclude", "tests",
                   "foo", str(ws)]


def test_build_cmd_case_insensitive_adds_i_flag(ws):
    cmd = grep_search._build_grep_cmd("foo", None, None, False)
    assert "-i" in cmd
    assert cmd[-2:] == ["foo", str(ws)]


def test_build_cmd_minimal(ws):
    cmd = grep_search._build_grep_cmd("needle", None, None, True)
    assert cmd == ["grep", "-rnI", "--color=never", "needle", str(ws)]
    # no include/exclude flags emitted when omitted
    assert "--include" not in cmd and "--exclude" not in cmd


# --------------------------------------------------------------------------- tool execution paths
@pytest.fixture
def fake_grep(ws, fake_subprocess, monkeypatch):
    """Install the FakeSubprocess seam. Tests register their own canned grep
    outputs (matchers are FIFO, so a fixture-default would shadow per-test
    handlers — we deliberately register nothing here)."""
    fake_subprocess.install(monkeypatch)
    return fake_subprocess


def test_grep_success_returns_matches(ws, fake_grep):
    fake_grep.register(predicate=lambda a: a[0] == "grep",
                       stdout="a.py:1:foo\nb.py:5:foo", returncode=0)
    out = grep_search.grep_search_tool("foo")
    assert "Search results for pattern: foo" in out
    assert "a.py:1:foo" in out and "b.py:5:foo" in out
    assert hasattr(fake_grep, "calls") and fake_grep.calls  # exec'd once


def test_grep_empty_stdout_reports_no_matches(ws, fake_grep):
    fake_grep.register(predicate=lambda a: a[0] == "grep", stdout="", returncode=0)
    out = grep_search.grep_search_tool("ZZZ-NOMATCH")
    assert "No matches found for pattern: ZZZ-NOMATCH" in out


def test_grep_timeout_is_handled_not_raised(ws, fake_grep):
    fake_grep.register(predicate=lambda a: a[0] == "grep",
                       side_effect=subprocess.TimeoutExpired(["grep"], 15))
    out = grep_search.grep_search_tool("slow")
    assert "timed out" in out and "slow" in out


def test_grep_missing_binary_is_handled(ws, fake_grep):
    fake_grep.register(predicate=lambda a: a[0] == "grep",
                       side_effect=FileNotFoundError())
    out = grep_search.grep_search_tool("x")
    assert "grep binary not found" in out


def test_grep_truncates_to_max_lines(ws, fake_grep):
    lines = "\n".join(f"f{i}.py:{i}:foo" for i in range(250))
    fake_grep.register(predicate=lambda a: a[0] == "grep", stdout=lines)
    out = grep_search.grep_search_tool("foo", max_lines=10)
    assert "truncated to 10 lines" in out
    assert "250 total matches" in out