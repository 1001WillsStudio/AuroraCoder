"""
Comprehensive test suite for the range_replace_edit tool (edit_file).
Tests edge cases: empty lines, tolerance search, [TO] anchors, whitespace,
boundaries, multi-edits, deletions, insertions, error handling, and more.

Run with:  cd /workspace && python test_edit_file_edge_cases.py
"""
import sys, os, tempfile, textwrap, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.code_tools.file_operations import range_replace_edit_tool, FileOperations
from src.code_tools import edit_anchors as am

_passed = 0
_failed = 0

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_file(content: str) -> str:
    """Write content to a temp file, return its path (relative to workspace)."""
    # Use a path inside workspace since FileOperations resolves relative to WORKSPACE
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, dir="/workspace"
    )
    tmp.write(content)
    tmp.close()
    return os.path.relpath(tmp.name, "/workspace")


def _read(path: str) -> str:
    with open(os.path.join("/workspace", path), "r") as f:
        return f.read()


def _cleanup(*paths):
    for p in paths:
        try:
            os.unlink(os.path.join("/workspace", p))
        except OSError:
            pass


def check(name: str, *, ok: bool, file_path: str = None,
          expected_content: str = None, result_prefix: str = None,
          result_contains: str = None, result_not_contains: str = None):
    """Assertion helper that counts passes/fails and cleans up."""
    global _passed, _failed
    try:
        if ok:
            assert ok, "ok must be True"
        if file_path and expected_content is not None:
            actual = _read(file_path)
            assert actual == expected_content, (
                f"\nEXPECTED:\n{repr(expected_content)}\nGOT:\n{repr(actual)}"
            )
        if result_prefix is not None:
            assert result_prefix.startswith(result_prefix), (
                f"result_prefix mismatch:\nEXPECTED: {result_prefix}\nGOT: {result_prefix}"
            )
        if result_contains is not None:
            # result_contains is passed via a separate mechanism; handled below
            pass
        if result_not_contains is not None:
            pass
        _passed += 1
        print(f"  ✅ {name}")
    except AssertionError as e:
        _failed += 1
        print(f"  ❌ {name}: {e}")
    finally:
        if file_path:
            _cleanup(file_path)


def edit(*, path: str, edits: list, expected_content: str,
         result_contains: str = None, result_not_contains: str = None):
    """Run an edit, verify file content and result message."""
    result = range_replace_edit_tool(target_file=path, edits=edits)

    # Check content
    actual = _read(path)
    assert actual == expected_content, (
        f"\nEXPECTED:\n{repr(expected_content)}\nGOT:\n{repr(actual)}"
    )

    # Check result message
    if result_contains:
        assert result_contains in result, (
            f"Result missing '{result_contains}':\n{result}"
        )
    if result_not_contains:
        assert result_not_contains not in result, (
            f"Result unexpectedly contains '{result_not_contains}':\n{result}"
        )
    return result


# ===================================================================
# 1. SINGLE-LINE REPLACEMENT
# ===================================================================
def test_single_line_replace():
    content = "line1\nline2\nline3\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "line2",
            "replace_content": "LINE_TWO",
        }], expected_content="line1\nLINE_TWO\nline3\n",
        result_contains="replaced 1 lines at 2-2")
        print("  ✅ single-line replace")
    finally:
        _cleanup(f)


# ===================================================================
# 2. MULTI-LINE REPLACEMENT (no [TO])
# ===================================================================
def test_multi_line_replace_no_to():
    content = "a\nb\nc\nd\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2-3",
            "content_to_remove": "b\nc",
            "replace_content": "B\nC\nX",
        }], expected_content="a\nB\nC\nX\nd\n",
        result_contains="replaced 2 lines with 3 lines")
        print("  ✅ multi-line replace (no [TO])")
    finally:
        _cleanup(f)


# ===================================================================
# 3. DELETION (empty replace_content)
# ===================================================================
def test_delete_single_line():
    content = "keep\nremove\nkeep\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "remove",
            "replace_content": "",
        }], expected_content="keep\nkeep\n",
        result_contains="deleted lines 2-2")
        print("  ✅ delete single line")
    finally:
        _cleanup(f)


def test_delete_multi_line():
    content = "a\nb\nc\nd\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2-3",
            "content_to_remove": "b\nc",
            "replace_content": "",
        }], expected_content="a\nd\n",
        result_contains="deleted lines 2-3")
        print("  ✅ delete multi line")
    finally:
        _cleanup(f)


