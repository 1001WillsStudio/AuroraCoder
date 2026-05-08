# Context Compression Design

## Goal

Keep conversations within context windows without the frontend knowing. Reuse partial compression state via the middleware. The agent has full, surgical control over its own context — choosing *what* to compress, *how* to compress it, and *what to preserve*.

---

## Core Idea: Agent-Editable Context

The agent's message history is a list of items it can see. We make that list **editable** — the agent can inspect what's consuming space and selectively compress specific items by reference, choosing from multiple compression strategies.

---

## Compressible Item Types

Every compressible item gets a sequential **context ID** (`ctx-1`, `ctx-2`, ...) embedded in the content so the agent can reference it.

| Type | Where it lives | Example |
|------|---------------|---------|
| **Tool result** | `role: "tool"` message content | read_file output, grep matches, terminal output, web results |
| **Reasoning** | `reasoning_content` / `thinking` field on assistant message | DeepSeek thinking blocks (often 5k-15k chars) |
| **Assistant output** | `content` field on `role: "assistant"` message | Long explanations, code reviews, analysis text |

All three can grow large and become stale as the conversation progresses.

---

## How the Agent Sees Its Context

Items are tagged with their ctx-ID as they're produced:

```
Tool result:
  [ctx-3]
  File: src/main_flow.py (320 lines)
    1| import json
    2| import copy
    ...

Reasoning (visible in the reasoning field):
  [ctx-5 reasoning]
  Let me think about the user's request step by step...
  First, I need to understand the data flow...
  (3.2k tokens)

Assistant output:
  [ctx-6 output]
  Here's the analysis of the architecture. The system has three layers...
  (1.8k tokens)
```

---

## Compression Modes

The agent chooses **how** to compress each item:

### Mode 1: `drop` — Full removal, minimal placeholder

Replaces content with a short marker. Maximum space savings, zero preserved information.

```
Before: "[ctx-3]\nFile: src/main_flow.py (320 lines)\n  1| import json\n..."
After:  "[ctx-3 dropped: read_file — src/main_flow.py]"
```

Best for: stale file reads that were superseded by a re-read, old grep results after the agent already acted on them, reasoning from early turns.

### Mode 2: `summarize` — Replace with agent-written summary

The agent writes a summary of what matters from the item. Preserves key information at a fraction of the size.

```
Before: "[ctx-7]\n<4.5k tokens of grep output across 40 files>"
After:  "[ctx-7 summarized: grep 'handleSubmit']\nFound in 3 key files:
         App.jsx:145 (main handler), api.js:89 (submit call), Form.jsx:67 (validation).
         Other 37 files were test/config with no relevant matches."
```

Best for: large tool outputs where specific details still matter, reasoning blocks where the conclusion is useful, long assistant explanations that the agent may need to reference.

---

## Two Tools

### 1. `context_status` — See what's consuming space

```python
{
    "name": "context_status",
    "description": "Show all context items (tool results, reasoning, assistant outputs) with IDs, types, sizes, and status. Use to decide what to compress."
}
```

Example output (reflects ReACT loop — reasoning before every tool call batch):
```
Context usage: ~32.1k tokens (of ~64k window)

 ID      Type       Status   Size     Source
 ── iteration 1 ──
 ctx-1   reasoning  open     2.3k tk  "Need to read config and main_flow..."
 ctx-2   tool       open     1.2k tk  read_file(src/config.py)
 ctx-3   tool       open     3.1k tk  read_file(src/main_flow.py)
 ── iteration 2 ──
 ctx-4   reasoning  open     1.8k tk  "Config shows MAX_TOKENS=8192, now grep for usages..."
 ctx-5   tool       open     0.8k tk  grep("MAX_TOKENS")
 ── iteration 3 ──
 ctx-6   reasoning  open     2.1k tk  "Found 3 usages, need to edit main_flow..."
 ctx-7   tool       open     0.2k tk  str_replace(src/main_flow.py)
 ── iteration 4 ──
 ctx-8   reasoning  open     3.5k tk  "Edit done, now search for handleSubmit..."
 ctx-9   tool       open     4.5k tk  grep("handleSubmit") — 40 files
 ── iteration 5 ──
 ctx-10  reasoning  open     2.8k tk  "Found in 3 key files, need to run tests..."
 ctx-11  tool       open     3.8k tk  run_terminal("npm test") — 200 lines
 ── iteration 6 ──
 ctx-12  reasoning  open     1.9k tk  "3 tests failed, re-read config to check..."
 ctx-13  tool       open     1.3k tk  read_file(src/config.py) ← re-read of ctx-2
 ── iteration 7 ──
 ctx-14  reasoning  open     2.0k tk  "Found the issue, fixing assertion format..."
 ctx-15  output     open     0.8k tk  "I've updated the file and identified the test failures..."

Open: 15 | Compressed: 0 | Total: ~32.1k tokens
```

