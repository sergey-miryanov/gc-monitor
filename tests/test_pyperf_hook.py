"""Tests for pyperf hook integration."""

# pyright: reportPrivateUsage=none, reportUnknownMemberType=none, reportUnknownVariableType=none, reportUnknownArgumentType=none, reportUnusedFunction=none

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from gc_monitor.pyperf_hook import _aggregate_gc_stats, gc_monitor_hook, GCMonitorHook


def _assert_valid_chrome_trace_format(file_path: Path) -> list[dict[str, Any]]:
    """Validate that a file contains valid Chrome Trace format (JSON array of objects).
    
    Args:
        file_path: Path to the JSON file to validate
        
    Returns:
        List of parsed event dictionaries
        
    Raises:
        AssertionError: If the file is not valid Chrome Trace format
    """
    assert file_path.exists(), f"File {file_path} does not exist"
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check basic JSON array structure
    content_stripped = content.strip()
    assert content_stripped.startswith("["), f"Chrome Trace file should start with '[', got: {content_stripped[:20]}"
    assert content_stripped.endswith("]"), f"Chrome Trace file should end with ']', got: {content_stripped[-20:]}"
    
    # Parse and validate structure
    data: object = json.loads(content)
    assert isinstance(data, list), f"Chrome Trace file should contain a JSON array, got {type(data)}"
    
    # Validate each item is a dict (JSON object)
    for idx, item in enumerate(data):
        assert isinstance(item, dict), f"Item {idx} in Chrome Trace file should be a dict, got {type(item)}"
    
    # Cast to expected type after validation
    return data  # type: ignore[return-value]


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
        # Command structure: [sys.executable, "-m", "gc_monitor", "monitor", pid, ...]
        assert call_args[0] == sys.executable
        assert call_args[1] == "-m"
        assert call_args[2] == "gc_monitor"
        assert call_args[3] == "monitor"
        assert call_args[4] == "12345"
        assert "-o" in call_args
        assert "--format" in call_args
        assert "jsonl" in call_args

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
    def test_enter_includes_thread_id(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__enter__ includes thread-id with run_index in command."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify thread-id was included
        call_args = mock_popen.call_args[0][0]
        assert "--thread-id" in call_args
        thread_id_idx = call_args.index("--thread-id") + 1
        # Thread ID is the run_index (e.g., "0" for first run)
        assert call_args[thread_id_idx] == "0"


class TestGCMonitorHookExit:
    """Test GCMonitorHook __exit__ method."""

    @pytest.mark.skipif(os.name != "posix", reason="Unix-only test")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.os.name", "posix")
    def test_exit_sends_sigint_unix(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__exit__ sends SIGINT on Unix systems."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_process.pid = 54321
        mock_process.communicate.return_value = (b"", b"")
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify SIGINT was sent via send_signal
        mock_process.send_signal.assert_called_once_with(signal.SIGINT)
        mock_process.communicate.assert_called_once_with(timeout=5.0)

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
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    @patch("gc_monitor.pyperf_hook.os.getpid")
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.os.name", "posix")
    def test_exit_fallback_to_sigterm_on_timeout(
        self,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_popen: Mock,
    ) -> None:
        """__exit__ falls back to SIGTERM on timeout (Unix)."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_process.pid = 54321
        # First communicate() times out, second communicate() also times out (for SIGKILL fallback)
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="gc-monitor", timeout=5.0),
            subprocess.TimeoutExpired(cmd="gc-monitor", timeout=2.0),
            (b"", b""),  # Final communicate(timeout=None) succeeds
        ]
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify SIGINT and SIGTERM were sent via send_signal, then SIGKILL via kill
        assert mock_process.send_signal.call_count == 2
        mock_process.send_signal.assert_any_call(signal.SIGINT)
        mock_process.send_signal.assert_any_call(signal.SIGTERM)
        mock_process.kill.assert_called_once()

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
        # Set returncode to None initially (process still running)
        mock_process.returncode = None
        # First communicate() times out, triggering kill()
        # Second communicate() after kill() succeeds and sets returncode
        def communicate_side_effect(timeout=None):
            if timeout == 5.0:  # Graceful timeout
                raise subprocess.TimeoutExpired(cmd="gc-monitor", timeout=5.0)
            else:  # After kill()
                mock_process.returncode = 0
                return (b"", b"")
        mock_process.communicate.side_effect = communicate_side_effect
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
        """teardown reads JSONL files and adds metrics to metadata."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        # Create temp JSONL file
        hook = GCMonitorHook()
        with hook:
            temp_file = hook._temp_files[0]  # type: ignore[reportPrivateUsage]
            assert temp_file is not None

            # Write test data in JSONL format (one JSON object per line)
            # Each line is a complete JSON object (no array wrapper)
            # JSONL format has flat fields (as written by JsonlExporter)
            test_events: list[dict[str, Any]] = [
                # GC event in JSONL format (flat fields)
                {
                    "pid": 12345,
                    "tid": 0,
                    "gen": 0,
                    "ts": 1_000_000,  # nanoseconds
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
            ]
            # Write as JSONL (one JSON object per line)
            with open(temp_file, "w") as f:
                for event in test_events:
                    f.write(json.dumps(event) + "\n")

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
                    "tid": 0,
                    "args": {"name": "Python Process (PID: 12345)"},
                },
                {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": 12345,
                    "tid": 0,
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
        tmp_path: Path,
    ) -> None:
        """teardown combines events from multiple temp files."""
        mock_getpid.return_value = 12345
        mock_process = Mock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()

        # Simulate multiple benchmark runs
        with hook:
            temp_file_0 = hook._temp_files[0]
            # Write test data in JSONL format (one JSON object per line)
            # JSONL format has flat fields (as written by JsonlExporter)
            test_events_0: list[dict[str, Any]] = [
                {
                    "pid": 12345,
                    "tid": 0,
                    "gen": 0,
                    "ts": 1_000_000,  # nanoseconds
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
            ]
            with open(temp_file_0, "w") as f:
                for event in test_events_0:
                    f.write(json.dumps(event) + "\n")

        with hook:
            temp_file_1 = hook._temp_files[1]
            # Write test data in JSONL format (one JSON object per line)
            test_events_1: list[dict[str, Any]] = [
                {
                    "pid": 12345,
                    "tid": 1,
                    "gen": 0,
                    "ts": 2_000_000,  # nanoseconds
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
            ]
            with open(temp_file_1, "w") as f:
                for event in test_events_1:
                    f.write(json.dumps(event) + "\n")

        metadata: dict[str, Any] = {"name": "test_benchmark"}
        
        # Use tmp_path for combined file by patching _get_env_pyperf_hook_output
        combined_file = tmp_path / "gc_monitor_test_benchmark_combined_12345.json"
        with patch(
            "gc_monitor.pyperf_hook._get_env_pyperf_hook_output",
            return_value=combined_file,
        ):
            hook.teardown(metadata)

        # Verify combined metrics (5 + 3 = 8 collections)
        assert metadata["gc_collections_total"] == 8
        assert metadata["gc_objects_collected_total"] == 80
        assert metadata["gc_event_count"] == 2

        # Verify combined trace file was created in tmp_path
        assert combined_file.exists()

        # Verify combined file has correct Chrome Trace format (JSON array)
        combined_data = _assert_valid_chrome_trace_format(combined_file)
        # Should have process_name, thread_names, and events
        assert len(combined_data) >= 3  # metadata + events

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


class TestGCMonitorHookSharedOutput:
    """Test GCMonitorHook with shared output file (GC_MONITOR_PYPERF_HOOK_OUTPUT)."""

    @patch("gc_monitor.pyperf_hook._get_env_pyperf_hook_verbose", return_value=False)
    @patch("gc_monitor.pyperf_hook.os.getpid", return_value=12345)
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    def test_multiple_runs_write_to_shared_output_file(
        self,
        mock_popen: Mock,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_verbose: Mock,
        tmp_path: Path,
    ) -> None:
        """Test multiple pyperf runs writing to same output file via env var.

        When GC_MONITOR_PYPERF_HOOK_OUTPUT is set, multiple GCMonitorHook
        instances will write to the same output file. This tests that the
        combine logic correctly handles this case.
        """
        from gc_monitor.pyperf_hook import GCMonitorHook

        # Set up shared output file
        shared_output = tmp_path / "shared_gc_output.json"

        # Create first hook instance (first pyperf run)
        hook1 = GCMonitorHook()

        # Mock the temp file for first run
        temp_file_1 = tmp_path / "gc_monitor_run_0_12345.jsonl"
        hook1._temp_files = [temp_file_1]

        # Write test events from first run
        test_events_run1: list[dict[str, Any]] = [
            {
                "pid": 12345,
                "tid": 1,
                "gen": 0,
                "ts": 1_000_000_000,
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
            }
        ]
        with open(temp_file_1, "w", encoding="utf-8") as f:
            for event in test_events_run1:
                f.write(json.dumps(event) + "\n")

        # Mock _get_env_pyperf_hook_output to return shared output
        with patch(
            "gc_monitor.pyperf_hook._get_env_pyperf_hook_output",
            return_value=shared_output,
        ):
            metadata1: dict[str, Any] = {"name": "benchmark_run1"}
            hook1.teardown(metadata1)

        # Create second hook instance (second pyperf run)
        hook2 = GCMonitorHook()

        # Mock the temp file for second run
        temp_file_2 = tmp_path / "gc_monitor_run_1_12345.jsonl"
        hook2._temp_files = [temp_file_2]

        # Write test events from second run
        test_events_run2: list[dict[str, Any]] = [
            {
                "pid": 12345,
                "tid": 1,
                "gen": 0,
                "ts": 2_000_000_000,
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
            }
        ]
        with open(temp_file_2, "w", encoding="utf-8") as f:
            for event in test_events_run2:
                f.write(json.dumps(event) + "\n")

        # Second run also writes to shared output (overwrites)
        with patch(
            "gc_monitor.pyperf_hook._get_env_pyperf_hook_output",
            return_value=shared_output,
        ):
            metadata2: dict[str, Any] = {"name": "benchmark_run2"}
            hook2.teardown(metadata2)

        # Verify shared output file exists
        assert shared_output.exists()

        # Verify combined file has correct Chrome Trace format
        combined_data = _assert_valid_chrome_trace_format(shared_output)
        # Should have process_name, thread_names, and events from second run
        # (second run overwrites first)
        assert len(combined_data) >= 3  # metadata + events

        # Verify metadata from second run
        assert metadata2["gc_collections_total"] == 3
        assert metadata2["gc_objects_collected_total"] == 30

        # Cleanup temp files
        temp_file_1.unlink(missing_ok=True)
        temp_file_2.unlink(missing_ok=True)
        shared_output.unlink(missing_ok=True)


class TestGCMonitorHookBenchNameSubstitution:
    """Test GCMonitorHook with {bench_name} substitution in output path."""

    @patch("gc_monitor.pyperf_hook._get_env_pyperf_hook_verbose", return_value=False)
    @patch("gc_monitor.pyperf_hook.os.getpid", return_value=12345)
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    def test_bench_name_substitution_basic(
        self,
        mock_popen: Mock,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_verbose: Mock,
        tmp_path: Path,
    ) -> None:
        """Test {bench_name} substitution in GC_MONITOR_PYPERF_HOOK_OUTPUT."""
        from gc_monitor.pyperf_hook import GCMonitorHook

        # Set up environment variable with {bench_name} placeholder
        output_pattern = str(tmp_path / "gc_trace_{bench_name}.json")
        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            hook = GCMonitorHook()

            # Mock temp file
            temp_file = tmp_path / "gc_monitor_12345_0_50.jsonl"
            hook._temp_files = [temp_file]

            # Write test events
            test_events: list[dict[str, Any]] = [
                {
                    "pid": 12345,
                    "tid": 1,
                    "gen": 0,
                    "ts": 1_000_000_000,
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
                }
            ]
            with open(temp_file, "w", encoding="utf-8") as f:
                for event in test_events:
                    f.write(json.dumps(event) + "\n")

            # Call teardown with specific benchmark name
            metadata: dict[str, Any] = {"name": "my_benchmark"}
            hook.teardown(metadata)

            # Verify output file was created with substituted name
            expected_output = tmp_path / "gc_trace_my_benchmark.json"
            assert expected_output.exists()

            # Verify file content
            data = _assert_valid_chrome_trace_format(expected_output)
            assert len(data) > 0

            # Verify metadata was populated
            assert metadata["gc_collections_total"] == 5
            assert metadata["gc_objects_collected_total"] == 50

            # Cleanup
            expected_output.unlink(missing_ok=True)
            temp_file.unlink(missing_ok=True)

    @patch("gc_monitor.pyperf_hook._get_env_pyperf_hook_verbose", return_value=False)
    @patch("gc_monitor.pyperf_hook.os.getpid", return_value=12345)
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    def test_bench_name_substitution_multiple_benchmarks(
        self,
        mock_popen: Mock,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_verbose: Mock,
        tmp_path: Path,
    ) -> None:
        """Test multiple teardown calls with different benchmark names write to different files."""
        from gc_monitor.pyperf_hook import GCMonitorHook

        # Set up environment variable with {bench_name} placeholder
        output_pattern = str(tmp_path / "gc_{bench_name}_trace.json")

        benchmark_configs = [
            {"name": "benchmark_alpha", "collections": 5, "collected": 50},
            {"name": "benchmark_beta", "collections": 10, "collected": 100},
            {"name": "benchmark_gamma", "collections": 15, "collected": 150},
        ]

        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            for idx, config in enumerate(benchmark_configs):
                hook = GCMonitorHook()

                # Mock temp file
                temp_file = tmp_path / f"gc_monitor_12345_{idx}_50.jsonl"
                hook._temp_files = [temp_file]

                # Write test events with unique data per benchmark
                test_events: list[dict[str, Any]] = [
                    {
                        "pid": 12345,
                        "tid": 1,
                        "gen": 0,
                        "ts": 1_000_000_000 + (idx * 1_000_000_000),
                        "collections": config["collections"],
                        "collected": config["collected"],
                        "uncollectable": 2,
                        "candidates": 10,
                        "object_visits": 200,
                        "objects_transitively_reachable": 100,
                        "objects_not_transitively_reachable": 100,
                        "heap_size": 20000,
                        "work_to_do": 20,
                        "duration": 0.005,
                        "total_duration": 0.005,
                    }
                ]
                with open(temp_file, "w", encoding="utf-8") as f:
                    for event in test_events:
                        f.write(json.dumps(event) + "\n")

                # Call teardown with specific benchmark name
                metadata: dict[str, Any] = {"name": config["name"]}
                hook.teardown(metadata)

                # Verify output file was created with substituted name
                expected_output = tmp_path / f"gc_{config['name']}_trace.json"
                assert expected_output.exists(), f"Expected {expected_output} to exist"

                # Verify file contains correct data
                data = _assert_valid_chrome_trace_format(expected_output)
                assert len(data) > 0

                # Verify metadata was populated correctly
                assert metadata["gc_collections_total"] == config["collections"]
                assert metadata["gc_objects_collected_total"] == config["collected"]

                # Cleanup temp file
                temp_file.unlink(missing_ok=True)

        # Verify all three output files exist
        for config in benchmark_configs:
            expected_output = tmp_path / f"gc_{config['name']}_trace.json"
            assert expected_output.exists()
            expected_output.unlink(missing_ok=True)

    @patch("gc_monitor.pyperf_hook._get_env_pyperf_hook_verbose", return_value=False)
    @patch("gc_monitor.pyperf_hook.os.getpid", return_value=12345)
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    def test_bench_name_substitution_with_special_chars(
        self,
        mock_popen: Mock,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_verbose: Mock,
        tmp_path: Path,
    ) -> None:
        """Test {bench_name} substitution sanitizes special characters."""
        from gc_monitor.pyperf_hook import GCMonitorHook

        # Set up environment variable with {bench_name} placeholder
        output_pattern = str(tmp_path / "gc_{bench_name}.json")

        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            hook = GCMonitorHook()

            # Mock temp file
            temp_file = tmp_path / "gc_monitor_12345_0_50.jsonl"
            hook._temp_files = [temp_file]

            # Write test events
            test_events: list[dict[str, Any]] = [
                {
                    "pid": 12345,
                    "tid": 1,
                    "gen": 0,
                    "ts": 1_000_000_000,
                    "collections": 5,
                    "collected": 50,
                    "uncollectable": 2,
                    "candidates": 10,
                    "object_visits": 200,
                    "heap_size": 20000,
                    "work_to_do": 20,
                    "duration": 0.005,
                    "total_duration": 0.005,
                }
            ]
            with open(temp_file, "w", encoding="utf-8") as f:
                for event in test_events:
                    f.write(json.dumps(event) + "\n")

            # Call teardown with benchmark name containing special characters
            metadata: dict[str, Any] = {"name": "my-benchmark.with/special:chars"}
            hook.teardown(metadata)

            # Verify output file was created with sanitized name
            # Special chars should be replaced with underscores
            expected_output = tmp_path / "gc_my-benchmark_with_special_chars.json"
            assert expected_output.exists()

            # Cleanup
            expected_output.unlink(missing_ok=True)
            temp_file.unlink(missing_ok=True)

    @patch("gc_monitor.pyperf_hook._get_env_pyperf_hook_verbose", return_value=False)
    @patch("gc_monitor.pyperf_hook.os.getpid", return_value=12345)
    @patch("gc_monitor.pyperf_hook.time.sleep")
    @patch("gc_monitor.pyperf_hook.subprocess.Popen")
    def test_bench_name_substitution_combine_with_existing(
        self,
        mock_popen: Mock,
        mock_sleep: Mock,
        mock_getpid: Mock,
        mock_verbose: Mock,
        tmp_path: Path,
    ) -> None:
        """Test that existing file is combined with new data when using {bench_name} substitution."""
        from gc_monitor.pyperf_hook import GCMonitorHook

        # Set up environment variable with {bench_name} placeholder
        output_pattern = str(tmp_path / "gc_{bench_name}.json")

        with patch.dict("os.environ", {"GC_MONITOR_PYPERF_HOOK_OUTPUT": output_pattern}):
            # First run
            hook1 = GCMonitorHook()
            temp_file_1 = tmp_path / "gc_monitor_12345_0_50.jsonl"
            hook1._temp_files = [temp_file_1]

            test_events_1: list[dict[str, Any]] = [
                {
                    "pid": 12345,
                    "tid": 1,
                    "gen": 0,
                    "ts": 1_000_000_000,
                    "collections": 5,
                    "collected": 50,
                    "uncollectable": 2,
                    "candidates": 10,
                    "object_visits": 200,
                    "heap_size": 20000,
                    "work_to_do": 20,
                    "duration": 0.005,
                    "total_duration": 0.005,
                }
            ]
            with open(temp_file_1, "w", encoding="utf-8") as f:
                for event in test_events_1:
                    f.write(json.dumps(event) + "\n")

            metadata1: dict[str, Any] = {"name": "shared_bench"}
            hook1.teardown(metadata1)

            # Second run with same benchmark name
            hook2 = GCMonitorHook()
            temp_file_2 = tmp_path / "gc_monitor_12345_1_50.jsonl"
            hook2._temp_files = [temp_file_2]

            test_events_2: list[dict[str, Any]] = [
                {
                    "pid": 12345,
                    "tid": 1,
                    "gen": 0,
                    "ts": 2_000_000_000,
                    "collections": 3,
                    "collected": 30,
                    "uncollectable": 1,
                    "candidates": 8,
                    "object_visits": 150,
                    "heap_size": 25000,
                    "work_to_do": 15,
                    "duration": 0.008,
                    "total_duration": 0.008,
                }
            ]
            with open(temp_file_2, "w", encoding="utf-8") as f:
                for event in test_events_2:
                    f.write(json.dumps(event) + "\n")

            metadata2: dict[str, Any] = {"name": "shared_bench"}
            hook2.teardown(metadata2)

            # Verify combined output file exists
            expected_output = tmp_path / "gc_shared_bench.json"
            assert expected_output.exists()

            # Verify combined file has events from both runs
            data = _assert_valid_chrome_trace_format(expected_output)
            # Should have metadata + events from both runs combined
            assert len(data) >= 6  # At least metadata + events from both runs

            # Verify second run metadata (first run metadata is lost in this scenario)
            assert metadata2["gc_collections_total"] == 3
            assert metadata2["gc_objects_collected_total"] == 30

            # Cleanup
            expected_output.unlink(missing_ok=True)
            temp_file_1.unlink(missing_ok=True)
            temp_file_2.unlink(missing_ok=True)
