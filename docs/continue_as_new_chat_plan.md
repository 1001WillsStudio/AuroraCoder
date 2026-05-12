# "Continue as a New Chat" — Feature Design Plan

## Overview

When an agent's context window approaches exhaustion (~80%+ used), a **one-time hint** appears in the system prompt telling the agent it can call a `continue_as_new_chat` tool. The agent has full autonomy to decide:

1. **Whether** to use it (optional — never forced)
2. **When** to use it (strategically — after wrapping up a logical unit)
3. **What** information to pass forward to the new session

The tool creates a **brand-new conversation** seeded with a structured context package, so the new agent picks up where the old one left off — with a clean, empty context window.

---

## 1. Architecture & Data Flow

**Key principle**: `continue_as_new_chat` works like `subagent` — the tool actually POSTs
to the conversation server, starts the new agent loop, streams the SSE response, and
reports the result back to the current agent. Success → agent ends naturally. Error →
agent can retry. The proxy handles persistence; `main_flow.py` sees just another tool call.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CURRENT SESSION                              │
│                                                                     │
│  1. main_flow.py estimates context usage each iteration             │
│  2. At ≥80% → one-time hint injected into system message            │
│  3. Agent sees hint, continues working until ready                  │
│  4. Agent calls continue_as_new_chat(summary, files, tasks, ctx)    │
│     → Tool returns JSON string (just like any other tool)           │
│  5. Agent finishes naturally, loop ends with normal "completed"     │
│  6. Proxy (8081) notices the signal in the tool result within       │
│     the SSE stream — creates child conversation, seeds messages,    │
│     marks parent "continued", annotates events with new_convo_id    │
│  7. Frontend sees new_conversation_id, auto-navigates               │
│                                                                     │
│  ⚠️ src/ changes NONE for the tool execution flow.                 │
│     The loop is completely oblivious to continuation.               │
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
     │  (just another tool call, loop       │                              │
     │   continues as normal)               │                              │
     │                                      │                              │
     │  messages event (status: running)    │                              │
     │  [tool result contains JSON signal]  │  Proxy scans raw_messages,   │
     ├─────────────────────────────────────►│  detects continue_as_new_chat │
     │                                      │  signal → creates child convo │
     │                                      │  seeds messages, marks parent │
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
     │                                      │  Frontend navigates to       │
     │                                      │  new conversation             │
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

## 3. Dynamic Tool Availability (No Hint Needed)

The `continue_as_new_chat` tool is **hidden from the agent's tool list** until context
usage crosses 80%. The tool appearing IS the notification — no verbose hint required.

### Mechanism

In `main_flow.py`, the tool list is filtered each iteration based on context usage:

```python
def _filter_tools_by_context(tools: list, messages: list) -> list:
    """Remove continue_as_new_chat if context is below threshold."""
    usage_pct = estimate_context_usage_pct(messages)
    if usage_pct < CONTEXT_WARN_THRESHOLD:
        return [t for t in tools if t["function"]["name"] != "continue_as_new_chat"]
    return tools  # Tool appears at ≥80%
```

And when the tool first appears, a brief one-liner is appended to the system message
(only once, detected by a marker):

```python
    # --- Maybe inject a brief notification when the tool first appears ---
    if not _has_continuation_notice_been_shown(current_processing_messages):
        tools = _filter_tools_by_context(tools, current_processing_messages, with_notice=True)
```

Where `with_notice=True` returns both the filtered tools and whether the tool
just became available (for the one-liner injection):

```
⚠️ `continue_as_new_chat` is now available in your tool list — you are at ~80% context.
```

That's it. No verbose hint blocks. The tool simply didn't exist before — now it does.
The agent's description of the tool itself explains what it does and when to use it.
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

**Architecture note**: This tool works like `subagent` — it actually POSTs to the
conversation server (8081), starts the new agent loop, streams the SSE response,
and returns the result. The current agent gets the outcome directly:

- **Success**: returns "✅ Continuation completed. The new session finished the task."
  The agent can end its turn naturally. The frontend auto-navigates to the new convo.
