"""
Native main flow using OpenAI chat completions API with native tool calling.

This is the primary agent loop: messages in → streamed responses out.
Tool execution is delegated to tool_executor.py; code-interpreter display
management is delegated to code_tools/code_interpreter_panel.py.
"""

import time
import datetime
import json
from pathlib import Path
import logging
from typing import Callable, Dict, List, Generator, Optional

from httpx import TransportError
from openai import APIConnectionError

try:
    from httpx2 import TransportError as SDKTransportError
except ImportError:
    SDKTransportError = TransportError

from .tool_evidence import (
    READ_RESULT_SCHEMA, compact_provider_view, estimated_tokens, has_evidence, load_mode,
)
from .tool_definitions import get_tool_definitions
from .core_tools.conversation_history import reset_turn_token, set_turn_token
from .core_tools.tool_store_client import get_toolstore_tools_prompt
from .config import (
    DEFAULT_PROVIDER, PROVIDER_DEFAULT_MODELS,
    MAX_TOKENS, MAX_ITERATIONS,
    MAX_STREAMING_RETRIES,
    SYSTEM_MESSAGE_TEMPLATE, DISPLAY_GUIDE, TERMINAL_ENV_NOTE,
    CONTEXT_WINDOW_TOKENS, CONTEXT_WARN_THRESHOLD,
    CONTINUATION_TOOL_RESULT,
    _CONTINUATION_NOTICE_MARKER, CONTINUATION_NOTICE,
)
from .providers import messages_for_provider, provider_manager
from .provider_diagnostics import record_provider_failure
from .core_tools.tool_store_client import prefetch_primary_tools  # noqa: E402 — redefined in function body
from .code_tools.panel_manager import get_all as get_panels
from .code_tools.panel_manager import register
from .code_tools.code_interpreter_panel import CodeInterpreterPanel
from .code_tools.tool_store_panel import ToolStorePanel
from .code_tools.memory_panel import MemoryPanel
from .core_tools import memory_client
from .task_checklist import AgentTaskTools
from .provider_context_errors import is_context_limit_error

# ── Register all Living Tool State panels ──────────────────────────
register(CodeInterpreterPanel())
register(ToolStorePanel())
register(MemoryPanel())
from .tool_executor import execute_tool_calls
from .training_log import record_api_call, load_save_training_flag

_main_logger = logging.getLogger(__name__)


class IncompleteModelStream(RuntimeError):
    """The provider ended a stream before producing a usable response."""


def _validate_tool_calls(tool_calls: list[dict], finish_reason: str | None) -> None:
    if finish_reason not in {"stop", "tool_calls", "function_call"}:
        raise IncompleteModelStream("The response ended before the tool calls completed")
    seen = set()
    for call in tool_calls:
        call_id = call["id"]
        function = call["function"]
        if not call_id or call_id in seen or not function["name"]:
            raise IncompleteModelStream("A streamed tool call had a missing or duplicate identifier")
        seen.add(call_id)
        try:
            arguments = json.loads(function["arguments"])
        except (TypeError, ValueError):
            raise IncompleteModelStream("A streamed tool call had incomplete arguments") from None
        if not isinstance(arguments, dict):
            raise IncompleteModelStream("Streamed tool arguments must be a JSON object")


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
    model: Optional[str] = None,
    tools_override: Optional[List[Dict]] = None,
    conversation_id: str | None = None,
    workspace_tree: str = "",
    stop_requested: Callable[[], bool] | None = None,
    task_tools: AgentTaskTools | None = None,
    turn_token: str | None = None,
) -> Generator[dict, None, None]:
    """Bind the gateway history credential to one model run and its tools."""
    token = set_turn_token(turn_token)
    try:
        yield from _generate_chat_responses(
            messages, max_iterations, provider_id, model, tools_override,
            conversation_id, workspace_tree, stop_requested, task_tools,
        )
    finally:
        reset_turn_token(token)


