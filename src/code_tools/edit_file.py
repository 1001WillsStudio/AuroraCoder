"""Anchor-matching engine for the range_replace_edit tool, plus the RangeReplaceEditor class.

Provides tolerant anchor matching with ±N line shift, two-pass
(strict then relaxed whitespace), and whole-file diagnostic hints.
"""

from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import os
import tempfile

MAX_ANCHOR_SHIFT = 3
_NO_CHANGES = "\nNo edits were applied — the file is unchanged."


class _AnchorError(Exception):
    """Raised internally when an anchor cannot be resolved."""


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


def adjust_indent(text: str, delta: int) -> str:
    """Adjust leading whitespace of each non-blank line by *delta* spaces.

    Positive delta adds spaces; negative delta removes up to abs(delta).
    Blank lines and lines whose content differs substantively are unchanged.
    """
    if delta == 0 or not text:
        return text
    lines = text.splitlines(keepends=True)
    adjusted = []
    for line in lines:
        stripped = line.lstrip(' ')
        if not stripped.strip('\n\r'):
            adjusted.append(line)                  # blank — leave alone
        elif delta > 0:
            adjusted.append(' ' * delta + line)
        else:
            removed = len(line) - len(stripped)
            adjusted.append(line[min(abs(delta), removed):])
    return ''.join(adjusted)


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
                "Wrong file? Re-add with manage_open_files().")
    return (f"\n💡 Found at line {found_at + 1} (specified {specified_line}). "
            "Re-read the file for current line numbers.")


# ═══════════════════════════════════════════════════════════════════════════
# RangeReplaceEditor — orchestrates anchor-matching replace edits on files
# ═══════════════════════════════════════════════════════════════════════════

