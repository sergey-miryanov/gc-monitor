# Programmatic Control

If you start your app with `gcmon run` or `gcmon monitor`, the control plane API
lets you programmatically start, stop, and annotate GC monitoring from within
your application.

## Import and Setup

```python
from gcmon.control.control_client import ControlClient

# Create a client — no address needed, auto-discovered from environment
client = ControlClient()
```

## Start/Stop Monitoring

Control when GC monitoring is active:

```python
# Skip monitoring during setup
client.stop_monitoring()
# ... setup code ...
client.start_monitoring()

# Now GC events are tracked
```

## Context Manager

Temporarily pause monitoring for a block of code:

```python
with client.pause_monitoring():
    # GC monitoring is paused here
    # ... code that shouldn't be monitored ...
# Monitoring automatically resumes
```

## Custom Instant Messages

Add application-specific markers to your trace:

```python
client.instant_msg("request_start")
# ... handle request ...
client.instant_msg("request_end")
```

These messages appear as instant events in the trace viewer, helping you
correlate GC activity with application behavior.

## When to Use

- **Skip setup/teardown**: Avoid monitoring during initialization or cleanup
  phases that aren't relevant to your analysis.
- **Focus on specific phases**: Monitor only the critical sections of your
  application (e.g., request handling, batch processing).
- **Correlate with application events**: Add custom markers to understand how GC
  pauses relate to specific operations (database queries, API calls, etc.).
- **Dynamic control**: Enable/disable monitoring based on runtime conditions
  (e.g., only monitor during peak load).

## Prerequisites

The control plane is only available if you start your app with `gcmon run` or
`gcmon monitor`. Standalone processes cannot use the control plane.
