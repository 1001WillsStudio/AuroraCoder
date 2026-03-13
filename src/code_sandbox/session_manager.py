import os
import uuid
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
import json
import logging
import sys

logger = logging.getLogger(__name__)

class SessionManager:
    """Manages isolated conda environments and working directories per session"""
    
    # Default name of the persistent sandbox base environment that new sessions will clone
    DEFAULT_BASE_ENV_NAME = "thinktool_sandbox_base"
    
    def __init__(self, base_sessions_dir: Optional[Path] = None, base_env_name: Optional[str] = None):
        self.base_sessions_dir = base_sessions_dir or Path.home() / ".thinktool_sessions"
        self.base_sessions_dir.mkdir(exist_ok=True)
        
        # Base environment name to clone from (can be overridden per session)
        self.default_base_env_name = base_env_name or self.DEFAULT_BASE_ENV_NAME
        
        # Current session info
        self.session_id: Optional[str] = None
        self.session_dir: Optional[Path] = None
        self.conda_env_name: Optional[str] = None
        self.base_env_name: Optional[str] = None  # The actual base env used for this session
        self.session_info: Dict[str, Any] = {}
        
        # Optional override: when set, all tools and the shell use this as cwd
        # instead of session_dir (e.g. a shared workspace).
        self.working_directory_override: Optional[Path] = None

        # For persistent terminal
        self.persistent_shell: Optional[subprocess.Popen] = None
        
    def create_session(self, session_name: Optional[str] = None,
                       base_env_name: Optional[str] = None,
                       reuse_env: bool = False) -> Dict[str, Any]:
        """Create a new session with conda environment and working directory.
        
        Args:
            session_name: Optional name for the session
            base_env_name: Optional conda environment name to clone from. 
                          If not provided, uses self.default_base_env_name.
                          If that's also not set, falls back to DEFAULT_BASE_ENV_NAME.
            reuse_env: If True, use base_env_name directly instead of cloning.
                       Faster startup and avoids path/config issues in cloned envs.
        """
        
        # Generate session ID and name
        self.session_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if session_name:
            session_name = session_name.replace(" ", "_")
            full_session_name = f"{session_name}_{timestamp}_{self.session_id}"
        else:
            full_session_name = f"session_{timestamp}_{self.session_id}"
        
        # Create session directory
        self.session_dir = self.base_sessions_dir / full_session_name
        self.session_dir.mkdir(exist_ok=True)
        
        # Determine which base environment to use
        self.base_env_name = base_env_name or self.default_base_env_name
        self.conda_env_name = self.base_env_name if reuse_env else f"thinktool_{self.session_id}"
        
        # Session info
        self.session_info = {
            "session_id": self.session_id,
            "session_name": full_session_name,
            "conda_env_name": self.conda_env_name,
            "base_env_name": self.base_env_name,
            "reuse_env": reuse_env,
            "session_dir": str(self.session_dir),
            "created_at": datetime.now().isoformat(),
            "status": "initializing"
        }
        
        try:
            if reuse_env:
                logger.info(f"Reusing conda environment: {self.conda_env_name}")
            else:
                logger.info(f"Creating conda environment: {self.conda_env_name}")
                self._create_conda_environment()
            
            # Create session workspace
            self._setup_session_workspace()
            
            # Save session info
            self._save_session_info()
            
            self.session_info["status"] = "active"
            logger.info(f"Session created successfully: {full_session_name}")

            # Start persistent shell and activate environment
            self._start_persistent_shell()
            
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            self.session_info["status"] = "failed"
            self.session_info["error"] = str(e)
            
        return self.session_info
    
    def _create_conda_environment(self):
        """Create a new conda environment for this session by cloning the base environment."""
        # Verify that conda is available
        use_shell = sys.platform == "win32"
        try:
            subprocess.run(["conda", "--version"], check=True, capture_output=True, shell=use_shell)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("Conda is not installed or not in PATH")

        # Check if the specified base environment exists
        if not self._check_env_exists(self.base_env_name):
            # If the specified base env doesn't exist and it's not the default, raise an error
            if self.base_env_name != self.DEFAULT_BASE_ENV_NAME:
                raise RuntimeError(
                    f"Specified base conda environment '{self.base_env_name}' does not exist. "
                    f"Please create it first or use an existing environment."
                )
            # If it's the default base env, create it
            self._ensure_base_environment()

        # Clone the base environment for this session
        cmd = ["conda", "create", "--name", self.conda_env_name, "--clone", self.base_env_name, "-y"]
        result = subprocess.run(cmd, capture_output=True, text=True, shell=use_shell)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone base conda environment '{self.base_env_name}': {result.stderr}")
    
    def _install_session_packages(self):
        """Install the necessary packages in the session conda environment (wrapper)."""
        self._install_packages_in_env(self.conda_env_name)

    def _check_env_exists(self, env_name: str) -> bool:
        """Check if a conda environment exists."""
        use_shell = sys.platform == "win32"
        check_cmd = ["conda", "list", "-n", env_name]
        check_result = subprocess.run(check_cmd, capture_output=True, text=True, shell=use_shell)
        return check_result.returncode == 0

    def _ensure_base_environment(self):
        """Ensure the default sandbox base environment exists; create it if it does not."""
        use_shell = sys.platform == "win32"

        # Check if the base environment already exists
        if self._check_env_exists(self.DEFAULT_BASE_ENV_NAME):
            return  # Base environment found

        logger.info(f"Base conda environment '{self.DEFAULT_BASE_ENV_NAME}' not found. Creating it...")

        # Create the base environment
        create_cmd = ["conda", "create", "--name", self.DEFAULT_BASE_ENV_NAME, "python=3.11", "-y"]
        create_result = subprocess.run(create_cmd, capture_output=True, text=True, shell=use_shell)
        if create_result.returncode != 0:
            raise RuntimeError(f"Failed to create base conda environment: {create_result.stderr}")

        # Install essential packages into the base environment
        self._install_packages_in_env(self.DEFAULT_BASE_ENV_NAME)

    def _install_packages_in_env(self, env_name: str):
        """Install essential packages into the specified conda environment."""
        essential_packages = ["pip", "numpy", "pandas", "requests"]

        for package in essential_packages:
            try:
                cmd = ["conda", "install", "-n", env_name, package, "-y"]
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                use_shell = sys.platform == "win32"
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, shell=use_shell)
                if result.returncode != 0:
                    logger.warning(f"Failed to install {package} in {env_name}: {result.stderr}")
            except Exception as e:
                logger.warning(f"Error installing conda package {package} in {env_name}: {str(e)}")

    def _start_persistent_shell(self):
        """Starts a persistent shell and activates the conda environment.
        
        If a shell is already running, it will be terminated first to ensure
        a fresh shell with the correct environment for the current session.
        """
        if self.persistent_shell:
            logger.info("Terminating existing persistent shell before starting a new one...")
            try:
                self.persistent_shell.terminate()
                self.persistent_shell.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Could not terminate existing shell cleanly, killing it. Error: {e}")
                try:
                    self.persistent_shell.kill()
                except Exception:
                    pass
            self.persistent_shell = None

        if sys.platform == "win32":
            shell_cmd = ["cmd.exe", "/D"]
        else:
            shell_cmd = ["bash", "-i"]

        try:
            self.persistent_shell = subprocess.Popen(
                shell_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.get_session_working_directory(),
                shell=False,
                bufsize=1,
                universal_newlines=True
            )
            
            activation_cmd = self.activate_session_in_shell()
            if activation_cmd:
                self._run_init_command(activation_cmd)
            logger.info(f"Persistent shell started and environment '{self.conda_env_name}' activated.")
            
        except Exception as e:
            logger.error(f"Failed to start persistent shell: {e}")
            self.persistent_shell = None

    def _run_init_command(self, command: str):
        """Run a shell-state-changing command (e.g. conda activate) without output capture.

        Uses a boundary on stdout purely for synchronization.  Output is
        discarded — this is only for commands whose side-effects on the shell
        environment matter (PATH changes, working directory, etc.).
        """
        if not self.persistent_shell or not command:
            return
        boundary = f"INIT_{uuid.uuid4().hex[:8]}"
        self.persistent_shell.stdin.write(f"{command}\necho {boundary}\n")
        self.persistent_shell.stdin.flush()
        while True:
            line = self.persistent_shell.stdout.readline()
            if not line or boundary in line:
                break

    def run_in_persistent_shell(self, command: str, timeout: int = 120) -> Tuple[str, str]:
        """Runs a command in the persistent shell and returns (stdout, exit_code).

        Output is redirected to a temp file so it is completely free of
        prompt noise and command echo.  A boundary marker echoed to stdout
        is used only for synchronization.  A background thread reads stdout
        so the call can be cleanly timed out.
        """
        if not self.persistent_shell:
            return "", "Persistent shell not running."

        cmd_id = uuid.uuid4().hex[:8]
        out_file = os.path.join(tempfile.gettempdir(), f"shell_out_{cmd_id}.txt")
        boundary = f"END_{cmd_id}"

        if sys.platform == "win32":
            wrapped = (
                f'({command}) > "{out_file}" 2>&1\n'
                f'echo {boundary}\n'
            )
        else:
            wrapped = (
                f'{{ {command}; }} > "{out_file}" 2>&1\n'
                f'echo {boundary}\n'
            )

        try:
            self.persistent_shell.stdin.write(wrapped)
            self.persistent_shell.stdin.flush()
        except Exception as e:
            return "", f"Failed to write to shell: {e}"

        found = threading.Event()

        def _wait_for_boundary():
            while True:
                line = self.persistent_shell.stdout.readline()
                if not line or boundary in line:
                    found.set()
                    return

        reader = threading.Thread(target=_wait_for_boundary, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        if not found.is_set():
            try:
                os.remove(out_file)
            except OSError:
                pass
            return "", f"Command timed out after {timeout}s"

        stdout = ""
        try:
            with open(out_file, "r", encoding="utf-8", errors="replace") as f:
                stdout = f.read()
        except FileNotFoundError:
            pass
        except Exception as e:
            stdout = f"[Error reading command output: {e}]"
        finally:
            try:
                os.remove(out_file)
            except OSError:
                pass

        return stdout, ""

    def restart_persistent_shell(self) -> str:
        """Terminates the current shell and starts a new one."""
        logger.info("Restarting persistent shell...")
        # Terminate the existing shell
        if self.persistent_shell:
            try:
                self.persistent_shell.terminate()
                self.persistent_shell.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Could not terminate existing shell cleanly, killing it. Error: {e}")
                self.persistent_shell.kill()
            self.persistent_shell = None
        
        # Start a new one
        self._start_persistent_shell()
        return "Persistent shell has been restarted."

    def _setup_session_workspace(self):
        """Set up the session workspace, leaving it empty."""
        pass
    
    def _save_session_info(self):
        """Save session information to a JSON file"""
        session_file = self.session_dir / "session_info.json"
        with open(session_file, 'w') as f:
            json.dump(self.session_info, f, indent=2)
    
    def get_session_command_prefix(self) -> list:
        """Get the command prefix for running commands in the session environment"""
        if not self.conda_env_name:
            return []
        return ["conda", "run", "-n", self.conda_env_name]
    
    def get_session_working_directory(self) -> Path:
        """Get the session working directory (respects working_directory_override)."""
        if self.working_directory_override:
            return self.working_directory_override
        return self.session_dir or Path.cwd()
    
    def activate_session_in_shell(self) -> str:
        """Get the shell command to activate the session environment"""
        if not self.conda_env_name:
            return ""
        if sys.platform == "win32":
            # On Windows cmd.exe, conda activate is available via condabin on PATH
            return f"conda activate {self.conda_env_name}"
        # On Unix we have to make sure the shell knows about 'conda' before we try to activate
        return (
            f"source $(conda info --base)/etc/profile.d/conda.sh && "
            f"conda activate {self.conda_env_name}"
        )
    
    def cleanup_session(self):
        """Clean up the session (remove conda environment and optionally files)"""
        if not self.session_id:
            return
        
        try:
            # Remove conda environment (skip if reusing a shared env)
            if self.conda_env_name and not self.session_info.get("reuse_env"):
                logger.info(f"Removing conda environment: {self.conda_env_name}")
                cmd = ["conda", "env", "remove", "-n", self.conda_env_name, "-y"]
                
                use_shell = sys.platform == "win32"
                result = subprocess.run(cmd, capture_output=True, text=True, shell=use_shell)
                if result.returncode != 0:
                    logger.warning(f"Failed to remove conda environment: {result.stderr}")
            elif self.session_info.get("reuse_env"):
                logger.info(f"Skipping env removal (shared env: {self.conda_env_name})")
            
            # Mark session as cleaned up
            if self.session_dir and self.session_dir.exists():
                self.session_info["status"] = "cleaned_up"
                self.session_info["cleaned_up_at"] = datetime.now().isoformat()
                self._save_session_info()

            # Terminate the persistent shell
            if self.persistent_shell:
                self.persistent_shell.terminate()
                self.persistent_shell = None
                
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
    
    def list_sessions(self, include_loadable_only: bool = False) -> list:
        """List all available sessions.
        
        Args:
            include_loadable_only: If True, only return sessions that can be loaded
                                  (i.e., their conda environment still exists)
        """
        sessions = []
        for session_path in self.base_sessions_dir.iterdir():
            if session_path.is_dir():
                session_info_file = session_path / "session_info.json"
                if session_info_file.exists():
                    try:
                        with open(session_info_file, 'r') as f:
                            session_info = json.load(f)
                        
                        # Check if the conda environment still exists
                        conda_env_name = session_info.get("conda_env_name")
                        env_exists = self._check_env_exists(conda_env_name) if conda_env_name else False
                        session_info["env_exists"] = env_exists
                        session_info["loadable"] = env_exists and session_info.get("status") != "cleaned_up"
                        
                        if include_loadable_only and not session_info["loadable"]:
                            continue
                            
                        sessions.append(session_info)
                    except Exception as e:
                        logger.warning(f"Failed to read session info for {session_path}: {e}")
        
        return sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)
    
    def load_session(self, session_id: Optional[str] = None, session_name: Optional[str] = None) -> Dict[str, Any]:
        """Load an existing session and continue working in it.
        
        Args:
            session_id: The session ID to load (e.g., "abc12345")
            session_name: The full session name to load (e.g., "my_session_20240101_120000_abc12345")
            
        Returns:
            Dictionary with session information
            
        Note: Either session_id or session_name must be provided.
        """
        if not session_id and not session_name:
            return {
                "status": "failed",
                "error": "Either session_id or session_name must be provided"
            }
        
        # Find the session directory
        session_dir = None
        session_info = None
        
        for path in self.base_sessions_dir.iterdir():
            if not path.is_dir():
                continue
                
            session_info_file = path / "session_info.json"
            if not session_info_file.exists():
                continue
                
            try:
                with open(session_info_file, 'r') as f:
                    info = json.load(f)
                
                # Match by session_id or session_name
                if session_id and info.get("session_id") == session_id:
                    session_dir = path
                    session_info = info
                    break
                elif session_name and info.get("session_name") == session_name:
                    session_dir = path
                    session_info = info
                    break
                    
            except Exception as e:
                logger.warning(f"Failed to read session info for {path}: {e}")
                continue
        
        if not session_dir or not session_info:
            return {
                "status": "failed",
                "error": f"Session not found: {session_id or session_name}"
            }
        
        # Verify the conda environment still exists
        conda_env_name = session_info.get("conda_env_name")
        if not conda_env_name or not self._check_env_exists(conda_env_name):
            return {
                "status": "failed",
                "error": f"Conda environment '{conda_env_name}' no longer exists. Session cannot be loaded."
            }
        
        # Check if session was cleaned up
        if session_info.get("status") == "cleaned_up":
            return {
                "status": "failed", 
                "error": "Session was already cleaned up and cannot be loaded."
            }
        
        # Clean up any existing session first
        if self.session_id:
            logger.info(f"Cleaning up current session before loading: {self.session_id}")
            if self.persistent_shell:
                try:
                    self.persistent_shell.terminate()
                    self.persistent_shell.wait(timeout=5)
                except Exception:
                    pass
                self.persistent_shell = None
        
        # Set session state
        self.session_id = session_info.get("session_id")
        self.session_dir = session_dir
        self.conda_env_name = conda_env_name
        self.base_env_name = session_info.get("base_env_name")
        self.session_info = session_info.copy()
        
        # Update session info
        self.session_info["status"] = "active"
        self.session_info["loaded_at"] = datetime.now().isoformat()
        
        try:
            # Start persistent shell and activate environment
            self._start_persistent_shell()
            
            # Save updated session info
            self._save_session_info()
            
            logger.info(f"Session loaded successfully: {session_info.get('session_name')}")
            
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            self.session_info["status"] = "failed"
            self.session_info["error"] = str(e)
        
        return self.session_info
    
    def cleanup_old_sessions(self, max_sessions: int = 10):
        """Clean up old sessions, keeping only the most recent ones"""
        sessions = self.list_sessions()
        
        if len(sessions) > max_sessions:
            sessions_to_remove = sessions[max_sessions:]
            
            for session_info in sessions_to_remove:
                try:
                    # Remove conda environment (skip if it was a shared/reused env)
                    conda_env_name = session_info.get("conda_env_name")
                    if conda_env_name and not session_info.get("reuse_env"):
                        cmd = ["conda", "env", "remove", "-n", conda_env_name, "-y"]
                        use_shell = sys.platform == "win32"
                        subprocess.run(cmd, capture_output=True, text=True, shell=use_shell)
                    
                    # Remove session directory
                    session_dir = Path(session_info["session_dir"])
                    if session_dir.exists():
                        shutil.rmtree(session_dir)
                        logger.info(f"Removed old session: {session_info['session_name']}")
                        
                except Exception as e:
                    logger.warning(f"Failed to cleanup session {session_info['session_name']}: {e}")

    def get_shell_setup_command(self) -> str:
        """Return a shell command that switches to the session dir and activates the env.

        The caller (e.g. higher-level agent) can run this once with run_terminal_cmd. After
        that, every subsequent run_terminal_cmd call will already execute in the correct
        working directory and environment (Option 3 behaviour).
        """
        if not self.session_dir or not self.conda_env_name:
            raise RuntimeError("Session has not been fully initialized yet")

        # Build the command in a cross-platform way
        if sys.platform == "win32":
            activate_cmd = f"conda activate {self.conda_env_name}"
            cd_cmd = f"cd \"{self.session_dir}\""
            return f"{cd_cmd} && {activate_cmd}"
        else:
            activate_cmd = f"source activate {self.conda_env_name}"
            cd_cmd = f"cd \"{self.session_dir}\""
            return f"{cd_cmd} && {activate_cmd}"

    def get_conda_env_python_path(self) -> Optional[Path]:
        """Get the Python executable path for the current session's conda environment.
        
        Returns:
            Path to the Python executable, or None if session is not initialized.
        """
        if not self.conda_env_name:
            return None
        
        use_shell = sys.platform == "win32"
        try:
            # Get conda info to find the envs directory
            result = subprocess.run(
                ["conda", "info", "--json"],
                capture_output=True,
                text=True,
                shell=use_shell
            )
            if result.returncode != 0:
                logger.warning(f"Failed to get conda info: {result.stderr}")
                return None
            
            import json as json_module
            conda_info = json_module.loads(result.stdout)
            
            # Look for the environment in envs_dirs
            for env_dir in conda_info.get("envs_dirs", []):
                env_path = Path(env_dir) / self.conda_env_name
                if sys.platform == "win32":
                    python_path = env_path / "python.exe"
                else:
                    python_path = env_path / "bin" / "python"
                
                if python_path.exists():
                    return python_path
            
            # Fallback: check conda prefix
            conda_prefix = conda_info.get("conda_prefix")
            if conda_prefix:
                envs_path = Path(conda_prefix) / "envs" / self.conda_env_name
                if sys.platform == "win32":
                    python_path = envs_path / "python.exe"
                else:
                    python_path = envs_path / "bin" / "python"
                
                if python_path.exists():
                    return python_path
            
            return None
            
        except Exception as e:
            logger.warning(f"Error getting conda env Python path: {e}")
            return None

    def get_conda_env_path(self) -> Optional[Path]:
        """Get the path to the current session's conda environment directory.
        
        Returns:
            Path to the conda environment directory, or None if session is not initialized.
        """
        if not self.conda_env_name:
            return None
        
        use_shell = sys.platform == "win32"
        try:
            result = subprocess.run(
                ["conda", "info", "--json"],
                capture_output=True,
                text=True,
                shell=use_shell
            )
            if result.returncode != 0:
                return None
            
            import json as json_module
            conda_info = json_module.loads(result.stdout)
            
            # Look for the environment in envs_dirs
            for env_dir in conda_info.get("envs_dirs", []):
                env_path = Path(env_dir) / self.conda_env_name
                if env_path.exists():
                    return env_path
            
            # Fallback: check conda prefix
            conda_prefix = conda_info.get("conda_prefix")
            if conda_prefix:
                envs_path = Path(conda_prefix) / "envs" / self.conda_env_name
                if envs_path.exists():
                    return envs_path
            
            return None
            
        except Exception as e:
            logger.warning(f"Error getting conda env path: {e}")
            return None

    def set_default_base_env(self, base_env_name: str):
        """Set the default base environment name for future sessions.
        
        Args:
            base_env_name: Name of the conda environment to clone from
        """
        self.default_base_env_name = base_env_name

# Global session manager instance
session_manager = SessionManager() 