# Agent Memory — Implementation Summary

Branch: `feature/agent-memory` (3 commits, one per milestone). Design source:
`docs/code-agent-memory-design.md`.

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
    prompts.py       Extraction system prompt (no-op gate, "what NOT to save")
    extractor.py      Layer 2a: one structured LLM call per finished session
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
  test_memory_layer1.py   schema round-trip, store CRUD, gateway routes (TestClient)
  test_memory_layer2.py   extractor (fake LLM client) + consolidator heuristics
  test_memory_layer3.py   gap ledger dedupe/escalation, dispatcher gate + docker-arg build
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
- **In a turn**: the agent may call `remember` (write), `recall` (read,
  parallel/subagent-safe), or `log_gap` (flag an unresolved unknown, also a
  write). `remember` and `log_gap` are sequential-only and excluded from
  subagents; `recall` is read-only and safe for both.
- **At session end** (`gateway/streaming.py`, top-level `user_chat` only,
  terminal status): passive extraction runs off the hot path in a small
  dedicated thread pool, never awaited. No-op is the expected common case —
  most sessions should write nothing. If something was written,
  consolidation (dedupe + decay) runs immediately after, also cheap/local.
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
| `passive_enabled` | `true` | Layer 2a extraction after each session |
| `extraction_provider` | *(default provider)* | Which provider/model runs extraction |
| `heavy_ops_enabled` | `false` | Layer 2b — spawn worker containers |
| `worker_image` | `"auroracoder"` | Image tag used for `memory-worker` containers |

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
