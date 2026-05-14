"""
Tests for the content-anchor edit_file tool.

Covers exact matches, drift recovery (±1/±2/±3 lines), multi-line anchors,
whitespace tolerance (trailing and leading), deletion, edge cases, errors,
and self-correction markers.

Parameter order: (target_file, start_line, content_to_remove, end_line, replace_content)
"""

import json
import os
import re
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# Import range_replace_edit_tool directly to avoid triggering the __init__.py chain
# (which in turn imports file_operations -> ..config -> fails outside the package).
import importlib.util as _iu
_spec = _iu.spec_from_file_location(
    "file_operations",
    Path(__file__).resolve().parents[1] / "src" / "code_tools" / "file_operations.py",
)
_fo = _iu.module_from_spec(_spec)
sys.modules["file_operations"] = _fo
_spec.loader.exec_module(_fo)
edit_file = _fo.range_replace_edit_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(content: str, dir: str = None) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", dir=dir or "/workspace")
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path


def _read(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = Path("/workspace") / p
    return p.read_text(encoding="utf-8")


def _relative(path: str) -> str:
    try:
        return str(Path(path).relative_to("/workspace"))
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE = textwrap.dedent("""\
    # --- Imports ---
    import os
    import sys
    import json
    import re
    import logging
    from pathlib import Path
    from typing import List, Dict, Any, Optional, Callable

    logger = logging.getLogger(__name__)

    # --- Constants ---
    MAX_TOKENS = 8192
    MAX_ITERATIONS = 30

    # --- Core logic ---
    def process(items: List[str]) -> Dict[str, int]:
        result = {}
        for item in items:
            result[item] = len(item)
        return result

    if __name__ == "__main__":
        print(process(["hello", "world"]))
""")

SELF_CORRECT_MARKER = "<!--SELF_CORRECT:"
AUTO_CORRECTED_NOTICE = "auto-corrected"


# ---------------------------------------------------------------------------
# Exact match
# ---------------------------------------------------------------------------

def test_exact_match_single_line_anchors():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "# New constants\nMAX = 100")
    assert "✅ Applied edit" in result
    content = _read(path)
    assert "# New constants" in content
    assert "MAX_TOKENS = 8192" not in content
    assert "MAX_ITERATIONS = 30" not in content


def test_exact_match_replacement_preserves_lines():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "MAX_TOKENS = 100\nMAX_ITERATIONS = 50")
    assert "✅ Applied edit" in result
    content = _read(path)
    assert "MAX_TOKENS = 100" in content
    assert "MAX_ITERATIONS = 50" in content


# ---------------------------------------------------------------------------
# Anchor drift
# ---------------------------------------------------------------------------

def test_start_anchor_drift_plus_1():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 14, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result
    assert AUTO_CORRECTED_NOTICE in result


def test_start_anchor_drift_plus_3():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 16, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result
    assert AUTO_CORRECTED_NOTICE in result


def test_end_anchor_drift_minus_2():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 12, "X = 1")
    assert "✅ Applied edit" in result
    assert AUTO_CORRECTED_NOTICE in result


def test_both_anchors_drift():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 15, anchor, 16, "X = 1")
    assert "✅ Applied edit" in result
    assert AUTO_CORRECTED_NOTICE in result


# ---------------------------------------------------------------------------
# Multi-line anchors
# ---------------------------------------------------------------------------

def test_multi_line_start_anchor():
    path = _relative(_make_file(SAMPLE))
    anchor = (
        "logger = logging.getLogger(__name__)\n"
        "\n"
        "...\n"
        "MAX_ITERATIONS = 30"
    )
    result = edit_file(path, 11, anchor, 14, "# removed\n")
    assert "✅ Applied edit" in result
    content = _read(path)
    assert "# removed" in content
    assert "logger = logging.getLogger" not in content


def test_multi_line_end_anchor():
    path = _relative(_make_file(SAMPLE))
    anchor = (
        "MAX_TOKENS = 8192\n"
        "...\n"
        "MAX_ITERATIONS = 30\n"
        "\n"
    )
    result = edit_file(path, 13, anchor, 14, "# removed\n")
    assert "✅ Applied edit" in result


def test_both_anchors_multi_line():
    path = _relative(_make_file(SAMPLE))
    anchor = (
        "MAX_TOKENS = 8192\n"
        "MAX_ITERATIONS = 30\n"
        "...\n"
        "def process(items: List[str]) -> Dict[str, int]:\n"
        "    result = {}"
    )
    result = edit_file(path, 13, anchor, 17, "# removed\n")
    assert "✅ Applied edit" in result
    content = _read(path)
    assert "# removed" in content
    assert "MAX_TOKENS = 8192" not in content
    assert "def process" not in content


# ---------------------------------------------------------------------------
# Whitespace tolerance
# ---------------------------------------------------------------------------

