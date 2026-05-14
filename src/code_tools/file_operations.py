# --- Imports ---
import os
import shutil
import subprocess
import tempfile
import difflib
import json
import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple
# Support both package-context and standalone loading (for testing).
try:
    from ..config import EDIT_ZONE_MARKER
except (ImportError, ValueError):
    EDIT_ZONE_MARKER = "__EDIT_ZONE__"
try:
    from ..code_sandbox import WORKSPACE
except (ImportError, ValueError):
    WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))

logger = logging.getLogger(__name__)

# --- Constants ---
WILDCARD_SENTINEL = EDIT_ZONE_MARKER  # Backward compatibility with old constant name
ANCHOR_SEPARATOR = "\n...\n"
MAX_ANCHOR_SHIFT = 3

# --- File Access Callbacks ---
_on_file_read: Optional[Callable[[str, str, str], None]] = None
_on_file_write: Optional[Callable[[str, str], None]] = None
_current_conversation_id: Optional[str] = None


def set_file_tracking_callbacks(
    on_read: Optional[Callable[[str, str, str], None]] = None,
    on_write: Optional[Callable[[str, str], None]] = None
):
    global _on_file_read, _on_file_write
    _on_file_read = on_read
    _on_file_write = on_write


def set_current_conversation(conversation_id: Optional[str]):
    global _current_conversation_id
    _current_conversation_id = conversation_id


def _notify_file_read(file_path: str, content: str):
    if _on_file_read and _current_conversation_id:
        try:
            _on_file_read(_current_conversation_id, file_path, content)
        except Exception:
            pass


def _notify_file_write(file_path: str):
    if _on_file_write and _current_conversation_id:
        try:
            _on_file_write(_current_conversation_id, file_path)
        except Exception:
            pass