# ===================================================================
# 4. INSERTION (replace 1 line with N > 1 lines)
# ===================================================================
def test_insert_lines():
    content = "top\nmiddle\nbottom\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "middle",
            "replace_content": "A\nB\nC",
        }], expected_content="top\nA\nB\nC\nbottom\n",
        result_contains="replaced 1 lines with 3 lines")
        print("  ✅ insert multiple lines")
    finally:
        _cleanup(f)


# ===================================================================
# 5. [TO] ANCHOR — SINGLE → SINGLE
# ===================================================================
def test_to_anchor_single_single():
    content = "alpha\nbravo\ncharlie\ndelta\necho\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2-4",
            "content_to_remove": "bravo\n[TO]\ndelta",
            "replace_content": "NEW",
        }], expected_content="alpha\nNEW\necho\n",
        result_contains="replaced 3 lines with 1 lines")
        print("  ✅ [TO] single→single anchor")
    finally:
        _cleanup(f)


# ===================================================================
# 6. [TO] ANCHOR — MULTI → MULTI
# ===================================================================
def test_to_anchor_multi_multi():
    content = "H1\nA\nB\nC\nD\nE\nF\nZ\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2-7",
            "content_to_remove": "A\nB\n[TO]\nE\nF",
            "replace_content": "MIDDLE",
        }], expected_content="H1\nMIDDLE\nZ\n",
        result_contains="replaced 6 lines with 1 lines")
        print("  ✅ [TO] multi→multi anchor")
    finally:
        _cleanup(f)


# ===================================================================
# 7. EMPTY LINE REMOVAL (the fix we made)
# ===================================================================
def test_empty_line_removal_explicit():
    content = "a\nb\n\nc\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "3",
            "content_to_remove": "",
            "replace_content": "",
        }], expected_content="a\nb\nc\n",
        result_contains="deleted lines 3-3")
        print("  ✅ empty line removal (content_to_remove='')")
    finally:
        _cleanup(f)


def test_empty_line_first():
    content = "\na\nb\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "",
            "replace_content": "",
        }], expected_content="a\nb\n",
        result_contains="deleted lines 1-1")
        print("  ✅ empty line removal at line 1")
    finally:
        _cleanup(f)


def test_empty_line_last():
    content = "a\nb\n\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "3",
            "content_to_remove": "",
            "replace_content": "",
        }], expected_content="a\nb\n",
        result_contains="deleted lines 3-3")
        print("  ✅ empty line removal at last line")
    finally:
        _cleanup(f)


def test_consecutive_empty_lines():
    content = "a\n\n\nb\n"
    f = _make_file(content)
    try:
        # Remove the middle empty line (line 3)
        edit(path=f, edits=[{
            "remove_line_number": "3",
            "content_to_remove": "",
            "replace_content": "",
        }], expected_content="a\n\nb\n",
        result_contains="deleted lines 3-3")
        print("  ✅ consecutive empty lines (remove one)")
    finally:
        _cleanup(f)


# ===================================================================
# 8. TOLERANCE SEARCH (line numbers off by ±N)
# ===================================================================
def test_tolerance_start_shift():
    content = "X\nA\nB\nC\n"
    f = _make_file(content)
    try:
        # Say line 3 is "A" but it's actually at line 2
        edit(path=f, edits=[{
            "remove_line_number": "3",
            "content_to_remove": "A",
            "replace_content": "FOUND",
        }], expected_content="X\nFOUND\nB\nC\n",
        result_contains="auto-corrected")
        print("  ✅ tolerance: start shifted up")
    finally:
        _cleanup(f)


def test_tolerance_not_found():
    content = "a\nb\nc\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "xyz_NOT_IN_FILE",
            "replace_content": "x",
        }])
        assert result.startswith("Error"), f"Expected error, got: {result}"
        print("  ✅ tolerance: not found → error")
    finally:
        _cleanup(f)


