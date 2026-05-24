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
                         expected_line_num: int, expected_content: str) -> Optional[int]:
    """Search for anchor content within ±MAX_ANCHOR_SHIFT lines.

    Supports single-line and multi-line content.
    Returns 0-based index of first matched line, or None.
    """
    if not expected_content.strip():
        positions = _candidates(total_lines, expected_line_num, 1)
        for pos in positions:
            if lines[pos].strip() == '':
                return pos
        return None

    anchor_lines = expected_content.splitlines(keepends=True)
    if not anchor_lines:
        return None
    if len(anchor_lines) > 1 and anchor_lines[-1].strip() == '':
        anchor_lines = anchor_lines[:-1]

    positions = _candidates(total_lines, expected_line_num, len(anchor_lines))
    for pos in positions:
        if _block_match(lines, total_lines, pos, anchor_lines, strict=True):
            return pos
    for pos in positions:
        if _block_match(lines, total_lines, pos, anchor_lines, strict=False):
            return pos
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
