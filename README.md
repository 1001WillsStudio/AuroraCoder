<div align="center">
  <img src="https://img.shields.io/badge/status-active-success?style=flat-square" alt="Status" />
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square" alt="Python" />
  <img src="https://img.shields.io/badge/node-18+-green?style=flat-square" alt="Node" />
  <img src="https://img.shields.io/badge/docker-required-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker" />
</div>

<h1 align="center">🌌 AuroraCoder</h1>
<p align="center"><strong>An autonomous coding agent that thinks, reasons, and builds —<br/>powered by native OpenAI tool calling with deep reasoning models.</strong></p>

<p align="center">
  <a href="#-overview">Overview</a> •
  <a href="#-key-innovations">Innovations</a> •
  <a href="#-design-decisions">Design</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-tools">Tools</a> •
  <a href="#-configuration">Config</a> •
  <a href="#-browser-desktop">VNC Desktop</a> •
  <a href="#-roadmap">Roadmap</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

## ✨ Overview

**AuroraCoder** is a state-of-the-art autonomous AI coding agent that combines **reasoning LLMs** (DeepSeek V4 Pro, GLM-5.1, Gemini 3.1 Pro) with **native OpenAI function calling** to execute real-world tasks in a Docker sandbox. It's not just a chat interface — it's an autonomous agent that reads your codebase, writes code, runs commands, searches the web, delegates to sub-agents, and even launches GUI applications visible through a built-in VNC desktop.

> **Think of it as giving a frontier reasoning model a terminal, a file editor, a web browser, and a sub-agent workforce — all in an isolated Linux container.**

---

## 🧠 Key Innovations

AuroraCoder isn't a wrapper around someone else's agent framework. It's built from scratch — and increasingly, **by itself**. The vast majority of recent changes to this codebase (including this README, the frontend, the gateway, and tool improvements) were coded by AuroraCoder autonomously. This is a code agent that actively develops its own codebase.

Below are the genuinely novel architectural ideas that set it apart — followed by supporting design decisions that emerged from the same principles.

### 1. 🔗 Stateless Core × Stateful Gateway — The "Thin Engine" Pattern

The agent loop (`main_flow.py`) is **completely stateless** — it takes `messages` in, yields `{messages, status}` out. All persistence, file diffing, conversation management, and context monitoring happen in a separate **conversation gateway** layer (port 8081). 

**Why this matters**: You can swap out the frontend, add another consumer, test the loop in isolation, or run multiple gateways — the core stays simple and testable. This is the opposite of frameworks that entangle state management with agent logic.

### 2. 🧵 Smart Parallel Tool Execution

Read-only tools (`read_file`, `grep_search`, `web_browser`, `google_search`, `list_directory`, `search_files`) execute **concurrently** via `ThreadPoolExecutor`. Write tools (`write_file`, `edit_file`, `run_terminal_command`) run sequentially. 

The trick: `partition_tool_calls()` automatically splits mixed tool batches into parallel-safe and sequential groups, so the agent gets maximum throughput without risking race conditions on file operations.

### 3. 📟 Persistent Shell with Background Process Management

Instead of spinning up one-shot subprocesses (which lose state and are slow), AuroraCoder maintains a **single persistent Bash shell**. The blocking/non-blocking paradigm is elegant:

- `blocking=true` → runs command in foreground, returns output when done
- `blocking=false` → wraps in `nohup bash -c ... &`, returns a log file path immediately
- On timeout → automatically spawns a new shell so the stalled command keeps running

**This means**: the agent can `npm run dev` in the background, check the logs, make code changes, and see hot-reload take effect — all without losing shell state between calls.

### 4. 📟 Living Tool State — Mutating Responses, Not Appending Them

Every other agent framework treats tool responses as **immutable, append-only history**. The model calls a tool, the result is appended, and it stays in context forever, accumulating stale, contradictory file contents that waste tokens and confuse the model.

AuroraCoder introduces a fundamentally different paradigm: **tool responses are mutable**. After every code-related tool call (`read_file`, `write_file`, `edit_file`), the system scans the entire conversation for all currently open files, re-reads them from disk, formats them with line numbers, and appends a single **consolidated state block** to the *last* tool message. Then it **strips every previous state block** from earlier tool messages — collapsing them down to near-zero tokens.

