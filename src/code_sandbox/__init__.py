"""
Code Sandbox - Isolated Environment Management

This package provides tools for creating and managing isolated conda environments
and workspaces for safe AI agent operations.
"""

from .session_manager import SessionManager, session_manager
from .session_utils import (
    create_session_environment,
    with_session_environment,
    SessionContext,
    init_application_session,
    get_session_status,
    cleanup_sessions,
    load_session_environment,
    list_loadable_sessions
)

__all__ = [
    'SessionManager',
    'session_manager',
    'create_session_environment',
    'with_session_environment',
    'SessionContext',
    'init_application_session',
    'get_session_status',
    'cleanup_sessions',
    'load_session_environment',
    'list_loadable_sessions'
] 