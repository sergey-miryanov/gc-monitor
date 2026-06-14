import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import msgspec
import pytest

from gcmon.exporters.chrome_trace_format import begin_event, end_event, process_meta, thread_meta
from tests.helpers import (
    assert_is_begin,
    assert_is_counter,
    assert_is_process_meta,
    assert_is_thread_meta,
    assert_valid_chrome_trace_format,
    create_jsonl_record,
)

# =============================================================================
# Factory function for well-formed Chrome trace events
# =============================================================================


def make_event_pair(name: str, ts: int = 0, dur: float = 10, pid: int = 1, tid: int = 1, cat: str = "test") -> list[dict]:
    args = {"generation": 0, "iid": tid, "collections": 1, "heap_size": 1000, "collected": 0, "uncollectable": 0, "candidates": 0}
    return [
        begin_event(pid=pid, tid=tid, name=name, cat=cat, ts_us=ts, args=args),
        end_event(pid=pid, tid=tid, name=name, cat=cat, ts_us=ts + int(dur)),
    ]


# =============================================================================
# Local fixtures
# =============================================================================


@pytest.fixture
def make_trace_file(tmp_path: Path) -> Path:
    """Create a Chrome Trace JSON file with given events."""
    def _make(name: str, events: list[dict]) -> Path:
        path = tmp_path / name
        path.write_bytes(msgspec.json.encode(events))
        return path
    return _make


@pytest.fixture
def make_raw_file(tmp_path: Path) -> Path:
    """Create a file with raw (non-JSON) text content for invalid-JSON tests."""
    def _make(name: str, content: str) -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path
    return _make


@pytest.fixture
def run_combine():
    def _run(inputs: list[Path], output: Path | None = None, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "gcmon.cli", "combine"]
        cmd.extend(str(f) for f in inputs)
        if output:
            cmd += ["-o", str(output)]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(cmd, capture_output=True, text=True)
    return _run


@pytest.fixture
def combine_output(tmp_path: Path) -> Path:
    """Standard combined output path."""
    return tmp_path / "combined.json"


# =============================================================================
# Unit Tests for cmd_combine
# =============================================================================


def test_cmd_combine_basic(tmp_path: Path) -> None:
    from gcmon.commands import convert_cmd

    input_file = tmp_path / "input.json"
    input_file.write_bytes(msgspec.json.encode(make_event_pair("test", ts=100)))

    args = Namespace(inputs=[input_file], output=tmp_path / "output.json", verbose=1, normalize=False, input_format="chrome", output_format="chrome")
    assert convert_cmd.cmd_combine(args) == 0
    assert args.output.exists()


