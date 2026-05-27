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
from . import edit_anchors as am

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


# --- Internal anchor-error exception ---

class _AnchorError(Exception):
    """Raised internally when an anchor cannot be resolved."""


# --- File Operations Class ---

class FileOperations:
    """File operations tool for reading, editing, and managing files."""

    def __init__(self, workspace_root: str = None):
        self.workspace_root = WORKSPACE

    # -------------------------------------------------------------------
    #  Read
    # -------------------------------------------------------------------

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

    # -------------------------------------------------------------------
    #  Range Replace Edit  (edit_file)  —  public entry point
    # -------------------------------------------------------------------

    def range_replace_edit(self, target_file: str, edits: List[Dict[str, Any]]) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return f"Error: File '{target_file}' not found."

            original_text = file_path.read_text(encoding="utf-8", errors="ignore").replace('\r\n', '\n')
            _notify_file_read(target_file, original_text)
            original_lines = original_text.splitlines(keepends=True)
            total_lines = len(original_lines)

            # ---- Phase 1: validate & resolve each edit ----
            validated = []          # (start_idx, end_idx, replace_content, edit_num)
            markers = []            # per-edit self-correction marker dicts
            any_corrected = False
            any_indent_fixed = False

            for i, edit in enumerate(edits):
                outcome = self._validate_one_edit(i, edit, original_lines, total_lines)
                if isinstance(outcome, str):          # error message
                    return outcome
                (s, e, repl, corrected, indent_fixed, marker) = outcome
                validated.append((s, e, repl, i))
                markers.append(marker)
                any_corrected |= corrected
                any_indent_fixed |= indent_fixed

            # ---- Phase 2: guard against overlapping edits ----
            sorted_edits = sorted(validated, key=lambda e: e[0])
            for a_i in range(len(sorted_edits) - 1):
                a_s, a_e, _, a_n = sorted_edits[a_i]
                b_s, b_e, _, b_n = sorted_edits[a_i + 1]
                if a_e >= b_s:
                    return (f"Error: edit #{a_n + 1} (lines {a_s + 1}-{a_e + 1}) "
                            f"overlaps edit #{b_n + 1} (lines {b_s + 1}-{b_e + 1}). "
                            f"Split overlapping edits into separate edit_file calls."
                            f"{am._NO_CHANGES}")

            # ---- Phase 3: apply edits bottom-to-top ----
            validated.sort(key=lambda e: e[0], reverse=True)
            new_lines = list(original_lines)
            summaries = []

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

            # ---- Phase 4: write result ----
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

            return self._build_result(target_file, validated, markers, total_lines,
                                      new_content, any_indent_fixed, any_corrected,
                                      summaries)

        except Exception as e:
            return f"Error applying range replace edits to '{target_file}': {str(e)}"

    # -------------------------------------------------------------------
    #  Edit-validation helpers  (static — pure logic, no side effects)
    # -------------------------------------------------------------------

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
        delta = am.indent_delta(expected, actual_line)
        if delta == 0:
            return replace_content, False
        return am.adjust_indent(replace_content, delta), True

    @staticmethod
    def _resolve_start_anchor(original_lines, total_lines, start_line,
                              start_content, start_idx):
        """Tolerant (+fallback) start-anchor search.
        Returns (pos, indent_mismatch) or raises _AnchorError."""
        found = am.find_anchor_tolerant(original_lines, total_lines, start_line, start_content)
        if found is not None:
            return found
        found = am.find_anchor_anywhere(original_lines, total_lines, start_content)
        if found is not None:
            return found
        ctx_s = max(0, start_idx - 1)
        ctx_e = min(total_lines, start_idx + 4)
        ctx = ''.join(original_lines[ctx_s:ctx_e])
        raise _AnchorError(
            f"start_content does not match file at line {start_line} "
            f"(searched \u00b1{am.MAX_ANCHOR_SHIFT} and entire file).\n"
            f"File context around line {start_line}:\n"
            f"---\n{ctx}---"
            f"{am.anchor_hint(original_lines, start_content, start_line, '\n' in start_content)}"
            f"{am._NO_CHANGES}"
        )

    @staticmethod
    def _resolve_singleline_start(original_lines, total_lines, start_line,
                                  start_content, start_idx, edit_num):
        """Verify a single-line start anchor; returns (pos, indent_mismatch)
        or raises _AnchorError."""
        actual = am.normalise(original_lines[start_idx])
        expected = am.normalise(start_content)
        if actual == expected:
            return (start_idx, False)
        found = am.find_anchor_tolerant(original_lines, total_lines, start_line, start_content)
        if found is not None:
            return found
        ctx_s = max(0, start_idx - 1)
        ctx_e = min(total_lines, start_idx + 3)
        ctx = ''.join(original_lines[ctx_s:ctx_e])
        raise _AnchorError(
            f"start_content does not match file at line {start_line} "
            f"(searched \u00b1{am.MAX_ANCHOR_SHIFT}).\n"
            f"Expected: {repr(start_content.rstrip())}\n"
            f"Actual:   {repr(original_lines[start_line - 1].rstrip())}\n"
            f"File context around line {start_line}:\n"
            f"---\n{ctx}---"
            f"{am.indentation_hint(expected, actual)}"
            f"{am.anchor_hint(original_lines, start_content, start_line, False)}"
            f"{am._NO_CHANGES}"
        )

    @staticmethod
    def _resolve_end_anchor(original_lines, total_lines, end_content,
                            end_line, end_idx, ea_count, edit_num):
        """Verify a multi-line end anchor; returns new end_idx or raises _AnchorError."""
        search_start = end_line - ea_count + 1
        found = am.find_anchor_tolerant(original_lines, total_lines, search_start, end_content)
        if found is None:
            ctx_s = max(0, end_idx - 2)
            ctx_e = min(total_lines, end_idx + 3)
            ctx = ''.join(original_lines[ctx_s:ctx_e])
            raise _AnchorError(
                f"end_content does not match file at line {end_line} "
                f"(searched \u00b1{am.MAX_ANCHOR_SHIFT}).\n"
                f"File context around line {end_line}:\n---\n{ctx}---{am._NO_CHANGES}"
            )
        return found[0] + ea_count - 1

    @staticmethod
    def _resolve_singleline_end(original_lines, total_lines, end_content,
                                end_line, end_idx, edit_num):
        """Verify a single-line end anchor; returns (pos, _) or raises _AnchorError."""
        actual = am.normalise(original_lines[end_idx])
        expected = am.normalise(end_content)
        if actual == expected:
            return (end_idx, False)
        found = am.find_anchor_tolerant(original_lines, total_lines, end_line, end_content)
        if found is None:
            ctx_s = max(0, end_idx - 1)
            ctx_e = min(total_lines, end_idx + 3)
            ctx = ''.join(original_lines[ctx_s:ctx_e])
            raise _AnchorError(
                f"end_content does not match file at line {end_line} "
                f"(searched \u00b1{am.MAX_ANCHOR_SHIFT}).\n"
                f"Expected: {repr(end_content.rstrip())}\n"
                f"Actual:   {repr(original_lines[end_line - 1].rstrip())}\n"
                f"File context around line {end_line}:\n---\n{ctx}---"
                f"{am.indentation_hint(expected, actual)}{am._NO_CHANGES}"
            )
        return (found[0], False)

    @staticmethod
    def _compute_end_idx_no_to(start_content, start_idx):
        """Compute end_idx from *start_content* when [TO] marker is absent."""
        blines = start_content.splitlines(keepends=True)
        if len(blines) > 1 and am.normalise(blines[-1]) == '':
            blines = blines[:-1]
        if len(blines) == 0:
            return start_idx          # empty → single empty-line removal
        return start_idx + len(blines) - 1

    def _validate_one_edit(self, n: int, edit: dict,
                           original_lines, total_lines):
        """Fully validate & resolve a single edit: parse params, resolve
        start + end anchors (with tolerance + indent auto-fix), build marker.

        Returns (start_idx, end_idx, replace_content, corrected, indent_fixed, marker)
        or an error string.
        """
        NO = am._NO_CHANGES

        # ---- 1. Parse line range ----
        outcome = self._parse_line_range(edit.get("remove_line_number", ""), n, total_lines)
        if isinstance(outcome, str):
            return outcome
        start_line, end_line = outcome

        # ---- 2. Parse content_to_remove ----
        raw_ctr = str(edit.get("content_to_remove") or "")
        has_to = '\n[TO]\n' in raw_ctr
        if has_to:
            start_content, end_content = raw_ctr.split('\n[TO]\n', 1)
        else:
            start_content, end_content = raw_ctr, ""
        replace_content = str(edit.get("replace_content", ""))

        if not end_content and has_to:
            end_content = am.normalise(original_lines[end_line - 1])

        start_idx = start_line - 1
        end_idx = end_line - 1

        start_is_multi = "\n" in start_content
        end_is_multi = "\n" in end_content
        sa_count = len([l for l in start_content.splitlines() if l.strip()]) if start_is_multi else 1
        ea_count = len([l for l in end_content.splitlines() if l.strip()]) if end_is_multi else 1

        corrected = False
        indent_fixed = False

        # ---- 3. Resolve start anchor ----
        original_start_idx = start_idx
        if start_is_multi:
            try:
                start_idx, im = self._resolve_start_anchor(
                    original_lines, total_lines, start_line, start_content, start_idx)
            except _AnchorError as e:
                return f"Error in edit #{n + 1}: {e.args[0]}"
            corrected = True
            if im:
                fl = start_content.splitlines()[0]
                al = am.normalise(original_lines[start_idx])
                replace_content, indent_fixed = self._maybe_fix_indent(replace_content, fl, al)
        else:
            try:
                start_idx, im = self._resolve_singleline_start(
                    original_lines, total_lines, start_line, start_content, start_idx, n)
            except _AnchorError as e:
                return f"Error in edit #{n + 1}: {e.args[0]}"
            if start_idx != original_start_idx:
                corrected = True
            if im:
                actual = am.normalise(original_lines[start_idx])
                expected = am.normalise(start_content)
                replace_content, indent_fixed = self._maybe_fix_indent(replace_content, expected, actual)

        # ---- 4. Resolve end anchor ----
        try:
            if not has_to:
                end_idx = self._compute_end_idx_no_to(start_content, start_idx)
            elif end_is_multi:
                end_idx = self._resolve_end_anchor(
                    original_lines, total_lines, end_content, end_line, end_idx, ea_count, n)
                corrected = True
            else:
                end_idx, _ = self._resolve_singleline_end(
                    original_lines, total_lines, end_content, end_line, end_idx, n)
                if end_idx != (end_line - 1):
                    corrected = True
        except _AnchorError as e:
            return f"Error in edit #{n + 1}: {e.args[0]}"

        if start_idx > end_idx:
            return (f"Error in edit #{n + 1}: after tolerance search, start_line "
                    f"({start_idx + 1}) > end_line ({end_idx + 1}). Check your line numbers."
                    f"{NO}")

        corrected = corrected or start_is_multi or end_is_multi

        # ---- 5. Build self-correction marker ----
        if has_to:
            sp = ''.join(original_lines[start_idx:start_idx + sa_count]).rstrip('\n')
            ep = ''.join(original_lines[end_idx - ea_count + 1:end_idx + 1]).rstrip('\n')
        else:
            sp = original_lines[start_idx].rstrip('\n')
            ep = original_lines[end_idx].rstrip('\n')
        marker = {
            "remove_line_number": f"{start_idx + 1}-{end_idx + 1}",
            "content_to_remove": sp + '\n[TO]\n' + ep,
            "replace_content": replace_content,
        }

        return (start_idx, end_idx, replace_content, corrected, indent_fixed, marker)

    @staticmethod
    def _build_result(target_file, validated, markers, total_lines, new_content,
                      any_indent_fixed, any_corrected, summaries):
        """Format the final human-readable result message."""
        new_total = len(new_content.splitlines())
        delta = new_total - total_lines

        result = (f"\u2705 Applied {len(validated)} edit(s) to '{target_file}': "
                  f"{'; '.join(summaries)}")
        result += (f"\n\U0001f4cf File: {total_lines} \u2192 {new_total} lines "
                   f"({'+' if delta >= 0 else ''}{delta})")
        if delta != 0:
            result += (f"\n\u26a0\ufe0f  Line numbers have shifted by "
                       f"{'+' if delta >= 0 else ''}{delta}.")

        if any_indent_fixed:
            result = ("\u26a0\ufe0f  Indentation mismatch detected in content_to_remove \u2014 "
                      "replace_content indentation was auto-adjusted to match the file.\n\n"
                      + result)

        if any_corrected:
            correction = {"target_file": target_file, "edits": markers}
            result = ("\u26a0\ufe0f  Original parameters were auto-corrected. "
                      "No action needed from you.\n\n" + result)
            result += "\n\n<!--SELF_CORRECT:" + json.dumps(correction) + "-->"

        return result

    # -------------------------------------------------------------------
    #  Full File Write
    # -------------------------------------------------------------------

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
                    items.append(f"\U0001f4c1 {item.name}/")
                else:
                    size = item.stat().st_size
                    items.append(f"\U0001f4c4 {item.name} ({size} bytes)")
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
            output.append(f"\U0001f4c4 {r['path']} ({r['size']} bytes)")
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
