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

`memory/` is a top-level package, not nested under `gateway/` — it's a
distinct subsystem that happens to run inside the gateway process (see
"Why memory isn't a gateway concern" below), not one of the gateway's
own concerns like conversation/settings persistence are.

```
memory/
  settings.py     Central other.memory.* flag reader (master switch + sub-flags)
  schema.py       MemoryItem dataclass, markdown+frontmatter (de)serialization
  store.py        MemoryRepository — file-backed store + SQLite ranking index
  redact.py       Secret redaction applied on every write
  retrieval.py    rank_candidates() — keyword+recency+usage blend (no embeddings, MVP)
  stance.py       build_stance_block() — assembles the always-injected prefix block
  gap_store.py    GapLedger — SQLite work-queue for open knowledge gaps
  ops/
    prompts.py             Unified write-pass system prompt (no-op gate, "what NOT to save")
    similarity.py          Shared keyword-overlap helper (used by extractor + consolidator)
    conversation_search.py Shared cross-conversation keyword search (used by extractor;
                           reachable over HTTP for the future Layer 2b worker too)
    extractor.py            Layer 2a: ONE structured LLM call per finished session, judges
                            both agent-nominated (`remember`) and discovered candidates
    consolidator.py         Layer 2a: dedupe + evidence-based decay heuristics (no LLM)
    dispatcher.py           Layer 2b: DooD worker spawn/teardown — gated, unexercised

src/core_tools/
  memory_client.py  Backend's only bridge to gateway memory API (fail-open)
  memory_tools.py   remember_tool / recall_tool / forget_tool / log_gap_tool implementations

src/code_tools/memory_panel.py   "Living Tool State" panel showing what got remembered

docker/
  supervisord.memory-worker.conf   Slim profile: agent process only
  entrypoint.sh                    AURORACODER_ROLE=memory-worker branch

tests/
  test_memory_layer1.py    schema round-trip, store CRUD, gateway routes (TestClient)
  test_memory_layer2.py    unified extractor (nominated + discovered, fake LLM client),
                           remember_tool no-op, consolidator heuristics
  test_memory_layer3.py    gap ledger dedupe/escalation, dispatcher gate + docker-arg build
  test_memory_toggle.py    master switch: tool-list filtering, route no-ops, extraction
                           short-circuit, system-prompt substitution, gateway/backend agreement
```

The gateway process is the **sole owner** of all memory state at runtime
(files + `index.sqlite` + `gaps.sqlite` under `DATA_DIR/memory/`), exactly
mirroring how it already exclusively owns conversations and settings —
but that's a statement about which *process* runs this code, not which
*package* it lives in (see below). The backend (`src/`) never touches the
store directly — it only talks to `/api/memory/*` over HTTP, and every
call in `memory_client.py` fails open (empty/inert result) rather than
raising, so a memory outage can never break the agent's turn loop.

## Why memory isn't a gateway concern

`memory/` originally lived at `gateway/memory/`, reasoned about (correctly)
as "runs in the same process that already owns all other persistent
state." But nesting it inside a package literally named `gateway` —
alongside `conversation_store.py`, `routes.py`, `streaming.py`, the actual
SSE-proxy/persistence plumbing the name describes — made memory read like
one more gateway concern, when it's really a whole separate subsystem
(schema, store, retrieval, redaction, a Layer 2 write pass, a Gap Ledger)
that simply happens to run in the same process. Moved to a top-level
`memory/` package, sibling to `gateway/` and `src/`. Nothing about
*runtime* changed: still the same process, same container, same
`/api/memory/*` routes — `gateway/routes.py` and `gateway/streaming.py`
just import from `memory.*` instead of owning it.

## Runtime behavior

- **Master switch** (`settings.other.memory.enabled`, default **`false`**):
  everything below is inert unless this is explicitly turned on. When off,
  the agent behaves exactly as it would with no memory module at all — the
  `remember`/`recall`/`log_gap` tool schemas are filtered out of the tool
  list before it ever reaches the LLM (`src/tool_definitions.py`'s
  `memory_filter_tools`, applied in both `get_tool_definitions()` and the
  subagent-filtered `get_filtered_tools()`), the static "Memory" guideline
  bullet + stance block are dropped from the system prompt entirely
  (`src/main_flow.py`), the stance HTTP call is skipped rather than made
  and discarded, and every `/api/memory/*` gateway route (including gap
  logging) short-circuits to a safe no-op. `passive_enabled` and
  `heavy_ops_enabled` below are `memory_enabled() AND <their own flag>` —
  see `memory/settings.py` — so a disabled master also disables passive
  extraction and gap investigation without needing to flip those
  separately. Read from two places (`memory/settings.py` on the gateway
  side, `src/core_tools/memory_client.memory_enabled()` on the backend
  side, both reading the same `settings.json`) since tool-list filtering
  happens in the backend process and route gating happens in the gateway
  process — see `tests/test_memory_toggle.py`.
- **Every turn** (when enabled): Stance block is fetched once (session
  start only) and baked into the cached system-prompt prefix — never busts
  prompt cache on later turns.
