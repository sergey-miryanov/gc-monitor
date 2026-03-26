"""Process termination utilities for graceful subprocess shutdown.

This module provides cross-platform process termination functionality
with escalating signals and timeout handling.
"""

import logging
import os
import signal
import subprocess

__all__ = ["terminate_process", "log_process_output"]

# Timeout constants
DEFAULT_GRACEFUL_TIMEOUT = 5.0  # seconds: timeout for graceful shutdown
DEFAULT_FORCE_TIMEOUT = 2.0  # seconds: timeout for forceful termination

_logger = logging.getLogger("gc_monitor")


def terminate_process(
    process: subprocess.Popen[bytes],
    verbose: bool = False,
    logger: logging.Logger | None = None,
    graceful_timeout: float = DEFAULT_GRACEFUL_TIMEOUT,
    force_timeout: float = DEFAULT_FORCE_TIMEOUT,
) -> tuple[bytes, bytes]:
    """
    Gracefully terminate a subprocess with escalating signals.

    Sends SIGINT (or CTRL_BREAK_EVENT on Windows) to the process for graceful
    shutdown. If the process does not exit within the graceful timeout, escalates
    to SIGTERM (Unix) or kill() (Windows). As a last resort, uses SIGKILL (Unix)
    or kill() (Windows) to forcefully terminate the process.

    All exceptions from signal operations are caught and logged internally.
    The function always returns normally with whatever output could be collected.

    Args:
        process: The subprocess to terminate
        verbose: If True, log detailed progress
        logger: Logger instance (uses default if None)
        graceful_timeout: Timeout for graceful shutdown in seconds
            (default: 5.0)
        force_timeout: Timeout for forceful termination in seconds
            (default: 2.0)

    Returns:
        Tuple of (stdout_data, stderr_data) from the process

    Note:
        Exceptions from signal operations are logged but not raised.
        The function always returns normally.
    """
    log = logger if logger is not None else _logger

    stdout_data: bytes = b""
    stderr_data: bytes = b""

    # Send SIGINT for graceful shutdown
    try:
        if os.name == "nt":
            # Windows: Use CTRL_BREAK_EVENT for console processes
            if verbose:
                log.debug("Sending CTRL_BREAK_EVENT to process: %s", process)
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            # Unix: SIGINT
            if verbose:
                log.debug("Sending SIGINT to process: %s", process)
            process.send_signal(signal.SIGINT)
    except (ProcessLookupError, OSError) as e:
        log.warning("Failed to send SIGINT to process: %s", e)

    # Wait for clean exit and read output atomically
    try:
        if verbose:
            log.debug(
                "Waiting for process to exit (timeout=%ss)", graceful_timeout
            )
        stdout_data, stderr_data = process.communicate(timeout=graceful_timeout)
    except subprocess.TimeoutExpired:
        # Fallback to SIGTERM, then SIGKILL
        if verbose:
            log.debug(
                "Process did not exit gracefully, escalating to SIGTERM/SIGKILL"
            )

        if os.name != "nt":
            # Unix: SIGTERM then SIGKILL
            try:
                process.send_signal(signal.SIGTERM)
            except (ProcessLookupError, OSError) as e:
                log.warning("Failed to send SIGTERM to process: %s", e)

            try:
                stdout_data, stderr_data = process.communicate(timeout=force_timeout)
            except subprocess.TimeoutExpired:
                # Final escalation to SIGKILL (Unix only)
                try:
                    process.kill()
                except (ProcessLookupError, OSError) as e:
                    log.warning("Failed to kill process: %s", e)

                # Final attempt to reap the process
                try:
                    stdout_data, stderr_data = process.communicate(
                        timeout=force_timeout
                    )
                except subprocess.TimeoutExpired:
                    # Last resort: wait indefinitely to prevent zombie process
                    stdout_data, stderr_data = process.communicate(timeout=None)
        else:
            # Windows: Use kill() which sends SIGKILL equivalent
            if verbose:
                log.debug("Killing process: %s", process)

            try:
                process.kill()
            except (ProcessLookupError, OSError) as e:
                log.warning("Failed to kill process: %s", e)

            # Final attempt to reap the process and read output
            try:
                stdout_data, stderr_data = process.communicate(timeout=force_timeout)
            except subprocess.TimeoutExpired:
                # Last resort: wait indefinitely to prevent zombie process
                stdout_data, stderr_data = process.communicate(timeout=None)

    return stdout_data, stderr_data


def log_process_output(
    process: subprocess.Popen[bytes],
    stdout_data: bytes,
    stderr_data: bytes,
    verbose: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    """
    Log process output based on exit code and verbose flag.

    When verbose mode is enabled, always logs output; otherwise only logs
    on non-zero exit codes.

    Args:
        process: The subprocess that was terminated
        stdout_data: Standard output data from the process
        stderr_data: Standard error data from the process
        verbose: If True, log detailed output regardless of exit code
        logger: Logger instance (uses default if None)
    """
    log = logger if logger is not None else logging.getLogger("gc_monitor")

    # Decode output
    stdout_str = stdout_data.decode("utf-8", errors="replace").strip()
    stderr_str = stderr_data.decode("utf-8", errors="replace").strip()

    # Check if process has exited
    if process.returncode is None:
        log.warning(
            "Process (PID %s) has not terminated - returncode is None",
            process.pid,
        )
        return

    # Log output based on exit code and verbose flag
    if process.returncode != 0:
        # Log with warning level on error
        if stdout_str:
            log.warning(
                "Process (PID %s) exited with code %s. stdout:\n%s",
                process.pid,
                process.returncode,
                stdout_str,
            )
        if stderr_str:
            log.warning(
                "Process (PID %s) exited with code %s. stderr:\n%s",
                process.pid,
                process.returncode,
                stderr_str,
            )
    elif verbose:
        # Log with debug level when verbose and exit code is 0
        if stdout_str:
            log.debug(
                "Process (PID %s) exited successfully. stdout:\n%s",
                process.pid,
                stdout_str,
            )
        if stderr_str:
            log.debug(
                "Process (PID %s) exited successfully. stderr:\n%s",
                process.pid,
                stderr_str,
            )
