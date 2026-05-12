# Conversation History System Design

## Motivation

The agent currently has no centralized conversation storage. The frontend owns all message state via `rawMessages`, the backend is stateless per request, and there's a separate `RECORDING_FILE` for logging. This creates tight coupling and limits functionality.

A conversation system becomes the **single source of truth** for all message history, replacing both the frontend's in-memory state management and the backend's ad-hoc JSONL logging.

---

## Architecture

### Role of the Conversation System

The conversation system is the **relay layer** between the agent backend and the frontend. It:

1. **Forwards SSE from the agent to the frontend** in real-time (thinking, content, tool status)
2. **Tracks the in-flight state** as streaming progresses
3. **Persists to storage** when a turn ends or is interrupted — by writing the **full message list** provided by the backend

### Why Full Replacement, Not Append

The backend's agent loop mutates past messages in-place during execution:
- `clean_previous_interpreter_blocks()` strips old code interpreter blocks from earlier tool results
- Future context compaction will rewrite/compress old tool results
- Any context management layer may modify historical messages

Because of these retroactive mutations, the conversation system cannot use an append-only model. Instead, the backend provides the **complete current message list** on every SSE event (as it does today). When a turn completes, the conversation system **replaces the stored message list wholesale** with the latest version from the backend.

### Data Flow

```
Frontend                 Conversation System              Agent (main_flow)
   |                           |                              |
   |-- POST /api/chat -------->|                              |
   |   (message, conv_id?)     |                              |
   |                           |-- create/resume conv         |
   |<-- conv_id (immediately)--|                              |
   |                           |                              |
   |                           |-- start agent loop --------->|
   |                           |                              |
   |                           |<-- full messages[] + status -|
   |<-- SSE: relay to frontend-|   (keeps latest in memory)   |
   |   (full message list,     |                              |
   |    streamed as today)     |                              |
   |                           |                              |
   |                           |<-- turn complete (status     |
   |                           |    changes or tools done)    |
   |                           |-- persist: overwrite stored  |
   |                           |   messages with latest copy  |
   |                           |                              |
   |                           |<-- more turns... ------------|
   |                           |-- persist after each turn    |
   |                           |                              |
   |                           |<-- done / error / interrupt -|
   |<-- SSE: done -------------|-- final persist + status     |
   |                           |                              |
   |-- GET /api/conversations/{id} -------->|                 |
   |<-- stored messages + metadata ---------|                 |
```

### Key Principles

1. **Full replacement on persist**: The backend provides the complete message list. The store overwrites what it has. This naturally handles in-place mutations, compaction, and any future context management — the store always reflects the backend's current truth.

2. **Persist per round, not per turn**: A "round" is the full cycle: user sends a message → agent runs N iterations (model calls + tool executions) → agent produces a final response or hits max_iterations. The conversation is only persisted when a round ends — not after every individual tool iteration. This keeps writes infrequent and only stores stable, presentable state.

3. **Interrupts also persist**: If the user stops or the connection drops mid-round, the conversation system persists the last received message list as-is. This is the only mid-round persist.

4. **SSE relay is unchanged**: The frontend still receives the full message list on every event, exactly as today. The conversation system is transparent — it just also writes to disk at the right moments.

5. **Frontend reads from the store**: For history, resume, and subagent traces, the frontend fetches from the conversation API.

---

## Key Distinction: Sessions vs Conversations

| Concept | What it is | Scope |
|---------|-----------|-------|
| **Session** | Execution environment — conda env, workspace dir, shell process | Exists (`session_manager.py`) |
| **Conversation** | Message history — user messages, assistant responses, tool calls/results | This system |

A session can have many conversations. A conversation can spawn child conversations (subagents). These are orthogonal.

---

## Conversation Store Design

### Data Model

```python
Conversation:
    id: str                    # UUID
    parent_id: str | None      # null for user chats, conv_id for subagents
    session_id: str | None     # link to execution session
    type: "user_chat" | "subagent"
    status: "running" | "completed" | "error" | "interrupted" | "max_iterations_reached"
    provider_id: str
    title: str                 # first user message, truncated
    created_at: datetime
    updated_at: datetime
    messages: list[Message]    # the full message list, replaced on each persist
```

No per-turn structure in storage. The stored messages list is the flat list of all messages as the backend sees them — including any mutations, compaction, or rewriting the backend has done.

### Core Operations

```
create_conversation(parent_id=None, session_id=None, provider_id=None, type="user_chat") -> conversation_id
save_messages(conversation_id, messages: list) -> None       # full replacement
update_status(conversation_id, status) -> None
get_conversation(conversation_id) -> {metadata, messages}
get_messages(conversation_id) -> list[Message]               # for feeding back to the model
list_conversations(type=None, parent_id=None, session_id=None) -> [summary, ...]
```

