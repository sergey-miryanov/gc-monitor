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


def _is_windows() -> bool:
    """Check if running on Windows."""
    return os.name == "nt"


def _send_signal_safe(
    process: subprocess.Popen[bytes],
    signal_value: int,
    verbose: bool,
    logger: logging.Logger,
    signal_name: str,
) -> None:
    """
    Send a signal to a process, catching and logging any errors.

    Args:
        process: The subprocess to send signal to
        signal_value: The signal to send
        verbose: If True, log detailed progress
        logger: Logger instance
        signal_name: Human-readable signal name for logging
    """
    try:
        if verbose:
            logger.debug("Sending %s to process: %s", signal_name, process)
        process.send_signal(signal_value)
    except (ProcessLookupError, OSError) as e:
        logger.warning("Failed to send %s to process: %s", signal_name, e)


def _communicate_with_timeout(
    process: subprocess.Popen[bytes],
    timeout: float | None,
    verbose: bool,
    logger: logging.Logger,
    timeout_description: str,
) -> tuple[bytes, bytes]:
    """
    Call process.communicate() with timeout, handling TimeoutExpired.

    Args:
        process: The subprocess to communicate with
        timeout: Timeout in seconds, or None for indefinite wait
        verbose: If True, log detailed progress
        logger: Logger instance
        timeout_description: Description of this timeout stage for logging

    Returns:
        Tuple of (stdout_data, stderr_data), or (b"", b"") on timeout
    """
    try:
        if verbose:
            logger.debug(
                "Waiting for process to exit (%s, timeout=%s)",
                timeout_description,
                timeout if timeout is not None else "indefinite",
            )
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if verbose:
            logger.debug(
                "Process did not exit within %s timeout", timeout_description
            )
        return b"", b""


def terminate_process(
    process: subprocess.Popen[bytes],
    verbose: bool = False,
    logger: logging.Logger | None = None,
    graceful_timeout: float = DEFAULT_GRACEFUL_TIMEOUT,
    force_timeout: float = DEFAULT_FORCE_TIMEOUT,
) -> tuple[bytes, bytes]:
    """
    Gracefully terminate a subprocess with escalating signals.

    Signal escalation flow:
    1. Send graceful signal (SIGINT on Unix, CTRL_BREAK_EVENT on Windows)
    2. Wait for graceful_timeout
    3. On timeout:
       - Unix: Send SIGTERM, wait, then SIGKILL via kill()
       - Windows: Call kill() directly
    4. Final wait (indefinite if needed) to prevent zombie processes

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
    is_windows = _is_windows()

    # Step 1: Send graceful shutdown signal
    if is_windows:
        _send_signal_safe(
            process=process,
            signal_value=signal.CTRL_BREAK_EVENT,
            verbose=verbose,
            logger=log,
            signal_name="CTRL_BREAK_EVENT",
        )
    else:
        _send_signal_safe(
            process=process,
            signal_value=signal.SIGINT,
            verbose=verbose,
            logger=log,
            signal_name="SIGINT",
        )

    # Step 2: Wait for graceful exit
    stdout_data, stderr_data = _communicate_with_timeout(
        process=process,
        timeout=graceful_timeout,
        verbose=verbose,
        logger=log,
        timeout_description="graceful shutdown",
    )

    # Check if process exited gracefully
    if process.returncode is not None:
        return stdout_data, stderr_data

    # Step 3: Process still running - escalate
    if verbose:
        log.debug(
            "Process did not exit gracefully, escalating to forceful termination"
        )

    if not is_windows:
        # Unix: SIGTERM -> wait -> SIGKILL
        _send_signal_safe(
            process=process,
            signal_value=signal.SIGTERM,
            verbose=verbose,
            logger=log,
            signal_name="SIGTERM",
        )

        stdout_data, stderr_data = _communicate_with_timeout(
            process=process,
            timeout=force_timeout,
            verbose=verbose,
            logger=log,
            timeout_description="SIGTERM",
        )

        # Still running? Use SIGKILL (Unix only)
        if process.returncode is None:
            _send_signal_safe(
                process=process,
                signal_value=getattr(signal, "SIGKILL", 9),
                verbose=verbose,
                logger=log,
                signal_name="SIGKILL",
            )
    else:
        # Windows: kill() directly
        if verbose:
            log.debug("Killing process: %s", process)
        try:
            process.kill()
        except (ProcessLookupError, OSError) as e:
            log.warning("Failed to kill process: %s", e)

    # Step 4: Final wait to reap process (prevent zombies)
    # First try with timeout
    stdout_data, stderr_data = _communicate_with_timeout(
        process=process,
        timeout=force_timeout,
        verbose=verbose,
        logger=log,
        timeout_description="final cleanup",
    )

    # If still running (shouldn't happen), wait indefinitely
    if process.returncode is None:
        if verbose:
            log.debug(
                "Process still running after forceful termination, waiting indefinitely"
            )
        stdout_data, stderr_data = _communicate_with_timeout(
            process=process,
            timeout=None,
            verbose=verbose,
            logger=log,
            timeout_description="indefinite cleanup",
        )

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
