# Code Quality Concerns

Issues identified during a comparative analysis against OpenCode (May 2026).
Sorted by severity. Each item includes the affected file, what's wrong, and
a suggested fix direction.

---

## P0 — Fix Immediately

### 1. Hardcoded Google API key in source code

**File:** `src/core_tools/google_search.py` line 63–64

**Problem:** A live Google API key and Custom Search Engine ID are committed
as string literals:

```python
my_api_key: str = "[REDACTED]"
my_cse_id: str = "[REDACTED]"
```

If the repo is cloned, shared, or leaked, the key is compromised.

**Fix:** Move to environment variables (already done for other keys in
`config.py`). Rotate the current key. Add to `.env.example`.

---

### 2. `full_file_write` is not atomic

**File:** `src/code_tools/file_operations.py` lines 244–252

**Problem:** `edit_file` correctly uses temp file + `os.replace()` for atomic
writes, but `write_file` opens the target file directly and writes to it. A
crash or Docker kill mid-write corrupts the file with a partial write.

**Fix:** Apply the same temp-file + `os.replace()` pattern used in
`search_replace_edit` (lines 200–208).

---

### 3. CORS misconfiguration

**Files:** `src/web_api/app.py` lines 439–446, `conversation_history/api.py`
lines 503–509

**Problem:** Both services use `allow_origins=["*"]` with
`allow_credentials=True`. This combination is invalid per the CORS spec —
browsers will reject credentialed requests with a wildcard origin. More
importantly, if these ports are ever network-exposed, any website can make
authenticated requests to the API.

**Fix:** Replace `"*"` with explicit allowed origins (e.g.
`["http://localhost:3000"]`), or drop `allow_credentials` if cookies aren't
needed.

---

## P1 — Fix Soon

### 4. Agent file tools have no workspace containment

**File:** `src/code_tools/file_operations.py` lines 349–355

**Problem:** `_resolve_path` joins `workspace_root / relative_path` but never
checks that the resolved path stays within the workspace. Paths like
`../../etc/passwd` or `../other_session/` can escape. The web API endpoints
(`app.py` lines 1063–1067, 1096–1098) DO have containment checks — the agent
tools don't.

**Fix:** After resolving, call `resolved.resolve()` and verify
`resolved.is_relative_to(workspace_root)`. Return an error if not.

---

### 5. `delete_file` silently removes entire directories

**File:** `src/code_tools/file_operations.py` lines 282–284,
`src/tool_definitions.py` lines 162–174

**Problem:** The tool schema says "Deletes a specified file" but the
implementation uses `shutil.rmtree` for directories. An LLM calling
`delete_file("src/")` would wipe the entire source tree with no confirmation.

**Fix:** Either make the schema honest ("file or directory"), add a
confirmation step for directories, or refuse to delete directories entirely
(require `run_terminal_command("rm -rf ...")` which is more explicit).

---

### 6. `edit_file` has a corner-case bug with empty search content

**File:** `src/code_tools/file_operations.py` lines 182–187

**Problem:** If `search_content` is empty and `found_idx == 0`, the
newline-preservation logic accesses `original_lines[found_idx + search_len - 1]`
which evaluates to `original_lines[-1]` — the last line of the file instead
of the first. This is unlikely to trigger (the schema requires
`search_content`) but is a real logic error.

**Fix:** Guard with `if search_len > 0` before the newline-preservation block.

---

### 7. `grep_search` include pattern matches filename only, not path

**File:** `src/code_tools/grep_search.py` lines 85–88

**Problem:** `include_pattern` is matched against `file_path.name` (just the
filename), not the relative path. A pattern like `core_tools/*.py` won't
work as users expect — it only matches filenames like `*.py`.

**Fix:** Match against the path relative to workspace root instead of just
the filename.

---

### 8. `grep_search` highlight can attach to wrong occurrence

**File:** `src/code_tools/grep_search.py` lines 176–177

**Problem:** Highlight uses `line_content.replace(matched_text, ..., 1)` which
replaces the first literal occurrence. But the regex match position might not
be the first occurrence on the line, causing the highlight to attach to the
wrong substring.

**Fix:** Use the match's start/end position to insert highlight markers at the
correct offset.

---

## P2 — Address When Convenient

### 9. Code interpreter re-reads all files on every tool call

**File:** `src/main_flow.py` lines 526–540,
`src/code_tools/code_interpreter.py`

**Problem:** After any file-related tool executes, ALL open files are re-read
from disk and Pyright is re-run on each `.py` file (one subprocess per file).
With 5 open Python files, that's 5 disk reads + 5 Pyright subprocesses per
iteration. No caching of unchanged files, no incremental updates, no batched
Pyright.

**Fix direction:**
- Cache file contents + mtime; skip re-read if unchanged.
- Batch Pyright calls (one invocation for multiple files).
- Only re-check files that were modified in the current iteration.

---

### 10. `web_api/app.py` is 1200 lines of mixed concerns

**File:** `src/web_api/app.py`

**Problem:** Routes, streaming logic, SSE generation, file upload/download,
session management, workspace operations, cancellation, and health checks are
all in one file. This makes debugging, code review, and onboarding harder.

