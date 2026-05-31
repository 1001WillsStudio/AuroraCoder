# Aurora Coder — Agent Knowledge Base

**Aurora Coder** is an advanced AI coding agent framework designed for complex coding and research tasks. It leverages native OpenAI function calling with extended thinking/reasoning capabilities to provide a robust and precise interface for agentic operations.

> **Note to AI agents**: This document gives you a complete understanding of the codebase in a single read — it replaces exploratory `read_file` on every source file.

---

## Core Philosophy

This project implements a **Code Agent** architecture with a strict separation of concerns:

### Architectural Layers

```
src/                          ← Stateless agent loop (messages in → messages out)
    main_flow.py              ← Pure: takes messages, streams responses, yields statuses
    tool_executor.py          ← Parallel/serial tool execution dispatch
    tool_definitions.py       ← Pure: tool schemas + dispatch, all return strings
    training_log.py           ← Daily JSONL training data logging
    All tools are stateless   ← No conversation-store access, no direct persistence

gateway/         ← Middleware between frontend and backend (the "dirty work")
    api.py                    ← FastAPI app factory + CORS (port 8081, internal)
    routes.py                 ← SSE proxy, chat/continue endpoints, file display
    streaming.py              ← SSE stream management, event queues, keepalive
    conversation_store.py     ← File-backed store (thread-safe, atomic writes)
    settings_store.py         ← Provider & model settings persistence
    provider_registry.py      ← Dynamic provider registration and listing
    workspace.py              ← File diff, tree, upload/delete/export utilities

frontend/                     ← UI + conversation ownership
    App.jsx                   ← React SPA, owns conversation state
    components/               ← 11 components (ChatInput, ChatMessage, CodePanel,
                                 ConversationHistory, FileTree, LoginScreen,
                                 SettingsPanel, Sidebar, ThinkingIndicator,
                                 ToolActivity, WelcomeScreen)
    hooks/                    ← useAutoScroll, useFileTracking, useLanguage,
                                 createStreamCallbacks
    services/api.js           ← SSE streaming client
    utils/                    ← auth, injectToolStop, streamUtils
    i18n/                     ← translations.js (27KB), LanguageContext
    styles/                   ← 12 domain CSS files (tokens, layout, messages,
                                 sidebar, settings, tool-activity, code-panel,
                                 file-tree, input, welcome, reset, responsive)
mobile/                       ← Standalone vanilla JS mobile web app (api.js,
                                 app.js, auth.js, chat.js, mobile.css)
launcher/                     ← One-click Go launcher (main.go, docker.go,
                                 extract.go, progress.go, build.sh)
```

**The rule**: `src/` never touches the conversation store. It just processes messages and returns signals. The gateway layer intercepts SSE events and handles all persistence, conversation creation, status management, and context-window monitoring. This keeps the agent loop testable, swappable, and dead-simple.

### Capabilities

- **Persistent Terminal Access**: Stateful Bash sessions for running system commands, git operations, and environment management.
- **Direct File Manipulation**: Full read/write capabilities on the codebase with intelligent code display.
- **Native Tool Calling**: Structured, reliable OpenAI function calling format.
- **Extended Thinking**: Supports models with reasoning/thinking capabilities (e.g., DeepSeek, GLM).
- **Docker Sandbox**: Runs in a container with a fixed `/workspace` directory and pre-built conda environment.
- **VNC Desktop**: Xvfb + fluxbox + noVNC on port 6080 for GUI applications.
- **Sub-Agent Delegation**: Delegates tasks to read-only child agents via HTTP.
- **ToolStore Integration**: Universal tool discovery — search, inspect, and invoke tools from MCP servers, local skills, and tool packs.
- **Living Tool State**: Tool responses are mutable — old code interpreter blocks are stripped, only the latest consolidated file display stays in context.

---

## 1. What Is Aurora Coder?

Aurora Coder is an **autonomous AI agent framework** that wraps LLMs with native OpenAI function-calling tools. It gives an LLM the ability to:

