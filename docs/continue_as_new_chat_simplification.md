# "Continue as New Chat" — Simplification

## What changes

Remove the **"Seeds messages: context package as system msg + original user task"** behavior. The continued conversation should be a normal chat — the agent's prompt IS the sole user message, with a note it came from another agent. No user involvement (no Start button, no `user_chat_continued` type, no special view mode).

## How it works after change

```
Agent calls continue_as_new_chat(prompt="...")
        │
        ▼
   main_flow.py — no changes (already ends loop when tool called)
        │
        ▼
   Proxy (8081)
   - Detects tool call in raw_messages
   - Creates child conversation (conv_type="user_chat", not "user_chat_continued")
   - Seeds ONE user message: "[Continued from previous agent session]\n\n<prompt>"
   - SIMULATES USER SEND: immediately POSTs the seeded messages to /api/chat
     (exactly like a user opening a new chat, typing that message, and hitting Send)
   - Marks parent status "continued"
   - Annotates events with new_conversation_id
        │
        ▼
   Frontend
   - Sees new_conversation_id → auto-navigates to new conversation
   - Conversation is already running (proxy already POSTed it)
   - Resumes the live stream — no Start button, no special view
   - Looks exactly like any other running chat
```

## Files to change

### 1. `ThinkWithTool/conversation_history/api.py`

#### 1a. Remove `_find_original_user_task()` (lines 182-191)

Delete the entire function. No longer needed — we don't look up the original task.

#### 1b. Remove `_build_context_package()` (lines 194-196)

Delete the entire function. The `[CONTINUED FROM PREVIOUS SESSION]` marker in system messages is gone.

#### 1c. Replace the continuation block (lines 248-272)

**Current (lines 248-272):**
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
            provider_id=stream.provider,
        )
        # Find the original user message from the parent
        original_task = _find_original_user_task(cid)
        store.save_messages(new_cid, [
            {"role": "system", "content": context_pkg},
            {"role": "user", "content": original_task},
        ])
        store.save_frontend_messages(new_cid, [
            {"role": "system", "content": context_pkg},
            {"role": "user", "content": original_task},
        ])
        store.update_status(cid, "continued")
        stream.new_conversation_id = new_cid
        logger.info(f"[proxy] Created continuation {new_cid[:8]}... from {cid[:8]}...")
```

**Replace with:**
```python
if not stream.new_conversation_id:
    args = _scan_for_continuation(edata.get("raw_messages", []))
    if args:
        prompt = args.get("prompt", "")
        new_cid = str(uuid.uuid4())

        # Build the user message: just the agent's prompt with a note
        user_msg = f"[Continued from previous agent session]\n\n{prompt}"

        store.create_conversation(
            conversation_id=new_cid,
            parent_id=cid,
            conv_type="user_chat",          # ← normal chat, not "user_chat_continued"
            provider_id=stream.provider,
        )
        store.save_messages(new_cid, [
            {"role": "user", "content": user_msg},
        ])
        store.save_frontend_messages(new_cid, [
            {"role": "user", "content": user_msg},
        ])
        store.update_status(cid, "continued")
        stream.new_conversation_id = new_cid

        # Simulate user pressing Send — POST to backend immediately
        asyncio.create_task(_start_continuation(new_cid, stream.provider, user_msg))

        logger.info(f"[proxy] Created continuation {new_cid[:8]}... from {cid[:8]}... — auto-started")
```

#### 1d. Add `_start_continuation()` helper

New function that simulates a user sending:

```python
async def _start_continuation(new_cid: str, provider_id: str, user_msg: str):
    """
    Simulate a user opening a new chat, typing the continuation message,
    and pressing Send.  POSTs to the backend via the normal proxy flow.
    """
    body = {
        "conversation_id": new_cid,
        "message": user_msg,
        "provider": provider_id,
    }
    conv_type = "user_chat"
    parent_id = None  # The child already has parent set in store; this is for the stream object

    stream = ActiveStream(
        conversation_id=new_cid,
        conv_type=conv_type,
        parent_id=parent_id,
        provider=provider_id,
    )

    async with _streams_lock:
        active_streams[new_cid] = stream

    stream.task = asyncio.create_task(_proxy_backend_stream(stream, body))

    logger.info(f"[proxy] Continuation {new_cid[:8]}... auto-started")
