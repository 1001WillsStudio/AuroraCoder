"""
Native tool definitions for OpenAI function calling format.

This module defines all available tools in the standard OpenAI function calling format
to replace the previous custom XML-based tool system.
"""

import copy
import logging
from typing import Dict, List, Any

# Import all the tool functions
from .core_tools.google_search import search_for_llm
from .core_tools.web_browser import web_fetch
from .code_tools.file_operations import (
    read_file_tool,
    full_file_write_tool,
    delete_file_tool,
    list_dir_tool,
    file_search_tool,
    close_file_tool,
    execute_edit_file,
)
# from .code_tools.grep_search import grep_search_tool  # COMMENTED OUT — agent can use terminal grep
from .code_tools.terminal_runner import run_terminal_cmd_tool, close_terminal_tool
from .core_tools.tool_store_client import (
    tool_store_tool,
    get_primary_tool_schemas,
    execute_tool_direct,
)
from .core_tools.subagent import run_subagent
from .core_tools.continue_chat import continue_as_new_chat



EDIT_FILE_DESCRIPTION = """Range-based file editing.

Each edit replaces the line range given by `remove_line_number` (e.g. "13-15", "24-36") with new content. For a single line, just use the line number (e.g. "42", shorthand for "42-42").
Edits are atomic: if ANY edit in the call fails validation, NONE are applied and the file is unchanged.

RULES:
- ALWAYS get line numbers and content from the code interpreter display. NEVER use memorised or assumed line numbers.
- `content_to_remove` uses anchor matching: "first_line\\n[TO]\\nlast_line". The "[TO]" is a range marker — only the first and last lines act as anchors to find the block; intermediate lines are ignored. Always use "[TO]" for multi-line ranges (omit only for single-line edits where start == end). Example: to replace a 4-line function, use "def foo():\\n[TO]\\n    return x" — do NOT paste the full function body.
- `remove_line_number` format: "13-15" for lines 13 through 15 inclusive, or "42" for a single line.
- Multiple edits per call: all line numbers refer to the file as it was BEFORE this call. Ranges must not overlap.
- Use empty `replace_content` to delete the range.
- Do NOT edit the same file more than once per turn. After an edit, read the refreshed code interpreter for correct line numbers before editing that file again.
"""


