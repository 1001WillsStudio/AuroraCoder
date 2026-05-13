# ThinkWithTool

**ThinkWithTool** is an advanced AI agent framework designed for complex coding and research tasks. It leverages Native Tool Calling (OpenAI function calling format) with extended thinking/reasoning capabilities to provide a robust and precise interface for agentic operations.

> **Note to AI agents**: This document gives you a complete understanding of the codebase in a single read — it replaces exploratory `read_file` on every source file.

---

## Core Philosophy

This project implements a **Code Agent** architecture with a strict separation of concerns:

### Architectural Layers

```
src/                          ← Stateless agent loop (messages in → messages out)
    main_flow.py              ← Pure: takes messages, streams responses, yields statuses
    tool_definitions.py       ← Pure: tool schemas + dispatch, all return strings
    All tools are stateless   ← No conversation-store access, no direct persistence

conversation_gateway/         ← Middleware between frontend and backend (the "dirty work")
    api.py                    ← SSE proxy + file display endpoints (port 8081)
    conversation_store.py     ← File-backed store (thread-safe, atomic writes)
    workspace.py              ← File diff, tree, upload/delete/export utilities

frontend/                     ← UI + conversation ownership
    App.jsx                   ← React SPA, owns conversation state
```

**The rule**: `src/` never touches the conversation store. It just processes messages and returns signals. The proxy (8081) intercepts SSE events and handles all persistence, conversation creation, status management, and context-window monitoring. This keeps the agent loop testable, swappable, and dead-simple.

### Capabilities

- **Persistent Terminal Access**: Stateful Bash sessions for running system commands, git operations, and environment management.
- **Direct File Manipulation**: Full read/write capabilities on the codebase with intelligent code display.
- **Native Tool Calling**: Structured, reliable OpenAI function calling format.
- **Extended Thinking**: Supports models with reasoning/thinking capabilities (e.g., DeepSeek, GLM).
- **Session Isolation**: Each session gets its own cloned conda environment and working directory.

---

## 1. What Is ThinkWithTool?

ThinkWithTool is an **autonomous AI agent framework** that wraps LLMs with native OpenAI function-calling tools. It gives an LLM the ability to:

- Read/write/edit/delete files on a real filesystem
- Run terminal commands in a persistent shell
- Search the web (Google CSE)
- Fetch and summarise web pages (via a cheap secondary model)
- Delegate sub-tasks to sub-agents
- Search code with regex (grep)
- Display files in a consolidated "code interpreter" view

The agent runs inside a **Docker container** with an optional VNC desktop for GUI apps.

---

## 2. Repository Layout

```
ThinkWithTool/
├── src/
│   ├── main_flow.py            ← THE CORE: chat loop, streaming, tool execution
│   ├── tool_definitions.py     ← All tool schemas + function dispatch
│   ├── config.py               ← ALL config: API keys, limits, system prompt
│   ├── providers.py            ← Multi-provider LLM client manager
│   ├── code_tools/
│   │   ├── file_operations.py  ← read/write/edit/delete/list/search/close
│   │   ├── terminal_runner.py  ← persistent shell command execution
│   │   ├── grep_search.py      ← regex search across workspace
│   │   └── code_interpreter.py ← consolidated file display (line numbers + pyright)
│   ├── core_tools/
│   │   ├── google_search.py    ← Google Custom Search
│   │   ├── web_browser.py      ← URL fetch → HTML→MD → secondary-model summary
│   │   ├── subagent.py         ← HTTP-based sub-agent delegation
│   │   ├── tool_store_client.py← ToolStore integration (optional)
│   │   └── jupyter_code_runner.py ← Jupyter-style Python execution (UNUSED)
│   ├── code_sandbox/
│   │   ├── session_manager.py  ← Session lifecycle, conda envs, persistent shell
│   │   ├── session_utils.py    ← High-level session helpers
│   │   └── session_cli.py      ← CLI for session management
│   └── web_api/
│       └── app.py              ← FastAPI backend server (port 8080, agent loop only)
├── conversation_gateway/       ← Middleware layer (the "dirty work")
│   ├── api.py                  ← SSE proxy + file display endpoints (port 8081)
│   ├── conversation_store.py   ← File-backed store (thread-safe, atomic writes)
│   └── workspace.py            ← File diff, tree, upload/delete/export utilities
├── frontend/                   ← React + Vite web UI
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── run_web.py                  ← Entry point for backend server
```

