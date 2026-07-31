# AuroraCoder — QA Unit-Test Plan

Status: **in progress** — branch `test/add-unit-test-suite`. This document is the
QA-engineer design for "unit tests that ensure every module works right," plus
the implementation map and the findings surfaced so far.

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
| `gateway.conversation_store` | ⬜ | SQLite CRUD/migrations in `tmp_path` |
| `gateway.settings_store` | ⬜ | obfuscation round-trip, missing-file defaults |

### Side-effecting (subprocess / FS / network)
| Module | Status | Notes |
|---|---|---|
| `src.code_tools.grep_search` | ✅ | exact cmd build + tool contract via `FakeSubprocess` |
| `src.code_tools.file_operations` | ✅ | read/write/delete/list/search, large-file guard, wrappers, **xfail: path traversal gap** |
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
| `src.web_api.app` | ⬜ | FastAPI app wiring smoke |
| `src.main_flow` / `core_tools.subagent` | ⬜ | inject `FakeLLMClient` + fake tool_executor |

### Frontend (`frontend/`, `mobile/`)
Currently **no JS test framework**. Recommended: Vitest + jsdom + @testing-library +
msw (reuses the existing Vite config). Prime targets: `utils/streamUtils.js`,
`utils/auth.js`, `services/api.js`, `i18n/translations.js` (key-completeness),
`hooks/createStreamCallbacks.js`. Extract pure helpers from `SettingsPanel.jsx`
/`FileTree.jsx`/`ToolActivity.jsx` before component testing. — ⬜ (separate PR recommended).

## 4. Coverage gate (target)

```
pytest --cov=src --cov=gateway --cov=memory --cov-fail-under=85
```
Pure modules → ~95–100%; orchestration modules realistically land ~70–80% via
injected fakes (the remainder is integration-layer territory).

## 5. Updated recommendations

- Adopt the **injected-fake-client** pattern project-wide (the memory ops tests
  already do; `conftest` generalizes it).
- Keep `requirements-dev.txt` authoritative for QA tooling.
- Run ruff + pytest in CI (`.github/workflows/ci.yml` — to add).

## 6. Known issues (pre-existing, NOT introduced by this branch)

Baseline (`dev`, untouched): **42 failed, 112 passed**. With this branch:
**42 failed, 199 passed (+87), 1 xfail, 0 new regressions.** The 42 are
environment/behaviour-drift failures that pre-date this work:

- `tests/test_edit_file_edge_cases.py` (≈30): the `edit_file` tool lowercases
  single-line content and otherwise drifts from the encoded contract
  (e.g. `test_single_line_file` expects `'ONLY\n'`, gets `'only\n'`).
  **Finding:** test-staleness / behaviour drift in `edit_file` — needs a
  contract decision (preserve-case vs normalize) and either fix or test update.
- `tests/test_memory_toggle.py` (≈8) + a few `test_memory_layer1`/`layer3`
  gateway tests: `memory_enabled()` returns `True` because a host
  `settings.json`/env enables memory; the tests assume disabled-by-default and
  are not isolated from host env. **Finding:** the toggle tests leak host env
  — they should run under `env_isolated` + a tmp settings store (the very
  hermeticity pattern added here would fix them).

These are filed for follow-up; out of scope for the *add-unit-tests* task, but
the new infrastructure is exactly what's needed to harden them.