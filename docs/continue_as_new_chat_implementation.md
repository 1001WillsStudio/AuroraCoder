# "Continue as a New Chat" — Implementation (Current State)

## Overview

When an agent's context window approaches exhaustion (~80%+ used), the
`continue_as_new_chat` tool appears in the tool list. The agent has full autonomy
to decide when to call it. The tool call itself is the signal — no complex payload,
no error handling, no subagent-like streaming. The proxy detects the tool call,
creates the child conversation, and the frontend auto-navigates.

The src/ part is minimal: the tool function is a no-op, `main_flow.py` just
ends the loop when the tool is called. All the real work lives in the proxy.

---

## 1. Architecture & Data Flow

```
Agent calls continue_as_new_chat(prompt="...")
        │
        ▼
   main_flow.py
   - Tool function returns "OK" (no-op)
   - Loop detects the tool was called → yields "completed" → returns
        │
        ▼
   Proxy (8081) — _proxy_backend_stream()
   - Scans raw_messages for assistant message with continue_as_new_chat tool_call
   - Extracts the single `prompt` argument
   - Builds context package: "[CONTINUED FROM PREVIOUS SESSION]\n{prompt}"
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
     │                                      │                              │
     │                                      ├─────────────────────────────►│
     │                                      │                              │
     │  done event (status: completed)      │                              │
     ├─────────────────────────────────────►│  done event                  │
     │                                      │  + new_conversation_id        │
     │                                      │                              │
     │                                      ├─────────────────────────────►│
     │                                      │                              │
     │                                      │  Frontend auto-navigates     │
     │                                      │  to new conversation          │
```

---

## 2. Context Usage Tracking — Real Token Counts

No estimation needed. DeepSeek's API returns per-request token usage during
streaming via `stream_options={"include_usage": True}`. The final chunk in each
stream carries a `usage` object with `prompt_tokens`, `completion_tokens`, etc.

In `main_flow.py`, after the chunk loop, capture `chunk.usage` when non-null:

```python
# In the for-chunk loop:
if hasattr(chunk, "usage") and chunk.usage:
    current_usage = chunk.usage.model_dump()

# After the chunk loop:
prompt_tokens = current_usage.get("prompt_tokens", 0) if current_usage else 0
```

The context check is:

```python
def _filter_tools_by_context(tools: list, prompt_tokens: int) -> list:
    if prompt_tokens / CONTEXT_WINDOW_TOKENS < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools
```

This requires `stream_options` in the API call (`api_kwargs` in `main_flow.py`):

```python
api_kwargs = {
    ...
    "stream_options": {"include_usage": True},
}
```

---

## 3. Dynamic Tool Availability

The `continue_as_new_chat` tool is filtered out of the tool list until context
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

A single `prompt` parameter. The agent writes a comprehensive free-form prompt
covering everything the next agent needs: what was accomplished, what remains,
key files, important decisions, and any nuanced context.

### Tool Implementation (`src/core_tools/continue_chat.py`)

```python
"""continue_as_new_chat — the tool call itself is the signal. The proxy handles everything."""

def continue_as_new_chat(prompt: str) -> str:
    """The proxy detects this tool call and creates the new conversation."""
    return "Continuing in a new chat with fresh context."
```

The tool function is a pure no-op. The proxy does all the real work.

### Classification

- **Read-only**: Yes — no side effects.
- **NOT in `PARALLEL_SAFE_TOOLS`**: The tool description instructs the agent to make this the ONLY tool call in the turn, and `main_flow.py` ends the loop immediately when it's called. Parallel execution is irrelevant.
- **NOT in `SUBAGENT_READ_ONLY_TOOLS`**: Subagents run with tighter iteration caps (15 vs 30) and read-only tool sets. Their context is unlikely to reach the 80% threshold, so the tool is intentionally excluded to avoid unnecessary complexity.

---

## 5. src/ Changes

### 5.1 `src/config.py` — 4 constants

