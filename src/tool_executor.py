"""
Tool execution engine.

Handles tool call partitioning, parallel/sequential execution, and
same-turn edit guards.  Separated from main_flow.py to keep the agent
loop focused.
"""

import json
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .tool_definitions import execute_tool_call, PARALLEL_SAFE_TOOLS
from .code_tools.file_operations import maybe_truncate_edits, apply_self_correction
from .code_tools.context_manager import should_trigger_code_interpreter
from .config import MAX_TOOL_CONCURRENCY


# Shared executor for parallel read-only tool execution.
_tool_executor = ThreadPoolExecutor(max_workers=MAX_TOOL_CONCURRENCY)


def partition_tool_calls(tool_calls: List[Dict]) -> List[Tuple[bool, List[Dict]]]:
    """
    Group consecutive tool calls by concurrency safety.

    Returns a list of (is_safe, [tool_call, ...]) batches.
    Consecutive safe tools are grouped together for parallel execution;
    unsafe tools are kept in their own sequential batches.
    """
    if not tool_calls:
        return []

    batches: List[Tuple[bool, List[Dict]]] = []
    current_safe: Optional[bool] = None
    current_batch: List[Dict] = []

    for tc in tool_calls:
        is_safe = tc["function"]["name"] in PARALLEL_SAFE_TOOLS
        if current_safe is not None and is_safe != current_safe:
            batches.append((current_safe, current_batch))
            current_batch = []
        current_safe = is_safe
        current_batch.append(tc)

    if current_batch and current_safe is not None:
        batches.append((current_safe, current_batch))

    return batches


def _execute_single_tool(tool_call: Dict) -> Tuple[Dict, str, str]:
    """Execute one tool call and return (tool_call, tool_name, result)."""
    tool_name = tool_call["function"]["name"]
    try:
        arguments = json.loads(tool_call["function"]["arguments"])
    except json.JSONDecodeError as e:
        return (tool_call, tool_name, f"Error: could not parse tool arguments — {e}")
    try:
        result = execute_tool_call(tool_name, arguments)
    except Exception as e:
        result = f"Error executing tool '{tool_name}': {type(e).__name__}: {e}"
    return (tool_call, tool_name, result)


def _check_same_file_edit_guard(
    tool_call: Dict,
    files_edited_this_turn: set,
) -> Optional[str]:
    """
    If this tool call is an edit_file targeting a file already edited in this
    turn, return an error message.  Otherwise return None and register the file.

    The agent cannot see the updated code interpreter between tool calls in
    the same turn, so a second edit to the same file would use stale line
    numbers.  Force it to wait for the next turn.
    """
    tool_name = tool_call["function"]["name"]
    if tool_name != "edit_file":
        return None
    try:
        args = json.loads(tool_call["function"]["arguments"])
    except (json.JSONDecodeError, TypeError):
        return None
    target = args.get("target_file")
    if not target:
        return None
    if target in files_edited_this_turn:
        return (f"Error: '{target}' was already edited earlier in this turn. "
                f"You cannot edit the same file twice in one turn because the "
                f"code interpreter has not refreshed yet — your line numbers "
                f"are stale. Wait for the next turn and use the updated code "
                f"interpreter display for correct line numbers.")
    files_edited_this_turn.add(target)
    return None


def execute_tool_calls(
    current_tool_calls: List[Dict],
    messages: List[Dict],
) -> bool:
    """
    Execute tool calls, running concurrent-safe tools in parallel.
    Appends tool response messages to `messages` in place.

    Returns:
        True if any code-related tool was called
    """
    code_tool_called = False
    files_edited_this_turn: set = set()

    for is_safe, batch in partition_tool_calls(current_tool_calls):
        if is_safe and len(batch) > 1:
            for tc in batch:
                maybe_truncate_edits(tc)
            futures = {
                _tool_executor.submit(_execute_single_tool, tc): tc
                for tc in batch
            }
            # Collect results keyed by tool_call id to preserve original order
            results_by_id = {}
            for future in as_completed(futures):
                tc, tool_name, result = future.result()
                results_by_id[tc["id"]] = (tc, tool_name, result)
            # Append in original batch order
            for tc in batch:
                tc_out, tool_name, result = results_by_id[tc["id"]]
                if should_trigger_code_interpreter(tool_name):
                    code_tool_called = True
                result = apply_self_correction(tc_out, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_out["id"],
                    "content": result
                })
        else:
            for tc in batch:
                guard_err = _check_same_file_edit_guard(tc, files_edited_this_turn)
                if guard_err:
                    tool_name = tc["function"]["name"]
                    if should_trigger_code_interpreter(tool_name):
                        code_tool_called = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": guard_err
                    })
                    continue
                maybe_truncate_edits(tc)
                tc_out, tool_name, result = _execute_single_tool(tc)
                if should_trigger_code_interpreter(tool_name):
                    code_tool_called = True
                result = apply_self_correction(tc_out, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_out["id"],
                    "content": result
                })

    return code_tool_called
