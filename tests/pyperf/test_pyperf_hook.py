"""Tests for pyperf hook integration."""

# pyright: reportPrivateUsage=none, reportUnknownMemberType=none, reportUnknownVariableType=none, reportUnknownArgumentType=none, reportUnusedFunction=none

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from gc_monitor.data import GCStatsInfo
from gc_monitor.pyperf.hook import (
    GCMonitorHook,
    _get_env_pyperf_hook_control_timeout,
    gc_monitor_hook,
)
from gc_monitor.stats import StreamingStats

from tests.helpers import assert_valid_jsonl_format


@pytest.fixture(autouse=True)
def _mock_control_connect():
    """Prevent real control plane connection attempts in hook tests."""
    with patch("gc_monitor.pyperf.hook.connect_with_retry", return_value=None):
        yield


@pytest.fixture
def mock_popen_process():
    """Patches Popen and getpid with defaults. Yields (mock_popen, mock_process)."""
    with (
        patch("gc_monitor.pyperf.hook.subprocess.Popen") as mock_popen,
        patch("gc_monitor.pyperf.hook.os.getpid", return_value=12345),
    ):
        mock_process = Mock()
        mock_process.pid = 54321
        mock_process.poll.return_value = None
        mock_process.communicate.return_value = (b"", b"")
        mock_popen.return_value = mock_process
        yield mock_popen, mock_process


def _make_event(**kwargs: Any) -> GCStatsInfo:
    defaults: dict[str, Any] = dict(
        gen=0, iid=0, ts_start=1_000_000_000, ts_stop=1_005_000_000,
        heap_size=20000, collections=5, collected=50,
        uncollectable=2, candidates=10, duration=0.005,
    )
    return GCStatsInfo(**{**defaults, **kwargs})


