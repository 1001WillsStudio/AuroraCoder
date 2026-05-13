"""
Native main flow using OpenAI chat completions API with native tool calling.

This replaces the previous custom XML-based tool system with standard OpenAI function calling.
Supports multiple model providers that can be switched at runtime.
"""

import json
import datetime

import re
from typing import Dict, List, Any, Generator, Set, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .tool_definitions import get_tool_definitions, execute_tool_call, PARALLEL_SAFE_TOOLS
from .config import (
    TRAINING_DATA_DIR, DEFAULT_PROVIDER,
    MAX_TOKENS, MAX_ITERATIONS, CONTINUE_ITERATIONS,
    MAX_STREAMING_RETRIES, MAX_TOOL_CONCURRENCY,
    SYSTEM_MESSAGE_TEMPLATE, VNC_INSTRUCTIONS, TERMINAL_ENV_NOTE,
    INTERPRETER_WARN_CHARS, INTERPRETER_MAX_FILES,
    CONTEXT_WINDOW_TOKENS, CONTEXT_WARN_THRESHOLD,
    _CONTINUATION_NOTICE_MARKER, CONTINUATION_NOTICE,
)
from .providers import provider_manager
from .code_tools.code_interpreter import (
    code_interpreter, 
    CODE_INTERPRETER_START, 
    CODE_INTERPRETER_END
)
from .code_sandbox import WORKSPACE


# --- Code Interpreter Management ---

# Tools that trigger code interpreter display
CODE_RELATED_TOOLS = {'read_file', 'write_file', 'edit_file'}

# Tools that remove files from the interpreter
FILE_REMOVAL_TOOLS = {'delete_file', 'close_file'}


