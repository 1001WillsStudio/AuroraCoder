# --- Imports ---
import os
import subprocess
import shutil
import tempfile
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from ..code_sandbox import WORKSPACE
from .edit_file import RangeReplaceEditor, maybe_truncate_edits

logger = logging.getLogger(__name__)

# --- Conversation tracking ---
# conversation_id is now threaded as a parameter through main_flow →
# tool_executor → execute_tool_call → subagent.  No module-level
# global is needed.


# --- Edit-file pre/post-processing ---


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
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            lines = content.count('\n') + 1
            size = file_path.stat().st_size
            return f"The file '{target_file}' ({lines} lines, {size} bytes) is opened in the code interpreter."
        except Exception as e:
            return f"Error reading file '{target_file}': {str(e)}"


    # -------------------------------------------------------------------
    #  Full File Write
    # -------------------------------------------------------------------

    def full_file_write(self, target_file: str, code_edit: str) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code_edit)
                return f"Created new file: {target_file}"
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                current_content = f.read().replace('\r\n', '\n')
            new_content = self._apply_edit(current_content, code_edit)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return f"Successfully edited {target_file}"
        except Exception as e:
            return f"Error editing file '{target_file}': {str(e)}"

    def delete_file(self, target_file: str) -> str:
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                return f"File '{target_file}' does not exist"
            if file_path.is_file():
                file_path.unlink()
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

def read_file_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    target = arguments.get("file")
    focus = arguments.get("focus", False)

    if isinstance(target, list):
        targets = target
    else:
        targets = [target]

    fo = FileOperations()
    results = [fo.read_file(t) for t in targets]
    msg = "\n".join(results)

    if focus:
        msg += f"\n(Focus mode: closed all other files)"

    return msg, arguments



def execute_edit_file(arguments: Dict[str, Any]):
    """Execute an ``edit_file`` call and return ``(result, applied_arguments)``.

    ``applied_arguments`` is the full argument dict in the exact form that was
    applied to the file (line numbers resolved, ``[TO]`` normalised, indent
    fixed), or ``None`` if the edit did not complete (e.g. an error). The
    caller rebuilds the originating tool call from it. This is the structured
    replacement for the old ``<!--SELF_CORRECT:...-->`` result-text marker.
    """
    editor = RangeReplaceEditor(WORKSPACE)
    result, applied = editor.edit(
        arguments.get("file"),
        arguments.get("edits"),
    )
    return result, applied if applied is not None else arguments

def full_file_write_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    return FileOperations().full_file_write(arguments["file"], arguments["content"]), arguments

def delete_file_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        return FileOperations().delete_file(arguments["file"]), arguments

def list_dir_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    return FileOperations().list_dir(arguments.get("relative_workspace_path", "")), arguments

def file_search_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    return FileOperations().file_search(arguments["query"]), arguments

def close_file_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    keep = arguments.get("keep")
    if keep is not None:
        # "Close all except" mode — file is ignored
        keep_list = keep if isinstance(keep, list) else [keep]
        if keep_list:
            return f"Closed all files except: {', '.join(repr(k) for k in keep_list)}", arguments
        else:
            return "Closed all files from code interpreter view.", arguments

    target = arguments.get("file")
    if not target:
        return "Error: must provide 'file' or 'keep' parameter", arguments

    if isinstance(target, list):
        targets = target
    else:
        targets = [target]

    closed = ", ".join(f"'{t}'" for t in targets)
    if len(targets) == 1:
        return f"Closed {closed} from code interpreter view.", arguments
    else:
        return f"Closed {closed} from code interpreter view.", arguments




