# Context Management Design Discussion

## The Problem

Our agent sends `current_processing_messages` — the **entire conversation history** — to the API on every iteration. After many tool calls with large results (file reads, grep, terminal output), this grows unboundedly until it hits the model's context limit and fails.

Currently, the only mitigation is the code interpreter's `close_file` mechanism and `TERMINAL_MAX_OUTPUT_CHARS` (15k) truncation at the tool level. There is no conversation-level context management.

---

## What Claude Code Does (5 Layers)

Claude Code applies these in order before each API call:

1. **Tool result budget** — Caps aggregate tool result size; can persist large outputs to disk and replace with a reference
2. **History snip** — Drops older messages from the model's view while keeping them in the UI scrollback
3. **Micro-compact** — Compresses individual tool results for specific tool types (read, grep, shell, web) — e.g. clearing old file content when a newer read of the same file exists
4. **Context collapse** — Staged selective collapses of conversation sections with summaries
5. **Auto-compact** — Full conversation summarization: calls a secondary model to summarize everything, replaces history with summary + recent messages

Plus **reactive compact** as emergency fallback when the API returns 413 (prompt too long).

---

## Design Questions for Our Agent

### Q1: Which layers do we actually need?

**Option A — Just tool result budget + old result compression (no LLM call)**
- Cheapest, simplest, no extra API calls
- Cap individual tool results (e.g. 10k chars), compress old ones to one-liners
- Might be enough for most use cases (30 iterations × reasonably sized results)

**Option B — Option A + auto-compact with LLM summarization**
- Handles truly long sessions (50+ tool calls, multi-step projects)
- Requires calling a secondary model, adds latency and cost
- More complex: need to preserve conversation structure across the summary boundary

**Option C — Option A + simple truncation (drop old turns)**
- Middle ground: just keep the N most recent turns + system message
- No LLM call needed, but loses context about earlier work
- Risk: model forgets what files it already edited, repeats work

**Recommendation:** Start with **Option A** (no LLM calls), evaluate if sessions actually hit limits in practice, add auto-compact (Option B) later if needed.

### Q2: What constitutes "old" tool results?

Our loop structure is: `[system, user, assistant+tools, assistant+tools, ..., user, assistant+tools, ...]`

Each "turn" is roughly: user message → N iterations of (assistant + tool results).

Options for what to compress:
- **By turn**: Keep the most recent K user-initiated turns fully intact, compress everything before
- **By iteration**: Keep the most recent K iterations (assistant + tool results), compress older ones
- **By token budget**: Compress from oldest until total is under a target

The turn-based approach seems most natural — the model needs recent tool results to continue its current task, but old results from the previous user question are rarely needed in full.

### Q3: How should we handle the code interpreter blocks?

The code interpreter already does some context management: `clean_previous_interpreter_blocks()` strips old interpreter displays from tool messages. But this only handles the display block — the raw tool result (file content) from `read_file` still stays in the message.

Options:
- **A**: Compress tool results independently from interpreter blocks (current `clean_previous_interpreter_blocks` + new compression)
- **B**: Merge the two — the compression layer is aware of code interpreter and handles both

Option A is simpler and keeps concerns separate.

### Q4: What should compressed tool results look like?

The API requires every `tool_calls[].id` in an assistant message to have a matching `role: "tool"` message with `tool_call_id`. We can't delete old tool results entirely — we can only replace their content.

Options for compressed content:

```
# Option 1: Minimal
"[Previous read_file result for src/main.py — 450 lines]"

# Option 2: With key info preserved
"[read_file: src/main.py (450 lines) — Python module with functions: generate_chat_responses_stream_native, discover_open_files, ...]"

# Option 3: First/last N lines + summary
"First 3 lines:\nimport json\nimport datetime\nimport copy\n...\n[truncated 440 lines]\n...last 3 lines..."
```