def discover_open_files(messages: List[Dict]) -> Set[str]:
    """
    Scan message history to discover all files that should be displayed
    in the code interpreter.
    
    Files are added when read_file, write_file, or edit_file is called.
    Files are removed when delete_file is called.
    
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

    notes = "\n\nNote: Closing a file removes it from this display, including previous tool responses — you will no longer see its contents unless you open it again. Only close a file after you have fully extracted all information you need from it."

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


# --- Tool Concurrency ---

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


def record_api_call(request_messages: list, response_message: dict):
    """Append one request→response pair to today's training log."""
    try:
        TRAINING_DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = TRAINING_DATA_DIR / f"{datetime.datetime.now():%Y-%m-%d}.jsonl"
        entry = {
            "request": request_messages,
            "response": response_message,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# --- Context Continuation Helpers ---

def _filter_tools_by_context(tools: list, prompt_tokens: int, context_window: int) -> list:
    """
    Hide continue_as_new_chat from the tool list until context usage
    crosses CONTEXT_WARN_THRESHOLD.  Keeps the model focused and avoids
    wasting a tool slot when context is still plentiful.
    """
    if context_window and prompt_tokens / context_window < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools


def _has_continuation_notice_been_shown(messages: List[Dict]) -> bool:
    """Check whether the continuation notice has already been injected."""
    for msg in messages:
        if msg.get("role") == "system" and _CONTINUATION_NOTICE_MARKER in msg.get("content", ""):
            return True
    return False


# --- Main Chat Flow ---

def generate_chat_responses_stream_native(
    messages: list,
    max_iterations: int = MAX_ITERATIONS,
    provider_id: Optional[str] = None,
    tools_override: Optional[List[Dict]] = None,
) -> Generator[dict, None, None]:
    """
    Handles chat interaction using native OpenAI tool calling with thinking/reasoning support.
    
    Args:
        messages (list): List of OpenAI message dicts in chat format.
        max_iterations (int): Maximum number of iterations before stopping. Default is 30.
        provider_id (str, optional): The provider to use. Defaults to DEFAULT_PROVIDER.
        tools_override (list, optional): If provided, use these tool definitions instead
            of the default set. Used by subagents to run with a filtered tool set.

    Yields:
        dict: Contains 'messages' (updated message list), 'status', and 'provider' info
    """
    # Use default provider if not specified
    if provider_id is None:
        provider_id = DEFAULT_PROVIDER
    
    # Get client and config for the selected provider
    client = provider_manager.get_client(provider_id)
    config = provider_manager.get_config(provider_id)
    model_name = config["model"]
    extra_body = config.get("extra_body")
    
    # Strip old code interpreter blocks from tool messages before copying
    clean_previous_interpreter_blocks(messages)
    
    current_processing_messages = list(messages)
    
    # Get tool definitions (or use override for subagents / force_continuation)
    tools = tools_override if tools_override is not None else get_tool_definitions()
    filter_continuation = tools_override is None  # only filter the default set
    
    # Per-provider context window (falls back to global default)
    context_window = config.get("context_window", CONTEXT_WINDOW_TOKENS)
    
    system_message = SYSTEM_MESSAGE_TEMPLATE.format(
        current_time=datetime.datetime.now().isoformat(),
        vnc_instructions=VNC_INSTRUCTIONS,
        terminal_env_note=TERMINAL_ENV_NOTE,
    )
    
    # Add system message if not already present.
    if not current_processing_messages or current_processing_messages[0].get("role") != "system":
        current_processing_messages.insert(0, {"role": "system", "content": system_message})
    # System message already present -- leave immutable
    
    iteration_count = 0
    streaming_errors = 0
    prompt_tokens = 0  # Track accumulated prompt tokens across iterations
    
    while iteration_count < max_iterations:
        iteration_count += 1
        
        # Filter continuation tool out of the default tool set until context
        # is high enough.  Skipped when tools_override is set (e.g. subagent
        # tool set, or force_continuation from the UI).
        tools_for_iteration = (
            _filter_tools_by_context(tools, prompt_tokens, context_window)
            if filter_continuation else tools
        )
        
        api_kwargs = {
            "model": model_name,
            "messages": current_processing_messages,
            "tools": tools_for_iteration,
            "tool_choice": "auto",
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        
        # Add extra_body if provider requires it (e.g., NVIDIA thinking mode)
        if extra_body:
            api_kwargs["extra_body"] = extra_body
        
        # Create chat completion with tools
        completion_stream = client.chat.completions.create(**api_kwargs)
        
        current_content = ""
        current_reasoning = ""
        assistant_message = {"role": "assistant"}
        current_tool_calls = []
        current_usage = None
        
        try:
            for chunk in completion_stream:
                if not chunk.choices:
                    # Usage info may come on a chunk with no choices
                    if hasattr(chunk, "usage") and chunk.usage:
                        current_usage = chunk.usage.model_dump()
                    continue
                    
                delta = chunk.choices[0].delta
                # Handle reasoning content
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    current_reasoning += delta.reasoning_content

                if delta.content:
                    current_content += delta.content

                # Handle tool calls
                if delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        # Use the index from the API response, not enumerate
                        idx = tool_call_delta.index if tool_call_delta.index is not None else 0
                        # Ensure we have enough tool calls in our list
                        while len(current_tool_calls) <= idx:
                            current_tool_calls.append({
                                "id": "",
                                "type": "function", 
                                "function": {"name": "", "arguments": ""}
                            })
                        
                        current_tool_call = current_tool_calls[idx]
                        
                        if tool_call_delta.id:
                            current_tool_call["id"] = tool_call_delta.id
                        
                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                current_tool_call["function"]["name"] += tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                current_tool_call["function"]["arguments"] += tool_call_delta.function.arguments

                # Capture usage from the final chunk
                if hasattr(chunk, "usage") and chunk.usage:
                    current_usage = chunk.usage.model_dump()

                assistant_message["thinking"] = current_reasoning
                assistant_message["reasoning_content"] = current_reasoning
                assistant_message["content"] = current_content
                if current_tool_calls:
                    assistant_message["tool_calls"] = current_tool_calls
                yield {
                    "messages": current_processing_messages + [assistant_message],
                    "status": "running",
                    "provider": provider_id
                }
        
        except Exception as e:
            streaming_errors += 1
            print(f"Streaming error ({streaming_errors}/{MAX_STREAMING_RETRIES}): {e}")
            if streaming_errors >= MAX_STREAMING_RETRIES:
                yield {
                    "messages": current_processing_messages,
                    "status": "error",
                    "error": f"Streaming failed after {MAX_STREAMING_RETRIES} retries: {e}",
                    "provider": provider_id
                }
                return
            continue

        streaming_errors = 0

        # Update prompt_tokens from the usage response
        if current_usage:
            prompt_tokens = current_usage.get("prompt_tokens", prompt_tokens)

        record_api_call(current_processing_messages, assistant_message)

        current_tool_calls = [
            tc for tc in current_tool_calls 
            if tc["function"]["name"]
        ]
        
        if current_tool_calls:
            formatted_tool_calls = []
            for tc in current_tool_calls:
                formatted_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"]
                    }
                })
            assistant_message["tool_calls"] = formatted_tool_calls
        
        # If no tool calls, we're done (or retry if empty)
        if not current_tool_calls:
            if not current_content:
                current_processing_messages.append({
                    "role": "system",
                    "content": """This message only appears when you made this mistake in previous (removed) responses.
                    You did not provide any tool call or reply last time."""
                })
                continue
            else:
                current_processing_messages.append(assistant_message)
                yield {
                    "messages": current_processing_messages,
                    "status": "completed",
                    "provider": provider_id
                }
                return

        # Add tool call requests to messages
        current_processing_messages.append(assistant_message)
        
        # Inject continuation notice once when context threshold is crossed
        if context_window and prompt_tokens / context_window >= CONTEXT_WARN_THRESHOLD:
            if not _has_continuation_notice_been_shown(current_processing_messages):
                current_processing_messages[0]["content"] += (
                    "\n\n" + _CONTINUATION_NOTICE_MARKER + "\n" + CONTINUATION_NOTICE
                )
        
        # Track if any code-related tool was called in this batch
        code_tool_called = False
        
        # Execute tool calls — concurrent-safe tools run in parallel
        for is_safe, batch in partition_tool_calls(current_tool_calls):
            if is_safe and len(batch) > 1:
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
                    current_processing_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_out["id"],
                        "content": result
                    })
            else:
                for tc in batch:
                    tc_out, tool_name, result = _execute_single_tool(tc)
                    if should_trigger_code_interpreter(tool_name):
                        code_tool_called = True
                    current_processing_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_out["id"],
                        "content": result
                    })

        # If continue_as_new_chat was called, end the loop immediately
        if any(tc["function"]["name"] == "continue_as_new_chat" for tc in current_tool_calls):
            yield {
                "messages": current_processing_messages,
                "status": "completed",
                "provider": provider_id
            }
            return

        # If any code-related tool was called, update the interpreter display
        if code_tool_called:
            # Clean previous interpreter blocks from all tool messages
            clean_previous_interpreter_blocks(current_processing_messages)
            
            # Generate consolidated interpreter display for all open files
            interpreter_display = generate_consolidated_interpreter_display(current_processing_messages)
            
            # Append interpreter to the last tool response
            if interpreter_display and current_processing_messages:
                # Find the last tool message and append interpreter
                for i in range(len(current_processing_messages) - 1, -1, -1):
                    if current_processing_messages[i].get("role") == "tool":
                        current_processing_messages[i]["content"] += "\n\n" + interpreter_display
                        break

        yield {
            "messages": current_processing_messages,
            "status": "running",
            "provider": provider_id
        }
    
    # Max iterations reached - yield special status so UI can show Continue button
    yield {
        "messages": current_processing_messages,
        "status": "max_iterations_reached",
        "provider": provider_id
    }


# Main function for the system
generate_chat_responses_stream = generate_chat_responses_stream_native
