"""
Code Sandbox — Docker workspace and persistent shell.

Provides a fixed workspace path (``/workspace``) and a long-lived Bash
shell for the Docker deployment model.
"""

from .sandbox import (
    WORKSPACE,
    get_workspace,
    get_python_path,
    get_conda_env_path,
    shell,
    PersistentShell,
)

__all__ = [
    "WORKSPACE",
    "get_workspace",
    "get_python_path",
    "get_conda_env_path",
    "shell",
    "PersistentShell",
]