- **In a turn**: the agent may call `remember` (nominate, no I/O — see
  below), `recall` (read, parallel/subagent-safe — its output includes each
  result's `id` so a correction can target it precisely), `forget` (delete
  one memory by id immediately — a real synchronous write, no judgment
  pass, since an explicit "that's wrong" from the user needs no hindsight
  the way a `remember` nomination does), or `log_gap` (flag an unresolved
  unknown, a real synchronous write to the Gap Ledger). `remember`,
  `forget`, and `log_gap` are sequential-only and excluded from subagents;
  `recall` is read-only and safe for both.
- **Fixing/removing a wrong memory**: three ways, from lightest to
  heaviest. (1) `DELETE /api/memory/{id}` — the agent's `forget` tool, or
  a human via the Settings → Memory browser (`GET /api/memory` now
  returns full content, not just metadata, so the browser can render it).
  (2) Hand-edit or delete the plain markdown file directly under
  `DATA_DIR/memory/{plane}/{id}.md` — reads go straight to the file on
  every call (no cache to bust), so this takes effect immediately; the one
  rough edge is a stale SQLite index row if you delete the file without
  going through the API (harmless — reads of a missing file just return
  `None` — but not auto-cleaned). (3) `remember` with `memory_id` set (or
  `duplicate_of` returned by the write-pass judge) to overwrite an
  existing memory in place instead of creating a duplicate. Note
  `supersedes` is descriptive lineage metadata only — it does NOT delete
  or hide the memory it points to; only same-id reuse actually replaces
  content.
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

Toggleable from the frontend Settings panel ("Memory" section — the master
switch plus a Stored Memories browser with per-item delete; the sub-flags
below are settings.json-only for now).

| Key | Default | Effect |
|---|---|---|
| `enabled` | **`false`** | Master switch for the whole subsystem — see "Runtime behavior" above |
| `passive_enabled` | `true` | The unified write pass, run at session end (requires `enabled`) |
| `extraction_provider` | *(default provider)* | Which provider/model runs the write pass |
| `heavy_ops_enabled` | `false` | Layer 2b — spawn worker containers (requires `enabled`). **Also controls whether the Docker socket gets mounted into the main container** (`launcher/docker.go`) — flipping this on requires relaunching the container, since a bind mount can't be added to one already running. |
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

## Confidence is self-reported — and deliberately not trusted alone (§17)

The write-pass LLM still emits a `confidence: high|medium|low` field per
candidate (`ops/prompts.py`), but two things changed once we recognized a
model asked to self-rate its own certainty has no external anchor and
tends to skew toward "high" regardless of actual reliability:

1. **The prompt gives concrete anchors instead of three bare words** —
   "high" now requires an unambiguous, unhedged, direct user statement
   that's either a correction/instruction or independently corroborated
   by another past conversation; everything else defaults to "medium" or
   "low". The prompt states outright that confidence carries no decay
   benefit (see next point), removing any incentive to inflate it.
2. **`confidence` alone no longer buys a memory anything in the decay
   pass** (`ops/consolidator.py`). The old logic gave `confidence="high"`
   permanent decay immunity — if the model over-reports "high" (the
   expected failure mode), that mechanism goes silently toothless
   forever. The new rule is asymmetric: self-reported confidence is only
   trusted to make a memory decay **faster** (`low` → half the grace
   period — a safe, recoverable direction to trust a self-report in),
   never to grant it longer life on its own.

Longer life is earned only through evidence the model can't fabricate by
picking a word:
- **`corroboration_count`** (new field on `MemoryItem`) — incremented in
  `ops/extractor.py`, purely in code, every time a *later, separate*
  session's candidate independently resolves back to this same memory via
  `duplicate_of`. Each corroboration multiplies the decay grace period
  (capped at 6x) — real, repeated reinforcement, not a one-shot rating.
- **Actual retrieval usage** (`usage_count` / `last_used`) — but this
  isn't permanent immunity either anymore (see below).

Reinforcing an existing memory (`duplicate_of`) now also preserves its
`usage_count`/`last_used`/`created` instead of resetting them to
defaults, which the original implementation did by accident — decay
judges a memory by its whole history, so an in-place update can't be
allowed to erase it.

### Decay pass rework (`ops/consolidator.py`)

Three independent, evidence-based checks (any one removes the item; still
world-plane only, stance is never touched automatically):

1. **Volatile + past its `ttl_days`** → expires on schedule regardless of
   usage. The schema has always carried `volatile`/`ttl_days` (design doc
   §10: "carries ttl, re-verify on read") but the consolidator never
   actually read them before this — now it does. There's no
   re-verify-on-read mechanism yet, so honest behavior is to expire
   rather than let an un-reverified volatile fact linger indefinitely.
2. **Never retrieved, past its confidence/corroboration-scaled grace
   period** (`_effective_grace_days`) — the reworked version of the
   original "unused > 90 days" check.
3. **Retrieved before, but not recently enough** — closes a real gap in
   the original logic, where `usage_count > 0` granted permanent immunity
   from a single hit, ever. Now measured from `last_used` with a longer
   (3x) allowance relative to the item's own grace period — a track
   record of usefulness earns more time, not forever.