def test_trailing_whitespace_in_anchor():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192   \n...\nMAX_ITERATIONS = 30   "
    result = edit_file(path, 13, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result


def test_trailing_whitespace_in_file():
    raw = SAMPLE.replace("MAX_TOKENS = 8192\n", "MAX_TOKENS = 8192   \n")
    path = _relative(_make_file(raw))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result


def test_leading_whitespace_mismatch_relaxed():
    raw = SAMPLE.replace("        result = {}", "            result = {}")
    path = _relative(_make_file(raw))
    anchor = "def process(items: List[str]) -> Dict[str, int]:\n...\n        result = {}"
    result = edit_file(path, 17, anchor, 18, "    pass")
    assert "✅ Applied edit" in result


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def test_delete_range():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "")
    assert "✅ Applied edit" in result
    assert "deleted 2 lines" in result
    content = _read(path)
    assert "MAX_TOKENS = 8192" not in content
    assert "MAX_ITERATIONS = 30" not in content


# ---------------------------------------------------------------------------
# No change
# ---------------------------------------------------------------------------

def test_replace_with_identical_content():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "MAX_TOKENS = 8192\nMAX_ITERATIONS = 30\n")
    assert "No change" in result


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_file_not_found():
    result = edit_file("nonexistent_file_12345.xyz", 1, "a\n...\nb", 2, "c")
    assert "not found" in result.lower()


def test_anchor_not_found_outside_window():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 1, anchor, 2, "X = 1")
    assert "could not find" in result.lower()


def test_start_after_end():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_ITERATIONS = 30\n...\nMAX_TOKENS = 8192"
    result = edit_file(path, 13, anchor, 14, "X = 1")
    assert "appears after" in result.lower() or "could not find" in result.lower()


def test_no_separator():
    path = _relative(_make_file(SAMPLE))
    result = edit_file(path, 13, "MAX_TOKENS = 8192", 14, "X = 1")
    assert "must contain both start and end anchors" in result.lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_single_line_file():
    path = _relative(_make_file("hello world\n"))
    anchor = "hello world\n...\nhello world"
    result = edit_file(path, 1, anchor, 1, "goodbye")
    assert "✅ Applied edit" in result


def test_no_trailing_newline_in_file():
    path = _relative(_make_file("line1\nline2"))
    anchor = "line1\n...\nline2"
    result = edit_file(path, 1, anchor, 2, "replaced")
    assert "✅ Applied edit" in result


def test_no_auto_correct_when_exact():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result
    assert AUTO_CORRECTED_NOTICE not in result


# ---------------------------------------------------------------------------
# Self-correction markers
# ---------------------------------------------------------------------------

def test_self_correct_marker_present_on_drift():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 14, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result
    assert SELF_CORRECT_MARKER in result
    m = re.search(r'<!--SELF_CORRECT:(.*?)-->', result, re.DOTALL)
    assert m, f"Expected SELF_CORRECT marker in: {result}"
    correction = json.loads(m.group(1))
    assert correction["start_line"] == 13
    assert correction["end_line"] == 14
    assert "MAX_TOKENS = 8192" in correction["content_to_remove"]
    assert "MAX_ITERATIONS = 30" in correction["content_to_remove"]
    assert correction["replace_content"] == "X = 1"


def test_self_correct_marker_present_on_relaxed_match():
    raw = SAMPLE.replace("        result = {}", "            result = {}")
    path = _relative(_make_file(raw))
    anchor = "def process(items: List[str]) -> Dict[str, int]:\n...\n        result = {}"
    result = edit_file(path, 17, anchor, 18, "    pass")
    assert "✅ Applied edit" in result
    assert SELF_CORRECT_MARKER in result


def test_self_correct_marker_absent_when_exact():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 13, anchor, 14, "X = 1")
    assert "✅ Applied edit" in result
    assert SELF_CORRECT_MARKER not in result
    assert AUTO_CORRECTED_NOTICE not in result


def test_self_correct_content_to_remove_is_verbatim_from_file():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 15, anchor, 15, "X = 1")
    m = re.search(r'<!--SELF_CORRECT:(.*?)-->', result, re.DOTALL)
    correction = json.loads(m.group(1))
    ctr = correction["content_to_remove"]
    parts = ctr.split("\n...\n", 1)
    assert parts[0] == "MAX_TOKENS = 8192\n"
    assert parts[1] == "MAX_ITERATIONS = 30\n"


def test_auto_correct_notice_at_top_of_result():
    path = _relative(_make_file(SAMPLE))
    anchor = "MAX_TOKENS = 8192\n...\nMAX_ITERATIONS = 30"
    result = edit_file(path, 16, anchor, 14, "X = 1")
    assert result.startswith("⚠️  Original parameters were auto-corrected.")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