- Read/write/edit/delete files on a real filesystem
- Run terminal commands in a persistent shell (with background process management)
- Search the web (Google CSE)
- Fetch and summarise web pages (via a cheap secondary model)
- Delegate sub-tasks to sub-agents
- Search code with real grep (subprocess wrapper)
- Display files in a consolidated "code interpreter" view
- Discover and invoke tools from MCP servers, skill packs, and local tool stores
- Display plots and GUI applications via VNC

The agent runs inside a **Docker container** with an optional VNC desktop for GUI apps.

---

## 2. Repository Layout

```
Aurora Coder/
├── src/
│   ├── __init__.py
│   ├── main_flow.py            ← THE CORE: chat loop, streaming, tool execution
│   ├── tool_definitions.py     ← All tool schemas + function dispatch
│   ├── tool_executor.py        ← Parallel/serial tool executor
│   ├── config.py               ← ALL config: API keys, limits, system prompt
│   ├── providers.py            ← Multi-provider LLM client manager
│   ├── training_log.py         ← Daily JSONL training telemetry
│   ├── code_tools/
│   │   ├── __init__.py
│   │   ├── file_operations.py  ← read/write/delete/list/search/close
│   │   ├── edit_file.py        ← Anchor-matching engine (tolerant ±3 line search)
│   │   ├── terminal_runner.py  ← Persistent shell command execution
│   │   ├── grep_search.py      ← Real grep subprocess wrapper
│   │   ├── code_interpreter.py ← Consolidated file display (line numbers)
│   │   ├── context_manager.py  ← Living Tool State: open/display/strip files
│   │   ├── context_tracker.py  ← Abstract ContextTracker base class
│   │   └── toolset_context_manager.py ← Living Tool State for ToolStore tools
│   ├── core_tools/
│   │   ├── __init__.py
│   │   ├── google_search.py    ← Google Custom Search
│   │   ├── web_browser.py      ← URL fetch → HTML→MD → secondary-model summary
│   │   ├── subagent.py         ← HTTP-based sub-agent delegation
│   │   ├── tool_store_client.py← ToolStore integration wrapper
│   │   ├── jupyter_code_runner.py ← Jupyter-style code execution
│   │   └── continue_chat.py    ← continue_as_new_chat tool
│   ├── code_sandbox/
│   │   ├── __init__.py
│   │   └── sandbox.py          ← Workspace path (/workspace) + persistent shell
│   └── web_api/
│       ├── __init__.py
│       └── app.py              ← FastAPI backend server (port 8080, agent loop)
├── gateway/
│   ├── __init__.py             ← Re-exports ConversationStore
│   ├── api.py                  ← FastAPI app factory (port 8081, internal)
│   ├── routes.py               ← SSE proxy, chat/continue/stream endpoints
│   ├── streaming.py            ← SSE stream management, event queues, keepalive
│   ├── conversation_store.py   ← File-backed store (thread-safe, atomic writes)
│   ├── settings_store.py       ← Provider/model settings persistence
│   ├── provider_registry.py    ← Dynamic provider registration
│   └── workspace.py            ← File diff, tree, upload/delete/export
├── frontend/                   ← React + Vite web UI
│   ├── src/
│   │   ├── App.jsx             ← Main app (~800 lines, refactored from ~1465)
│   │   ├── main.jsx
│   │   ├── constants.js
│   │   ├── components/         ← 11 components
│   │   ├── hooks/              ← useAutoScroll, useFileTracking, useLanguage,
│   │   │                          createStreamCallbacks
│   │   ├── services/api.js     ← SSE streaming client
│   │   ├── utils/              ← auth, injectToolStop, streamUtils
│   │   ├── i18n/               ← translations.js, LanguageContext
│   │   └── styles/             ← 12 domain CSS files
│   ├── server.py               ← Python static file server for production
│   ├── package.json
│   └── vite.config.js
├── mobile/                     ← Standalone vanilla JS mobile web app
│   ├── index.html
│   ├── js/                     ← api.js, app.js, auth.js, chat.js
│   └── css/mobile.css          ← 24KB standalone stylesheet
├── launcher/                   ← Go source for one-click binary (built by CI)
│   ├── main.go                 ← Entry point
│   ├── docker.go               ← Docker image build logic
│   ├── extract.go              ← Embedded project extraction
│   ├── progress.go             ← Terminal progress UI
│   ├── build.sh                ← Cross-compilation (used by CI, outputs to releases)
│   └── go.mod
├── docker/
│   ├── Dockerfile              ← App image
│   ├── Dockerfile.base         ← Base image with conda environment
│   ├── docker-compose.yml      ← Multi-service orchestration
│   ├── entrypoint.sh           ← Container entrypoint
│   └── supervisord.conf        ← Process supervision config
├── dev-scripts/                ← Power-user launch scripts (repo-clone workflow)
│   ├── start.bat / start.sh    ← Docker build + launch + frontend
│   ├── another-one.bat / .sh   ← Multi-instance launcher
│   └── build-base.bat / .sh    ← Base image build
├── tests/                      ← Test suite
│   ├── test_context_fix_propagation.py
│   ├── test_edit_file_edge_cases.py
│   ├── test_mergePanelFiles.mjs
│   └── test_streaming_race.py
├── .github/workflows/release.yml ← CI/CD release workflow
├── .env.example                ← Environment variable template
├── requirements.txt            ← Python dependencies
├── run_web.py                  ← Backend entry point
└── AGENT_README.md             ← This file
```

