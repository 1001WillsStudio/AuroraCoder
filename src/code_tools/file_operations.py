# --- Imports ---
import os
import shutil
import tempfile
import json
import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from ..code_sandbox import WORKSPACE
from . import anchor_matcher as am

logger = logging.getLogger(__name__)

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


# --- Edit-file pre/post-processing ---

_EDIT_SELF_CORRECT_RE = re.compile(r'\n?\n?<!--SELF_CORRECT:(.*?)-->', re.DOTALL)
MAX_EDITS_PER_CALL = 3


def maybe_truncate_edits(tc: Dict) -> None:
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
    match = _EDIT_SELF_CORRECT_RE.search(result)
    if not match:
        return result
    try:
        correction = json.loads(match.group(1))
    except json.JSONDecodeError:
        return _EDIT_SELF_CORRECT_RE.sub('', result)
    tc["function"]["arguments"] = json.dumps(correction, ensure_ascii=False)
    return _EDIT_SELF_CORRECT_RE.sub('', result)


# --- File Operations Class ---

class FileOperations:
    """File operations tool for reading, editing, and managing files."""

    def __init__(self, workspace_root: str = None):
        self.workspace_root = WORKSPACE

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

    # --- Range Replace Edit ---
    def range_replace_edit(self, target_file: str, edits: List[Dict[str, Any]]) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return f"Error: File '{target_file}' not found."

            original_text = file_path.read_text(encoding="utf-8", errors="ignore").replace('\r\n', '\n')
            _notify_file_read(target_file, original_text)
            original_lines = original_text.splitlines(keepends=True)
            total_lines = len(original_lines)

            validated_edits = []
            any_correction_needed = False
            corrected_edits_for_marker = []

            for i, edit in enumerate(edits):
                remove_line_number = edit.get("remove_line_number", "")
                try:
                    parts = str(remove_line_number).strip().split("-")
                    if len(parts) == 1:
                        start_line = int(parts[0])
                        end_line = start_line
                    elif len(parts) == 2:
                        start_line = int(parts[0])
                        end_line = int(parts[1])
                    else:
                        return (f"Error in edit #{i + 1}: remove_line_number must be "
                                f"like '13-15' or '42', got '{remove_line_number}'")
                except (ValueError, AttributeError):
                    return (f"Error in edit #{i + 1}: remove_line_number must be "
                            f"like '13-15' or '42', got '{remove_line_number}'")

                content_to_remove = str(edit.get("content_to_remove") or "")
                has_to_marker = '\n[TO]\n' in content_to_remove
                if has_to_marker:
                    parts = content_to_remove.split('\n[TO]\n', 1)
                    start_content = parts[0]
                    end_content = parts[1]
                else:
                    start_content = content_to_remove
                    end_content = ""
                replace_content = str(edit.get("replace_content", ""))

                if end_line is None:
                    end_line = start_line
                if not isinstance(end_line, int) or end_line < 1:
                    return (f"Error in edit #{i + 1}: end_line must be a positive integer, "
                            f"got {end_line}")
                if end_line > total_lines:
                    end_line = total_lines
                if start_line > end_line:
                    return (f"Error in edit #{i + 1}: start_line ({start_line}) must be "
                            f"<= end_line ({end_line})")
                if not end_content and has_to_marker:
                    end_content = am.normalise(original_lines[end_line - 1])

                if not isinstance(start_line, int) or start_line < 1:
                    return (f"Error in edit #{i + 1}: start_line must be a positive integer, "
                            f"got {start_line}")

                start_idx = start_line - 1
                end_idx = end_line - 1

                start_is_multiline = "\n" in start_content
                end_is_multiline = "\n" in end_content
                start_anchor_line_count = len([l for l in start_content.splitlines() if l.strip()]) if start_is_multiline else 1
                end_anchor_line_count = len([l for l in end_content.splitlines() if l.strip()]) if end_is_multiline else 1

                # Verify start anchor
                start_corrected = False
                NO = am._NO_CHANGES

                if start_is_multiline:
                    found_idx = am.find_anchor_tolerant(original_lines, total_lines,
                                                        start_line, start_content)
                    if found_idx is not None:
                        start_idx = found_idx
                        start_corrected = True
                    else:
                        ctx_s = max(0, start_idx - 1)
                        ctx_e = min(total_lines, start_idx + 4)
                        ctx = ''.join(original_lines[ctx_s:ctx_e])
                        return (f"Error in edit #{i + 1}: start_content does not match "
                                f"file at line {start_line} (searched ±{am.MAX_ANCHOR_SHIFT}).\n"
                                f"File context around line {start_line}:\n"
                                f"---\n{ctx}---"
                                f"{am.anchor_hint(original_lines, start_content, start_line, True)}"
                                f"{NO}")
                else:
                    actual_start = am.normalise(original_lines[start_idx])
                    expected_start = am.normalise(start_content)
                    if actual_start != expected_start:
                        found_idx = am.find_anchor_tolerant(original_lines, total_lines,
                                                            start_line, start_content)
                        if found_idx is not None:
                            start_idx = found_idx
                            start_corrected = True
                        else:
                            ctx_s = max(0, start_idx - 1)
                            ctx_e = min(total_lines, start_idx + 3)
                            ctx = ''.join(original_lines[ctx_s:ctx_e])
                            return (f"Error in edit #{i + 1}: start_content does not match "
                                    f"file at line {start_line} (searched ±{am.MAX_ANCHOR_SHIFT}).\n"
                                    f"Expected: {repr(start_content.rstrip())}\n"
                                    f"Actual:   {repr(original_lines[start_line - 1].rstrip())}\n"
                                    f"File context around line {start_line}:\n"
                                    f"---\n{ctx}---"
                                    f"{am.indentation_hint(expected_start, actual_start)}"
                                    f"{am.anchor_hint(original_lines, start_content, start_line, False)}"
                                    f"{NO}")

                # Verify end anchor
                end_corrected = False
                if not has_to_marker:
                    _blines = start_content.splitlines(keepends=True)
                    if len(_blines) > 1 and am.normalise(_blines[-1]) == '':
                        _blines = _blines[:-1]
                    if len(_blines) == 0:
                        # Empty content_to_remove → single empty-line removal
                        end_idx = start_idx
                    else:
                        end_idx = start_idx + len(_blines) - 1
                elif end_is_multiline:
                    end_search_start = end_line - end_anchor_line_count + 1
                    found_idx = am.find_anchor_tolerant(original_lines, total_lines,
                                                        end_search_start, end_content)
                    if found_idx is not None:
                        end_idx = found_idx + end_anchor_line_count - 1
                        end_corrected = True
                    else:
                        ctx_s = max(0, end_idx - 2)
                        ctx_e = min(total_lines, end_idx + 3)
                        ctx = ''.join(original_lines[ctx_s:ctx_e])
                        return (f"Error in edit #{i + 1}: end_content does not match "
                                f"file at line {end_line} (searched ±{am.MAX_ANCHOR_SHIFT}).\n"
                                f"File context around line {end_line}:\n"
                                f"---\n{ctx}---"
                                f"{NO}")
                else:
                    actual_end = am.normalise(original_lines[end_idx])
                    expected_end = am.normalise(end_content)
                    if actual_end != expected_end:
                        found_idx = am.find_anchor_tolerant(original_lines, total_lines,
                                                            end_line, end_content)
                        if found_idx is not None:
                            end_idx = found_idx
                            end_corrected = True
                        else:
                            ctx_s = max(0, end_idx - 1)
                            ctx_e = min(total_lines, end_idx + 3)
                            ctx = ''.join(original_lines[ctx_s:ctx_e])
                            return (f"Error in edit #{i + 1}: end_content does not match "
                                    f"file at line {end_line} (searched ±{am.MAX_ANCHOR_SHIFT}).\n"
                                    f"Expected: {repr(end_content.rstrip())}\n"
                                    f"Actual:   {repr(original_lines[end_line - 1].rstrip())}\n"
                                    f"File context around line {end_line}:\n"
                                    f"---\n{ctx}---"
                                    f"{am.indentation_hint(expected_end, actual_end)}"
                                    f"{NO}")

                if start_idx > end_idx:
                    return (f"Error in edit #{i + 1}: after tolerance search, start_line "
                            f"({start_idx + 1}) > end_line ({end_idx + 1}). "
                            f"Check your line numbers and anchor content."
                            f"{NO}")

                validated_edits.append((start_idx, end_idx, replace_content, i))

                if start_corrected or end_corrected or start_is_multiline or end_is_multiline:
                    any_correction_needed = True

                if has_to_marker:
                    start_portion = ''.join(original_lines[start_idx:start_idx + start_anchor_line_count]).rstrip('\n')
                    end_portion = ''.join(original_lines[end_idx - end_anchor_line_count + 1:end_idx + 1]).rstrip('\n')
                else:
                    start_portion = original_lines[start_idx].rstrip('\n')
                    end_portion = original_lines[end_idx].rstrip('\n')
                actual_block = start_portion + '\n[TO]\n' + end_portion
                corrected_edits_for_marker.append({
                    "remove_line_number": f"{start_idx + 1}-{end_idx + 1}",
                    "content_to_remove": actual_block,
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
                            f"{am._NO_CHANGES}")

            # Apply edits bottom-to-top
            validated_edits.sort(key=lambda e: e[0], reverse=True)
            new_lines = list(original_lines)
            summary_parts = []

            for start_idx, end_idx, replace_content, edit_num in validated_edits:
                old_range_len = end_idx - start_idx + 1
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
            if original_text.endswith('\n') and not new_content.endswith('\n'):
                new_content += '\n'
            if new_content == original_text:
                return f"Edits processed but resulted in no change for '{target_file}'."

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

    def file_search(self, query: str, max_results: int = 12) -> str:
        """Find files by name pattern — delegates to `find` subprocess (fast)."""
        import subprocess
        try:
            # Prune heavy/noisy dirs — find never descends into them
            cmd = [
                "find", str(self.workspace_root),
                "(", "-name", ".git", "-o",
                       "-name", "node_modules", "-o",
                       "-name", "__pycache__", "-o",
                       "-name", ".venv", "-o",
                       "-name", "venv", "-o",
                       "-name", "dist",
                ")", "-prune", "-o",
                "-type", "f", "-iname", f"*{query}*", "-print",
            ]
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=str(self.workspace_root))
        except subprocess.TimeoutExpired:
            return f"Error: file search timed out looking for '{query}'"
        except FileNotFoundError:
            return "Error: find binary not found on this system."

        lines = [l.strip() for l in cp.stdout.strip().split("\n") if l.strip()]
        if not lines:
            return f"No files found matching '{query}'"

        # Build relative paths
        import os
        results = []
        ws = str(self.workspace_root)
        for abs_path in lines:
            rel = os.path.relpath(abs_path, ws)
            name = os.path.basename(abs_path)
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = 0
            results.append({"path": rel, "name": name, "size": size})

        results.sort(key=lambda x: (
            0 if query.lower() == x["name"].lower() else 1,
            x["name"].lower()
        ))

        output = [f"Found {len(results)} files matching '{query}':\n"]
        for r in results[:max_results]:
            output.append(f"📄 {r['path']} ({r['size']} bytes)")
        if len(results) > max_results:
            output.append(f"\n... and {len(results) - max_results} more files")
        return "\n".join(output)

    def _resolve_path(self, path: str) -> Path:
        path_obj = Path(path)
        return path_obj if path_obj.is_absolute() else self.workspace_root / path

    def _apply_edit(self, current_content: str, code_edit: str) -> str:
        return code_edit


# --- Public Tool Wrappers ---

def read_file_tool(target_file: str) -> str:
    return FileOperations().read_file(target_file)

def range_replace_edit_tool(target_file: str, edits: List[Dict[str, Any]]) -> str:
    return FileOperations().range_replace_edit(target_file, edits)

def full_file_write_tool(target_file: str, code_edit: str) -> str:
    return FileOperations().full_file_write(target_file, code_edit)

def delete_file_tool(target_file: str) -> str:
    return FileOperations().delete_file(target_file)

def list_dir_tool(relative_workspace_path: str = "") -> str:
    return FileOperations().list_dir(relative_workspace_path)

def file_search_tool(query: str) -> str:
    return FileOperations().file_search(query)

def close_file_tool(target_file: str) -> str:
    return f"Closed '{target_file}' from code interpreter view."
