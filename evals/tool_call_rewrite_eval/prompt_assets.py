"""Faithful copies of AuroraCoder production prompt assets used by the eval harness.

Nothing here is imported from AuroraCoder (to keep the harness dependency-light and
to avoid triggering heavy package-chain imports). It is a verbatim reproduction of:

  * the production `SYSTEM_MESSAGE_TEMPLATE` (from src/main_flow.py + src/config.py),
    rendered with our fixture values, and
  * the production native-tool JSON schemas (from src/tool_definitions.py) for the
  focused subset of tools we expose to the model: read_file, write_file, edit_file,
  delete_file, close_file, list_directory, run_terminal_command.

Exposing a *focused* (but byte-identical-schema) tool set keeps the probe on
edit_file and removes the model's ability to detour into network/search tools,
without altering the edit_file schema the model must learn to format correctly.
"""
import datetime

# ---------------------------------------------------------------------------
# Tool schemas (verbatim from AuroraCoder/src/tool_definitions.py)
# ---------------------------------------------------------------------------
_EDIT_FILE_DESCRIPTION = (
    "Range-based file editing.\n\n"
    "Each edit replaces the line range given by `remove_line_number` (e.g. \"13-15\", \"24-36\") "
    "with `replace_content`. For a single line, just use the line number (e.g. \"42\", shorthand for \"42-42\").\n\n"
    "`content_to_remove` uses anchor matching: \"first_line\\n[TO]\\nlast_line\" — only the first and last "
    "lines act as anchors to find the block; intermediate lines are ignored. Always use \"[TO]\" for "
    "multi-line ranges (omit only for single-line edits where start == end).\n"
    "Edits are atomic: if ANY edit in the call fails validation, NONE are applied and the file is unchanged.\n\n"
    "RULES:\n"
    "- ALWAYS get line numbers and content from the code interpreter display, never from memory.\n"
    "- `content_to_remove` boundary lines must match the file exactly (the engine searches within +/-3 lines "
    "of the stated range and falls back to a whole-file search if the range hint is wrong).\n"
    "- Use empty `replace_content` (empty string) to delete lines.\n"
    "- `remove_line_number` ranges must not overlap across edits in the same call.\n"
    "- Do NOT edit the same file more than once per turn.\n"
    "- Always re-read the refreshed code interpreter display for correct line numbers after a prior edit."
)

NATIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Reads a file and displays its content in the code interpreter. Checks for file existence "
                "and confirms it can be opened."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": ["string", "array"],
                        "description": "Path to the file(s) to read (relative to workspace). Pass a single string or an array of strings to open multiple files at once.",
                        "items": {"type": "string"},
                    }
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Creates a new file or completely replaces the content of an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to the file to write (relative to workspace)"},
                    "content": {"type": "string", "description": "The complete content to write to the file"},
                },
                "required": ["file", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": _EDIT_FILE_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to the file to edit (relative to workspace)"},
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply. Each edit has remove_line_number, content_to_remove, replace_content.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "remove_line_number": {"type": "string", "description": 'Line range to remove, e.g. "13-15" (lines 13-15 inclusive) or "42" (line 42 only).'},
                                "content_to_remove": {"type": "string", "description": 'Anchor-based block identifier. For multi-line: "first_line\\n[TO]\\nlast_line" — only the boundary lines are matched. For single-line edits (start == end): just the line content itself with no [TO].'},
                                "replace_content": {"type": "string", "description": "New content that replaces everything in the specified remove_line_number (inclusive). Use empty string to delete."},
                            },
                            "required": ["remove_line_number", "content_to_remove", "replace_content"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["file", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Deletes a specified file from the filesystem. Accepts a single file path or a list of file paths to delete multiple files at once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": ["string", "array"],
                        "description": "Path to the file(s) to delete (relative to /workspace).",
                        "items": {"type": "string"},
                    }
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_file",
            "description": "Removes files from the code interpreter display. The files themselves are not deleted or modified. Once closed, you will no longer see their contents in the conversation until you reopen them with read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": ["string", "array"], "description": "Path to the file(s) to close (relative to /workspace).", "items": {"type": "string"}},
                    "keep": {"type": "array", "description": "Close ALL open files EXCEPT those listed here.", "items": {"type": "string"}},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Lists the contents of a directory, showing files and subdirectories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_workspace_path": {
                        "type": "string",
                        "description": "Path to directory to list (relative to /workspace). Empty string for workspace root.",
                        "default": "",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "Executes terminal/shell commands in a persistent, stateful Bash shell.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."},
                    "blocking": {"type": "boolean", "default": True},
                    "new_terminal": {"type": "boolean", "default": False},
                    "timeout": {"type": "integer", "description": "Timeout in seconds."},
                },
                "required": ["command"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Code-interpreter panel renderer (mirrors src/code_tools/panel_manager.py)
# ---------------------------------------------------------------------------
_PANEL_NOTE = (
    "Note: This display shows the LATEST state of each file with accurate line "
    "numbers. Always use these line numbers for edit_file calls — never use "
    "memorised line numbers. Closing a file removes it from this display, "
    "including previous tool responses — you will no longer see its contents "
    "unless you open it again. Only close a file after you have fully extracted "
    "all information you need from it."
)


def render_panel(relpath, file_lines):
    out = ["<====CODE_INTERPRETER_START====>", f"--- {relpath} ---"]
    for i, line in enumerate(file_lines):
        out.append(f"{i + 1:>5}\t{line.rstrip()}")
    out.append(_PANEL_NOTE)
    out.append("<====CODE_INTERPRETER_END====>")
    return "\n".join(out)


def read_file_notice(relpath, file_lines, nbytes):
    return (
        f"The file '{relpath}' ({len(file_lines)} lines, {nbytes} bytes) is opened "
        f"in the code interpreter."
    )


# ---------------------------------------------------------------------------
# System message (faithful reproduction of the production template)
# ---------------------------------------------------------------------------
def build_system_message(workspace_tree, current_time=None):
    if current_time is None:
        current_time = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    display_guide = (
        "**GUI Display via noVNC**:\n"
        "A virtual desktop (Xvfb + fluxbox) is running. GUI applications render on DISPLAY=:99 automatically.\n"
        "The user can view the live desktop through the noVNC viewer (port 6080).\n"
        "- **matplotlib**: use `matplotlib.use(\"TkAgg\")` BEFORE importing pyplot, then `plt.show()`.\n"
        "- **Any GUI app** (pygame, tkinter, browser, etc.): just run it — the window appears on the noVNC desktop.\n"
        "- To launch a GUI app in the background, use `blocking=false` in `run_terminal_command`.\n"
        "- To serve web content for the user to view, start an HTTP server on ports 8900–8902."
    )
    terminal_env_note = (
        "**Terminal**: For long-running processes, set blocking=false instead of using nohup or &. "
        "The command runs in the background and a log file path is returned."
    )
    return f"""You are a helpful and autonomous agent with powerful tools. You are running inside a Docker container (Linux). Your primary goal is to thoroughly address the user's query by leveraging your tools to gather comprehensive information and execute necessary actions.

**Workspace**: Your working directory is `/workspace`. All file operations use paths **relative to /workspace** unless an absolute path is given. The terminal shell also starts in `/workspace`. When the user uploads a project, its files appear here.

**Workspace File Tree**:
{workspace_tree}

**SUPER IMPORTANT**: Do exactly what the user asks — no more, no less. Work autonomously toward the goal without stopping to ask the user. Use your tools to investigate and resolve issues yourself. Only ask the user when you truly cannot proceed without their input.

{display_guide}

{terminal_env_note}

**Guidelines**:
- Use English exclusively in all generated code and comments.
- **edit_file**: at most 3 edits per call per file. Split larger changes across multiple tool rounds.

Current Time: {current_time}"""


def build_workspace_tree(files_relpaths, root_label="/workspace"):
    """Compact tree of only the fixture files the model needs to see."""
    lines = [f"{root_label}"]
    for rel in files_relpaths:
        depth = rel.count("/")
        name = rel.split("/")[-1]
        lines.append("  " * (depth + 1) + "📄 " + name)
    return "\n".join(lines)