---

## 3. Central Data Flow

```
User Message
    │
    ▼
gateway/routes.py  ──►  main_flow.generate_chat_responses_stream_native()
                              │
                              ├─ Injects system message (from config.py template)
                              ├─ Builds API call: model + messages + tools
                              ├─ Streams response from LLM provider
                              ├─ Parses tool_call deltas from stream
                              ├─ Executes tools (parallel for read-only, sequential for write)
                              ├─ Manages code interpreter display via ContextTracker system
                              ├─ Manages ToolStore display via ToolsetContextTracker
                              └─ Loops until: completion, max_iterations, or error
                                    │
                                    ▼
                              Yields {messages, status, provider} dicts
                                    │
                                    ▼
                              gateway/streaming.py manages SSE events
                                    │
                                    ▼
                              gateway/routes.py serves streaming SSE to frontend
```

### Iteration loop (in `main_flow.py`):
1. Call LLM with current messages + tools
2. Stream response; collect `content`, `reasoning_content`, `tool_calls`
3. If no tool calls → done (or retry if also no content)
4. If tool calls → execute them, append results as `role: tool` messages
5. If any code-related tool was called → regenerate interpreter display via ContextTracker
6. If `tool_store` was called → regenerate toolset display via ToolsetContextTracker
7. Loop back (max 30 iterations by default, then offers "Continue")

---

## 4. Complete Tool Catalog

### 4.1 Tool Definitions & Function Map

Defined in `tool_definitions.py`. 13 tools (plus 1 conditional):

| # | Tool Name | Function | Parallel-Safe? | Subagent? |
|---|-----------|----------|:---:|:---:|
| 1 | `google_search` | `search_for_llm` | ✅ | ✅ |
| 2 | `web_browser` | `web_fetch` | ✅ | ✅ |
| 3 | `read_file` | `read_file_tool` | ✅ | ✅ |
| 4 | `write_file` | `full_file_write_tool` | ❌ | ❌ |
| 5 | `edit_file` | `range_replace_edit_tool` | ❌ | ❌ |
| 6 | `delete_file` | `delete_file_tool` | ❌ | ❌ |
| 7 | `close_file` | `close_file_tool` | ✅ | ✅ |
| 8 | `list_directory` | `list_dir_tool` | ✅ | ✅ |
| 9 | `search_files` | `file_search_tool` | ✅ | ✅ |
| 10 | `grep_search` | `grep_search_tool` | ✅ | ✅ |
| 11 | `run_terminal_command` | `run_terminal_cmd_tool` | ❌ | ❌ |
| 12 | `tool_store` | `tool_store_tool` | ✅ | ❌ |
| 13 | `subagent` | `run_subagent` | ✅ | ❌ (recursive) |
| * | `continue_as_new_chat` | `continue_chat_tool` | n/a | n/a |

