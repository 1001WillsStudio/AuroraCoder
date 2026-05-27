"""Anchor-matching engine for the range_replace_edit tool.

Provides tolerant anchor matching with ±N line shift, two-pass
(strict then relaxed whitespace), and whole-file diagnostic hints.
"""

from typing import List, Optional

MAX_ANCHOR_SHIFT = 3
_NO_CHANGES = "\nNo edits were applied — the file is unchanged."


def normalise(line: str) -> str:
    """Strip trailing whitespace (trailing spaces usually have no meaning)."""
    return line.rstrip('\n').rstrip()


def search_entire_file(lines: List[str], pattern: str) -> Optional[int]:
    """Search the entire file for an anchor line. Returns 0-based index or None."""
    for idx, line in enumerate(lines):
        if normalise(line) == normalise(pattern):
            return idx
    return None


def indentation_hint(expected: str, actual: str) -> str:
    """If content matches ignoring leading whitespace, return a hint."""
    if expected.strip() == actual.strip() and expected != actual:
        exp_indent = len(expected) - len(expected.lstrip())
        act_indent = len(actual) - len(actual.lstrip())
        return (f"\n⚠️  Content matches but indentation differs — "
                f"expected {exp_indent} leading spaces, "
                f"got {act_indent}.")
    return ""


def _block_match(lines: List[str], total_lines: int,
                 start_pos: int, anchor_lines: List[str], strict: bool) -> bool:
    """Check if anchor_lines match file lines starting at start_pos."""
    if start_pos < 0 or start_pos + len(anchor_lines) > total_lines:
        return False
    for j, anchor_line in enumerate(anchor_lines):
        an = anchor_line.rstrip('\n').rstrip()
        fl = lines[start_pos + j].rstrip('\n').rstrip()
        if strict:
            if an != fl:
                return False
        else:
            if an.strip() != fl.strip():
                return False
    return True


def _indent_aware_block_match(
    lines: List[str], total_lines: int,
    start_pos: int, anchor_lines: List[str]
) -> tuple:
    """Try strict first, then relaxed.  Returns (matched, indent_mismatch).

    *matched* is True when the block matches at start_pos (strict or relaxed).
    *indent_mismatch* is True when strict failed but relaxed succeeded —
    meaning the expected and actual content differ **only** in leading
    whitespace (indentation).
    """
    if _block_match(lines, total_lines, start_pos, anchor_lines, strict=True):
        return (True, False)
    if _block_match(lines, total_lines, start_pos, anchor_lines, strict=False):
        return (True, True)
    return (False, False)


def indent_delta(expected: str, actual: str) -> int:
    """Compute the indentation difference between expected and actual strings.

    Returns a positive delta when *actual* has more leading whitespace than
    *expected*; negative when *expected* has more.  Returns 0 when the two
    have identical leading whitespace or when the stripped content differs
    (i.e. the strings are not just indent-variants of each other).
    """
    exp_norm = normalise(expected)
    act_norm = normalise(actual)
    if exp_norm.strip() != act_norm.strip():
        return 0  # substantively different — not an indentation-only mismatch
    exp_indent = len(exp_norm) - len(exp_norm.lstrip())
    act_indent = len(act_norm) - len(act_norm.lstrip())
    return act_indent - exp_indent


def _candidates(total_lines: int, expected_line_num: int, block_len: int) -> List[int]:
    """Generate candidate 0-based positions sorted by distance from expected."""
    seen = set()
    result = []
    for shift in range(MAX_ANCHOR_SHIFT + 1):
        for direction in (0, 1, -1):
            if shift == 0 and direction != 0:
                continue
            idx = (expected_line_num - 1) + direction * shift
            if 0 <= idx <= total_lines - block_len and idx not in seen:
                seen.add(idx)
                result.append(idx)
    return result


def find_anchor_tolerant(lines: List[str], total_lines: int,
                         expected_line_num: int, expected_content: str):
    """Search for anchor content within ±MAX_ANCHOR_SHIFT lines.

    Supports single-line and multi-line content.
    Returns (0-based index of first matched line, indent_mismatch) or None.
    """
    if not expected_content.strip():
        # Empty or whitespace-only content: search for an empty line (±shift)
        positions = _candidates(total_lines, expected_line_num, 1)
        for pos in positions:
            if normalise(lines[pos]) == '':
                return (pos, False)
        return None

    anchor_lines = expected_content.splitlines(keepends=True)
    if not anchor_lines:
        # Non-empty strip but no lines (unlikely, but guard)
        return None
    # Drop trailing empty line so multi-line anchors match cleanly
    if len(anchor_lines) > 1 and normalise(anchor_lines[-1]) == '':
        anchor_lines = anchor_lines[:-1]

    positions = _candidates(total_lines, expected_line_num, len(anchor_lines))
    for pos in positions:
        matched, indent_mismatch = _indent_aware_block_match(
            lines, total_lines, pos, anchor_lines)
        if matched:
            return (pos, indent_mismatch)
    return None


def find_anchor_anywhere(lines: List[str], total_lines: int,
                         expected_content: str):
    """Fallback: search the ENTIRE file for anchor content.

    Used when the ±MAX_ANCHOR_SHIFT tolerant search fails — typically
    because an LLM pasted a large block (aider-style confusion) and the
    line numbers are far off.  Scans every possible position.
    Returns (0-based index of first matched line, indent_mismatch) or None.
    """
    if not expected_content.strip():
        return None

    anchor_lines = expected_content.splitlines(keepends=True)
    if not anchor_lines:
        return None
    if len(anchor_lines) > 1 and normalise(anchor_lines[-1]) == '':
        anchor_lines = anchor_lines[:-1]

    block_len = len(anchor_lines)
    for pos in range(total_lines - block_len + 1):
        matched, indent_mismatch = _indent_aware_block_match(
            lines, total_lines, pos, anchor_lines)
        if matched:
            return (pos, indent_mismatch)
    return None


def anchor_hint(lines: List[str], anchor_text: str,
                specified_line: int, is_multiline: bool) -> str:
    """Search whole file for anchor; return a diagnostic hint."""
    search_key = anchor_text.split('\n')[0] if is_multiline else anchor_text
    found_at = search_entire_file(lines, search_key)
    if found_at is None:
        return ("\n💡 Not found anywhere in this file. "
                "Wrong file? Re-read with read_file().")
    return (f"\n💡 Found at line {found_at + 1} (specified {specified_line}). "
            "Re-read the file for current line numbers.")