- **Error**: returns "❌ Continuation failed: [error details]". The agent can fix
  the issue and retry — or try a different approach entirely.

The proxy handles conversation creation (parent_id linking) and persistence.
`main_flow.py` needs NO changes — it just sees another tool result.

```python
"""
continue_as_new_chat tool — starts a new agent loop with fresh context,
streams the result, and reports back (success or error) to the current agent.
"""

import json
import logging
import os
import uuid

import requests

from ..config import SUBAGENT_MAX_RESULT_CHARS

logger = logging.getLogger(__name__)

CONVO_SERVER_URL = os.environ.get("CONVO_SERVER_URL", "http://localhost:8081")

# Reuse the subagent's cancellation infrastructure
from .subagent import _active_subagents, _active_lock


def continue_as_new_chat(
    summary: str,
    pending_tasks: str,
    key_files: list = None,
    important_context: str = "",
) -> str:
    """
    Start a new agent loop seeded with context, wait for the result.

    Posts to the conversation server which creates the child conversation,
    forwards to the backend, and streams the SSE response back. The current
    agent gets the final result.

    Returns:
        Success message if the new agent completed the task.
        Error message if it failed — the agent can retry with different context.
    """
    from ..code_tools.file_operations import _current_conversation_id as parent_cid

    if key_files is None:
        key_files = []

    new_cid = str(uuid.uuid4())

    context_package = _build_context_package(
        summary=summary,
        key_files=key_files,
        pending_tasks=pending_tasks,
        important_context=important_context,
    )

    # Request body: seeded messages (no "message" field — backend processes
    # the pre-built message list which contains the context package + user task)
    body = {
        "conversation_id": new_cid,
        "messages": [
            {"role": "system", "content": context_package},
            {"role": "user", "content": "[ORIGINAL TASK REPLAYED BY CONTINUATION]"},
        ],
        "conv_type": "user_chat_continued",
        "parent_id": parent_cid,
    }

    cancel_event = threading.Event()
    with _active_lock:
        _active_subagents[new_cid] = (cancel_event, None)

    try:
        resp = requests.post(
            f"{CONVO_SERVER_URL}/api/chat",
            json=body,
            stream=True,
            timeout=None,
        )

        with _active_lock:
            if new_cid in _active_subagents:
                _active_subagents[new_cid] = (cancel_event, resp)

        if resp.status_code != 200:
            return (
                f"❌ Continuation failed: conversation server returned "
                f"{resp.status_code}: {resp.text[:500]}\n\n"
                f"You may want to retry with different context or handle the "
                f"remaining tasks yourself."
            )

        final_text = ""
        final_status = "unknown"

        for line in resp.iter_lines(decode_unicode=True):
            if cancel_event.is_set():
                break
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue

            if data.get("status"):
                final_status = data["status"]

            for msg in reversed(data.get("raw_messages", [])):
                if msg.get("role") == "assistant" and msg.get("content"):
                    final_text = msg["content"]
                    break

    except Exception as e:
        logger.exception(f"Continuation HTTP error for {new_cid[:8]}")
        return (
            f"❌ Continuation error: {type(e).__name__}: {e}\n\n"
            f"The new session could not be started. You may want to:\n"
            f"- Retry with different context\n"
            f"- Handle the remaining tasks yourself\n"
            f"- Ask the user for guidance"
        )

    finally:
        with _active_lock:
            _active_subagents.pop(new_cid, None)

    if cancel_event.is_set():
        return "[Continuation was stopped by user.]"

    if final_status == "completed":
        if final_text:
            if len(final_text) > SUBAGENT_MAX_RESULT_CHARS:
                final_text = final_text[:SUBAGENT_MAX_RESULT_CHARS] + "\n... [truncated]"
            return (
                f"✅ Continuation succeeded. The new session completed the task.\n\n"
                f"Result:\n{final_text}\n\n"
                f"New conversation ID: {new_cid[:8]}..."
            )
        return (
            f"✅ Continuation succeeded. The new session completed the task "
            f"(no text summary produced). New conversation ID: {new_cid[:8]}..."
        )

    # Non-completed status (error, max_iterations, interrupted)
    return (
        f"❌ Continuation ended with status '{final_status}'.\n\n"
        f"Last output:\n{final_text[:2000] if final_text else '(none)'}\n\n"
        f"You may want to retry with adjusted context or handle the "
        f"remaining tasks yourself. New conversation ID: {new_cid[:8]}..."
    )


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

```python
# Context continuation
CONTEXT_WINDOW_TOKENS = 128_000          # Default for most models
CONTEXT_WARN_THRESHOLD = 0.80            # 80% — tool becomes available

