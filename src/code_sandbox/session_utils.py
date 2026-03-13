"""
Session Management Utilities

This module provides reusable functions for managing isolated conda environments
and working directories that can be used by any application.
"""

import logging
from typing import Optional, Dict, Any, Callable
from pathlib import Path
from .session_manager import SessionManager, session_manager
import atexit
import signal
import sys

logger = logging.getLogger(__name__)


def create_session_environment(
    session_name: Optional[str] = None,
    auto_cleanup: bool = True,
    base_env_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create an isolated session environment with conda and workspace.
    
    Args:
        session_name: Optional name for the session
        auto_cleanup: Whether to automatically clean up on exit
        base_env_name: Optional conda environment name to clone from.
                      If not provided, uses the SessionManager's default.
        
    Returns:
        Dictionary with session information
    """
    try:
        session_info = session_manager.create_session(session_name, base_env_name=base_env_name)
        
        if session_info['status'] == 'active' and auto_cleanup:
            # Register cleanup handlers
            _register_cleanup_handlers(session_manager)
            
        return session_info
        
    except Exception as e:
        logger.error(f"Failed to create session environment: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'session_id': None,
            'session_name': None,
            'conda_env_name': None,
            'base_env_name': None,
            'session_dir': None
        }


def with_session_environment(
    session_name: Optional[str] = None,
    auto_cleanup: bool = True,
    base_env_name: Optional[str] = None
):
    """
    Decorator to run a function within an isolated session environment.
    
    Args:
        session_name: Optional name for the session
        auto_cleanup: Whether to automatically clean up on exit
        base_env_name: Optional conda environment name to clone from
        
    Example:
        @with_session_environment("my_task", base_env_name="my_base_env")
        def my_function():
            # This runs in isolated environment cloned from my_base_env
            pass
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            session_info = create_session_environment(session_name, auto_cleanup, base_env_name)
            
            if session_info['status'] == 'failed':
                logger.error(f"Session creation failed: {session_info.get('error')}")
                raise RuntimeError(f"Session creation failed: {session_info.get('error')}")
            
            logger.info(f"Running {func.__name__} in session: {session_info['session_name']}")
            
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in session function {func.__name__}: {e}")
                raise
            finally:
                if not auto_cleanup:
                    logger.info(f"Session preserved: {session_info['session_dir']}")
                    
        return wrapper
    return decorator


class SessionContext:
    """
    Context manager for session environments.
    
    Example:
        with SessionContext("my_task", base_env_name="my_base_env") as session:
            # Work in isolated environment cloned from my_base_env
            print(f"Working in: {session.session_dir}")
    """
    
    def __init__(
        self,
        session_name: Optional[str] = None,
        auto_cleanup: bool = True,
        base_env_name: Optional[str] = None
    ):
        self.session_name = session_name
        self.auto_cleanup = auto_cleanup
        self.base_env_name = base_env_name
        self.session_info = None
        
    def __enter__(self):
        self.session_info = session_manager.create_session(
            self.session_name, 
            base_env_name=self.base_env_name
        )
        
        if self.session_info['status'] == 'failed':
            raise RuntimeError(f"Session creation failed: {self.session_info.get('error')}")
        
        logger.info(f"Entered session: {self.session_info['session_name']}")
        return session_manager
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.auto_cleanup:
            try:
                session_manager.cleanup_session()
                logger.info("Session cleaned up successfully")
            except Exception as e:
                logger.error(f"Error during session cleanup: {e}")
        else:
            logger.info(f"Session preserved: {self.session_info['session_dir']}")


def init_application_session(
    app_name: str,
    cleanup_on_exit: bool = True,
    max_old_sessions: int = 10,
    base_env_name: Optional[str] = None,
    reuse_env: bool = False
) -> Dict[str, Any]:
    """
    Initialize session environment for an application.
    
    This is a high-level function that:
    1. Creates a session for the application
    2. Sets up cleanup handlers
    3. Cleans up old sessions
    4. Returns session info for the application to use
    
    Args:
        app_name: Name of the application
        cleanup_on_exit: Whether to clean up when app exits
        max_old_sessions: Maximum number of old sessions to keep
        base_env_name: Optional conda environment name to clone from.
                      If not provided, uses the SessionManager's default.
        reuse_env: If True, use base_env_name directly instead of cloning.
        
    Returns:
        Session information dictionary
    """
    try:
        # Clean up old sessions first
        if max_old_sessions > 0:
            logger.info("Cleaning up old sessions...")
            session_manager.cleanup_old_sessions(max_old_sessions)
        
        # Create new session
        logger.info(f"Creating session for application: {app_name}")
        if base_env_name:
            logger.info(f"Using base environment: {base_env_name} (reuse={reuse_env})")
        session_info = session_manager.create_session(
            app_name, base_env_name=base_env_name, reuse_env=reuse_env
        )
        
        if session_info['status'] == 'failed':
            logger.error(f"Session creation failed: {session_info.get('error')}")
            return session_info
        
        # Set up cleanup if requested
        if cleanup_on_exit:
            _register_cleanup_handlers(session_manager)
        
        # Log success
        logger.info(f"✅ Session initialized for {app_name}")
        logger.info(f"📁 Session directory: {session_info['session_dir']}")
        logger.info(f"🐍 Conda environment: {session_info['conda_env_name']}")
        logger.info(f"📦 Cloned from: {session_info.get('base_env_name', 'default')}")
        
        return session_info
        
    except Exception as e:
        logger.error(f"Failed to initialize application session: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'session_id': None,
            'session_name': None,
            'conda_env_name': None,
            'base_env_name': None,
            'session_dir': None
        }


def _register_cleanup_handlers(session_manager: SessionManager):
    """Register cleanup handlers for the session manager."""
    
    def cleanup_handler():
        """Cleanup handler function."""
        try:
            logger.info("Cleaning up session...")
            session_manager.cleanup_session()
            logger.info("Session cleanup complete")
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
    
    # Register with atexit
    atexit.register(cleanup_handler)
    
    # Register signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, cleaning up...")
        cleanup_handler()
        sys.exit(0)
    
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, signal_handler)
    except (ValueError, OSError) as e:
        # Some signals might not be available on all platforms
        logger.debug(f"Could not register signal handler: {e}")


