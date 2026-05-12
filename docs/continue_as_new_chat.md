# Continue as New Chat

## Philosophy

When the agent calls `continue_as_new_chat(prompt="...")`, the agent's
prompt **IS** the user message.  There is zero difference between the
continuation and a human opening a new chat, typing the message, and
pressing Send.  No special conversation type, no Start button, no
frontend view mode — just a normal `user_chat`.

---

## Flow

```
Agent calls continue_as_new_chat(prompt="...")   (only when context ≥ 80%)
        │
        ▼
main_flow.py: loop exits immediately with status="completed"
        │
        ▼
Proxy (_proxy_backend_stream, api.py):
  1. _scan_for_continuation() detects the tool call in raw_messages
  2. Creates child conversation:  conv_type="user_chat", parent_id=cid
  3. Seeds ONE user message:  "[Continued from previous agent session]\n\n<prompt>"
  4. Fires asyncio.create_task(_start_continuation(...))
        │
        ▼
_start_continuation():
  - Creates a new ActiveStream (parent_id=None, conv_type="user_chat")
  - POSTs the user message to the backend via _proxy_backend_stream
  - Backend processes it exactly like any new chat
        │
        ▼
Frontend:
  - Receives new_conversation_id in SSE events → auto-navigates
  - Conversation is already running → resumes live stream
```

---

## Key files

### `conversation_history/api.py`

| Location | Responsibility |
|----------|---------------|
| `_scan_for_continuation(raw_messages)` → line 165 | Scan assistant messages for `continue_as_new_chat` tool call; return `{"prompt": "..."}` |
| `_start_continuation(new_cid, provider_id, user_msg)` → line 182 | Create ActiveStream + POST user message to backend (simulates user Send) |
| `_proxy_backend_stream` continuation block → lines 261–293 | Detect, create child conv, seed message, fire `_start_continuation`, annotate events with `new_conversation_id` |

### `src/main_flow.py`

| Location | Responsibility |
|----------|---------------|
| `_filter_tools_by_context()` → line 247 | Hides `continue_as_new_chat` tool until context ≥ 80% |
| System message assignment → lines 307–311 | Standard assignment — no `[CONTINUED FROM PREVIOUS SESSION]` marker |
| Line 508–515 | When `continue_as_new_chat` is called, exit loop with `status="completed"` |
| `_CONTINUATION_NOTICE_MARKER` injection → lines 466–470 | Once context crosses threshold, inject notice into system message informing agent the tool is available |

### `src/config.py`

| Constant | Purpose |
|----------|---------|
| `CONTEXT_WINDOW_TOKENS` = 128 000 | Total context window |
| `CONTEXT_WARN_THRESHOLD` = 0.80 | Show tool at 80% usage |
| `_CONTINUATION_NOTICE_MARKER` | `[CONTEXT CONTINUATION TOOL AVAILABLE]` — injected into system message header once |
| `CONTINUATION_NOTICE` | One-liner telling the agent the tool is now available |

### `src/tool_definitions.py` → lines 363–391

Tool definition with single `prompt` parameter.

### `src/core_tools/continue_chat.py`

No-op function — the tool call itself is the signal; proxy handles everything.

### `frontend/src/App.jsx`

| Location | Responsibility |
|----------|---------------|
| `handleLoadConversation` → line 946 | Only checks for `subagent` type — all conversations are either `user_chat` or `subagent` |
| `onMessages` → lines 601–608 | Receives `new_conversation_id` → auto-navigates to continuation |
| `continuationNavigatedRef` → line 191 | Tracks already-navigated conversations to avoid double-navigation |

There is no special `user_chat_continued` type, no `viewMode === 'continued'`,
no `handleStartContinued`, no continuation view bar, and no Start button.
The frontend treats continuations as ordinary `user_chat` conversations.

---

## What was removed

| Removed | From | Why |
|---------|------|-----|
| `_find_original_user_task()` | api.py | No longer needed — the agent's prompt IS the user message |
| `_build_context_package()` | api.py | No `[CONTINUED FROM PREVIOUS SESSION]` system message anymore |
| `conv_type="user_chat_continued"` | api.py | All continuations use normal `"user_chat"` |
| `[CONTINUED FROM PREVIOUS SESSION]` marker handling | main_flow.py | No special system message treatment needed |
| `isContinued` / `viewMode === 'continued'` | App.jsx | Dead code — no `user_chat_continued` type exists |
| `handleStartContinued()` | App.jsx | No Start button needed — auto-started server-side |
| Continuation view bar | App.jsx | Dead code |
| `viewMode !== 'continued'` in input hiding | App.jsx | Dead code |

---

## Unfinished / notes

1. **`tool_definitions.py` line 370**: The description says the prompt is passed
   as a **system message** — it is actually passed as a **user message**.
   Update the description to say "user message" instead of "system message".

2. **`_start_continuation`** sets `parent_id=None` on the ActiveStream (line 199)
   even though the store record has `parent_id=cid`.  This is intentional:
   the continuation is an independent `user_chat`, not a subagent, so it
   should not be cascaded in parent-sidebar hierarchy.  The cascading cancel
   code (api.py lines 108–120) handles it explicitly via `new_conversation_id`.
