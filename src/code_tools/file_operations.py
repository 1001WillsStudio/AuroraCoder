# --- Imports ---
import os
import shutil
import subprocess
import tempfile
import difflib
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from ..config import EDIT_ZONE_MARKER
from ..code_sandbox import WORKSPACE

# --- Constants ---
WILDCARD_SENTINEL = EDIT_ZONE_MARKER  # Backward compatibility with old constant name

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

    # --- Range Replace Edit (multi-edit, anchor-based) ---
    def range_replace_edit(self, target_file: str, edits: List[Dict[str, Any]]) -> str:
        """
        Apply one or more range-based edits to a file.

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

            # --- Validate every edit before applying any ---
            validated_edits = []  # (start_idx, end_idx, replace_content, edit_idx)
            for i, edit in enumerate(edits):
                start_line = edit.get("start_line")
                start_content = str(edit.get("start_line_content") or edit.get("start_content") or "")
                end_line = edit.get("end_line")
                end_content = str(edit.get("end_line_content") or edit.get("end_content") or "")
                replace_content = str(edit.get("replace_content", ""))

                # --- Multiline guard: reject start_line_content / end_line_content ---
                # --- that contain newlines.  These fields must be exactly one   ---
                # --- line to serve as anchors for line-range verification.     ---
                if "\n" in start_content:
                    return (f"Error in edit #{i + 1}: start_line_content must be a single line "
                            f"(no newlines).  You passed multi-line content.  Please provide "
                            f"only the SINGLE LINE of text at start_line as it appears in the file.")
                if "\n" in end_content:
                    return (f"Error in edit #{i + 1}: end_line_content must be a single line "
                            f"(no newlines).  You passed multi-line content.  Please provide "
                            f"only the SINGLE LINE of text at end_line as it appears in the file.")

                # --- Defaults: end_line → start_line, end_content → file ---
                if end_line is None:
                    end_line = start_line
                if not isinstance(end_line, int) or end_line < 1:
                    return (f"Error in edit #{i + 1}: end_line must be a positive integer, "
                            f"got {end_line}")
                if end_line > total_lines:
                    return (f"Error in edit #{i + 1}: end_line {end_line} exceeds "
                            f"file length ({total_lines} lines)")
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

                # Verify start anchor
                actual_start = _normalise_line(original_lines[start_idx])
                expected_start = _normalise_line(start_content)
                if actual_start != expected_start:
                    context_start = max(0, start_idx - 1)
                    context_end = min(total_lines, start_idx + 3)
                    context = ''.join(original_lines[context_start:context_end])
                    return (f"Error in edit #{i + 1}: start_content does not match "
                            f"file at line {start_line}.\n"
                            f"Expected: {repr(start_content.rstrip())}\n"
                            f"Actual:   {repr(original_lines[start_idx].rstrip())}\n"
                            f"File context around line {start_line}:\n"
                            f"---\n{context}---")

                # Verify end anchor
                actual_end = _normalise_line(original_lines[end_idx])
                expected_end = _normalise_line(end_content)
                if actual_end != expected_end:
                    context_start = max(0, end_idx - 1)
                    context_end = min(total_lines, end_idx + 3)
                    context = ''.join(original_lines[context_start:context_end])
                    return (f"Error in edit #{i + 1}: end_content does not match "
                            f"file at line {end_line}.\n"
                            f"Expected: {repr(end_content.rstrip())}\n"
                            f"Actual:   {repr(original_lines[end_idx].rstrip())}\n"
                            f"File context around line {end_line}:\n"
                            f"---\n{context}---")

                validated_edits.append((start_idx, end_idx, replace_content, i))

            # --- Detect overlapping edit ranges ---
            if len(validated_edits) > 1:
                sorted_by_pos = sorted(validated_edits, key=lambda e: e[0])
                for idx in range(len(sorted_by_pos) - 1):
                    a_start, a_end, _, a_num = sorted_by_pos[idx]
                    b_start, b_end, _, b_num = sorted_by_pos[idx + 1]
                    if a_end >= b_start:
                        return (f"Error: overlapping edit ranges detected — "
                                f"edit #{a_num + 1} (lines {a_start + 1}-{a_end + 1}) "
                                f"overlaps edit #{b_num + 1} (lines {b_start + 1}-{b_end + 1}). "
                                f"Split overlapping edits into separate edit_file calls.")

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
                result += (f"\n⚠️  Line numbers after the edited region(s) have shifted by "
                           f"{'+' if line_delta >= 0 else ''}{line_delta}. "
                           f"If you need to make further edits, use the NEW line numbers.")
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
    """Range-based multi-edit tool wrapper."""
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