# --- File Operations Class ---
class FileOperations:
    """File operations tool for reading, editing, and managing files."""

    def __init__(self, workspace_root: str = None):
        self.workspace_root = WORKSPACE

    # --- File Reading ---
    def read_file(self, target_file: str) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                return f"Error: File '{target_file}' does not exist"
            if not file_path.is_file():
                return f"Error: '{target_file}' is not a file"
            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')
                _notify_file_read(target_file, content)
            except Exception:
                pass
            return f"The file '{target_file}' is opened in the code interpreter."
        except Exception as e:
            return f"Error reading file '{target_file}': {str(e)}"

    # --- Content-Anchor Edit (replaces the old range-based edit) ---
    # The agent supplies approximate line numbers and anchor text.
    # The tool searches for anchors within a ±3-line window with two-pass
    # matching (strict → relaxed whitespace tolerance).  On success it
    # emits a self-correction marker so main_flow can retroactively patch
    # the conversation's tool call arguments to look perfectly correct.
    # ------------------------------------------------------------------

    def range_replace_edit(
        self,
        target_file: str,
        start_line: int,
        content_to_remove: str,
        end_line: int,
        replace_content: str = "",
    ) -> str:
        self._last_target = target_file

        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return f"Error: File '{target_file}' not found."

            original_text = file_path.read_text(encoding="utf-8", errors="ignore")
            original_text = original_text.replace("\r\n", "\n")
            _notify_file_read(target_file, original_text)

            lines = original_text.splitlines(keepends=True)
            total_lines = len(lines)

            # Parse anchors ---------------------------------------------------
            start_anchor_str, end_anchor_str = self._parse_anchors(content_to_remove)
            start_anchor_lines = start_anchor_str.splitlines(keepends=True)
            end_anchor_lines = end_anchor_str.splitlines(keepends=True)

            if not start_anchor_lines or not end_anchor_lines:
                return (
                    "Error: content_to_remove must contain both start and end "
                    "anchors separated by a line containing only '...'."
                )

            # Search for anchors ------------------------------------------------
            start_idx, start_err = self._find_anchor(
                lines, start_anchor_lines, start_line - 1, MAX_ANCHOR_SHIFT, "start"
            )
            if start_idx is None:
                return start_err

            end_idx, end_err = self._find_anchor(
                lines, end_anchor_lines, end_line - 1, MAX_ANCHOR_SHIFT, "end"
            )
            if end_idx is None:
                return end_err

            actual_start_idx = start_idx
            actual_end_idx = end_idx + len(end_anchor_lines) - 1

            if actual_start_idx > actual_end_idx:
                return (
                    f"Error: matched start anchor (line {start_idx + 1}) appears "
                    f"after matched end anchor (line {end_idx + 1}).  Check your "
                    f"anchor text and line numbers."
                )

            # Apply replacement -------------------------------------------------
            new_lines = list(lines)
            if replace_content:
                replace_lines = replace_content.splitlines(keepends=True)
                if (
                    lines[actual_end_idx].endswith("\n")
                    and replace_lines
                    and not replace_lines[-1].endswith("\n")
                ):
                    replace_lines[-1] = replace_lines[-1] + "\n"
            else:
                replace_lines = []

            old_range_len = actual_end_idx - actual_start_idx + 1
            new_lines[actual_start_idx : actual_end_idx + 1] = replace_lines
            new_range_len = len(replace_lines)
            new_content = "".join(new_lines)

            if original_text.endswith("\n") and not new_content.endswith("\n"):
                new_content += "\n"

            if new_content == original_text:
                return (
                    f"No change — the replacement is identical to the "
                    f"matched region in '{target_file}'."
                )

            # Atomic write ------------------------------------------------------
            with tempfile.NamedTemporaryFile(
                "w", delete=False, encoding="utf-8", newline="",
                dir=str(file_path.parent),
            ) as tmp:
                tmp.write(new_content)
                temp_path = tmp.name
            os.replace(temp_path, file_path)
            _notify_file_write(target_file)

            # Result ------------------------------------------------------------
            new_total = len(new_content.splitlines())
            line_delta = new_total - total_lines

            if not replace_content:
                action = (
                    f"deleted {old_range_len} lines "
                    f"(lines {actual_start_idx + 1}-{actual_end_idx + 1})"
                )
            elif old_range_len == new_range_len:
                action = (
                    f"replaced {old_range_len} lines "
                    f"(lines {actual_start_idx + 1}-{actual_end_idx + 1})"
                )
            else:
                action = (
                    f"replaced {old_range_len} lines with {new_range_len} lines "
                    f"(lines {actual_start_idx + 1}-{actual_end_idx + 1})"
                )

            result = f"✅ Applied edit to '{target_file}': {action}."
            result += (
                f"\n📏 File: {total_lines} → {new_total} lines "
                f"({chr(43) if line_delta >= 0 else ''}{line_delta})"
            )
            if line_delta != 0:
                result += (
                    f"\n⚠️  Line numbers have shifted by "
                    f"{chr(43) if line_delta >= 0 else ''}{line_delta}."
                )

            # Self-correction ---------------------------------------------------
            start_drift = actual_start_idx - (start_line - 1)
            end_drift = actual_end_idx - (end_line - 1)
            correction = self._build_correction(
                lines, start_anchor_lines, end_anchor_lines,
                start_line, end_line, actual_start_idx, actual_end_idx,
                content_to_remove, replace_content,
                start_drift, end_drift,
            )
            if correction:
                result = (
                    "⚠️  Original parameters were auto-corrected. "
                    "No action needed from you.\n\n" + result
                )
                result += "\n\n<!--SELF_CORRECT:" + json.dumps(correction) + "-->"
            return result

        except Exception as e:
            return f"Error applying edit to '{target_file}': {str(e)}"

    # --- Full File Write ---
    def full_file_write(self, target_file: str, code_edit: str) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                _notify_file_read(target_file, "")
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code_edit)
                _notify_file_write(target_file)
                return f"Created new file: {target_file}"
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                current_content = f.read().replace('\r\n', '\n')
            _notify_file_read(target_file, current_content)
            new_content = self._apply_edit(current_content, code_edit)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            _notify_file_write(target_file)
            return f"Successfully edited {target_file}"
        except Exception as e:
            return f"Error editing file '{target_file}': {str(e)}"

    # --- File/Directory Deletion ---
    def delete_file(self, target_file: str) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                return f"File '{target_file}' does not exist"
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding='utf-8', errors='replace')
                    _notify_file_read(target_file, content)
                except Exception:
                    pass
                file_path.unlink()
                _notify_file_write(target_file)
                return f"Successfully deleted file: {target_file}"
            elif file_path.is_dir():
                shutil.rmtree(file_path)
                return f"Successfully deleted directory: {target_file}"
            else:
                return f"Error: '{target_file}' is not a regular file or directory"
        except Exception as e:
            return f"Error deleting '{target_file}': {str(e)}"

    # --- Directory Listing ---
    def list_dir(self, relative_workspace_path: str = "") -> str:
        try:
            dir_path = self._resolve_path(relative_workspace_path)
            if not dir_path.exists():
                return f"Error: Directory '{relative_workspace_path}' does not exist"
            if not dir_path.is_dir():
                return f"Error: '{relative_workspace_path}' is not a directory"
            items = []
            for item in sorted(dir_path.iterdir()):
                if item.is_dir():
                    items.append(f"📁 {item.name}/")
                else:
                    size = item.stat().st_size
                    items.append(f"📄 {item.name} ({size} bytes)")
            display_path = relative_workspace_path if relative_workspace_path else str(dir_path)
            if not items:
                return f"Directory '{display_path}' is empty"
            return f"Contents of '{display_path}':\n" + '\n'.join(items)
        except Exception as e:
            return f"Error listing directory '{relative_workspace_path}': {str(e)}"

    # --- Fuzzy File Search ---
    def file_search(self, query: str, max_results: int = 10) -> str:
        try:
            results = []
            query_lower = query.lower()
            for file_path in self.workspace_root.rglob('*'):
                if file_path.is_file():
                    name = file_path.name.lower()
                    relative_path = str(file_path.relative_to(self.workspace_root))
                    if query_lower in name or query_lower in relative_path.lower():
                        results.append({
                            'path': relative_path,
                            'name': file_path.name,
                            'size': file_path.stat().st_size
                        })
            results.sort(key=lambda x: (
                0 if query_lower == x['name'].lower() else 1,
                x['name'].lower()
            ))
            if not results:
                return f"No files found matching '{query}'"
            output = [f"Found {len(results)} files matching '{query}':\n"]
            for result in results[:max_results]:
                output.append(f"📄 {result['path']} ({result['size']} bytes)")
            if len(results) > max_results:
                output.append(f"\n... and {len(results) - max_results} more files")
            return '\n'.join(output)
        except Exception as e:
            return f"Error searching for files: {str(e)}"

    # --- Path Resolution ---
    def _resolve_path(self, path: str) -> Path:
        path_obj = Path(path)
        if path_obj.is_absolute():
            return path_obj
        else:
            return self.workspace_root / path

    def _apply_edit(self, current_content: str, code_edit: str) -> str:
        return code_edit

    # ---- Internal: anchor parsing -------------------------------------------

    def _parse_anchors(self, raw: str) -> Tuple[str, str]:
        parts = raw.split(ANCHOR_SEPARATOR, 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        lines_raw = raw.splitlines(keepends=True)
        for i, line in enumerate(lines_raw):
            if line.strip() == "...":
                start = "".join(lines_raw[:i])
                end = "".join(lines_raw[i + 1 :])
                if start.strip() and end.strip():
                    return start, end
        return raw, ""

    # ---- Internal: anchor search --------------------------------------------

    def _find_anchor(
        self,
        lines: List[str],
        anchor_lines: List[str],
        expected_idx: int,
        max_shift: int,
        label: str,
    ) -> Tuple[Optional[int], Optional[str]]:
        anchor_count = len(anchor_lines)
        total = len(lines)

        candidates: List[Tuple[int, int]] = []
        for shift in range(max_shift + 1):
            for direction in (0, 1, -1):
                if shift == 0 and direction != 0:
                    continue
                pos = expected_idx + direction * shift
                if 0 <= pos <= total - anchor_count:
                    candidates.append((pos, abs(pos - expected_idx)))
        seen = set()
        unique = []
        for pos, dist in candidates:
            if pos not in seen:
                seen.add(pos)
                unique.append((pos, dist))
        unique.sort(key=lambda x: x[1])

        for pos, _dist in unique:
            if self._blocks_match(lines, pos, anchor_lines, strict=True):
                return pos, None
        for pos, _dist in unique:
            if self._blocks_match(lines, pos, anchor_lines, strict=False):
                return pos, None

        context_start = max(0, expected_idx - 2)
        context_end = min(total, expected_idx + anchor_count + 3)
        context = "".join(lines[context_start:context_end])
        anchor_display = "".join(anchor_lines).rstrip("\n")
        return None, (
            f"Error: could not find {label} anchor in "
            f"'{getattr(self, '_last_target', '?')}' "
            f"within ±{max_shift} lines of line {expected_idx + 1}.\n"
            f"Anchor text:\n---\n{anchor_display}\n---\n"
            f"File context around line {expected_idx + 1}:\n"
            f"---\n{context}---\n"
            f"Tip: make sure the anchor text matches the file content "
            f"exactly (trailing whitespace is ignored)."
        )

    # ---- Internal: block matching -------------------------------------------

    @staticmethod
    def _blocks_match(
        lines: List[str], start_pos: int,
        anchor_lines: List[str], strict: bool,
    ) -> bool:
        for i, anchor_line in enumerate(anchor_lines):
            file_line = lines[start_pos + i]
            an = anchor_line.rstrip("\n").rstrip()
            fl = file_line.rstrip("\n").rstrip()
            if strict:
                if an != fl:
                    return False
            else:
                if an.strip() != fl.strip():
                    return False
        return True

    # ---- Internal: self-correction ------------------------------------------

    def _build_correction(
        self, lines, start_anchor_lines, end_anchor_lines,
        start_line, end_line, actual_start_idx, actual_end_idx,
        content_to_remove, replace_content, start_drift, end_drift,
    ) -> Optional[Dict]:
        needs_correction = (start_drift != 0 or end_drift != 0)
        if not needs_correction:
            sa_ok = self._blocks_match(
                lines, actual_start_idx, start_anchor_lines, strict=True)
            ea_start = actual_end_idx - len(end_anchor_lines) + 1
            ea_ok = self._blocks_match(
                lines, ea_start, end_anchor_lines, strict=True)
            if sa_ok and ea_ok:
                return None
        start_exact = "".join(
            lines[actual_start_idx : actual_start_idx + len(start_anchor_lines)])
        end_exact = "".join(
            lines[actual_end_idx - len(end_anchor_lines) + 1 : actual_end_idx + 1])
        corrected_ctr = start_exact + ANCHOR_SEPARATOR + end_exact
        return {
            "start_line": actual_start_idx + 1,
            "end_line": actual_end_idx + 1,
            "content_to_remove": corrected_ctr,
            "replace_content": replace_content,
        }


# --- Public Tool Wrappers ---

def read_file_tool(target_file: str) -> str:
    file_ops = FileOperations()
    return file_ops.read_file(target_file)


def range_replace_edit_tool(
    target_file: str,
    start_line: int,
    content_to_remove: str,
    end_line: int,
    replace_content: str = "",
) -> str:
    """Content-anchor file edit — the ``edit_file`` tool."""
    ops = FileOperations()
    return ops.range_replace_edit(
        target_file, start_line, content_to_remove, end_line, replace_content
    )


def full_file_write_tool(target_file: str, code_edit: str) -> str:
    ops = FileOperations()
    return ops.full_file_write(target_file, code_edit)


def delete_file_tool(target_file: str) -> str:
    ops = FileOperations()
    return ops.delete_file(target_file)


def list_dir_tool(relative_workspace_path: str = "") -> str:
    ops = FileOperations()
    return ops.list_dir(relative_workspace_path)


def file_search_tool(query: str) -> str:
    ops = FileOperations()
    return ops.file_search(query)


def close_file_tool(target_file: str) -> str:
    return f"Closed '{target_file}' from code interpreter view."
