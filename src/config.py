"""
Configuration for the native tool calling system.

This replaces the XML-based configuration with settings for native OpenAI function calling.
"""

from pathlib import Path
import os
import sys


# ---------------------------------------------------------------------------
# Data directory — all runtime data (conversation logs, training data, etc.)
# In Docker: /app/data  (volume-mounted to the host)
# Locally:   ~/.auroracoder/data  (outside the project tree)
# Override:  set AURORACODER_DATA_DIR env var

if os.environ.get("AURORACODER_DOCKER", "0") == "1":
    DATA_DIR = Path("/app/data")  # Inside Docker (always at /app/data)
else:
    DATA_DIR = Path(
        os.environ.get(
            "AURORACODER_DATA_DIR",
            os.path.expanduser("~/.auroracoder/data"),
        )
    )
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRAINING_DATA_DIR = DATA_DIR / "training"

_proxy_port = os.environ.get("AURORACODER_PROXY_PORT")
proxy_host = os.environ.get("AURORACODER_PROXY_HOST")
proxy_port = int(_proxy_port) if _proxy_port else None

# =============================================================================
# Model Provider Configurations
# =============================================================================
# Each provider config contains all settings needed to create an OpenAI client
# and make API calls. Users can switch between providers on the frontend.

MODEL_PROVIDERS = {
    # ── 3 provider *families* (base_url + api_key).  Models are selected
    #     per-family via provider_models in settings.json (or defaults below).
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "extra_body": None,
    },
    "opencode": {
        "id": "opencode",
        "name": "OpenCode",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": os.environ.get("OPENCODE_API_KEY", ""),
        "extra_body": None,
    },
    "nvidia": {
        "id": "nvidia",
        "name": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": os.environ.get("NVIDIA_API_KEY", ""),
        "extra_body": {"chat_template_kwargs": {"thinking": True}},
    },
}