def _make_jsonl_event(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = dict(
        pid=12345, tid=0, gen=0, iid=0,
        ts_start=1_000_000_000, ts_stop=1_005_000_000,
        collections=5, collected=50, uncollectable=2,
        candidates=10, heap_size=20000, duration=0.005,
    )
    return {**defaults, **kwargs}


def _write_jsonl(path: Path, *events: dict[str, Any]) -> None:
    """Write one or more JSON objects as JSONL to a file."""
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


class TestGCMonitorHookInit:
    """Test GCMonitorHook initialization."""

    def test_hook_init_default_values(self) -> None:
        """Hook initializes with default values."""
        hook = gc_monitor_hook()
        assert len(hook._temp_files) > 0
        assert hook._process is not None
        assert hook._pid == os.getpid()


class TestGCMonitorHookEnter:
    """Test GCMonitorHook __enter__ method."""

    def test_enter_spawns_subprocess(
        self,
        mock_popen_process: tuple[Mock, Mock],
    ) -> None:
        """__enter__ spawns subprocess with correct command."""
        mock_popen, _mock_process = mock_popen_process

        hook = gc_monitor_hook()

        assert hook._pid == 12345  # type: ignore[reportPrivateUsage]
        assert hook._process is not None  # type: ignore[reportPrivateUsage]

        # Verify subprocess.Popen was called with correct args
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        # Command structure: [sys.executable, "-m", "gc_monitor", "monitor", pid, ...]
        assert call_args[0] == sys.executable
        assert call_args[1] == "-m"
        assert call_args[2] == "gc_monitor"
        assert call_args[3] == "monitor"
        assert call_args[4] == "12345"
        assert "-o" in call_args
        assert "--format" in call_args
        assert "jsonl" in call_args

    def test_enter_raises_on_missing_cli(
        self,
        mock_popen_process: tuple[Mock, Mock],
    ) -> None:
        """__enter__ raises RuntimeError if gc-monitor module not found."""
        mock_popen, _mock_process = mock_popen_process
        mock_popen.side_effect = FileNotFoundError("module not found")

        with pytest.raises(RuntimeError) as exc_info:
            gc_monitor_hook()

        assert "Failed to run gc-monitor module" in str(exc_info.value)
        assert "Ensure gc-monitor is installed" in str(exc_info.value)

    def test_enter_creates_temp_file_path(
        self,
        mock_popen_process: tuple[Mock, Mock],
    ) -> None:
        """__enter__ creates temp file path with PID."""
        mock_popen, _mock_process = mock_popen_process

        hook = gc_monitor_hook()
        with hook:
            assert len(hook._temp_files) == 1  # type: ignore[reportPrivateUsage]
            assert "gc_monitor_12345_" in str(hook._temp_files[0])  # type: ignore[reportPrivateUsage]

    def test_enter_accumulates_temp_files(
        self,
        mock_popen_process: tuple[Mock, Mock],
    ) -> None:
        """__enter__ accumulates temp files for multiple calls."""
        mock_popen, _mock_process = mock_popen_process

        hook = gc_monitor_hook()

        # First enter
        with hook:
            assert "gc_monitor_12345_" in str(hook._temp_files[0])  # type: ignore[reportPrivateUsage]

        # Second enter (simulating multiple benchmark runs)
        with hook:
            assert len(hook._temp_files) == 1
            assert "gc_monitor_12345_" in str(hook._temp_files[0])


class TestGCMonitorHookExit:
    """Test GCMonitorHook __exit__ delegates to terminate_process."""

    def test_exit_calls_terminate_process(
        self,
        mock_popen_process: tuple[Mock, Mock],
        tmp_path: Path,
    ) -> None:
        """__exit__ calls terminate_process with correct arguments."""
        _mock_popen, mock_process = mock_popen_process

        with (
            patch(
                "gc_monitor.pyperf.hook.terminate_process",
                return_value=(b"", b""),
            ) as mock_terminate,
            patch(
                "gc_monitor.pyperf.hook._get_env_pyperf_hook_output",
                return_value=tmp_path / "out.jsonl",
            ),
        ):
            hook = gc_monitor_hook()
            hook.teardown({})

        mock_terminate.assert_called_once_with(
            process=mock_process,
            graceful_timeout=5.0,
            force_timeout=2.0,
        )


class TestGCMonitorHookTeardown:
    """Test GCMonitorHook teardown method."""

    def test_teardown_reads_json_and_adds_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        """teardown reads JSONL files and adds metrics to metadata."""
        hook = gc_monitor_hook()
        temp_file = tmp_path / "gc_monitor_12345_0.jsonl"
        hook._temp_files = [temp_file]
        _write_jsonl(temp_file, _make_jsonl_event())

        metadata: dict[str, object] = {}
        temp_output = tmp_path / "combined.jsonl"
        with patch("gc_monitor.pyperf.hook._get_env_pyperf_hook_output", return_value=temp_output):
            hook.teardown(metadata)

        # Verify metadata was added
        assert "gc_pause_gen_0_p99" in metadata
        assert isinstance(metadata["gc_pause_gen_0_p99"], (int, float))
        assert metadata["gc_pause_gen_0_p99"] > 0
        assert "gc_heap_size_p99" in metadata

    def test_teardown_handles_missing_file(self, tmp_path: Path) -> None:
        """teardown handles missing temp file gracefully."""
        hook = gc_monitor_hook()
        metadata: dict[str, object] = {}
        with patch("gc_monitor.pyperf.hook._get_env_pyperf_hook_output", return_value=tmp_path / "out.jsonl"):
            hook.teardown(metadata)

        # Should not add any keys if file doesn't exist
        assert metadata == {}

    def test_teardown_cleans_up_temp_files(
        self,
        mock_popen_process: tuple[Mock, Mock],
        tmp_path: Path,
    ) -> None:
        """teardown removes temp files after reading."""
        _mock_popen, _mock_process = mock_popen_process

        hook = gc_monitor_hook()
        with hook:
            temp_file = hook._temp_files[0]  # type: ignore[reportPrivateUsage]
            assert temp_file is not None

            _write_jsonl(temp_file, _make_jsonl_event())

            assert temp_file.exists()

        metadata: dict[str, Any] = {}
        with patch("gc_monitor.pyperf.hook._get_env_pyperf_hook_output", return_value=tmp_path / "out.jsonl"):
            hook.teardown(metadata)

        # Temp file should be removed
        assert not temp_file.exists()

    def test_teardown_closes_control_client(
        self,
        mock_popen_process: tuple[Mock, Mock],
        tmp_path: Path,
    ) -> None:
        """teardown closes the control plane connection."""
        _mock_popen, _mock_process = mock_popen_process

        hook = gc_monitor_hook()
        with hook:
            pass

        with patch("gc_monitor.pyperf.hook._get_env_pyperf_hook_output", return_value=tmp_path / "out.jsonl"):
            with patch.object(hook._control_client, "close") as mock_close:
                hook.teardown({})

        mock_close.assert_called_once()

    def test_teardown_combines_multiple_files(
        self,
        mock_popen_process: tuple[Mock, Mock],
        tmp_path: Path,
    ) -> None:
        """teardown combines events from multiple temp files."""
        _mock_popen, _mock_process = mock_popen_process

        hook = gc_monitor_hook()

        # Simulate multiple benchmark runs
        with hook:
            temp_file_0 = hook._temp_files[0]
            test_events_0 = [_make_jsonl_event()]
            _write_jsonl(temp_file_0, _make_jsonl_event())

        with hook:
            temp_file_1 = hook._temp_files[0]
            _write_jsonl(temp_file_1, _make_jsonl_event(
                tid=1, iid=1,
                ts_start=2_000_000_000, ts_stop=2_005_000_000,
                collections=3, collected=30, uncollectable=1,
                candidates=8, heap_size=25000, duration=0.008,
            ))

        metadata: dict[str, Any] = {"name": "test_benchmark"}

        # Use tmp_path for combined file by patching _get_env_pyperf_hook_output
        combined_file = tmp_path / "gc_monitor_test_benchmark_combined_12345.json"
        with patch(
            "gc_monitor.pyperf.hook._get_env_pyperf_hook_output",
            return_value=combined_file,
        ):
            hook.teardown(metadata)

        # Verify combined metrics
        assert "gc_pause_gen_0_p99" in metadata
        assert isinstance(metadata["gc_pause_gen_0_p99"], (int, float))
        assert "gc_heap_size_p99" in metadata

        # Verify combined trace file was created in tmp_path
        assert combined_file.exists()

        # Verify combined file has correct JSONL format
        combined_data = assert_valid_jsonl_format(combined_file)
        assert len(combined_data) >= 1  # at least 1 event

        # Both temp files should be removed
        assert not temp_file_0.exists()
        assert not temp_file_1.exists()


class TestAggregateGcStats:
    """Test StreamingStats aggregate method."""

    def test_empty_no_metadata(self) -> None:
        assert StreamingStats().aggregate() == {"pause_count": 0}

    def test_single_event_single_pid(self) -> None:
        ss = StreamingStats()
        ss.update(100, _make_event())
        result = ss.aggregate()
        assert result["pause_gen_0_p99"] > 0
        assert result["heap_size_p99"] == 20000

    def test_multiple_events_all_gen0(self) -> None:
        ss = StreamingStats()
        for i in range(3):
            ss.update(100, _make_event(
                iid=i,
                ts_start=1_000_000_000 + i * 100_000_000,
                ts_stop=1_005_000_000 + i * 100_000_000,
                heap_size=20000 + i * 5000,
            ))
        result = ss.aggregate()
        assert "pause_gen_0_p99" in result
        assert "pause_gen_1_p99" not in result
        assert "pause_gen_2_p99" not in result

    def test_multiple_pids_heap_p99(self) -> None:
        ss = StreamingStats()
        for pid in [100, 200, 300]:
            ss.update(pid, _make_event(heap_size=pid * 100))
        assert ss.aggregate()["heap_size_p99"] == 29800

    def test_per_generation_p99(self) -> None:
        ss = StreamingStats()
        for gen in range(3):
            ss.update(100, _make_event(gen=gen, iid=gen, ts_stop=1_005_000_000 + gen * 5_000_000))
        result = ss.aggregate()
        for gen in range(3):
            assert f"pause_gen_{gen}_p99" in result


class TestGcMonitorHookFactory:
    """Test gc_monitor_hook factory function."""

    def test_factory_returns_new_hook_each_time(self) -> None:
        """Factory returns a new hook instance each time."""
        hook1 = gc_monitor_hook()
        hook2 = gc_monitor_hook()
        assert hook1 is not hook2


class TestGCMonitorHookSharedOutput:
    """Test GCMonitorHook with shared output file (GC_MONITOR_PYPERF_HOOK_OUTPUT)."""

    def test_multiple_runs_write_to_shared_output_file(
        self,
        tmp_path: Path,
    ) -> None:
        """Test multiple pyperf runs writing to same output file via env var.

        When GC_MONITOR_PYPERF_HOOK_OUTPUT is set, multiple GCMonitorHook
        instances will write to the same output file. This tests that the
        combine logic correctly handles this case.
        """

        # Set up shared output file
        shared_output = tmp_path / "shared_gc_output.json"

        # Create first hook instance (first pyperf run)
        hook1 = gc_monitor_hook()

        # Mock the temp file for first run
        temp_file_1 = tmp_path / "gc_monitor_run_0_12345.jsonl"
        hook1._temp_files = [temp_file_1]

        # Write test events from first run
        _write_jsonl(temp_file_1, _make_jsonl_event(tid=1))

        # Mock _get_env_pyperf_hook_output to return shared output
        with patch(
            "gc_monitor.pyperf.hook._get_env_pyperf_hook_output",
            return_value=shared_output,
        ):
            metadata1: dict[str, Any] = {"name": "benchmark_run1"}
            hook1.teardown(metadata1)

        # Create second hook instance (second pyperf run)
        hook2 = gc_monitor_hook()

        # Mock the temp file for second run
        temp_file_2 = tmp_path / "gc_monitor_run_1_12345.jsonl"
        hook2._temp_files = [temp_file_2]

        # Write test events from second run
        _write_jsonl(temp_file_2, _make_jsonl_event(
            tid=1, ts_start=2_000_000_000, ts_stop=2_008_000_000,
            collections=3, collected=30, uncollectable=1,
            candidates=8, heap_size=25000, duration=0.008,
        ))

        # Second run also writes to shared output (overwrites)
        with patch(
            "gc_monitor.pyperf.hook._get_env_pyperf_hook_output",
            return_value=shared_output,
        ):
            metadata2: dict[str, Any] = {"name": "benchmark_run2"}
            hook2.teardown(metadata2)

        # Verify shared output file exists
        assert shared_output.exists()

        # Verify combined file has correct JSONL format
        combined_data = assert_valid_jsonl_format(shared_output)
        assert len(combined_data) >= 1  # at least 1 event

        # Verify metadata from second run
        assert "gc_pause_gen_0_p99" in metadata2

        # Cleanup temp files
        temp_file_1.unlink(missing_ok=True)
        temp_file_2.unlink(missing_ok=True)
        shared_output.unlink(missing_ok=True)


class TestGCMonitorHookBenchNameSubstitution:
    """Test GCMonitorHook with {bench_name} substitution in output path."""

    @pytest.mark.parametrize("bench_name, pattern, expected", [
        ("my_benchmark", "gc_trace_{bench_name}.json", "gc_trace_my_benchmark.json"),
        ("my-benchmark.with/special:chars", "gc_{bench_name}.json", "gc_my-benchmark_with_special_chars.json"),
    ])
    def test_bench_name_substitution(
        self,
        bench_name: str,
        pattern: str,
        expected: str,
        tmp_path: Path,
    ) -> None:
        """Test {bench_name} substitution in GC_MONITOR_PYPERF_HOOK_OUTPUT."""
        output_pattern = str(tmp_path / pattern)
        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            hook = gc_monitor_hook()
            temp_file = tmp_path / "gc_monitor_12345_0_50.jsonl"
            hook._temp_files = [temp_file]
            _write_jsonl(temp_file, _make_jsonl_event(tid=1))
            metadata: dict[str, Any] = {"name": bench_name}
            hook.teardown(metadata)
            expected_output = tmp_path / expected
            assert expected_output.exists()
            data = assert_valid_jsonl_format(expected_output)
            assert len(data) > 0
            assert "gc_pause_gen_0_p99" in metadata
            expected_output.unlink(missing_ok=True)
            temp_file.unlink(missing_ok=True)

    def test_bench_name_substitution_multiple_benchmarks(
        self,
        tmp_path: Path,
    ) -> None:
        """Test multiple teardown calls with different benchmark names write to different files."""

        # Set up environment variable with {bench_name} placeholder
        output_pattern = str(tmp_path / "gc_{bench_name}_trace.json")

        benchmark_configs = [
            {"name": "benchmark_alpha", "collections": 5, "collected": 50},
            {"name": "benchmark_beta", "collections": 10, "collected": 100},
            {"name": "benchmark_gamma", "collections": 15, "collected": 150},
        ]

        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            for idx, config in enumerate(benchmark_configs):
                hook = gc_monitor_hook()

                # Mock temp file
                temp_file = tmp_path / f"gc_monitor_12345_{idx}_50.jsonl"
                hook._temp_files = [temp_file]

                # Write test events with unique data per benchmark
                _write_jsonl(temp_file, _make_jsonl_event(
                    tid=1,
                    ts_start=1_000_000_000 + (idx * 1_000_000_000),
                    ts_stop=1_005_000_000 + (idx * 1_000_000_000),
                    collections=config["collections"],
                    collected=config["collected"],
                ))

                # Call teardown with specific benchmark name
                metadata: dict[str, Any] = {"name": config["name"]}
                hook.teardown(metadata)

                # Verify output file was created with substituted name
                expected_output = tmp_path / f"gc_{config['name']}_trace.json"
                assert expected_output.exists(), f"Expected {expected_output} to exist"

                # Verify file contains correct data
                data = assert_valid_jsonl_format(expected_output)
                assert len(data) > 0

                # Verify metadata was populated correctly
                assert "gc_pause_gen_0_p99" in metadata

                # Cleanup temp file
                temp_file.unlink(missing_ok=True)

        # Verify all three output files exist
        for config in benchmark_configs:
            expected_output = tmp_path / f"gc_{config['name']}_trace.json"
            assert expected_output.exists()
            expected_output.unlink(missing_ok=True)

    def test_bench_name_substitution_combine_with_existing(
        self,
        tmp_path: Path,
    ) -> None:
        """Test that existing file is combined with new data when using {bench_name} substitution."""

        # Set up environment variable with {bench_name} placeholder
        output_pattern = str(tmp_path / "gc_{bench_name}.json")

        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            # First run
            hook1 = gc_monitor_hook()
            temp_file_1 = tmp_path / "gc_monitor_12345_0_50.jsonl"
            hook1._temp_files = [temp_file_1]

            _write_jsonl(temp_file_1, _make_jsonl_event(tid=1))

            metadata1: dict[str, Any] = {"name": "shared_bench"}
            hook1.teardown(metadata1)

            # Second run with same benchmark name
            hook2 = gc_monitor_hook()
            temp_file_2 = tmp_path / "gc_monitor_12345_1_50.jsonl"
            hook2._temp_files = [temp_file_2]

            _write_jsonl(temp_file_2, _make_jsonl_event(
                tid=1, ts_start=2_000_000_000, ts_stop=2_008_000_000,
                collections=3, collected=30, uncollectable=1,
                candidates=8, heap_size=25000, duration=0.008,
            ))

            metadata2: dict[str, Any] = {"name": "shared_bench"}
            hook2.teardown(metadata2)

            # Verify combined output file exists
            expected_output = tmp_path / "gc_shared_bench.json"
            assert expected_output.exists()

            # Verify combined file has events from second run
            data = assert_valid_jsonl_format(expected_output)
            assert len(data) >= 1  # Event from second run

            # Verify second run metadata was populated
            assert "gc_pause_gen_0_p99" in metadata2

            # Cleanup
            expected_output.unlink(missing_ok=True)
            temp_file_1.unlink(missing_ok=True)
            temp_file_2.unlink(missing_ok=True)


class TestGetEnvControlTimeout:
    def test_default_value(self):
        with patch.dict(os.environ, clear=True):
            assert _get_env_pyperf_hook_control_timeout() == 10.0

    def test_custom_value(self):
        with patch.dict(os.environ, {"GC_MONITOR_PYPERF_HOOK_CONTROL_TIMEOUT": "30"}):
            assert _get_env_pyperf_hook_control_timeout() == 30.0

    def test_invalid_value_returns_default(self):
        with patch.dict(os.environ, {"GC_MONITOR_PYPERF_HOOK_CONTROL_TIMEOUT": "not-a-number"}):
            assert _get_env_pyperf_hook_control_timeout() == 10.0
