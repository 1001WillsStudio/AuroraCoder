#!/usr/bin/env python3
"""
Complete Session Cleanup Script

This script provides comprehensive cleanup of all session environments and directories.
It should only be run when you want to completely remove all session data.

WARNING: This will permanently delete all session directories and conda environments!
"""

import sys
import subprocess
import shutil
import argparse
from pathlib import Path
import json
import logging
import os

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

try:
    from code_sandbox import SessionManager, get_session_status
except ImportError:
    print("❌ Could not import session modules. Make sure you're in the project root directory.")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def confirm_action(prompt: str) -> bool:
    """Ask user for confirmation"""
    while True:
        response = input(f"{prompt} (yes/no): ").lower().strip()
        if response in ['yes', 'y']:
            return True
        elif response in ['no', 'n']:
            return False
        else:
            print("Please enter 'yes' or 'no'")


def list_conda_environments() -> list:
    """List all conda environments that start with 'thinktool_'"""
    try:
        # Use shell=True for Windows compatibility
        use_shell = sys.platform == "win32"
        
        result = subprocess.run(
            ['conda', 'env', 'list', '--json'],
            capture_output=True,
            text=True,
            check=True,
            shell=use_shell
        )
        
        env_data = json.loads(result.stdout)
        thinktool_envs = []
        
        for env_path in env_data.get('envs', []):
            env_name = Path(env_path).name
            if env_name.startswith('thinktool_') and env_name != 'thinktool_sandbox_base':
                thinktool_envs.append(env_name)
                
        return thinktool_envs
        
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"Failed to list conda environments: {e}")
        return []


def remove_conda_environment(env_name: str) -> bool:
    """Remove a conda environment"""
    try:
        logger.info(f"Removing conda environment: {env_name}")
        
        # Use shell=True for Windows compatibility
        use_shell = sys.platform == "win32"
        
        result = subprocess.run(
            ['conda', 'env', 'remove', '-n', env_name, '-y'],
            capture_output=True,
            text=True,
            check=True,
            shell=use_shell
        )
        logger.info(f"Removed conda environment: {env_name}")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to remove conda environment {env_name}: {e.stderr}")
        return False


def remove_session_directories(base_dir: Path = None) -> bool:
    """Remove all session directories"""
    if base_dir is None:
        base_dir = Path.home() / ".thinktool_sessions"
    
    try:
        if base_dir.exists():
            logger.info(f"Removing session directories from: {base_dir}")
            
            # List directories before removal
            session_dirs = [d for d in base_dir.iterdir() if d.is_dir()]
            
            if session_dirs:
                logger.info(f"Found {len(session_dirs)} session directories:")
                for dir_path in session_dirs:
                    logger.info(f"  - {dir_path.name}")
                
                # Remove the entire base directory
                shutil.rmtree(base_dir)
                logger.info(f"Removed all session directories")
                return True
            else:
                logger.info("No session directories found")
                return True
        else:
            logger.info(f"Session directory does not exist: {base_dir}")
            return True
            
    except Exception as e:
        logger.error(f"❌ Failed to remove session directories: {e}")
        return False