Note: `save_messages` replaces `append_turn`. It's simpler — just overwrite the whole list.

### Storage

**Phase 1 — JSON files**

```
data/conversations/
    index.json                          # metadata for all conversations
    {conversation_id}.json              # full message list (overwritten on each save)
```

Not JSONL anymore — since we're doing full replacement, a single JSON file per conversation is simpler. Each save overwrites the file atomically (write to temp + rename).

**Phase 2 — SQLite (when needed)**

Better for: full-text search, pagination, multi-user, structured queries. Same API, different backend.

---

## What Gets Stored When

A **round** = user message → agent loop (N iterations of model + tools) → final response.

| Event | Stored? | What happens |
|-------|---------|--------------|
| Streaming delta (thinking/content) | No | Relay to frontend, keep latest messages in memory |
| Tool call starts / finishes | No | Relay status to frontend |
| Iteration completes (tools done, next model call) | No | Agent continues, conversation system just tracks latest messages |
| **Round ends** (status: completed) | **Yes** | Save latest messages list to storage |
| **Round ends** (status: max_iterations_reached) | **Yes** | Save latest messages list to storage |
| **Round ends** (status: error) | **Yes** | Save latest messages list to storage |
| **User interrupts / disconnect** | **Yes** | Save last received messages list to storage |

---

## API Endpoints

```
POST /api/chat
    → creates or resumes a conversation
    → returns conversation_id immediately in response headers
    → conversation system relays SSE from agent to frontend
    → persists messages when turns complete

GET  /api/conversations
    → list conversations (filters: type, session_id, parent_id)
    → returns metadata only (no messages), sorted by updated_at

GET  /api/conversations/{id}
    → full conversation: metadata + messages
    → used by frontend to load a conversation (history, subagent trace, resume)

GET  /api/conversations/{id}/children
    → list child conversations (subagents spawned by this conversation)
```

---

## Integration Points

### `main_flow.py` (agent core)

**No changes needed.** The agent already yields `{"messages": [...], "status": "..."}` on every event. The conversation system consumes this from outside. The agent stays pure.

### `app.py` → Conversation System (the relay layer)

The current `stream_chat_response` wraps the agent generator and pushes events to SSE. The conversation system extends this:

```
Current:   app.py → run_generator() → agent loop → queue → SSE
Proposed:  app.py → run_generator() → agent loop → conversation system → queue → SSE
                                                          ↓
                                                   storage (on turn end)
```

The conversation system receives every `{"messages": [...], "status": "..."}` event. It:
1. Keeps the latest `messages` in memory
2. Relays to the SSE queue (unchanged behavior)
3. When `status` changes from `"running"` to a terminal state (`completed`, `error`, `max_iterations_reached`) or the stream is interrupted, writes `messages` to storage — once per round

### `subagent.py`

`run_subagent` creates a child conversation. The subagent's loop produces events the same way. The conversation system (or a simpler inline version for subagents) persists the child's final messages. The parent's tool result includes the child `conversation_id`.

### Frontend

- **Live streaming**: Unchanged — SSE relayed through the conversation system
- **After stream ends**: Frontend can fetch `GET /api/conversations/{id}` for the persisted messages
- **Continue**: Send `conversation_id` — backend loads messages from the store
- **Subagent traces**: Fetch child conversation on demand
- **History sidebar**: List past conversations, click to load

---

## Migration Path

1. Build `src/conversation_store.py` — storage layer (create/save/get/list with JSON files)
2. Build conversation relay in `app.py` — wrap agent output, relay to SSE, persist at turn boundaries
3. Wire `run_subagent` — create child conversations, persist on completion
4. Add `/api/conversations/*` REST endpoints
5. Frontend: fetch persisted conversation on stream end
6. Frontend: subagent trace viewer, history sidebar
7. Remove `RECORDING_FILE` and `rawMessages` passthrough

Steps 1-4 are backend-only, no breaking changes. Steps 5-7 are incremental frontend migration.

---

## Open Questions

1. **Retention policy**: Max conversations? Max age? Manual deletion only? For Docker single-user, keeping everything is fine. Add cleanup when storage becomes a concern.
2. **Size limits**: Full message lists can be large (many iterations with big tool results). The file will be the size of the backend's working message list. This is the same data the backend already holds in memory, so disk usage mirrors memory usage — acceptable.
3. **Continue flow**: When the user clicks "Continue", should the backend reload messages from the store (cleanest) or should the frontend still send them (backward-compatible)? Store-based resume is the end goal, but supporting both during migration makes sense.
4. **Atomic writes**: Full replacement means writing the entire file each time. Use write-to-temp + rename to avoid corruption if the process crashes mid-write.
5. **Subagent relay**: Subagents run inside a tool call (blocking). Their events don't go through the main SSE relay. For now, the subagent's conversation is persisted on completion only. Live subagent progress streaming is a future enhancement.