---

## 3. Central Data Flow

```
User Message
    │
    ▼
web_api/app.py  ──►  main_flow.generate_chat_responses_stream_native()
                          │
                          ├─ Injects system message (from config.py template)
                          ├─ Builds API call: model + messages + tools
                          ├─ Streams response from LLM provider
                          ├─ Parses tool_call deltas from stream
                          ├─ Executes tools (parallel for read-only, sequential for write)
                          ├─ Manages code interpreter display
                          └─ Loops until: completion, max_iterations, or error
                                │
                                ▼
                          Yields {messages, status, provider} dicts
                                │
                                ▼
                          web_api/app.py streams SSE to frontend
```

### Iteration loop (in `main_flow.py`):
1. Call LLM with current messages + tools
2. Stream response; collect `content`, `reasoning_content`, `tool_calls`
3. If no tool calls → done (or retry if also no content)
4. If tool calls → execute them, append results as `role: tool` messages
5. If any code-related tool was called → regenerate interpreter display
6. Loop back (max 30 iterations by default)

---

## 4. Complete Tool Catalog

### 4.1 Tool Definitions & Function Map

Defined in `tool_definitions.py`. 13 tools total:

| # | Tool Name | Function | Read-Only? | Side Effects |
|---|-----------|----------|------------|--------------|
| 1 | `google_search` | `search_for_llm` | ✅ | None |
| 2 | `web_browser` | `web_fetch` | ✅ | None (cached) |
| 3 | `read_file` | `read_file_tool` | ✅ | None |
| 4 | `write_file` | `full_file_write_tool` | ❌ | Creates/overwrites file |
| 5 | `edit_file` | `search_replace_edit_tool` | ❌ | Aider-style edit |
| 6 | `delete_file` | `delete_file_tool` | ❌ | Deletes file or dir |
| 7 | `close_file` | `close_file_tool` | ✅ | Removes from interpreter view only |
| 8 | `list_directory` | `list_dir_tool` | ✅ | None |
| 9 | `search_files` | `file_search_tool` | ✅ | None |
| 10 | `grep_search` | `grep_search_tool` | ✅ | None |
| 11 | `run_terminal_command` | `run_terminal_cmd_tool` | ❌ | Executes shell commands |
| 12 | `tool_store` | `tool_store_tool` | ⚠️ | Parallel-safe; excluded from subagent (can run write APIs) |
| 13 | `subagent` | `run_subagent` | ✅ | Spawns child agent (excluded from subagent tool list) |
| 14 | *(removed)* | — | — | Former `python_interpreter` (dead code purged) |

### 4.2 Tool Parameter Signatures

```
google_search(search_term: str) → str
web_browser(target_url: str, prompt: str) → str
read_file(target_file: str) → str
write_file(target_file: str, code_edit: str) → str
edit_file(target_file: str, start_line: int, search_content: str, replace_content: str) → str
delete_file(target_file: str) → str
close_file(target_file: str) → str
list_directory(relative_workspace_path: str = "") → str
search_files(query: str) → str
grep_search(query: str, include_pattern: str = None, exclude_pattern: str = None, case_sensitive: bool = True) → str
run_terminal_command(command: str, timeout: int = 30, blocking: bool = True) → str
tool_store(action: str, query: str = None, tool_name: str = None, arguments: dict = None) → str
subagent(task: str) → str
```

### 4.3 `edit_file` — Aider-Style Search & Replace

**Parameters:**
- `target_file`: Path to the file to edit
- `start_line`: Line number to start searching from (1-based)
- `search_content`: Exact content to find and replace (whitespace and indentation matter)
- `replace_content`: The replacement content (use empty string to delete)

**Rules:**
- `search_content` must match file content exactly (indentation, newlines matter; trailing spaces are ignored)
- Include 1-3 lines of context to uniquely identify the location
- One edit per call; use multiple calls for multiple edits
- Use empty `replace_content` to delete content