```
Before (append-only — every tool response stays):
  [read_file → 500 lines of main.py]
  [edit_file  → 500 lines of main.py]
  [read_file → 300 lines of utils.py]
  [edit_file  → 500 lines of main.py AGAIN]
  = 1800+ lines of duplicate/stale content wasting context

After (living state — only the latest is visible):
  [read_file → "(file opened)"]          ← collapsed to 1 line
  [edit_file  → "(edit applied)"]         ← collapsed to 1 line
  [read_file → "(file opened)"]          ← collapsed to 1 line
  [edit_file  → FULL STATE: main.py + utils.py]  ← the single source of truth
  = 500 lines total, always fresh from disk
```

This is not just deduplication — it's a **redefinition of what a tool response means**. A tool response isn't a historical record; it's a **living window into the current filesystem state**. Previous responses are amortized away. The newest tool call carries the complete truth. An LLM reading the conversation sees exactly what's on disk *right now*, not what was on disk three edits ago.

A context warning fires when >5 files or >50K characters are open, so the model knows when it's holding too much state. This turns the conversation from a growing append-only log into a **self-cleaning state machine**.

### 5. 🚦 Strict Gates in a Loose Loop — The Pattern Circuit Breaker

LLMs are **pattern-following machines**. Give them one successful tool call and they'll produce ten more just like it. But give them **one malformed tool call** — wrong JSON, missing parameters, hallucinated field names — and the pattern they learn is *broken*. The next call copies the same mistake. Then the next. Then the model gets stuck in a **cascade of wrong tool calls**, each one reinforcing the broken template. Most agent frameworks let this happen: a sloppy tool call returns a generic error or partial result, the model sees it as a "successful pattern," and the death spiral begins.

AuroraCoder takes the opposite approach: **the execution layer is ruthlessly strict, precisely so the agent loop can stay loose and autonomous.** Every tool call is validated at the gate:

- **JSON arguments must parse correctly** — malformed JSON gets an immediate, specific error describing exactly what's wrong
- **Required parameters are enforced** — missing fields get a clear message naming what's needed
- **Type mismatches are caught** — passing a string where an integer is expected returns a precise type error with the expected schema
- **File existence is verified before reads** — the agent gets "File not found" not a silent empty string
- **Edit anchors must match exactly** — the `edit_file` tool normalizes trailing whitespace and searches from the specified start line; if the anchor text isn't found, it returns *where* it looked and *what* it found instead

When a tool call fails validation, the error message goes back as a tool response with **enough specificity that the LLM can correct itself in the next turn**. This breaks the pattern cascade before it starts. The model sees: "that didn't work, here's exactly why, try this instead" — not a silent failure it might copy.

**The philosophy**: the agent operates in a loose, autonomous, exploration-friendly loop — it can try things, make mistakes, course-correct. But the *gates* between the LLM and the filesystem are strict. Every tool call either succeeds cleanly or fails with an actionable error. There is no middle ground where the LLM thinks it succeeded but produced garbage. This prevents the most insidious failure mode in AI agents: the model confidently continuing down a path built on a broken foundation.
---

## 🏗️ Design Decisions

These aren't innovations per se — they're deliberate architectural choices that support the innovations above. Each one solves a real problem that emerged during development.

### 🔍 Aider-Style Search-and-Replace Editing

`edit_file` uses an **aider-inspired search-and-replace** algorithm: provide a `search_content` anchor (with surrounding context), and the replacement is applied only if an exact match is found. Trailing whitespace is normalized before comparison. This is far more reliable than sending full-file diffs or trying to specify line ranges.

### 🌐 Dual-Model Web Summarization

When the agent fetches a web page, the raw HTML is converted to Markdown (via `BeautifulSoup` + `markdownify`), then fed to a **cheap secondary model** (`deepseek-chat`) for summarization. Only the summary enters the main agent's context. An LRU cache (15-min TTL, 64 entries) prevents redundant fetches. Cross-host redirects are reported rather than followed, preventing SSRF risks.

### 👥 Sub-Agent Delegation System

The agent can spawn **sub-agents** for research-heavy subtasks. Sub-agents run with a filtered read-only tool set, have lower iteration caps, and their results are truncated to preserve parent context. This is implemented as an HTTP call back into the gateway, so sub-agents can stream their progress too.

### 🔄 Context Window Intelligence

When context usage crosses 80%, a `continue_as_new_chat` tool appears in the agent's tool list. The system prompt includes a one-liner notice injected at the right moment. The agent can call this tool to gracefully archive the current conversation and start fresh — a cleaner alternative to silent truncation.

### 🖥️ VNC Desktop for GUI Applications

