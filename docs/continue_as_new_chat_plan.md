# "Continue as a New Chat" — Feature Design Plan (Simplified)

## Overview

When an agent's context window approaches exhaustion (~80%+ used), the
`continue_as_new_chat` tool **appears in the tool list**. The agent has full autonomy
to decide when to call it. The tool call **itself is the signal** — no JSON payload,
no error handling, no subagent-like streaming. The proxy detects the tool call,
creates the child conversation, and the frontend auto-navigates.

**The `src/` part is minimal**: the tool function is a no-op, `main_flow.py` just
ends the loop when the tool is called. All the real work lives in the proxy.

---

## 1. Architecture & Data Flow

```
Agent calls continue_as_new_chat(summary=..., pending_tasks=..., ...)
        │
        ▼
   main_flow.py
   - Tool function returns "OK" (no-op)
   - Loop detects the tool was called → yields "completed" → returns
        │
        ▼
   Proxy (8081) — _proxy_backend_stream()
   - Scans raw_messages for assistant message with continue_as_new_chat tool_call
   - Extracts arguments (summary, pending_tasks, key_files, important_context)
   - Builds context package
   - Creates child conversation (parent_id = current cid)
   - Seeds messages: context package as system msg + original user task
   - Marks parent status "continued"
   - Annotates all subsequent events with new_conversation_id
        │
        ▼
   Frontend
   - Sees new_conversation_id in SSE events
   - Auto-navigates to new conversation
   - User clicks "Start" → POST /api/chat with pre-seeded messages
```

### SSE Event Flow

```
Backend (8080)                          Proxy (8081)                    Frontend
     │                                      │                              │
     │  messages event (status: running)    │                              │
     │  [assistant msg has                  │                              │
     │   continue_as_new_chat tool_call]    │                              │
     ├─────────────────────────────────────►│  Proxy scans raw_messages,   │
     │                                      │  detects the tool_call →     │
     │                                      │  builds context_package,     │
     │                                      │  creates child convo,        │
     │                                      │  seeds messages              │
     │                                      │                              │
     │                                      │  messages event              │
     │                                      │  + new_conversation_id        │
     │                                      ├─────────────────────────────►│
     │                                      │                              │
     │  done event (status: completed)      │                              │
     ├─────────────────────────────────────►│  done event                  │
     │                                      │  + new_conversation_id        │
     │                                      ├─────────────────────────────►│
     │                                      │                              │
     │                                      │  Frontend auto-navigates     │
     │                                      │  to new conversation          │
```

---

## 2. Context Usage Tracking — Real Token Counts

**No estimation needed.** DeepSeek's API returns per-request token usage during
streaming via `stream_options={"include_usage": True}`. The final chunk in each
stream carries a `usage` object with `prompt_tokens`, `completion_tokens`, etc.
(see `docs/deepseek_token_usage.md` for full details).

In `main_flow.py`, after the chunk loop, capture `chunk.usage` when non-null:

```python
# After the chunk loop (around line 365), capture usage:
current_usage = None
# ... inside the for chunk loop:
if hasattr(chunk, "usage") and chunk.usage:
    current_usage = chunk.usage.model_dump()

# Track accumulated prompt tokens across iterations:
total_prompt_tokens = current_usage["prompt_tokens"] if current_usage else 0
```

Then the context check is simply:

```python
def _filter_tools_by_context(tools: list, prompt_tokens: int) -> list:
    usage_pct = prompt_tokens / CONTEXT_WINDOW_TOKENS
    if usage_pct < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools
```

**This also requires a small prerequisite**: enable `stream_options` in the API
call (add to `api_kwargs` in `main_flow.py`):

```python
api_kwargs = {
    ...
    "stream_options": {"include_usage": True},
}
```

And add `STREAM_OPTIONS = {"include_usage": True}` to `config.py` (or just inline it).

The `prompt_tokens` value is the exact token count sent to the model — no
heuristic, no guessing. It increases each iteration as tool results accumulate.

---

## 3. Dynamic Tool Availability

The `continue_as_new_chat` tool is **filtered out** of the tool list until context
usage crosses `CONTEXT_WARN_THRESHOLD` (80%). When it first appears, a one-liner is
injected into the system message (once only, tracked by a marker).

```python
def _filter_tools_by_context(tools: list, prompt_tokens: int) -> list:
    if prompt_tokens / CONTEXT_WINDOW_TOKENS < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools
```

---

## 4. The `continue_as_new_chat` Tool

### Tool Definition

Same schema as before — `summary` + `pending_tasks` (required), `key_files` +
`important_context` (optional). The tool description explains when and how to use it.

### Tool Implementation (`src/core_tools/continue_chat.py`) — DEAD SIMPLE

