#!/usr/bin/env python3
"""
Session Management CLI for ThinkWithTool

This script provides command-line utilities for managing isolated conda environments
and working directories for AI agent sessions.
"""

import argparse
import sys
import json
from pathlib import Path
from .session_utils import create_session_environment, get_session_status, cleanup_sessions
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def create_session(args):
    """Create a new session"""
    try:
        print(f"Creating session: {args.name or 'default'}")
        session_info = create_session_environment(
            session_name=args.name,
            auto_cleanup=False  # Don't auto cleanup for CLI-created sessions
        )
        
        if session_info['status'] == 'active':
            print(f"✅ Session created successfully!")
            print(f"📁 Session directory: {session_info['session_dir']}")
            print(f"🐍 Conda environment: {session_info['conda_env_name']}")
            print(f"🆔 Session ID: {session_info['session_id']}")
            
            # Print activation command
            print(f"\nTo activate this session environment manually:")
            print(f"  conda activate {session_info['conda_env_name']}")
            print(f"  cd {session_info['session_dir']}")
            
        else:
            print(f"❌ Session creation failed: {session_info.get('error', 'Unknown error')}")
            return 1
            
    except Exception as e:
        print(f"❌ Error creating session: {e}")
        return 1
    
    return 0


def list_sessions(args):
    """List all sessions"""
    try:
        status_info = get_session_status()
        
        if status_info.get('status') != 'success':
            print(f"❌ Error getting session status: {status_info.get('error', 'Unknown error')}")
            return 1
        
        sessions = status_info.get('sessions', [])
        
        if not sessions:
            print("No sessions found.")
            return 0
        
        print(f"Found {len(sessions)} sessions:\n")
        
        for i, session in enumerate(sessions, 1):
            status_emoji = {
                'active': '✅',
                'failed': '❌',
                'cleaned_up': '🧹',
                'initializing': '⏳'
            }.get(session.get('status', 'unknown'), '❓')
            
            print(f"{i}. {status_emoji} {session['session_name']}")
            print(f"   ID: {session['session_id']}")
            print(f"   Status: {session.get('status', 'unknown')}")
            print(f"   Created: {session.get('created_at', 'unknown')}")
            print(f"   Directory: {session['session_dir']}")
            print(f"   Conda env: {session['conda_env_name']}")
            print()
            
    except Exception as e:
        print(f"❌ Error listing sessions: {e}")
        return 1
    
    return 0


def cleanup_session(args):
    """Clean up a specific session or all sessions"""
    try:
        if args.session_id:
            # Clean up specific session
            print(f"Cleaning up session: {args.session_id}")
            result = cleanup_sessions(session_id=args.session_id)
            
            if result.get('status') == 'success':
                print("✅ Session cleanup complete")
            else:
                print(f"❌ Cleanup failed: {result.get('error', 'Unknown error')}")
                return 1
        else:
            # Clean up old sessions
            print("Cleaning up old sessions...")
            result = cleanup_sessions(max_sessions=args.keep or 5)
            
            if result.get('status') == 'success':
                print("✅ Old sessions cleanup complete")
            else:
                print(f"❌ Cleanup failed: {result.get('error', 'Unknown error')}")
                return 1
            
    except Exception as e:
        print(f"❌ Error cleaning up sessions: {e}")
        return 1
    
    return 0


def session_info(args):
    """Show detailed information about a session"""
    try:
        if args.session_id:
            session = get_session_status(args.session_id)
            
            if session.get('status') == 'not_found':
                print(f"❌ Session '{args.session_id}' not found")
                return 1
            elif session.get('status') == 'error':
                print(f"❌ Error getting session info: {session.get('error', 'Unknown error')}")
                return 1
            
            print(f"Session Details:")
            print(f"  Name: {session['session_name']}")
            print(f"  ID: {session['session_id']}")
            print(f"  Status: {session.get('status', 'unknown')}")
            print(f"  Created: {session.get('created_at', 'unknown')}")
            print(f"  Directory: {session['session_dir']}")
            print(f"  Conda env: {session['conda_env_name']}")
            
            if session.get('cleaned_up_at'):
                print(f"  Cleaned up: {session['cleaned_up_at']}")
            
            # Check if directory still exists
            session_dir = Path(session['session_dir'])
            if session_dir.exists():
                print(f"  Directory exists: ✅")
                print(f"  Directory size: {sum(f.stat().st_size for f in session_dir.rglob('*') if f.is_file())} bytes")
            else:
                print(f"  Directory exists: ❌")
                
        else:
            print("❌ Please provide a session ID")
            return 1
            
    except Exception as e:
        print(f"❌ Error getting session info: {e}")
        return 1
    
    return 0


def test_session(args):
    """Test the session environment"""
    try:
        # Get the most recent session
        status_info = get_session_status()
        
        if status_info.get('status') != 'success':
            print(f"❌ Error getting session status: {status_info.get('error', 'Unknown error')}")
            return 1
        
        sessions = status_info.get('sessions', [])
        
        if not sessions:
            print("❌ No sessions found")
            return 1
        
        # Use the most recent session
        current_session = sessions[0]
        session_dir = Path(current_session['session_dir'])
        
        print(f"Testing session environment...")
        print(f"📁 Session directory: {session_dir}")
        print(f"🐍 Conda environment: {current_session['conda_env_name']}")
        
        # Test directory access
        if session_dir.exists():
            print("✅ Session directory accessible")
            
            # Test script exists
            test_script = session_dir / "scripts" / "test_env.py"
            if test_script.exists():
                print("✅ Test script found")
                
                # Run test script
                from ..code_tools.terminal_runner import TerminalRunner
                runner = TerminalRunner()
                result = runner.run_command(
                    f"python {test_script}", 
                    is_background=False, 
                    require_user_approval=False
                )
                print("📋 Test script output:")
                print(result)
            else:
                print("❌ Test script not found")
        else:
            print("❌ Session directory not accessible")
            
    except Exception as e:
        print(f"❌ Error testing session: {e}")
        return 1
    
    return 0


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(description='Session Management CLI for ThinkWithTool')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Create session command
    create_parser = subparsers.add_parser('create', help='Create a new session')
    create_parser.add_argument('--name', '-n', help='Session name (optional)')
    create_parser.set_defaults(func=create_session)
    
    # List sessions command
    list_parser = subparsers.add_parser('list', help='List all sessions')
    list_parser.set_defaults(func=list_sessions)
    
    # Cleanup sessions command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up sessions')
    cleanup_parser.add_argument('--session-id', '-s', help='Specific session ID to cleanup')
    cleanup_parser.add_argument('--keep', '-k', type=int, default=5, help='Number of sessions to keep (default: 5)')
    cleanup_parser.set_defaults(func=cleanup_session)
    
    # Session info command
    info_parser = subparsers.add_parser('info', help='Show session information')
    info_parser.add_argument('session_id', help='Session ID to show info for')
    info_parser.set_defaults(func=session_info)
    
    # Test session command
    test_parser = subparsers.add_parser('test', help='Test the current session environment')
    test_parser.set_defaults(func=test_session)
    
    # Parse arguments
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Execute command
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main()) 