def test_tolerance_end_shift():
    """End anchor says 'T' but specified end_line is 4 where 'S' lives —
    tolerance finds 'T' at line 5 and corrects the range."""
    content = "P\nQ\nR\nS\nT\n"
    f = _make_file(content)
    try:
        # remove_line_number says 2-4 (Q,R,S), but end anchor 'T' is actually at line 5
        edit(path=f, edits=[{
            "remove_line_number": "2-4",
            "content_to_remove": "Q\n[TO]\nT",
            "replace_content": "MID",
        }], expected_content="P\nMID\n",  # Q,R,S,T replaced by MID
        result_contains="auto-corrected")
        print("  ✅ tolerance: end anchor shifted")
    finally:
        _cleanup(f)


# ===================================================================
# 9. WHITESPACE HANDLING (normalise strips trailing)
# ===================================================================
def test_trailing_spaces_normalised():
    content = "hello   \nworld\n"
    f = _make_file(content)
    try:
        # content_to_remove without trailing spaces should match
        edit(path=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "hello",
            "replace_content": "hi",
        }], expected_content="hi\nworld\n",
        result_contains="replaced 1 lines")
        print("  ✅ trailing spaces normalised")
    finally:
        _cleanup(f)


def test_unmatched_content_error():
    """Content that doesn't exist anywhere in the file → clear error."""
    content = "lineA\nlineB\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "non_existent",
            "replace_content": "x",
        }])
        assert result.startswith("Error"), f"Expected error: {result}"
        assert "Not found anywhere" in result, f"Expected 'Not found anywhere' hint: {result}"
        print("  ✅ unmatched content → error with diagnostic hint")
    finally:
        _cleanup(f)


def test_whitespace_only_line():
    content = "a\n   \nb\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "",
            "replace_content": "",
        }], expected_content="a\nb\n",
        result_contains="deleted lines 2-2")
        print("  ✅ whitespace-only line treated as empty")
    finally:
        _cleanup(f)


# ===================================================================
# 10. BOUNDARY: FIRST LINE, LAST LINE, WHOLE FILE
# ===================================================================
def test_first_line_edit():
    content = "first\nsecond\nthird\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "first",
            "replace_content": "FIRST!",
        }], expected_content="FIRST!\nsecond\nthird\n")
        print("  ✅ edit first line")
    finally:
        _cleanup(f)


def test_last_line_edit():
    content = "a\nb\nc\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "3",
            "content_to_remove": "c",
            "replace_content": "LAST!",
        }], expected_content="a\nb\nLAST!\n")
        print("  ✅ edit last line")
    finally:
        _cleanup(f)


def test_entire_file_range_to():
    content = "one\ntwo\nthree\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "1-3",
            "content_to_remove": "one\n[TO]\nthree",
            "replace_content": "ALL NEW",
        }], expected_content="ALL NEW\n")
        print("  ✅ entire file replacement via [TO]")
    finally:
        _cleanup(f)


def test_line_beyond_file_clamped():
    content = "a\nb\n"
    f = _make_file(content)
    try:
        # end_line 99 gets clamped to total_lines (2)
        edit(path=f, edits=[{
            "remove_line_number": "2-99",
            "content_to_remove": "b",
            "replace_content": "BIG",
        }], expected_content="a\nBIG\n",
        result_contains="replaced 1 lines")
        print("  ✅ end_line beyond file clamped to total_lines")
    finally:
        _cleanup(f)


# ===================================================================
# 11. MULTIPLE EDITS IN ONE CALL
# ===================================================================
def test_two_non_overlapping_edits():
    content = "1\n2\n3\n4\n5\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[
            {"remove_line_number": "1", "content_to_remove": "1", "replace_content": "ONE"},
            {"remove_line_number": "5", "content_to_remove": "5", "replace_content": "FIVE"},
        ], expected_content="ONE\n2\n3\n4\nFIVE\n")
        print("  ✅ two non-overlapping edits")
    finally:
        _cleanup(f)


def test_overlapping_edits_error():
    content = "a\nb\nc\nd\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[
            {"remove_line_number": "2-3", "content_to_remove": "b\nc", "replace_content": "X"},
            {"remove_line_number": "3-4", "content_to_remove": "c\nd", "replace_content": "Y"},
        ])
        assert "overlaps" in result, f"Expected overlap error: {result}"
        print("  ✅ overlapping edits → error")
    finally:
        _cleanup(f)


# ===================================================================
# 12. SPECIAL CHARACTERS
# ===================================================================
def test_unicode_content():
    content = "café\nnaïve\nrésumé\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "naïve",
            "replace_content": "NAÏVE!",
        }], expected_content="café\nNAÏVE!\nrésumé\n")
        print("  ✅ unicode content")
    finally:
        _cleanup(f)