```python
"""continue_as_new_chat — the tool call itself is the signal. The proxy handles everything."""

def continue_as_new_chat(
    summary: str,
    pending_tasks: str,
    key_files: list = None,
    important_context: str = "",
) -> str:
    """The proxy detects this tool call and creates the new conversation."""
    return "Continuing in a new chat with fresh context."
```

That's it. No HTTP calls. No JSON signal. No error handling. No threading. The
tool call in the assistant message IS the entire signal.

### Classification

- **Read-only**: Yes (add to `PARALLEL_SAFE_TOOLS` for concurrent execution; also add to `SUBAGENT_READ_ONLY_TOOLS` since it's a pure signal with no side effects)
- **Concurrent-safe**: Yes (no side effects)

---

## 5. `src/` Changes — What's Actually Minimal

### 5.1 `src/config.py` — 4 new constants

```python
# Context continuation
CONTEXT_WINDOW_TOKENS = 128_000
CONTEXT_WARN_THRESHOLD = 0.80

# One-liner notice (injected once when tool first appears)
_CONTINUATION_NOTICE_MARKER = "[CONTEXT CONTINUATION TOOL AVAILABLE]"
CONTINUATION_NOTICE = (
    "⚠️ `continue_as_new_chat` is now available in your tool list — "
    "you are at ~80% context."
)
```

### 5.2 `src/main_flow.py` — 3 changes

#### A) Token usage capture (prerequisite) + tool filtering + notice injection

**Prerequisite — `stream_options`**: Add to the `api_kwargs` dict in `main_flow.py`:
```python
api_kwargs = {
    ...
    "stream_options": {"include_usage": True},
}
```

**Capture `prompt_tokens`**: Inside the chunk loop, grab usage from the final chunk:
```python
# In the for-chunk loop:
if hasattr(chunk, "usage") and chunk.usage:
    current_usage = chunk.usage.model_dump()
```

**Filtering** — now uses real token counts, not estimates:

```python
# New imports
from .config import (
    ...existing...,
    CONTEXT_WINDOW_TOKENS, CONTEXT_WARN_THRESHOLD,
    _CONTINUATION_NOTICE_MARKER, CONTINUATION_NOTICE,
)

# New helper (one-liner):
def _filter_tools_by_context(tools: list, prompt_tokens: int) -> list:
    if prompt_tokens / CONTEXT_WINDOW_TOKENS < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools

# In the loop, after chunk loop captures current_usage:
prompt_tokens = current_usage["prompt_tokens"] if current_usage else 0
tools_for_iteration = _filter_tools_by_context(tools, prompt_tokens)

# After filtering, inject one-liner once:
if prompt_tokens / CONTEXT_WINDOW_TOKENS >= CONTEXT_WARN_THRESHOLD:
    if not _has_continuation_notice_been_shown(current_processing_messages):
        current_processing_messages[0]["content"] += "\n\n" + CONTINUATION_NOTICE
```

#### B) System message preservation

When the incoming system message contains `[CONTINUED FROM PREVIOUS SESSION]`,
preserve it and append the standard system prompt:

```python
# Replace lines 286-289:
if not current_processing_messages or current_processing_messages[0].get("role") != "system":
    current_processing_messages.insert(0, {"role": "system", "content": system_message})
else:
    existing = current_processing_messages[0]["content"]
    if "[CONTINUED FROM PREVIOUS SESSION]" in existing:
        current_processing_messages[0]["content"] = existing + "\n\n---\n\n" + system_message
    else:
        current_processing_messages[0]["content"] = system_message
```

#### C) End loop when `continue_as_new_chat` is called

After all tool results are appended and before the post-execution yield:

```python
# After the tool execution loop (after line 460 in current code),
# check if continue_as_new_chat was called:
if any(tc["function"]["name"] == "continue_as_new_chat" for tc in current_tool_calls):
    yield {
        "messages": current_processing_messages,
        "status": "completed",
        "provider": provider_id
    }
    return
```

**Total change**: ~40 lines added to `main_flow.py`.

### 5.3 `src/core_tools/continue_chat.py` — NEW file (~8 lines)

```python
"""continue_as_new_chat — the tool call itself is the signal."""

def continue_as_new_chat(
    summary: str,
    pending_tasks: str,
    key_files: list = None,
    important_context: str = "",
) -> str:
    return "Continuing in a new chat with fresh context."
```

### 5.4 `src/tool_definitions.py` — Register the tool

- Add tool definition to `NATIVE_TOOL_DEFINITIONS`
- Add `"continue_as_new_chat"` to `PARALLEL_SAFE_TOOLS` and `SUBAGENT_READ_ONLY_TOOLS`
- Add `"continue_as_new_chat": continue_as_new_chat` to `TOOL_FUNCTION_MAP`
- Import: `from .core_tools.continue_chat import continue_as_new_chat`

### 5.5 `src/web_api/app.py` — NO changes

The backend remains completely unaware of continuation.

---

## 6. Proxy Changes (`conversation_history/api.py`) — Where the Magic Happens

### 6.1 Add `new_conversation_id` to `ActiveStream`

```python
@dataclass
class ActiveStream:
    ...
    new_conversation_id: Optional[str] = None   # set when continuation detected
```

### 6.2 Scan for continuation in `_proxy_backend_stream()`

After parsing each SSE event (inside the `for etype, edata in _parse_sse_blocks(...)` loop):

```python
# --- Continuation detection ---
if not stream.new_conversation_id:
    args = _scan_for_continuation(edata.get("raw_messages", []))
    if args:
        new_cid = str(uuid.uuid4())
        context_pkg = _build_context_package(**args)

        store.create_conversation(
            conversation_id=new_cid,
            parent_id=cid,
            conv_type="user_chat_continued",
            title=args.get("summary", "Continued conversation")[:80],
            provider_id=stream.provider,
        )
        # Find the original user message from the parent
        original_task = _find_original_user_task(cid)
        store.save_messages(new_cid, [
            {"role": "system", "content": context_pkg},
            {"role": "user", "content": original_task},
        ])
        store.update_status(cid, "continued")
        stream.new_conversation_id = new_cid
```

### 6.3 Annotate events with `new_conversation_id`

Before putting events into subscriber queues:

```python
if stream.new_conversation_id:
    edata["new_conversation_id"] = stream.new_conversation_id
```

### 6.4 `_scan_for_continuation()` — scan assistant tool_calls, NOT tool results

```python
def _scan_for_continuation(raw_messages: list) -> dict | None:
    """
    Scan assistant messages for a continue_as_new_chat tool call.
    Returns the tool arguments dict, or None.
    """
    for msg in raw_messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("function", {}).get("name") == "continue_as_new_chat":
                try:
                    return json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    return None
    return None
```

### 6.5 `_find_original_user_task()`

```python
def _find_original_user_task(cid: str) -> str:
    """Find the first user message in the parent conversation."""
    try:
        msgs = store.get_messages(cid)
        for msg in msgs:
            if msg.get("role") == "user":
                return msg.get("content", "Continue the previous task.")
    except Exception:
        pass
    return "[Original task — see parent conversation]"
```

### 6.6 `_build_context_package()`

```python
def _build_context_package(
    summary: str,
    pending_tasks: str,
    key_files: list = None,
    important_context: str = "",
) -> str:
    key_files = key_files or []
    package = f"""[CONTINUED FROM PREVIOUS SESSION]
The previous agent session reached its context limit. You are continuing the work.

## Task Summary
{summary}

## Key Files
"""
    if key_files:
        for f in key_files:
            package += f"- {f}\n"
    else:
        package += "(None specified — explore the workspace)\n"

    package += f"""
## Pending Tasks
{pending_tasks}
"""
    if important_context:
        package += f"""
## Important Context
{important_context}
"""
    package += """

You are now the active agent. Pick up from here and continue working toward
completing the user's original request. The workspace files are exactly as
the previous agent left them.
"""
    return package
```

### 6.7 Cancellation cascade

When a parent stream is cancelled, also cancel the child continuation stream:

```python
# In _cancel_active_stream(), add after canceling children:
async with _streams_lock:
    parent = active_streams.get(conversation_id)
    if parent and parent.new_conversation_id:
        continuation = active_streams.get(parent.new_conversation_id)
        if continuation and not continuation.finished:
            continuation.cancel_event.set()
            if continuation.task and not continuation.task.done():
                continuation.task.cancel()
```

---

## 7. Frontend Changes

### 7.1 `App.jsx` — SSE Event Handling

```javascript
// In the SSE event handler:
if (eventData.new_conversation_id) {
    const newConvId = eventData.new_conversation_id;

    setNotification({
        type: 'info',
        message: 'Conversation continuing in a new chat with fresh context...',
        duration: 3000,
    });

    setTimeout(() => {
        navigateToConversation(newConvId);
    }, 500);
}
```

### 7.2 Starting the continued conversation

When the user navigates to the new conversation (type `"user_chat_continued"`),
the frontend loads the pre-seeded messages from the store. It shows:

- A banner: "⬆️ Continued from [parent title]"
- The context package (collapsible)
- The original user task
- A "Start" button

When the user clicks "Start", the frontend sends:
```json
{
    "conversation_id": "<new_cid>",
    "messages": [/* pre-seeded messages from store */],
    "provider": "<same as parent>"
}
```
Note: no `"message"` field — the backend processes the existing messages.

### 7.3 UI Elements

- **Sidebar**: Show parent/child relationship with indentation + "↳ Continued" label
- **Chat view**: "⬆️ Continued from [parent title]" banner (clickable)
- **Status badges**: "Continued →" on parent, "← Continued" on child

---

## 8. Implementation Checklist

### Phase 1: `src/` (minimal — ~50 lines total)

- [ ] **`config.py`**: Add `CONTEXT_WINDOW_TOKENS`, `CONTEXT_WARN_THRESHOLD`, marker + notice
- [ ] **`core_tools/continue_chat.py`**: New file — ~8 line no-op function
- [ ] **`tool_definitions.py`**: Register tool definition, PARALLEL_SAFE_TOOLS, SUBAGENT_READ_ONLY_TOOLS, TOOL_FUNCTION_MAP
- [ ] **`main_flow.py`**: `stream_options` + capture `prompt_tokens` + tool filtering + notice injection
- [ ] **`main_flow.py`**: System message preservation for `[CONTINUED FROM PREVIOUS SESSION]`
- [ ] **`main_flow.py`**: End loop immediately when `continue_as_new_chat` called

### Phase 2: Proxy

- [ ] **`api.py`**: Add `new_conversation_id` to `ActiveStream`
- [ ] **`api.py`**: `_scan_for_continuation()` — scan assistant tool_calls
- [ ] **`api.py`**: Continuation detection + child convo creation in `_proxy_backend_stream()`
- [ ] **`api.py`**: `_find_original_user_task()`, `_build_context_package()`
- [ ] **`api.py`**: Annotate events with `new_conversation_id`
- [ ] **`api.py`**: Cancellation cascade for continuation streams

### Phase 3: Frontend

- [ ] **`App.jsx`**: SSE handler for `new_conversation_id` → auto-navigate
- [ ] **`App.jsx`**: Handle `user_chat_continued` type — pre-seeded messages + "Start" button
- [ ] **`App.jsx`**: Sidebar — parent/child relationship display
- [ ] **`App.jsx`**: Chat view — continuation banner + status badges

### Phase 4: Polish

- [ ] Test with a real long conversation that hits 80%+
- [ ] Verify tool appears/disappears at threshold
- [ ] Verify notice injected exactly once
- [ ] Verify agent can call the tool and loop ends cleanly
- [ ] Verify child conversation created with correct messages
- [ ] Verify frontend auto-navigates
- [ ] Verify the new agent picks up correctly
- [ ] Verify parent/child linking in sidebar
- [ ] Test multiple continuations (chain of 3+)
- [ ] Test interruption during transition

---

## 9. Edge Cases & Risk Mitigation

| Edge Case | Handling |
|-----------|----------|
| **Agent ignores the tool** | Tool appears at 80%+, no forced action. Model may truncate/error naturally. User can manually start new chat. |
| **Agent calls tool and model doesn't get to respond** | Loop ends immediately after tool execution — no wasted API call |
| **Frontend disconnected during transition** | Proxy creates child convo server-side. Frontend discovers it on reconnect via store. |
| **Multiple continue_as_new_chat in one turn** | Loop ends on first one. If agent batches it with other tools, only the first triggers end. |
| **Continue from a continued conversation** | Fully supported — each continuation creates a child with `parent_id` pointing to immediate parent. |
| **Workspace file state** | Same Docker container and session — all files, processes, shell state persist. |
| **Subagent running during continuation** | Subagent parent_id points to original convo. Cancellation cascades if parent is cancelled. |
| **Title extraction** | Title comes from `summary` argument: `[Continued] {first 80 chars}` |

---

## 10. Key Design Decisions

1. **Tool call IS the signal**: No JSON payload in the tool result. No error handling.
   The proxy scans the assistant's `tool_calls` array — the arguments ARE the context.

2. **Loop ends immediately**: When `continue_as_new_chat` is called, `main_flow.py`
   yields "completed" and returns. No extra model call to acknowledge. Saves tokens.

3. **Dead-simple tool function**: The tool function returns a one-line string.
   It does nothing else. The proxy does all the work.

4. **Proxy is the orchestrator**: Detects the tool call, builds the context package,
   creates the child conversation, seeds messages, marks parent status. `src/` sees
   nothing unusual.

5. **System message preserved via marker**: The `[CONTINUED FROM PREVIOUS SESSION]`
   marker ensures the context package survives system message regeneration.

6. **Same workspace**: No new session/container needed — continuation runs in the
   same environment, exactly like the existing "Continue after max_iterations" flow.

7. **No failure modes**: The tool always "succeeds" from the agent's perspective.
   If the proxy fails to create the child convo, that's a server error — the user
   sees it and can manually start a new chat. The agent doesn't need to handle it.