**Example — changing a print statement on line 10:**
```
# Call edit_file with:
target_file: "main.py"
start_line: 10
search_content: 'print("hello")'
replace_content: 'print("world")'
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
1. `discover_open_files(messages)` — scans all assistant messages for `read_file`, `write_file`, `edit_file` calls → adds to `open_files` set. `delete_file` and `close_file` remove from set.
2. `generate_consolidated_interpreter_display(messages)` — reads all open files, formats them with line numbers, wraps in markers, appends context warning if >5 files or >50K chars.
3. `clean_previous_interpreter_blocks(messages)` — strips old markers from tool messages to save context.
4. After any code-related tool execution, old blocks are cleaned and a fresh consolidated display is appended to the LAST tool message.

---

## 6. Configuration (`config.py`)

### Model Providers
| ID | Model | Thinking? | Base URL |
|----|-------|-----------|----------|
| `deepseek` | `deepseek-v4-pro` | ✅ | `api.deepseek.com` |
| `nvidia` | `deepseek-ai/deepseek-v3.2` | ✅ | `integrate.api.nvidia.com` |
| `nvidia-fast` | same model | ❌ | same |
| `nvidia-glm5` | `z-ai/glm5` | ✅ | same |
| `nvidia-glm5-fast` | same model | ❌ | same |
| `gemini-3-pro` | Vertex AI | ✅ | Google Cloud |
| `gemini-3-pro-api` | AI Studio | ✅ | Google API |

Default: `deepseek`

### Key Limits
```python
MAX_TOKENS = 8192
MAX_ITERATIONS = 30         # loop iterations per user turn
CONTINUE_ITERATIONS = 30    # extra iterations on "Continue"
MAX_STREAMING_RETRIES = 10
MAX_TOOL_CONCURRENCY = 5    # parallel threads for read-only tools
SUBAGENT_MAX_ITERATIONS = 15
SUBAGENT_MAX_RESULT_CHARS = 4000
```
(Note: TEMPERATURE was intentionally removed — modern models have proper defaults for agent tasks.)

### Environment Detection
- `THINKTOOL_DOCKER=1` → `DOCKER_MODE=True`, workspace at `/workspace`
- `THINKTOOL_VNC=1` → `DOCKER_VNC=True`, VNC instructions added to system prompt

### Web Browser (Secondary Model)
- Uses DeepSeek Chat (`deepseek-chat`) to summarise pages
- Cached: 15-min TTL, 64 max entries
- 100K char limit for markdown fed to summariser
- 10MB max HTTP response, 60s timeout

---

## 7. Session Management (`session_manager.py`)

### Session Lifecycle
1. `create_session()` — generates UUID8 id, creates session dir under `~/.thinktool_sessions/`
2. Clones (or reuses) a conda environment
3. Starts a persistent shell (`bash -i` on Linux, `cmd.exe /D` on Windows)
4. Activates conda env in the shell via `_run_init_command()`

### Persistent Shell
- `run_in_persistent_shell(command, timeout, blocking)` — writes command to shell stdin, waits for boundary marker on stdout, reads output from temp file
- `blocking=False` → wraps in `nohup bash -c ... > logfile 2>&1 &`, returns log path
- On timeout → spawns new shell, returns note about log file

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

### `file_operations.py` — FILE TOOLS

- `read_file` — validates existence, snapshots content for diff tracking
- `write_file` — atomic write via temp file + `os.replace()`
- `edit_file` — aider-style: normalize for comparison (strip trailing whitespace), search from start_line, replace, atomic write
- `delete_file` — handles both files and directories
- `list_directory` — emoji-prefixed listing
- `search_files` — fuzzy filename search
- File tracking callbacks (`set_file_tracking_callbacks`) for web API diffing

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

---

## 10. Getting Started

### Prerequisites
- Docker
- Node.js (for the frontend dev server)
- Conda (optional, for running outside Docker)

### Running the Application

**`start.bat` is the sole supported entry point on the host.** It builds and runs the Docker container (backend + gateway server) with persistent data volumes, then starts the frontend dev server.

```powershell
.\start.bat
```

Services started:
- Backend API: http://localhost:8080 (agent loop)
- Gateway: http://localhost:8081 (SSE proxy, file display, conversation persistence)
- Frontend: http://localhost:3000
- VNC Desktop: http://localhost:6080

Press `Ctrl+C` to stop the frontend. To stop the backend: `docker stop thinkwithtool-agent`.

**Alternative (docker compose):**
```bash
docker compose up --build
# Then in a separate terminal:
cd frontend && npm install && npm run dev
```

> **Important:** Do NOT run `python run_web.py` directly on the host. The backend must run inside Docker for proper session isolation, VNC support, and persistent data storage. Running outside Docker will lose conversation history on restart.

**Session CLI:**
```bash
conda activate agent
python -m src.code_sandbox.session_cli
```

Available subcommands: `create`, `list`, `cleanup`, `info`, `test`

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
- `conversation_gateway/conversation_store.py` — file-backed store (thread-safe, atomic writes)
- `conversation_gateway/api.py` — FastAPI server on port 8081, proxies to backend, persists on SSE events, serves file-display endpoints
- `conversation_gateway/workspace.py` — file snapshots, diffs, tree building, workspace upload/delete/export
- `src/config.py` — `DATA_DIR` / `TRAINING_DATA_DIR` path resolution

Without the volume mount (`-v`), the `--rm` flag on `docker run` causes the container to be deleted on stop, destroying all data inside. The volume mount ensures conversations and training logs survive container restarts.

---

## 11. Requirements

Core dependencies (see `requirements.txt`):
- `openai>=1.0.0` — API client
- `fastapi>=0.104.1` — Web API
- `google-api-python-client>=2.169.0` — Google Search
- `pyright` — Python type checking (via nodejs)

---

## 12. Known Issues & Quirks

### Fixed issues

1. ~~**`TEMPERATURE` not passed to API**~~ → **FIXED**: `TEMPERATURE` removed entirely (modern models handle defaults).
2. ~~**`web_browser` prompt mismatch**~~ → **FIXED**: `prompt` is required in tool definition; function default `""` is a defensive fallback never reached.
3. ~~**`python_interpreter` dead code**~~ → **FIXED**: All `run_like_jupyter` imports and commented-out definitions removed.
4. ~~**`terminal_runner.py` background process stubs**~~ → **FIXED**: 3 stub functions + unused fields removed.
5. ~~**Code interpreter note duplication**~~ → **VERIFIED NOT A BUG**: Note is only added once in `generate_consolidated_interpreter_display`; `display_multiple_files` does not include it.
6. ~~**Fragile title extraction**~~ → **FIXED**: Task instructions are now wrapped in `[TASK INSTRUCTION]` / `[/TASK INSTRUCTION]` markers by the frontend (`App.jsx`). The full marked message passes through to the stateless agent backend unchanged. The conversation store (`conversation_store.py`) strips the markers via regex in `_extract_title()`, and `save_messages()` now **always** re-extracts the title (not just when "Untitled") so the first incremental persist overwrites the raw initial title from `api.py`. Legacy conversations without markers fall back to the old `\n\n` heuristic.

**Still present:**
1. **Hardcoded API keys** in `config.py`: Some defaults use plain text or placeholder values (e.g., `"YOUR_GEMINI_API_KEY"`). Most keys read from env vars, but fallback values exist.
2. **Missing `stderr` capture**: `run_in_persistent_shell` may merge stderr into stdout; `terminal_runner.py` does report both channels if returned separately.

---

## 13. Key Patterns & Conventions

- **All tools return strings** — never raise exceptions to the agent
- **Workspace root** comes from `session_manager.get_session_working_directory()`
- **Atomic writes** — temp file + `os.replace()` pattern
- **Tool wrappers** — each tool has a `_tool` suffix function for the registry
- **Global singletons** — `session_manager`, `provider_manager`, `code_interpreter`
- **No async** — everything is synchronous, concurrency via threads
- **Streaming** — SSE from main_flow to web API to frontend

---

## 14. Quick Reference: If You Need To...

| Task | Where to look |
|------|---------------|
| Add a new tool | `tool_definitions.py` — add schema + function mapping |
| Change the system prompt | `config.py` → `SYSTEM_MESSAGE_TEMPLATE` |
| Add a new LLM provider | `config.py` → `MODEL_PROVIDERS` |
| Change iteration limits | `config.py` → `MAX_ITERATIONS`, `CONTINUE_ITERATIONS` |
| Fix tool execution | `tool_definitions.py` → `execute_tool_call()` |
| Change subagent behavior | `core_tools/subagent.py` |
| Modify session isolation | `code_sandbox/session_manager.py` |
| Change the web API | `web_api/app.py` |
| Change the frontend | `frontend/src/` (React + Vite) |
| Understand the edit_file algorithm | `file_operations.py` → `search_replace_edit()` lines 105-223 |
| Understand web fetch pipeline | `web_browser.py` → `web_fetch()` |
| Understand shell execution | `session_manager.py` → `run_in_persistent_shell()` |

---

## License

This project is provided as-is for research and development purposes.