The Docker container runs Xvfb + fluxbox + noVNC, giving the agent a **virtual desktop**. It can launch matplotlib plots, pygame games, tkinter apps, or any GUI tool. Users watch live at port 6080. The system prompt includes specific instructions for matplotlib backends and non-blocking GUI launch.

### 🔌 Pluggable Provider Architecture

Seven model providers configured out of the box, with thinking/reasoning mode toggled per provider. The `ProviderManager` singleton initializes all clients at import time and reports which ones succeed. Custom `VertexAIClient` wraps Google Cloud auth with automatic token refresh, mimicking the OpenAI SDK interface.

---

## 🚀 Quick Start

### Prerequisites

- **Docker** — the agent runs inside a container (sandbox + VNC + gateway)
- **Node.js 18+** — for the React frontend dev server
- **API Keys** — at least `DEEPSEEK_API_KEY` or `NVIDIA_API_KEY` (set in `.env`)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/auroracoder.git
cd auroracoder

# 2. Copy and fill in the environment file
cp .env.example .env
# Edit .env with your API keys — at minimum set DEEPSEEK_API_KEY

# 3. Build the base Docker image (conda environment)
docker build -t thinkwithtool-base -f Dockerfile.base .

# 4. Launch via docker compose
docker compose up --build

# 5. In a separate terminal, start the frontend
cd frontend && npm install && npm run dev
```

> **Windows users**: Run `.\start.bat` which handles everything automatically.

### Services

| Service | URL | Purpose |
|---------|-----|---------|
| 🖥️ **Frontend** | `http://localhost:3000` | Chat UI with streaming, thinking viz, file tree |
| ⚙️ **Agent Backend** | `http://localhost:8080` | Stateless agent loop + tool execution |
| 🌉 **Gateway** | `http://localhost:8081` | SSE proxy, conversation persistence, file display |
| 🖱️ **VNC Desktop** | `http://localhost:6080` | Live Linux desktop for GUI apps |

### Multi-Instance Mode

Run `another-one.bat` (or `another-one.bat 5`) to spin up an additional isolated instance with auto-incremented ports — useful for running multiple agents side by side.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Docker Container                          │
│                                                               │
│  ┌── Frontend (:3000) ────────────────────────────────────┐  │
│  │  React SPA (Vite)                                       │  │
│  │  ├─ Chat streaming via SSE                              │  │
│  │  ├─ File tree + diff viewer                             │  │
│  │  └─ Thinking visualization (reasoning tokens)           │  │
│  └─────────────────────────────────────────────────────────┘  │
│                              │ SSE                             │
│  ┌── Gateway (:8081) ─────────────────────────────────────┐  │
│  │  FastAPI                                                │  │
│  │  ├─ SSE proxy (intercepts agent events)                 │  │
│  │  ├─ Conversation persistence (atomic file writes)       │  │
│  │  ├─ File snapshots + diff generation                    │  │
│  │  └─ Workspace management (upload/delete/export)         │  │
│  └─────────────────────────────────────────────────────────┘  │
│                              │ HTTP                            │
│  ┌── Agent Backend (:8080) ───────────────────────────────┐  │
│  │  main_flow.py — The Engine                              │  │
│  │  ├─ System prompt injection (config.py)                 │  │
│  │  ├─ LLM streaming (7 providers)                         │  │
│  │  ├─ Tool execution (parallel read, sequential write)    │  │
│  │  ├─ Code interpreter display management                 │  │
│  │  └─ Context continuation logic                          │  │
│  │                                                         │  │
│  │  13 Built-in Tools:                                     │  │
│  │  read_file · write_file · edit_file · delete_file       │  │
│  │  list_directory · search_files · grep_search            │  │
│  │  run_terminal_command · google_search · web_browser     │  │
│  │  subagent · tool_store · close_file                     │  │
│  └─────────────────────────────────────────────────────────┘  │
│                              │                                 │
│  ┌── Sandbox (/workspace) ────────────────────────────────┐  │
│  │  Persistent Bash Shell · Conda Environment              │  │
│  │  Xvfb + Fluxbox + noVNC (:6080)                        │  │
│  │  Background process management                          │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User sends message
       │
       ▼
  Frontend wraps in [TASK INSTRUCTION] markers
       │
       ▼
  Gateway proxies to backend on :8080
       │
       ▼
  main_flow.py generate_chat_responses_stream_native()
       │
       ├─ 1. Inject system message
       ├─ 2. Call LLM (DeepSeek/NVIDIA/Gemini)
       ├─ 3. Stream thinking + content + tool calls
       ├─ 4. Execute tools (parallelized by safety)
       ├─ 5. Append tool results as role:tool messages
       ├─ 6. Refresh code interpreter display
       └─ 7. Loop (max 30 iterations, then "Continue")
       │
       ▼
  Gateway intercepts SSE, persists conversation
       │
       ▼
  Frontend renders streaming response + thinking
