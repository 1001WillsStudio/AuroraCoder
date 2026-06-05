# AuroraCoder — Native npm Launcher 🧪 EXPERIMENTAL

> ⚠️ **This feature is experimental.** The npm launcher is under active development
> and may have rough edges. Docker-based methods are the recommended, stable path.

**`npm install -g aurora-coder`** — one command to run AuroraCoder directly on your machine, **no Docker required**.

```bash
npm install -g aurora-coder
aurora-coder           # opens browser → http://localhost:8081
```

---

## What This Is

Three files that make AuroraCoder installable via npm with no Docker:

| File | Purpose |
|---|---|
| `cli.js` | Zero-dependency Node.js launcher — finds project root, checks deps, starts services |
| `run_native.py` | Patches the system prompt for native mode, then launches backend/gateway via uvicorn |
| `package.json` | npm metadata — `bin: { "aurora-coder": "./cli.js" }` |

The launcher manages the full lifecycle:

1. Checks prerequisites (Python 3.10+, Node 18+)
2. Installs Python dependencies (`pip install -r requirements.txt`)
3. Builds the React frontend (`vite build`)
4. Patches the system prompt (replaces Docker text with native-mode text)
5. Starts the agent backend (port 8080) and gateway (port 8081)
6. Serves the web UI at `http://localhost:8081`
7. Gracefully shuts down everything on Ctrl+C

Subsequent runs are near-instant — pip install and frontend build only happen once.

---

## Prerequisites

| Requirement | Why |
|---|---|
| **Node.js ≥ 18** | Runs the launcher and builds the frontend |
| **Python ≥ 3.10** | Runs the agent backend + gateway (FastAPI) |
| **`DEEPSEEK_API_KEY`** | The agent needs an LLM API key to work |

No Docker, no conda, no Go compiler needed.

---

## Usage

```bash
# Default ports (gateway :8081, backend :8080)
aurora-coder

# Custom ports
aurora-coder --port 9000 --backend-port 9001

# Help
aurora-coder --help
```

### Environment Variables

| Variable | Required | Default |
|---|---|---|
| `DEEPSEEK_API_KEY` | **Yes** | — |
| `AURORACODER_HOME` | No | Auto-detected |
| `AURORACODER_DATA_DIR` | No | `~/.auroracoder/data` |

API keys can also be set in the Settings panel of the web UI after launch.

---

## What's Different from the Docker Version

| Feature | Docker | Native npm |
|---|---|---|
| Chat + streaming | ✅ | ✅ |
| File editing (read/write/edit/delete) | ✅ | ✅ |
| Terminal commands | ✅ | ✅ |
| Web search + browsing | ✅ | ✅ |
| Sub-agents | ✅ | ✅ |
| ToolStore | ✅ | ✅ |
| VNC Desktop (GUI apps) | ✅ | ❌ |
| Sandbox isolation | ✅ | ❌ (runs as your user) |
| ToolStore Docker tools | ✅ | ❌ |

> **Security note**: Without Docker, the agent can read/write any file your user account can access. It's recommended to run it inside a project directory you trust it to modify.

---

## How the Native Patch Works

The original `src/config.py` contains a hardcoded Docker system message:

> "You are running inside a Docker container (Linux)."
> "Your working directory is `/workspace`."

**`run_native.py`** patches this **at import time** before any server starts — no files are modified:

```python
import src.config as cfg
cfg.SYSTEM_MESSAGE_TEMPLATE = _NATIVE_SYSTEM_MESSAGE  # native version
```

The native message says:

> "You are running on the host machine (native install — no Docker sandbox)."
> "Your working directory is the current working directory (cwd)."

The original `src/config.py` is never touched.

---

## Publishing to npm

A **root-level `package.json`** already exists in the repository. To publish a new version:

1. Update the `version` field in **both** `package.json` (root) and `npm-package/package.json`
2. Run from the **repo root** (not `npm-package/`):

```bash
npm publish
```

The root `package.json` includes all project files (100 files, ~6.8 MB) and
automatically excludes `node_modules`, `__pycache__`, `.git`, tests, and docs
via `.npmignore`.

---

## License

MIT — see [LICENSE](../LICENSE) for details.

Co-authored-by: AuroraCoderAgent <aurorathesnowyfox@gmail.com>