The pattern is clear: reasoning accumulates fast (one per iteration), and many early reasoning blocks become irrelevant once the agent has moved past that decision point.

### 2. `close_context` — Compress specific items

```python
{
    "name": "close_context",
    "description": "Compress context items to free space. Include a brief 'reason' explaining why you are compressing — this replaces the reasoning block for this tool call so no context is wasted on meta-reasoning.",
    "parameters": {
        "reason": {
            "type": "string",
            "description": "Brief explanation of why you are compressing these items and what you are about to do next. This replaces the reasoning for this call."
        },
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": { "type": "string", "description": "Context ID, e.g. 'ctx-3'" },
                    "mode": {
                        "type": "string",
                        "enum": ["drop", "summarize"],
                        "description": "drop: replace with placeholder. summarize: replace with your summary."
                    },
                    "summary": {
                        "type": "string",
                        "description": "Your summary of the key information (required when mode is 'summarize')"
                    }
                },
                "required": ["id", "mode"]
            }
        }
    }
}
```

Example call:
```json
{
    "reason": "Finished investigating config and grep results. Now moving on to fix the 3 failing tests. Keeping test output and recent file reads, dropping stale early context.",
    "targets": [
        { "id": "ctx-1", "mode": "drop" },
        { "id": "ctx-4", "mode": "drop" },
        { "id": "ctx-6", "mode": "drop" },
        { "id": "ctx-8", "mode": "drop" },
        { "id": "ctx-2", "mode": "drop" },
        { "id": "ctx-9", "mode": "summarize", "summary": "Found handleSubmit in App.jsx:145 (main handler), api.js:89 (submit call), Form.jsx:67 (validation). Other 37 files were test/config, not relevant." },
        { "id": "ctx-11", "mode": "summarize", "summary": "npm test: 42 passed, 3 failed (auth.test.js:12, api.test.js:45, form.test.js:78). Failures are assertion mismatches in response format." }
    ]
}
```

Result:
```
Compressed 7 items:
  ctx-1   drop       reasoning (iter 1)            freed ~2.3k tk
  ctx-2   drop       read_file(src/config.py)      freed ~1.2k tk
  ctx-4   drop       reasoning (iter 2)            freed ~1.8k tk
  ctx-6   drop       reasoning (iter 3)            freed ~2.1k tk
  ctx-8   drop       reasoning (iter 4)            freed ~3.5k tk
  ctx-9   summarize  grep("handleSubmit")          freed ~4.2k tk (kept ~0.3k)
  ctx-11  summarize  run_terminal("npm test")      freed ~3.5k tk (kept ~0.3k)

Total freed: ~18.6k tokens
Context now: ~13.5k tokens (of ~64k window)
```

### Auto-drop of `close_context` reasoning

The reasoning model will produce a `reasoning_content` block for the `close_context` call itself — thinking about what to close. This is meta-reasoning that wastes space.

**Solution**: When `close_context` is executed, the system **automatically drops the reasoning block** from the assistant message that made this tool call. The agent's `reason` parameter replaces it — it's already a concise version of "what I decided and why."

In practice:
```
Before close_context execution:
  assistant msg: reasoning_content="Let me think about what to close... ctx-1 is a stale
    read of config.py since I re-read it as ctx-13... ctx-4 reasoning was about grepping
    which I already did... [2.5k tokens of meta-reasoning]"
    tool_calls: [close_context({reason: "Finished config investigation...", targets: [...]})]

After close_context execution:
  assistant msg: reasoning_content=None  ← auto-dropped
    content prepended with: "[Context management: Finished config investigation...]"
    tool_calls: [close_context({...})]
  tool msg: "Compressed 7 items... freed ~18.6k tokens"
```

The `reason` field (short, written by the agent) replaces the reasoning block (long, produced by the model). Net effect: context management costs almost zero context.

---

## What Each Mode Produces

### `drop` on a tool result
```
[ctx-1 dropped: read_file — src/config.py (45 lines)]
```

### `drop` on reasoning
Removes `reasoning_content` / `thinking` field entirely. Adds note to assistant content:
```
[ctx-3 reasoning dropped]
```

### `drop` on assistant output
Replaces `content` with:
```
[ctx-6 output dropped]
```

### `summarize` on a tool result
```
[ctx-8 summarized: grep("handleSubmit")]
Found handleSubmit in App.jsx:145 (main handler), api.js:89 (submit call), Form.jsx:67 (validation). Other 37 files were test/config, not relevant.
```

### `summarize` on reasoning
Replaces `reasoning_content` with agent's summary:
```
[ctx-7 reasoning summarized]
Concluded that the streaming pipeline needs modification at the proxy layer, not the backend.
```

