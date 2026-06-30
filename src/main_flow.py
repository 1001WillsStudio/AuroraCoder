"""
Native main flow using OpenAI chat completions API with native tool calling.

This is the primary agent loop: messages in → streamed responses out.
Tool execution is delegated to tool_executor.py; code-interpreter display
management is delegated to code_tools/code_interpreter_panel.py.
"""

import time
import datetime
from pathlib import Path
import logging
from typing import Dict, List, Generator, Optional

from .tool_definitions import get_tool_definitions
from .core_tools.tool_store_client import get_toolstore_tools_prompt
from .config import (
    DEFAULT_PROVIDER,
    MAX_TOKENS, MAX_ITERATIONS,
    MAX_STREAMING_RETRIES,
    SYSTEM_MESSAGE_TEMPLATE, VNC_INSTRUCTIONS, TERMINAL_ENV_NOTE,
    CONTEXT_WINDOW_TOKENS, CONTEXT_WARN_THRESHOLD,
    _CONTINUATION_NOTICE_MARKER, CONTINUATION_NOTICE,
)
from .providers import provider_manager
from .core_tools.tool_store_client import prefetch_primary_tools  # noqa: E402 — redefined in function body
from .code_tools.panel_manager import get_all as get_panels
from .code_tools.panel_manager import register
from .code_tools.code_interpreter_panel import CodeInterpreterPanel
from .code_tools.tool_store_panel import ToolStorePanel

# ── Register all Living Tool State panels ──────────────────────────
register(CodeInterpreterPanel())
register(ToolStorePanel())
from .tool_executor import execute_tool_calls
from .training_log import record_api_call, load_save_training_flag

_main_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Timing Helper
# ---------------------------------------------------------------------------

def _log_api_timing(iteration_count: int, model_name: str,
                    t_api_start: float, t_first_chunk: float,
                    t_first_content: float | None):
    """Log per‑API‑call latency (only when INFO logging is enabled)."""
    if not _main_logger.isEnabledFor(logging.INFO):
        return
    t_now = time.time()
    api_latency = t_first_chunk - t_api_start
    content_latency = (t_first_content - t_first_chunk) if t_first_content else None
    _main_logger.info(
        "[main_flow] iter=%d model=%s api_ttfb=%.0fms first_content=%s total=%.0fms",
        iteration_count, model_name,
        api_latency * 1000,
        ("%.0fms" % (content_latency * 1000)) if t_first_content else "N/A",
        (t_now - t_api_start) * 1000,
    )


# ---------------------------------------------------------------------------
#  Context Continuation Helpers
# ---------------------------------------------------------------------------

def _filter_tools_by_context(tools: list, total_tokens: int, context_window: int) -> list:
    """
    Hide continue_as_new_chat from the tool list until context usage
    crosses CONTEXT_WARN_THRESHOLD.  Keeps the model focused and avoids
    wasting a tool slot when context is still plentiful.
    """
    if context_window and total_tokens / context_window < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools


def _has_continuation_notice_been_shown(messages: List[Dict]) -> bool:
    """Check whether the continuation notice has already been injected."""
    for msg in messages:
        if msg.get("role") == "system" and _CONTINUATION_NOTICE_MARKER in msg.get("content", ""):
            return True
    return False


# ---------------------------------------------------------------------------
#  Primary Agent Loop
# ---------------------------------------------------------------------------

