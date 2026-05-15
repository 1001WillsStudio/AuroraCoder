"""
Native tool definitions for OpenAI function calling format.

This module defines all available tools in the standard OpenAI function calling format
to replace the previous custom XML-based tool system.
"""

from typing import Dict, List, Any

# Import all the tool functions
from .core_tools.google_search import search_for_llm
from .core_tools.web_browser import web_fetch
from .code_tools.file_operations import (
    read_file_tool,
    full_file_write_tool,
    range_replace_edit_tool,
    delete_file_tool,
    list_dir_tool,
    file_search_tool,
    close_file_tool,
)
from .code_tools.grep_search import grep_search_tool
from .code_tools.terminal_runner import run_terminal_cmd_tool
from .core_tools.tool_store_client import tool_store_tool
from .core_tools.subagent import run_subagent
from .core_tools.continue_chat import continue_as_new_chat



EDIT_FILE_DESCRIPTION = """Range-based file editing. Supports at most 3 edits per call.

Each edit replaces a line range (start_line through end_line inclusive) with new content.
Edits are atomic: if ANY edit in the call fails validation, NONE are applied and the file is unchanged.

RULES:
- At most 3 edits per call.
- ALWAYS get line numbers and content from the code interpreter display. NEVER use memorised or assumed line numbers.
- `start_line_content` / `end_line_content` are SINGLE LINE verification anchors (no newlines). Leading whitespace MUST match; trailing spaces are ignored.
- `end_line` defaults to `start_line`; `end_line_content` auto-fills from file if omitted.
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
                    "target_file": {
                        "type": "string",
                        "description": "Path to the file to read (relative to workspace)"
                    }
                },
                "required": ["target_file"]
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
                    "target_file": {
                        "type": "string",
                        "description": "Path to the file to write (relative to workspace)"
                    },
                    "code_edit": {
                        "type": "string",
                        "description": "The complete content to write to the file"
                    }
                },
                "required": ["target_file", "code_edit"]
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
                    "target_file": {
                        "type": "string",
                        "description": "Path to the file to edit (relative to workspace)"
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_line": {
                                    "type": "integer",
                                    "description": "1-based line number where the range begins"
                                },
                                "start_line_content": {
                                    "type": "string",
                                    "description": "The SINGLE LINE of text at start_line — exactly one line, NO newlines. Used to verify the file boundary. Trailing spaces are ignored."
                                },
                                "end_line": {
                                    "type": "integer",
                                    "description": "1-based line number where the range ends"
                                },
                                "end_line_content": {
                                    "type": "string",
                                    "description": "The SINGLE LINE of text at end_line — exactly one line, NO newlines. Used to verify the file boundary. Trailing spaces are ignored."
                                },
                                "replace_content": {
                                    "type": "string",
                                    "description": "New content that replaces everything from start_line through end_line (inclusive). Use empty string to delete the range."
                                }
                            },
                            "required": ["start_line", "start_line_content", "replace_content"]
                        }
                    }
                },
                "required": ["target_file", "edits"]
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
                    "target_file": {
                        "type": "string",
                        "description": "Path to the file to delete (relative to workspace)"
                    }
                },
                "required": ["target_file"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_file",
            "description": "Removes a file from the code interpreter display. The file itself is not deleted or modified. Once closed, you will no longer see its contents in the conversation until you reopen it with read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "Path to the file to close from interpreter view (relative to workspace)"
                    }
                },
                "required": ["target_file"]
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
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Performs regex-based text search across files in the workspace, similar to grep command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The regex pattern to search for"
                    },
                    "include_pattern": {
                        "type": "string",
                        "description": "Glob pattern for files to include (e.g., '*.py')"
                    },
                    "exclude_pattern": {
                        "type": "string",
                        "description": "Glob pattern for files to exclude (e.g., '*test*')"
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Whether the search should be case sensitive",
                        "default": True
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "Executes terminal/shell commands in a persistent, stateful Bash shell. The shell starts in /workspace and its environment and working directory are preserved between commands. For long-running processes (servers, training, etc.), set blocking=false so the command runs in the background — output streams to a log file whose path is returned. You can read that log file later to check progress. Do NOT use nohup or trailing & yourself; use blocking=false instead. If a blocking command times out, the command keeps running in the old terminal and a new terminal is started automatically — the log file path is returned so you can check progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute. Do NOT include nohup or trailing &; use the blocking parameter instead. Use 'start_new_terminal' to restart the terminal if needed."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default is 30. Increase for long-running commands."
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "If false, run the command in the background and return immediately with the PID and log file path. Use for servers, training scripts, or any long-running process. Default is true."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_store",
            "description": "A universal tool manager that allows you to search for and execute thousands of public APIs and local utilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "execute", "info"],
                        "description": "The action to perform: 'search' for tools, 'execute' to run a tool, or 'info' to get tool details."
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (required for action='search')"
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool to execute or get info for (required for action='execute'/'info')"
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
    "read_file", "list_directory", "search_files", "grep_search",
    "google_search", "web_browser", "tool_store",
    "subagent",
}

# Tools available to subagents in "read_only" mode.
# Subagents must NOT write/edit/delete files, run terminal commands, or mutate
# system state.  Used by get_filtered_tools() in web_api/app.py.
# "subagent" itself is excluded by get_filtered_tools() to prevent nesting.
SUBAGENT_READ_ONLY_TOOLS = {
    "read_file", "list_directory", "search_files", "grep_search",
    "google_search", "web_browser", "close_file",
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
    "edit_file": range_replace_edit_tool,
    "delete_file": delete_file_tool,
    "close_file": close_file_tool,
    "list_directory": list_dir_tool,
    "search_files": file_search_tool,
    "grep_search": grep_search_tool,
    "run_terminal_command": run_terminal_cmd_tool,
    "tool_store": tool_store_tool,
    "subagent": run_subagent,
    "continue_as_new_chat": continue_as_new_chat,
}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Returns the list of tool definitions for OpenAI function calling."""
    return NATIVE_TOOL_DEFINITIONS


def get_tool_function_map() -> Dict[str, Any]:
    """Returns the mapping of tool names to their implementation functions."""
    return TOOL_FUNCTION_MAP


def execute_tool_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    Executes a tool call with the given arguments.

    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool

    Returns:
        String result from the tool execution
    """
    if tool_name not in TOOL_FUNCTION_MAP:
        return f"Error: Unknown tool '{tool_name}'"

    try:
        function = TOOL_FUNCTION_MAP[tool_name]
        result = function(**arguments)
        return str(result)
    except Exception as e:
        return f"Error executing tool '{tool_name}': {str(e)}"
