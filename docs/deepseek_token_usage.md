# DeepSeek Token Usage Tracking

**Status**: Planned — not yet implemented  
**Source**: DeepSeek API Reference (`api-docs.deepseek.com/api/create-chat-completion`)  
**Last verified**: 2026-05-12

## Overview

DeepSeek's API supports returning per-request token usage during streaming via the standard OpenAI-compatible `stream_options` mechanism. Currently, ThinkWithTool does **not** capture this data. The API call in `main_flow.py` omits `stream_options`, and the chunk loop never inspects `chunk.usage`, so all usage info is silently discarded.

## DeepSeek Usage Object

```json
{
  "prompt_tokens": 4521,
  "completion_tokens": 847,
  "total_tokens": 5368,
  "prompt_cache_hit_tokens": 1200,
  "prompt_cache_miss_tokens": 3321,
  "completion_tokens_details": {
    "reasoning_tokens": 612
  }
}
```

| Field | Notes |
|---|---|
| `prompt_tokens` | = `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens` |
| `completion_tokens` | Total tokens generated (thinking + visible content) |
| `total_tokens` | = `prompt_tokens` + `completion_tokens` |
| `prompt_cache_hit_tokens` | DeepSeek KV cache hits; high = efficient reuse |
| `prompt_cache_miss_tokens` | DeepSeek KV cache misses; high = churning context |
| `completion_tokens_details.reasoning_tokens` | Tokens consumed by the internal thinking chain (only for reasoning models like `deepseek-v4-pro`) |

## Streaming Mechanism

Set `stream_options={"include_usage": True}` in the API call. All intermediate chunks have `"usage": null`; only the final chunk before `data: [DONE]` carries the populated usage object.

## Files to Change

### `src/config.py`
- Add `STREAM_OPTIONS = {"include_usage": True}` (default on)

### `src/main_flow.py`
1. **Line ~304** (`api_kwargs`): add `"stream_options": STREAM_OPTIONS`
2. **Chunk loop (~line 320)**: capture `chunk.usage` when non-null into `current_usage`
3. **Yield dicts** (`running` / `completed` / `max_iterations_reached`): add `"usage": current_usage`
4. **Loop-level accumulators**: maintain `total_prompt_tokens`, `total_completion_tokens`, `total_reasoning_tokens` across iterations — these are the numbers users actually care about
5. **`record_api_call` (~line 382)**: include usage in training log entries

### `src/web_api/app.py`
- Pass `usage` through SSE `messages` events so the frontend can display it

### Frontend (`frontend/src/`)
- Display cumulative token usage (per-turn and per-conversation totals)

## Also: `base_url` Anomaly

The current config uses `"base_url": "https://api.deepseek.com/v1"`, but DeepSeek's docs specify `https://api.deepseek.com` (no `/v1`). This should be corrected when touching config. The OpenAI SDK appends `/chat/completions` onto `base_url`, so the current setting produces `https://api.deepseek.com/v1/chat/completions` instead of the canonical `https://api.deepseek.com/chat/completions`. Worth checking whether DeepSeek tolerates the `/v1` prefix.

## Key Design Decisions (when implementing)

1. **Accumulate across iterations**: The main loop runs up to 30 iterations per user turn. Per-chunk usage is interesting, but the user-facing metric is the cumulative total for the entire turn (sum of all API calls).

2. **Separate thinking vs. visible tokens**: `reasoning_tokens` is a key metric for reasoning models. The UI should distinguish "thinking tokens" from "output tokens" so users understand where their budget is going.

3. **KV cache metrics**: `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` are useful for debugging context efficiency but probably too granular for the default UI. Consider logging them but not displaying them by default.

4. **Thread safety**: The main flow runs synchronously (single thread per conversation), so accumulators don't need locking. Subagents call the same function with their own message list, so usage will be correctly scoped to each call.

5. **Training data**: `record_api_call` currently logs `{request, response}` to daily JSONL files. Usage data should be added to these entries for cost analysis.