`continue_as_new_chat` is conditionally included — it only appears in the tool list when context usage exceeds ~80%.

### 4.2 Tool Parameter Signatures

```
google_search(search_term: str) → str
web_browser(target_url: str, prompt: str) → str
read_file(target_file: str) → str
write_file(target_file: str, code_edit: str) → str
edit_file(target_file: str, edits: array, remove_line_number: str, content_to_remove: str, replace_content: str) → str
delete_file(target_file: str) → str
close_file(target_file: str) → str
list_directory(relative_workspace_path: str = "") → str
search_files(query: str) → str
grep_search(query: str, include_pattern: str = None, exclude_pattern: str = None, case_sensitive: bool = True, max_lines: int = 200) → str
run_terminal_command(command: str, timeout: int = 30, blocking: bool = True) → str
tool_store(action: str, query: str = None, tool_name: str = None, arguments: dict = None) → str
subagent(task: str) → str
continue_as_new_chat() → str
```

### 4.3 `edit_file` — Anchor-Based Range Replace

**File:** `src/code_tools/edit_file.py` (anchor-matching engine) + `src/code_tools/file_operations.py` (orchestration)

**Parameters (per edit in the `edits` array):**
- `target_file`: Path to the file to edit
- `remove_line_number`: Line range to remove, e.g., `"13-15"` or `"42"`
- `content_to_remove`: Anchor-based block identifier using `[TO]` marker for multi-line ranges
- `replace_content`: New content replacing the specified range (empty to delete)

**Editing rules:**
- At most 3 edits per call — extras are silently dropped
- Same-file edit guard: cannot edit the same file twice in one turn (line numbers are stale until code interpreter refreshes)
- All edits in batch are validated before ANY are applied — if any fail, zero edits touch the file
- Anchor matching uses ±3 line tolerance with two-pass matching (strict then relaxed whitespace)
- When line numbers are auto-corrected, a `<!--SELF_CORRECT:{...}-->` marker patches the LLM's original tool call in-place so the model only sees successful patterns
- **SUPER IMPORTANT**: Always get line numbers from the code interpreter display — never use memorised or assumed line numbers

**Example — single-line replacement:**
```json
{
  "target_file": "src/main.py",
  "edits": [{
    "remove_line_number": "42",
    "content_to_remove": "old_function_name()",
    "replace_content": "new_function_name()"
  }]
}
```

**Example — multi-line replacement:**
```json
{
  "target_file": "src/main.py",
  "edits": [{
    "remove_line_number": "42-45",
    "content_to_remove": "def foo():\n    pass\n\ndef bar():",
    "replace_content": "def foo():\n    return 42\n\ndef bar():"
  }]
}
```

---

## 5. The Code Interpreter System

This is a **display-only** system — it doesn't execute code. It shows file contents with line numbers in a consolidated view.

### Markers
```
CODE_INTERPRETER_START = "<====CODE_INTERPRETER_START====>"
CODE_INTERPRETER_END   = "<====CODE_INTERPRETER_END====>"
```

### Mechanics
1. `ContextTracker` base class in `context_tracker.py` defines the abstract interface
2. `CodeContextTracker` in `context_manager.py` implements file tracking:
   - `discover_open_files(messages)` — scans all assistant messages for `read_file`, `write_file`, `edit_file` calls → adds to `open_files` set. `delete_file` and `close_file` remove from set.
   - `render(state)` — reads all open files, formats them with line numbers, wraps in markers, appends context warning if >5 files or >50K chars.
3. `ToolsetContextTracker` in `toolset_context_manager.py` — same pattern for `tool_store` tools
4. After any tool execution, old interpreter blocks are cleaned and fresh consolidated displays are appended to the LAST tool message.

