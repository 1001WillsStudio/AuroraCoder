# Agent Memory — Implementation Summary

Branch: `feature/agent-memory`. Design source: `docs/code-agent-memory-design.md`.

Note: the original M1/M2 split had `remember` write immediately (after its
own synchronous review) and a separate passive pass mine the transcript
afterward. That's since been unified into a single end-of-session pass —
see "The unified write pass" below for why and what changed.

This implements the design doc's layering exactly:

| Layer | What it is | Status |
|---|---|---|
| 1 — Light runtime | Sync CRUD, retrieval, redaction, Stance assembly | **Done, always on** |
| 2a — Passive pipeline | Async, structured-output-only extraction + consolidation | **Done, on by default, toggleable** |
| 2b — Heavy ops (Gap Engine) | Tool-using investigation in an isolated worker | **Scaffolding only, off by default** |

## Where everything lives

```
gateway/memory/
  schema.py       MemoryItem dataclass, markdown+frontmatter (de)serialization
  store.py        MemoryRepository — file-backed store + SQLite ranking index
  redact.py       Secret redaction applied on every write
  retrieval.py    rank_candidates() — keyword+recency+usage blend (no embeddings, MVP)
  stance.py       build_stance_block() — assembles the always-injected prefix block
  gap_store.py    GapLedger — SQLite work-queue for open knowledge gaps
  ops/
    prompts.py       Unified write-pass system prompt (no-op gate, "what NOT to save")
    similarity.py    Shared keyword-overlap helper (used by extractor + consolidator)
    extractor.py      Layer 2a: ONE structured LLM call per finished session, judges
                      both agent-nominated (`remember`) and discovered candidates
    consolidator.py   Layer 2a: dedupe + unused-decay heuristics (no LLM)
    dispatcher.py     Layer 2b: DooD worker spawn/teardown — gated, unexercised

src/core_tools/
  memory_client.py  Backend's only bridge to gateway memory API (fail-open)
  memory_tools.py   remember_tool / recall_tool / log_gap_tool implementations

src/code_tools/memory_panel.py   "Living Tool State" panel showing what got remembered

docker/
  supervisord.memory-worker.conf   Slim profile: agent process only
  entrypoint.sh                    AURORACODER_ROLE=memory-worker branch

tests/
  test_memory_layer1.py    schema round-trip, store CRUD, gateway routes (TestClient)
  test_memory_layer2.py    unified extractor (nominated + discovered, fake LLM client),
                           remember_tool no-op, consolidator heuristics
  test_memory_layer3.py    gap ledger dedupe/escalation, dispatcher gate + docker-arg build
```

Gateway is the **sole owner** of all memory state (files + `index.sqlite` +
`gaps.sqlite` under `DATA_DIR/memory/`), exactly mirroring how it already
exclusively owns conversations and settings. The backend (`src/`) never
touches the store directly — it only talks to `gateway/memory/*` over HTTP,
and every call in `memory_client.py` fails open (empty/inert result) rather
than raising, so a memory outage can never break the agent's turn loop.

## Runtime behavior

- **Every turn**: Stance block is fetched once (session start only) and
  baked into the cached system-prompt prefix — never busts prompt cache on
  later turns.
- **In a turn**: the agent may call `remember` (nominate, no I/O — see
  below), `recall` (read, parallel/subagent-safe), or `log_gap` (flag an
  unresolved unknown, a real synchronous write to the Gap Ledger).
  `remember` and `log_gap` are sequential-only and excluded from
  subagents; `recall` is read-only and safe for both.