# Tool definitions in OpenAI function calling format
NATIVE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": "Performs a Google search and returns the results as formatted text with titles, sources, and summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "The search query to look up on Google"
                    }
                },
                "required": ["search_term"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_browser",
            "description": (
                "Fetches content from a URL, converts HTML to Markdown, and processes "
                "it with a fast secondary model to extract relevant information.\n\n"
                "IMPORTANT: Always provide a 'prompt' describing what you need — the raw "
                "page is processed by a cheap secondary model so only a concise summary "
                "enters your context. Without a prompt, you get raw Markdown which can be "
                "very large.\n\n"
                "Usage notes:\n"
                "- The URL must be a fully-formed valid URL\n"
                "- HTTP URLs are automatically upgraded to HTTPS\n"
                "- Includes a 15-minute cache for repeated access\n"
                "- Cross-host redirects are reported rather than followed\n"
                "- Use google_search first to find URLs, then this tool to read them"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_url": {
                        "type": "string",
                        "description": "The URL to fetch and extract content from"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "What information to extract from the page. The full page is sent to a fast secondary model with this prompt, and only the concise answer is returned. Examples: 'What are the main API endpoints?', 'Extract the pricing table', 'Summarize the key findings'"
                    }
                },
                "required": ["target_url", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads a file and displays its content in the code interpreter. Checks for file existence and confirms it can be opened.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": ["string", "array"],
                        "description": "Path to the file(s) to read (relative to workspace). Pass a single string or an array of strings to open multiple files at once.",
                        "items": {"type": "string"}
                    },
                },
                "required": ["file"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Creates a new file or completely replaces the content of an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to the file to write (relative to workspace)"
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete content to write to the file"
                    }
                },
                "required": ["file", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": EDIT_FILE_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to the file to edit (relative to workspace)"
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply.",
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "remove_line_number": {
                                    "type": "string",
                                    "description": "Line range to remove, e.g., \"13-15\" (lines 13–15 inclusive) or \"42\" (line 42 only)."
                                },
                                "content_to_remove": {
                                    "description": "Anchor-based block identifier. For multi-line: 'first_line\\n[TO]\\nlast_line' — only the boundary lines are matched, intermediate lines are ignored. For single-line edits (start == end): just the line content itself with no '[TO]'."
                                },
                                "replace_content": {
                                    "type": "string",
                                    "description": "New content that replaces everything in the specified remove_line_number (inclusive). Use empty string to delete the range."
                                }
                            },
                            "required": ["remove_line_number", "content_to_remove", "replace_content"]
                        }
                    }
                },
                "required": ["file", "edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Deletes a specified file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to the file to delete (relative to workspace)"
                    }
                },
                "required": ["file"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_file",
            "description": "Removes files from the code interpreter display. The files themselves are not deleted or modified. Once closed, you will no longer see their contents in the conversation until you reopen them with read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": ["string", "array"],
                        "description": "Path to the file(s) to close (relative to workspace). Pass a single string or an array of strings to close multiple files at once. Omit when using 'keep'.",
                        "items": {"type": "string"}
                    },
                    "keep": {
                        "type": "array",
                        "description": "Close ALL open files EXCEPT those listed here. When provided, file is ignored. Use an empty array to close everything.",
                        "items": {"type": "string"}
                    }
                },
                "required": []
            }
        }
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
                        "default": ""
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Searches for files by name pattern in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "File name pattern to search for"
                    }
                },
                "required": ["query"]
            }
        }
    },
    # COMMENTED OUT — agent can use terminal: grep -rn "pattern" .
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "grep_search",
    #         "description": "Performs regex-based text search across files in the workspace, similar to grep command.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "query": {
    #                     "type": "string",
    #                     "description": "The regex pattern to search for"
    #                 },
    #                 "include_pattern": {
    #                     "type": "string",
    #                     "description": "Glob pattern for files to include (e.g., '*.py')"
    #                 },
    #                 "exclude_pattern": {
    #                     "type": "string",
    #                     "description": "Glob pattern for files to exclude (e.g., '*test*')"
    #                 },
    #                 "case_sensitive": {
    #                     "type": "boolean",
    #                     "description": "Whether the search should be case sensitive",
    #                     "default": True
    #                 }
    #             },
    #             "required": ["query"]
    #         }
    #     }
    # },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "Executes terminal/shell commands in a persistent, stateful Bash shell. The shell starts in /workspace and its environment and working directory are preserved between commands. For long-running processes (servers, training, etc.), set blocking=false so the command runs in the background — output streams to a log file whose path is returned. You can read that log file later to check progress. Do NOT use nohup or trailing & yourself; use blocking=false instead. If a blocking command times out, the command keeps running in the old terminal and a new terminal is started automatically — the log file path is returned so you can check progress.\n\nThe Terminal Panel displays all active terminals with ordered names (Terminal #1, #2, …). Use `close_terminal_id` to remove a terminal from the panel — the log file path will be returned so you can still read the results with read_file later. Use `refresh=true` to refresh the panel without executing a command. Use `terminal_label` to give a terminal a custom name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute. Do NOT include nohup or trailing &; use the blocking parameter instead."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default is 30. Increase for long-running commands."
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "If false, run the command in the background and return immediately with the PID and log file path. Use for servers, training scripts, or any long-running process. Default is true."
                    },
                    "new_terminal": {
                        "type": "boolean",
                        "description": "If true, restart the persistent shell before running the command. Use when the terminal is unresponsive or you need a clean environment. Default is false."
                    },
                    "close_terminal_id": {
                        "type": "string",
                        "description": "Close (remove) a terminal from the Terminal Panel display. Set this to a terminal ID shown in the panel (e.g., 'Terminal #1'). The log file path for the closed terminal will be returned so you can still read_file it later. No command is executed when this is set."
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "If true, refresh the Terminal Panel without executing any command. Use this to update the display when a background process may have completed. No command is executed."
                    },
                    "terminal_label": {
                        "type": "string",
                        "description": "Optional human-readable label for this terminal. If not provided, an ordered name like 'Terminal #1' is auto-generated."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_terminal",
            "description": "Close (remove) a terminal from the Terminal Panel display without executing any command. Like close_file but for terminals. The terminal's log file path is returned so you can still read_file the results later. Use this after you have finished reading a background terminal's output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "terminal_id": {
                        "type": "string",
                        "description": "The terminal to close. Use the label shown in the Terminal Panel, e.g. 'Terminal #1' or a custom label set via terminal_label."
                    }
                },
                "required": ["terminal_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_store",
            "description": "A universal tool manager that allows you to search for, inspect, and execute thousands of tools and local utilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "execute", "info", "close"],
                        "description": "The action to perform: 'search' for tools, 'execute' to run a tool, 'info' to add a tool to your context, 'close' to remove it."
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (required for action='search')"
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool to execute, inspect, or manage context for (required for action='execute'/'info'/'close')"
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments for the tool execution (required for action='execute')"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": (
                "Delegate a self-contained research task to a read-only agent. "
                "It runs independently with its own context and tools, then returns a concise summary.\n\n"
                "- READ-ONLY: can read files, search, and browse the web — cannot write, edit, or run commands.\n"
                "- PARALLEL: launch multiple subagents in one turn for independent tasks.\n"
                "- Use when a task needs 3+ read/search calls whose intermediate outputs you don't need.\n"
                "- Don't use for 1-2 tool calls (do those directly) or tasks needing conversation history.\n"
                "- Include all necessary context in the task — the subagent cannot see your conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Detailed description of what the subagent should do and what it should return. Include all necessary context — file paths, requirements, constraints — since the subagent cannot see your conversation history."
                    }
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "continue_as_new_chat",
            "description": (
                "Continue the current task in a fresh conversation. "
                "Call this when the context window is nearly full (~80%+) to hand off "
                "to a new agent instance. Your prompt will be passed directly to the "
                "new agent as its user message — include all essential context.\n\n"
                "WHEN TO CALL: Your context is approaching the limit. You have made "
                "significant progress and need a fresh context window to continue "
                "working efficiently.\n\n"
                "IMPORTANT: This should be the ONLY tool call in the turn. The loop "
                "ends after this call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "A comprehensive prompt covering all the context the next agent needs: what was accomplished, what remains to be done, key files, important decisions made, and any nuanced understanding required."
                    }
                },
                "required": ["prompt"]
            }
        }
    },
]