## Why the write pass isn't an agent (and where cross-conversation context comes from instead)

Natural follow-up question once judgment needs full transcript context:
should the write pass go further and become a real agent with tool
access (read files, search other sessions, etc.) to judge candidates
more accurately? Deliberately no, for now:

- It runs unattended, after **every** session and every
  `continue_as_new_chat` handoff — by far the highest-frequency automated
  LLM call in this system. Giving it the full tool suite (writes, shell)
  would reintroduce the exact blast-radius problem the Layer 2b worker's
  Docker isolation exists to contain, for something that fires
  constantly rather than on rare, explicit gap-investigation requests.
- The case that genuinely needs multi-step, tool-using investigation
  already exists and is already isolated: Layer 2b's Gap Engine
  (`ops/dispatcher.py`). Growing the write pass into an agent would
  duplicate that role with a *less* isolated version of it.

What the write pass actually needed was a bit more read-only context, not
autonomy — so instead of tools, each nominated candidate gets two cheap,
**deterministic** pre-fetches while the prompt is being built (the model
never decides whether/how to search; it just receives the results):

1. Similar existing memories (`ops/similarity.find_similar_existing`) —
   unchanged from before.
2. Relevant snippets from **other past conversations**
   (`ops/conversation_search.search_conversations`) — new. Plain
   keyword-overlap search over this gateway's own conversation store, so
   a nominated candidate can be corroborated, contradicted, or revealed
   as a one-off by earlier sessions instead of being judged on this
   session's framing alone.

`conversation_search.py` is deliberately a single, shared implementation
rather than something built directly into the write pass: the Gap Engine
worker will eventually want the same "search this user's history"
capability, and since it runs in its own isolated container rather than
the gateway process, two independent components each growing their own
private reach into *all* stored conversations would be worse than one
piece of logic with two access paths. The write pass calls it in-process
(as a normal function); a new route,
`GET /api/memory/conversations/search`, exposes the identical logic over
HTTP for whenever the worker's investigate/report protocol is built —
that route has no caller yet outside tests and the write pass. No project/
workspace filter is needed here: AuroraCoder runs exactly one workspace
per gateway instance, so every conversation this gateway has ever stored
already belongs to the same project.

## What was deliberately left unfinished (and why)

- **Gap Engine investigation protocol.** `dispatcher.py` can spawn/snapshot/
  teardown a worker container **and reach it** (see "DooD infrastructure"
  below — that part used to be broken, now isn't), but doesn't yet drive it
  through a "here's the gap, investigate with tools, report findings"
  exchange — that needs a defined one-shot task contract against
  `src/web_api` (likely a dedicated `report_findings` tool the worker's
  agent calls, parsed by the dispatcher). Building that without a live
  Docker integration-test loop risked shipping a plausible-looking but
  untested fake. The container lifecycle + networking plumbing it will sit
  on top of is real and tested. `GET /api/memory/conversations/search` is
  already there and tested for when this gets built, so the worker doesn't
  need its own separate path into conversation history (see "Why the write
  pass isn't an agent" above).

### DooD infrastructure (fixed — was silently broken before)

Three real infra gaps were found when actually tracing through what
`dispatch_gap_investigation` would need to work end-to-end, none of which
any test caught since everything was mocked at the `docker`-binary
boundary:

1. **Docker CLI in the image** — turned out to already be installed
   (`docker-ce-cli` in `docker/Dockerfile.base`, added earlier for
   ToolStore's own DinD support), so no change needed here.
2. **Docker socket never mounted into the main container** —
   `launcher/docker.go`'s `startContainer`/`startGpuContainer` had no
   `-v /var/run/docker.sock:/var/run/docker.sock`, so the CLI being
   present didn't matter; there was no daemon to talk to. Fixed, but
   gated: `memoryDockerSocketArgs()` only adds the mount when
   `settings.other.memory.heavy_ops_enabled` is on in `settings.json`,
   checked at container-creation time (a bind mount can't be retrofitted
   onto a running container, so this requires a relaunch after flipping
   the setting). Mounting the host's Docker socket is root-equivalent
   host access, so this fails closed on any read/parse error and stays
   off for the overwhelming majority of installs that never touch heavy
   ops.
3. **Wrong networking model** — the original plan was to publish the
   worker's port (`-p 0:8080`) and reach it via `localhost:<port>` +
   `docker port` discovery. That only works for host→container, not
   container→container: the main container and the worker are *sibling*
   containers on the host's Docker daemon (DooD, not nested), so
   `localhost` inside the main container never reaches a sibling no
   matter what it publishes. Fixed by putting both containers on a
   shared **user-defined** bridge network (`MEMORY_NETWORK_NAME` —
   Docker's embedded DNS/name resolution only works on user-defined
   networks, never the default `bridge`) and addressing the worker by
   container name (`worker_base_url()`) instead of a discovered port.
   `ensure_memory_network()` creates the network and self-connects the
   already-running main container to it (containers CAN be attached to
   an additional network after creation, unlike a volume mount) every
   time a worker is spawned, tolerating "already exists"/"already
   connected" as success.
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
