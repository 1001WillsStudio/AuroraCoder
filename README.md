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

### 1. 📟 Living Tool State — Mutating Responses, Not Appending Them

Most agent frameworks treat tool responses as **immutable, append-only history**. The model calls a tool, the result is appended, and it stays in context forever — accumulating stale, contradictory file contents that waste tokens and confuse the model.

But beyond the append-only problem, there's a deeper design choice that divides all coding agents into two camps — what does the agent return after editing a file?

| Pattern | After Edit | Token Cost | Model Visibility | Examples |
|---------|-----------|------------|------------------|----------|
| **A: Minimal Response** | `"Edit applied successfully."` + diff | Low | Must mentally reconstruct file state from past actions | [OpenCode](https://github.com/anomalyco/opencode), Aider |
| **B: Full State Response** | Complete file content with line numbers | Higher | Perfect — sees exact disk state every turn | AuroraCoder, Claude Code |

**Pattern A** (used by [OpenCode](https://github.com/anomalyco/opencode) — 160K+ GitHub stars) returns only a status message and a unified diff. The model never sees the full updated file after an edit unless it explicitly calls `read` again. This saves context tokens but forces the model to mentally reconstruct file state across multiple edits — a fragile process prone to drift, phantom content, and cascading errors when the model's mental model diverges from what's actually on disk. The [OpenCode source](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/edit.ts) confirms: its `edit` tool's output is literally `"Edit applied successfully."` — nothing more.

**Pattern B** re-reads every affected file from disk after each code-changing operation and presents the authoritative state to the model. This costs extra tokens (re-sending file contents the model already edited) but eliminates state hallucination — the model always operates on ground truth.

AuroraCoder is a refined Pattern B implementation. But it goes further than naive re-reading: **tool responses are mutable**. After every code-related tool call (`read_file`, `write_file`, `edit_file`), the system scans the entire conversation for all currently open files, re-reads them from disk, formats them with line numbers, and appends a single **consolidated state block** to the *last* tool message. Then it **strips every previous state block** from earlier tool messages — collapsing them down to near-zero tokens.

```
Before (append-only — every tool response stays):
  [read_file → 500 lines of main.py]
  [edit_file  → 500 lines of main.py]         ← duplicate!
  [read_file → 300 lines of utils.py]
  [edit_file  → 500 lines of main.py AGAIN]    ← triplicate!
  = 1800+ lines of duplicate/stale content wasting context

After (living state — only the latest is visible):
  [read_file → "(file opened)"]                ← collapsed to ~1 line
  [edit_file  → "✅ Applied 1 edit(s)..."]      ← collapsed to ~1 line
  [read_file → "(file opened)"]                ← collapsed to ~1 line
  [edit_file  → FULL STATE: main.py + utils.py] ← the sole source of truth
  = 500 lines total, always fresh from disk
```

This is not just deduplication — it's a **redefinition of what a tool response means**. A tool response isn't a historical record; it's a **living window into the current filesystem state**. Previous responses are amortized away. The newest tool call carries the complete truth. An LLM reading the conversation sees exactly what's on disk *right now*, not what was on disk three edits ago.

A context warning fires when >5 files or >50K characters are open. This turns the conversation from a growing append-only log into a **self-cleaning state machine**.

**A forward-looking side effect**: because the consolidated code interpreter always displays each file with line numbers in a canonical format, the `edit_file` tool doesn't need the LLM to embed the target file's content in the tool call. The model references line numbers from the interpreter view, and the tool resolves them against the actual file on disk. Tool calls stay lean regardless of file size. In the future, as models grow more reliable at line-level reasoning, the anchor content could become optional — the model could edit using *only* start and end line numbers, making tool calls near-zero-token operations. The code interpreter lays the groundwork for that transition today.

### 2. 🚦 Strict Gates in a Loose Loop — Generous Acceptance, Rigorous Validation

LLMs are **pattern-following machines**. Let one malformed tool call slide through with a partial success, and the model learns the wrong lesson — it copies the broken pattern into the next call, then the next, spiraling into a cascade of subtly wrong outputs. Most agents let this happen because their tools are brittle: reject the call outright (wasting a turn) or accept garbage input (reinforcing the mistake).

AuroraCoder's `edit_file` tool takes a third path: **generous on input, ruthless on output**.

**Loose acceptance** — the LLM doesn't need to get line numbers exactly right. The tool searches for its anchor content (`start_line_content`, `end_line_content`) within **±3 lines** of the stated position. Two-pass matching: first strict (trailing whitespace ignored), then relaxed (all whitespace ignored). If the anchors are found at different positions, the tool auto-corrects and proceeds.

**Rigorous validation** — but before ANY edit touches the file, ALL edits in the batch are validated. Anchors must be found. Ranges must not overlap. If any edit fails, **zero edits are applied**. The file is untouched. The error message is precise: it shows the expected content, the actual content, the surrounding file context, and even an indentation hint when whitespace differs.

**Silent self-correction** — here's the trick that breaks the cascade. When the tool auto-corrects line numbers, it emits a `<!--SELF_CORRECT:{...}-->` marker with the corrected parameters. The tool executor strips this marker and patches the LLM's original tool call **in place** in the conversation history. The LLM *never sees the correction*. On the next turn, it reads back its own message and sees the corrected version — as if it got everything right the first time. The model only ever sees successful patterns, never its own mistakes.

**Additional gates**:
- **Same-file edit guard** — blocks editing the same file twice in one turn (line numbers are stale until the code interpreter refreshes). Returns a clear explanation, not a cryptic failure.
- **Edit truncation** — silently caps at 3 edits per call. If the LLM tries more, the extras are dropped rather than letting an over-ambitious batch cause partial failures.

```
LLM calls edit_file with slightly wrong line numbers:
  start_line: 148, start_line_content: "def foo():"
  (but the function actually moved to line 150)

AuroraCoder:
  1. Searches ±3 lines → finds "def foo():" at line 150 ✓
  2. Validates all edits in batch — all pass ✓
  3. Applies edits atomically ✓
  4. Emits SELF_CORRECT marker → patches LLM's message to say line 150
  5. Returns: "⚠️ Original parameters were auto-corrected. ✅ Applied 1 edit"

LLM on next turn: sees its own call at line 150, the correct position.
It learned nothing about its mistake. Only good patterns are reinforced.
```

**The philosophy**: the agent loop is autonomous, free to explore, free to be slightly wrong. But the *gates* between the LLM and the filesystem are strict — every tool call either succeeds cleanly (possibly with silent correction) or fails with an actionable error that helps the model recover. There is no middle ground where the LLM thinks it succeeded but produced garbage, and no wasted turns from pedantic rejection of slightly-off input.

---

## 🏗️ Design Decisions

These are the architectural choices that make the innovations above possible — deliberate design, not accidental.

### 🔗 Stateless Core × Stateful Gateway

The agent loop (`main_flow.py`) is **completely stateless** — it takes messages in, yields `{messages, status}` out. All persistence, file diffing, conversation management, and context monitoring happen in a separate **conversation gateway** layer (port 8081). You can swap the frontend, add consumers, or test the loop in isolation.

### 🧵 Smart Parallel Tool Execution

Read-only tools (`read_file`, `grep_search`, `web_browser`, `google_search`, `list_directory`, `search_files`) execute **concurrently** via `ThreadPoolExecutor`. Write tools (`write_file`, `edit_file`, `run_terminal_command`) run sequentially. `partition_tool_calls()` splits mixed batches automatically.

### 📟 Persistent Shell with Background Process Management

A single persistent Bash shell instead of one-shot subprocesses. `blocking=false` wraps commands in nohup and returns a log path. On timeout, the shell auto-respawns so the stalled command keeps running. The agent can start a dev server, check logs, edit code, and see hot-reload — all in one session.

### 🌐 Dual-Model Web Summarization

Raw HTML → Markdown via BeautifulSoup + markdownify, then summarized by a cheap secondary model (`deepseek-chat`). Only the summary enters the main agent's context. LRU cache with 15-min TTL. Cross-host redirects reported rather than followed.

### 👥 Sub-Agent Delegation

Sub-agents run with a filtered read-only tool set, lower iteration caps (15), and truncated results (4000 chars). Implemented as an HTTP call back into the gateway so sub-agents stream progress too.

### 🔄 Context Window Intelligence

At 80% context usage, `continue_as_new_chat` appears in the tool list with an inline notice. The agent can archive and start fresh — cleaner than silent truncation.

### 🖥️ VNC Desktop

Xvfb + fluxbox + noVNC on port 6080. The agent can launch matplotlib (TkAgg backend), pygame, tkinter, or any GUI. System prompt auto-includes VNC instructions.

### 🔌 Pluggable Provider Architecture

Seven model providers with reasoning mode toggled per provider. `ProviderManager` singleton initializes all clients at import time. Custom `VertexAIClient` wraps Google Cloud auth with automatic token refresh.

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
┌── Host Machine ────────────────────────────────────────────────┐
│                                                                 │
│  ┌── Frontend (:3000) ──────────────────────────────────────┐  │
│  │  React SPA (Vite)                                         │  │
│  │  ├─ Chat streaming via SSE                                │  │
│  │  ├─ File tree + diff viewer                               │  │
│  │  └─ Thinking visualization (reasoning tokens)             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │ SSE                               │
│  ┌── Docker Container ───────────────────────────────────────┐  │
│  │                                                            │  │
│  │  ┌── Gateway (:8081) ──────────────────────────────────┐  │  │
│  │  │  FastAPI                                             │  │  │
│  │  │  ├─ SSE proxy (intercepts agent events)              │  │  │
│  │  │  ├─ Conversation persistence (atomic file writes)    │  │  │
│  │  │  ├─ File snapshots + diff generation                 │  │  │
│  │  │  └─ Workspace management (upload/delete/export)      │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │                              │ HTTP                         │  │
│  │  ┌── Agent Backend (:8080) ────────────────────────────┐  │  │
│  │  │  main_flow.py — The Engine                           │  │  │
│  │  │  ├─ System prompt injection (config.py)              │  │  │
│  │  │  ├─ LLM streaming (7 providers)                      │  │  │
│  │  │  ├─ Tool execution (parallel read, sequential write) │  │  │
│  │  │  ├─ Code interpreter display management              │  │  │
│  │  │  └─ Context continuation logic                       │  │  │
│  │  │                                                      │  │  │
│  │  │  13 Built-in Tools (+1 conditional):                 │  │  │
│  │  │  read_file · write_file · edit_file · delete_file    │  │  │
│  │  │  list_directory · search_files · grep_search         │  │  │
│  │  │  run_terminal_command · google_search · web_browser  │  │  │
│  │  │  subagent · tool_store · close_file                  │  │  │
│  │  │  continue_as_new_chat (at ~80% context)              │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │                              │                              │  │
│  │  ┌── Sandbox (/workspace) ─────────────────────────────┐  │  │
│  │  │  Persistent Bash Shell · Conda Environment           │  │  │
│  │  │  Xvfb + Fluxbox + noVNC (:6080)                     │  │  │
│  │  │  Background process management                       │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │                                                            │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
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

> **`tool_store`** is a built-in meta-tool that provides universal tool discovery and execution — search, inspect, and invoke thousands of public APIs and local utilities from a single interface.
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

All conversations and training data survive container restarts via Docker volume mounts. The application stores data at `/app/data` inside the container, mapped from the host:

```
# Docker mode (inside container):
/app/data/
├── conversations/
│   ├── index.json           # Conversation metadata index
│   ├── {id}.json            # Raw API messages
│   └── {id}.frontend.json   # UI-shaped messages
└── training/
    └── YYYY-MM-DD.jsonl     # Daily API call telemetry

# Local mode (outside Docker, development only):
~/.thinktool/data/
├── conversations/           # Same structure as above
└── training/
```

Override the data path with the `THINKTOOL_DATA_DIR` env var. For Docker deployments, `THINKTOOL_WORKSPACE_DIR` and `THINKTOOL_SESSIONS_DIR` can be set in `docker-compose.yml` to override volume mount paths.

---

## 🗺️ Roadmap

### In Progress
- [ ] **AgentToolStore Integration** — Dynamic tool discovery via MCP servers, skill registration, and a web-based tool management UI

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

- **Aider** — the gold standard for LLM-powered code editing (search-and-replace pattern)
- **[OpenCode](https://github.com/anomalyco/opencode)** — open-source AI coding agent (160K+ stars); follows Pattern A (minimal edit response — model must reconstruct state from past actions)
- **Claude Code** — Anthropic's agent architecture and skills system
- **OpenAI** — Function calling API design
- **Model Context Protocol (MCP)** — Standardized tool server interface

---

<p align="center">
  <sub>Built with ❤️ for developers who want an agent that actually codes.</sub>
</p>
