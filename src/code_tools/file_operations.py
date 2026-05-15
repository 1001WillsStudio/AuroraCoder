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
from typing import List, Dict, Any, Optional, Callable
from ..config import EDIT_ZONE_MARKER
from ..code_sandbox import WORKSPACE

logger = logging.getLogger(__name__)

# --- Constants ---
WILDCARD_SENTINEL = EDIT_ZONE_MARKER  # Backward compatibility with old constant name
MAX_ANCHOR_SHIFT = 3  # ±3 line tolerance for anchor verification

# --- File Access Callbacks ---
# These callbacks allow external systems (like web API) to track file operations
# for diffing purposes. Set by the web API when it initializes.
_on_file_read: Optional[Callable[[str, str, str], None]] = None  # (conversation_id, file_path, content)
_on_file_write: Optional[Callable[[str, str], None]] = None  # (conversation_id, file_path)
_current_conversation_id: Optional[str] = None


def set_file_tracking_callbacks(
    on_read: Optional[Callable[[str, str, str], None]] = None,
    on_write: Optional[Callable[[str, str], None]] = None
):
    """
    Set callbacks for file access tracking.
    
    Args:
        on_read: Called when a file is read. Args: (conversation_id, file_path, content)
        on_write: Called when a file is written/edited. Args: (conversation_id, file_path)
    """
    global _on_file_read, _on_file_write
    _on_file_read = on_read
    _on_file_write = on_write


def set_current_conversation(conversation_id: Optional[str]):
    """Set the current conversation ID for file tracking."""
    global _current_conversation_id
    _current_conversation_id = conversation_id


def _notify_file_read(file_path: str, content: str):
    """Notify that a file was read (for snapshotting)."""
    if _on_file_read and _current_conversation_id:
        try:
            _on_file_read(_current_conversation_id, file_path, content)
        except Exception:
            pass  # Don't let tracking errors break file operations


def _notify_file_write(file_path: str):
    """Notify that a file was written (for diff tracking)."""
    if _on_file_write and _current_conversation_id:
        try:
            _on_file_write(_current_conversation_id, file_path)
        except Exception:
            pass  # Don't let tracking errors break file operations

# --- Edit-file pre/post-processing ---

_EDIT_SELF_CORRECT_RE = re.compile(r'\n?\n?<!--SELF_CORRECT:(.*?)-->', re.DOTALL)
MAX_EDITS_PER_CALL = 3


def maybe_truncate_edits(tc: Dict) -> None:
    """If *tc* is an edit_file call with >MAX_EDITS_PER_CALL edits, truncate
    the arguments to just the first MAX_EDITS_PER_CALL in-place."""
    if tc["function"]["name"] != "edit_file":
        return
    try:
        args = json.loads(tc["function"]["arguments"])
    except (json.JSONDecodeError, TypeError):
        return
    edits = args.get("edits")
    if not isinstance(edits, list) or len(edits) <= MAX_EDITS_PER_CALL:
        return
    args["edits"] = edits[:MAX_EDITS_PER_CALL]
    tc["function"]["arguments"] = json.dumps(args, ensure_ascii=False)


def apply_self_correction(tc: Dict, result: str) -> str:
    """
    If *result* contains a <!--SELF_CORRECT:{...}--> marker, strip it, parse the
    correction JSON, and update *tc*["function"]["arguments"] in-place.

    *tc* is the same dict that lives in the assistant message already appended
    to *messages*, so in-place modification keeps history consistent.

    Returns the cleaned result string.
    """
    match = _EDIT_SELF_CORRECT_RE.search(result)
    if not match:
        return result

    try:
        correction = json.loads(match.group(1))
    except json.JSONDecodeError:
        return _EDIT_SELF_CORRECT_RE.sub('', result)

    # Patch the tool call arguments in-place.
    tc["function"]["arguments"] = json.dumps(correction, ensure_ascii=False)

    return _EDIT_SELF_CORRECT_RE.sub('', result)

