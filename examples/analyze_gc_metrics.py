"""Analyze GC metrics from pyperf benchmark results.

Usage:
    python examples/analyze_gc_metrics.py benchmark_results.json
"""

import json
import sys
from pathlib import Path


def analyze_gc_metrics(results_path: Path) -> None:
    """Load and analyze GC metrics from pyperf results."""
    with open(results_path) as f:
        data = json.load(f)

    print("=" * 60)
    print(f"GC Metrics Analysis: {results_path}")
    print("=" * 60)

    # Navigate to benchmarks
    benchmarks = data.get("benchmarks", [])

    for bench in benchmarks:
        name = bench.get("name", "Unknown")
        metadata = bench.get("metadata", {})

        # Extract GC metrics
        gc_metrics = {k: v for k, v in metadata.items() if k.startswith("gc_")}

        if not gc_metrics:
            print(f"\n{name}: No GC metrics available")
            continue

        print(f"\n{name}:")
        print("-" * 40)

        # Group metrics by category
        collections = {
            k: v for k, v in gc_metrics.items() if "collections" in k
        }
        durations = {
            k: v for k, v in gc_metrics.items() if "duration" in k
        }
        heap = {k: v for k, v in gc_metrics.items() if "heap" in k}
        objects = {
            k: v for k, v in gc_metrics.items() if "object" in k
        }
        other = {
            k: v
            for k, v in gc_metrics.items()
            if k not in collections and k not in durations and k not in heap and k not in objects
        }

        if collections:
            print("  Collections:")
            for k, v in sorted(collections.items()):
                print(f"    {k}: {v}")

        if durations:
            print("  Durations (seconds):")
            for k, v in sorted(durations.items()):
                print(f"    {k}: {v:.6f}" if isinstance(v, float) else f"    {k}: {v}")

        if heap:
            print("  Heap (bytes):")
            for k, v in sorted(heap.items()):
                print(f"    {k}: {v:,}")

        if objects:
            print("  Objects:")
            for k, v in sorted(objects.items()):
                print(f"    {k}: {v:,}")

        if other:
            print("  Other:")
            for k, v in sorted(other.items()):
                print(f"    {k}: {v}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze_gc_metrics.py <benchmark_results.json>")
        sys.exit(1)

    results_path = Path(sys.argv[1])
    if not results_path.exists():
        print(f"Error: File not found: {results_path}")
        sys.exit(1)

    analyze_gc_metrics(results_path)
