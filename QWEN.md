# gc-monitor

A modern Python package for monitoring Python's garbage collector (GC) and exporting statistics in various formats.

## Project Overview

**Purpose:** Provides a monitoring infrastructure for Python's GC with support for multiple export formats (currently Chrome Trace Event format).

**Architecture:**
- **src layout** - Package code in `src/gc_monitor/`
- **Core components:**
  - `_gc_monitor.py` - Low-level API (`GCMonitorStatsItem`, `GCMonitorHandler`, `connect()`, `disconnect()`)
  - `core.py` - High-level monitoring (`GCMonitor` class, `connect()` convenience function)
  - `exporter.py` - Base exporter interface (`GCMonitorExporter`)
  - `chrome_trace_exporter.py` - Chrome Trace Event format exporter (`TraceExporter`)
- **Tests** - pytest-based test suite in `tests/`

**Key Design Patterns:**
- Handler pattern for GC data collection
- Exporter pattern for output format abstraction
- Background thread polling for continuous monitoring
- Thread-safe event collection with locking

## Building and Running

### Prerequisites
- Python 3.12+
- Poetry (for dependency management)

### Setup
```bash
# Install dependencies
poetry install

# Or using pip
pip install -e .
```

### CLI Entry Point

After installation, the `gc-monitor` command is available:

```bash
# Usage pattern
gc-monitor <pid> [options]

# Examples
gc-monitor 12345                           # Monitor PID 12345
gc-monitor 12345 -o trace.json             # Custom output file
gc-monitor 12345 -r 0.05 -d 30 -v          # High rate, 30s duration, verbose
```

### Commands (via Makefile)
```bash
make test           # Run pytest
make build          # Build distribution
make install        # Install in editable mode
make typecheck      # Run both pyright and mypy
make typecheck-pyright  # Run pyright only
make typecheck-mypy     # Run mypy only
```

### Manual Commands
```bash
# Run tests
pytest

# Type checking
poetry run pyright
poetry run mypy src/ tests/

# Build
python -m build

# Install editable
pip install -e .

# Run CLI
gc-monitor --pid 12345 --output gc_trace.json --verbose
```

## Development Conventions

### Type Checking
- **Strict mode** enforced for both pyright and mypy
- Python 3.12 target
- Tests have relaxed typing rules (`disallow_untyped_defs = false`)
- Key strict checks:
  - `reportAny = "warning"`
  - `reportUnknownParameterType = "error"`
  - `reportUnknownArgumentType = "error"`
  - `reportMissingTypeStubs = "warning"`

### Code Style
- Type annotations required for all function signatures
- Modern Python 3.12+ features preferred
- CRLF line endings (Windows-style)

### Testing Practices
- **TDD approach:** Write tests first, then implementation
- pytest framework with fixtures
- Tests in `tests/` directory
- `conftest.py` ensures `src/` is on `sys.path`
- Mock-based testing for handlers and exporters

### API Usage Pattern
```python
from gc_monitor import TraceExporter, connect

# Create exporter
exporter = TraceExporter(pid=12345, output_path=Path("trace.json"))

# Connect and start monitoring
monitor = connect(pid, exporter=exporter, rate=0.1)

# Monitor for some time
time.sleep(5)

# Stop and save
monitor.stop()
```

## Project Structure
```
gc-monitor/
├── src/gc_monitor/
│   ├── __init__.py              # Package exports
│   ├── _gc_monitor.py           # Low-level GC monitoring API
│   ├── core.py                  # GCMonitor class, connect()
│   ├── exporter.py              # GCMonitorExporter base class
│   └── chrome_trace_exporter.py # TraceExporter implementation
├── tests/
│   └── test_chrome_trace.py     # Chrome trace exporter tests
├── examples/
│   └── chrome_trace_example.py  # Usage example
├── pyproject.toml               # Poetry config, type checking
├── Makefile                     # Build/test commands
└── conftest.py                  # pytest configuration
```

## Notes
- CI tests on Python 3.14 and 3.15 (experimental)
- The `_gc_monitor` module currently uses mock/random data for demonstration
- Future implementations should use the `_gc_monitor` API, not Python's `gc` module directly

## Qwen Added Memories
- We should use `--basetemp=.temp` option when run tests.
- CODE_REVIEW.md is the living code review document for gc-monitor that tracks issues, fixes, and test coverage status
- **Windows Development:** Use PowerShell equivalents instead of Unix commands (e.g., `Get-Content -Head` instead of `head`, `Select-String` instead of `grep`, `Get-ChildItem` instead of `ls`)
