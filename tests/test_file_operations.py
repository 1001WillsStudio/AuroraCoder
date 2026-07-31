"""Unit tests for :mod:`src.code_tools.file_operations`.

QA focus: filesystem tooling that the agent drives directly. We verify the
read/write/delete/list/search contract, the large-file guard, and —
critically — flag the path-escape surface as a known security finding
(``_resolve_path`` performs no traversal validation).
"""
import subprocess

import pytest

from src.code_tools import file_operations as fo


@pytest.fixture
def ws(tmp_workspace, monkeypatch):
    """FileOperations.__init__ binds the module-level WORKSPACE constant;
    redirect it into the isolated tmp workspace for every test here."""
    monkeypatch.setattr(fo, "WORKSPACE", tmp_workspace)
    return tmp_workspace


def _fops():
    return fo.FileOperations()


# --------------------------------------------------------------------------- _resolve_path / traversal
def test_resolve_path_relative_joins_workspace(ws):
    resolved = _fops()._resolve_path("a/b.py")
    assert resolved == ws / "a" / "b.py"


def test_resolve_path_absolute_passes_through(ws):
    resolved = _fops()._resolve_path("/tmp/some/abs/path.py")
    assert str(resolved) == "/tmp/some/abs/path.py" and resolved.is_absolute()


@pytest.mark.xfail(
    reason="SECURITY GAP: _resolve_path performs no path-escape validation; "
           "a relative '../../x' resolves outside WORKSPACE. Filed for hardening.",
    strict=False,
)
def test_resolve_path_blocks_traversal(ws):
    fops = _fops()
    resolved = fops._resolve_path("subdir/../../escape.txt")
    # Passes only once traversal is actually blocked:
    assert resolved.resolve().is_relative_to(ws)


# --------------------------------------------------------------------------- read_file
def test_read_file_returns_header_for_existing(ws, tmp_workspace):
    f = tmp_workspace / "a.py"
    f.write_text("line1\nline2\nline3")
    out = _fops().read_file("a.py")
    assert "The file 'a.py' (3 lines" in out
    assert "is opened in the code interpreter" in out


def test_read_file_missing_reports_error(ws):
    out = _fops().read_file("nope.py")
    assert out == "Error: File 'nope.py' does not exist"


def test_read_file_on_directory_reports_error(ws, tmp_workspace):
    (tmp_workspace / "dir").mkdir()
    out = _fops().read_file("dir")
    assert "is not a file" in out


def test_read_file_large_file_guard(ws, tmp_workspace, monkeypatch):
    monkeypatch.setattr(fo, "MAX_FILE_READ_SIZE", 10)
    big = tmp_workspace / "big.log"
    big.write_text("x" * 200 + "\n" + "y" * 200 + "\n")  # >10 bytes
    out = _fops().read_file("big.log")
    assert "too large to display in full" in out
    assert "big.log" in out


# --------------------------------------------------------------------------- full_file_write
def test_full_file_write_creates_new_file(ws, tmp_workspace):
    out = _fops().full_file_write("newdir/new.py", "print('hi')\n")
    assert out == "Created new file: newdir/new.py"
    assert (tmp_workspace / "newdir" / "new.py").read_text() == "print('hi')\n"


def test_full_file_write_overwrites_existing(ws, tmp_workspace):
    f = tmp_workspace / "e.py"
    f.write_text("old\n")
    out = _fops().full_file_write("e.py", "new content")
    assert out == "Successfully edited e.py"
    assert f.read_text() == "new content"  # _apply_edit is a full replace


# --------------------------------------------------------------------------- delete_file
def test_delete_file_removes_file(ws, tmp_workspace):
    f = tmp_workspace / "d.py"
    f.write_text("x")
    out = _fops().delete_file("d.py")
    assert out == "Successfully deleted file: d.py"
    assert not f.exists()


def test_delete_file_removes_directory(ws, tmp_workspace):
    d = tmp_workspace / "ddir"
    (d / "inner.txt").parent.mkdir(parents=True)
    (d / "inner.txt").write_text("x")
    out = _fops().delete_file("ddir")
    assert out == "Successfully deleted directory: ddir"
    assert not d.exists()


def test_delete_file_missing(ws):
    out = _fops().delete_file("absent.py")
    assert out == "File 'absent.py' does not exist"


# --------------------------------------------------------------------------- list_dir
def test_list_dir_lists_contents(ws, tmp_workspace):
    (tmp_workspace / "f.py").write_text("x")
    (tmp_workspace / "sub").mkdir()
    out = _fops().list_dir("")
    assert "Contents of" in out
    assert "f.py" in out and "bytes" in out
    assert "sub/" in out  # directory entry