# Tools safe to execute in parallel via ThreadPoolExecutor.
# These have no conflicting side effects / race conditions.
# Used by partition_tool_calls() in main_flow.py to decide what can run concurrently.
PARALLEL_SAFE_TOOLS = {
    "read_file", "list_directory", "search_files",  # "grep_search",  # COMMENTED OUT
    "google_search", "web_browser", "tool_store",
    "subagent", "close_terminal",  # Read-only — just closes a panel display entry
}

# Tools available to subagents in "read_only" mode.
# Subagents must NOT write/edit/delete files, run terminal commands, or mutate
# system state.  Used by get_filtered_tools() in web_api/app.py.
# "subagent" itself is excluded by get_filtered_tools() to prevent nesting.
SUBAGENT_READ_ONLY_TOOLS = {
    "read_file", "list_directory", "search_files",  # "grep_search",  # COMMENTED OUT
    "google_search", "web_browser", "close_file",
    "close_terminal",  # Read-only — just closes a panel display entry
    # "tool_store",  # TODO: candidate — needs review.  Many external APIs
    #                 # are write-capable, so this can bypass subagent safety.
}

# Backward-compatible alias — kept so any external references don't break immediately.
READ_ONLY_TOOLS = PARALLEL_SAFE_TOOLS


# Tool function mappings - maps tool names to their actual functions
TOOL_FUNCTION_MAP = {
    "google_search": search_for_llm,
    "web_browser": web_fetch,
    "read_file": read_file_tool,
    "write_file": full_file_write_tool,
    "edit_file": execute_edit_file,
    "delete_file": delete_file_tool,
    "close_file": close_file_tool,
    "list_directory": list_dir_tool,
    "search_files": file_search_tool,
    # "grep_search": grep_search_tool,  # COMMENTED OUT — agent can use terminal grep
    "run_terminal_command": run_terminal_cmd_tool,
    "close_terminal": close_terminal_tool,
    "tool_store": tool_store_tool,
    "subagent": run_subagent,
    "continue_as_new_chat": continue_as_new_chat,
}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Returns the list of tool definitions for OpenAI function calling.

    Merges the hard‑coded native tool schemas with primary ToolStore tools
    whose function schemas are injected directly so the LLM can call them
    like any other native tool.
    """
    tools = copy.deepcopy(NATIVE_TOOL_DEFINITIONS)
    try:
        tools.extend(get_primary_tool_schemas())
    except Exception:
        pass
    return tools


def get_tool_function_map() -> Dict[str, Any]:
    """Returns the mapping of tool names to their implementation functions."""
    return TOOL_FUNCTION_MAP


# ── Build the set of valid parameters per tool from NATIVE_TOOL_DEFINITIONS ──
# This guards against LLM-hallucinated extra arguments.
def _build_valid_params() -> Dict[str, set]:
    valid = {}
    for tdef in NATIVE_TOOL_DEFINITIONS:
        props = tdef["function"]["parameters"].get("properties", {})
        valid[tdef["function"]["name"]] = set(props.keys())
    return valid

_TOOL_VALID_PARAMS: Dict[str, set] = _build_valid_params()


def execute_tool_call(tool_name: str, arguments: Dict[str, Any], tool_call_id: str | None = None, conversation_id: str | None = None):
    """
    Executes a tool call with the given arguments.

    Every tool returns a (result, arguments) pair:
      * ``arguments`` is the argument dict as-applied (hallucinated
        args dropped, line numbers resolved for edit_file, etc.)
      * ``result`` is the string result from the tool execution.

    All tools share the same uniform interface:
      function(arguments: Dict) -> (result: str, arguments: Dict)

    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool
        tool_call_id: Optional id of the tool call (used by subagent to link events)

    Returns:
        Tuple of (arguments: Dict, result: str)
    """
    # ── Schema-based argument filtering ──────────────────────────────
    # Drop any LLM-hallucinated extra arguments that are not declared in
    # the tool's JSON schema.  This guard runs BEFORE any tool logic.
    valid_params = _TOOL_VALID_PARAMS.get(tool_name)
    if valid_params is not None:
        extra = set(arguments) - valid_params
        if extra:
            logging.getLogger(__name__).warning(
                "Dropping unknown arguments for %s: %s", tool_name, extra,
            )
            arguments = {k: v for k, v in arguments.items() if k in valid_params}

    # ── ToolStore routing ────────────────────────────────────────────
    if tool_name not in TOOL_FUNCTION_MAP:
        # Not a native tool — route to the ToolStore (primary tools). Their
        # schemas are injected at startup so the LLM calls them like any other.
        return arguments, execute_tool_direct(tool_name, arguments)

    # ── Uniform native-tool execution ────────────────────────────────
    # All tools in TOOL_FUNCTION_MAP have signature:
    #   (arguments: Dict[str, Any]) -> (result: str, arguments: Dict[str, Any])
    function = TOOL_FUNCTION_MAP[tool_name]

    # Subagent: inject execution-only metadata.  The subagent function strips
    # tool_call_id and conversation_id from the returned applied args so the
    # LLM never sees them.
    if tool_name == "subagent":
        if tool_call_id:
            arguments = {**arguments, "tool_call_id": tool_call_id}
        if conversation_id:
            arguments = {**arguments, "conversation_id": conversation_id}

    result, arguments = function(arguments)
    return arguments, result