```

### Repository Structure

```
auroracoder/
├── src/                          # Stateless agent core
│   ├── main_flow.py              # THE ENGINE: chat loop + streaming
│   ├── tool_definitions.py       # Tool schemas + execution dispatch
│   ├── tool_executor.py          # Parallel/serial tool executor
│   ├── config.py                 # ALL config: models, limits, prompts
│   ├── providers.py              # Multi-provider LLM client manager
│   ├── code_tools/               # File & code manipulation tools
│   │   ├── file_operations.py    # read/write/edit/delete/list/search
│   │   ├── terminal_runner.py    # Persistent shell execution
│   │   ├── grep_search.py        # Regex search across workspace
│   │   └── code_interpreter.py   # Consolidated file display
│   ├── core_tools/               # Higher-level agent tools
│   │   ├── google_search.py      # Google Custom Search Engine
│   │   ├── web_browser.py        # URL → MD → secondary-model summary
│   │   ├── subagent.py           # Sub-agent delegation
│   │   └── tool_store_client.py  # ToolStore integration
│   ├── code_sandbox/
│   │   └── sandbox.py            # Workspace + persistent shell singleton
│   └── web_api/
│       └── app.py                # FastAPI backend (port 8080)
├── conversation_gateway/         # Middleware layer (the "dirty work")
│   ├── api.py                    # SSE proxy + file endpoints (port 8081)
│   ├── conversation_store.py     # Thread-safe atomic file persistence
│   └── workspace.py              # File diffs, tree, upload/export
├── frontend/                     # React + Vite SPA
│   ├── src/
│   │   ├── App.jsx               # Main app + conversation management
│   │   ├── components/           # ChatMessage, ThinkingIndicator, etc.
│   │   ├── services/api.js       # SSE streaming client
│   │   └── styles/index.css      # Dark theme with gradient accents
│   ├── package.json
│   └── vite.config.js
├── .env.example                  # Template for API keys
├── docker-compose.yml            # Docker service orchestration
├── Dockerfile                    # App image
├── Dockerfile.base               # Base image with conda environment
├── start.bat                     # Windows one-click launcher
├── another-one.bat               # Multi-instance launcher
├── requirements.txt              # Python dependencies
├── AGENT_README.md               # Detailed internal docs for AI agents
└── run_web.py                    # Backend entry point
```

---

## 🔧 Tools

AuroraCoder gives the LLM **13 built-in tools** via native OpenAI function calling:

| Tool | Type | Description |
|------|------|-------------|
| `read_file` | Read | Read any file with line numbers |
| `write_file` | Write | Atomic file creation (temp + `os.replace()`) |
| `edit_file` | Write | Aider-style search-and-replace editing |
| `delete_file` | Write | Delete files or directories |
| `close_file` | Read | Remove from code interpreter view (no filesystem change) |
| `list_directory` | Read | List directory contents with emoji prefixes |
| `search_files` | Read | Fuzzy filename search across workspace |
| `grep_search` | Read | Regex search with include/exclude patterns |
| `run_terminal_command` | Write | Execute commands in persistent Bash shell |
| `google_search` | Read | Google Custom Search Engine |
| `web_browser` | Read | URL fetch → Markdown → secondary-model summary |
| `subagent` | Read | Delegate tasks to read-only child agents |
| `tool_store` | Mixed | Universal tool discovery and execution |

**Parallel execution**: Read-only tools run concurrently (5 threads max). Write tools serialize. Sub-agents get a filtered read-only subset.

---

## ⚙️ Configuration

### Supported Model Providers

| Provider | Model | Reasoning | Context |
|----------|-------|-----------|---------|
| **DeepSeek** | `deepseek-v4-pro` | ✅ | 1M tokens |
| **NVIDIA** | `deepseek-ai/deepseek-v4-pro` | ✅/❌ | 1M tokens |
| **NVIDIA GLM** | `z-ai/glm-5.1` | ✅/❌ | 128K tokens |
| **Gemini (Vertex AI)** | `gemini-3.1-pro-preview` | ✅ | 1M tokens |
| **Gemini (AI Studio)** | `gemini-3.1-pro-preview` | ✅ | 1M tokens |

Set via `DEFAULT_PROVIDER` in `config.py` or select from the frontend.

### Key Tuning Parameters

```python
MAX_TOKENS = 32768           # Completion token limit
MAX_ITERATIONS = 30          # Agent loop iterations per turn
CONTINUE_ITERATIONS = 30     # Extra iterations on "Continue"
MAX_TOOL_CONCURRENCY = 5     # Parallel thread pool size
SUBAGENT_MAX_ITERATIONS = 15 # Sub-agent iteration cap
MAX_STREAMING_RETRIES = 10   # Retries on stream failure
```

### Environment Variables

Copy `.env.example` → `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | **Yes** | DeepSeek API key |
| `NVIDIA_API_KEY` | No | NVIDIA-hosted models |
| `GEMINI_API_KEY` | No | Google AI Studio |
| `GOOGLE_SEARCH_API_KEY` | No | Google Custom Search |
| `GOOGLE_CSE_ID` | No | Custom Search Engine ID |
| `VERTEX_AI_PROJECT_ID` | No | Google Cloud project for Vertex AI |