def test_cmd_combine_file_not_found(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    from gcmon.commands import convert_cmd

    args = Namespace(inputs=[tmp_path / "nonexistent.json"], output=tmp_path / "output.json", verbose=1, normalize=False, input_format="chrome", output_format="chrome")
    assert convert_cmd.cmd_combine(args) == 1
    assert "Error combining files" in caplog.text


def test_cmd_combine_invalid_json(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    from gcmon.commands import convert_cmd

    input_file = tmp_path / "invalid.json"
    input_file.write_text("not valid json")

    args = Namespace(inputs=[input_file], output=tmp_path / "output.json", verbose=1, normalize=False, input_format="chrome", output_format="chrome")
    assert convert_cmd.cmd_combine(args) == 1
    assert "Error combining files" in caplog.text


# =============================================================================
# Subprocess Tests - Combine Command
# =============================================================================


class TestCliCombine:
    def test_basic(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", make_event_pair("event1", ts=100))
        f2 = make_trace_file("trace2.json", make_event_pair("event2", ts=200))

        result = run_combine([f1, f2], output=combine_output, extra_args=["-v"])

        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_begin(begins[0], name="event1", ts=100)
        assert_is_begin(begins[1], name="event2", ts=200)

    def test_verbose_output(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", make_event_pair("event1", ts=100))

        result = run_combine([f1], output=combine_output, extra_args=["-v"])

        assert result.returncode == 0
        assert "Combining 1 file(s)" in result.stderr
        assert f"Input: {f1}" in result.stderr
        assert f"Output: {combine_output}" in result.stderr
        assert "Combine complete" in result.stderr

    def test_missing_file(self, run_combine, combine_output) -> None:
        result = run_combine([Path("nonexistent.json")], output=combine_output)
        assert result.returncode != 0
        assert "Error combining files" in result.stderr

    def test_invalid_json(self, make_raw_file, run_combine, combine_output) -> None:
        f = make_raw_file("invalid.json", "not valid json{{{")
        result = run_combine([f], output=combine_output)
        assert result.returncode != 0, result.stderr
        assert "Error combining files" in result.stderr
        assert "json" in result.stderr.lower()

    def test_multiple_files(self, make_trace_file, run_combine, combine_output) -> None:
        files = [
            make_trace_file(f"trace{i}.json", make_event_pair(f"event{i}", ts=i * 100))
            for i in range(1, 4)
        ]
        result = run_combine(files, output=combine_output)
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_begin(begins[0], name="event1", ts=100)
        assert_is_begin(begins[1], name="event2", ts=200)
        assert_is_begin(begins[2], name="event3", ts=300)


class TestCliCombineNormalize:
    """Tests for combine --normalize behavior."""

    def test_basic(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json",
            make_event_pair("event1", ts=1000) + make_event_pair("event2", ts=1100))
        f2 = make_trace_file("trace2.json",
            make_event_pair("event3", ts=5000) + make_event_pair("event4", ts=5200))

        result = run_combine([f1, f2], output=combine_output, extra_args=["--normalize", "-v"])

        assert result.returncode == 0
        assert "Normalizing timestamps: yes" in result.stderr
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_begin(begins[0], name="event1", ts=0)
        assert_is_begin(begins[1], name="event2", ts=100)
        assert_is_begin(begins[2], name="event3", ts=0)
        assert_is_begin(begins[3], name="event4", ts=200)

    def test_preserves_relative_timing(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json",
            make_event_pair("event1", ts=1000) + make_event_pair("event2", ts=1050)
            + make_event_pair("event3", ts=1200) + make_event_pair("event4", ts=1700))
        result = run_combine([f1], output=combine_output, extra_args=["--normalize"])

        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert [e["ts"] for e in begins] == [0, 50, 200, 700]

    def test_multiple_files_independent(self, make_trace_file, run_combine, combine_output) -> None:
        files = [
            make_trace_file("trace1.json",
                make_event_pair("f1_e1", ts=100) + make_event_pair("f1_e2", ts=200)),
            make_trace_file("trace2.json",
                make_event_pair("f2_e1", ts=10000) + make_event_pair("f2_e2", ts=10100)),
            make_trace_file("trace3.json",
                make_event_pair("f3_e1", ts=50000) + make_event_pair("f3_e2", ts=50050)),
        ]
        result = run_combine(files, output=combine_output, extra_args=["--normalize"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert [e["ts"] for e in begins] == [0, 100, 0, 100, 0, 50]

    def test_without_normalize(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json",
            make_event_pair("event1", ts=1000) + make_event_pair("event2", ts=1100))
        f2 = make_trace_file("trace2.json",
            make_event_pair("event3", ts=5000) + make_event_pair("event4", ts=5200))
        result = run_combine([f1, f2], output=combine_output)
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert [e["ts"] for e in begins] == [1000, 1100, 5000, 5200]

    def test_with_metadata(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", [
            process_meta(pid=123, name="process_name"),
            *make_event_pair("event1", ts=1000, pid=123),
            thread_meta(pid=123, tid=1, name="thread_name"),
            *make_event_pair("event2", ts=1500, pid=123),
        ])
        result = run_combine([f1], output=combine_output, extra_args=["--normalize"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_process_meta(data[0], pid=123)
        assert_is_begin(begins[0], name="event1", ts=0, pid=123)
        assert_is_thread_meta(data[3], pid=123, tid=1)
        assert_is_begin(begins[1], name="event2", ts=500, pid=123)

    def test_empty_file(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", [])
        f2 = make_trace_file("trace2.json", make_event_pair("event1", ts=1000))
        result = run_combine([f1, f2], output=combine_output, extra_args=["--normalize"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_begin(begins[0], name="event1", ts=0)
        assert len(data) == 2  # begin + end

    def test_metadata_only(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", [
            process_meta(pid=123, name="process_name"),
            thread_meta(pid=123, tid=1, name="thread_name"),
        ])
        result = run_combine([f1], output=combine_output, extra_args=["--normalize"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        assert_is_process_meta(data[0], pid=123)
        assert_is_thread_meta(data[1], pid=123, tid=1)

    def test_short_option(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", make_event_pair("event1", ts=5000))
        result = run_combine([f1], output=combine_output, extra_args=["-n"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_begin(begins[0], name="event1", ts=0)

    def test_mixed_metadata_and_events(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace1.json", [
            process_meta(pid=123, name="process_name"),
            *make_event_pair("event1", ts=100, pid=123),
            *make_event_pair("event2", ts=150, pid=123),
        ])
        f2 = make_trace_file("trace2.json", [
            process_meta(pid=456, name="process_name"),
            *make_event_pair("event3", ts=1000, pid=456),
            *make_event_pair("event4", ts=1200, pid=456),
        ])
        result = run_combine([f1, f2], output=combine_output, extra_args=["--normalize"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        begins = [e for e in data if e["ph"] == "B"]
        assert_is_process_meta(data[0], pid=123)
        assert_is_begin(begins[0], name="event1", ts=0, pid=123)
        assert_is_begin(begins[1], name="event2", ts=50, pid=123)
        assert_is_process_meta(data[5], pid=456)
        assert_is_begin(begins[2], name="event3", ts=0, pid=456)
        assert_is_begin(begins[3], name="event4", ts=200, pid=456)


# =============================================================================
# JSONL fixtures
# =============================================================================


@pytest.fixture
def make_jsonl_file(tmp_path: Path) -> Path:
    """Create a JSONL file with given raw GC stats records."""
    def _make(name: str, records: list[dict]) -> Path:
        path = tmp_path / name
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        return path
    return _make


def assert_valid_jsonl(path: Path) -> list[dict]:
    """Validate that a file contains valid JSONL (one JSON object per line)."""
    assert path.exists(), f"File {path} does not exist"
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            assert isinstance(data, dict), f"Line {idx} is not a JSON object"
            records.append(data)
    return records


# =============================================================================
# Subprocess Tests - JSONL to Chrome
# =============================================================================


class TestCliCombineJsonlToChrome:
    def test_basic(self, make_jsonl_file, run_combine, combine_output) -> None:
        f1 = make_jsonl_file("data1.jsonl", [create_jsonl_record()])

        result = run_combine([f1], output=combine_output, extra_args=["--input-format", "jsonl", "-v"])

        assert result.returncode == 0
        assert "Input format: jsonl" in result.stderr
        assert "Output format: chrome" in result.stderr
        data = assert_valid_chrome_trace_format(combine_output)
        # 1 JSONL record → 1 GC Pause (B) + 1 G0 counter (C) + process_name + thread_name
        assert_is_process_meta(next(e for e in data if e["name"] == "process_name"), pid=123)
        assert_is_thread_meta(next(e for e in data if e["name"] == "thread_name"), pid=123, tid=1)
        assert_is_begin(next(e for e in data if e["ph"] == "B"), name="GC Pause (gen=0)")
        assert_is_counter(next(e for e in data if e["ph"] == "C"), name="G0")

    def test_multiple_files(self, make_jsonl_file, run_combine, combine_output) -> None:
        f1 = make_jsonl_file("data1.jsonl", [create_jsonl_record(pid=123, tid=1, gen=0)])
        f2 = make_jsonl_file("data2.jsonl", [create_jsonl_record(pid=456, tid=2, gen=1)])

        result = run_combine([f1, f2], output=combine_output, extra_args=["--input-format", "jsonl"])

        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        # 2 files → 2 process_meta + 2 thread_meta + 2 B + 2 C
        assert len([e for e in data if e["ph"] == "B"]) == 2
        assert len([e for e in data if e["ph"] == "C"]) == 2
        assert len([e for e in data if e["name"] == "process_name"]) == 2
        assert len([e for e in data if e["name"] == "thread_name"]) == 2

    def test_normalize(self, make_jsonl_file, run_combine, combine_output) -> None:
        f1 = make_jsonl_file("data.jsonl", [
            create_jsonl_record(ts_start=5_000_000, ts_stop=6_000_000, collections=1, collected=100),
            create_jsonl_record(ts_start=10_000_000, ts_stop=11_000_000, collections=2, collected=200),
        ])

        result = run_combine([f1], output=combine_output, extra_args=["--input-format", "jsonl", "--normalize", "-v"])

        assert result.returncode == 0
        assert "Normalizing timestamps: yes" in result.stderr
        data = assert_valid_chrome_trace_format(combine_output)
        pause_events = [e for e in data if e["ph"] == "B"]
        assert len(pause_events) == 2
        assert pause_events[0]["ts"] == 0


# =============================================================================
# Subprocess Tests - JSONL to JSONL
# =============================================================================


class TestCliCombineJsonlToJsonl:
    def test_basic(self, make_jsonl_file, tmp_path) -> None:
        f1 = make_jsonl_file("data1.jsonl", [create_jsonl_record()])
        output = tmp_path / "combined.jsonl"

        cmd = [sys.executable, "-m", "gcmon.cli", "combine", str(f1), "-o", str(output),
               "--input-format", "jsonl", "--output-format", "jsonl"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 0
        records = assert_valid_jsonl(output)
        assert records[0]["pid"] == 123
        assert records[0]["ts_start"] == 1_000_000

    def test_multiple_files(self, make_jsonl_file, tmp_path) -> None:
        f1 = make_jsonl_file("data1.jsonl", [create_jsonl_record(pid=123, tid=1, gen=0)])
        f2 = make_jsonl_file("data2.jsonl", [create_jsonl_record(pid=456, tid=2, gen=1)])
        output = tmp_path / "combined.jsonl"

        cmd = [sys.executable, "-m", "gcmon.cli", "combine", str(f1), str(f2), "-o", str(output),
               "--input-format", "jsonl", "--output-format", "jsonl"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 0
        records = assert_valid_jsonl(output)
        assert len(records) == 2
        assert records[0]["pid"] == 123
        assert records[1]["pid"] == 456

    def test_normalize(self, make_jsonl_file, tmp_path) -> None:
        f1 = make_jsonl_file("data.jsonl", [
            create_jsonl_record(ts_start=5_000_000, ts_stop=6_000_000, collections=1, collected=100),
            create_jsonl_record(ts_start=10_000_000, ts_stop=11_000_000, collections=2, collected=200),
        ])
        output = tmp_path / "combined.jsonl"

        cmd = [sys.executable, "-m", "gcmon.cli", "combine", str(f1), "-o", str(output),
               "--input-format", "jsonl", "--output-format", "jsonl", "--normalize"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 0
        records = assert_valid_jsonl(output)
        assert records[0]["ts_start"] == 0
        assert records[1]["ts_start"] == 5_000_000
        assert records[0]["pid"] == 123
        assert records[1]["pid"] == 123


# =============================================================================
# Subprocess Tests - Format Validation
# =============================================================================


class TestCliCombineFormatValidation:
    """Tests for format validation (chrome→jsonl should error)."""

    def test_chrome_to_jsonl_error(self, make_trace_file, combine_output, run_combine) -> None:
        f1 = make_trace_file("trace.json", [make_event_pair("event1", ts=100)])
        result = run_combine([f1], output=combine_output, extra_args=["--output-format", "jsonl"])
        assert result.returncode == 1
        assert "not supported" in result.stderr.lower()

    def test_explicit_chrome_to_chrome(self, make_trace_file, run_combine, combine_output) -> None:
        f1 = make_trace_file("trace.json", make_event_pair("event1", ts=100))
        result = run_combine([f1], output=combine_output, extra_args=["--input-format", "chrome", "--output-format", "chrome"])
        assert result.returncode == 0
        data = assert_valid_chrome_trace_format(combine_output)
        assert_is_begin(data[0], name="event1", ts=100)


class TestCliCombineHelp:
    def test_shows_normalize_option(self, run_combine) -> None:
        result = run_combine([], extra_args=["--help"])
        assert "--normalize" in result.stdout or "-n" in result.stdout
        assert "Normalize" in result.stdout

    def test_shows_format_options(self, run_combine) -> None:
        result = run_combine([], extra_args=["--help"])
        assert "--input-format" in result.stdout
        assert "--output-format" in result.stdout
        assert "jsonl" in result.stdout
        assert "chrome" in result.stdout
