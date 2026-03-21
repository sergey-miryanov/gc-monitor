"""Example: Simple benchmark to test gc_monitor pyperf hook.

This is a minimal example demonstrating how to run a pyperf benchmark
with the gc_monitor hook enabled.

Usage:
    # First, install gc-monitor in editable mode:
    pip install -e .

    # Run with gc_monitor hook
    python examples/pyperf_benchmark_example.py --hook=gc_monitor

    # Run with verbose output
    python examples/pyperf_benchmark_example.py --hook=gc_monitor -v

    # Run and save results
    python examples/pyperf_benchmark_example.py --hook=gc_monitor -o benchmark_results.json

    # Analyze GC metrics from saved results
    python examples/analyze_gc_metrics.py benchmark_results.json
"""

import pyperf


def gc_heavy_operation() -> None:
    """A simple operation that triggers GC."""
    # Create and discard many objects to trigger GC
    data = []
    for i in range(1000):
        data.append([x for x in range(100)])
        if len(data) > 100:
            data.pop(0)


def simple_operation() -> int:
    """A simple arithmetic operation."""
    result = 0
    for i in range(1000):
        result += i * i
    return result


def string_operation() -> str:
    """A simple string operation."""
    result = ""
    for i in range(100):
        result += f"item_{i}_"
    return result


if __name__ == "__main__":
    runner = pyperf.Runner()

    # Benchmark 1: GC-heavy operation (will generate GC events)
    runner.bench_func("gc_heavy_operation", gc_heavy_operation)

    # Benchmark 2: Simple arithmetic (minimal GC)
    runner.bench_func("simple_operation", simple_operation)

    # Benchmark 3: String operations (some GC)
    runner.bench_func("string_operation", string_operation)
