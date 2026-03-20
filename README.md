# gc-monitor

A modern Python package for monitoring Python's garbage collector (GC) and exporting statistics in various formats.

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- **Real-time GC monitoring** - Track garbage collection events in running Python processes
- **Multiple export formats** - Chrome Trace Event format support with extensible exporter API
- **CLI and API** - Use via command-line or integrate into your own tools
- **Lightweight** - Minimal overhead background thread polling

## Installation

```bash
pip install -e .
```

## CLI Usage

The `gc-monitor` command provides a convenient way to monitor GC activity from the command line.

### Basic Usage

```bash
# Monitor a process until interrupted
gc-monitor --pid 12345

# Monitor with custom output file
gc-monitor --pid 12345 --output gc_trace.json

# Monitor for a specific duration with verbose output
gc-monitor --pid 12345 --duration 30 --verbose
```

### Command-Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `pid` (required) | Process ID to monitor | - |
| `-o, --output` | Output file path for trace data | `gc_trace.json` |
| `-r, --rate` | Polling rate in seconds | `0.1` |
| `-d, --duration` | Monitoring duration in seconds | Until interrupted |
| `-v, --verbose` | Enable verbose output | `False` |

### Example Commands

**Monitor a long-running process:**
```bash
gc-monitor --pid 12345 --output server_gc.json --verbose
```

**Quick 10-second sampling:**
```bash
gc-monitor --pid 12345 --duration 10 --rate 0.05
```

**High-frequency monitoring:**
```bash
gc-monitor --pid 12345 --output detailed_trace.json --rate 0.01
```

## API Usage

```python
from gc_monitor import TraceExporter, connect
import time

# Create exporter
exporter = TraceExporter(pid=12345, output_path=Path("trace.json"))

# Connect and start monitoring
monitor = connect(pid, exporter=exporter, rate=0.1)

# Monitor for some time
time.sleep(5)

# Stop and save
monitor.stop()
```

## Getting Started

1. **Install dependencies:**
   ```bash
   poetry install
   # Or using pip
   pip install -e .
   ```

2. **Run tests:**
   ```bash
   pytest
   ```

3. **Build the distribution:**
   ```bash
   python -m build
   ```

## Project Structure

- `src/gc_monitor/` - Package source
- `tests/` - Test suite
- `examples/` - Usage examples
- `pyproject.toml` - Packaging configuration
- `Makefile` - Build/test commands
- `README.md` - This file
- `LICENSE` - License

## License

MIT License - see [LICENSE](LICENSE) for details.