def _generate_chat_responses(
    messages: list,
    max_iterations: int = MAX_ITERATIONS,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
    tools_override: Optional[List[Dict]] = None,
    conversation_id: str | None = None,
    workspace_tree: str = "",
    stop_requested: Callable[[], bool] | None = None,
    task_tools: AgentTaskTools | None = None,
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
    def stopped() -> bool:
        return stop_requested is not None and stop_requested()

    if stopped():
        return

    # Use default provider if not specified
    if provider_id is None:
        provider_id = DEFAULT_PROVIDER
    

    # Get client and config for the resolved provider
    client = provider_manager.get_client(provider_id)
    config = provider_manager.get_config(provider_id)

    # Model name: explicit param → config → PROVIDER_DEFAULT_MODELS
    model_name = model or config.get("model") or ""
    if not model_name:
        defaults = PROVIDER_DEFAULT_MODELS.get(provider_id, [])
        model_name = defaults[0]["id"] if defaults else "deepseek-chat"

    extra_body = config.get("extra_body")

    # Context window & max output — use the real per-model limits captured
    # from the provider's /models endpoint (stored in settings.provider_models
    # when the model was enabled in Settings), falling back to the constants
    # in config.py when the API didn't report them.
    _ctx, _max_out = provider_manager.get_model_limits(provider_id, model_name)
    context_window = _ctx or CONTEXT_WINDOW_TOKENS
    api_max_tokens = _max_out or MAX_TOKENS
    
    # Eagerly load primary tool schemas once at startup so the LLM's
    # tools[] array includes them from the very first turn.
    prefetch_primary_tools()

    # Resolve tool definitions — use override if provided, else defaults
    tools = get_tool_definitions() if tools_override is None else tools_override
    filter_continuation = tools_override is None  # only filter for default tools
    restricted_tool_names = (
        None if tools_override is None else {tool["function"]["name"] for tool in tools_override}
    )

    # Add system message if not already present.
    if not messages or messages[0].get("role") != "system":
        # Memory is opt-in (settings.other.memory.enabled, default False).
        # When disabled, skip the stance fetch entirely (no gateway round
        # trip) and drop the whole memory section from the prompt — with
        # the remember/recall/log_gap tool schemas also filtered out
        # (tool_definitions.memory_filter_tools), a disabled agent sees no
        # mention of memory anywhere and behaves like a build with no
        # memory module at all.
        memory_section = ""
        if memory_client.memory_enabled():
            # Stance is fetched once here, at session start — NOT per turn.
            # Subsequent turns reuse the already-inserted system message, so
            # this network call only ever happens on the first turn of a
            # conversation (fails open to "" on any error — see memory_client).
            memory_stance = memory_client.get_stance()
            memory_section = (
                "- **Memory**: you have `remember`/`recall` tools for durable facts that persist "
                "across sessions. Use `remember` sparingly — only for facts that are NOT derivable "
                "from the code/git and would concretely change future behavior (preferences, "
                "corrections, project context, external-system pointers, non-obvious conventions). "
                "Silence is the correct default; most turns should not call it. If the user says a "
                "remembered fact is wrong, `recall` it to get its id, then use `forget` to delete it "
                "(or `remember` with `memory_id` set, if it should be replaced rather than removed).\n"
                f"{memory_stance}\n"
            )
        system_message = SYSTEM_MESSAGE_TEMPLATE.format(
            current_time=datetime.datetime.now().isoformat(),
            display_guide=DISPLAY_GUIDE,
            terminal_env_note=TERMINAL_ENV_NOTE,
            toolstore_tools=get_toolstore_tools_prompt(
                context={
                    "conversation_id": conversation_id or "",
                    "workspace_root": str(Path.cwd()),
                }
            ),
            workspace_tree=workspace_tree,
            memory_section=memory_section,
        )
        messages.insert(0, {"role": "system", "content": system_message})
    
    iteration_count = 0
    total_tokens = 0
    streaming_errors = 0

    # Read the user's training-data preference *once* — it cannot change mid-request.
    save_training = load_save_training_flag()
    retrying_empty_reply = False
    compaction_mode = load_mode() if conversation_id and tools_override is None else "off"

    while iteration_count < max_iterations:
        if stopped():
            return
        iteration_count += 1
        
        # Filter continuation tool out of the default tool set until context
        # is high enough.  Skipped when tools_override is set.
        api_messages = messages_for_provider(messages)
        compact_provider_view(
            messages, api_messages, compaction_mode, context_window, api_max_tokens, known_window=bool(_ctx),
        )
        input_budget = max(1, context_window - api_max_tokens)
        tools_for_iteration = (
            _filter_tools_by_context(
                tools, estimated_tokens(api_messages) if compaction_mode != "off" else total_tokens,
                input_budget if compaction_mode != "off" else context_window,
            )
            if filter_continuation else tools
        )
        if tools_override is None and has_evidence(messages):
            tools_for_iteration = [*tools_for_iteration, READ_RESULT_SCHEMA]
        if task_tools and tools_override is None:
            tools_for_iteration = [*tools_for_iteration, *task_tools.schemas()]
            system = api_messages[0]
            task_context = task_tools.prompt()
            api_messages = [{**system, "content": f"{system['content']}\n\n{task_context}"}, *api_messages[1:]]

        if retrying_empty_reply:
            api_messages = [*api_messages, {
                "role": "system",
                "content": "The previous response was empty. Reply to the user or call the next appropriate tool.",
            }]
        api_kwargs = {
            "model": model_name,
            "messages": api_messages,
            "max_tokens": api_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools_for_iteration:
            api_kwargs.update(tools=tools_for_iteration, tool_choice="auto")
        
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
        try:
            completion_stream = client.chat.completions.create(**api_kwargs)
        except Exception as exc:
            if task_tools and task_tools.active and is_context_limit_error(exc):
                _main_logger.info("Provider rejected the current context for task recovery (%s/%s)", provider_id, model_name)
                yield {"messages": messages, "status": "context_limit_reached", "provider": provider_id}
                return
            record_provider_failure(exc, provider_id, model_name, conversation_id, api_kwargs)
            raise

        finish_reason = None
        try:
            if stopped():
                return
            yield {
                "messages": messages + [assistant_message],
                "status": "running",
                "provider": provider_id,
            }

            t_first_chunk = time.time()
            t_first_content = None
            for chunk in completion_stream:
                if stopped():
                    return
                if not chunk.choices:
                    # Usage info may come on a chunk with no choices
                    if hasattr(chunk, "usage") and chunk.usage:
                        current_usage = chunk.usage.model_dump()
                    continue
                    
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
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
                        if idx < 0:
                            raise IncompleteModelStream("A streamed tool call had an invalid index")
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

            if stopped():
                return
            if finish_reason is None and (current_content.strip() or current_reasoning.strip() or current_tool_calls):
                raise IncompleteModelStream("The model stream ended without a finish reason")
            if current_tool_calls:
                _validate_tool_calls(current_tool_calls, finish_reason)

            # Log timing for this API call
            _log_api_timing(iteration_count, model_name,
                            t_api_start, t_first_chunk, t_first_content)
        
        except (TransportError, SDKTransportError, APIConnectionError, IncompleteModelStream) as exc:
            if stopped():
                return
            if streaming_errors >= MAX_STREAMING_RETRIES:
                error = "Connection lost while reading the model response"
                if isinstance(exc, IncompleteModelStream):
                    error = "The model repeatedly returned an incomplete response"
                yield {
                    "messages": messages,
                    "status": "error",
                    "error": f"{error}: {exc}",
                    "provider": provider_id,
                }
                return
            streaming_errors += 1
            iteration_count -= 1
            _main_logger.warning("Retrying interrupted model stream (%s/%s): %s",
                                 streaming_errors, MAX_STREAMING_RETRIES, exc)
            continue
        finally:
            close = getattr(completion_stream, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    _main_logger.debug("Could not close the model response stream")

        streaming_errors = 0

        if current_usage:
            total_tokens = current_usage.get("total_tokens", 0)

        record_api_call(api_messages, assistant_message, enabled=save_training)

        # A single empty completion is retryable. Never spend the agent's
        # remaining task budget sending the same ineffective request.
        if not current_tool_calls:
            if not current_content.strip():
                if retrying_empty_reply or iteration_count >= max_iterations:
                    raise RuntimeError("The model provider returned no answer or tool call.")
                retrying_empty_reply = True
                continue
            else:
                messages.append(assistant_message)
                if task_tools and task_tools.take_turn_inputs(messages, finishing=True):
                    iteration_count = 0
                    retrying_empty_reply = False
                    yield {"messages": messages, "status": "running", "provider": provider_id}
                    continue
                yield {
                    "messages": messages,
                    "status": "interrupted" if finish_reason == "length" else "completed",
                    "provider": provider_id
                }
                return

        retrying_empty_reply = False

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
        if stopped():
            return
        tool_options = {} if restricted_tool_names is None else {"allowed_tools": restricted_tool_names}
        if stop_requested is not None:
            tool_options["stop_requested"] = stop_requested
        if task_tools and tools_override is None:
            tool_options["task_tool_handler"] = task_tools.call
        triggered_trackers = execute_tool_calls(
            current_tool_calls, messages, conversation_id=conversation_id, **tool_options,
        )
        if stopped():
            yield {"messages": messages, "status": "interrupted", "provider": provider_id}
            return

        # Warn once when estimated context nears the model's window.
        if context_window and current_usage and not _has_continuation_notice_been_shown(messages):
            if total_tokens / context_window >= CONTEXT_WARN_THRESHOLD:
                messages.append({
                    "role": "system",
                    "content": _CONTINUATION_NOTICE_MARKER + "\n" + CONTINUATION_NOTICE
                })

        # If continue_as_new_chat was called, end the loop immediately
        handoff_ids = {
            tc["id"] for tc in current_tool_calls if tc["function"]["name"] == "continue_as_new_chat"
        }
        if any(
            msg.get("tool_call_id") in handoff_ids and msg.get("content") == CONTINUATION_TOOL_RESULT
            for msg in messages[-len(current_tool_calls):]
        ):
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

        if task_tools and task_tools.active and context_window and total_tokens / context_window >= CONTEXT_WARN_THRESHOLD:
            yield {"messages": messages, "status": "context_limit_reached", "provider": provider_id}
            return

        if task_tools and task_tools.take_turn_inputs(messages, finishing=iteration_count >= max_iterations):
            iteration_count = 0

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


