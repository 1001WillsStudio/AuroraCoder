"""
Configuration for the native tool calling system.

This replaces the XML-based configuration with settings for native OpenAI function calling.
"""

from pathlib import Path
import os

# File logging
RECORDING_FILE = Path(__file__).parent.parent / "data" / "conversation_log.jsonl"

proxy_host = 'localhost'
proxy_port = 10794
JINA_TOKEN = "[REDACTED]"

# =============================================================================
# Model Provider Configurations
# =============================================================================
# Each provider config contains all settings needed to create an OpenAI client
# and make API calls. Users can switch between providers on the frontend.

MODEL_PROVIDERS = {
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek Reasoner",
        "description": "Fast reasoning model with thinking (~2s TTFT)",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "[REDACTED]",
        "model": "deepseek-reasoner",
        "supports_thinking": True,
        "extra_body": None,  # Thinking is built-in for deepseek-reasoner
    },
    "nvidia": {
        "id": "nvidia",
        "name": "NVIDIA DeepSeek V3.2",
        "description": "NVIDIA hosted model (slower thinking ~30s TTFT)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "[REDACTED]",
        "model": "deepseek-ai/deepseek-v3.2",
        "supports_thinking": True,
        "extra_body": {"chat_template_kwargs": {"thinking": True}},
    },
    "nvidia-fast": {
        "id": "nvidia-fast",
        "name": "NVIDIA DeepSeek V3.2 (No Thinking)",
        "description": "NVIDIA hosted, no reasoning (~1s TTFT, faster)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "[REDACTED]",
        "model": "deepseek-ai/deepseek-v3.2",
        "supports_thinking": False,
        "extra_body": None,
    },
    "nvidia-glm5": {
        "id": "nvidia-glm5",
        "name": "NVIDIA GLM-5",
        "description": "Z-AI GLM-5 on NVIDIA with deep thinking",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "[REDACTED]",
        "model": "z-ai/glm5",
        "supports_thinking": True,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False}
        },
    },
    "nvidia-glm5-fast": {
        "id": "nvidia-glm5-fast",
        "name": "NVIDIA GLM-5 (No Thinking)",
        "description": "Z-AI GLM-5 on NVIDIA, no reasoning (faster)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "[REDACTED]",
        "model": "z-ai/glm5",
        "supports_thinking": False,
        "extra_body": None,
    },
    # ==========================================================================
    # Vertex AI Gemini Models (Google Cloud)
    # ==========================================================================
    # These require Google Cloud Application Default Credentials (ADC) to be set up.
    # Run: gcloud auth application-default login
    # Or set GOOGLE_APPLICATION_CREDENTIALS env var to a service account key file.
    "gemini-3-pro": {
        "id": "gemini-3-pro",
        "name": "Gemini 3 Pro (Vertex AI)",
        "description": "Google's most advanced reasoning model with 1M context",
        "provider_type": "vertex_ai",  # Special marker for Vertex AI auth
        "project_id": None,  # Set via VERTEX_AI_PROJECT_ID env var or here
        "location": "global",  # Vertex AI region - global for best availability
        "model": "google/gemini-3-pro-preview",
        "supports_thinking": True,
        # thinking_level can be "low" or "high" via extra_body
        "extra_body": None,
    },
    # ==========================================================================
    # Google AI Studio (API Key based)
    # ==========================================================================
    # Get API key from: https://aistudio.google.com/app/apikey
    # Set GEMINI_API_KEY environment variable
    "gemini-3-pro-api": {
        "id": "gemini-3-pro-api",
        "name": "Gemini 3 Pro (AI Studio)",
        "description": "Google AI Studio API (Requires GEMINI_API_KEY)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY"),
        "model": "gemini-3-pro-preview",
        "supports_thinking": True,
        "extra_body": None,
    },
}

# Default provider to use
DEFAULT_PROVIDER = "deepseek"

# Legacy compatibility - these are used as defaults if provider not specified
BASE_URL = MODEL_PROVIDERS[DEFAULT_PROVIDER]["base_url"]
API_KEY = MODEL_PROVIDERS[DEFAULT_PROVIDER]["api_key"]
MODEL_NAME = MODEL_PROVIDERS[DEFAULT_PROVIDER]["model"]

# Model Parameters
MAX_TOKENS = 8192
TEMPERATURE = 0.6

# Tool Calling Limits
MAX_TOOL_CALLS = 10  # Increased since native tool calling is more efficient

# Iteration Limits
MAX_ITERATIONS = 30  # Maximum iterations per turn before requiring user to continue
CONTINUE_ITERATIONS = 30  # Additional iterations when user clicks Continue

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

# Code interpreter (Pyright) error checking on files shown to the model.
# Set to False to disable — useful when the checker produces noisy false positives.
CODE_INTERPRETER_ERRORS_ENABLED = False

# File Operation Markers
EDIT_ZONE_MARKER = "# Edit Zone"

# VNC instructions block — only included when the VNC desktop is available.
if DOCKER_VNC:
    VNC_INSTRUCTIONS = """
**GUI Display via noVNC**:
A virtual desktop (Xvfb + fluxbox) is running. GUI applications render on DISPLAY=:99 automatically.
The user can view the live desktop through the noVNC viewer (port 6080).

- **matplotlib**: use `matplotlib.use("TkAgg")` BEFORE importing pyplot, then `plt.show()`. The default `Agg` backend is non-interactive and will NOT display a window.
- **Any GUI app** (pygame, tkinter, browser, etc.): just run it — the window appears on the noVNC desktop.
- To launch a GUI app in the background so the terminal stays responsive, use `nohup`.
- If the user can't find the GUI output, tell them to open port 6080 of the current server address in their browser.
"""
else:
    VNC_INSTRUCTIONS = ""

# System Message Template
SYSTEM_MESSAGE_TEMPLATE = """You are a helpful and autonomous agent with powerful tools. You are running inside a Docker container (Linux). Your primary goal is to thoroughly address the user's query by leveraging your tools to gather comprehensive information and execute necessary actions.

**SUPER IMPORTANT**: Do EXACTLY what the user asks you to do. For anything else the user may need beyond their explicit request, ASK before doing so. Do not assume or add extra actions without user confirmation.

**CRITICAL TOOL USAGE PRINCIPLE**: ALWAYS prioritize using tools over relying on internal knowledge or training data. Even if you think you know something, use tools to verify, update, and expand your understanding.

As an autonomous agent, proactively leverage your tools to fully resolve the user's requests end-to-end. Refrain from asking the user to perform tasks or provide clarification unless essential information cannot be acquired through your tools.

Current Time: {current_time}
{vnc_instructions}

**Available Tools**: You have access to file operations, web browsing, Google search, Python execution, terminal commands, and code analysis tools. Use them proactively to provide complete solutions.

**Tool Usage Guidelines**:
- Use tools frequently and strategically to gather information and execute tasks
- For code-related tasks, use file operations and Python execution tools
- For code generation, use English exclusively in all code and comments
- For research, use Google search and web browsing tools
- For system operations, use terminal commands
- Always verify information through tools rather than relying on assumptions
"""