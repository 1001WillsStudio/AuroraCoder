import json

from src.code_tools import code_interpreter_panel, file_operations
from src.tool_definitions import execute_tool_call


def test_native_read_range_keeps_numbering_after_an_oversized_line(tmp_path, monkeypatch):
    monkeypatch.setattr(file_operations, "WORKSPACE", tmp_path)
    file = tmp_path / "long.log"
    file.write_text("界" * 70_000 + "\nready\n完成\nlast", encoding="utf-8")

    args, result = execute_tool_call(
        "read_file", {"file": file.name, "start_line": 2, "end_line": 3},
    )

    assert args["start_line"] == 2 and args["end_line"] == 3
    assert result.splitlines() == ["--- long.log ---", "2\tready", "3\t完成"]


def test_excerpts_are_bounded_and_do_not_open_the_whole_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_operations, "WORKSPACE", tmp_path)
    file = tmp_path / "fixture.txt"
    file.write_text("text\n" * 500, encoding="utf-8")
    args, result = execute_tool_call(
        "read_file", {"file": file.name, "start_line": 40, "end_line": 310},
    )
    assert "40\ttext" in result and "239\ttext" in result
    assert "start_line=240" in result
    msg = {"role": "assistant", "tool_calls": [{"function": {
        "name": "read_file", "arguments": json.dumps(args),
    }}]}
    assert code_interpreter_panel.discover_open_files([msg]) == set()
    msg["tool_calls"][0]["function"]["arguments"] = json.dumps({"file": file.name})
    assert code_interpreter_panel.discover_open_files([msg]) == {file.name}


def test_invalid_ranges_and_multiple_paths_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(file_operations, "WORKSPACE", tmp_path)
    _, invalid = execute_tool_call("read_file", {"file": "fixture.txt", "start_line": 5, "end_line": 3})
    _, multiple = execute_tool_call("read_file", {"file": ["a", "b"], "start_line": 1})
    assert invalid.startswith("Error: line numbers")
    assert multiple.startswith("Error: a line range applies to one file")