# --- File Operations Class ---
class FileOperations:
    """File operations tool for reading, editing, and managing files."""

    def __init__(self, workspace_root: str = None):
        self.workspace_root = WORKSPACE

    # --- File Reading ---
    def read_file(self, target_file: str) -> str:
        """
        Checks for a file's existence and confirms it can be opened.
        The actual file content will be displayed by the code interpreter.
        """
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                return f"Error: File '{target_file}' does not exist"
            if not file_path.is_file():
                return f"Error: '{target_file}' is not a file"
            
            # Snapshot the file content for diff tracking
            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')
                _notify_file_read(target_file, content)
            except Exception:
                pass  # Non-critical for the main operation
            
            return f"The file '{target_file}' is opened in the code interpreter."
        except Exception as e:
            return f"Error reading file '{target_file}': {str(e)}"

    # --- Range Replace Edit (multi-edit, anchor-based, with ±3 line tolerance) ---
    def range_replace_edit(self, target_file: str, edits: List[Dict[str, Any]]) -> str:
        """
        Apply one or more range-based edits to a file.

        Same interface as the strict version, but with ±MAX_ANCHOR_SHIFT line
        tolerance for start_line_content / end_line_content verification.
        Two-pass matching: strict (trailing whitespace ignored) then relaxed
        (leading whitespace also ignored).  When anchors are found at
        different positions than specified, a self-correction marker is
        emitted so main_flow can patch the conversation history.

        Each edit specifies a line range (start_line..end_line) and replacement
        content.  start_line_content / end_line_content are single-line verification
        strings to ensure the file hasn't drifted.

        Args:
            target_file: Path to the file to edit (relative to workspace)
            edits: List of edit dicts, each with:
                start_line (int): 1-based line number where the range begins
                start_line_content (str): Single line of text at start_line for verification
                end_line (int): 1-based line number where the range ends
                end_line_content (str): Single line of text at end_line for verification
                replace_content (str): New content to insert (replaces everything
                    from start_line through end_line inclusive; empty string to delete)

        Returns:
            Success message with summary of changes, or error description
        """
        try:

            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return f"Error: File '{target_file}' not found."

            original_text = file_path.read_text(encoding="utf-8", errors="ignore").replace('\r\n', '\n')

            # Snapshot original content for diff tracking
            _notify_file_read(target_file, original_text)
            original_lines = original_text.splitlines(keepends=True)
            total_lines = len(original_lines)

            # --- Helper: normalise a single line for comparison ---
            def _normalise_line(line: str) -> str:
                """Strip trailing whitespace (trailing spaces usually have no meaning)."""
                return line.rstrip('\n').rstrip()

            def _indentation_hint(expected: str, actual: str) -> str:
                """If content matches ignoring leading whitespace, return a hint."""
                if expected.strip() == actual.strip() and expected != actual:
                    exp_indent = len(expected) - len(expected.lstrip())
                    act_indent = len(actual) - len(actual.lstrip())
                    return (f"\n⚠️  Content matches but indentation differs — "
                            f"expected {exp_indent} leading spaces, "
                            f"got {act_indent}.")
                return ""

            _NO_CHANGES = "\nNo edits were applied — the file is unchanged."

            # --- Find anchor within ±MAX_ANCHOR_SHIFT lines ---
            # Handles both single-line and multi-line anchor content.
            # For multi-line: matches the block starting at candidate position.
            # Returns the 0-based index of the first matched line, or None.

            def _block_match(start_pos: int, anchor_lines: List[str], strict: bool) -> bool:
                if start_pos < 0 or start_pos + len(anchor_lines) > total_lines:
                    return False
                for j, anchor_line in enumerate(anchor_lines):
                    an = anchor_line.rstrip('\n').rstrip()
                    fl = original_lines[start_pos + j].rstrip('\n').rstrip()
                    if strict:
                        if an != fl:
                            return False
                    else:
                        if an.strip() != fl.strip():
                            return False
                return True

            def _candidates(expected_line_num: int, block_len: int) -> List[int]:
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

            def _find_anchor_tolerant(expected_line_num: int, expected_content: str) -> Optional[int]:
                """Search for anchor content within ±MAX_ANCHOR_SHIFT lines.
                Supports single-line and multi-line content.
                Returns 0-based index of first matched line, or None."""
                # Empty/whitespace-only content matches any empty line
                if not expected_content.strip():
                    positions = _candidates(expected_line_num, 1)
                    for pos in positions:
                        if original_lines[pos].strip() == '':
                            return pos
                    return None

                anchor_lines = expected_content.splitlines(keepends=True)
                if not anchor_lines:
                    return None
                # Drop trailing empty line from splitlines artifact,
                # but keep it if it's the only line (genuinely empty anchor).
                if len(anchor_lines) > 1 and anchor_lines[-1].strip() == '':
                    anchor_lines = anchor_lines[:-1]

                positions = _candidates(expected_line_num, len(anchor_lines))
                # Pass 1: strict
                for pos in positions:
                    if _block_match(pos, anchor_lines, strict=True):
                        return pos
                # Pass 2: relaxed whitespace
                for pos in positions:
                    if _block_match(pos, anchor_lines, strict=False):
                        return pos
                return None

            # --- Validate every edit before applying any ---
            validated_edits = []  # (start_idx, end_idx, replace_content, edit_idx)
            any_correction_needed = False
            corrected_edits_for_marker = []

            for i, edit in enumerate(edits):
                start_line = edit.get("start_line")
                start_content = str(edit.get("start_line_content") or edit.get("start_content") or "")
                end_line = edit.get("end_line")
                end_content = str(edit.get("end_line_content") or edit.get("end_content") or "")
                replace_content = str(edit.get("replace_content", ""))

                # --- Defaults: end_line → start_line, end_content → file ---
                if end_line is None:
                    end_line = start_line
                if not isinstance(end_line, int) or end_line < 1:
                    return (f"Error in edit #{i + 1}: end_line must be a positive integer, "
                            f"got {end_line}")
                # Clamp end_line that points past EOF (code interpreter
                # sometimes shows a phantom empty line after the last real line).
                if end_line > total_lines:
                    end_line = total_lines
                if start_line > end_line:
                    return (f"Error in edit #{i + 1}: start_line ({start_line}) must be "
                            f"<= end_line ({end_line})")
                if not end_content:
                    end_content = _normalise_line(original_lines[end_line - 1])

                # Validate line numbers
                if not isinstance(start_line, int) or start_line < 1:
                    return (f"Error in edit #{i + 1}: start_line must be a positive integer, "
                            f"got {start_line}")

                start_idx = start_line - 1  # 0-based
                end_idx = end_line - 1      # 0-based

                # Count how many file lines the anchor content spans
                start_is_multiline = "\n" in start_content
                end_is_multiline = "\n" in end_content
                start_anchor_line_count = len([l for l in start_content.splitlines() if l.strip()]) if start_is_multiline else 1
                end_anchor_line_count = len([l for l in end_content.splitlines() if l.strip()]) if end_is_multiline else 1

                # Verify start anchor (with ±MAX_ANCHOR_SHIFT tolerance)
                start_corrected = False
                if start_is_multiline:
                    # Multi-line start content: find the block
                    found_idx = _find_anchor_tolerant(start_line, start_content)
                    if found_idx is not None:
                        start_idx = found_idx
                        start_corrected = True
                    else:
                        context_start = max(0, start_idx - 1)
                        context_end = min(total_lines, start_idx + 4)
                        context = ''.join(original_lines[context_start:context_end])
                        return (f"Error in edit #{i + 1}: start_content does not match "
                                f"file at line {start_line} (also searched ±{MAX_ANCHOR_SHIFT} lines).\n"
                                f"File context around line {start_line}:\n"
                                f"---\n{context}---"
                                f"{_NO_CHANGES}")
                else:
                    actual_start = _normalise_line(original_lines[start_idx])
                    expected_start = _normalise_line(start_content)
                    if actual_start != expected_start:
                        found_idx = _find_anchor_tolerant(start_line, start_content)
                        if found_idx is not None:
                            start_idx = found_idx
                            start_corrected = True
                        else:
                            context_start = max(0, start_idx - 1)
                            context_end = min(total_lines, start_idx + 3)
                            context = ''.join(original_lines[context_start:context_end])
                            indent_hint = _indentation_hint(expected_start, actual_start)
                            return (f"Error in edit #{i + 1}: start_content does not match "
                                    f"file at line {start_line} (also searched ±{MAX_ANCHOR_SHIFT} lines).\n"
                                    f"Expected: {repr(start_content.rstrip())}\n"
                                    f"Actual:   {repr(original_lines[start_line - 1].rstrip())}\n"
                                    f"File context around line {start_line}:\n"
                                    f"---\n{context}---"
                                    f"{indent_hint}"
                                    f"{_NO_CHANGES}")

                # Verify end anchor (with ±MAX_ANCHOR_SHIFT tolerance)
                # For end anchor, the expected_line_num points to the LAST line
                # of the block, so we search for the block ending at that line.
                end_corrected = False
                if end_is_multiline:
                    # Multi-line end: search for block whose last line is near end_line
                    end_search_start = end_line - end_anchor_line_count + 1
                    found_idx = _find_anchor_tolerant(end_search_start, end_content)
                    if found_idx is not None:
                        end_idx = found_idx + end_anchor_line_count - 1
                        end_corrected = True
                    else:
                        context_start = max(0, end_idx - 2)
                        context_end = min(total_lines, end_idx + 3)
                        context = ''.join(original_lines[context_start:context_end])
                        return (f"Error in edit #{i + 1}: end_content does not match "
                                f"file at line {end_line} (also searched ±{MAX_ANCHOR_SHIFT} lines).\n"
                                f"File context around line {end_line}:\n"
                                f"---\n{context}---"
                                f"{_NO_CHANGES}")
                else:
                    actual_end = _normalise_line(original_lines[end_idx])
                    expected_end = _normalise_line(end_content)
                    if actual_end != expected_end:
                        found_idx = _find_anchor_tolerant(end_line, end_content)
                        if found_idx is not None:
                            end_idx = found_idx
                            end_corrected = True
                        else:
                            context_start = max(0, end_idx - 1)
                            context_end = min(total_lines, end_idx + 3)
                            context = ''.join(original_lines[context_start:context_end])
                            indent_hint = _indentation_hint(expected_end, actual_end)
                            return (f"Error in edit #{i + 1}: end_content does not match "
                                    f"file at line {end_line} (also searched ±{MAX_ANCHOR_SHIFT} lines).\n"
                                    f"Expected: {repr(end_content.rstrip())}\n"
                                    f"Actual:   {repr(original_lines[end_line - 1].rstrip())}\n"
                                    f"File context around line {end_line}:\n"
                                    f"---\n{context}---"
                                    f"{indent_hint}"
                                    f"{_NO_CHANGES}")

                if start_idx > end_idx:
                    return (f"Error in edit #{i + 1}: after tolerance search, start_line "
                            f"({start_idx + 1}) > end_line ({end_idx + 1}). "
                            f"Check your line numbers and anchor content."
                            f"{_NO_CHANGES}")

                validated_edits.append((start_idx, end_idx, replace_content, i))

                if start_corrected or end_corrected or start_is_multiline or end_is_multiline:
                    any_correction_needed = True

                corrected_edits_for_marker.append({
                    "start_line": start_idx + 1,
                    "start_line_content": _normalise_line(original_lines[start_idx]),
                    "end_line": end_idx + 1,
                    "end_line_content": _normalise_line(original_lines[end_idx]),
                    "replace_content": replace_content,
                })

            # Check for overlapping edits
            validated_edits_sorted = sorted(validated_edits, key=lambda e: e[0])
            for a_i in range(len(validated_edits_sorted) - 1):
                a_start, a_end, _, a_num = validated_edits_sorted[a_i]
                b_start, b_end, _, b_num = validated_edits_sorted[a_i + 1]
                if a_end >= b_start:
                    return (f"Error: edit #{a_num + 1} (lines {a_start + 1}-{a_end + 1}) "
                            f"overlaps edit #{b_num + 1} (lines {b_start + 1}-{b_end + 1}). "
                            f"Split overlapping edits into separate edit_file calls."
                            f"{_NO_CHANGES}")

            # --- Apply edits bottom-to-top to preserve line numbers ---
            validated_edits.sort(key=lambda e: e[0], reverse=True)

            new_lines = list(original_lines)
            summary_parts = []

            for start_idx, end_idx, replace_content, edit_num in validated_edits:
                old_range_len = end_idx - start_idx + 1

                # Build replacement lines, preserving newline conventions
                if replace_content:
                    replace_lines = replace_content.splitlines(keepends=True)
                    if original_lines[end_idx].endswith('\n') and replace_lines and not replace_lines[-1].endswith('\n'):
                        replace_lines[-1] = replace_lines[-1] + '\n'
                else:
                    replace_lines = []

                new_lines[start_idx:end_idx + 1] = replace_lines

                new_range_len = len(replace_lines)
                if not replace_content:
                    summary_parts.append(f"edit #{edit_num + 1}: deleted lines {start_idx + 1}-{end_idx + 1}")
                elif old_range_len == new_range_len:
                    summary_parts.append(f"edit #{edit_num + 1}: replaced {old_range_len} lines at {start_idx + 1}-{end_idx + 1}")
                else:
                    summary_parts.append(f"edit #{edit_num + 1}: replaced {old_range_len} lines with {new_range_len} lines at {start_idx + 1}-{end_idx + 1}")

            new_content = ''.join(new_lines)

            # Preserve trailing newline behavior
            if original_text.endswith('\n') and not new_content.endswith('\n'):
                new_content += '\n'

            if new_content == original_text:
                return f"Edits processed but resulted in no change for '{target_file}'."

            # Atomic write via temp file (same-dir to avoid EXDEV)
            with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', newline='',
                                            dir=str(file_path.parent)) as tmp:
                tmp.write(new_content)
                temp_path = tmp.name
            os.replace(temp_path, file_path)

            _notify_file_write(target_file)

            new_total = len(new_content.splitlines())
            line_delta = new_total - total_lines

            summary = "; ".join(summary_parts)
            result = (f"✅ Applied {len(validated_edits)} edit(s) to '{target_file}': "
                      f"{summary}")
            result += (f"\n📏 File: {total_lines} → {new_total} lines "
                       f"({'+' if line_delta >= 0 else ''}{line_delta})")
            if line_delta != 0:
                result += (f"\n⚠️  Line numbers have shifted by "
                           f"{'+' if line_delta >= 0 else ''}{line_delta}.")

            if any_correction_needed:
                correction = {
                    "target_file": target_file,
                    "edits": corrected_edits_for_marker,
                }
                result = ("⚠️  Original parameters were auto-corrected. "
                          "No action needed from you.\n\n" + result)
                result += "\n\n<!--SELF_CORRECT:" + json.dumps(correction) + "-->"


            return result

        except Exception as e:
            return f"Error applying range replace edits to '{target_file}': {str(e)}"

    # --- Full File Write ---
    def full_file_write(self, target_file: str, code_edit: str) -> str:
        """
        Creates a new file or completely replaces an existing one.
        """
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
        """
        Delete a file or directory.
        """
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
        """
        List the contents of a directory.
        """
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
        """
        Search for files by name using fuzzy matching.
        """
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
        """Resolve a path relative to the workspace root."""
        path_obj = Path(path)
        if path_obj.is_absolute():
            return path_obj
        else:
            return self.workspace_root / path

    def _apply_edit(self, current_content: str, code_edit: str) -> str:
        """Apply code edit to current content (replace entire content)."""
        return code_edit

