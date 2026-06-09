"""
Code interpreter context management.

Handles discovering open files from message history, cleaning stale
interpreter blocks, and generating consolidated file displays to help
the agent maintain awareness of the files it is working with.
"""

import json
import re
from typing import Dict, List, Set

from .code_interpreter import code_interpreter, CODE_INTERPRETER_START
from .panel_manager import Panel
from ..code_sandbox import WORKSPACE
from ..config import CONTEXT_DISPLAY_WARN_CHARS, CONTEXT_DISPLAY_MAX_ITEMS


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




# ---------------------------------------------------------------------------
# Panel implementation
# ---------------------------------------------------------------------------

class CodeInterpreterPanel(Panel):
    """Living Tool State panel for workspace files."""

    name = "files"
    trigger_tools = CODE_RELATED_TOOLS | FILE_REMOVAL_TOOLS
    heading = CODE_INTERPRETER_START

    def discover(self, messages):
        return discover_open_files(messages)

    def render(self, state):
        if not state:
            return ""
        sorted_files = sorted(state)
        code_interpreter.set_root_path(WORKSPACE)
        display = code_interpreter.display_multiple_files(sorted_files)
        notes = (
            f"\n\nNote: This display shows the LATEST state of each file (refreshed after `{self._trigger_tool}`). "
            "Always use these line numbers for edit_file calls — never "
            "use memorised line numbers. Closing a file removes it from "
            "this display, including previous tool responses — you will "
            "no longer see its contents unless you open it again. Only "
            "close a file after you have fully extracted all information "
            "you need from it."
        )
        if len(state) > CONTEXT_DISPLAY_MAX_ITEMS or len(display) > CONTEXT_DISPLAY_WARN_CHARS:
            notes += (
                f"\n⚠️ CONTEXT WARNING: You have {len(state)} files open "
                f"({', '.join(sorted_files)}). "
                "To avoid running out of context, please close files "
                "you no longer need by calling close_file() on them."
            )
        return display + notes


