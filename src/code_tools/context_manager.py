"""
Code interpreter context management.

Handles discovering open files from message history, cleaning stale
interpreter blocks, and generating consolidated file displays to help
the agent maintain awareness of the files it is working with.
"""

import json
import re
from typing import Dict, List, Set

from .code_interpreter import code_interpreter, CODE_INTERPRETER_START, CODE_INTERPRETER_END
from .context_tracker import ContextTracker, register
from ..code_sandbox import WORKSPACE
from ..config import INTERPRETER_WARN_CHARS, INTERPRETER_MAX_FILES


# Tools that trigger code interpreter display
CODE_RELATED_TOOLS = {'read_file', 'write_file', 'edit_file'}

# Tools that remove files from the interpreter
FILE_REMOVAL_TOOLS = {'delete_file', 'close_file'}


def discover_open_files(messages: List[Dict]) -> Set[str]:
    """
    Scan message history to discover all files that should be displayed
    in the code interpreter.
    
    Files are added when read_file, write_file, or edit_file is called.
    Files are removed when delete_file or close_file is called.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Set of file paths that should be displayed
    """
    open_files = set()
    
    for msg in messages:
        # Look for assistant messages with tool calls
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not tc.get("function"):
                    continue
                    
                tool_name = tc["function"].get("name", "")
                args_str = tc["function"].get("arguments", "{}")
                
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    continue
                
                target_file = args.get("target_file")
                if not target_file:
                    continue
                
                if tool_name in CODE_RELATED_TOOLS:
                    open_files.add(target_file)
                elif tool_name in FILE_REMOVAL_TOOLS:
                    open_files.discard(target_file)
    
    return open_files


def strip_code_interpreter_blocks(content: str) -> str:
    """
    Remove all code interpreter blocks from a string.
    
    Args:
        content: The string content to clean
        
    Returns:
        Content with all code interpreter blocks removed
    """
    if not content:
        return content
    
    # Pattern to match the entire code interpreter block
    pattern = re.compile(
        re.escape(CODE_INTERPRETER_START) + r'.*?' + re.escape(CODE_INTERPRETER_END),
        re.DOTALL
    )
    
    cleaned = pattern.sub('', content)
    
    # Clean up any double newlines left behind
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return cleaned.strip()


def clean_previous_interpreter_blocks(messages: List[Dict]) -> List[Dict]:
    """
    Remove code interpreter blocks from all previous tool response messages.
    This is done in-place to save context tokens.
    
    Args:
        messages: List of message dictionaries (modified in place)
        
    Returns:
        The same list with interpreter blocks removed from tool messages
    """
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if CODE_INTERPRETER_START in content:
                msg["content"] = strip_code_interpreter_blocks(content)
    
    return messages


def should_trigger_code_interpreter(tool_name: str) -> bool:
    """Determines if a tool should trigger the code interpreter display."""
    return tool_name in CODE_RELATED_TOOLS or tool_name in FILE_REMOVAL_TOOLS


def generate_consolidated_interpreter_display(messages: List[Dict]) -> str:
    """
    Generate a consolidated code interpreter display for all open files.
    
    If the display would be too large, appends a warning asking the agent
    to close files it no longer needs.
    
    Args:
        messages: Current message history to discover open files from
        
    Returns:
        Consolidated code interpreter block, or empty string if no files
    """
    open_files = discover_open_files(messages)
    
    if not open_files:
        return ""
    
    # Sort files for consistent display order
    sorted_files = sorted(open_files)
    
    # Set root path and generate display
    root_path = WORKSPACE
    code_interpreter.set_root_path(root_path)
    
    display = code_interpreter.display_multiple_files(sorted_files)

    notes = "\n\nNote: This display shows the LATEST state of each file with accurate line numbers. Always use these line numbers for edit_file calls — never use memorised line numbers. Closing a file removes it from this display, including previous tool responses — you will no longer see its contents unless you open it again. Only close a file after you have fully extracted all information you need from it."

    if len(open_files) > INTERPRETER_MAX_FILES or len(display) > INTERPRETER_WARN_CHARS:
        file_list = ", ".join(sorted_files)
        notes += (
            f"\n⚠️ CONTEXT WARNING: You have {len(open_files)} files open "
            f"({file_list}). "
            "To avoid running out of context, please close files you no longer "
            "need by calling close_file() on them. If you still need data from a "
            "currently open file, disregard this warning."
        )

    display = display.replace(CODE_INTERPRETER_END, notes + "\n" + CODE_INTERPRETER_END)

    return display


# ---------------------------------------------------------------------------
# ContextTracker wrapper — auto-registered
# ---------------------------------------------------------------------------

class FileContextTracker(ContextTracker):
    """Living Tool State tracker for workspace files."""

    name = "files"
    trigger_tools = CODE_RELATED_TOOLS | FILE_REMOVAL_TOOLS
    block_start = CODE_INTERPRETER_START
    block_end = CODE_INTERPRETER_END

    def discover(self, messages):
        return discover_open_files(messages)

    def render(self, state):
        if not state:
            return ""
        sorted_files = sorted(state)
        code_interpreter.set_root_path(WORKSPACE)
        display = code_interpreter.display_multiple_files(sorted_files)
        notes = (
            "\n\nNote: This display shows the LATEST state of each file "
            "with accurate line numbers. Always use these line numbers "
            "for edit_file calls — never use memorised line numbers. "
            "Closing a file removes it from this display, including "
            "previous tool responses — you will no longer see its "
            "contents unless you open it again. Only close a file "
            "after you have fully extracted all information you need from it."
        )
        if len(state) > INTERPRETER_MAX_FILES or len(display) > INTERPRETER_WARN_CHARS:
            notes += (
                f"\n⚠️ CONTEXT WARNING: You have {len(state)} files open "
                f"({', '.join(sorted_files)}). "
                "To avoid running out of context, please close files "
                "you no longer need by calling close_file() on them."
            )
        return display.replace(self.block_end, notes + "\n" + self.block_end)


register(FileContextTracker())