- **`remember` writes nothing at call time.** It's a purely local no-op
  (`src/core_tools/memory_tools.py`) that returns an acknowledgment and
  leaves its arguments as a tool call in the transcript — no network call,
  no dependency on the gateway being reachable. `log_gap` is unaffected by
  this and still writes immediately (it's a work-queue note, not a fact
  injected into future context, so the risk profile is much lower and
  there's no discovery step to unify it with).
- **At session end** (`gateway/streaming.py`, top-level `user_chat`,
  terminal status — **or** the moment a conversation hands off via
  `continue_as_new_chat`, since that segment's transcript would otherwise
  never be mined): the unified write pass runs off the hot path in a small
  dedicated thread pool, never awaited. It parses the transcript for
  `remember` calls (nominated candidates) and independently scans for
  anything else memory-worthy (discovered candidates), judges both in ONE
  LLM call under the same rules, and writes only what's approved — see
  "The unified write pass" below. No-op is the expected common case, for
  nominated candidates too, not just discovered ones. If something was
  written, consolidation (dedupe + decay) runs immediately after, also
  cheap/local.
- **Gap investigation** (`/api/memory/gaps/{id}/investigate`): always
  callable, but no-ops with a clear reason string unless
  `settings.other.memory.heavy_ops_enabled` is explicitly set. When enabled,
  it spawns an isolated worker container (workspace **copy**, never the live
  one) and — since the investigate-and-report protocol isn't built yet —
  immediately tears it down and defers the gap rather than faking a result.

## Settings (all under `settings.json` → `other.memory`, all optional)

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | Master switch for Layer 1 (stance/remember/recall/log_gap) |
| `passive_enabled` | `true` | The unified write pass, run at session end |
| `extraction_provider` | *(default provider)* | Which provider/model runs the write pass |
| `heavy_ops_enabled` | `false` | Layer 2b — spawn worker containers |
| `worker_image` | `"auroracoder"` | Image tag used for `memory-worker` containers |

## The unified write pass (design doc §11 "Active" + "Passive", merged)

Originally this repo had two separate write paths: `remember` wrote (after
a synchronous review) immediately, mid-session; a passive pass separately
scanned the finished transcript for anything else. That synchronous
reviewer had a structural problem: judging a candidate against a handful of
recent messages could only catch surface-level issues (obviously ephemeral,
obviously a duplicate of something just shown to it) — it had no way to
verify the candidate was actually *grounded* in what happened earlier in
the session, because it never saw the full conversation.

The fix was to stop trying to review in the moment at all. `remember` now
only leaves a marker; **the only place memory is ever written is the
end-of-session pass** (`ops/extractor.py`), and it handles both kinds of
candidate in one call:

1. Parse the transcript for `remember` tool calls → "nominated" candidates.
   These skip *discovery* (they don't need to be found, they're given) but
   not *judgment* — same rules, same no-op bias, no free pass.
2. For each nominated candidate, a cheap keyword-overlap search
   (`ops/similarity.py`) surfaces existing memories that might be
   duplicates (an explicit `memory_id` from the agent is honored as a
   strong duplicate signal even if the keyword search misses it).
3. One structured-output LLM call sees the full transcript, the nominated
   candidates, and their possible duplicates, and returns a single
   `{"memories": [...]}` list — nominated items it approved (optionally
   merged into an existing memory via `duplicate_of`, or with plane/
   confidence adjusted), plus anything it discovered on its own.
4. Everything in that list gets written; everything left out doesn't —
   there's no separate accept/reject step afterward.

Consequences worth being explicit about:
- A memory from `remember` is **not visible to `recall` later in the same
  session** — it only exists once the session ends (or hands off). This
  is a real behavior change from the original synchronous design, judged
  acceptable since memory here is about cross-session continuity, not a
  same-session scratchpad (the agent already has full context of the
  current session).
- If the write pass itself fails for a session (provider outage, etc.),
  that session's nominations are not retried — consistent with the rest
  of this system being fail-open by default, and there's no user-facing
  tool result left to surface a failure through by the time this runs
  anyway. A missed memory can usually be re-established next session.

## What was deliberately left unfinished (and why)

- **Gap Engine investigation protocol.** `dispatcher.py` can spawn/snapshot/
  teardown a worker container, but doesn't yet drive it through a "here's
  the gap, investigate with tools, report findings" exchange — that needs a
  defined one-shot task contract against `src/web_api` (likely a dedicated
  `report_findings` tool the worker's agent calls, parsed by the
  dispatcher). Building that without a live Docker integration-test loop
  risked shipping a plausible-looking but untested fake. The container
  lifecycle plumbing it will sit on top of is real and tested.
- **Reflection / lesson learning (design doc §14).** Not started. This is
  additive on top of the same extraction pass (a second prompt variant keyed
  off error/retry/correction signals) — natural next milestone once the Gap
  Engine investigation loop exists, since lessons and gap-resolutions share
  the "self-authored, lower-trust" provenance handling.
- **Volatile/TTL re-verification on read.** Schema supports
  `volatile`/`ttl_days`/`reverify_at` fields, but nothing currently acts on
  a stale volatile memory at read time (design doc §12) — retrieval returns
  it as-is. Small follow-up: check `ttl_days` in `retrieval.rank_candidates`
  and open a gap ledger entry instead of trusting it.
- **Embeddings for retrieval.** Explicitly out of scope for the MVP per the
  design doc (§12 calls it "optional") — current ranking is
  keyword+recency+usage only, which is enough for identifiers/paths but
  will miss fuzzy/paraphrased recall queries.
- **Frontend UI.** No Memory/Gaps browser panel yet — routes exist
  (`GET /api/memory`, `GET /api/memory/gaps`) specifically so a UI can be
  added without backend changes, and memory files are plain
  human-editable markdown in the meantime.

## Testing notes

All three test files are self-contained scripts (matching this repo's
existing `tests/` convention — no pytest dependency), runnable directly:

```
python tests/test_memory_layer1.py
python tests/test_memory_layer2.py
python tests/test_memory_layer3.py
```

Everything runs in-process (`fastapi.testclient.TestClient`, no real port
bound) against an isolated temp `AURORACODER_DATA_DIR`, and all LLM/docker
calls are mocked — the suites are safe to run alongside a live AuroraCoder
container without touching it. No real Docker container was spawned as part
of this implementation; `dispatcher.py`'s container-lifecycle code is
unit-tested with `subprocess`/filesystem calls mocked out, not integration-
tested against real Docker-in-Docker.