def generate_chat_responses_stream_native(
    messages: list,
    max_iterations: int = MAX_ITERATIONS,
    provider_id: Optional[str] = None,
    tools_override: Optional[List[Dict]] = None,
    conversation_id: str | None = None,
    workspace_tree: str = "",
) -> Generator[dict, None, None]:
    """
    Handles chat interaction using native OpenAI tool calling with thinking/reasoning support.
    
    Args:
        messages (list): List of OpenAI message dicts in chat format.
        max_iterations (int): Maximum number of iterations before stopping.
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
    
    
    # Get tool definitions (or use override for subagents / force_continuation)
    tools = tools_override if tools_override is not None else get_tool_definitions()
    filter_continuation = tools_override is None  # only filter the default set
    
    # Per-provider context window (falls back to global default)
    context_window = config.get("context_window", CONTEXT_WINDOW_TOKENS)
    
    system_message = SYSTEM_MESSAGE_TEMPLATE.format(
        current_time=datetime.datetime.now().isoformat(),
        vnc_instructions=VNC_INSTRUCTIONS,
        terminal_env_note=TERMINAL_ENV_NOTE,
        toolstore_tools=get_toolstore_tools_prompt(),
        workspace_tree=workspace_tree,
    )

    # Eagerly load primary tool schemas once at startup so the LLM's
    # tools[] array includes them from the very first turn.
    prefetch_primary_tools()
    
    # Add system message if not already present.
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": system_message})
    
    iteration_count = 0
    total_tokens = 0
    streaming_errors = 0

    # Read the user's training-data preference *once* — it cannot change mid-request.
    save_training = load_save_training_flag()

    while iteration_count < max_iterations:
        iteration_count += 1
        
        # Filter continuation tool out of the default tool set until context
        # is high enough.  Skipped when tools_override is set.
        tools_for_iteration = (
            _filter_tools_by_context(tools, total_tokens, context_window)
            if filter_continuation else tools
        )
        
        api_kwargs = {
            "model": model_name,
            "messages": messages,
            "tools": tools_for_iteration,
            "tool_choice": "auto",
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        
        # Add extra_body if provider requires it (e.g., NVIDIA thinking mode)
        if extra_body:
            api_kwargs["extra_body"] = extra_body
        
        # ── Call the LLM ────────────────────────────────────────────────
        assistant_message = {"role": "assistant", "content": "", "reasoning_content": ""}
        current_content = ""
        current_reasoning = ""
        current_tool_calls = []
        current_usage = None

        t_api_start = time.time()
        completion_stream = client.chat.completions.create(**api_kwargs)

        yield {
            "messages": messages + [assistant_message],
            "status": "running",
            "provider": provider_id,
            "llm_delta": {
                "content": "",
                "reasoning_content": "",
            },
        }

        try:
            t_first_chunk = time.time()
            t_first_content = None
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
                    if t_first_content is None:
                        t_first_content = time.time()
                    current_content += delta.content

                # Handle tool calls
                if delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        idx = tool_call_delta.index if tool_call_delta.index is not None else 0
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

                # Yield streaming updates so the frontend can render in real-time
                assistant_message["thinking"] = current_reasoning
                assistant_message["reasoning_content"] = current_reasoning
                assistant_message["content"] = current_content
                if current_tool_calls:
                    assistant_message["tool_calls"] = current_tool_calls

                llm_delta = {
                    "content": delta.content or "",
                    "reasoning_content": getattr(delta, "reasoning_content", "") or "",
                }
                if current_tool_calls:
                    llm_delta["tool_calls"] = [
                        {
                            "id": tc.get("id", ""),
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        }
                        for tc in current_tool_calls
                    ]
                yield {
                    "messages": messages + [assistant_message],
                    "status": "running",
                    "provider": provider_id,
                    "llm_delta": llm_delta,
                }

            # Log timing for this API call
            _log_api_timing(iteration_count, model_name,
                            t_api_start, t_first_chunk, t_first_content)
        
        except Exception as e:
            # Retry the *same* iteration — transient stream errors
            # shouldn't burn an iteration slot.
            streaming_errors += 1
            _main_logger.warning("Streaming error (%s/%s): %s", streaming_errors, MAX_STREAMING_RETRIES, e)
            if streaming_errors >= MAX_STREAMING_RETRIES:
                yield {
                    "messages": messages,
                    "status": "error",
                    "error": f"Streaming failed after {MAX_STREAMING_RETRIES} retries: {e}",
                    "provider": provider_id
                }
                return
            continue

        streaming_errors = 0

        if current_usage:
            total_tokens = current_usage.get("total_tokens", 0)

        record_api_call(messages, assistant_message, enabled=save_training)

        # ── Process tool calls ──────────────────────────────────────────
        current_tool_calls = [
            tc for tc in current_tool_calls 
            if tc["function"]["name"]
        ]
        
        if current_tool_calls:
            assistant_message["tool_calls"] = current_tool_calls
        
        # If no tool calls, we're done (or retry if also no content)
        if not current_tool_calls:
            if not current_content:
                messages.append({
                    "role": "system",
                    "content": """This message only appears when you made this mistake in previous (removed) responses.
                    You did not provide any tool call or reply last time."""
                })
                continue
            else:
                messages.append(assistant_message)
                yield {
                    "messages": messages,
                    "status": "completed",
                    "provider": provider_id
                }
                return

        # Add tool call requests to messages
        messages.append(assistant_message)

        # Yield full snapshot BEFORE execution so the frontend shows tool
        # calls with their details (command, args, etc.) while the tool runs.
        yield {
            "messages": messages,
            "status": "running",
            "provider": provider_id
        }

        # Delegate to the tool execution engine
        triggered_trackers = execute_tool_calls(current_tool_calls, messages, conversation_id=conversation_id)

        # Warn once when estimated context nears the model's window.
        if context_window and current_usage and not _has_continuation_notice_been_shown(messages):
            if total_tokens / context_window >= CONTEXT_WARN_THRESHOLD:
                messages.append({
                    "role": "system",
                    "content": _CONTINUATION_NOTICE_MARKER + "\n" + CONTINUATION_NOTICE
                })

        # If continue_as_new_chat was called, end the loop immediately
        if any(tc["function"]["name"] == "continue_as_new_chat" for tc in current_tool_calls):
            yield {
                "messages": messages,
                "status": "completed",
                "provider": provider_id
            }
            return

        # Refresh panels — append display to the triggering tool's message
        for panel in get_panels():
            idx = triggered_trackers.get(panel.name)
            if idx is not None:
                panel.refresh(messages, at_index=idx)

        yield {
            "messages": messages,
            "status": "running",
            "provider": provider_id
        }
    
    # Max iterations reached — yield special status so UI can show Continue button
    yield {
        "messages": messages,
        "status": "max_iterations_reached",
        "provider": provider_id
    }