# One-liner notice when tool first appears (injected once, detected by marker)
_CONTINUATION_NOTICE_MARKER = "[CONTEXT CONTINUATION TOOL AVAILABLE]"
CONTINUATION_NOTICE = "⚠️ `continue_as_new_chat` is now available in your tool list — you are at ~80% context."
```

### 5.2 `src/main_flow.py`

**Only two changes** — both are pure message-level operations, no continuation awareness:

#### A) Context estimation + hint injection

```python
from .config import (
    # ... existing imports ...
    CONTEXT_WINDOW_TOKENS, CONTEXT_WARN_THRESHOLD, CONTEXT_CRITICAL_THRESHOLD,
    CONTINUATION_HINT_NORMAL, CONTINUATION_HINT_URGENT,
    _CONTINUATION_HINT_MARKER,
)

def estimate_token_count(messages: list) -> int:
    serialized = json.dumps(messages, ensure_ascii=False, default=str)
    return len(serialized) // 2.5

def estimate_context_usage_pct(messages: list, context_window: int = None) -> float:
    if context_window is None:
        context_window = CONTEXT_WINDOW_TOKENS
    return estimate_token_count(messages) / context_window

def _has_continuation_hint_been_shown(messages: list) -> bool:
    for msg in messages:
        if msg.get("role") == "system" and _CONTINUATION_HINT_MARKER in msg.get("content", ""):
            return True
    return False
```

After the system message is set (~line 289), add:

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

#### B) System message preservation for continued sessions

When the incoming system message contains `[CONTINUED FROM PREVIOUS SESSION]`, preserve it
instead of replacing it:

```python
    if not current_processing_messages or current_processing_messages[0].get("role") != "system":
        current_processing_messages.insert(0, {"role": "system", "content": system_message})
    else:
        existing = current_processing_messages[0]["content"]
        if "[CONTINUED FROM PREVIOUS SESSION]" in existing:
            # Preserve the continuation context, append the standard prompt
            current_processing_messages[0]["content"] = existing + "\n\n---\n\n" + system_message
        else:
            current_processing_messages[0]["content"] = system_message
```

**That's it for `main_flow.py`**. No status detection, no special yielding, no early return.
The `continue_as_new_chat` tool result flows through the loop just like any other tool.
The proxy (8081) handles everything else.

### 5.3 `src/tool_definitions.py`

- Add `continue_as_new_chat` to `NATIVE_TOOL_DEFINITIONS`
- Add `"continue_as_new_chat"` to `READ_ONLY_TOOLS`
- Add `"continue_as_new_chat": continue_as_new_chat` to `TOOL_FUNCTION_MAP`
- Import `from ..core_tools.continue_chat import continue_as_new_chat`

### 5.4 `src/web_api/app.py`

No changes needed. The backend remains completely stateless. The tool returns a string
like any other. The proxy does all the work.

### 5.5 `conversation_history/api.py` (Proxy — where the magic happens)

The proxy scans `raw_messages` in each SSE event for the `continue_as_new_chat` tool
result. When it finds the JSON signal (`"status": "continue_requested"`), it:

1. Extracts `new_conversation_id` and `context_package`
2. Creates the child conversation via `store.create_conversation(parent_id=cid)`
3. Seeds messages: context package as system, original user task as first user message
4. Marks parent conversation as `"continued"`
5. **Annotates** all subsequently-forwarded SSE events with `new_conversation_id`
   so the frontend knows where to navigate

```python
# In _proxy_backend_stream(), after parsing each SSE event:

