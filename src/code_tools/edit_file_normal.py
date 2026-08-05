"""String-replacement editor for the "normal" edit mode.

Aligned with Claude Code / Claw Code / attractor best practices:
exact string matching with uniqueness enforcement.  The LLM provides
the exact ``old_string`` to find, the ``new_string`` to replace it with,
and an optional ``replace_all`` flag.

Key behaviour (matching the ecosystem consensus):
- ``old_string`` MUST be unique in the file unless ``replace_all`` is True.
- If ``old_string`` appears more than once, the edit FAILS with a message
  showing where each match occurs so the model can provide more context.
- Exact match is tried first; a relaxed-whitespace fallback is attempted
  if the exact match fails.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from pathlib import Path
import os
import re
import tempfile


def _normalise(line: str) -> str:
    """Strip leading/trailing whitespace for relaxed content comparison."""
    return line.strip()


class StringReplaceEditor:
    """Orchestrates exact-string-replace edits on a file.

    Simpler and more explicit than the aurora-style anchor-based engine:
    the LLM provides the exact ``old_string`` to find and the exact
    ``new_string`` to replace it with.  No line numbers, no [TO] markers,
    no anchor tolerance.

    Uniqueness is enforced: ``old_string`` must be unique unless
    ``replace_all`` is True.  This prevents accidental edits to the
    wrong part of a file.
    """

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root)
        self.applied_arguments: dict | None = None

    # ── public API ──────────────────────────────────────────────────

    def edit(
        self,
        target_file: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Tuple[str, dict | None]:
        """Apply a string replacement to *target_file*.

        Returns ``(result_message, applied_arguments)``.
        ``applied_arguments`` is ``None`` when the edit could not be
        applied (the file is left unchanged).
        """
        self.applied_arguments = {
            "file": target_file,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        }

        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return (
                    f"Error: File '{target_file}' not found.",
                    None,
                )

            original_text = file_path.read_text(
                encoding="utf-8", errors="ignore"
            ).replace('\r\n', '\n')
            original_len = original_text.count('\n') + 1

            # ── 1. Exact match ───────────────────────────────────
            new_text, match_info = self._try_exact_match(
                original_text, old_string, new_string, replace_all
            )
            if match_info == "applied":
                pass  # success
            elif match_info == "not_found":
                # ── 2. Relaxed-whitespace fallback ───────────────
                new_text, match_info = self._try_relaxed_match(
                    original_text, old_string, new_string, replace_all
                )
            elif match_info == "not_unique":
                return (
                    self._build_not_unique_error(
                        target_file, original_text, old_string
                    ),
                    None,
                )

            if match_info == "not_found":
                return (
                    f"Error: Could not find the exact string to replace in "
                    f"'{target_file}'.\n\n"
                    f"💡 The file content may have changed since you last "
                    f"read it. Re-read the file with read_file() and "
                    f"try again with the current content.\n\n"
                    f"No edits were applied — the file is unchanged.",
                    None,
                )

            if new_text == original_text:
                return (
                    f"Edit processed but resulted in no change for "
                    f"'{target_file}'.  old_string equals new_string.",
                    None,
                )

            # ── Atomic write ─────────────────────────────────────
            with tempfile.NamedTemporaryFile(
                'w', delete=False, encoding='utf-8', newline='',
                dir=str(file_path.parent),
            ) as tmp:
                tmp.write(new_text)
                temp_path = tmp.name
            os.replace(temp_path, file_path)

            new_len = new_text.count('\n') + 1
            delta = new_len - original_len

            result = (
                f"✅ Applied edit to '{target_file}'.\n"
                f"📏 File: {original_len} → {new_len} lines "
                f"({' +' if delta > 0 else ''}{delta})"
            )
            if delta != 0:
                result += (
                    f"\n⚠️  Line numbers have shifted by "
                    f"{'+' if delta > 0 else ''}{delta}."
                )

            return result, self.applied_arguments

        except Exception as e:
            return (
                f"Error applying edit to '{target_file}': {str(e)}",
                None,
            )

    # ── path resolution ────────────────────────────────────────────

    def _resolve_path(self, path: str) -> Path:
        path_obj = Path(path)
        return (
            path_obj if path_obj.is_absolute()
            else self.workspace_root / path_obj
        )

    # ── exact matching ─────────────────────────────────────────────

    @staticmethod
    def _try_exact_match(
        text: str, old: str, new: str, replace_all: bool,
    ) -> Tuple[str, str]:
        """Try exact string matching.

        Returns ``(new_text, status)`` where status is one of
        ``"applied"``, ``"not_found"``, ``"not_unique"``.
        """
        if not old:
            return text, "not_found"

        count = text.count(old)
        if count == 0:
            return text, "not_found"

        if count > 1 and not replace_all:
            return text, "not_unique"

        return text.replace(old, new), "applied"

    # ── relaxed matching ───────────────────────────────────────────

    @staticmethod
    def _try_relaxed_match(
        text: str, old: str, new: str, replace_all: bool,
    ) -> Tuple[str, str]:
        """Relaxed-whitespace fallback: compare lines by stripped content.

        When exact matching fails, this tries to find *old* by comparing
        each line's non-whitespace content.  If found, the original
        indentation is preserved in the replacement.
        """
        if not old.strip():
            return text, "not_found"

        old_lines = [_normalise(l) for l in old.splitlines()]
        text_lines = text.splitlines()
        text_len = len(text_lines)
        old_len = len(old_lines)

        # ── Find *all* match positions ──
        positions: List[int] = []
        for i in range(text_len - old_len + 1):
            if all(
                _normalise(text_lines[i + j]) == old_lines[j]
                for j in range(old_len)
            ):
                positions.append(i)

        if not positions:
            return text, "not_found"

        if len(positions) > 1 and not replace_all:
            return text, "not_unique"

        # ── Apply replacements (bottom-to-top for correct offsets) ──
        new_lines_list = new.splitlines()
        for i in reversed(positions):
            # Adjust replacement indentation to match original
            adjusted_new = StringReplaceEditor._adjust_indent(
                text_lines[i:i + old_len], new_lines_list
            )
            # Reconstruct: find byte offset of line i
            byte_offset = sum(
                len(text_lines[k]) + 1 for k in range(i)
            )
            old_block = '\n'.join(text_lines[i:i + old_len])
            text = (
                text[:byte_offset]
                + adjusted_new
                + text[byte_offset + len(old_block):]
            )

        return text, "applied"

    @staticmethod
    def _adjust_indent(
        orig_lines: List[str], new_lines: List[str],
    ) -> str:
        """Adjust *new_lines* indentation to match *orig_lines*."""
        if not new_lines or not new_lines[0].strip():
            return '\n'.join(new_lines)

        orig_first = orig_lines[0]
        orig_indent = len(orig_first) - len(orig_first.lstrip())
        new_indent = len(new_lines[0]) - len(new_lines[0].lstrip())
        delta = orig_indent - new_indent

        if delta == 0:
            return '\n'.join(new_lines)

        adjusted = []
        for line in new_lines:
            if not line.strip():
                adjusted.append(line)
            elif delta > 0:
                adjusted.append(' ' * delta + line)
            else:
                strip_count = min(abs(delta), len(line) - len(line.lstrip()))
                adjusted.append(line[strip_count:])
        return '\n'.join(adjusted)

    # ── error helpers ──────────────────────────────────────────────

    @staticmethod
    def _build_not_unique_error(
        target_file: str, text: str, old_string: str,
    ) -> str:
        """Build a detailed error when *old_string* is not unique."""
        lines = text.split('\n')
        matches = []
        for i, line in enumerate(lines, 1):
            if old_string in line:
                # Show a snippet around each match
                start = max(0, i - 2)
                end = min(len(lines), i + 2)
                snippet = '\n'.join(
                    f"  {j:>5}  {lines[j - 1]}"
                    for j in range(start + 1, end + 1)
                )
                matches.append(
                    f"Match at line {i}:\n{snippet}"
                )

        if len(matches) > 6:
            shown = matches[:6]
            msg = (
                f"Error: old_string is not unique in '{target_file}' — "
                f"found {len(matches)} occurrences.\n\n"
                f"The string to replace must uniquely identify the target "
                f"location. Include more surrounding context to make it "
                f"unique, or use replace_all=true to replace all occurrences.\n\n"
                f"Occurrences found (showing first 6 of {len(matches)}):\n\n"
            )
        else:
            shown = matches
            msg = (
                f"Error: old_string is not unique in '{target_file}' — "
                f"found {len(matches)} occurrences.\n\n"
                f"The string to replace must uniquely identify the target "
                f"location. Include more surrounding context to make it "
                f"unique, or use replace_all=true to replace all occurrences.\n\n"
                f"Occurrences found:\n\n"
            )

        return msg + '\n\n'.join(shown)
