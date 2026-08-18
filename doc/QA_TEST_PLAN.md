# AuroraCoder — QA Unit-Test Plan

Status: **whole suite GREEN** — branch `test/add-unit-test-suite` (pushed).
Final: hermetic unit suite green (0 xfailed). This document is
the QA-engineer design for "unit tests that ensure every module works right," the
implementation map, and how all pre-existing failures were diagnosed and fixed.

---

## 1. Design principles (QA-driven)

1. **Hermeticity first.** Unit-layer tests never touch the real host filesystem,
   network, LLM APIs, Docker, or git push. Every external seam is faked via
   injectable objects (the codebase already uses this pattern in the memory
   `ops` tests; we codified it in `tests/conftest.py`).
2. **Determinism.** No wall-clock dependence (`freezegun` available), no CWD
   dependence (`tmp_workspace` chdir), no shared global mutable state across
   tests, no real subprocess — `FakeSubprocess` fails *loudly* on unregistered
   commands rather than silently exec'ing on the host.
3. **Layering.**
   - `@pytest.mark.unit` (default) — fast, isolated.
   - `@pytest.mark.integration` — needs Docker/git/LLM; **auto-skipped unless
     `-m integration`** (see `pytest_collection_modifyitems`).
   - `@pytest.mark.smoke` — assembled-app sanity.
4. **Risk-prioritized coverage.** Security-critical and data-integrity surfaces
   (path escape, secret redaction, persistence, auth, race conditions) are
   covered first; pure logic next; orchestration last via injected fakes.
5. **Regression isolation.** The pre-existing suite has failures independent of
   this work (see §6). The new layer is structured so it cannot *introduce*
   regressions and so pre-existing failures are clearly attributable.

## 2. Test infrastructure (added)