# Scan for continuation signal in raw messages
continuation_signal = _scan_for_continuation(edata.get("raw_messages", []))
if continuation_signal:
    new_cid = continuation_signal["new_conversation_id"]
    context_pkg = continuation_signal["context_package"]
    
    store.create_conversation(
        conversation_id=new_cid,
        parent_id=cid,
        conv_type="user_chat_continued",
        title=_extract_title_from_context(context_pkg),
    )
    original_task = _find_original_user_task(cid)
    store.save_messages(new_cid, [
        {"role": "system", "content": context_pkg},
        {"role": "user", "content": original_task},
    ])
    store.update_status(cid, "continued")
    
    # Attach new_conversation_id to this stream so subsequent events
    # forwarded to frontend include it
    stream.new_conversation_id = new_cid


def _scan_for_continuation(raw_messages: list) -> dict | None:
    """Scan tool results in raw_messages for the continue_as_new_chat JSON signal."""
    for msg in raw_messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if '"continue_as_new_chat"' in content or '"continue_requested"' in content:
            try:
                parsed = json.loads(content)
                if parsed.get("status") == "continue_requested":
                    return parsed
            except json.JSONDecodeError:
                pass
    return None
```

When forwarding events to frontend subscribers, if `stream.new_conversation_id` is set,
annotate the event data with it:

```python
if stream.new_conversation_id:
    edata["new_conversation_id"] = stream.new_conversation_id
```

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
- [ ] **`src/core_tools/continue_chat.py`**: New file — pure signal tool implementation
- [ ] **`tool_definitions.py`**: Register the new tool

### Phase 2: Proxy & Storage

- [ ] **`conversation_history/api.py`**: Add `_scan_for_continuation()` — scan raw_messages for the signal; create child convo, seed messages, mark parent "continued", annotate events with `new_conversation_id`
- [ ] **`conversation_history/conversation_store.py`**: Add `conv_type="user_chat_continued"` support if needed

### Phase 3: Frontend

- [ ] **`App.jsx`**: SSE handler for `new_conversation_id` in events
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
| **Multiple continue_as_new_chat in one turn** | The agent can technically call it multiple times, but the proxy only acts on the first signal it detects. Subsequent calls are just JSON in tool results. |
| **Continue from a continued conversation** | Fully supported — each continuation creates a new child with `parent_id` pointing to the immediate parent, forming a chain. |
| **Workspace file state** | The new conversation uses the same Docker container and session — all files, running processes, and shell state persist. This is already how "Continue" (after max_iterations) works today. |
| **Provider/model mismatch** | The new conversation uses whatever provider the frontend selects (defaults to the same as parent). Changeable in UI. |
| **Title extraction** | The continuation conversation title is auto-generated from the summary: `[Continued] {first 80 chars of summary}`. `_extract_title()` in the store handles this (it looks for the first user message). |

---

## 9. Key Design Decisions

1. **Tool hidden until needed**: `continue_as_new_chat` is filtered out of the tool list until context hits 80%. The tool appearing IS the notification — a brief one-liner in the system message. No verbose hint blocks, no premature calls. Saves context space and keeps the agent focused.

2. **Agent autonomy**: The agent decides when and whether to use the tool. This is consistent with the existing "MAX_ITERATIONS / Continue" pattern where the agent is informed but not forced.

3. **Subagent-like execution**: `continue_as_new_chat` actually POSTs to the conversation server, starts the new agent loop, streams the SSE response, and reports back. Success → agent ends. Error → agent retries. Like `run_subagent` but with full tools and context seeding instead of a plain task string.

4. **Agent-loop-oblivious**: `main_flow.py` has zero awareness of continuation. The `continue_as_new_chat` tool is just another function returning a string. The proxy (8081) scans `raw_messages` in SSE events, detects the JSON signal in the tool result, and handles all persistence. The loop never sees a special status, never returns early.

5. **System message preservation**: The `[CONTINUED FROM PREVIOUS SESSION]` marker ensures the context package survives the system message regeneration in `main_flow.py`.

6. **Same workspace**: No new session/container — the continuation runs in the same environment, exactly like the existing "Continue after max_iterations" flow.