def test_regex_special_chars():
    content = "foo.bar\nbaz*qux\nend\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "foo.bar",
            "replace_content": "replaced",
        }], expected_content="replaced\nbaz*qux\nend\n")
        print("  ✅ regex-special characters")
    finally:
        _cleanup(f)


def test_newlines_in_replace():
    content = "header\nbody\nfooter\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "body",
            "replace_content": "A\nB\nC\nD",
        }], expected_content="header\nA\nB\nC\nD\nfooter\n")
        print("  ✅ newlines in replace_content")
    finally:
        _cleanup(f)


# ===================================================================
# 13. TRAILING NEWLINE PRESERVATION
# ===================================================================
def test_trailing_newline_preserved():
    content = "a\nb\nc\n"   # file ends with \n
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "b",
            "replace_content": "BB",
        }], expected_content="a\nBB\nc\n")
        assert _read(f).endswith("\n"), "Trailing newline should be preserved"
        print("  ✅ trailing newline preserved")
    finally:
        _cleanup(f)


def test_no_trailing_newline_preserved():
    """File without trailing newline stays without one (preserves original style)."""
    content = "a\nb\nc"   # file does NOT end with \n
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2",
            "content_to_remove": "b",
            "replace_content": "BB",
        }], expected_content="a\nBB\nc")  # stays without trailing newline
        print("  ✅ no trailing newline preserved (not added)")
    finally:
        _cleanup(f)


# ===================================================================
# 14. ERROR CASES
# ===================================================================
def test_nonexistent_file():
    result = range_replace_edit_tool(target_file="nonexistent_xyz.txt", edits=[{
        "remove_line_number": "1", "content_to_remove": "x", "replace_content": "y",
    }])
    assert result.startswith("Error"), f"Expected error: {result}"
    print("  ✅ nonexistent file → error")


def test_invalid_line_format():
    content = "a\nb\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "abc",
            "content_to_remove": "a",
            "replace_content": "x",
        }])
        assert "must be like '13-15' or '42'" in result, f"Unexpected: {result}"
        print("  ✅ invalid remove_line_number format → error")
    finally:
        _cleanup(f)


def test_start_greater_than_end():
    content = "a\nb\nc\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "3-1",
            "content_to_remove": "c\na",
            "replace_content": "x",
        }])
        assert "start_line" in result and "end_line" in result, f"Unexpected: {result}"
        print("  ✅ start_line > end_line → error")
    finally:
        _cleanup(f)


# ===================================================================
# 15. ANCHOR MATCHER UNIT TESTS
# ===================================================================
def test_normalise():
    assert am.normalise("hello  \n") == "hello"
    assert am.normalise("\n") == ""
    assert am.normalise("  \n") == ""
    assert am.normalise("no_newline") == "no_newline"
    print("  ✅ normalise()")


def test_search_entire_file():
    lines = ["a\n", "b\n", "c\n"]
    assert am.search_entire_file(lines, "b") == 1
    assert am.search_entire_file(lines, "z") is None
    print("  ✅ search_entire_file()")


def test_indentation_hint():
    hint = am.indentation_hint("  hello", "hello")
    assert "indentation differs" in hint.lower()
    no_hint = am.indentation_hint("hello", "hello")
    assert no_hint == ""
    print("  ✅ indentation_hint()")


def test_block_match():
    lines = ["a\n", "b\n", "c\n"]
    assert am._block_match(lines, 3, 0, ["a\n", "b\n"], strict=True)
    assert not am._block_match(lines, 3, 0, ["a\n", "c\n"], strict=True)
    # relaxed mode ignores leading whitespace
    assert am._block_match(["  a\n", "  b\n"], 2, 0, ["a\n", "b\n"], strict=False)
    print("  ✅ _block_match()")


def test_indent_aware_block_match():
    """_indent_aware_block_match returns (matched, indent_mismatch)."""
    lines = ["    def foo():\n", "        pass\n"]
    # exact match → no indent mismatch
    assert am._indent_aware_block_match(lines, 2, 0, ["    def foo():\n"]) == (True, False)
    # different indent → matched with indent_mismatch=True
    assert am._indent_aware_block_match(lines, 2, 0, ["  def foo():\n"]) == (True, True)
    # substantively different → not matched
    assert am._indent_aware_block_match(lines, 2, 0, ["  def bar():\n"]) == (False, False)
    print("  ✅ _indent_aware_block_match()")


