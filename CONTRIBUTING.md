# Contributing to AuroraCoder

Thank you for your interest in contributing to AuroraCoder! 🎉

## Code of Conduct

- Be respectful and constructive in all interactions
- Focus on the technical merits of contributions
- Assume good intent from other contributors

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/YOUR_USERNAME/auroracoder/issues) first
2. Include: OS, Docker version, steps to reproduce, expected vs actual behavior
3. Attach relevant logs from the Docker container (`docker logs thinkwithtool-agent`)

### Suggesting Features

- Open an issue with a clear use case and motivation
- Describe how the feature fits AuroraCoder's design philosophy (see below)

### Pull Requests

1. **Fork** the repository and create a feature branch
2. **Follow project conventions** (see below)
3. **Test** your changes in Docker (`docker compose up --build`)
4. **Keep PRs focused** — one feature or fix per PR
5. **Write clear commit messages** — what and why, not just how
6. **Update documentation** if your change affects user-facing behavior

### Project Conventions

These MUST be followed — the agent loop depends on them:

| Convention | Rule |
|------------|------|
| **Tool returns** | All tool functions must return plain strings (never raise exceptions) |
| **File writes** | Always use atomic writes: temp file + `os.replace()` |
| **Concurrency** | No `async`/`await` — use threads for parallelism |
| **Paths** | All paths relative to `/workspace` (use `code_sandbox.WORKSPACE`) |
| **Language** | All generated code and comments must be in English |
| **State** | `src/` modules must never access conversation store or file persistence directly |

### Architecture Rules

AuroraCoder has a strict separation of concerns:

- **`src/`** — Stateless agent loop. Takes messages in, yields messages out. Never touches persistence.
- **`gateway/`** — Middleware. Owns all persistence, proxying, and file display logic.
- **`frontend/`** — UI. Owns conversation management state.

DO NOT:
- Import `conversation_store` from any file in `src/`
- Add file I/O outside of the atomic pattern (temp + replace)
- Use `async`/`await` — the entire system is synchronous
- Add dependencies without updating `requirements.txt`

### Adding a New Tool

1. Create the tool function in `src/code_tools/` or `src/core_tools/`
2. Add the OpenAI function definition to `tool_definitions.py`
3. Add the function to `TOOL_FUNCTION_MAP` in `tool_definitions.py`
4. If it's parallel-safe, add it to `PARALLEL_SAFE_TOOLS`
5. If sub-agents can use it, add it to `SUBAGENT_READ_ONLY_TOOLS`
6. Return a descriptive string from the function

### Adding a New Model Provider

1. Add the provider config to `MODEL_PROVIDERS` dict in `config.py`
2. If it needs special auth (like Vertex AI), add handling in `providers.py`
3. Test that `provider_manager.list_providers()` includes your new provider

### Development Quick Start

```bash
# Set up environment
conda create -n auroracoder python=3.11
conda activate auroracoder
pip install -r requirements.txt

# Frontend
cd frontend && npm install

# Run backend (dev mode — outside Docker)
python run_web.py

# Run frontend (separate terminal)
cd frontend && npm run dev
```

### Code Review Process

1. All PRs require review by a maintainer
2. CI checks must pass
3. Documentation must be up to date
4. Breaking changes need explicit discussion in an issue first

---

## Design Philosophy

AuroraCoder is a **code agent** — its primary job is writing and reasoning about code. Contributions should align with these principles:

- **Stateless core**: The agent loop is a pure function. Don't add side effects.
- **Tool simplicity**: Tools do one thing well. Compose them, don't bloat them.
- **Context efficiency**: Every token counts. Be concise in tool outputs.
- **Docker-first**: Everything runs in a container. Don't assume host capabilities.
- **Personal innovation**: This is a research project with novel tricks. Creative architectural ideas are welcome.

---

## Questions?

Open a [discussion](https://github.com/YOUR_USERNAME/auroracoder/discussions) or start an issue with the "question" label.
