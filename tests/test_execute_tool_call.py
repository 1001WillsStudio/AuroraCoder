"""Regression: ``execute_tool_call`` must invoke the mapped tool.

A merge that swapped ``TOOL_FUNCTION_MAP`` for a dynamic ``function_map``
dropped the call + return, so every native tool looked up its handler and
then returned ``None``. Edits, writes, and context-fix propagation all went
silent. These tests lock the dispatch contract without touching the network
or a real workspace.
"""

from src.tool_definitions import execute_tool_call


def test_execute_tool_call_invokes_mapped_function(monkeypatch):
    """The looked-up handler must run and its (result, args) pair is swapped."""
    seen = {}

    def fake_tool(arguments):
        seen["args"] = dict(arguments)
        return "tool-ok", {"file": arguments["file"], "canonical": True}

    monkeypatch.setattr(
        "src.tool_definitions.get_tool_function_map",
        lambda: {"write_file": fake_tool},
    )
    args, result = execute_tool_call(
        "write_file", {"file": "x.py", "content": "hi"}
    )
    assert seen["args"] == {"file": "x.py", "content": "hi"}
    assert result == "tool-ok"
    assert args == {"file": "x.py", "canonical": True}


def test_execute_tool_call_injects_subagent_metadata(monkeypatch):
    """tool_call_id / conversation_id are injected, then stripped from applied args."""
    seen = {}

    def fake_subagent(arguments):
        seen["args"] = dict(arguments)
        return "done", {
            k: v for k, v in arguments.items()
            if k not in ("tool_call_id", "conversation_id")
        }

    monkeypatch.setattr(
        "src.tool_definitions.get_tool_function_map",
        lambda: {"subagent": fake_subagent},
    )
    args, result = execute_tool_call(
        "subagent",
        {"task": "look around"},
        tool_call_id="tc1",
        conversation_id="c1",
    )
    assert seen["args"]["tool_call_id"] == "tc1"
    assert seen["args"]["conversation_id"] == "c1"
    assert "tool_call_id" not in args
    assert "conversation_id" not in args
    assert args["task"] == "look around"
    assert result == "done"


def test_execute_tool_call_none_return_is_error_not_unpack_exception(monkeypatch):
    """A handler that returns None must not leak ``cannot unpack NoneType``."""

    monkeypatch.setattr(
        "src.tool_definitions.get_tool_function_map",
        lambda: {"read_file": lambda arguments: None},
    )
    args, result = execute_tool_call("read_file", {"file": "README.md"})
    assert args == {"file": "README.md"}
    assert "cannot unpack" not in result
    assert "NoneType" not in result
    assert result.startswith("Error")
    assert "read_file" in result