---

## 6. Configuration (`config.py`)

### Model Providers
| ID | Model | Thinking? | Base URL |
|----|-------|-----------|----------|
| `deepseek` | `deepseek-v4-pro` | ✅ | `api.deepseek.com` |
| `nvidia` | `deepseek-ai/deepseek-v4-pro` | ✅ | `integrate.api.nvidia.com` |
| `nvidia-fast` | same model | ❌ | same |
| `nvidia-glm5` | `z-ai/glm-5.1` | ✅ | same |
| `nvidia-glm5-fast` | same model | ❌ | same |
| `gemini-3-pro` | Vertex AI (3.1 Pro) | ✅ | Google Cloud |
| `gemini-3-pro-api` | AI Studio (3.1 Pro) | ✅ | Google API |

Default: `deepseek`

### Key Limits
```python
MAX_TOKENS = 32768           # Completion token limit
MAX_ITERATIONS = 30          # Loop iterations per user turn
CONTINUE_ITERATIONS = 30     # Extra iterations on "Continue"
MAX_STREAMING_RETRIES = 10
MAX_TOOL_CONCURRENCY = 5     # Parallel threads for read-only tools
SUBAGENT_MAX_ITERATIONS = 15
SUBAGENT_MAX_RESULT_CHARS = 4000
```
(Note: TEMPERATURE was intentionally removed — modern models have proper defaults for agent tasks.)

### Environment Detection
- `AURORACODER_DOCKER=1` → `DOCKER_MODE=True`, workspace at `/workspace`
- `AURORACODER_VNC=1` → `DOCKER_VNC=True`, VNC instructions added to system prompt

### Web Browser (Secondary Model)
- Uses DeepSeek Chat (`deepseek-chat`) to summarise pages
- Cached: 15-min TTL, 64 max entries
- 100K char limit for markdown fed to summariser
- 10MB max HTTP response, 60s timeout

---

## 7. Sandbox (`code_sandbox/sandbox.py`)

The Docker-first sandbox. It provides:

- **`WORKSPACE`** — `Path("/workspace")` (from `WORKSPACE_DIR` env var, falls back to `cwd`)
- **`get_workspace()`** — returns `WORKSPACE`, creating it if needed
- **`get_python_path()` / `get_conda_env_path()`** — resolve the pre-built conda `agent` environment
- **`shell`** — module-level `PersistentShell` singleton

### Persistent Shell
- `shell.run(command, timeout, blocking)` — writes command to bash stdin, waits for boundary marker, reads output from temp file
- `blocking=False` → wraps in `nohup bash -c ... > logfile 2>&1 &`, returns log path
- On timeout → spawns a new shell, returns note about log file
- Conda environment is auto-activated on shell start

---

## 8. Provider System (`providers.py`)

### ProviderManager
- `ProviderManager` — singleton, initializes all configured clients at import time
- `get_client(provider_id)` → OpenAI client (or VertexAIClient)
- `get_config(provider_id)` → dict with model name, extra_body, etc.
- `list_providers()` → only returns successfully-initialized providers

### Vertex AI
- `VertexAIClient` wraps Google Cloud auth with automatic token refresh
- Mimics `OpenAI.chat.completions.create()` interface
- Tokens refreshed before every API call
- Requires `google.auth` package and ADC

---

## 9. Key Files Deep Dive

### `main_flow.py` — THE ENGINE

```
generate_chat_responses_stream_native(
    messages: list,           # OpenAI-format message list
    max_iterations: int,      # default 30
    provider_id: str | None,  # default from config
    tools_override: list | None  # for subagents (read-only subset)
) → Generator[dict]
```

**Yield format**: `{"messages": [...], "status": "running"|"completed"|"error"|"max_iterations_reached", "provider": str}`