def load_session_environment(
    session_id: Optional[str] = None,
    session_name: Optional[str] = None,
    auto_cleanup: bool = True
) -> Dict[str, Any]:
    """
    Load an existing session environment and continue working in it.
    
    Args:
        session_id: The session ID to load (e.g., "abc12345")
        session_name: The full session name to load
        auto_cleanup: Whether to automatically clean up on exit
        
    Returns:
        Dictionary with session information
    """
    try:
        session_info = session_manager.load_session(
            session_id=session_id,
            session_name=session_name
        )
        
        if session_info.get('status') == 'active' and auto_cleanup:
            # Register cleanup handlers
            _register_cleanup_handlers(session_manager)
        
        return session_info
        
    except Exception as e:
        logger.error(f"Failed to load session environment: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'session_id': None,
            'session_name': None,
            'conda_env_name': None,
            'base_env_name': None,
            'session_dir': None
        }


def list_loadable_sessions() -> Dict[str, Any]:
    """
    List all sessions that can be loaded (have active conda environments).
    
    Returns:
        Dictionary with list of loadable sessions
    """
    try:
        sessions = session_manager.list_sessions(include_loadable_only=True)
        return {
            'status': 'success',
            'sessions': sessions,
            'total_sessions': len(sessions)
        }
    except Exception as e:
        logger.error(f"Failed to list loadable sessions: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'sessions': []
        }


def get_session_status(session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get status of sessions.
    
    Args:
        session_id: Optional session ID to get specific session status
        
    Returns:
        Session status information
    """
    try:
        sessions = session_manager.list_sessions()
        
        if session_id:
            # Return specific session
            session = next((s for s in sessions if s['session_id'] == session_id), None)
            if session:
                return session
            else:
                return {'status': 'not_found', 'error': f'Session {session_id} not found'}
        else:
            # Return all sessions
            return {
                'status': 'success',
                'sessions': sessions,
                'total_sessions': len(sessions)
            }
            
    except Exception as e:
        logger.error(f"Error getting session status: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


def cleanup_sessions(
    session_id: Optional[str] = None,
    max_sessions: int = 5
) -> Dict[str, Any]:
    """
    Clean up sessions.
    
    Args:
        session_id: Optional specific session ID to cleanup
        max_sessions: Maximum number of sessions to keep (if session_id not specified)
        
    Returns:
        Cleanup result information
    """
    try:
        if session_id:
            # For now, this will clean the currently active session in the global manager
            current_session_id = session_manager.session_id
            if current_session_id == session_id:
                session_manager.cleanup_session()
                return {
                    'status': 'success',
                    'message': f'Session {session_id} cleaned up successfully'
                }
            else:
                # In a real-world scenario, you might need to load session info
                # to clean up an arbitrary session. This is a simplification.
                return {
                    'status': 'failed',
                    'message': f'Cleanup of arbitrary session {session_id} is not fully supported. Only active session can be cleaned.'
                }
        else:
            # Cleanup old sessions
            session_manager.cleanup_old_sessions(max_sessions)
            return {
                'status': 'success',
                'message': f'Old sessions cleaned up, keeping {max_sessions} most recent'
            }
            
    except Exception as e:
        logger.error(f"Error cleaning up sessions: {e}")
        return {
            'status': 'error',
            'error': str(e)
        } 