```

### 2. `ThinkWithTool/src/main_flow.py`

Remove the `[CONTINUED FROM PREVIOUS SESSION]` marker handling (lines 308-316).

**Current (lines 308-316):**
```python
    # Preserve [CONTINUED FROM PREVIOUS SESSION] context package if it exists.
    if not current_processing_messages or current_processing_messages[0].get("role") != "system":
        current_processing_messages.insert(0, {"role": "system", "content": system_message})
    else:
        existing = current_processing_messages[0]["content"]
        if "[CONTINUED FROM PREVIOUS SESSION]" in existing:
            current_processing_messages[0]["content"] = existing + "\n\n---\n\n" + system_message
        else:
            current_processing_messages[0]["content"] = system_message
```

**Replace with (standard handling — no marker logic):**
```python
    if not current_processing_messages or current_processing_messages[0].get("role") != "system":
        current_processing_messages.insert(0, {"role": "system", "content": system_message})
    else:
        current_processing_messages[0]["content"] = system_message
```

### 3. `ThinkWithTool/frontend/src/App.jsx`

#### 3a. Remove `continued` view mode from `handleLoadConversation`

**Current (lines 947-968):**
```javascript
const isSubagent = conv.type === 'subagent'
const isContinued = conv.type === 'user_chat_continued'

setConversationId(targetConversationId)
setRawMessages(conv.messages || [])
setMessages(conv.frontend_messages || [])
setCanContinue(!isSubagent && !isContinued && conv.status === 'max_iterations_reached')
setIsStreaming(false)
setActiveConvoWarning(false)
setEditedFiles([])
setClosedFiles(new Set())
setViewMode(isSubagent ? 'subagent' : (isContinued ? 'continued' : 'main'))
setParentConversationId((isSubagent || isContinued) ? conv.parent_id : null)

// Restore draft input for this conversation (not for subagents or continuations)
const draft = (isSubagent || isContinued) ? '' : (draftInputsRef.current.get(targetConversationId) || '')
setInputValue(draft)

// Auto-launch continued conversations immediately (like normal chat)
if (isContinued) {
  // Small delay to let state settle, then auto-start
  setTimeout(() => handleStartContinued(), 300)
}
```

**Replace with:**
```javascript
const isSubagent = conv.type === 'subagent'

setConversationId(targetConversationId)
setRawMessages(conv.messages || [])
setMessages(conv.frontend_messages || [])
setCanContinue(!isSubagent && conv.status === 'max_iterations_reached')
setIsStreaming(false)
setActiveConvoWarning(false)
setEditedFiles([])
setClosedFiles(new Set())
setViewMode(isSubagent ? 'subagent' : 'main')
setParentConversationId(isSubagent ? conv.parent_id : null)

// Restore draft input
const draft = isSubagent ? '' : (draftInputsRef.current.get(targetConversationId) || '')
setInputValue(draft)
```

#### 3b. Remove `handleStartContinued` function (lines 1002-1061)

Delete the entire `handleStartContinued` function. No longer needed — the proxy auto-starts the conversation server-side.

#### 3c. Remove continuation view bar UI (lines 1376-1399)

Delete this entire block:
```jsx
{/* Continuation view bar */}
{viewMode === 'continued' && (
  <div className="subagent-view-bar continued-view-bar">
    ...
  </div>
)}
```

#### 3d. Fix input-area hiding (line 1402)

**Current:**
```jsx
{viewMode !== 'subagent' && viewMode !== 'continued' && (
```

**Replace with:**
```jsx
{viewMode !== 'subagent' && (
```

### 4. Docs to update

- `ThinkWithTool/docs/continue_as_new_chat_plan.md` — remove line 32 (`Seeds messages: context package as system msg + original user task`), update section 6, update frontend section
- `ThinkWithTool/docs/continue_as_new_chat_implementation.md` — same changes to reflect new behavior

---

## Summary of what gets deleted

| File | What's removed |
|------|---------------|
| `api.py` | `_find_original_user_task()` function |
| `api.py` | `_build_context_package()` function |
| `api.py` | System + user message seeding → single user message seeding |
| `api.py` | `conv_type="user_chat_continued"` → `conv_type="user_chat"` |
| `main_flow.py` | `[CONTINUED FROM PREVIOUS SESSION]` marker handling |
| `App.jsx` | `isContinued` variable and all its usages |
| `App.jsx` | `handleStartContinued()` function (entire) |
| `App.jsx` | Continuation view bar JSX block |
| `App.jsx` | `viewMode === 'continued'` from input-area condition |
| Both docs | References to seeding original user task, system message context package, Start button |

## What gets added

| File | Addition |
|------|----------|
| `api.py` | `_start_continuation()` — server-side auto-start that simulates user Send |