```python
CONTEXT_WINDOW_TOKENS = 128_000
CONTEXT_WARN_THRESHOLD = 0.80

_CONTINUATION_NOTICE_MARKER = "[CONTEXT CONTINUATION TOOL AVAILABLE]"
CONTINUATION_NOTICE = (
    "⚠️ `continue_as_new_chat` is now available in your tool list — "
    "you are at ~80% context."
)
```

### 5.2 `src/main_flow.py`

#### A) Token usage + tool filtering + notice injection

- `stream_options={"include_usage": True}` added to `api_kwargs`
- `prompt_tokens` captured from `chunk.usage` after the chunk loop
- `_filter_tools_by_context()` filters out `continue_as_new_chat` below 80%
- `_has_continuation_notice_been_shown()` checks for marker in system message
- Notice (with marker) injected once when threshold crosses 80%

#### B) System message preservation for continuation

When the incoming system message contains `[CONTINUED FROM PREVIOUS SESSION]`,
the context package is preserved and the standard system prompt is appended:

```python
if "[CONTINUED FROM PREVIOUS SESSION]" in existing:
    current_processing_messages[0]["content"] = existing + "\n\n---\n\n" + system_message
```

#### C) End loop when `continue_as_new_chat` is called

```python
if any(tc["function"]["name"] == "continue_as_new_chat" for tc in current_tool_calls):
    yield {"messages": current_processing_messages, "status": "completed", "provider": provider_id}
    return
```

### 5.3 `src/core_tools/continue_chat.py` — NEW file

```python
def continue_as_new_chat(prompt: str) -> str:
    return "Continuing in a new chat with fresh context."
```

### 5.4 `src/tool_definitions.py`

- Tool definition added to `NATIVE_TOOL_DEFINITIONS` with a single `prompt` parameter
- Added to `TOOL_FUNCTION_MAP`
- Import: `from .core_tools.continue_chat import continue_as_new_chat`

### 5.5 `src/web_api/app.py` — NO changes

The backend remains completely unaware of continuation.

---

## 6. Proxy Changes (`conversation_history/api.py`)

### 6.1 `ActiveStream` — `new_conversation_id` field

```python
@dataclass
class ActiveStream:
    ...
    new_conversation_id: Optional[str] = None   # set when continuation detected
```

### 6.2 Continuation detection in `_proxy_backend_stream()`

After parsing each SSE event:

```python
if not stream.new_conversation_id:
    args = _scan_for_continuation(edata.get("raw_messages", []))
    if args:
        new_cid = str(uuid.uuid4())
        context_pkg = _build_context_package(**args)

        store.create_conversation(
            conversation_id=new_cid,
            parent_id=cid,
            conv_type="user_chat_continued",
            title=args.get("prompt", "Continued conversation")[:80],
            provider_id=stream.provider,
        )
        original_task = _find_original_user_task(cid)
        store.save_messages(new_cid, [
            {"role": "system", "content": context_pkg},
            {"role": "user", "content": original_task},
        ])
        store.update_status(cid, "continued")
        stream.new_conversation_id = new_cid
```

### 6.3 Event annotation

```python
if stream.new_conversation_id:
    edata["new_conversation_id"] = stream.new_conversation_id
```

### 6.4 `_scan_for_continuation()`

Scans assistant `tool_calls` in `raw_messages` for `continue_as_new_chat`:

```python
def _scan_for_continuation(raw_messages: list) -> dict | None:
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

Wraps the agent's prompt with the continuation marker. The agent itself is
responsible for structuring all necessary context (summary, pending tasks,
key files, important decisions) within its free-form `prompt`:

```python
def _build_context_package(prompt: str) -> str:
    return f"[CONTINUED FROM PREVIOUS SESSION]\n{prompt}"
```

The resulting system message for the new agent looks like:

```
[CONTINUED FROM PREVIOUS SESSION]
<agent's comprehensive prompt covering summary, pending tasks, key files, etc.>

---

<standard system prompt — tool usage guidelines, subagent safety, etc.>
```

### 6.7 Cancellation cascade

When a parent stream is cancelled, any continuation child is also cancelled:

```python
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