**Tool execution**: Two separate tool sets control behavior: `PARALLEL_SAFE_TOOLS` (tools safe for concurrent `ThreadPoolExecutor` execution) and `SUBAGENT_READ_ONLY_TOOLS` (tools granted to subagents in read-only mode). Write tools run sequentially. Batches are partitioned by `partition_tool_calls()`.

**Error handling**: Streaming errors trigger retry up to `MAX_STREAMING_RETRIES` (10). Empty responses with no tool calls get a corrective system message.

### `edit_file.py` — ANCHOR-MATCHING ENGINE

- `find_anchor_tolerant()` — searches within ±3 lines of stated position
- `find_anchor_anywhere()` — fallback whole-file scan
- Two-pass matching: strict (trailing whitespace ignored) → relaxed (all whitespace ignored)
- `indent_delta()` / `adjust_indent()` — indentation-aware correction
- `anchor_hint()` — diagnostic hints with actual locations

### `file_operations.py` — FILE TOOLS

- `read_file` — validates existence, snapshots content for diff tracking
- `write_file` — atomic write via temp file + `os.replace()`
- `delete_file` — handles both files and directories
- `list_directory` — emoji-prefixed listing
- `search_files` — fuzzy filename search
- File tracking callbacks (`set_file_tracking_callbacks`) for gateway diffing

### `context_manager.py` + `context_tracker.py` — LIVING TOOL STATE

- `ContextTracker` — abstract base class (discover + render + strip pattern)
- `CodeContextTracker` — tracks open files, generates consolidated display
- `TOOLSTORE_START/END` blocks for ToolStore tools in `toolset_context_manager.py`
- Previous blocks stripped from old messages; only latest display retained

### `web_browser.py` — WEB FETCH

- HTTP fetch with same-host-only redirect following (cross-host redirects reported)
- HTML → Markdown via `BeautifulSoup` + `markdownify`
- Secondary model summarization via `deepseek-chat`
- LRU cache with TTL (15 min)

### `subagent.py` — SUB-AGENT DELEGATION

- Sends HTTP POST to `CONVO_SERVER_URL/api/chat` (default `http://localhost:8081`)
- Uses `tools: "read_only"` to restrict subagent to safe tools
- Streams SSE response, extracts final assistant message
- Truncates to `SUBAGENT_MAX_RESULT_CHARS` (4000)

### Gateway Layer

- `api.py` — FastAPI app factory with CORS middleware
- `routes.py` — All route handlers: chat, continue, conversations, files, settings, health
- `streaming.py` — SSE stream registration, event queue management, keepalive, cancellation
- `conversation_store.py` — File-backed store with thread-safe atomic writes and index management
- `settings_store.py` — Provider and model settings persistence
- `provider_registry.py` — Dynamic provider discovery and listing
- `workspace.py` — File snapshots, diffs, tree building, upload/delete/export

---

## 10. Getting Started

Two launch methods:

### Easy: One-Click Launcher

Download the pre-built binary from the project's [GitHub Releases](https://github.com/1001WillsStudio/AuroraCoder/releases) page (built automatically by the CI workflow in `.github/workflows/release.yml`):

```bash
./auroracoder       # handles Docker build, start, and opens browser
```

**Requirements**: Docker Desktop only. No git clone, no terminal, no Node.js, no Python needed — the binary embeds the entire project and builds the Docker image on first launch. Subsequent launches are near-instant (cached image).

### Power User: Dev Scripts

For developers who clone the repo and want full control:

**Prerequisites**: Docker, Node.js 18+, and API keys (at least `DEEPSEEK_API_KEY` in `.env`)

```bash
# Clone, set up .env, then run the dev script:
./dev-scripts/start.sh     # Linux/macOS (handles build + launch + frontend)
dev-scripts\start.bat      # Windows
```

Or manually step-by-step:

```bash
docker build -t auroracoder-base -f Dockerfile.base .
docker compose up --build
cd frontend && npm install && npm run dev
```

Services started:
- Backend API: http://localhost:8080 (agent loop)
- Gateway: *internal* `:8081` (SSE proxy, persistence)
- Frontend: http://localhost:3000 (SPA + API proxy)
- VNC Desktop: http://localhost:6080