Option 1 is simplest and still gives the model enough to know "I already read that file." Option 2 requires parsing. Option 3 preserves some signal but is more complex.

### Q5: Where in the code should this live?

Options:
- **A**: New module `src/context_management.py` with pure functions, called from `main_flow.py` before the API call
- **B**: Inside `main_flow.py` directly (less modular but fewer files)
- **C**: As a middleware/wrapper around the messages list (e.g. a class that wraps the list and auto-manages)

Option A keeps `main_flow.py` clean and makes the logic testable.

### Q6: Token estimation

We need to decide when to trigger compaction. Options:
- **Rough char-based**: `len(json.dumps(messages)) / 4` — simple, ~20% accurate
- **tiktoken**: Accurate but adds a dependency and only works for OpenAI tokenizers (not DeepSeek/Gemini)
- **Char-based with tool-type awareness**: Different multipliers for code vs prose vs JSON

The rough char-based estimate is probably fine as a trigger threshold — we don't need precision, just a "getting close to limit" signal. The models we use have different context limits:
- DeepSeek: 64k context
- NVIDIA models: varies
- Gemini: 1M context

So the threshold should ideally be configurable or derived from the provider's known context window.

### Q7: Should compaction be visible to the user?

Options:
- **Silent**: Just compress, model sees shorter history, user sees nothing
- **Visible**: Send an SSE event like `{"type": "compaction", "message": "Compressed old context to save tokens"}` so the UI can show a subtle indicator
- **In-conversation**: Insert a visible `[Context compressed]` marker in the chat

Silent is simplest. A subtle UI indicator would be nice but isn't essential.

### Q8: Provider-aware context limits

Different providers have vastly different context windows. Should we:
- **A**: Use a single conservative threshold (e.g. 50k tokens for all providers)
- **B**: Store context window size per provider in `config.py` and compute threshold as a percentage (e.g. 80% of window)
- **C**: Don't preempt — just react to API errors (413/400)

Option B is the right approach. Gemini with 1M context rarely needs compaction; DeepSeek at 64k needs it sooner. But Option A is a fine starting point.

---

## Proposed Implementation (Pending Discussion)

Assuming we go with **Option A from Q1** (tool result budget + old result compression, no LLM):

```
New file: src/context_management.py

Functions:
  estimate_message_tokens(messages) -> int
    Rough char/4 estimate across all message content

  apply_tool_result_budget(messages, max_chars_per_result=10000) -> messages
    Truncate any single tool result exceeding the limit (head+tail)
    Skip most recent tool results batch

  compress_old_tool_results(messages, keep_recent_turns=2) -> messages  
    Find turn boundaries (user messages)
    For turns older than keep_recent_turns, replace tool result content
    with one-line summary "[Previous {tool_name} result for {key_arg}]"

Integration in main_flow.py:
  Before api_kwargs construction:
    current_processing_messages = apply_tool_result_budget(current_processing_messages)
    current_processing_messages = compress_old_tool_results(current_processing_messages)

Config additions in config.py:
  TOOL_RESULT_MAX_CHARS = 10_000
  CONTEXT_KEEP_RECENT_TURNS = 2
```

This is ~60 lines of code, no new dependencies, no extra API calls. If sessions routinely hit limits even with this, we add LLM-based summarization as a second phase.

---

## Open Questions

1. Should we compress `reasoning_content`/`thinking` from old assistant messages too? These can be very large (10k+ chars from DeepSeek reasoner) and are never referenced by the model in subsequent turns.
2. Should `web_browser` results get special treatment? They're already summarized by the secondary model, so they're usually small — but the raw markdown fallback can be huge.
3. Do we want a hard fail-safe that catches API 413/400 errors and triggers emergency compaction (reactive compact), or is the preemptive approach enough?
4. For the code interpreter: when we compress an old `read_file` result, should we also remove that file from the "open files" set (so it doesn't get re-appended by `generate_consolidated_interpreter_display`)?