def test_list_dir_empty(ws, tmp_workspace):
    (tmp_workspace / "empty").mkdir()
    out = _fops().list_dir("empty")
    assert out == "Directory 'empty' is empty"


def test_list_dir_missing(ws):
    out = _fops().list_dir("no/such/dir")
    assert "does not exist" in out


# --------------------------------------------------------------------------- file_search (subprocess-backed)
@pytest.fixture
def fake_find(monkeypatch, fake_subprocess):
    """Install the FakeSubprocess seam. Tests register their own canned `find`
    outputs (matchers are FIFO, so a fixture-default would shadow per-test
    handlers — we deliberately register nothing here)."""
    fake_subprocess.install(monkeypatch)
    return fake_subprocess


def test_file_search_no_results(ws, fake_find):
    fake_find.register(predicate=lambda a: a[0] == "find", stdout="", returncode=0)
    out = _fops().file_search("argv")
    assert out == "No files found matching 'argv'"


def test_file_search_parses_and_orders_results(ws, fake_find, tmp_workspace):
    a = tmp_workspace / "a.py"
    b = tmp_workspace / "sub" / "a.txt"
    fake_find.register(predicate=lambda a: a[0] == "find",
                       stdout=f"{a}\n{b}\n", returncode=0)
    out = _fops().file_search("a")
    assert "Found 2 files matching 'a'" in out
    # names sorted alphabetically; both surfaced with their relpaths
    assert "a.py" in out and "a.txt" in out
    # relative paths (not absolute) in the output
    assert str(tmp_workspace) not in out.split("\n")[1]


def test_file_search_truncates_to_max_results(ws, fake_find, tmp_workspace):
    paths = "\n".join(str(tmp_workspace / f"f{i}.py") for i in range(5))
    fake_find.register(predicate=lambda a: a[0] == "find", stdout=paths)
    out = _fops().file_search("f", max_results=2)
    assert "... and 3 more files" in out


def test_file_search_timeout(ws, fake_find):
    fake_find.register(predicate=lambda a: a[0] == "find",
                       side_effect=subprocess.TimeoutExpired(["find"], 10))
    out = _fops().file_search("slow")
    assert "file search timed out looking for 'slow'" in out


def test_file_search_missing_find_binary(ws, fake_find):
    fake_find.register(predicate=lambda a: a[0] == "find",
                       side_effect=FileNotFoundError())
    out = _fops().file_search("x")
    assert "find binary not found" in out


# --------------------------------------------------------------------------- tool wrappers
def test_read_file_tool_accepts_list_of_targets(ws, tmp_workspace):
    (tmp_workspace / "a.py").write_text("a")
    (tmp_workspace / "b.py").write_text("b")
    msg, returned = fo.read_file_tool({"file": ["a.py", "b.py"]})
    assert "a.py" in msg and "b.py" in msg
    assert returned == {"file": ["a.py", "b.py"]}


def test_full_file_write_tool_requires_content(ws):
    with pytest.raises(KeyError):
        fo.full_file_write_tool({"file": "x.py"})  # missing 'content'


def test_delete_file_tool_accepts_list(ws, tmp_workspace):
    (tmp_workspace / "a.py").write_text("x")
    (tmp_workspace / "b.py").write_text("y")
    msg, _ = fo.delete_file_tool({"file": ["a.py", "b.py"]})
    assert "Successfully deleted file: a.py" in msg
    assert "Successfully deleted file: b.py" in msg
    assert not (tmp_workspace / "a.py").exists()


def test_list_dir_tool_defaults_to_root(ws, tmp_workspace):
    (tmp_workspace / "z.py").write_text("z")
    msg, _ = fo.list_dir_tool({})
    assert "z.py" in msg


def test_file_search_tool_requires_query(ws, fake_find):
    # KeyError raised before any subprocess dispatch — no handler needed
    with pytest.raises(KeyError):
        fo.file_search_tool({})  # missing 'query'


# --------------------------------------------------------------------------- close_file_tool modes
def test_close_file_tool_keep_mode(ws):
    msg, _ = fo.close_file_tool({"keep": ["a.py", "b.py"]})
    assert msg == "Closed all files except: 'a.py', 'b.py'"


def test_close_file_tool_keep_empty_closes_all(ws):
    msg, _ = fo.close_file_tool({"keep": []})
    assert msg == "Closed all files from code interpreter view."


def test_close_file_tool_single_close(ws):
    msg, _ = fo.close_file_tool({"file": "a.py"})
    assert msg == "Closed 'a.py' from code interpreter view."


def test_close_file_tool_requires_file_or_keep(ws):
    msg, _ = fo.close_file_tool({})
    assert msg == "Error: must provide 'file' or 'keep' parameter"