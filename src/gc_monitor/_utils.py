"""Internal utility functions for gc-monitor."""

import subprocess
import time

__all__ = ["wait_for_process_ready"]


def wait_for_process_ready(process: subprocess.Popen[bytes], timeout: float = 0.2) -> bool:
    """Wait for a subprocess to be ready by polling.
    
    Checks if the process exits immediately (e.g., syntax errors, import failures).
    Returns as soon as we detect the process has exited, or after timeout if running.
    
    Args:
        process: subprocess.Popen instance to wait for
        timeout: Maximum time to wait in seconds (default: 0.2)
        
    Returns:
        True if process is still running after timeout, False if it exited
    """
    # Check immediately
    if process.poll() is not None:
        return False
        
    # Wait up to timeout, checking periodically
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(0.02)
        if process.poll() is not None:
            return False
    
    # Process still running after timeout
    return True