### 7.1 `App.jsx` — SSE event handling

```javascript
if (data?.new_conversation_id && !continuationNavigatedRef.current.has(data.new_conversation_id)) {
    continuationNavigatedRef.current.add(data.new_conversation_id)
    setTimeout(() => {
        handleLoadConversation(data.new_conversation_id)
    }, 500)
}
```

### 7.2 Continued conversation flow

When the frontend loads a conversation with `type === "user_chat_continued"`:

1. Shows a "⬆️ Continued from previous conversation" banner with a Start button
2. Hides the input area until Start is clicked
3. Navigation back to parent is available via a "Back to parent" button
4. Auto-launches (clicks Start programmatically) after a short delay on initial load

When Start is clicked, the frontend sends the pre-seeded messages to the backend:

```json
{
    "conversation_id": "<new_cid>",
    "messages": [/* pre-seeded system + user messages */],
    "provider": "<same as parent>"
}
```

No `"message"` field — the backend processes the existing messages.

### 7.3 UI elements

- **Sidebar** (`ConversationHistory.jsx`): Parent/child shown via `CurrentSession` component with ↳ prefix
- **Chat view**: "⬆️ Continued from previous conversation" banner with Start and Back buttons (`continued-view-bar`)
- **History drawer**: Grouped display showing child conversations indented under their parents

---

## 8. Design Decisions

1. **Single `prompt` parameter**: The agent writes a free-form prompt covering all context the next agent needs. This avoids the complexity of structured fields while letting the agent decide what information is most relevant to communicate. The agent knows best what matters.

2. **Tool call IS the signal**: No JSON payload in the tool result. No error handling. The proxy scans the assistant's `tool_calls` array — the arguments ARE the context.

3. **Loop ends immediately**: When `continue_as_new_chat` is called, `main_flow.py` yields "completed" and returns. No extra model call to acknowledge. Saves tokens.

4. **Dead-simple tool function**: The tool function returns a one-line string. It does nothing else. The proxy does all the work.

5. **Proxy is the orchestrator**: Detects the tool call, builds the context package, creates the child conversation, seeds messages, marks parent status. `src/` sees nothing unusual.

6. **System message preserved via marker**: The `[CONTINUED FROM PREVIOUS SESSION]` marker ensures the context package survives system message regeneration. The standard system prompt is appended with a `---` separator.

7. **Same workspace**: No new session/container needed — continuation runs in the same environment, exactly like the existing "Continue after max_iterations" flow.

8. **No failure modes**: The tool always "succeeds" from the agent's perspective. If the proxy fails to create the child convo, that's a server error — the user sees it and can manually start a new chat.

9. **Not parallel-safe by design**: The tool instructs the agent to make it the only tool call in the turn. The loop ends immediately when it's called. Adding it to `PARALLEL_SAFE_TOOLS` would be misleading.

10. **Not available to subagents**: Subagents have tighter iteration caps (15) and read-only tool sets. Their context is unlikely to reach the 80% threshold, and keeping the tool set minimal avoids unnecessary complexity.

---

## 9. Edge Cases

| Edge Case | Handling |
|-----------|----------|
| **Agent ignores the tool** | Tool appears at 80%+, no forced action. Model may truncate/error naturally. User can manually start new chat. |
| **Agent calls tool and model doesn't get to respond** | Loop ends immediately after tool execution — no wasted API call |
| **Frontend disconnected during transition** | Proxy creates child convo server-side. Frontend discovers it on reconnect via store. |
| **Multiple `continue_as_new_chat` in one turn** | Loop ends on first one. If agent batches it with other tools, only the first triggers end. |
| **Continue from a continued conversation** | Fully supported — each continuation creates a child with `parent_id` pointing to immediate parent. |
| **Workspace file state** | Same Docker container and session — all files, processes, shell state persist. |
| **Subagent running during continuation** | Subagent `parent_id` points to original convo. Cancellation cascades if parent is cancelled. |
| **Title extraction** | Title comes from first 80 characters of the agent's `prompt`. Agent should front-load a concise summary. |
