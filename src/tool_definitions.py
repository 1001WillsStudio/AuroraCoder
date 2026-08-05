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
    execute_edit_file_normal,
)
# from .code_tools.grep_search import grep_search_tool  # COMMENTED OUT — agent can use terminal grep
from .code_tools.terminal_runner import run_terminal_cmd_tool
from .core_tools.tool_store_client import (
    tool_store_tool,
    get_primary_tool_schemas,
    execute_tool_direct,
)
from .core_tools.subagent import run_subagent
from .core_tools.continue_chat import continue_as_new_chat
from .core_tools.memory_tools import remember_tool, recall_tool, log_gap_tool, forget_tool, report_findings_tool
from .core_tools.memory_client import memory_enabled



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

NORMAL_EDIT_FILE_DESCRIPTION = """Performs exact string replacements in an existing file.

Usage:
- When editing text, ensure you preserve the exact indentation (tabs/spaces) as it appears before.
- ALWAYS prefer editing existing files. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file.
  * Either provide a larger string with more surrounding context to make it unique.
  * Or set `replace_all` to true to replace every occurrence.
- To create or overwrite a file, prefer using the `write_file` tool.

CRITICAL:
- old_string must include all whitespace, indentation, blank lines, and surrounding code exactly as it appears in the file.
- You MUST preserve the original punctuation exactly in old_string — including full-width/half-width forms and Chinese/English marks, especially quotation marks.
  * NEVER escape them with \\ or any other character.
  * If the original code uses Chinese quotation marks 「」, keep them in old_string — do NOT replace with ".
- For deletion, use an empty string as new_string.
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
            "description": "Deletes a specified file from the filesystem. Accepts a single file path or a list of file paths to delete multiple files at once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": ["string", "array"],
                        "description": "Path to the file(s) to delete (relative to workspace). Pass a single string or an array of strings to delete multiple files at once.",
                        "items": {"type": "string"}
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
    # COMMENTED OUT — agent can use terminal: find / fd to search for files
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "search_files",
    #         "description": "Searches for files by name pattern in the workspace.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "query": {
    #                     "type": "string",
    #                     "description": "File name pattern to search for"
    #                 }
    #             },
    #             "required": ["query"]
    #         }
    #     }
    # },
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
            "description": "Executes terminal/shell commands in a persistent, stateful Bash shell. The shell starts in /workspace and its environment and working directory are preserved between commands. For long-running processes (servers, training, etc.), set blocking=false so the command runs in the background — output streams to a log file whose path is returned. You can read that log file later to check progress. Do NOT use nohup or trailing & yourself; use blocking=false instead. If a blocking command times out, the command keeps running in the old terminal and a new terminal is started automatically — the log file path is returned so you can check progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute. Do NOT include nohup or trailing &; use the blocking parameter instead."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. The default of 10 is sufficient for most commands, so you usually should not specify this parameter. For commands that may take longer than 30 seconds, prefer blocking=false instead."
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "If false, run the command in the background and return immediately with the PID and log file path. Use for servers, training scripts, or any long-running process. Default is true."
                    },
                    "new_terminal": {
                        "type": "boolean",
                        "description": "If true, restart the persistent shell before running the command. Use when the terminal is unresponsive or you need a clean environment. Default is false."
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
            "name": "remember",
            "description": (
                "Nominate a durable fact to be saved to long-term memory so future sessions "
                "don't need to be told again. Use SPARINGLY — only for facts that (a) are NOT "
                "derivable from the code/git/AGENTS.md, and (b) would concretely change "
                "how you or a future agent should act. Good candidates: a stated "
                "preference or correction, project context (goals/ownership/deadlines/"
                "incidents), a pointer to an external system (ticket tracker, dashboard), "
                "or a non-obvious convention/gotcha. Do NOT save: anything re-derivable by "
                "reading the code, ephemeral task state, secrets/credentials, or anything "
                "already in AGENTS.md. When in doubt, don't call this tool — silence is the "
                "correct default (most turns should not call `remember`). Note: this does NOT "
                "write anything immediately — it's judged (and possibly rejected, merged into "
                "an existing memory, or adjusted) together with the rest of this session's "
                "transcript at session end, so it will NOT be visible to `recall` later in this "
                "same session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory itself, written so a future agent can act on it directly."
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary used to decide relevance later — be specific (mention identifiers/names)."
                    },
                    "plane": {
                        "type": "string",
                        "enum": ["stance", "world"],
                        "description": "'stance' = always shown to you every turn (use only for preference/feedback/communication/autonomy — keep these SHORT). 'world' = retrieved on demand when relevant (use for everything else). Default 'world'."
                    },
                    "type": {
                        "type": "string",
                        "enum": ["preference", "feedback", "communication", "autonomy", "project", "reference", "convention", "landmine"],
                        "description": "preference/feedback/communication/autonomy → stance plane. project/reference/convention/landmine → world plane."
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "project"],
                        "description": "'user' = applies to this person across all projects. 'project' = specific to the current workspace. Default 'project'."
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "'high' if the user stated it directly, 'medium' if inferred, 'low' if guessed."
                    },
                    "provenance": {
                        "type": "string",
                        "description": "How you learned this, e.g. 'user stated 2026-07-01' or 'inferred from git blame src/pipe/*'."
                    },
                    "volatile": {
                        "type": "boolean",
                        "description": "True if this fact can go stale (e.g. a deadline, a status) and should be re-verified later. Default false."
                    },
                    "memory_id": {
                        "type": "string",
                        "description": "Pass the id of an existing memory (from a prior `recall`) to UPDATE it in place instead of creating a duplicate. Prefer this over creating a near-duplicate memory."
                    }
                },
                "required": ["content", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search long-term memory for facts relevant to the current task "
                "(preferences, project context, conventions, past gotchas). Use when "
                "you suspect something was already established in a previous session, "
                "or the user references prior work you don't have in this conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you're trying to find out, in natural language."
                    },
                    "plane": {
                        "type": "string",
                        "enum": ["stance", "world"],
                        "description": "Almost always 'world' (default) — 'stance' is already shown to you every turn so rarely needs an explicit recall."
                    },
                    "k": {
                        "type": "integer",
                        "description": "Max number of results (default 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_gap",
            "description": (
                "Flag something you don't know but had to guess at or defer, so it's "
                "tracked instead of silently re-guessed next time (a 'gap'). Use when: "
                "you resolved ambiguity by guessing, a needed convention/ownership/intent "
                "was missing, an external system was referenced but you don't understand "
                "it, or you noticed the SAME uncertainty come up again (recurring gaps are "
                "escalated automatically). Prefer self-investigating cheap gaps in this "
                "turn and calling `remember` with the answer instead of logging them — "
                "`log_gap` is for gaps that are NOT cheap to resolve right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The specific thing you don't know, phrased as a concrete question."
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "project"],
                        "description": "'user' = applies across all projects. 'project' = specific to this workspace. Default 'project'."
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "How much this gap is blocking good decisions. Default 'medium'."
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["self", "ask"],
                        "description": "'self' if this is plausibly self-investigable later (grep/git log/docs). 'ask' if only the user can answer it. Default 'ask'."
                    }
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": (
                "Permanently delete one memory, right now — no judgment pass, unlike "
                "`remember`. Use ONLY when the user explicitly says a remembered fact is "
                "wrong, outdated, or asks you to forget/remove/delete something specific. "
                "You MUST `recall` first to get the real id — never guess or invent one. "
                "If the user's correction should instead be REPLACED with a corrected "
                "fact (not just removed), prefer `remember` with `memory_id` set to update "
                "it in place."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The exact id of the memory to delete, from a prior `recall` result."
                    }
                },
                "required": ["memory_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "report_findings",
            "description": (
                "Report the outcome of your investigation and end the task. Call this EXACTLY "
                "ONCE, as your final action — do not call any tool after it. Only meaningful "
                "inside an isolated gap-investigation session (see your system prompt); calling "
                "it anywhere else is harmless but pointless."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resolved": {
                        "type": "boolean",
                        "description": "True if you found a real, evidence-backed answer. False if you genuinely could not — that is a normal, acceptable outcome, not a failure."
                    },
                    "answer": {
                        "type": "string",
                        "description": "The fact/answer you found, written so a future agent can act on it directly. Required if resolved=true."
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary of the finding, used for relevance ranking later. Required if resolved=true."
                    },
                    "plane": {
                        "type": "string",
                        "enum": ["stance", "world"],
                        "description": "Almost always 'world' for investigation findings. Default 'world'."
                    },
                    "type": {
                        "type": "string",
                        "enum": ["project", "reference", "convention", "landmine", "gap_resolution"],
                        "description": "Default 'gap_resolution'."
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "project"],
                        "description": "Default 'project'."
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "Judge honestly against the checklist in your instructions — do not default to high."
                    },
                    "notes": {
                        "type": "string",
                        "description": "If resolved=false, briefly explain what you tried and why it came up empty."
                    }
                },
                "required": ["resolved"]
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
    "read_file", "list_directory",  # "search_files", "grep_search",  # COMMENTED OUT
    "google_search", "web_browser", "tool_store",
    "subagent", "recall",
}

# Tools available to subagents in "read_only" mode.
# Subagents must NOT write/edit/delete files, run terminal commands, or mutate
# system state.  Used by get_filtered_tools() in web_api/app.py.
# "subagent" itself is excluded by get_filtered_tools() to prevent nesting.
SUBAGENT_READ_ONLY_TOOLS = {
    "read_file", "list_directory",  # "search_files", "grep_search",  # COMMENTED OUT
    "google_search", "web_browser", "close_file",
    "recall",  # read-only memory query — safe; "remember" is deliberately excluded
    # "tool_store",  # TODO: candidate — needs review.  Many external APIs
    #                 # are write-capable, so this can bypass subagent safety.
}

# Backward-compatible alias — kept so any external references don't break immediately.
READ_ONLY_TOOLS = PARALLEL_SAFE_TOOLS

# Tool names owned by the memory subsystem — excluded entirely from the tool
# list (not just no-op'd) when ``settings.other.memory.enabled`` is False, so
# a disabled agent looks and behaves exactly like a build with no memory
# module: the LLM never even sees these schemas. See memory_filter_tools().
# report_findings is included here too even though it only ever actually
# reaches an LLM via the "gap_investigation" tools mode (get_filtered_tools
# in src/web_api/app.py) — heavy_ops_enabled() already implies
# memory_enabled(), so this is belt-and-suspenders, not load-bearing.
MEMORY_TOOL_NAMES = {"remember", "recall", "log_gap", "forget", "report_findings"}

# Tools available to an isolated gap-investigation worker (see
# memory/ops/dispatcher.py) — read-oriented plus the one tool that ends the
# task. Used by get_filtered_tools() in web_api/app.py via the "tools":
# "gap_investigation" mode on that one-shot /api/chat call. Deliberately
# does NOT include remember/recall/log_gap/forget: the worker has no
# gateway to reach the memory HTTP API through anyway, and findings are
# meant to flow back through report_findings -> the dispatcher -> the
# normal write pass, not through the worker calling memory tools directly.
GAP_INVESTIGATION_TOOLS = {"read_file", "list_directory", "run_terminal_command", "report_findings"}

# Unified tool set for the memory-maintenance worker — the agent directly
# calls remember/forget to write into the shared memory store (the worker
# mounts the main container's AURORACODER_DATA_DIR, so writes are immediate
# and visible). Read tools let it inspect the workspace snapshot for
# investigation tasks; report_findings is kept for backward compatibility
# with the existing gap-investigation path.  Used by get_filtered_tools() in
# web_api/app.py via the "tools":"memory_maintenance" mode.
MEMORY_MAINTENANCE_TOOLS = {"remember", "forget", "read_file", "list_directory", "run_terminal_command", "report_findings"}

# Alias — gap investigation is just one maintenance kind now.
GAP_INVESTIGATION_TOOLS = MEMORY_MAINTENANCE_TOOLS


def memory_filter_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop memory tool schemas from *tools* unless memory is enabled.

    Applied everywhere a tool list reaches the LLM — the default set
    (get_tool_definitions) and the subagent-filtered set
    (web_api.app.get_filtered_tools) — so a disabled subagent run can't
    see ``recall`` either.
    """
    if memory_enabled():
        return tools
    return [t for t in tools if t["function"]["name"] not in MEMORY_TOOL_NAMES]


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
    # "search_files": file_search_tool,  # COMMENTED OUT — agent can use terminal: find / fd
    # "grep_search": grep_search_tool,  # COMMENTED OUT — agent can use terminal grep
    "run_terminal_command": run_terminal_cmd_tool,
    "tool_store": tool_store_tool,
    "subagent": run_subagent,
    "continue_as_new_chat": continue_as_new_chat,
    "remember": remember_tool,
    "recall": recall_tool,
    "log_gap": log_gap_tool,
    "forget": forget_tool,
    "report_findings": report_findings_tool,
}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Returns the list of tool definitions for OpenAI function calling.

    Merges the hard‑coded native tool schemas with primary ToolStore tools
    whose function schemas are injected directly so the LLM can call them
    like any other native tool.

    The ``edit_file`` schema is swapped based on the ``EDIT_MODE`` env var:
    ``"aurora"`` (default) → anchor-based range replacement; ``"normal"``
    → simple string replacement.
    """
    import os
    tools = copy.deepcopy(NATIVE_TOOL_DEFINITIONS)

    # ── Edit-mode swap ───────────────────────────────────────────────
    edit_mode = os.environ.get("EDIT_MODE", "aurora")
    if edit_mode == "normal":
        normal_def = {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": NORMAL_EDIT_FILE_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "Path to the file to edit (relative to workspace)"
                        },
                        "old_string": {
                            "type": "string",
                            "description": "The exact string to find and replace in the file. Must be unique in the file unless replace_all is true."
                        },
                        "new_string": {
                            "type": "string",
                            "description": "The replacement string (use empty string to delete)"
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences of old_string (default: false). When false, old_string must be unique in the file."
                        }
                    },
                    "required": ["file", "old_string", "new_string"]
                }
            }
        }
        # Replace the aurora edit_file entry
        tools = [
            normal_def if t["function"]["name"] == "edit_file" else t
            for t in tools
        ]

    tools = memory_filter_tools(tools)
    try:
        tools.extend(get_primary_tool_schemas())
    except Exception:
        pass
    return tools


def get_tool_function_map() -> Dict[str, Any]:
    """Returns the mapping of tool names to their implementation functions.

    The ``edit_file`` entry is swapped based on the ``EDIT_MODE`` env var.
    """
    import os
    result = dict(TOOL_FUNCTION_MAP)
    edit_mode = os.environ.get("EDIT_MODE", "aurora")
    if edit_mode == "normal":
        result["edit_file"] = execute_edit_file_normal
    return result


# ── Build the set of valid parameters per tool from NATIVE_TOOL_DEFINITIONS ──
# This guards against LLM-hallucinated extra arguments.
def _build_valid_params() -> Dict[str, set]:
    valid = {}
    for tdef in NATIVE_TOOL_DEFINITIONS:
        props = tdef["function"]["parameters"].get("properties", {})
        valid[tdef["function"]["name"]] = set(props.keys())
    # Also include the normal-mode edit_file params so argument filtering
    # doesn't drop them when EDIT_MODE=normal.
    if "edit_file" in valid:
        valid["edit_file"] |= {"old_string", "new_string", "replace_all"}
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
    # Use the dynamic function map (respects EDIT_MODE for edit_file)
    function_map = get_tool_function_map()
    if tool_name not in function_map:
        # Not a native tool — route to the ToolStore (primary tools). Their
        # schemas are injected at startup so the LLM calls them like any other.
        return arguments, execute_tool_direct(tool_name, arguments)

    # ── Uniform native-tool execution ────────────────────────────────
    # All tools in the function map have signature:
    #   (arguments: Dict[str, Any]) -> (result: str, arguments: Dict[str, Any])
    function = function_map[tool_name]