class RangeReplaceEditor:
    """Orchestrates anchor-based range-replace edits on a file.

    Uses the tolerant anchor-matching engine (find_anchor_tolerant,
    find_anchor_anywhere) to resolve edit specifications and applies them
    atomically.  Handles auto-correction of line numbers, indent fixes,
    [TO] normalisation, and edit truncation.
    """

    MAX_EDITS_PER_CALL = 3

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root)
        self.applied_arguments: dict | None = None

    # ── public entry point ──────────────────────────────────────────

    def edit(
        self,
        target_file: str,
        edits: List[Dict[str, Any]],
    ) -> Tuple[str, dict | None]:
        """Apply one or more range-replace edits to *target_file*.

        Returns ``(result_message, applied_arguments)``.
        """
        self.applied_arguments = {"target_file": target_file, "edits": edits}

        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return (f"Error: File '{target_file}' not found.", None)

            original_text = file_path.read_text(encoding="utf-8", errors="ignore").replace('\r\n', '\n')

            original_lines = original_text.splitlines(keepends=True)
            total_lines = len(original_lines)

            # ── Phase 1: validate & resolve each edit ──
            validated: list = []          # (start_idx, end_idx, replace_content, edit_num)
            applied_edits: list = []      # canonical applied arg form
            any_corrected = False
            any_indent_fixed = False

            for i, edit in enumerate(edits):
                outcome = self._validate_one_edit(i, edit, original_lines, total_lines)
                if isinstance(outcome, str):          # error message
                    return (outcome, None)
                (s, e, repl, corrected, indent_fixed, applied_edit) = outcome
                validated.append((s, e, repl, i))
                applied_edits.append(applied_edit)
                any_corrected |= corrected
                any_indent_fixed |= indent_fixed

            # ── Phase 2: guard against overlapping edits ──
            sorted_edits = sorted(validated, key=lambda e: e[0])
            for a_i in range(len(sorted_edits) - 1):
                a_s, a_e, _, a_n = sorted_edits[a_i]
                b_s, b_e, _, b_n = sorted_edits[a_i + 1]
                if a_e >= b_s:
                    return (f"Error: edit #{a_n + 1} (lines {a_s + 1}-{a_e + 1}) "
                            f"overlaps edit #{b_n + 1} (lines {b_s + 1}-{b_e + 1}). "
                            f"Split overlapping edits into separate edit_file calls."
                            f"{_NO_CHANGES}", None)

            # ── Phase 3: apply edits bottom-to-top ──
            validated.sort(key=lambda e: e[0], reverse=True)
            new_lines = list(original_lines)
            summaries: list[str] = []

            for s, e, repl, n in validated:
                old_len = e - s + 1
                if repl:
                    rl = repl.splitlines(keepends=True)
                    if original_lines[e].endswith('\n') and rl and not rl[-1].endswith('\n'):
                        rl[-1] += '\n'
                else:
                    rl = []
                new_lines[s:e + 1] = rl
                new_len = len(rl)
                if not repl:
                    summaries.append(f"edit #{n + 1}: deleted lines {s + 1}-{e + 1}")
                elif old_len == new_len:
                    summaries.append(f"edit #{n + 1}: replaced {old_len} lines at {s + 1}-{e + 1}")
                else:
                    summaries.append(f"edit #{n + 1}: replaced {old_len} lines "
                                     f"with {new_len} lines at {s + 1}-{e + 1}")

            # ── Phase 4: write result ──
            new_content = ''.join(new_lines)
            if original_text.endswith('\n') and not new_content.endswith('\n'):
                new_content += '\n'
            if new_content == original_text:
                return (f"Edits processed but resulted in no change for '{target_file}'.", None)

            with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', newline='',
                                            dir=str(file_path.parent)) as tmp:
                tmp.write(new_content)
                temp_path = tmp.name
            os.replace(temp_path, file_path)

            self.applied_arguments = {
                "target_file": target_file,
                "edits": applied_edits,
            }

            return self._build_result(target_file, validated, total_lines,
                                      new_content, any_indent_fixed, any_corrected,
                                      summaries), self.applied_arguments

        except Exception as e:
            return (f"Error applying range replace edits to '{target_file}': {str(e)}", None)

    # ── path resolution ─────────────────────────────────────────────

    def _resolve_path(self, path: str) -> Path:
        path_obj = Path(path)
        return path_obj if path_obj.is_absolute() else self.workspace_root / path_obj

    # ── edit-validation helpers (static — pure logic, no side effects) ──

    @staticmethod
    def _parse_line_range(raw: str, edit_num: int, total_lines: int):
        """Parse '13-15' or '42' → (start_line, end_line); or an error string."""
        try:
            parts = str(raw).strip().split("-")
            if len(parts) == 1:
                sl = el = int(parts[0])
            elif len(parts) == 2:
                sl, el = int(parts[0]), int(parts[1])
            else:
                return f"Error in edit #{edit_num + 1}: remove_line_number must be " \
                       f"like '13-15' or '42', got '{raw}'"
        except (ValueError, AttributeError):
            return f"Error in edit #{edit_num + 1}: remove_line_number must be " \
                   f"like '13-15' or '42', got '{raw}'"
        if sl < 1:
            return f"Error in edit #{edit_num + 1}: start_line must be >= 1, got {sl}"
        if el > total_lines:
            el = total_lines
        if sl > el:
            return f"Error in edit #{edit_num + 1}: start_line ({sl}) must be <= end_line ({el})"
        return (sl, el)

    @staticmethod
    def _maybe_fix_indent(replace_content: str, expected: str, actual_line: str):
        """Auto-adjust indent of *replace_content* when expected/actual differ only in
        leading whitespace.  Returns (content, fixed_bool)."""
        if not replace_content:
            return replace_content, False
        delta = indent_delta(expected, actual_line)
        if delta == 0:
            return replace_content, False
        return adjust_indent(replace_content, delta), True

    @staticmethod
    def _resolve_start_anchor(original_lines, total_lines, start_line,
                              start_content, start_idx):
        """Tolerant (+fallback) start-anchor search.
        Returns (pos, indent_mismatch) or raises _AnchorError."""
        found = find_anchor_tolerant(original_lines, total_lines, start_line, start_content)
        if found is not None:
            return found
        found = find_anchor_anywhere(original_lines, total_lines, start_content)
        if found is not None:
            return found
        ctx_s = max(0, start_idx - 1)
        ctx_e = min(total_lines, start_idx + 4)
        ctx = ''.join(original_lines[ctx_s:ctx_e])
        raise _AnchorError(
            f"start_content does not match file at line {start_line} "
            f"(searched ±{MAX_ANCHOR_SHIFT} and entire file).\n"
            f"File context around line {start_line}:\n"
            f"---\n{ctx}---"
            f"{anchor_hint(original_lines, start_content, start_line, '\n' in start_content)}"
            f"{_NO_CHANGES}"
        )

    @staticmethod
    def _resolve_singleline_start(original_lines, total_lines, start_line,
                                  start_content, start_idx, edit_num):
        """Verify a single-line start anchor; returns (pos, indent_mismatch)
        or raises _AnchorError."""
        actual = normalise(original_lines[start_idx])
        expected = normalise(start_content)
        if actual == expected:
            return (start_idx, False)
        found = find_anchor_tolerant(original_lines, total_lines, start_line, start_content)
        if found is not None:
            return found
        ctx_s = max(0, start_idx - 1)
        ctx_e = min(total_lines, start_idx + 3)
        ctx = ''.join(original_lines[ctx_s:ctx_e])
        raise _AnchorError(
            f"start_content does not match file at line {start_line} "
            f"(searched ±{MAX_ANCHOR_SHIFT}).\n"
            f"Expected: {repr(start_content.rstrip())}\n"
            f"Actual:   {repr(original_lines[start_line - 1].rstrip())}\n"
            f"File context around line {start_line}:\n"
            f"---\n{ctx}---"
            f"{indentation_hint(expected, actual)}"
            f"{anchor_hint(original_lines, start_content, start_line, False)}"
            f"{_NO_CHANGES}"
        )

    @staticmethod
    def _resolve_end_anchor(original_lines, total_lines, end_content,
                            end_line, end_idx, ea_count, edit_num):
        """Verify a multi-line end anchor; returns new end_idx or raises _AnchorError."""
        search_start = end_line - ea_count + 1
        found = find_anchor_tolerant(original_lines, total_lines, search_start, end_content)
        if found is None:
            ctx_s = max(0, end_idx - 2)
            ctx_e = min(total_lines, end_idx + 3)
            ctx = ''.join(original_lines[ctx_s:ctx_e])
            raise _AnchorError(
                f"end_content does not match file at line {end_line} "
                f"(searched ±{MAX_ANCHOR_SHIFT}).\n"
                f"File context around line {end_line}:\n---\n{ctx}---{_NO_CHANGES}"
            )
        return found[0] + ea_count - 1

    @staticmethod
    def _resolve_singleline_end(original_lines, total_lines, end_content,
                                end_line, end_idx, edit_num):
        """Verify a single-line end anchor; returns (pos, _) or raises _AnchorError."""
        actual = normalise(original_lines[end_idx])
        expected = normalise(end_content)
        if actual == expected:
            return (end_idx, False)
        found = find_anchor_tolerant(original_lines, total_lines, end_line, end_content)
        if found is None:
            ctx_s = max(0, end_idx - 1)
            ctx_e = min(total_lines, end_idx + 3)
            ctx = ''.join(original_lines[ctx_s:ctx_e])
            raise _AnchorError(
                f"end_content does not match file at line {end_line} "
                f"(searched ±{MAX_ANCHOR_SHIFT}).\n"
                f"Expected: {repr(end_content.rstrip())}\n"
                f"Actual:   {repr(original_lines[end_line - 1].rstrip())}\n"
                f"File context around line {end_line}:\n---\n{ctx}---"
                f"{indentation_hint(expected, actual)}{_NO_CHANGES}"
            )
        return (found[0], False)

    @staticmethod
    def _compute_end_idx_no_to(start_content, start_idx):
        """Compute end_idx from *start_content* when [TO] marker is absent."""
        blines = start_content.splitlines(keepends=True)
        if len(blines) > 1 and normalise(blines[-1]) == '':
            blines = blines[:-1]
        if len(blines) == 0:
            return start_idx          # empty → single empty-line removal
        return start_idx + len(blines) - 1

    @staticmethod
    def _validate_one_edit(n: int, edit: dict,
                           original_lines, total_lines):
        """Fully validate & resolve a single edit: parse params, resolve
        start + end anchors (with tolerance + indent auto-fix), and build the
        auto-corrected argument form for this edit.

        Returns (start_idx, end_idx, replace_content, corrected, indent_fixed,
        corrected_edit) or an error string. ``corrected_edit`` is the
        normalised {remove_line_number, content_to_remove, replace_content}
        dict that should replace the agent's original edit args.
        """
        # ── 1. Parse line range ──
        outcome = RangeReplaceEditor._parse_line_range(edit.get("remove_line_number", ""), n, total_lines)
        if isinstance(outcome, str):
            return outcome
        start_line, end_line = outcome

        # ── 2. Parse content_to_remove ──
        raw_ctr = str(edit.get("content_to_remove") or "")
        has_to = '\n[TO]\n' in raw_ctr
        if has_to:
            start_content, end_content = raw_ctr.split('\n[TO]\n', 1)
        else:
            start_content, end_content = raw_ctr, ""
        replace_content = str(edit.get("replace_content", ""))
        original_replace = replace_content

        if not end_content and has_to:
            end_content = normalise(original_lines[end_line - 1])

        start_idx = start_line - 1
        end_idx = end_line - 1

        start_is_multi = "\n" in start_content
        end_is_multi = "\n" in end_content
        ea_count = len([l for l in end_content.splitlines() if l.strip()]) if end_is_multi else 1

        corrected = False
        indent_fixed = False

        # ── 3. Resolve start anchor ──
        original_start_idx = start_idx
        if start_is_multi:
            try:
                start_idx, im = RangeReplaceEditor._resolve_start_anchor(
                    original_lines, total_lines, start_line, start_content, start_idx)
            except _AnchorError as e:
                return f"Error in edit #{n + 1}: {e.args[0]}"
            corrected = True
            if im:
                fl = start_content.splitlines()[0]
                al = normalise(original_lines[start_idx])
                replace_content, indent_fixed = RangeReplaceEditor._maybe_fix_indent(replace_content, fl, al)
        else:
            try:
                start_idx, im = RangeReplaceEditor._resolve_singleline_start(
                    original_lines, total_lines, start_line, start_content, start_idx, n)
            except _AnchorError as e:
                return f"Error in edit #{n + 1}: {e.args[0]}"
            if start_idx != original_start_idx:
                corrected = True
            if im:
                actual = normalise(original_lines[start_idx])
                expected = normalise(start_content)
                replace_content, indent_fixed = RangeReplaceEditor._maybe_fix_indent(replace_content, expected, actual)

        # ── 4. Resolve end anchor ──
        try:
            if not has_to:
                end_idx = RangeReplaceEditor._compute_end_idx_no_to(start_content, start_idx)
            elif end_is_multi:
                end_idx = RangeReplaceEditor._resolve_end_anchor(
                    original_lines, total_lines, end_content, end_line, end_idx, ea_count, n)
                corrected = True
            else:
                end_idx, _ = RangeReplaceEditor._resolve_singleline_end(
                    original_lines, total_lines, end_content, end_line, end_idx, n)
                if end_idx != (end_line - 1):
                    corrected = True
        except _AnchorError as e:
            return f"Error in edit #{n + 1}: {e.args[0]}"

        if start_idx > end_idx:
            return (f"Error in edit #{n + 1}: after tolerance search, start_line "
                    f"({start_idx + 1}) > end_line ({end_idx + 1}). Check your line numbers."
                    f"{_NO_CHANGES}")

        corrected = corrected or start_is_multi or end_is_multi

        # ── 5. Build the auto-corrected argument form for this edit ──
        removed = original_lines[start_idx:end_idx + 1]
        sp = removed[0].rstrip('\n')
        ep = removed[-1].rstrip('\n')
        if start_idx == end_idx:
            ctr = sp
        else:
            ctr = sp + '\n[TO]\n' + ep
        corrected_edit = {
            "remove_line_number": f"{start_idx + 1}-{end_idx + 1}",
            "content_to_remove": ctr,
            "replace_content": original_replace,
        }

        return (start_idx, end_idx, replace_content, corrected, indent_fixed,
                corrected_edit)

    @staticmethod
    def _build_result(target_file, validated, total_lines, new_content,
                      any_indent_fixed, any_corrected, summaries):
        """Format the final human-readable result message.
        Returns (result_message, applied_arguments_dict_or_None)."""
        new_total = len(new_content.splitlines())
        delta = new_total - total_lines

        result = (f"✅ Applied {len(validated)} edit(s) to '{target_file}': "
                  f"{'; '.join(summaries)}")
        result += (f"\n📏 File: {total_lines} → {new_total} lines "
                   f"({'+' if delta >= 0 else ''}{delta})")
        if delta != 0:
            result += (f"\n⚠️  Line numbers have shifted by "
                       f"{'+' if delta >= 0 else ''}{delta}.")

        if any_indent_fixed:
            result = ("⚠️  Indentation mismatch detected in content_to_remove — "
                      "replace_content indentation was auto-adjusted to match the file.\n\n"
                      + result)

        if any_corrected:
            result = ("⚠️  Original parameters were auto-corrected. "
                      "No action needed from you.\n\n" + result)

        return result


# ── Pre-processing helper (used by tool_executor before execution) ──

def maybe_truncate_edits(tc: dict) -> None:
    """If a tool call has > MAX_EDITS_PER_CALL edits, truncate to the max."""
    if tc["function"]["name"] != "edit_file":
        return
    import json
    args = json.loads(tc["function"]["arguments"])
    edits = args.get("edits")
    if len(edits) <= RangeReplaceEditor.MAX_EDITS_PER_CALL:
        return
    args["edits"] = edits[:RangeReplaceEditor.MAX_EDITS_PER_CALL]
    tc["function"]["arguments"] = json.dumps(args, ensure_ascii=False)