# --- Public Tool Wrappers ---

def read_file_tool(target_file: str) -> str:
    """Read file tool wrapper."""
    file_ops = FileOperations()
    return file_ops.read_file(target_file)

def range_replace_edit_tool(target_file: str, edits: List[Dict[str, Any]]) -> str:
    """Range-based multi-edit tool wrapper with ±3 line tolerance."""
    ops = FileOperations()
    return ops.range_replace_edit(target_file, edits)

def full_file_write_tool(target_file: str, code_edit: str) -> str:
    """Edit file tool wrapper."""
    ops = FileOperations()
    return ops.full_file_write(target_file, code_edit)

def delete_file_tool(target_file: str) -> str:
    """Delete file tool wrapper."""
    ops = FileOperations()
    return ops.delete_file(target_file)

def list_dir_tool(relative_workspace_path: str = "") -> str:
    """List directory tool wrapper."""
    ops = FileOperations()
    return ops.list_dir(relative_workspace_path)

def file_search_tool(query: str) -> str:
    """File search tool wrapper."""
    ops = FileOperations()
    return ops.file_search(query)

def close_file_tool(target_file: str) -> str:
    """
    Close a file from the code interpreter view.
    This removes the file from the consolidated interpreter display.
    The file itself is not modified or deleted.
    """
    return f"Closed '{target_file}' from code interpreter view."
