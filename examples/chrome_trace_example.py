"""Example: Export GC monitoring data to Chrome Trace format."""

import time
from pathlib import Path

from gc_monitor import TraceExporter, connect


def main() -> None:
    """Run GC monitoring and export to Chrome Trace format."""
    pid = 12345  # Process ID to monitor
    output_file = Path("gc_trace.json")

    # Create exporter
    exporter = TraceExporter(pid=pid)

    # Connect and start monitoring with exporter
    monitor = connect(pid, exporter=exporter, rate=0.1)

    if monitor is None:
        print(f"Failed to connect to GC monitor for PID {pid}")
        return

    try:
        # Monitor for 5 seconds
        print(f"Monitoring PID {pid} for 5 seconds...")
        time.sleep(5)
    finally:
        # Stop monitoring and save trace
        monitor.stop(save_path=output_file)
        print(f"Trace saved to: {output_file}")
        print(f"Total events: {exporter.get_event_count()}")
        print("\nTo view in Chrome:")
        print("  1. Open chrome://tracing")
        print("  2. Click 'Load' and select gc_trace.json")


if __name__ == "__main__":
    main()