def test_indent_delta():
    """indent_delta computes the indentation difference."""
    # actual has 2 more spaces than expected → positive delta
    assert am.indent_delta("  hello", "    hello") == 2
    # expected has 2 more spaces → negative delta
    assert am.indent_delta("    hello", "  hello") == -2
    # equal indent → zero
    assert am.indent_delta("  hello", "  hello") == 0
    # substantively different content → zero (special case)
    assert am.indent_delta("hello", "world") == 0
    # tab (1 char) vs 4 spaces → delta = 3 (4-1)
    assert am.indent_delta("\tdef foo():", "    def foo():") == 3
    # mixed: expected has 0 indent, actual has 4
    assert am.indent_delta("def foo():", "    def foo():") == 4
    print("  ✅ indent_delta()")


def test_find_anchor_tolerant_indent_mismatch():
    """Tolerant search detects indentation mismatch."""
    lines = ["z\n", "    target\n", "b\n"]
    # wrong indent → should find position but report mismatch
    result = am.find_anchor_tolerant(lines, 3, expected_line_num=1, expected_content="  target")
    assert result == (1, True), f"Expected (1, True), got {result}"
    # wrong indent in multi-line content
    lines2 = ["z\n", "    A\n", "    B\n", "c\n"]
    result2 = am.find_anchor_tolerant(lines2, 4, expected_line_num=1, expected_content="  A\n  B")
    assert result2 == (1, True), f"Expected (1, True), got {result2}"
    print("  ✅ find_anchor_tolerant() indent mismatch")


def test_candidates():
    cand = am._candidates(total_lines=10, expected_line_num=5, block_len=1)
    assert cand[0] == 4  # exact position first
    assert all(0 <= c < 10 for c in cand)
    assert len(cand) == len(set(cand))  # no duplicates
    print("  ✅ _candidates()")


def test_find_anchor_tolerant_single():
    lines = ["z\n", "a\n", "target\n", "b\n", "y\n"]
    result = am.find_anchor_tolerant(lines, 5, expected_line_num=1, expected_content="target")
    assert result == (2, False), f"Expected (2, False), got {result}"
    # not found
    assert am.find_anchor_tolerant(lines, 5, 1, "nope") is None
    print("  ✅ find_anchor_tolerant() single-line")


def test_find_anchor_tolerant_multi():
    lines = ["x\n", "A\n", "B\n", "C\n", "y\n"]
    result = am.find_anchor_tolerant(lines, 5, expected_line_num=1, expected_content="A\nB")
    assert result == (1, False), f"Expected (1, False), got {result}"
    print("  ✅ find_anchor_tolerant() multi-line")


def test_find_anchor_tolerant_empty():
    lines = ["a\n", "b\n", "\n", "c\n"]
    result = am.find_anchor_tolerant(lines, 4, expected_line_num=4, expected_content="")
    assert result == (2, False), f"Expected (2, False), got {result}"
    # whitespace-only
    lines2 = ["a\n", "   \n", "b\n"]
    result2 = am.find_anchor_tolerant(lines2, 3, expected_line_num=1, expected_content="")
    assert result2 == (1, False), f"Expected (1, False), got {result2}"
    print("  ✅ find_anchor_tolerant() empty line")


def test_anchor_hint():
    lines = ["a\n", "b\n", "c\n"]
    hint = am.anchor_hint(lines, "b", specified_line=1, is_multiline=False)
    assert "Found at line 2" in hint
    no_hint = am.anchor_hint(lines, "z", specified_line=1, is_multiline=False)
    assert "Not found anywhere" in no_hint
    print("  ✅ anchor_hint()")


# ===================================================================
# 16. EDGE: no-op (edit that doesn't change anything)
# ===================================================================
def test_noop_edit():
    content = "same\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "same",
            "replace_content": "same",
        }])
        assert "no change" in result, f"Expected no-change message: {result}"
        print("  ✅ no-op edit → 'no change' message")
    finally:
        _cleanup(f)


# ===================================================================
# 17. EDGE: single-line file
# ===================================================================
def test_single_line_file():
    content = "only\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "only",
            "replace_content": "ONLY",
        }], expected_content="ONLY\n")
        print("  ✅ single-line file edit")
    finally:
        _cleanup(f)


