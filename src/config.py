"""
Configuration for the native tool calling system.

This replaces the XML-based configuration with settings for native OpenAI function calling.
"""

from pathlib import Path
import os

# ---------------------------------------------------------------------------
# Data directory — all runtime data (conversation logs, training data, etc.)
# In Docker: /app/data  (volume-mounted to the host)
# Locally:   ~/.thinktool/data  (outside the project tree)
# Override:  set THINKTOOL_DATA_DIR env var
# ---------------------------------------------------------------------------
if os.environ.get("THINKTOOL_DOCKER", "0") == "1":
    DATA_DIR = Path("/app/data")
else:
    DATA_DIR = Path(os.environ.get(
        "THINKTOOL_DATA_DIR",
        os.path.expanduser("~/.thinktool/data"),
    ))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRAINING_DATA_DIR = DATA_DIR / "training"

proxy_host = 'localhost'
proxy_port = 10794

# =============================================================================
# Model Provider Configurations
# =============================================================================
# Each provider config contains all settings needed to create an OpenAI client
# and make API calls. Users can switch between providers on the frontend.

MODEL_PROVIDERS = {
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek V4 Pro",
        "description": "Fast reasoning model with thinking (~2s TTFT)",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": "deepseek-v4-pro",
        "supports_thinking": True,
        "extra_body": None,
        "context_window": 1_048_576,
    },
    "nvidia": {
        "id": "nvidia",
        "name": "NVIDIA DeepSeek V4 Pro",
        "description": "NVIDIA hosted V4 Pro with thinking",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": os.environ.get("NVIDIA_API_KEY", ""),
        "model": "deepseek-ai/deepseek-v4-pro",
        "supports_thinking": True,
        "extra_body": {"chat_template_kwargs": {"thinking": True}},
        "context_window": 1_048_576,
    },
    "nvidia-fast": {
        "id": "nvidia-fast",
        "name": "NVIDIA DeepSeek V4 Pro (No Thinking)",
        "description": "NVIDIA hosted V4 Pro, no reasoning (faster)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": os.environ.get("NVIDIA_API_KEY", ""),
        "model": "deepseek-ai/deepseek-v4-pro",
        "supports_thinking": False,
        "extra_body": None,
        "context_window": 1_048_576,
    },
    "nvidia-glm5": {
        "id": "nvidia-glm5",
        "name": "NVIDIA GLM-5.1",
        "description": "Z-AI GLM-5.1 on NVIDIA with deep thinking",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": os.environ.get("NVIDIA_API_KEY", ""),
        "model": "z-ai/glm-5.1",
        "supports_thinking": True,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False}
        },
        "context_window": 128_000,
    },
    "nvidia-glm5-fast": {
        "id": "nvidia-glm5-fast",
        "name": "NVIDIA GLM-5.1 (No Thinking)",
        "description": "Z-AI GLM-5.1 on NVIDIA, no reasoning (faster)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": os.environ.get("NVIDIA_API_KEY", ""),
        "model": "z-ai/glm-5.1",
        "supports_thinking": False,
        "extra_body": None,
        "context_window": 128_000,
    },
    "gemini-3-pro-api": {
        "id": "gemini-3-pro-api",
        "name": "Gemini 3.1 Pro (AI Studio)",
        "description": "Google AI Studio API (Requires GEMINI_API_KEY)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": os.environ.get("GEMINI_API_KEY", ""),
        "model": "gemini-3.1-pro-preview",
        "supports_thinking": True,
        "extra_body": None,
        "context_window": 1_048_576,
    },
}

# Default provider to use
DEFAULT_PROVIDER = "deepseek"

# Legacy compatibility - these are used as defaults if provider not specified
BASE_URL = MODEL_PROVIDERS[DEFAULT_PROVIDER]["base_url"]
API_KEY = MODEL_PROVIDERS[DEFAULT_PROVIDER]["api_key"]
MODEL_NAME = MODEL_PROVIDERS[DEFAULT_PROVIDER]["model"]

# Model Parameters
MAX_TOKENS = 32768

# Tool Calling Limits
MAX_TOOL_CALLS = 10  # Increased since native tool calling is more efficient

# Iteration Limits
MAX_ITERATIONS = 30  # Maximum iterations per turn before requiring user to continue
MAX_STREAMING_RETRIES = 10  # Consecutive streaming failures before giving up

# Tool Concurrency
MAX_TOOL_CONCURRENCY = 5  # Max parallel threads for concurrent-safe tools

