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

# Import session manager
try:
    from ..code_sandbox import session_manager
except ImportError:
    session_manager = None

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
        # Use session directory if available, otherwise use provided workspace or current directory
        if session_manager and session_manager.session_dir:
            self.workspace_root = session_manager.get_session_working_directory()
        else:
            self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()

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

    # --- Search and Replace Edit (Aider-style) ---
    def search_replace_edit(self, target_file: str, start_line: int, search_content: str, replace_content: str) -> str:
        """
        Apply an Aider-style search and replace edit to a file.
        
        Args:
            target_file: Path to the file to edit
            start_line: Starting line number to begin searching for the content (1-based)
            search_content: The exact content to find and replace (can be multi-line)
            replace_content: The replacement content (can be multi-line, empty string for deletion)
            
        Returns:
            Success message or error description
        """
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists() or not file_path.is_file():
                return f"Error: File '{target_file}' not found."
            
            original_text = file_path.read_text(encoding="utf-8", errors="ignore")
            
            # Snapshot original content for diff tracking
            _notify_file_read(target_file, original_text)
            original_lines = original_text.splitlines(keepends=True)
            
            # Normalize search content - handle both with and without trailing newlines
            search_lines = search_content.splitlines(keepends=True)
            # Ensure last line has proper ending for comparison
            if search_lines and not search_lines[-1].endswith('\n'):
                search_lines[-1] = search_lines[-1]
            
            # Validate start_line
            if start_line < 1:
                return f"Error: start_line must be >= 1, got {start_line}"
            if start_line > len(original_lines):
                return f"Error: start_line {start_line} exceeds file length ({len(original_lines)} lines)"
            
            # Search for the content starting from start_line
            start_idx = start_line - 1  # Convert to 0-based index
            search_len = len(search_lines)
            
            # Helper function to normalize lines for comparison
            # Strips trailing whitespace from each line (trailing spaces usually have no meaning in Python)
            def normalize_for_comparison(lines):
                return '\n'.join(line.rstrip() for line in ''.join(lines).rstrip('\n').split('\n'))
            
            # Build the normalized search text
            search_text_normalized = normalize_for_comparison(search_lines)
            
            found_idx = None
            
            # Search from start_line onwards
            for i in range(start_idx, len(original_lines) - search_len + 1):
                # Extract candidate block
                candidate_lines = original_lines[i:i + search_len]
                candidate_text = normalize_for_comparison(candidate_lines)
                
                if candidate_text == search_text_normalized:
                    found_idx = i
                    break
            
            if found_idx is None:
                # Try a more flexible search - maybe whitespace differences
                # Show what we found near start_line for debugging
                context_start = max(0, start_idx)
                context_end = min(len(original_lines), start_idx + search_len + 3)
                context = ''.join(original_lines[context_start:context_end])
                
                return (f"Error: Could not find the search content starting from line {start_line}.\n"
                        f"Search content ({len(search_lines)} lines):\n"
                        f"---\n{search_content}\n---\n"
                        f"File content near line {start_line}:\n"
                        f"---\n{context}---\n\n"
                        f"Hint: If partial edit keeps failing, use write_file to rewrite the entire file instead.")
            
            # Prepare replacement
            replace_lines = replace_content.splitlines(keepends=True)
            # Ensure last line has newline if original section ended with one
            if replace_lines:
                if original_lines[found_idx + search_len - 1].endswith('\n') and not replace_lines[-1].endswith('\n'):
                    replace_lines[-1] = replace_lines[-1] + '\n'
            elif original_lines[found_idx + search_len - 1].endswith('\n'):
                # Deletion case - no replacement lines, but we're removing content that had newline
                pass
            
            # Apply the replacement
            new_lines = original_lines[:found_idx] + replace_lines + original_lines[found_idx + search_len:]
            new_content = ''.join(new_lines)
            
            # Preserve trailing newline behavior
            if original_text.endswith('\n') and not new_content.endswith('\n'):
                new_content += '\n'
            
            if new_content == original_text:
                return f"Edit processed but resulted in no change for '{target_file}'."
            
            # Write back using temp file for atomic operation.
            # Use dir=file_path.parent so the temp file is on the same
            # filesystem — avoids EXDEV (cross-device link) when workspace
            # and /tmp are on different mounts (e.g. Docker volumes).
            with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', newline='',
                                            dir=str(file_path.parent)) as tmp:
                tmp.write(new_content)
                temp_path = tmp.name
            os.replace(temp_path, file_path)
            
            # Notify that file was written for diff tracking
            _notify_file_write(target_file)
            
            # Generate result message
            found_line = found_idx + 1  # Convert back to 1-based
            if not replace_content:
                return f"✅ Successfully deleted {search_len} lines starting at line {found_line} in '{target_file}'"
            elif not search_content:
                return f"✅ Successfully inserted {len(replace_lines)} lines at line {found_line} in '{target_file}'"
            else:
                return f"✅ Successfully replaced {search_len} lines with {len(replace_lines)} lines starting at line {found_line} in '{target_file}'"
            
        except Exception as e:
            return f"Error applying search/replace edit: {str(e)}"

    # --- Full File Write ---
    def full_file_write(self, target_file: str, code_edit: str) -> str:
        """
        Creates a new file or completely replaces an existing one.
        """
        try:
            file_path = self._resolve_path(target_file)
            if not file_path.exists():
                # Snapshot empty content for new files
                _notify_file_read(target_file, "")
                
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code_edit)
                
                # Notify file was written
                _notify_file_write(target_file)
                return f"Created new file: {target_file}"
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                current_content = f.read()
            
            # Snapshot original content for diff tracking
            _notify_file_read(target_file, current_content)
            
            new_content = self._apply_edit(current_content, code_edit)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Notify file was written
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
                # Snapshot content before deletion for diff tracking
                try:
                    content = file_path.read_text(encoding='utf-8', errors='replace')
                    _notify_file_read(target_file, content)
                except Exception:
                    pass
                
                file_path.unlink()
                
                # Notify file was "written" (deleted) for diff tracking
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
# def anchor_edit_file_tool(target_file: str, code_edit: str) -> str:
#     """Public wrapper to expose anchor_edit_file via tool system."""
#     ops = FileOperations()
#     return ops.anchor_edit_file(target_file, code_edit)

def read_file_tool(target_file: str) -> str:
    """Read file tool wrapper."""
    file_ops = FileOperations()
    return file_ops.read_file(target_file)

def search_replace_edit_tool(target_file: str, start_line: int, search_content: str, replace_content: str) -> str:
    """Aider-style search and replace edit tool wrapper."""
    ops = FileOperations()
    return ops.search_replace_edit(target_file, start_line, search_content, replace_content)

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