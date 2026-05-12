# Continue as New Chat

## Philosophy

When the agent calls `continue_as_new_chat(prompt="...")`, the agent's
prompt **IS** the user message.  There is zero difference between the
continuation and a human opening a new chat, typing the message, and
pressing Send.  No special conversation type, no Start button, no
frontend view mode — just a normal `user_chat`.

---

## Triggers

1. **Automatic (proactive):** When context usage crosses ~80% of the
   provider's `context_window`, a notice is injected into the system
   message prompting the agent to use the tool.
2. **Manual (user-initiated):** The user clicks the "Continue in New Chat"
   button in the UI, which sends a templated user message instructing
   the agent to call `continue_as_new_chat`.

The tool is **always included** in the tool list regardless of context
usage, so it is available whenever the user requests it.

---

## Flow

```
Agent calls continue_as_new_chat(prompt="...")
  (proactively after 80% notice, or on user request via UI button)
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
| `_scan_for_continuation(raw_messages)` | Scan assistant messages for `continue_as_new_chat` tool call; return `{"prompt": "..."}` |
| `_start_continuation(new_cid, provider_id, user_msg)` | Create ActiveStream + POST user message to backend (simulates user Send) |
| `_proxy_backend_stream` continuation block | Detect, create child conv, seed message, fire `_start_continuation`, annotate events with `new_conversation_id` |

### `src/main_flow.py`

The tool is **always included** in the tool list (no filtering). The 80%
notice prompts the agent to use it proactively; users can trigger it at any
time via the UI "Continue in New Chat" button.

| Location | Responsibility |
|----------|---------------|
| System message assignment | Standard — no `[CONTINUED FROM PREVIOUS SESSION]` marker |
| `continue_as_new_chat` exit check | When called, exit loop with `status="completed"` |
| `_CONTINUATION_NOTICE_MARKER` injection | Once context crosses per-provider threshold (~80%), inject notice prompting proactive use |

### `src/config.py`

| Constant | Purpose |
|----------|---------|
| `MODEL_PROVIDERS[*]["context_window"]` | Per-provider context window size (e.g. 1 048 576 for DS V4 Pro, 128 000 for NVIDIA models) |
| `CONTEXT_WINDOW_TOKENS` = 128 000 | Fallback when provider has no `context_window` key |
| `CONTEXT_WARN_THRESHOLD` = 0.80 | Inject proactive-use notice at 80% usage |
| `_CONTINUATION_NOTICE_MARKER` | `[CONTEXT CONTINUATION TOOL AVAILABLE]` — injected into system message header once |
| `CONTINUATION_NOTICE` | One-liner telling the agent the tool is now available |

### `src/tool_definitions.py`

Tool definition with single `prompt` parameter.

### `src/core_tools/continue_chat.py`

No-op function — the tool call itself is the signal; proxy handles everything.

### `frontend/src/App.jsx`

| Location | Responsibility |
|----------|---------------|
| "Continue in New Chat" button | Sends a templated user message asking the agent to call `continue_as_new_chat` |
| `handleLoadConversation` | Only checks for `subagent` type — all conversations are either `user_chat` or `subagent` |
| `onMessages` (in `handleSend`, `handleContinue`, `resumeStream`) | Receives `new_conversation_id` → auto-navigates to continuation |
| `continuationNavigatedRef` | Tracks already-navigated conversations to avoid double-navigation |

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
| `_filter_tools_by_context()` | main_flow.py | Tool is now always included; removed gating |

---

## Notes

1. ~~**`tool_definitions.py`**: The description said "system message" — **fixed**, now says "user message".~~

2. **`_start_continuation`** sets `parent_id=None` on the ActiveStream
   even though the store record has `parent_id=cid`.  This is intentional:
   the continuation is an independent `user_chat`, not a subagent, so it
   should not be cascaded in parent-sidebar hierarchy.  The cascading cancel
   code in api.py handles it explicitly via `new_conversation_id`.
