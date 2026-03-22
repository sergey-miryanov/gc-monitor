"""Tests for pyperf hook integration."""

# pyright: reportPrivateUsage=none, reportUnknownMemberType=none

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from gc_monitor.pyperf_hook import _aggregate_gc_stats, gc_monitor_hook, GCMonitorHook


class TestGCMonitorHookInit:
    """Test GCMonitorHook initialization."""

    def test_hook_init_default_values(self) -> None:
        """Hook initializes with default values."""
        hook = GCMonitorHook()
        assert hook._run_index == 0  # type: ignore[reportPrivateUsage]
        assert hook._temp_files == []  # type: ignore[reportPrivateUsage]
        assert hook._process is None  # type: ignore[reportPrivateUsage]
        assert hook._pid > 0  # type: ignore[reportPrivateUsage]

    def test_hook_init_sets_pid(self) -> None:
        """Hook initializes with current process PID."""
        hook = GCMonitorHook()
        assert hook._pid == os.getpid()  # type: ignore[reportPrivateUsage]


class TestGCMonitorHookEnter:
    """Test GCMonitorHook __enter__ method."""

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_enter_spawns_subprocess(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__enter__ spawns subprocess with correct command."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            assert hook._pid == 12345  # type: ignore[reportPrivateUsage]
            assert hook._process is not None  # type: ignore[reportPrivateUsage]

        # Verify subprocess.Popen was called with correct args
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        # Command should be: [sys.executable, "-m", "gc_monitor", pid, ...]
        assert call_args[1] == "-m"
        assert call_args[2] == "gc_monitor"
        assert call_args[3] == "12345"
        assert "-o" in call_args
        assert "--format" in call_args
        assert "chrome" in call_args

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    def test_enter_raises_on_missing_cli(
        self, mock_getpid: Mock, mock_popen: Mock
    ) -> None:
        """__enter__ raises RuntimeError if gc-monitor module not found."""
        mock_getpid.return_value = 12345
        mock_popen.side_effect = FileNotFoundError("module not found")

        hook = GCMonitorHook()
        with pytest.raises(RuntimeError) as exc_info:
            with hook:
                pass

        assert "Failed to run gc-monitor module" in str(exc_info.value)
        assert "Ensure gc-monitor is installed" in str(exc_info.value)

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_enter_creates_temp_file_path(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__enter__ creates temp file path with PID and run_index."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            assert len(hook._temp_files) == 1  # type: ignore[reportPrivateUsage]
            assert "gc_monitor_12345_0" in str(hook._temp_files[0])  # type: ignore[reportPrivateUsage]

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_enter_increments_run_index(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__enter__ increments run_index for multiple calls."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        
        # First enter
        with hook:
            assert "gc_monitor_12345_0" in str(hook._temp_files[0])  # type: ignore[reportPrivateUsage]
        
        # Second enter (simulating multiple benchmark runs)
        with hook:
            assert len(hook._temp_files) == 2  # type: ignore[reportPrivateUsage]
            assert "gc_monitor_12345_1" in str(hook._temp_files[1])  # type: ignore[reportPrivateUsage]

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_enter_includes_thread_name(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__enter__ includes thread-name with run_index in command."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify thread-name was included
        call_args = mock_popen.call_args[0][0]
        assert "--thread-name" in call_args
        thread_name_idx = call_args.index("--thread-name") + 1
        assert "run=0" in call_args[thread_name_idx]


class TestGCMonitorHookExit:
    """Test GCMonitorHook __exit__ method."""

    @pytest.mark.skipif(os.name != "posix", reason="Unix-only test")
    @patch("gc_monitor.pyperf_hook.os.kill")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.os.name", "posix")
    def test_exit_sends_sigint_unix(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
        mock_kill: Mock,
    ) -> None:
        """__exit__ sends SIGINT on Unix systems."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_process.pid = 54321
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify SIGINT was sent
        mock_kill.assert_called_once_with(54321, signal.SIGINT)
        mock_process.wait.assert_called_once_with(timeout=5.0)

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only test")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.os.name", "nt")
    def test_exit_sends_ctrl_break_windows(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__exit__ sends CTRL_BREAK_EVENT on Windows."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_process.pid = 54321
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify CTRL_BREAK_EVENT was sent
        mock_process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)

    @pytest.mark.skipif(os.name != "posix", reason="Unix-only test")
    @patch("gc_monitor.pyperf_hook.os.kill")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.os.name", "posix")
    def test_exit_fallback_to_sigterm_on_timeout(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
        mock_kill: Mock,
    ) -> None:
        """__exit__ falls back to SIGTERM on timeout (Unix)."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_process.pid = 54321
        # First wait() times out, second wait() also times out (for SIGKILL fallback)
        mock_process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="gc-monitor", timeout=5.0),
            subprocess.TimeoutExpired(cmd="gc-monitor", timeout=2.0),
        ]
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify SIGINT was sent first, then SIGTERM, then SIGKILL
        assert mock_kill.call_count == 3
        mock_kill.assert_any_call(54321, signal.SIGINT)
        mock_kill.assert_any_call(54321, signal.SIGTERM)
        # SIGKILL is sent via os.kill on Unix
        mock_kill.assert_any_call(54321, 9)  # SIGKILL = 9 on Unix

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only test")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.os.name", "nt")
    def test_exit_fallback_to_kill_on_timeout_windows(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__exit__ falls back to kill() on timeout (Windows)."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_process.pid = 54321
        mock_process.wait.side_effect = subprocess.TimeoutExpired(
            cmd="gc-monitor", timeout=5.0
        )
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify CTRL_BREAK_EVENT was sent first, then kill()
        mock_process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
        mock_process.kill.assert_called_once()


class TestGCMonitorHookTeardown:
    """Test GCMonitorHook teardown method."""

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_teardown_reads_json_and_adds_metadata(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
        tmp_path: Path,
    ) -> None:
        """teardown reads JSON files and adds metrics to metadata."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        # Create temp JSON file in Chrome Trace format
        hook = GCMonitorHook()
        with hook:
            temp_file = hook._temp_files[0]  # type: ignore[reportPrivateUsage]
            assert temp_file is not None

            # Write test data in Chrome Trace format (array of events)
            test_data: list[dict[str, Any]] = [
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {"name": "Python Process (PID: 12345)"},
                },
                {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {"name": "GC Monitor (run=0)"},
                },
                # GC Pause event (ph=X = complete event)
                {
                    "name": "GC Pause",
                    "cat": "gc.pause",
                    "ph": "X",
                    "ts": 1_000_000,  # microseconds
                    "dur": 5_000,  # 5ms in microseconds
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {
                        "gen": 0,
                        "collections": 5,
                        "collected": 50,
                        "uncollectable": 2,
                        "candidates": 10,
                        "object_visits": 200,
                        "objects_transitively_reachable": 100,
                        "objects_not_transitively_reachable": 100,
                        "heap_size": 20000,
                        "work_to_do": 20,
                        "duration": 0.005,  # 5ms in seconds
                        "total_duration": 0.005,
                    },
                },
                # Counter event (ph=C)
                {
                    "name": "Memory Counters (gen=0)",
                    "cat": "gc.memory(gen=0)",
                    "ph": "C",
                    "ts": 1_000_000,
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {
                        "heap_size": 20000,
                        "collected": 50,
                        "uncollectable": 2,
                        "candidates": 10,
                        "object_visits": 200,
                    },
                },
            ]
            with open(temp_file, "w") as f:
                json.dump(test_data, f)

        metadata: dict[str, object] = {}
        hook.teardown(metadata)

        # Verify metadata was added
        assert "gc_collections_total" in metadata
        assert metadata["gc_collections_total"] == 5
        assert "gc_avg_pause_duration_sec" in metadata
        assert "gc_event_count" in metadata

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    def test_teardown_handles_missing_file(
        self, mock_getpid: Mock, mock_popen: Mock
    ) -> None:
        """teardown handles missing temp file gracefully."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        metadata: dict[str, object] = {}
        hook.teardown(metadata)

        # Should not add any keys if file doesn't exist
        assert metadata == {}

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_teardown_cleans_up_temp_files(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """teardown removes temp files after reading."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            temp_file = hook._temp_files[0]  # type: ignore[reportPrivateUsage]
            assert temp_file is not None

            # Create the temp file with Chrome Trace format
            test_data: list[dict[str, Any]] = [
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor",
                    "args": {"name": "Python Process (PID: 12345)"},
                },
                {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor",
                    "args": {"name": "GC Monitor"},
                },
            ]
            with open(temp_file, "w") as f:
                json.dump(test_data, f)

            assert temp_file.exists()

        metadata: dict[str, Any] = {}
        hook.teardown(metadata)

        # Temp file should be removed
        assert not temp_file.exists()

    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    def test_teardown_combines_multiple_files(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """teardown combines events from multiple temp files."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()

        # Simulate multiple benchmark runs
        with hook:
            temp_file_0 = hook._temp_files[0]
            test_data_0: list[dict[str, Any]] = [
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {"name": "Python Process (PID: 12345)"},
                },
                {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {"name": "GC Monitor (run=0)"},
                },
                {
                    "name": "GC Pause",
                    "cat": "gc.pause",
                    "ph": "X",
                    "ts": 1_000_000,
                    "dur": 5_000,
                    "pid": 12345,
                    "tid": "GC Monitor (run=0)",
                    "args": {
                        "gen": 0,
                        "collections": 5,
                        "collected": 50,
                        "uncollectable": 2,
                        "candidates": 10,
                        "object_visits": 200,
                        "objects_transitively_reachable": 100,
                        "objects_not_transitively_reachable": 100,
                        "heap_size": 20000,
                        "work_to_do": 20,
                        "duration": 0.005,
                        "total_duration": 0.005,
                    },
                },
            ]
            with open(temp_file_0, "w") as f:
                json.dump(test_data_0, f)

        with hook:
            temp_file_1 = hook._temp_files[1]
            test_data_1: list[dict[str, Any]] = [
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor (run=1)",
                    "args": {"name": "Python Process (PID: 12345)"},
                },
                {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": "GC Monitor (run=1)",
                    "args": {"name": "GC Monitor (run=1)"},
                },
                {
                    "name": "GC Pause",
                    "cat": "gc.pause",
                    "ph": "X",
                    "ts": 2_000_000,
                    "dur": 8_000,
                    "pid": 12345,
                    "tid": "GC Monitor (run=1)",
                    "args": {
                        "gen": 0,
                        "collections": 3,
                        "collected": 30,
                        "uncollectable": 1,
                        "candidates": 8,
                        "object_visits": 150,
                        "objects_transitively_reachable": 80,
                        "objects_not_transitively_reachable": 70,
                        "heap_size": 25000,
                        "work_to_do": 15,
                        "duration": 0.008,
                        "total_duration": 0.008,
                    },
                },
            ]
            with open(temp_file_1, "w") as f:
                json.dump(test_data_1, f)

        metadata: dict[str, Any] = {"name": "test_benchmark"}
        hook.teardown(metadata)

        # Verify combined metrics (5 + 3 = 8 collections)
        assert metadata["gc_collections_total"] == 8
        assert metadata["gc_objects_collected_total"] == 80
        assert metadata["gc_event_count"] == 2

        # Verify combined trace file was created
        combined_file = Path("gc_monitor_test_benchmark_combined_12345.json")
        assert combined_file.exists()

        # Verify combined file has correct Chrome Trace format
        with open(combined_file, "r") as f:
            combined_data: list[dict[str, Any]] = json.load(f)
            assert isinstance(combined_data, list)
            # Should have process_name, thread_names, and events
            assert len(combined_data) >= 4  # metadata + events

        # Clean up combined file
        combined_file.unlink()

        # Both temp files should be removed
        assert not temp_file_0.exists()
        assert not temp_file_1.exists()


class TestAggregateGcStats:
    """Test _aggregate_gc_stats function."""

    def test_empty_events(self) -> None:
        """Empty event list returns empty dict."""
        result = _aggregate_gc_stats([])
        assert result == {}

    def test_single_event(self) -> None:
        """Single event produces correct aggregations.

        Note: ts is in nanoseconds, duration and total_duration are in seconds.
        """
        event: dict[str, Any] = {
            "gen": 0,
            "ts": 1_000_000_000,  # 1 second in nanoseconds
            "collections": 5,
            "collected": 50,
            "uncollectable": 2,
            "candidates": 10,
            "object_visits": 200,
            "objects_transitively_reachable": 100,
            "objects_not_transitively_reachable": 100,
            "heap_size": 20000,
            "work_to_do": 20,
            "duration": 0.005,  # 5ms in seconds
            "total_duration": 0.005,  # 5ms in seconds
        }
        result = _aggregate_gc_stats([event])

        assert result["collections_total"] == 5
        assert result["avg_pause_duration_sec"] == 0.005
        assert result["max_pause_duration_sec"] == 0.005
        assert result["min_pause_duration_sec"] == 0.005
        assert result["max_heap_size"] == 20000
        assert result["event_count"] == 1

    def test_multiple_events_sum(self) -> None:
        """Multiple events: sum is cumulative."""
        events: list[dict[str, Any]] = [
            {
                "gen": 0,
                "ts": 1_000_000_000 + (i * 100_000_000),  # Nanoseconds, 100ms apart
                "collections": 5,
                "collected": 50,
                "uncollectable": 2,
                "candidates": 10,
                "object_visits": 200,
                "objects_transitively_reachable": 100,
                "objects_not_transitively_reachable": 100,
                "heap_size": 20000,
                "work_to_do": 20,
                "duration": 0.005,
                "total_duration": 0.005 * (i + 1),
            }
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)

        assert result["collections_total"] == 15  # 5 * 3
        assert result["objects_collected_total"] == 150  # 50 * 3

    def test_multiple_events_average(self) -> None:
        """Multiple events: average is correct."""
        events: list[dict[str, Any]] = [
            {
                "gen": 0,
                "ts": 1_000_000_000 + (i * 100_000_000),  # Nanoseconds, 100ms apart
                "collections": 5,
                "collected": 50,
                "uncollectable": 2,
                "candidates": 10,
                "object_visits": 200,
                "objects_transitively_reachable": 100,
                "objects_not_transitively_reachable": 100,
                "heap_size": 20000 + i * 1000,
                "work_to_do": 20,
                "duration": 0.005 + i * 0.001,
                "total_duration": 0.005,
            }
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)

        # Avg heap: (20000 + 21000 + 22000) / 3 = 21000
        assert result["avg_heap_size"] == 21000
        # Avg duration: (0.005 + 0.006 + 0.007) / 3 = 0.006
        assert result["avg_pause_duration_sec"] == pytest.approx(0.006)

    def test_multiple_events_max_min(self) -> None:
        """Multiple events: max and min are correct."""
        events: list[dict[str, Any]] = [
            {
                "gen": 0,
                "ts": 1_000_000_000 + (i * 100_000_000),  # Nanoseconds, 100ms apart
                "collections": 5,
                "collected": 50,
                "uncollectable": 2,
                "candidates": 10,
                "object_visits": 200 + i * 50,
                "objects_transitively_reachable": 100,
                "objects_not_transitively_reachable": 100,
                "heap_size": 20000 + i * 5000,
                "work_to_do": 20,
                "duration": 0.005 + i * 0.002,
                "total_duration": 0.005,
            }
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)

        assert result["max_heap_size"] == 30000  # 20000 + 2*5000
        assert result["min_heap_size"] == 20000
        assert result["max_pause_duration_sec"] == pytest.approx(0.009)
        assert result["min_pause_duration_sec"] == pytest.approx(0.005)

    def test_per_generation_breakdown(self) -> None:
        """Events are grouped by generation."""
        events: list[dict[str, Any]] = [
            {
                "gen": 0,
                "ts": 1_000_000_000,  # 1 second in nanoseconds
                "collections": 5,
                "collected": 50,
                "uncollectable": 2,
                "candidates": 10,
                "object_visits": 200,
                "objects_transitively_reachable": 100,
                "objects_not_transitively_reachable": 100,
                "heap_size": 20000,
                "work_to_do": 20,
                "duration": 0.005,
                "total_duration": 0.005,
            },
            {
                "gen": 1,
                "ts": 2_000_000_000,  # 2 seconds in nanoseconds
                "collections": 3,
                "collected": 30,
                "uncollectable": 1,
                "candidates": 8,
                "object_visits": 150,
                "objects_transitively_reachable": 80,
                "objects_not_transitively_reachable": 70,
                "heap_size": 25000,
                "work_to_do": 15,
                "duration": 0.008,
                "total_duration": 0.008,
            },
            {
                "gen": 2,
                "ts": 3_000_000_000,  # 3 seconds in nanoseconds
                "collections": 1,
                "collected": 10,
                "uncollectable": 0,
                "candidates": 5,
                "object_visits": 100,
                "objects_transitively_reachable": 50,
                "objects_not_transitively_reachable": 50,
                "heap_size": 30000,
                "work_to_do": 10,
                "duration": 0.015,
                "total_duration": 0.015,
            },
        ]
        result = _aggregate_gc_stats(events)

        assert result["collections_by_gen_0"] == 5
        assert result["collections_by_gen_1"] == 3
        assert result["collections_by_gen_2"] == 1


class TestGcMonitorHookFactory:
    """Test gc_monitor_hook factory function."""

    def test_factory_creates_hook_with_defaults(self) -> None:
        """Factory creates hook with default values."""
        hook = gc_monitor_hook()
        assert hook._run_index == 0  # type: ignore[reportPrivateUsage]
        assert hook._temp_files == []  # type: ignore[reportPrivateUsage]

    def test_factory_returns_new_hook_each_time(self) -> None:
        """Factory returns a new hook instance each time."""
        hook1 = gc_monitor_hook()
        hook2 = gc_monitor_hook()
        assert hook1 is not hook2
