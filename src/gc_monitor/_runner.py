"""Subprocess runner for executing Python modules/files with GC monitoring.

This module provides functionality to spawn Python subprocesses and manage
their lifecycle for GC monitoring integration.
"""

import os
import subprocess
import sys
from pathlib import Path

from ._process_terminator import log_process_output, terminate_process

__all__ = ["GCRunner"]


class GCRunner:
    """Runner for Python subprocesses with GC monitoring support.

    Spawns a Python subprocess running a target script or module,
    and provides lifecycle management (start, monitor, terminate).

    Example:
        ```python
        # Run a script
        runner = GCRunner("my_script.py", is_module=False, passthrough_args=["arg1", "arg2"])
        pid = runner.start()
        # ... monitor the process ...
        stdout, stderr = runner.terminate(verbose=True)

        # Run a module
        runner = GCRunner("http.server", is_module=True, passthrough_args=["8080"])
        pid = runner.start()
        # ... monitor the process ...
        ```
    """

    def __init__(
        self,
        target: str,
        is_module: bool = False,
        passthrough_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Initialize the GC runner.

        Args:
            target: Script path or module name to run
            is_module: If True, treat target as module name; if False, as file path
            passthrough_args: Arguments to pass to the target script/module
            env: Optional environment variable overrides (merged with os.environ)
        """
        self._target = target
        self._is_module = is_module
        self._passthrough_args = passthrough_args or []
        self._env = env
        self._process: subprocess.Popen[bytes] | None = None

    def _validate_target(self) -> None:
        """Validate the target before spawning.

        Raises:
            FileNotFoundError: If script file doesn't exist (script mode)
            ValueError: If target is invalid
        """
        if self._is_module:
            # Module mode: validate module name is not empty
            if not self._target.strip():
                raise ValueError("Module name cannot be empty")
        else:
            # Script mode: validate file exists and is readable
            script_path = Path(self._target)
            if not script_path.exists():
                raise FileNotFoundError(f"Script not found: {self._target}")
            if not script_path.is_file():
                raise ValueError(f"Target is not a file: {self._target}")

    def _build_command(self) -> list[str]:
        """Build the subprocess command line.

        Returns:
            List of command arguments for subprocess.Popen
        """
        cmd = [sys.executable]

        if self._is_module:
            # Module mode: python -m module_name [args...]
            cmd.append("-m")
            cmd.append(self._target)
        else:
            # Script mode: python script_path [args...]
            # Resolve to absolute path to ensure correct execution
            script_path = str(Path(self._target).resolve())
            cmd.append(script_path)

        # Add passthrough arguments
        cmd.extend(self._passthrough_args)

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build the environment for the subprocess.

        Returns:
            Environment dictionary for subprocess
        """
        # Start with current environment
        env = os.environ.copy()

        # Merge custom environment variables
        if self._env:
            env.update(self._env)

        return env

    def start(self) -> int:
        """Spawn the subprocess and return its PID.

        Returns:
            Process ID of spawned subprocess

        Raises:
            FileNotFoundError: If target script doesn't exist (script mode)
            ValueError: If target is invalid
            RuntimeError: If subprocess fails to start
        """
        # Validate target before spawning
        self._validate_target()

        # Build command and environment
        cmd = self._build_command()
        env = self._build_env()

        # Configure subprocess creation flags for cross-platform compatibility
        creationflags = 0
        if os.name == "nt":
            # Windows: Create new process group for proper signal handling
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                env=env,
            )
        except OSError as e:
            raise RuntimeError(f"Failed to start subprocess: {e}") from e

        # Small delay to ensure process initializes
        # This helps avoid race conditions when connecting GC monitoring
        import time
        time.sleep(0.1)

        # Check if process exited immediately (e.g., syntax error, module not found)
        # We only raise an error if there's stderr output indicating a problem
        if self._process.poll() is not None:
            # Process exited immediately, collect stderr for error message
            _, stderr_data = self._process.communicate()
            stderr_str = stderr_data.decode("utf-8", errors="replace").strip()
            # Only raise if there's actual error output
            if stderr_str and ("error" in stderr_str.lower() or "traceback" in stderr_str.lower()):
                raise RuntimeError(
                    f"Subprocess exited immediately. stderr: {stderr_str or '(no output)'}"
                )

        return self._process.pid

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        """Return the subprocess handle, or None if not started."""
        return self._process

    @property
    def pid(self) -> int | None:
        """Return the process ID, or None if not started."""
        return self._process.pid if self._process else None

    @property
    def is_running(self) -> bool:
        """Check if the subprocess is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    @property
    def returncode(self) -> int | None:
        """Return the subprocess exit code, or None if still running."""
        return self._process.returncode if self._process else None

    def terminate(
        self,
        verbose: bool = False,
        logger: object | None = None,
        graceful_timeout: float = 5.0,
        force_timeout: float = 2.0,
    ) -> tuple[bytes, bytes]:
        """Terminate the subprocess gracefully.

        Uses escalating signals for graceful shutdown:
        - Unix: SIGINT → SIGTERM → SIGKILL
        - Windows: CTRL_BREAK_EVENT → kill()

        Args:
            verbose: If True, log detailed progress
            logger: Logger instance (uses default if None)
            graceful_timeout: Timeout for graceful shutdown in seconds
            force_timeout: Timeout for forceful termination in seconds

        Returns:
            Tuple of (stdout_data, stderr_data) from the process
        """
        if self._process is None:
            return b"", b""

        # Use the shared terminate_process utility
        stdout_data, stderr_data = terminate_process(
            process=self._process,
            verbose=verbose,
            logger=logger,  # type: ignore[arg-type]
            graceful_timeout=graceful_timeout,
            force_timeout=force_timeout,
        )

        # Log process output
        log_process_output(
            process=self._process,
            stdout_data=stdout_data,
            stderr_data=stderr_data,
            verbose=verbose,
            logger=logger,  # type: ignore[arg-type]
        )

        return stdout_data, stderr_data

    def __enter__(self) -> "GCRunner":
        """Context manager entry: start the subprocess."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Context manager exit: terminate the subprocess."""
        if self._process is not None:
            self.terminate(verbose=False)
