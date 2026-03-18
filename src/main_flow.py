"""
Native main flow using OpenAI chat completions API with native tool calling.

This replaces the previous custom XML-based tool system with standard OpenAI function calling.
Supports multiple model providers that can be switched at runtime.
"""

import json
import datetime
import copy
import re
from typing import Dict, List, Any, Generator, Set, Optional

from .tool_definitions import get_tool_definitions, execute_tool_call
from .config import (
    RECORDING_FILE, DEFAULT_PROVIDER,
    MAX_TOKENS, TEMPERATURE, MAX_ITERATIONS, CONTINUE_ITERATIONS,
    SYSTEM_MESSAGE_TEMPLATE, VNC_INSTRUCTIONS
)
from .providers import provider_manager
from .code_tools.code_interpreter import (
    code_interpreter, 
    CODE_INTERPRETER_START, 
    CODE_INTERPRETER_END
)
from .code_sandbox.session_manager import session_manager


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
    root_path = session_manager.get_session_working_directory()
    code_interpreter.set_root_path(root_path)
    
    return code_interpreter.display_multiple_files(sorted_files)


# --- Conversation Recording ---

def record_conversation_turn(current_messages_list: list):
    """Appends the current turn's latest message to a JSONL file."""
    if not current_messages_list:
        return
    try:
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "message": current_messages_list
        }
        with open(RECORDING_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"Error recording conversation turn: {e}")


# --- Main Chat Flow ---

def generate_chat_responses_stream_native(
    messages: list,
    max_iterations: int = MAX_ITERATIONS,
    provider_id: Optional[str] = None
) -> Generator[dict, None, None]:
    """
    Handles chat interaction using native OpenAI tool calling with thinking/reasoning support.
    
    Args:
        messages (list): List of OpenAI message dicts in chat format.
        max_iterations (int): Maximum number of iterations before stopping. Default is 30.
        provider_id (str, optional): The provider to use. Defaults to DEFAULT_PROVIDER.

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
    
    current_processing_messages = copy.deepcopy(messages)
    
    # Get tool definitions
    tools = get_tool_definitions()
    
    system_message = SYSTEM_MESSAGE_TEMPLATE.format(
        current_time=datetime.datetime.now().isoformat(),
        vnc_instructions=VNC_INSTRUCTIONS,
    )
    
    # Add system message if not already present
    if not current_processing_messages or current_processing_messages[0].get("role") != "system":
        current_processing_messages.insert(0, {"role": "system", "content": system_message})
    else:
        current_processing_messages[0]["content"] = system_message
    
    iteration_count = 0
    
    while iteration_count < max_iterations:
        iteration_count += 1
        
        # Build API call kwargs
        api_kwargs = {
            "model": model_name,
            "messages": current_processing_messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": MAX_TOKENS,
            "stream": True,
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
        
        try:
            for chunk in completion_stream:
                if not chunk.choices:
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
            print(f"Error processing chunk: {e}")
            continue

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
                record_conversation_turn(current_processing_messages)
                return

        # Add tool call requests to messages
        current_processing_messages.append(assistant_message)
        
        # Track if any code-related tool was called in this batch
        code_tool_called = False
        
        # Execute tool calls
        for tool_call in current_tool_calls:
            tool_name = tool_call["function"]["name"]
            try:
                arguments = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                arguments = {}
            
            # Execute the tool
            result = execute_tool_call(tool_name, arguments)
            
            # Check if this is a code-related tool
            if should_trigger_code_interpreter(tool_name):
                code_tool_called = True
            
            # Add tool response message (without interpreter - we'll add it at the end)
            tool_response = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result
            }
            current_processing_messages.append(tool_response)

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
    record_conversation_turn(current_processing_messages)


# Main function for the system
generate_chat_responses_stream = generate_chat_responses_stream_native
