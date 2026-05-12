# "Continue as a New Chat" — Feature Design Plan

## Overview

When an agent's context window approaches exhaustion (~80%+ used), a **one-time hint** appears in the system prompt telling the agent it can call a `continue_as_new_chat` tool. The agent has full autonomy to decide:

1. **Whether** to use it (optional — never forced)
2. **When** to use it (strategically — after wrapping up a logical unit)
3. **What** information to pass forward to the new session

The tool creates a **brand-new conversation** seeded with a structured context package, so the new agent picks up where the old one left off — with a clean, empty context window.

---

## 1. Architecture & Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CURRENT SESSION                              │
│                                                                     │
│  1. main_flow.py estimates context usage each iteration             │
│  2. At ≥80% → one-time hint injected into system message            │
│  3. Agent sees hint, continues working until ready                  │
│  4. Agent calls continue_as_new_chat(summary, files, tasks, ctx)    │
│  5. Tool implementation:                                            │
│     a. Persists current conversation as "continued"                 │
│     b. Creates new conversation with context package                │
│     c. Returns new_conversation_id in tool result                   │
│  6. Backend emits special SSE event with new_conversation_id        │
│  7. Frontend auto-navigates to the new conversation                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         NEW SESSION                                  │
│                                                                     │
│  • System message: standard + context package from old session      │
│  • First user message: original user task (replayed)                │
│  • Full context window available                                    │
│  • Same workspace / filesystem state (same Docker container)        │
│  • Linked to parent conversation via parent_id                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### SSE Event Flow

```
Backend (8080)                          Proxy (8081)                    Frontend
     │                                      │                              │
     │  messages event (status: running)    │                              │
     ├─────────────────────────────────────►│  messages event              │
     │                                      ├─────────────────────────────►│
     │  ... agent calls continue_as_new_chat ...                           │
     │                                      │                              │
     │  messages event                      │                              │
     │  status: "continue_requested"        │                              │
     │  new_conversation_id: "abc123"       │                              │
     ├─────────────────────────────────────►│                              │
     │                                      │  messages event              │
     │                                      │  (with new_conversation_id)  │
     │                                      ├─────────────────────────────►│
     │  done event (current convo)          │                              │
     ├─────────────────────────────────────►│                              │
     │                                      │  done event                  │
     │                                      ├─────────────────────────────►│
     │                                      │                              │
     │                                      │  Frontend navigates to       │
     │                                      │  new conversation, sends     │
     │                                      │  POST /api/chat with         │
     │                                      │  conversation_id=abc123      │
     │                                      │  (no user message — backend  │
     │                                      │   uses pre-seeded messages)  │
     │                                      │                              │
```

---

## 2. Context Usage Estimation

### Approach: Character-count-based approximation

Since we don't have access to model-specific tokenizers at runtime, use a heuristic:

```python
def estimate_token_count(messages: list) -> int:
    """Estimate token count from the OpenAI-format message list."""
    serialized = json.dumps(messages, ensure_ascii=False)
    # Rough heuristic: ~2.5 characters per token for English text
    # This is conservative — most models are closer to 3-4 chars/token
    return len(serialized) // 2.5

def estimate_context_usage_pct(messages: list, context_window: int = 128_000) -> float:
    """Return estimated context usage as a fraction (0.0 to 1.0+)."""
    return estimate_token_count(messages) / context_window
```

### Config entries to add (`config.py`)

```python
# Context continuation
CONTEXT_WINDOW_TOKENS = 128_000          # Default for most models
CONTEXT_WARN_THRESHOLD = 0.80            # 80% — hint appears
CONTEXT_CRITICAL_THRESHOLD = 0.95        # 95% — urgent tone in hint
```

The threshold should also be **per-provider** eventually, but start with a global default.

---

## 3. The One-Time Hint

### Where it lives