# Subagent
SUBAGENT_MAX_ITERATIONS = 15  # Lower cap than the parent agent
SUBAGENT_MAX_RESULT_CHARS = 4000  # Truncate subagent final response to save parent context

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
# Auto-detected via THINKTOOL_DOCKER env var (set in Dockerfile / docker-compose).
DOCKER_MODE = os.environ.get("THINKTOOL_DOCKER", "0") == "1"
DOCKER_VNC = os.environ.get("THINKTOOL_VNC", "0") == "1"
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

# =============================================================================
# Web Browser (secondary model summarization)
# =============================================================================
# A cheap/fast model processes raw web pages so only a concise summary
# enters the main agent's context window.
# The provider ID references one of MODEL_PROVIDERS (or a custom provider).
WEB_SECONDARY_PROVIDER = "deepseek"
WEB_SECONDARY_MODEL_MAX_TOKENS = 4096

# Max characters of page markdown fed to the secondary model
WEB_MAX_MARKDOWN_LENGTH = 100_000
# HTTP fetch timeout in seconds
WEB_FETCH_TIMEOUT_S = 60
# URL result cache: entries and TTL
WEB_CACHE_MAX_ENTRIES = 64
WEB_CACHE_TTL_S = 15 * 60  # 15 minutes

# Code interpreter display limits
INTERPRETER_WARN_CHARS = 50_000       # Total display chars before context warning
INTERPRETER_MAX_FILES = 5             # Max open files before context warning
TOOLSET_WARN_CHARS   = 30_000         # Total toolset display chars before context warning
TOOLSET_MAX_TOOLS    = 4              # Max open toolsets before context warning
INTERPRETER_MAX_FILE_CHARS = 150_000  # Per-file char limit; larger files get truncated
INTERPRETER_TRUNCATE_PREVIEW_LINES = 20  # Lines shown when a file is truncated

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
        "Environment Note: The terminal runs commands in a Bash shell inside a Docker container. "
        "You can chain commands with '&&' (e.g. 'command_1 && command_2')."
        + _TERMINAL_BLOCKING_NOTE
    )
else:
    import sys as _sys
    if _sys.platform == "win32":
        TERMINAL_ENV_NOTE = (
            "Environment Note: The terminal runs commands in a Windows Command Prompt (cmd.exe) session within a Conda environment. "
            "You can chain commands with '&&' (e.g. 'command_1 && command_2')."
        )
    else:
        TERMINAL_ENV_NOTE = (
            "Environment Note: The terminal runs commands in a Bash shell within a Conda environment. "
            "You can chain commands with '&&' (e.g. 'command_1 && command_2')."
            + _TERMINAL_BLOCKING_NOTE
        )

# VNC instructions block — only included when the VNC desktop is available.
if DOCKER_VNC:
    VNC_INSTRUCTIONS = """
**GUI Display via noVNC**:
A virtual desktop (Xvfb + fluxbox) is running. GUI applications render on DISPLAY=:99 automatically.
The user can view the live desktop through the noVNC viewer (port 6080).

- **matplotlib**: use `matplotlib.use("TkAgg")` BEFORE importing pyplot, then `plt.show()`. The default `Agg` backend is non-interactive and will NOT display a window.
- **Any GUI app** (pygame, tkinter, browser, etc.): just run it — the window appears on the noVNC desktop.
- To launch a GUI app in the background so the terminal stays responsive, use `blocking=false` in `run_terminal_command`.
- If the user can't find the GUI output, tell them to open port 6080 of the current server address in their browser.
"""
else:
    VNC_INSTRUCTIONS = ""

# System Message Template
SYSTEM_MESSAGE_TEMPLATE = """You are a helpful and autonomous agent with powerful tools. You are running inside a Docker container (Linux). Your primary goal is to thoroughly address the user's query by leveraging your tools to gather comprehensive information and execute necessary actions.

**Workspace**: Your working directory is `/workspace`. All file operations use paths **relative to /workspace** unless an absolute path is given. The terminal shell also starts in `/workspace`. When the user uploads a project, its files appear here.

**SUPER IMPORTANT**: Do EXACTLY what the user asks you to do. For anything else the user may need beyond their explicit request, ASK before doing so. Do not assume or add extra actions without user confirmation.

As an autonomous agent, proactively leverage your tools to fully resolve the user's requests end-to-end. Refrain from asking the user to perform tasks or provide clarification unless essential information cannot be acquired through your tools.

Current Time: {current_time}
{vnc_instructions}
{terminal_env_note}

**Guidelines**:
- Use English exclusively in all generated code and comments.
- Never delegate write/execute operations to a subagent — it is read-only.
- **edit_file**: at most 3 edits per call per file. Split larger changes across multiple tool rounds.
"""

# =============================================================================
# Dynamic configuration functions have moved to gateway/provider_registry.py.
# This file is now static constants only.
# =============================================================================