def cleanup_all_sessions(force: bool = False, keep_recent: int = 0):
    """
    Complete cleanup of all sessions
    
    Args:
        force: Skip confirmation prompts
        keep_recent: Number of recent sessions to keep (0 = remove all)
    """
    print("Complete Session Cleanup")
    print("=" * 50)
    
    # Get session status first
    try:
        status_info = get_session_status()
        sessions = status_info.get('sessions', []) if status_info.get('status') == 'success' else []
    except Exception as e:
        logger.warning(f"Could not get session status: {e}")
        sessions = []
    
    # List conda environments
    conda_envs = list_conda_environments()
    
    # Show what will be cleaned up
    print(f"\nCurrent Status:")
    print(f"  - Session directories: {len(sessions)}")
    print(f"  - Conda environments: {len(conda_envs)}")
    
    if sessions:
        print(f"\nSession directories to be removed:")
        for i, session in enumerate(sessions):
            if keep_recent == 0 or i >= keep_recent:
                print(f"  - {session['session_name']} ({session.get('status', 'unknown')})")
    
    if conda_envs:
        print(f"\nConda environments to be removed:")
        for env_name in conda_envs:
            print(f"  - {env_name}")
    
    if not sessions and not conda_envs:
        print("\nNo sessions or environments found. Nothing to clean up.")
        return True
    
    # Confirmation
    if not force:
        print(f"\n⚠️  WARNING: This will permanently delete:")
        if sessions:
            print(f"   - {max(0, len(sessions) - keep_recent)} session directories and all their contents")
        if conda_envs:
            print(f"   - {len(conda_envs)} conda environments")
        print()
        
        if not confirm_action("Are you sure you want to proceed?"):
            print("❌ Operation cancelled by user")
            return False
    
    # Perform cleanup
    success = True
    
    # Remove conda environments
    if conda_envs:
        print(f"\n🧹 Removing conda environments...")
        for env_name in conda_envs:
            if not remove_conda_environment(env_name):
                success = False
    
    # Remove session directories
    if sessions and keep_recent < len(sessions):
        print(f"\n🧹 Removing session directories...")
        
        if keep_recent == 0:
            # Remove all sessions
            if not remove_session_directories():
                success = False
        else:
            # Remove old sessions, keep recent ones
            session_manager = SessionManager()
            try:
                session_manager.cleanup_old_sessions(keep_recent)
                logger.info(f"✅ Kept {keep_recent} most recent sessions")
            except Exception as e:
                logger.error(f"❌ Failed to cleanup old sessions: {e}")
                success = False
    
    # Summary
    print(f"\n📋 Cleanup Summary:")
    if success:
        print("✅ All cleanup operations completed successfully")
        if keep_recent > 0:
            print(f"📁 Kept {keep_recent} most recent sessions")
    else:
        print("⚠️  Some cleanup operations failed - check logs above")
    
    return success


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Complete cleanup of all session environments and directories',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cleanup_all_sessions.py                    # Interactive cleanup of everything
  python cleanup_all_sessions.py --force            # Non-interactive cleanup of everything
  python cleanup_all_sessions.py --keep-recent 3    # Keep 3 most recent sessions
  python cleanup_all_sessions.py --list-only        # Just show what would be cleaned up
        """
    )
    
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Skip confirmation prompts'
    )
    
    parser.add_argument(
        '--keep-recent', '-k',
        type=int,
        default=0,
        help='Number of recent sessions to keep (default: 0 = remove all)'
    )
    
    parser.add_argument(
        '--list-only', '-l',
        action='store_true',
        help='List what would be cleaned up without actually doing it'
    )
    
    args = parser.parse_args()
    
    if args.list_only:
        # Just show what would be cleaned up
        try:
            status_info = get_session_status()
            sessions = status_info.get('sessions', []) if status_info.get('status') == 'success' else []
        except Exception as e:
            sessions = []
        
        conda_envs = list_conda_environments()
        
        print("Items that would be cleaned up:")
        print(f"  - Session directories: {max(0, len(sessions) - args.keep_recent)}")
        print(f"  - Conda environments: {len(conda_envs)}")
        
        if sessions:
            print(f"\nSession directories:")
            for i, session in enumerate(sessions):
                if args.keep_recent == 0 or i >= args.keep_recent:
                    print(f"  - {session['session_name']}")
        
        if conda_envs:
            print(f"\nConda environments:")
            for env_name in conda_envs:
                print(f"  - {env_name}")
                
        return 0
    
    # Perform actual cleanup
    try:
        success = cleanup_all_sessions(force=args.force, keep_recent=args.keep_recent)
        return 0 if success else 1
        
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main()) 