### `summarize` on assistant output
Replaces `content` with:
```
[ctx-6 output summarized]
Explained the three-layer architecture and recommended starting with the middleware changes.
```

---

## Context ID Assignment

IDs are assigned in `main_flow.py` as messages are built:

```python
# Tool result produced:
content = f"[ctx-{counter}]\n{tool_result}"
id_map[f"ctx-{counter}"] = {msg_index, "tool", tool_name, first_arg_summary, len(tool_result)}
counter += 1

# Assistant message with reasoning:
# Tag stored in id_map (not embedded in reasoning_content to avoid confusing the model)
id_map[f"ctx-{counter}"] = {msg_index, "reasoning", None, None, len(reasoning)}
counter += 1

# Assistant message with long output (>500 chars):
content = f"[ctx-{counter} output]\n{assistant_content}"
id_map[f"ctx-{counter}"] = {msg_index, "output", None, None, len(assistant_content)}
counter += 1
```

---

## Automatic Safeguards

### 1. Per-result hard cap
Tool results exceeding `TOOL_RESULT_MAX_CHARS` (15k) are truncated at execution time.

### 2. Emergency auto-compress on API error
On 413/400: auto-`drop` the oldest open items until estimated tokens fit, then retry.

### 3. System prompt nudge
```
You have `context_status` and `close_context` tools to manage your context window.
When working on long tasks, periodically check context_status and compress items
you no longer need in full:
  - "drop" to fully remove (old reads, stale results, old reasoning)
  - "summarize" to replace with your own summary (preserve key info compactly)
Include a brief "reason" in close_context explaining what you finished and what
you're doing next — this replaces the reasoning for the call itself, keeping
context management nearly free. You can always re-read a closed file if needed.
```

---

## Middleware Caching

### Storage

| File | Contents | Purpose |
|------|----------|---------|
| `{id}.json` | Full raw messages (no ctx-IDs) | Replay, debugging |
| `{id}.compressed.json` | Messages after compressions + `ctx_counter` + `ctx_id_map` | Resume |
| `{id}.frontend.json` | UI display messages | Frontend (unchanged) |

### Session flow
```
1. Tool results/reasoning/outputs get ctx-IDs as produced
2. Agent calls close_context with specific targets and modes
3. current_processing_messages is mutated in-place
4. Stream ends → SSE done emits raw_messages (stripped) + compressed_messages + ctx_counter
5. Middleware persists both
```

### Resume flow
```
1. Middleware loads {id}.compressed.json (messages + ctx_counter)
2. Forwards compressed messages to backend
3. Backend resumes numbering from saved ctx_counter
4. Previously compressed items stay compressed; new items get new IDs
5. Agent can compress new items or further compress old ones
```

---

## Implementation

### `src/context_management.py`
```python
class ContextManager:
    def __init__(self, start_counter=1, existing_map=None):
        self.counter = start_counter
        self.id_map = existing_map or {}

    def tag_tool_result(self, content, tool_name, key_args) -> str:
    def tag_reasoning(self, msg_index, reasoning_len) -> None:
    def tag_output(self, content) -> str:
    def get_status(self, messages) -> str:

    def compress(self, messages, targets) -> (list, str):
        """Apply compression targets. Returns (modified_messages, result_summary)."""
        for t in targets:
            if t.mode == "drop":      self._drop(messages, t.id)
            if t.mode == "summarize": self._summarize(messages, t.id, t.summary)

    def strip_ctx_ids(self, messages) -> list:
        """Remove ctx-ID prefixes for raw persistence."""
```

### `src/tool_definitions.py`
- Add `context_status` and `close_context` definitions
- Both in `READ_ONLY_TOOLS`

### `src/main_flow.py`
- Create `ContextManager` per session (with saved counter on resume)
- Tag items as they're added
- Handle `context_status` → `ctx_mgr.get_status()`
- Handle `close_context` → `ctx_mgr.compress()`
- Emit both raw and compressed on done

### Middleware
- `conversation_store.py`: persist/load `compressed_messages` + `ctx_counter`
- `api.py`: inject compressed state on continue/resume

---

## What Stays Unchanged

- **Frontend**: `frontend_messages` untouched, ctx-IDs never reach the frontend
- **Tool execution**: full results produced; ctx-IDs added after
- **Raw persistence**: `{id}.json` always has full clean history

---

## Implementation Order

1. `src/context_management.py` — `ContextManager` with drop + summarize modes
2. `src/tool_definitions.py` — tool definitions
3. `src/main_flow.py` — tagging + tool handling
4. System prompt nudge
5. Middleware caching (`conversation_store.py` + `api.py`)
6. Emergency 413 fallback