# Default models per provider family — used when provider_models is empty.
# Each entry: {"id": "model-api-name", "name": "display label"}
PROVIDER_DEFAULT_MODELS = {
    "deepseek": [
        {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
        {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
    ],
    "opencode": [
        {"id": "deepseek-v4-pro", "name": "OpenCode DS V4 Pro"},
        {"id": "deepseek-v4-flash", "name": "OpenCode DS V4 Flash"},
    ],
    "nvidia": [
        {"id": "deepseek-ai/DeepSeek-V4-Pro", "name": "NVIDIA DS V4 Pro"},
    ],
}

# Provider descriptions for the sidebar
PROVIDER_DESCRIPTIONS = {
    "deepseek": "Flagship reasoning model",
    "opencode": "OpenCode hosted",
    "nvidia": "NVIDIA NIM hosted",
}

# Default provider to use
DEFAULT_PROVIDER = "deepseek"


# Model Parameters
MAX_TOKENS = 32768

# Tool Calling Limits

# Edit mode — "aurora" (anchor-based, default) or "normal" (string replace)
DEFAULT_EDIT_MODE = "aurora"

# Tool Calling Limits
MAX_TOOL_CALLS = 10  # Increased since native tool calling is more efficient

# Iteration Limits
MAX_ITERATIONS = 30  # Maximum iterations per turn before requiring user to continue
MAX_STREAMING_RETRIES = 10  # Consecutive streaming failures before giving up

# Tool Concurrency
MAX_TOOL_CONCURRENCY = 5  # Max parallel threads for concurrent-safe tools

# Subagent
SUBAGENT_MAX_ITERATIONS = MAX_ITERATIONS  # Same cap as the parent agent

# Context continuation (CONTEXT_WINDOW_TOKENS is the fallback; per-provider
# values live in MODEL_PROVIDERS[provider]["context_window"])
CONTEXT_WINDOW_TOKENS = 128_000
CONTEXT_WARN_THRESHOLD = 0.80

# One-liner notice (injected once when tool first appears)
_CONTINUATION_NOTICE_MARKER = "[CONTEXT CONTINUATION TOOL AVAILABLE]"
CONTINUATION_NOTICE = (
    "⚠️ `continue_as_new_chat` is now available in your tool list — "
    "you are at ~80% context."
)

# =============================================================================
# Docker Mode
# =============================================================================
# Auto-detected via AURORACODER_DOCKER env var (set in Dockerfile).
DOCKER_MODE = os.environ.get("AURORACODER_DOCKER", "0") == "1"
DOCKER_VNC = os.environ.get("AURORACODER_VNC", "0") == "1"
WORKSPACE_DIR = "/workspace" if DOCKER_MODE else None

# Session Configuration
# Default conda environment to clone/reuse for new sessions.
# In Docker (default), reuses the pre-built "agent" env.
# Set to None to create fresh sandbox envs (local development).
DEFAULT_BASE_ENV_NAME = os.environ.get("DEFAULT_BASE_ENV_NAME", "agent" if DOCKER_MODE else None)

# =============================================================================
# Terminal Output
# =============================================================================
# Long command outputs are truncated to this limit (keeps head + tail).
TERMINAL_MAX_OUTPUT_CHARS = 15_000

# Default foreground timeout (seconds) for run_terminal_command.  10s is
# enough for the large majority of commands, so agents should rarely need
# to pass an explicit timeout.
TERMINAL_DEFAULT_TIMEOUT = 10

# Maximum configurable timeout (seconds).  Requests above this are silently
# clamped down to it: the command then runs in the foreground for at most
# this long before the existing PersistentShell timeout path moves it to the
# background (writing its full output to a readable log file).  Commands that
# contain a `sleep` are exempt and honor the agent-supplied timeout in full.
TERMINAL_MAX_TIMEOUT = 30

# =============================================================================
# Web Browser (secondary model summarization)
# =============================================================================
# A cheap/fast model processes raw web pages so only a concise summary
# enters the main agent's context window.  The selected provider/model is
# configured at runtime via settings.json (other.web_secondary.model) and
# synced into WEB_SECONDARY_* env vars — see src/providers.py and
# gateway/provider_registry.py.  The fallback is the DEFAULT_PROVIDER.
WEB_SECONDARY_MODEL_MAX_TOKENS = 4096

# Max characters of page markdown fed to the secondary model
WEB_MAX_MARKDOWN_LENGTH = 100_000
# HTTP fetch timeout in seconds
WEB_FETCH_TIMEOUT_S = 60
# URL result cache: entries and TTL
WEB_CACHE_MAX_ENTRIES = 64
WEB_CACHE_TTL_S = 15 * 60  # 15 minutes

# Code interpreter display limits
CODE_PANEL_WARN_CHARS = 150_000  # Total display chars before mild warning
CODE_PANEL_WARN_ITEMS  = 3        # Max open items before mild warning
INTERPRETER_MAX_FILE_CHARS = 150_000  # Per-file char limit; larger files get truncated
INTERPRETER_TRUNCATE_PREVIEW_LINES = 20  # Lines shown when a file is truncated

# =============================================================================
# File Read Safety — shared across gateway + code tools
# =============================================================================
# Files larger than this are never fully loaded into memory.  The gateway
# skips snapshotting/diffing them; the code tools use buffered I/O instead.
MAX_FILE_READ_SIZE = 5_000_000  # 5 MB

def count_lines_buffered(file_path, chunk_size: int = 64 * 1024) -> int:
    """Count lines in *file_path* without loading the entire file into memory.

    Reads in *chunk_size* blocks in binary mode and counts ``\n`` bytes.
    Safe for arbitrarily large files (e.g. 4 GB logs).
    """
    count = 0
    with open(file_path, 'rb') as f:
        while chunk := f.read(chunk_size):
            count += chunk.count(b'\n')
    return count

# Severe warning thresholds — triggers a stronger, more urgent warning
# when the agent has too many files open or the combined display is huge.
CODE_PANEL_CRITICAL_ITEMS = 7        # >= this many files → severe warning
CODE_PANEL_CRITICAL_CHARS = 300_000  # >= this many display chars → severe warning

# File Operation Markers
# Terminal Environment Note — adapts to platform
_TERMINAL_BLOCKING_NOTE = (
    " For long-running processes (servers, training, etc.), set blocking=false "
    "instead of using nohup or &. The command runs in the background and a log "
    "file path is returned so you can check progress later. "
    "If a blocking command times out, the process keeps running and a new "
    "terminal is created automatically — read the returned log path to monitor."
)
if DOCKER_MODE:
    TERMINAL_ENV_NOTE = (
        "**Terminal**: For long-running processes (servers, training, etc.), set blocking=false "
        "instead of using nohup or &. The command runs in the background and a log "
        "file path is returned so you can check progress later. "
        "If a blocking command times out, the process keeps running and a new "
        "terminal is created automatically — read the returned log path to monitor."
    )
else:
    if sys.platform == "win32":
        TERMINAL_ENV_NOTE = (
            "Environment Note: The terminal runs commands in a Windows Command Prompt (cmd.exe) session within a Conda environment. "
            "You can chain commands with '&&' (e.g. 'command_1 && command_2')."
        )
    else:
        TERMINAL_ENV_NOTE = (
            "Environment Note: The terminal runs commands in a Bash shell within a Conda environment."
            + _TERMINAL_BLOCKING_NOTE
        )

# Display guide — VNC desktop + dev-server ports for showing web content to the user.
if DOCKER_VNC:
    DISPLAY_GUIDE = """
**GUI Display via noVNC**:
A virtual desktop (Xvfb + fluxbox) is running. GUI applications render on DISPLAY=:99 automatically.
The user can view the live desktop through the noVNC viewer (port 6080).

- **matplotlib**: use `matplotlib.use("TkAgg")` BEFORE importing pyplot, then `plt.show()`. The default `Agg` backend is non-interactive and will NOT display a window.
- **Any GUI app** (pygame, tkinter, browser, etc.): just run it — the window appears on the noVNC desktop.
- To launch a GUI app in the background so the terminal stays responsive, use `blocking=false` in `run_terminal_command`.
- If the user can't find the GUI output, tell them to open port 6080 of the current server address in their browser.
- To serve web content (HTML pages, dashboards, reports) for the user to view, start an HTTP server on ports 8900–8902 (e.g., `python -m http.server 8900`). The user accesses them at the same port on the host.
"""
else:
    DISPLAY_GUIDE = ""

# System Message Template
SYSTEM_MESSAGE_TEMPLATE = """You are a helpful and autonomous agent with powerful tools. You are running inside a Docker container (Linux). Your primary goal is to thoroughly address the user's query by leveraging your tools to gather comprehensive information and execute necessary actions.

**Workspace**: Your working directory is `/workspace`. All file operations use paths **relative to /workspace** unless an absolute path is given. The terminal shell also starts in `/workspace`. When the user uploads a project, its files appear here.

**Workspace File Tree**:
{workspace_tree}

**SUPER IMPORTANT**: Do exactly what the user asks — no more, no less. Work autonomously toward the goal without stopping to ask the user. Use your tools to investigate and resolve issues yourself. Only ask the user when you truly cannot proceed without their input.

{display_guide}
{terminal_env_note}

**Guidelines**:
- Use English exclusively in all generated code and comments.
- Never delegate write/execute operations to a subagent — it is read-only.
- **edit_file**: at most 3 edits per call per file. Split larger changes across multiple tool rounds.
{memory_section}
Current Time: {current_time}
{toolstore_tools}
"""

# =============================================================================
# Dynamic configuration functions have moved to gateway/provider_registry.py.
# This file is now static constants only.
# =============================================================================