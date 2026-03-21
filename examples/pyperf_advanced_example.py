"""Example: Advanced benchmark with GC metrics analysis.

This example demonstrates how to:
1. Run benchmarks with gc_monitor hook
2. Access GC metrics from pyperf metadata
3. Analyze GC impact on performance

Usage:
    # First, install gc-monitor in editable mode:
    pip install -e .

    # Run with gc_monitor hook and save results
    python examples/pyperf_advanced_example.py --hook=gc_monitor -o gc_benchmark.json

    # Analyze results with GC metrics
    python examples/analyze_gc_metrics.py gc_benchmark.json
"""

import gc
import sys
from typing import Any, Dict, List

import pyperf


def create_garbage(num_objects: int) -> int:
    """Create and discard objects to trigger GC."""
    total = 0
    for _ in range(num_objects):
        data = [x for x in range(100)]
        total += sum(data)
    return total


def with_gc_disabled() -> int:
    """Run operation with GC disabled."""
    gc.collect()
    gc.disable()
    try:
        return create_garbage(500)
    finally:
        gc.enable()


def with_gc_enabled() -> int:
    """Run operation with GC enabled."""
    return create_garbage(500)


def force_gc_between_runs() -> None:
    """Force GC before each run."""
    gc.collect()


def benchmark_with_metadata(
    runner: pyperf.Runner, name: str, func, setup_func=None
) -> None:
    """Run benchmark and print GC metadata."""
    bench = runner.bench_func(name, func, setup_func=setup_func)

    # Access metadata from the benchmark
    metadata = bench.get_metadata()

    # Print GC metrics if available
    gc_metrics = {k: v for k, v in metadata.items() if k.startswith("gc_")}
    if gc_metrics:
        print(f"\nGC Metrics for {name}:")
        for key, value in sorted(gc_metrics.items()):
            print(f"  {key}: {value}")
    else:
        print(f"\nNo GC metrics available for {name}")
        print("  (Run with --hook=gc_monitor to collect GC metrics)")


if __name__ == "__main__":
    runner = pyperf.Runner()

    print("=" * 60)
    print("GC Monitor Hook Example")
    print("=" * 60)

    # Benchmark 1: GC enabled (normal operation)
    benchmark_with_metadata(
        runner, "with_gc_enabled", with_gc_enabled, force_gc_between_runs
    )

    # Benchmark 2: GC disabled (should have no GC events during operation)
    benchmark_with_metadata(
        runner, "with_gc_disabled", with_gc_disabled, force_gc_between_runs
    )

    print("\n" + "=" * 60)
    print("To see GC metrics, run with:")
    print("  pyperf run --hook=gc_monitor examples/pyperf_advanced_example.py")
    print("=" * 60)