The hint is **not** in `SYSTEM_MESSAGE_TEMPLATE` (that's static). Instead, it's injected dynamically by `main_flow.py` when usage crosses 80% — and only once per conversation.

### Injection mechanism

In `generate_chat_responses_stream_native()`, after building the system message:

```python
# Check if context continuation hint should be shown
hint_shown = _has_continuation_hint_been_shown(current_processing_messages)
usage_pct = estimate_context_usage_pct(current_processing_messages)

if not hint_shown and usage_pct >= CONTEXT_WARN_THRESHOLD:
    if usage_pct >= CONTEXT_CRITICAL_THRESHOLD:
        hint = CONTINUATION_HINT_URGENT
    else:
        hint = CONTINUATION_HINT_NORMAL
    # Append hint to system message
    current_processing_messages[0]["content"] += "\n\n" + hint
```

The hint is detected as "shown" by scanning for a marker string in any system message:

```python
_CONTINUATION_HINT_MARKER = "[CONTEXT CONTINUATION AVAILABLE]"

def _has_continuation_hint_been_shown(messages: list) -> bool:
    for msg in messages:
        if msg.get("role") == "system" and _CONTINUATION_HINT_MARKER in msg.get("content", ""):
            return True
    return False
```

### Hint text (in `config.py`)

```python
CONTINUATION_HINT_NORMAL = """
[CONTEXT CONTINUATION AVAILABLE]
⚠️ **Context Window Notice**: You have used approximately 80%+ of your context window.
You have access to a **`continue_as_new_chat`** tool that allows you to pass all the
information you've gathered (key findings, file states, pending tasks, and a summary)
to a brand-new agent session with a fresh context window.

**How it works**:
- Call `continue_as_new_chat` with a comprehensive summary, key file paths,
  pending tasks, and any other critical context.
- The new session will pick up exactly where you left off — same workspace,
  same files, same task, but with a full context window.
- This is optional — use it when you determine it's the right strategic moment.
- Ideal timing: after completing a logical unit of work, before starting the next.

**Recommendation**: If you anticipate needing many more tool calls or iterations,
consider using this tool now to avoid hitting context limits mid-task.
"""

CONTINUATION_HINT_URGENT = """
[CONTEXT CONTINUATION AVAILABLE]
🚨 **CRITICAL Context Warning**: You have used approximately 95%+ of your context window.
You are at risk of truncated responses or context overflow.

**Strongly recommended**: Call `continue_as_new_chat` NOW with a comprehensive
summary of everything accomplished, key files, pending tasks, and essential context.
The new session will continue with a completely fresh context window.

Do NOT delay — if context overflows, information will be lost.
"""
```

---

## 4. The `continue_as_new_chat` Tool

### Tool Definition (in `tool_definitions.py`)

```python
{
    "type": "function",
    "function": {
        "name": "continue_as_new_chat",
        "description": (
            "Continue the current task in a brand-new agent session with a fresh "
            "context window. Use this when you are running out of context space "
            "(typically after extensive file reading, web browsing, or many tool "
            "iterations).\n\n"
            "WHAT THIS DOES:\n"
            "- Saves the current conversation and creates a new one\n"
            "- Passes your summary, key files, and pending tasks to the new agent\n"
            "- The new agent runs in the SAME workspace with the SAME files\n"
            "- The new agent has a completely fresh context window\n\n"
            "WHEN TO USE:\n"
            "- You've used 80%+ of your context window\n"
            "- You've gathered extensive information and need more room to work\n"
            "- You're about to start a new phase of work\n\n"
            "WHAT TO INCLUDE IN YOUR SUMMARY:\n"
            "- What was accomplished so far (be specific)\n"
            "- Key decisions made and why\n"
            "- Files created/modified and their purpose\n"
            "- What remains to be done (pending tasks)\n"
            "- Any critical context the new agent MUST know\n\n"
            "The more thorough your summary, the more seamless the transition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Comprehensive summary of what has been accomplished so far, key decisions made, and the current state of the task."
                },
                "key_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of key file paths (relative to workspace) that the new agent should know about. Include a brief note about each file's purpose and state."
                },
                "pending_tasks": {
                    "type": "string",
                    "description": "Detailed description of what still needs to be done. Be specific about next steps, requirements, and constraints."
                },
                "important_context": {
                    "type": "string",
                    "description": "Any other critical context the new agent needs — configuration details, API keys used, URLs visited, research findings, error messages encountered, user preferences, etc."
                }
            },
            "required": ["summary", "pending_tasks"]
        }
    }
}
```

### Tool Classification

- **Read-only**: Yes (add to `READ_ONLY_TOOLS`) — it doesn't modify the filesystem; it only saves conversation state and creates a new conversation entry.
- **Concurrent-safe**: Yes

### Implementation (`src/core_tools/continue_chat.py`)

```python
"""
continue_as_new_chat tool — hands off the current task to a fresh agent session.
"""

import json
import uuid
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Will be set by the web API at startup to point to the conversation store
_conversation_store = None
_get_current_conversation_id = None

def set_continuation_dependencies(store, get_cid_fn):
    """Called by web_api/app.py during startup to inject dependencies."""
    global _conversation_store, _get_current_conversation_id
    _conversation_store = store
    _get_current_conversation_id = get_cid_fn


def continue_as_new_chat(
    summary: str,
    pending_tasks: str,
    key_files: list = None,
    important_context: str = "",
) -> str:
    """
    Creates a new conversation seeded with context from the current one.

    The new conversation starts with:
    - A system message containing the context package
    - The original user's task message (replayed)
    - Full context window available

    The current conversation is persisted and marked as "continued".
    """
    if key_files is None:
        key_files = []

    new_conversation_id = str(uuid.uuid4())
    current_id = _get_current_conversation_id() if _get_current_conversation_id else None

    # Build the context package that the new agent will receive
    context_package = _build_context_package(
        summary=summary,
        key_files=key_files,
        pending_tasks=pending_tasks,
        important_context=important_context,
        parent_conversation_id=current_id,
    )

    # Create the new conversation in the store
    if _conversation_store:
        try:
            _conversation_store.create_conversation(
                conversation_id=new_conversation_id,
                parent_id=current_id,
                conv_type="user_chat_continued",
                title=f"[Continued] {summary[:80]}",
            )

            # Seed with context package and replayed user task
            # The actual user message will be injected by the frontend
            # when it navigates to the new conversation.
            # For now, store minimal metadata.
            _conversation_store.save_messages(new_conversation_id, [])

        except Exception as e:
            logger.error(f"Failed to create continuation conversation: {e}")
            return f"Error: Could not create continuation — {e}"

    # Mark current conversation as continued
    if _conversation_store and current_id:
        try:
            _conversation_store.update_status(current_id, "continued")
        except Exception:
            pass

    # Return structured result that the backend can parse
    result = {
        "status": "continue_requested",
        "new_conversation_id": new_conversation_id,
        "context_package": context_package,
        "message": (
            f"✅ Continuation initiated. A new agent session will start with "
            f"all the context you provided. Conversation ID: {new_conversation_id[:8]}...\n\n"
            f"The new agent will receive:\n"
            f"- Your summary ({len(summary)} chars)\n"
            f"- {len(key_files)} key file paths\n"
            f"- Pending tasks ({len(pending_tasks)} chars)\n"
            f"- Additional context ({len(important_context)} chars)"
        ),
    }

    return json.dumps(result, ensure_ascii=False)


def _build_context_package(
    summary: str,
    key_files: list,
    pending_tasks: str,
    important_context: str,
    parent_conversation_id: str = None,
) -> str:
    """Build the context package that seeds the new agent's system message."""

    package = f"""[CONTINUED FROM PREVIOUS SESSION]
The previous agent session reached its context limit. You are continuing the work.
Below is everything you need to pick up seamlessly.

## Task Summary
{summary}

## Key Files
"""
    if key_files:
        for f in key_files:
            package += f"- {f}\n"
    else:
        package += "(No specific files noted — explore the workspace as needed)\n"

    package += f"""
## Pending Tasks
{pending_tasks}
"""

    if important_context:
        package += f"""
## Important Context
{important_context}
"""

    if parent_conversation_id:
        package += f"""
## Reference
Parent conversation ID: {parent_conversation_id}
(You can read the full conversation history from the conversation store if needed.)
"""

    package += """

You are now the active agent. Pick up from here and continue working toward
completing the user's original request. The workspace files are exactly as
the previous agent left them.
"""
    return package
```

---

## 5. Backend Changes

### 5.1 `src/config.py`

Add these entries:

```python
# Context continuation
CONTEXT_WINDOW_TOKENS = 128_000
CONTEXT_WARN_THRESHOLD = 0.80
CONTEXT_CRITICAL_THRESHOLD = 0.95

# One-time hint (injected into system message when threshold crossed)
_CONTINUATION_HINT_MARKER = "[CONTEXT CONTINUATION AVAILABLE]"

CONTINUATION_HINT_NORMAL = """..."""  # as shown above
CONTINUATION_HINT_URGENT = """..."""  # as shown above
```

Add `{continuation_hint}` placeholder to `SYSTEM_MESSAGE_TEMPLATE` (defaults to empty string).

### 5.2 `src/main_flow.py`

Add these functions:

```python
from .config import (
    # ... existing imports ...
    CONTEXT_WINDOW_TOKENS, CONTEXT_WARN_THRESHOLD, CONTEXT_CRITICAL_THRESHOLD,
    CONTINUATION_HINT_NORMAL, CONTINUATION_HINT_URGENT,
    _CONTINUATION_HINT_MARKER,
)

def estimate_token_count(messages: list) -> int:
    """Estimate token count from the message list using character ratio."""
    serialized = json.dumps(messages, ensure_ascii=False, default=str)
    return len(serialized) // 2.5

def estimate_context_usage_pct(messages: list, context_window: int = None) -> float:
    """Return estimated context usage as a fraction (0.0 to 1.0)."""
    if context_window is None:
        context_window = CONTEXT_WINDOW_TOKENS
    tokens = estimate_token_count(messages)
    return tokens / context_window

def _has_continuation_hint_been_shown(messages: list) -> bool:
    """Check if the one-time continuation hint has already been injected."""
    for msg in messages:
        if msg.get("role") == "system" and _CONTINUATION_HINT_MARKER in msg.get("content", ""):
            return True
    return False
```

Modify `generate_chat_responses_stream_native()`:

After the system message is set (around line 289), add:

```python
    # --- Inject context continuation hint (one-time, at 80%+) ---
    if not _has_continuation_hint_been_shown(current_processing_messages):
        usage_pct = estimate_context_usage_pct(current_processing_messages)
        if usage_pct >= CONTEXT_WARN_THRESHOLD:
            if usage_pct >= CONTEXT_CRITICAL_THRESHOLD:
                hint = CONTINUATION_HINT_URGENT
            else:
                hint = CONTINUATION_HINT_NORMAL
            current_processing_messages[0]["content"] += "\n\n" + hint
```

Also, after executing the `continue_as_new_chat` tool, detect its result and yield a special status. Modify the tool execution section (around line 446) to check for the continuation result:

```python
# After appending tool results, detect continuation
for tc in batch:
    tc_out, tool_name, result = _execute_single_tool(tc)
    # ... existing code to append tool result ...
    
    # If this was a continue_as_new_chat call, mark the status
    if tool_name == "continue_as_new_chat":
        try:
            parsed = json.loads(result)
            if parsed.get("status") == "continue_requested":
                _pending_continuation = parsed
        except (json.JSONDecodeError, KeyError):
            pass
```

Then at the yield point, include it:

```python
yield_dict = {
    "messages": current_processing_messages,
    "status": "continue_requested" if _pending_continuation else "running",
    "provider": provider_id,
}
if _pending_continuation:
    yield_dict["new_conversation_id"] = _pending_continuation["new_conversation_id"]
    yield_dict["context_package"] = _pending_continuation.get("context_package", "")
yield yield_dict

if _pending_continuation:
    # Don't loop further — let the frontend handle the transition
    return
```

### 5.3 `src/tool_definitions.py`

- Add `continue_as_new_chat` to `NATIVE_TOOL_DEFINITIONS`
- Add `"continue_as_new_chat"` to `READ_ONLY_TOOLS`
- Add `"continue_as_new_chat": continue_as_new_chat` to `TOOL_FUNCTION_MAP`
- Import `from ..core_tools.continue_chat import continue_as_new_chat`

### 5.4 `src/web_api/app.py`

- In `convert_messages_for_frontend()`, handle `status == "continue_requested"` by including `new_conversation_id` and `context_package` in the frontend message.
- In the SSE streaming, when status is `"continue_requested"`, the frontend will receive a `done` event with `new_conversation_id`.
- Register the continuation dependencies during startup:

```python
from ..core_tools.continue_chat import set_continuation_dependencies

# In lifespan startup:
set_continuation_dependencies(
    store=store,  # Need to import or create store reference
    get_cid_fn=lambda: current_conversation_id  # Thread-local or similar
)
```

Wait — there's a challenge: the backend (`web_api/app.py` on port 8080) is stateless and doesn't have direct access to the conversation store (which lives in the proxy on port 8081). The continuation tool needs to write to the store.

**Solution**: The `continue_as_new_chat` tool should communicate the continuation request via the SSE stream. The **proxy** (port 8081) already intercepts SSE events and persists them. So:

1. Backend tool returns a specially-formatted JSON result
2. Backend yields the status `continue_requested` with `new_conversation_id`
3. **Proxy** (8081) sees this, creates the new conversation in the store, and forwards the event to the frontend
4. Frontend navigates to the new conversation

Actually, looking at the existing architecture more carefully — the subagent tool already posts to the proxy at `http://localhost:8081/api/chat`. We can do something similar. But `continue_as_new_chat` is simpler — it doesn't need to start a new stream, just create a conversation entry.

**Better approach**: Have the `continue_as_new_chat` tool implementation directly call the conversation store. We can import the store from `conversation_history/conversation_store.py` which is a file-backed singleton that both the backend and proxy can access (they share the same filesystem in Docker).

Let me revise:

```python
# In continue_chat.py
from conversation_history.conversation_store import ConversationStore

_store = ConversationStore()

def continue_as_new_chat(...):
    # ... use _store directly ...
```

This avoids needing IPC between backend and proxy for this operation. Both services mount the same `/app/data` volume.

### 5.5 `conversation_history/api.py` (Proxy)

The proxy needs to:

1. Recognize the `continue_requested` status in proxied SSE events
2. When seen, transition the current stream to "continued" and close it
3. The frontend will then receive the `done` event with `new_conversation_id`

Modify `_proxy_backend_stream()` to handle this:

```python
if etype in ("messages", "done"):
    status = edata.get("status", stream.status)
    if status == "continue_requested":
        new_cid = edata.get("new_conversation_id")
        if new_cid:
            # Create the new conversation entry
            context_pkg = edata.get("context_package", "")
            store.create_conversation(
                conversation_id=new_cid,
                parent_id=cid,
                conv_type="user_chat_continued",
                title=extract_continuation_title(context_pkg),
            )
            # Seed the new conversation with the context package as a system message
            # and the original task as the user message
            original_task = _find_original_user_task(cid)
            seed_messages = [
                {"role": "system", "content": context_pkg},
                {"role": "user", "content": original_task},
            ]
            store.save_messages(new_cid, seed_messages)
            store.save_frontend_messages(new_cid, [
                {"role": "user", "content": original_task}
            ])
```

The proxy needs a helper to find the original user task from the parent conversation's stored messages.

---

## 6. Frontend Changes

### 6.1 `App.jsx` — SSE Event Handling

Add a handler for `continue_requested` status in the `messages` or `done` event:

```javascript
// In the SSE event handler
if (eventData.status === 'continue_requested' || eventData.new_conversation_id) {
    const newConvId = eventData.new_conversation_id;
    const contextPackage = eventData.context_package;
    
    // 1. Show a transition notification
    setNotification({
        type: 'info',
        message: 'Conversation continuing in a new chat with fresh context...',
        duration: 3000,
    });
    
    // 2. Store the context package for the new conversation
    sessionStorage.setItem(`continue_ctx_${newConvId}`, contextPackage);
    
    // 3. Navigate to the new conversation
    //    The new conversation will auto-start because the proxy has
    //    already seeded it with the context package and user task.
    setTimeout(() => {
        navigateToConversation(newConvId);
    }, 500);
}
```

### 6.2 New Conversation Auto-Start

When the frontend loads a conversation of type `"user_chat_continued"`, it should check if it has pre-seeded messages (from the proxy) and display them without sending a new chat request. The user can then click "Continue" or the agent auto-starts.

Actually, simpler: the proxy seeds the messages and the frontend loads them. The user sees:
- A system message: "Continuing from previous session..."
- The context package (collapsible)
- The original task
- A "Start" button to begin the new agent session

When the user clicks "Start" (or it auto-starts), the frontend sends the seeded messages to the backend via `POST /api/chat` with `conversation_id=new_cid`, `messages=messages` (which includes the context-package system message and original user task), and no new `message` field (this triggers the "continue" flow which just processes existing messages).

Wait — actually looking at the backend code more carefully:

```python
if chat_request.message:
    messages.append({"role": "user", "content": chat_request.message})
```

If we send `messages` containing the context-package system message + original user task, and NO `message` field, the backend will process those messages. The system message already contains all the context, and the user message is the original task. The agent will then pick up from there.

**But wait**: The system message in the backend is always regenerated from `SYSTEM_MESSAGE_TEMPLATE`. Looking at `main_flow.py`:

```python
if not current_processing_messages or current_processing_messages[0].get("role") != "system":
    current_processing_messages.insert(0, {"role": "system", "content": system_message})
else:
    current_processing_messages[0]["content"] = system_message
```

This **replaces** the existing system message! That means the context package we seeded would be lost.

**Fix needed**: Modify `main_flow.py` to handle a "continuation" system message. Option A: detect a special marker in the system message and prepend instead of replace. Option B: use a different role for the context package (e.g., "user" with a special prefix).

**Recommended approach**: Use a special marker. If the existing system message contains `[CONTINUED FROM PREVIOUS SESSION]`, preserve it and append the standard system message after it.

```python
if not current_processing_messages or current_processing_messages[0].get("role") != "system":
    current_processing_messages.insert(0, {"role": "system", "content": system_message})
else:
    existing = current_processing_messages[0]["content"]
    if "[CONTINUED FROM PREVIOUS SESSION]" in existing:
        # Preserve the continuation context, append the standard system prompt
        current_processing_messages[0]["content"] = existing + "\n\n---\n\n" + system_message
    else:
        current_processing_messages[0]["content"] = system_message
```

### 6.3 Frontend UI Elements

Add to `App.jsx`:

1. **Sidebar conversation list**: Show parent/child relationship with indentation and a "↳ Continued from..." label
2. **Chat view**: When viewing a continued conversation, show a banner at the top:
   ```
   ⬆️ Continued from [parent conversation title]
   ```
   This is a clickable link to the parent conversation.
3. **Status indicators**: 
   - Parent conversation shows a "Continued →" badge
   - Child conversation shows a "← Continued" badge

### 6.4 Conversation Store API additions

Add an endpoint to support querying continuation chains:

```
GET /api/conversations/{id}/continuation-chain
→ Returns the full chain: parent → child → grandchild
```

---

## 7. Implementation Checklist

### Phase 1: Core Infrastructure (Backend)

- [ ] **`config.py`**: Add `CONTEXT_WINDOW_TOKENS`, `CONTEXT_WARN_THRESHOLD`, `CONTEXT_CRITICAL_THRESHOLD`, hint text constants
- [ ] **`main_flow.py`**: Add `estimate_token_count()`, `estimate_context_usage_pct()`, `_has_continuation_hint_been_shown()`
- [ ] **`main_flow.py`**: Inject hint into system message at 80%+ (one-time)
- [ ] **`main_flow.py`**: Handle `[CONTINUED FROM PREVIOUS SESSION]` marker — preserve continuation context
- [ ] **`main_flow.py`**: Detect `continue_as_new_chat` result and yield `continue_requested` status
- [ ] **`src/core_tools/continue_chat.py`**: New file — tool implementation
- [ ] **`tool_definitions.py`**: Register the new tool

### Phase 2: Proxy & Storage

- [ ] **`conversation_history/api.py`**: Handle `continue_requested` status — create child conversation, seed messages, update parent status
- [ ] **`conversation_history/conversation_store.py`**: Add `conv_type="user_chat_continued"` support if needed

### Phase 3: Frontend

- [ ] **`App.jsx`**: SSE handler for `continue_requested` status
- [ ] **`App.jsx`**: Auto-navigate to new conversation
- [ ] **`App.jsx`**: Sidebar — parent/child relationship display
- [ ] **`App.jsx`**: Chat view — continuation banner
- [ ] **`App.jsx`**: Status badges ("Continued →" / "← Continued")
- [ ] **`App.jsx`**: Handle new conversation auto-start (seeded messages)

### Phase 4: Polish

- [ ] Test with a real long conversation that hits 80%+
- [ ] Verify the hint appears exactly once
- [ ] Verify the agent can call the tool and transition works
- [ ] Verify the new agent picks up correctly
- [ ] Verify parent/child linking in sidebar
- [ ] Test multiple continuations (chain of 3+)
- [ ] Test with subagents in progress

---

## 8. Edge Cases & Risk Mitigation

| Edge Case | Handling |
|-----------|----------|
| **Agent ignores the hint** | Hint appears once, no forced action. Agent may hit context limit naturally — model will truncate/error. The user can manually start a new chat. |
| **Agent calls tool too early** | The hint only appears at 80%+, but the tool is always available in the tool list. The agent can call it any time. If called too early, the new session still works — just a minor efficiency loss. |
| **Agent calls tool at 99%** | The urgent hint variant warns strongly. If the call succeeds before context overflow, transition is clean. If context overflows mid-response, the tool call may be malformed — the backend will surface the error. |
| **Frontend disconnected during transition** | The proxy (8081) keeps the backend connection alive. The new conversation is created server-side. When the frontend reconnects, it discovers the new conversation via the store. |
| **Multiple continue_as_new_chat in one turn** | After the first call, the stream ends with `continue_requested`. The agent cannot call it again in the same turn. |
| **Continue from a continued conversation** | Fully supported — each continuation creates a new child with `parent_id` pointing to the immediate parent, forming a chain. |
| **Workspace file state** | The new conversation uses the same Docker container and session — all files, running processes, and shell state persist. This is already how "Continue" (after max_iterations) works today. |
| **Provider/model mismatch** | The new conversation uses whatever provider the frontend selects (defaults to the same as parent). Changeable in UI. |
| **Title extraction** | The continuation conversation title is auto-generated from the summary: `[Continued] {first 80 chars of summary}`. `_extract_title()` in the store handles this (it looks for the first user message). |

---

## 9. Key Design Decisions

1. **One-time hint**: Injected into the system message dynamically, flagged with a marker so it's never shown twice. This prevents the hint from wasting context space on repeated appearances.

2. **Agent autonomy**: The agent decides when and whether to use the tool. This is consistent with the existing "MAX_ITERATIONS / Continue" pattern where the agent is informed but not forced.

3. **Read-only tool**: `continue_as_new_chat` is marked read-only so it can run in parallel with other read operations. Its only side effect is writing to the conversation store.

4. **Proxy handles persistence**: The proxy (8081) already owns conversation storage. The backend signals intent via SSE status; the proxy acts on it. This maintains the existing separation of concerns.

5. **System message preservation**: The `[CONTINUED FROM PREVIOUS SESSION]` marker ensures the context package survives the system message regeneration in `main_flow.py`.

6. **Same workspace**: No new session/container — the continuation runs in the same environment, exactly like the existing "Continue after max_iterations" flow.