**Fix direction:** Split into modules:
- `routes/chat.py` — streaming + SSE
- `routes/sessions.py` — session CRUD
- `routes/workspace.py` — file upload/download, workspace ops
- `middleware.py` — CORS, error handling
- `streaming.py` — SSE generator logic

---

### 11. Web browser cache is not thread-safe

**File:** `src/core_tools/web_browser.py` lines 42–72

**Problem:** The TTL LRU cache uses an `OrderedDict` without synchronization.
Concurrent tool calls from parallel read-only execution could corrupt the
dict. CPython's GIL mostly protects against this, but it's not guaranteed.

**Fix:** Wrap cache access in a `threading.Lock`, or use
`functools.lru_cache` with TTL wrapper.

---

### 12. `active_streams` dict has race conditions

**File:** `conversation_history/api.py`

**Problem:** Reads like `active_streams.get(conversation_id)` on the resume
path and `parent = active_streams.get(stream.parent_id)` in the proxy loop
occur without always holding `_streams_lock`. Deletion in `finally` is guarded,
but transient inconsistencies are possible.

**Fix:** Consistently acquire `_streams_lock` for all reads and writes.

---

### 13. SSE backpressure drops events silently

**File:** `conversation_history/api.py` lines 317–321

**Problem:** `put_nowait` + `except QueueFull: pass` silently drops SSE events
when a subscriber falls behind. This can desync the frontend if the client is
on a slow connection.

**Fix:** Either increase queue size, use a bounded queue with backpressure
signaling, or log dropped events as warnings.

---

### 14. Backend SSE keepalive sends no actual frame

**File:** `src/web_api/app.py` lines 358–366

**Problem:** The timeout branch comments mention keepalive but just
`continue`s without yielding an SSE comment frame. The conversation proxy
correctly sends `: keepalive\n\n`, but the backend doesn't. Long-running tool
calls with no output could cause proxy/load-balancer timeouts.

**Fix:** Yield `: keepalive\n\n` in the timeout branch, matching the proxy's
behavior.

---

## P3 — Nice to Have

### 15. No test coverage

**Problem:** Zero test files in the entire codebase. No unit tests for
`edit_file` matching, no integration tests for streaming, no tests for path
resolution or tool dispatch.

**Fix direction:** Start with the highest-risk areas:
- `edit_file` search/replace matching (edge cases, whitespace, encoding)
- `_resolve_path` containment
- Tool dispatch + error handling
- SSE event parsing/generation

---

### 16. Frontend has no TypeScript and ships debug flags

**Files:** `frontend/src/utils/streamUtils.js` line 1–2,
`frontend/src/services/api.js`

**Problem:** `const DEBUG = true` is committed. Verbose `console.log`
throughout `api.js`. The entire frontend is plain JavaScript with no type
checking — SSE event schemas between three services have no typed contracts.

**Fix:** Migrate to TypeScript incrementally. Remove or gate debug logging
behind `import.meta.env.DEV`.

---

### 17. `edit_file` has no fuzzy matching fallback

**File:** `src/code_tools/file_operations.py` lines 105–223

**Problem:** The algorithm does a single sliding-window exact match (after
trailing whitespace normalization). LLMs frequently produce edits with wrong
indentation, slight whitespace differences, or reordered blank lines. The
tool just fails, forcing the model to retry and burning iterations/tokens.
Aider handles this with multiple fallback strategies (relative indent
matching, diff-match-patch, stripped blank line comparison).

**Fix direction:** Add a secondary matching pass with relaxed rules (e.g.
strip all leading whitespace, or try relative indent adjustment) before
returning "not found".

---

### 18. `file_search` does full `rglob('*')` over workspace

**File:** `src/code_tools/file_operations.py` line 323

**Problem:** Searches the entire workspace tree with no `.gitignore` respect,
no depth limit, and no result limit. On large monorepos this will be very
slow and return noisy results.

**Fix:** Respect `.gitignore` via `pathspec` library or shell out to
`git ls-files`. Add a result limit.

---

### 19. Unused imports and dead code

**Files:** `src/code_tools/file_operations.py`,
`src/code_tools/code_interpreter.py`

**Problem:** Unused imports (`subprocess`, `difflib`, `ast`, `sys`,
`StringIO`), dead sentinels (`WILDCARD_SENTINEL`, `EDIT_ZONE_MARKER`), and
misleading alias (`READ_ONLY_TOOLS = PARALLEL_SAFE_TOOLS` at
`tool_definitions.py` line 389 — name implies safety guarantee that differs
from `SUBAGENT_READ_ONLY_TOOLS`).

**Fix:** Run a linter pass. Remove dead imports and rename the alias.

---

### 20. Synchronous threading model caps at 4 concurrent sessions

**File:** `src/web_api/app.py` lines 34–35

**Problem:** `ThreadPoolExecutor(max_workers=4)` means at most 4 simultaneous
chat generations. The conversation proxy is async, but the backend is fully
synchronous — half the stack is async, half isn't.

**Impact:** Acceptable for single-user Docker deployment today. Becomes a
hard ceiling if multi-user or multi-agent concurrency is ever needed.

**Fix direction:** Long-term, migrate the backend to async. Short-term,
make `max_workers` configurable via env var.