> **Important:** Do NOT run `python run_web.py` directly on the host. The backend must run inside Docker for proper session isolation, VNC support, and persistent data storage.

### Data Persistence

All persistent runtime data lives under `/app/data` inside the container, volume-mounted to `./data` on the host:

```
data/                        ← host directory (git-ignored)
├── conversations/
│   ├── index.json           ← metadata index for all conversations
│   ├── {id}.json            ← raw API messages per conversation
│   └── {id}.frontend.json   ← UI-shaped messages per conversation
└── training/
    └── YYYY-MM-DD.jsonl     ← daily training data logs
```

Key implementation files:
- `gateway/conversation_store.py` — file-backed store (thread-safe, atomic writes)
- `gateway/routes.py` — all route handlers proxying to the backend
- `gateway/streaming.py` — SSE stream lifecycle management
- `gateway/workspace.py` — file snapshots, diffs, tree building, workspace upload/delete/export
- `src/config.py` — `DATA_DIR` / `TRAINING_DATA_DIR` path resolution

---

## 11. Requirements

Core dependencies (see `requirements.txt`):
- `openai>=1.0.0` — API client
- `fastapi>=0.104.1` — Web API
- `google-api-python-client>=2.169.0` — Google Search
- `pyright` — Python type checking (via nodejs)

Frontend dependencies:
- React 18 + Vite
- react-syntax-highlighter, react-markdown
- 12 domain CSS files with design tokens

---

## 12. Key Patterns & Conventions

- **All tools return strings** — never raise exceptions to the agent
- **Workspace root** comes from `code_sandbox.WORKSPACE` (`/workspace` in Docker)
- **Atomic writes** — temp file + `os.replace()` pattern
- **Tool wrappers** — each tool has a `_tool` suffix function for the registry
- **Global singletons** — `shell` (PersistentShell), `provider_manager`, `code_interpreter`
- **No async** — everything is synchronous, concurrency via threads
- **Streaming** — SSE from main_flow to gateway to frontend
- **ContextTracker pattern** — discover + render + strip for living tool state
- **English only** — all generated code and comments must be in English

---

## 13. Quick Reference: If You Need To...

| Task | Where to look |
|------|---------------|
| Add a new tool | `tool_definitions.py` — add schema + function mapping |
| Change the system prompt | `config.py` → `SYSTEM_MESSAGE_TEMPLATE` |
| Add a new LLM provider | `config.py` → `MODEL_PROVIDERS` + `providers.py` |
| Change iteration limits | `config.py` → `MAX_ITERATIONS`, `CONTINUE_ITERATIONS` |
| Fix tool execution | `tool_definitions.py` → `execute_tool_call()` |
| Change subagent behavior | `core_tools/subagent.py` |
| Modify sandbox / workspace | `code_sandbox/sandbox.py` |
| Change the web API | `web_api/app.py` |
| Change the gateway | `gateway/routes.py` + `gateway/streaming.py` |
| Change the frontend | `frontend/src/` (React + Vite) |
| Understand the edit_file algorithm | `edit_file.py` → `find_anchor_tolerant()` + `find_anchor_anywhere()` |
| Understand context tracking | `context_manager.py` + `context_tracker.py` |
| Understand web fetch pipeline | `web_browser.py` → `web_fetch()` |
| Understand shell execution | `code_sandbox/sandbox.py` → `PersistentShell.run()` |
| Understand SSE streaming | `gateway/streaming.py` |
| Understand conversation persistence | `gateway/conversation_store.py` |

---

## 14. Tests

Test files in `tests/`:
- `test_context_fix_propagation.py` — ContextTracker display update tests
- `test_edit_file_edge_cases.py` — Edit file matching edge cases
- `test_streaming_race.py` — SSE streaming race condition tests
- `test_mergePanelFiles.mjs` — Frontend panel merging tests

---

## License

This project is provided as-is for research and development purposes.