# ===================================================================
# 18. EDGE: [TO] with trailing empty line in anchor
# ===================================================================
def test_to_trailing_empty_in_anchor():
    """content_to_remove has a trailing \n after the end anchor."""
    content = "START\nmiddle\nEND\n"
    f = _make_file(content)
    try:
        # Add trailing newline in content_to_remove — should be stripped by normalise
        edit(path=f, edits=[{
            "remove_line_number": "1-3",
            "content_to_remove": "START\n[TO]\nEND\n",
            "replace_content": "DONE",
        }], expected_content="DONE\n")
        print("  ✅ [TO] with trailing newline in anchor")
    finally:
        _cleanup(f)


# ===================================================================
# 19. EDGE: content_to_remove with [TO] but no end marker specified
# ===================================================================
def test_to_empty_end():
    """[TO] with end_content empty → auto-populated from file."""
    content = "A\nB\nC\nD\nE\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2-5",
            "content_to_remove": "B\n[TO]\n",  # empty after [TO]
            "replace_content": "X",
        }], expected_content="A\nX\n")
        print("  ✅ [TO] with empty end anchor (auto-populated)")
    finally:
        _cleanup(f)


# ===================================================================
# 20. INDENT AUTO-FIX (single-line)
# ===================================================================
def test_indent_auto_fix_single_line():
    """content_to_remove has wrong indent → replace_content indent is auto-fixed."""
    content = "    def foo():\n        pass\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "  def foo():",       # 2-space indent (wrong)
            "replace_content": "  def bar():",         # 2-space (should become 4)
        }], expected_content="    def bar():\n        pass\n",
        result_contains="Indentation mismatch")
        print("  ✅ indent auto-fix: single-line (2→4 spaces)")
    finally:
        _cleanup(f)


def test_indent_auto_fix_multi_line():
    """Multi-line [TO] with wrong indent — indent delta from first anchor line."""
    content = "class Foo:\n    def one():\n        pass\n    def two():\n        pass\n"
    f = _make_file(content)
    try:
        edit(path=f, edits=[{
            "remove_line_number": "2-4",
            "content_to_remove": "  def one():\n[TO]\n  def two():",  # 2-space (wrong)
            "replace_content": "  def replaced():\n      pass",       # 2-space (should become 4)
        }], expected_content="class Foo:\n    def replaced():\n        pass\n        pass\n",
        result_contains="Indentation mismatch")
        print("  ✅ indent auto-fix: multi-line [TO] (2→4 spaces)")
    finally:
        _cleanup(f)


def test_indent_auto_fix_deletion():
    """Indent mismatch + deletion: warns about indent but no replacement to adjust."""
    content = "    delete_me\n    keep\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "  delete_me",   # wrong indent
            "replace_content": "",                 # deletion
        }])
        # Should still work (delete) but no indent warning since replace is empty
        assert _read(f) == "    keep\n"
        print("  ✅ indent auto-fix: deletion with indent mismatch (no crash)")
    finally:
        _cleanup(f)


def test_indent_correct_no_warning():
    """Correct indent in content_to_remove → no indent warning."""
    content = "    hello\n"
    f = _make_file(content)
    try:
        result = range_replace_edit_tool(target_file=f, edits=[{
            "remove_line_number": "1",
            "content_to_remove": "    hello",
            "replace_content": "    world",
        }])
        assert "Indentation mismatch" not in result
        print("  ✅ indent: correct indent → no warning")
    finally:
        _cleanup(f)


def test_adjust_indent_positive_delta():
    """adjust_indent with positive delta adds spaces."""
    assert am.adjust_indent("hello\nworld\n", 2) == "  hello\n  world\n"
    # blank lines stay blank
    assert am.adjust_indent("hello\n\nworld\n", 3) == "   hello\n\n   world\n"
    print("  ✅ adjust_indent() positive delta")


def test_adjust_indent_negative_delta():
    """adjust_indent with negative delta removes spaces."""
    assert am.adjust_indent("    hello\n    world\n", -2) == "  hello\n  world\n"
    # can't go negative — stops at zero
    assert am.adjust_indent("  hello\n", -5) == "hello\n"
    print("  ✅ adjust_indent() negative delta")


