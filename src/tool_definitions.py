"""
Native tool definitions for OpenAI function calling format.

This module defines all available tools in the standard OpenAI function calling format
to replace the previous custom XML-based tool system.
"""

from typing import Dict, List, Any

# Import all the tool functions
from .core_tools.google_search import search_for_llm
from .core_tools.web_browser import web_fetch
# from .core_tools.code_interpreter import run_like_jupyter
from .code_tools.file_operations import (
    read_file_tool, 
    full_file_write_tool,
    search_replace_edit_tool,
    delete_file_tool,
    list_dir_tool,
    file_search_tool,
    close_file_tool,
)
from .code_tools.grep_search import grep_search_tool
from .code_tools.terminal_runner import run_terminal_cmd_tool
from .core_tools.tool_store_client import tool_store_tool
from .core_tools.subagent import run_subagent


EDIT_FILE_DESCRIPTION = """Aider-style search and replace. Finds exact content starting from `start_line` and replaces it.

RULES:
- `search_content` must match file content (indentation, newlines matter; trailing spaces are ignored)
- Include 1-3 lines of context to uniquely identify the location
- One edit per call; make multiple calls for multiple edits
- Use empty `replace_content` to delete; include a landmark line to insert after it
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
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "python_interpreter",
    #         "description": "Executes Python code in a Jupyter-like environment, capturing stdout and handling the last expression automatically. This is for simple task only, and code is run in a rather simple env - diff from the terminal.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "code": {
    #                     "type": "string",
    #                     "description": "The Python code to execute"
    #                 }
    #             },
    #             "required": ["code"]
    #         }
    #     }
    # },
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
                    "start_line": {
                        "type": "integer",
                        "description": "The line number to start searching from (1-based). The search begins at this line and looks forward."
                    },
                    "search_content": {
                        "type": "string",
                        "description": "The exact content to find and replace. Must match the file content exactly, including whitespace and indentation."
                    },
                    "replace_content": {
                        "type": "string",
                        "description": "The replacement content. Use empty string to delete the matched content."
                    }
                },
                "required": ["target_file", "start_line", "search_content", "replace_content"]
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
                        "description": "Path to directory to list (relative to workspace). Empty string for root.",
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
            "description": "Executes terminal/shell commands in a persistent, stateful shell session with Conda. The shell's environment and working directory are preserved between commands. For long-running processes (servers, training, etc.), set blocking=false so the command runs in the background — output streams to a log file whose path is returned. You can read that log file later to check progress. Do NOT use nohup or trailing & yourself; use blocking=false instead. If a blocking command times out, the command keeps running in the old terminal and a new terminal is started automatically — the log file path is returned so you can check progress.",
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
                "Delegate a self-contained task to an independent agent. The subagent "
                "gets its own context window and tools, works autonomously, and returns "
                "only a concise summary — keeping your own context clean.\n\n"
                "SAFETY: The subagent runs in READ-ONLY mode for safety. It can read "
                "files, search, browse the web, and list directories, but it CANNOT "
                "write/edit/delete files, run terminal commands, or make any changes to "
                "the system. Use it strictly for information gathering and analysis.\n\n"
                "WHEN TO USE:\n"
                "- The task requires multiple read/search calls (3+) whose intermediate "
                "outputs you don't need to see (e.g. research across several web pages, "
                "exploring a codebase to answer a question, reading and cross-referencing "
                "many files)\n"
                "- The task is self-contained and does not depend on your current "
                "conversation context\n"
                "- You want to preserve your own context window for the main task\n\n"
                "WHEN NOT TO USE:\n"
                "- For tasks that need only 1-2 tool calls — just call the tools directly\n"
                "- When you need to see exact raw outputs (file contents, error details)\n"
                "- When the subtask depends heavily on prior conversation context "
                "(the subagent cannot see your history)\n"
                "- When the task requires writing files, running commands, or any "
                "modifications — you must do those yourself\n\n"
                "IMPORTANT: The subagent has NO knowledge of your conversation. Provide "
                "all necessary context (file paths, requirements, constraints) in the task "
                "description. Be specific about what it should return."
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
]


# Read-only / side-effect-free tools.  Used for:
# - concurrent execution (safe to run in parallel since they don't mutate state)
# - subagent "read_only" mode (restricted tool set for exploration/research)
# Update this set when adding new read-only tools.
READ_ONLY_TOOLS = {
    "read_file", "list_directory", "search_files", "grep_search",
    "google_search", "web_browser", "close_file", "tool_store",
    "subagent",
}


# Tool function mappings - maps tool names to their actual functions
TOOL_FUNCTION_MAP = {
    "google_search": search_for_llm,
    "web_browser": web_fetch,
    # "python_interpreter": run_like_jupyter,
    "read_file": read_file_tool,
    "write_file": full_file_write_tool,
    "edit_file": search_replace_edit_tool,
    "delete_file": delete_file_tool,
    "close_file": close_file_tool,
    "list_directory": list_dir_tool,
    "search_files": file_search_tool,
    "grep_search": grep_search_tool,
    "run_terminal_command": run_terminal_cmd_tool,
    "tool_store": tool_store_tool,
    "subagent": run_subagent,
}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Returns the list of tool definitions for OpenAI function calling."""
    return NATIVE_TOOL_DEFINITIONS


def get_tool_function_map() -> Dict[str, callable]:
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