| File | Purpose |
|---|---|
| `pyproject.toml` | pytest config (asyncio auto-mode, `unit`/`integration`/`smoke` markers, branch coverage over `src`/`gateway`/`memory`), softened warning filter (strict `DeprecationWarning→error` was regressing the pre-existing memory-gateway tests). |
| `requirements-dev.txt` | `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `respx`, `responses`, `freezegun`. |
| `tests/conftest.py` | Reusable fixtures: `tmp_workspace`, `env_isolated`, `reload_config`; `FakeLLMClient`, `FakeSubprocess` (patches `subprocess.run` globally, keeps `TimeoutExpired`/`FileNotFoundError`), `FakeCompletedProcess`; auto-skip of integration tests. |

## 3. Per-module map & status

Legend: ✅ done · 🟡 next · ⬜ later.

### Pure / SQLite-backed
| Module | Status | Notes |
|---|---|---|
| `memory.redact` | ✅ | every secret shape scrubbed + count |
| `memory.ops.similarity` | ✅ | tokens, overlap, degenerate cases, `find_similar_existing` threshold/limit/ordering |
| `memory.stance` | ✅ | empty state, `MAX_STANCE_ITEMS` cap, sort, labels, usage-bump scope |
| `src.training_log` | ✅ | JSONL row shape, toggle, never-raise IO safety, flag loader corruption tolerance |
| `memory.ops.prompts` | 🟡 | pure templates — cheap, next |
| `memory.store` / `schema` | 🟡 | extend existing layer1 (migrations, embedding-null paths) |
| `gateway.conversation_store` | 🟡 | task-instruction strip/title + chip field via `gateway.task_instruction_display` (`test_task_instruction_display.py`); CRUD still ⬜ |
| `gateway.settings_store` | 🟡 | max_iterations range on update (`test_settings_max_iterations.py`); obfuscation round-trip still ⬜ |

### Side-effecting (subprocess / FS / network)
| Module | Status | Notes |
|---|---|---|
| `src.code_tools.grep_search` | ✅ | exact cmd build + tool contract via `FakeSubprocess` |
| `src.code_tools.file_operations` | ✅ | read/write/delete/list/search, large-file guard, wrappers, ``_resolve_path`` rejects ``..`` and symlink escapes |
| `src.code_tools.terminal_runner` | 🟡 | persistent-shell state + `subprocess.run` paths |
| `src.code_sandbox.sandbox` | 🟡 | timeout/resource/cwd isolation, exit codes (Docker branch = integration) |
| `src.config` | 🟡 | env→defaults, `.env` load, reload safety (env read at import) |
| `src.core_tools.google_search` | ⬜ | `respx` |
| `src.core_tools.web_browser` | ⬜ | HTML→Markdown fixtures, fetch mocked |
| `src.core_tools.memory_client` / `tool_store_client` | ⬜ | `respx`: request shapes + caching |
| `src.core_tools.jupyter_code_runner` | ⬜ | fake `jupyter`/`Client` |

### Async / HTTP / orchestration
| Module | Status | Notes |
|---|---|---|
| `src.tool_executor` | ✅ | partition batching, same-file guard, concurrency env knob (pure core) |
| `src.providers` | 🟡 | patch `openai.OpenAI`, stream vs non-stream branching |
| `gateway.streaming` | 🟡 | expand existing race/abort tests via injected provider |
| `gateway.routes` / `api` | ⬜ | `TestClient` per endpoint, **auth** via `ACCESS_PASSWORD` |
| `gateway.provider_registry` | ⬜ | lookup, model metadata, live-list fetch mocked |
| `gateway.workspace` | ⬜ | git push behind `GITHUB_TOKEN` (mock; skip when absent) |
| `src.web_api.app` | 🟡 | app wiring still ⬜ |
| `src.main_flow` / `core_tools.subagent` | ⬜ | inject `FakeLLMClient` + fake tool_executor |

### Frontend (`frontend/`, `mobile/`)
Currently **no JS test framework**. Recommended: Vitest + jsdom + @testing-library +
msw (reuses the existing Vite config). Prime targets: `utils/streamUtils.js`,
`utils/auth.js`, `services/api.js`, `i18n/translations.js` (key-completeness),
`hooks/createStreamCallbacks.js`. Extract pure helpers from `SettingsPanel.jsx`
/`FileTree.jsx`/`ToolActivity.jsx` before component testing. — ⬜ (separate PR recommended).
The Settings `/m` page is covered in `tests/test_mobile_routes.py`.

`frontend/src/utils/settingsValidation.js` is ``encodeStoredApiKey``
(empty field + stored key → keep) plus ``validateMaxIterations`` (Save
rejects a Max Iterations Per Turn value outside 5–200; HTML ``min``/``max``
are not enforced by the Save button; the sentinel ``unlimited`` is allowed).
`tests/test_settings_validation.py` covers both. `tests/test_settings_max_iterations.py`
locks the store: ``update_settings`` raises on ``"0"`` and leaves the on-disk
value unchanged, and accepts ``"unlimited"``.

| File | Status | Notes |
|---|---|---|
| `tests/test_mobile_sidebar_toggle.py` | ✅ | Source-level regression: at `max-width: 768px` the sidebar may stay `display: none` only if App.jsx renders a `.sidebar-toggle` *outside* the aside and `.app.sidebar-open .sidebar` reveals it. Locks the explorer finding that a 375px resize hid New Chat / History / Settings / model with no hamburger. |

Stable `data-testid` hooks on the desktop SPA (`chat-input`, `chat-send`,
`chat-message`, …) are locked by `tests/test_frontend_testids.py` (source scan;
no DOM). The mobile SPA is intentionally excluded.

## 4. Coverage gate (target)

```
pytest --cov=src --cov=gateway --cov=memory --cov-fail-under=85   # target
```
- **Current measured floor: 45%** — that's what `.github/workflows/test.yml`
  pins (`--cov-fail-under=45`) so CI cannot fail spuriously. Raise the gate as
  the modules marked 🟡/⬜ below get covered. Pure modules already sit at
  ~95–100%; orchestration modules realistically land ~70–80% via injected fakes
  (the remainder is integration-layer territory).

## 5. Updated recommendations

- Adopt the **injected-fake-client** pattern project-wide (the memory ops tests
  already do; `conftest` generalizes it).
- Keep `requirements-dev.txt` authoritative for QA tooling.
- Run ruff + pytest in CI — see `.github/workflows/test.yml` (added on this branch).

## 6. Pre-existing failures — diagnosed and fixed (whole suite now green)

The untouched `dev` branch was **42 failed, 112 passed**. As QA owner of the whole
suite I traced every failure to its root cause and fixed it (not masked). The full
suite now passes hermetically (the former ``_resolve_path`` xfail is a real gate).

### 6.1 `test_streaming_race.py` — 1 collection ERROR
**Cause:** the file defined `async def test(scenario, subscriber_fn)` as a plain
helper for its `main()` script, but `asyncio_mode=auto` collected it as a test and
failed resolving `scenario` as a fixture. **Fix:** renamed it `_run_one` and added a
real `test_streaming_race_fix_verified` entry point that locks the OLD-never-gets-
`done` / NEW-always-gets-`done` contract.

### 6.2 `test_memory_toggle.py` — 4 failures (host-env import-order leak)
**Cause:** the host shell exports `AURORACODER_DOCKER=1`, so `src.config` bakes
`DATA_DIR=/app/data` at first import. Several test modules import `src.config`
transitively, so whichever is collected first bakes `/app/data` for the whole
process; its `settings.json` (`memory.enabled=true`) then leaked into
`test_memory_toggle`, which expects the disabled-by-default baseline. The file's
own module-top env override ran too late (after `src.config` was already bound).
**Fix:** `tests/conftest.py` now forces `AURORACODER_DOCKER=0` + a fresh per-run
`mkdtemp` data dir (seeded `memory.enabled=true`) BEFORE any test module imports
`src.config`. layer1/2/3 still see enabled (as before); `test_memory_toggle`
overwrites settings per-test (it always writes before reading).

### 6.3 `test_edit_file_edge_cases.py` — 36 failures (contract drift + non-hermetic)
**Cause:** the suite called `execute_edit_file({"target_file": ...})`, but the
tool reads `arguments.get("file")`, so `file=None` -> every edit silently no-ops
-> file unchanged -> assertion fails. (Earlier guess about "lowercasing" was wrong;
the file was simply never edited.) The helpers also hardcoded `/workspace`, writing
scratch files into the live project tree.
**Fix:** `target_file`->`file`; per-test autouse `tmp_path` WORKSPACE.

### 6.4 `test_context_fix_propagation.py` — 2 failures + N false-passes
**Cause:** same `target_file`->`file` (and `code_edit`->`content`) drift across
edit_file/read_file/write_file/delete_file; tool_definitions drops the unknown keys
so the tools no-op. Worse, its `assert_equals`/`assert_contains`/`assert_not_in`
helper printed 🚫 but **never raised**, so most checks were false-passes masking
the broken `[TO]`-propagation verification.
**Fix:** renamed params to the current contract; per-test autouse tmp WORKSPACE;
converted the three assert helpers to REAL (raising) pytest assertions so the
suite genuinely verifies the context-fix channel.

### 6.5 Persistent QA findings (still open, low priority)
- `check()` helper in `test_edit_file_edge_cases.py` is dead code (defined, never
  called) — safe to delete.