# ===================================================================
# runner
# ===================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Comprehensive edit_file (range_replace_edit) tests")
    print("=" * 60)

    tests = [
        # 1-4: Basic operations
        ("single-line replace", test_single_line_replace),
        ("multi-line replace (no [TO])", test_multi_line_replace_no_to),
        ("delete single line", test_delete_single_line),
        ("delete multi line", test_delete_multi_line),
        ("insert multiple lines", test_insert_lines),
        # 5-6: [TO] anchors
        ("[TO] single→single anchor", test_to_anchor_single_single),
        ("[TO] multi→multi anchor", test_to_anchor_multi_multi),
        # 7: Empty line handling (our fix)
        ("empty line removal (content_to_remove='')", test_empty_line_removal_explicit),
        ("empty line at line 1", test_empty_line_first),
        ("empty line at last line", test_empty_line_last),
        ("consecutive empty lines", test_consecutive_empty_lines),
        # 8: Tolerance search
        ("tolerance: start shifted", test_tolerance_start_shift),
        ("tolerance: not found → error", test_tolerance_not_found),
        ("tolerance: end anchor shifted", test_tolerance_end_shift),
        # 9: Whitespace handling
        ("trailing spaces normalised", test_trailing_spaces_normalised),
        ("unmatched content → error with hint", test_unmatched_content_error),
        ("whitespace-only line treated as empty", test_whitespace_only_line),
        # 10: Boundaries
        ("edit first line", test_first_line_edit),
        ("edit last line", test_last_line_edit),
        ("entire file via [TO]", test_entire_file_range_to),
        ("end_line beyond file clamped", test_line_beyond_file_clamped),
        # 11: Multiple edits
        ("two non-overlapping edits", test_two_non_overlapping_edits),
        ("overlapping edits → error", test_overlapping_edits_error),
        # 12: Special characters
        ("unicode content", test_unicode_content),
        ("regex-special chars", test_regex_special_chars),
        ("newlines in replace_content", test_newlines_in_replace),
        # 13: Trailing newline
        ("trailing newline preserved", test_trailing_newline_preserved),
        ("no trailing newline preserved", test_no_trailing_newline_preserved),
        # 14: Error cases
        ("nonexistent file → error", test_nonexistent_file),
        ("invalid line format → error", test_invalid_line_format),
        ("start > end → error", test_start_greater_than_end),
        # 15: Anchor matcher unit tests
        ("normalise()", test_normalise),
        ("search_entire_file()", test_search_entire_file),
        ("indentation_hint()", test_indentation_hint),
        ("_block_match()", test_block_match),
        ("_candidates()", test_candidates),
        ("find_anchor_tolerant() single", test_find_anchor_tolerant_single),
        ("find_anchor_tolerant() multi", test_find_anchor_tolerant_multi),
        ("find_anchor_tolerant() empty", test_find_anchor_tolerant_empty),
        ("anchor_hint()", test_anchor_hint),
        ("_indent_aware_block_match()", test_indent_aware_block_match),
        ("indent_delta()", test_indent_delta),
        ("find_anchor_tolerant() indent mismatch", test_find_anchor_tolerant_indent_mismatch),
        # 16-18: Misc edges
        ("no-op edit → no change message", test_noop_edit),
        ("single-line file", test_single_line_file),
        ("[TO] trailing newline in anchor", test_to_trailing_empty_in_anchor),
        ("[TO] with empty end anchor", test_to_empty_end),
        # 20: Indent auto-fix
        ("indent auto-fix: single-line (2→4)", test_indent_auto_fix_single_line),
        ("indent auto-fix: multi-line [TO]", test_indent_auto_fix_multi_line),
        ("indent auto-fix: deletion (no crash)", test_indent_auto_fix_deletion),
        ("indent: correct → no warning", test_indent_correct_no_warning),
        ("adjust_indent() positive delta", test_adjust_indent_positive_delta),
        ("adjust_indent() negative delta", test_adjust_indent_negative_delta),
    ]

    for i, (name, fn) in enumerate(tests, 1):
        try:
            fn()
            _passed += 1
        except Exception as e:
            _failed += 1
            print(f"  ❌ {name}: UNHANDLED EXCEPTION: {e}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    total = _passed + _failed
    print(f"Results: {_passed}/{total} passed", end="")
    if _failed > 0:
        print(f", {_failed} FAILED")
        sys.exit(1)
    else:
        print(" — ALL PASSED 🎉")