---

## 🖥️ VNC Desktop

AuroraCoder includes a full virtual Linux desktop (Xvfb + fluxbox + noVNC) on port **6080**. This means:

- **matplotlib**: The agent can render plots (uses `TkAgg` backend)
- **pygame / tkinter / any GUI**: Just works — windows appear on the desktop
- **Web browsers**: Fire a browser and watch it live
- **IDE demos**: Launch VS Code, Jupyter, anything

The system prompt auto-includes VNC instructions when `THINKTOOL_VNC=1` is set.

---

## 💾 Data Persistence

All conversations and training data survive container restarts via Docker volume mounts:

```
~/.thinktool/data/
├── conversations/
│   ├── index.json           # Conversation metadata index
│   ├── {id}.json            # Raw API messages
│   └── {id}.frontend.json   # UI-shaped messages
└── training/
    └── YYYY-MM-DD.jsonl     # Daily API call telemetry
```

Override paths with `THINKTOOL_DATA_DIR`, `THINKTOOL_WORKSPACE_DIR`, `THINKTOOL_SESSIONS_DIR` env vars.

---

## 🗺️ Roadmap

### In Progress
- [ ] **AgentToolStore Integration** — Dynamic tool discovery via MCP servers, skill registration, and a web-based tool management UI (see `docs/DESIGN_TOOL_STORE_INTEGRATION.md`)

### Planned
- [ ] **Semantic Code Search** — FAISS-based embedding search across workspace
- [ ] **Linux/macOS Start Script** — `start.sh` equivalent of `start.bat`
- [ ] **Production Mode** — Serve frontend from Docker, no separate Node process
- [ ] **WebSocket Streaming** — Replace SSE with WebSocket for bidirectional streaming
- [ ] **Tool Sandboxing** — Per-tool resource limits and permission scoping
- [ ] **Conversation Branching** — Fork conversations at any message
- [ ] **Plugin Ecosystem** — Community-contributed tool packs

---

## 👥 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Setup

```bash
# Clone and enter the repo
git clone https://github.com/YOUR_USERNAME/auroracoder.git
cd auroracoder

# Set up Python environment
conda create -n auroracoder python=3.11
conda activate auroracoder
pip install -r requirements.txt

# Install frontend deps
cd frontend && npm install

# Run backend (outside Docker — for development only)
python run_web.py

# Run frontend (separate terminal)
cd frontend && npm run dev
```

> **Production/deployment always uses Docker** for sandbox isolation.

### Project Conventions

- **All tools return strings** — never raise exceptions to the agent
- **Atomic writes** — temp file + `os.replace()` pattern everywhere
- **No async** — everything is synchronous, concurrency via threads
- **Global singletons** — `shell` (PersistentShell), `provider_manager`, code interpreter
- **English only** — all generated code and comments must be in English
- **Path hygiene** — all paths relative to `/workspace` (or `WORKSPACE` from `code_sandbox`)

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

AuroraCoder draws inspiration from and builds upon ideas in:

- **Aider** — the gold standard for LLM-powered code editing (search-and-replace pattern)
- **Claude Code** — Anthropic's agent architecture and skills system
- **OpenAI** — Function calling API design
- **Model Context Protocol (MCP)** — Standardized tool server interface

---

<p align="center">
  <sub>Built with ❤️ for developers who want an agent that actually codes.</sub>
</p>
