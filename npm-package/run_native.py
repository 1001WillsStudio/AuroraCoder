#!/usr/bin/env python3
"""
Native-mode launcher for AuroraCoder.

Patches the system message template for non-Docker environments, then starts
either the backend (:8080) or gateway (:8081) via uvicorn.

Usage:
    python run_native.py backend 8080
    python run_native.py gateway 8081

The original ``src/config.py`` is NOT modified — patches are applied at import time
before any other module reads the template.

Co-authored-by: AuroraCoderAgent <aurorathesnowyfox@gmail.com>
"""

import os
import sys

# ---------------------------------------------------------------------------
# Make sure the AuroraCoder project root is on sys.path BEFORE we import
# anything from src/ or gateway/.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Patch: replace the Docker-centric system message with a native one.
# Must happen BEFORE src.web_api or gateway.api import src.config.
# ---------------------------------------------------------------------------
import src.config as _cfg  # noqa: E402

_NATIVE_SYSTEM_MESSAGE = """\
You are a helpful and autonomous agent with powerful tools. You are running on the
host machine (native install — no Docker sandbox). Your primary goal is to
thoroughly address the user's query by leveraging your tools to gather
comprehensive information and execute necessary actions.

**Workspace**: Your working directory is the current working directory (cwd). All
file operations use paths **relative to the working directory** unless an
absolute path is given. The terminal shell also starts in the working directory.
When the user uploads a project, its files appear here.

**SUPER IMPORTANT**: Do EXACTLY what the user asks you to do. For anything else
the user may need beyond their explicit request, ASK before doing so. Do not
assume or add extra actions without user confirmation.

As an autonomous agent, proactively leverage your tools to fully resolve the
user's requests end-to-end. Refrain from asking the user to perform tasks or
provide clarification unless essential information cannot be acquired through
your tools.

{vnc_instructions}
{terminal_env_note}

**Guidelines**:
- Use English exclusively in all generated code and comments.
- Never delegate write/execute operations to a subagent — it is read-only.
- **edit_file**: at most 3 edits per call per file. Split larger changes across
  multiple tool rounds.

Current Time: {current_time}
{toolstore_tools}
"""

# Apply the patch — replaces the module-level constant with our native version.
_cfg.SYSTEM_MESSAGE_TEMPLATE = _NATIVE_SYSTEM_MESSAGE

# Also patch TERMINAL_ENV_NOTE — config.py's default mentions Conda which
# may not exist on the user's native install.
_cfg.TERMINAL_ENV_NOTE = (
    "Environment Note: The terminal runs commands in a Bash shell within"
    " your host OS environment.\n"
)

# ---------------------------------------------------------------------------
# Now delegate to the actual server.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: run_native.py <backend|gateway> <port>", file=sys.stderr)
        sys.exit(1)

    server_type = sys.argv[1]
    port = int(sys.argv[2])

    import uvicorn

    if server_type == "backend":
        uvicorn.run(
            "src.web_api:app",
            host="0.0.0.0",
            port=port,
            log_level="warning",
        )
    elif server_type == "gateway":
        os.environ.setdefault("BACKEND_URL", f"http://localhost:8080")
        uvicorn.run(
            "gateway.api:app",
            host="0.0.0.0",
            port=port,
            log_level="warning",
        )
    else:
        print(f"Unknown server type: {server_type}", file=sys.stderr)
        sys.exit(1)
