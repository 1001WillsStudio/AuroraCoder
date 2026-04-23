# Subagent Design Document

## Current Implementation (v1)

The `subagent` tool spawns an independent agent that runs the same `generate_chat_responses_stream_native` loop with its own message list and a filtered tool set. Only the final text response is returned to the parent as a tool result.

### What's implemented

- **Blocking, sequential execution**: The parent loop blocks until the subagent finishes. Multiple subagents in one turn run one after another.
- **Tool filtering**: `tools="read_only"` (default) restricts to read-only tools. `tools="all"` gives everything except `subagent` itself (no recursion).
- **Iteration cap**: `SUBAGENT_MAX_ITERATIONS = 15` (vs parent's 30).
- **Result cap**: `SUBAGENT_MAX_RESULT_CHARS = 4000` to protect parent context.
- **Same filesystem**: Subagent operates in the same workspace as the parent.

---

## Design Decisions (Settled)

### Blocking is correct for now

The parent can't call the model again until all tool results from the current turn are ready. Making the subagent non-blocking wouldn't help — the parent would still wait. True non-blocking (fire-and-forget background agents with async result injection) would require rethinking the entire agent loop architecture.

### Sequential subagents (not concurrent)

Even though read-only subagents are side-effect-free, parallel execution risks:
- API rate limits (each subagent makes up to 15 API calls)
- Confusing interleaved execution for debugging
- Minimal real benefit since the parent blocks for all results anyway
- Models rarely request multiple subagents in one turn

### No recursion

Subagents cannot spawn their own subagents. The `subagent` tool is excluded from the subagent's tool set. This avoids runaway cost and complexity.

### Shared filesystem is fine

Since execution is sequential (parent blocks while subagent runs), there are no race conditions. For `tools="all"`, the subagent's file changes are visible to the parent when it resumes. This is intentional.

---

## Open Issues

### 1. Provider Inheritance

**Problem**: The subagent defaults to `DEFAULT_PROVIDER` (DeepSeek), not the parent's current provider. If the user selects Gemini on the frontend, the subagent silently uses DeepSeek.

**Options**:
- **A — Thread-local variable**: The main loop sets a thread-local with the current `provider_id` before executing tools. `run_subagent` reads it if no explicit provider is given.
- **B — Inject via execute_tool_call**: Extend `execute_tool_call` to optionally pass runtime context (like `provider_id`) to tools that need it. Cleaner but requires changing the tool execution interface.
- **C — Leave it**: Document that subagents use `DEFAULT_PROVIDER`. Users who care can change the default. Simplest, but surprising behavior.

**Recommendation**: Option A is low-effort and solves the problem. A module-level `_current_provider_id` variable set at the start of each iteration in `main_flow.py`, read by `run_subagent` as a fallback.

### 2. Chat History / Trace Visibility

**Problem**: The subagent's internal conversation (tool calls, results, thinking) is completely invisible. The parent gets a text summary, and the frontend sees only `subagent` tool call → result. For debugging and transparency, users may want to see what the subagent actually did.

**Solution**: Treat the subagent's conversation as a first-class conversation in a **conversation history store**. The subagent is just another "new chat" with a `parent_id` linking it to the parent conversation. The frontend can render it as a collapsible sub-conversation or a navigable link.

This is an infrastructure concern, not an agent concern. See **[`docs/conversation_history_design.md`](conversation_history_design.md)** for the full design.

**What the subagent needs to do** (once the history system exists):
- `run_subagent` creates a child conversation with `parent_id` pointing to the parent
- The subagent's messages are persisted to the child conversation as it runs
- The tool result includes the child `conversation_id` in metadata (not in the text the model sees)
- The frontend fetches the child conversation on demand to display the trace

### 3. Token Budget / Cost Tracking

**Problem**: A subagent can burn 15 iterations of tokens invisibly. The parent has no visibility into how much the subagent consumed.

**Options**:
- Track input/output tokens per subagent call (from API response `usage` field)
- Include token counts in the subagent trace
- Set a hard token budget per subagent (kill it if exceeded)
- Just rely on `SUBAGENT_MAX_ITERATIONS` as the cost cap

**Recommendation**: For now, the iteration cap is sufficient. Token tracking can be added when we add general cost tracking to the agent.

### 4. Subagent System Prompt

**Current**: The subagent uses the same `SYSTEM_MESSAGE_TEMPLATE` as the parent. This works but isn't optimal — the subagent doesn't need instructions about "ask before doing extra work" or general autonomy guidance. It has a specific task.

**Options**:
- **A — Same prompt (current)**: Simple, works fine in practice.
- **B — Stripped-down prompt**: Remove interactive guidelines, keep tool usage instructions. Add "You are a subagent executing a specific task. Focus on the task and return a concise summary when done."
- **C — Configurable via config.py**: A separate `SUBAGENT_SYSTEM_MESSAGE_TEMPLATE`.

**Recommendation**: Option B, but low priority. The current prompt works and the subagent naturally focuses on the task since the user message is the task description.

---

## Future Enhancements (Not Now)

- **Non-blocking background agents**: A separate `background_task` tool that fires and forgets. Results arrive as notifications injected into the parent's next turn. Requires async architecture changes.
- **Concurrent read-only subagents**: If rate limits are solved (e.g. with cheaper/local models), parallel read-only subagents could speed up multi-pronged research tasks.
- **Subagent-to-parent communication**: Allow the subagent to "ask" the parent for context it's missing, rather than failing silently. Complex interaction model.
- **Persistent subagent sessions**: Let a subagent resume from where it left off if the parent calls it again with the same task context.
