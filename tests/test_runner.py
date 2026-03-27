"""Tests for GCRunner subprocess runner."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gc_monitor._runner import GCRunner
from gc_monitor._process_terminator import terminate_process


class TestGCRunnerInit:
    """Tests for GCRunner initialization."""

    def test_init_script_mode(self) -> None:
        """Test initialization in script mode."""
        runner = GCRunner("my_script.py", is_module=False)
        assert runner._target == "my_script.py"
        assert runner._is_module is False
        assert runner._passthrough_args == []
        assert runner._env is None

    def test_init_module_mode(self) -> None:
        """Test initialization in module mode."""
        runner = GCRunner("http.server", is_module=True)
        assert runner._target == "http.server"
        assert runner._is_module is True
        assert runner._passthrough_args == []

    def test_init_with_passthrough_args(self) -> None:
        """Test initialization with passthrough arguments."""
        runner = GCRunner(
            "my_script.py",
            is_module=False,
            passthrough_args=["arg1", "arg2", "--flag"],
        )
        assert runner._passthrough_args == ["arg1", "arg2", "--flag"]

    def test_init_with_custom_env(self) -> None:
        """Test initialization with custom environment."""
        custom_env = {"CUSTOM_VAR": "value"}
        runner = GCRunner("my_script.py", is_module=False, env=custom_env)
        assert runner._env == custom_env

    def test_init_default_values(self) -> None:
        """Test default initialization values."""
        runner = GCRunner("my_script.py")
        assert runner._is_module is False
        assert runner._passthrough_args == []
        assert runner._process is None


class TestGCRunnerValidateTarget:
    """Tests for GCRunner target validation."""

    def test_validate_script_exists(self, tmp_path: Path) -> None:
        """Test validation when script file exists."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("print('hello')")

        runner = GCRunner(str(script_file), is_module=False)
        # Should not raise
        runner._validate_target()

    def test_validate_script_not_found(self) -> None:
        """Test validation when script file doesn't exist."""
        runner = GCRunner("/nonexistent/script.py", is_module=False)
        with pytest.raises(FileNotFoundError, match="Script not found"):
            runner._validate_target()

    def test_validate_target_is_directory(self, tmp_path: Path) -> None:
        """Test validation when target is a directory."""
        runner = GCRunner(str(tmp_path), is_module=False)
        with pytest.raises(ValueError, match="Target is not a file"):
            runner._validate_target()

    def test_validate_module_name_empty(self) -> None:
        """Test validation when module name is empty."""
        runner = GCRunner("   ", is_module=True)
        with pytest.raises(ValueError, match="Module name cannot be empty"):
            runner._validate_target()

    def test_validate_module_name_valid(self) -> None:
        """Test validation when module name is valid."""
        runner = GCRunner("http.server", is_module=True)
        # Should not raise (doesn't check if module actually exists)
        runner._validate_target()


class TestGCRunnerBuildCommand:
    """Tests for GCRunner command building."""

    def test_build_command_script_mode(self) -> None:
        """Test command building in script mode."""
        runner = GCRunner("my_script.py", is_module=False)
        cmd = runner._build_command()

        assert cmd[0] == sys.executable
        assert cmd[1].endswith("my_script.py")
        assert len(cmd) == 2

    def test_build_command_script_mode_with_args(self) -> None:
        """Test command building in script mode with arguments."""
        runner = GCRunner(
            "my_script.py",
            is_module=False,
            passthrough_args=["arg1", "arg2"],
        )
        cmd = runner._build_command()

        assert cmd[0] == sys.executable
        assert cmd[1].endswith("my_script.py")
        assert cmd[2:] == ["arg1", "arg2"]

    def test_build_command_module_mode(self) -> None:
        """Test command building in module mode."""
        runner = GCRunner("http.server", is_module=True)
        cmd = runner._build_command()

        assert cmd[0] == sys.executable
        assert cmd[1] == "-m"
        assert cmd[2] == "http.server"
        assert len(cmd) == 3

    def test_build_command_module_mode_with_args(self) -> None:
        """Test command building in module mode with arguments."""
        runner = GCRunner(
            "http.server",
            is_module=True,
            passthrough_args=["8080"],
        )
        cmd = runner._build_command()

        assert cmd[0] == sys.executable
        assert cmd[1] == "-m"
        assert cmd[2] == "http.server"
        assert cmd[3] == "8080"

    def test_build_command_resolves_absolute_path(self, tmp_path: Path) -> None:
        """Test that script path is resolved to absolute path."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("print('hello')")

        runner = GCRunner(str(script_file), is_module=False)
        cmd = runner._build_command()

        # Path should be absolute
        assert Path(cmd[1]).is_absolute()


class TestGCRunnerBuildEnv:
    """Tests for GCRunner environment building."""

    def test_build_env_default(self) -> None:
        """Test default environment building."""
        runner = GCRunner("my_script.py", is_module=False)
        env = runner._build_env()

        # Should contain current environment
        assert "PATH" in env or "SystemRoot" in env

    def test_build_env_with_custom_env(self) -> None:
        """Test environment building with custom variables."""
        custom_env = {"CUSTOM_VAR": "value", "ANOTHER_VAR": "test"}
        runner = GCRunner("my_script.py", is_module=False, env=custom_env)
        env = runner._build_env()

        assert env["CUSTOM_VAR"] == "value"
        assert env["ANOTHER_VAR"] == "test"

    def test_build_env_merges_with_os_env(self) -> None:
        """Test that custom env merges with os.environ."""
        custom_env = {"CUSTOM_VAR": "value"}
        runner = GCRunner("my_script.py", is_module=False, env=custom_env)
        env = runner._build_env()

        # Should contain both custom and OS environment
        assert "CUSTOM_VAR" in env
        assert "PATH" in env or "SystemRoot" in env


class TestGCRunnerStart:
    """Tests for GCRunner start method."""

    def test_start_script_mode(self, tmp_path: Path) -> None:
        """Test starting subprocess in script mode."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(0.5)")

        runner = GCRunner(str(script_file), is_module=False)
        try:
            pid = runner.start()
            assert pid is not None
            assert runner.pid is not None
            assert runner.is_running
        finally:
            if runner.is_running:
                runner.terminate()

    def test_start_module_mode(self) -> None:
        """Test starting subprocess in module mode."""
        # Use a module that runs for a bit (this one runs a simple loop)
        runner = GCRunner(
            "gc_monitor",
            is_module=True,
            passthrough_args=["--help"],
        )
        try:
            pid = runner.start()
            assert pid is not None
            assert runner.pid is not None
        finally:
            if runner.is_running:
                runner.terminate()

    def test_start_script_not_found(self) -> None:
        """Test starting with non-existent script."""
        runner = GCRunner("/nonexistent/script.py", is_module=False)
        with pytest.raises(FileNotFoundError):
            runner.start()

    def test_start_exits_immediately(self, tmp_path: Path) -> None:
        """Test handling of subprocess that exits immediately."""
        # Script with syntax error
        script_file = tmp_path / "bad_script.py"
        script_file.write_text("invalid syntax !!!")

        runner = GCRunner(str(script_file), is_module=False)
        with pytest.raises(RuntimeError, match="exited immediately"):
            runner.start()

    def test_start_sets_process(self, tmp_path: Path) -> None:
        """Test that start() sets the process attribute."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(0.5)")

        runner = GCRunner(str(script_file), is_module=False)
        try:
            runner.start()
            assert runner._process is not None
            assert isinstance(runner._process, subprocess.Popen)
        finally:
            if runner.is_running:
                runner.terminate()


class TestGCRunnerProperties:
    """Tests for GCRunner properties."""

    def test_process_not_started(self) -> None:
        """Test process property when not started."""
        runner = GCRunner("my_script.py")
        assert runner.process is None

    def test_pid_not_started(self) -> None:
        """Test pid property when not started."""
        runner = GCRunner("my_script.py")
        assert runner.pid is None

    def test_is_running_not_started(self) -> None:
        """Test is_running property when not started."""
        runner = GCRunner("my_script.py")
        assert runner.is_running is False

    def test_returncode_not_started(self) -> None:
        """Test returncode property when not started."""
        runner = GCRunner("my_script.py")
        assert runner.returncode is None

    def test_is_running_after_start(self, tmp_path: Path) -> None:
        """Test is_running after starting subprocess."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(0.5)")

        runner = GCRunner(str(script_file), is_module=False)
        try:
            runner.start()
            assert runner.is_running is True
        finally:
            if runner.is_running:
                runner.terminate()

    def test_returncode_after_terminate(self, tmp_path: Path) -> None:
        """Test returncode after terminating subprocess."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(0.5)")

        runner = GCRunner(str(script_file), is_module=False)
        runner.start()
        runner.terminate()
        assert runner.returncode is not None


class TestGCRunnerTerminate:
    """Tests for GCRunner terminate method."""

    def test_terminate_not_started(self) -> None:
        """Test terminate when subprocess not started."""
        runner = GCRunner("my_script.py")
        stdout, stderr = runner.terminate()
        assert stdout == b""
        assert stderr == b""

    def test_terminate_running_process(self, tmp_path: Path) -> None:
        """Test terminating a running process."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(10)")

        runner = GCRunner(str(script_file), is_module=False)
        runner.start()

        try:
            stdout, stderr = runner.terminate(verbose=False)
            assert isinstance(stdout, bytes)
            assert isinstance(stderr, bytes)
            assert runner.is_running is False
        except Exception:
            # Ensure cleanup even if test fails
            if runner.is_running:
                runner.terminate()
            raise

    def test_terminate_already_terminated(self, tmp_path: Path) -> None:
        """Test terminating an already terminated process."""
        script_file = tmp_path / "test_script.py"
        # Script that runs long enough to start, then exits
        script_file.write_text("import time; time.sleep(0.05); print('quick exit')")

        runner = GCRunner(str(script_file), is_module=False)
        runner.start()

        # Wait for process to exit naturally (poll with timeout)
        import time
        start_time = time.time()
        while runner.is_running and time.time() - start_time < 2.0:
            time.sleep(0.05)

        # Should not raise
        stdout, stderr = runner.terminate()
        assert isinstance(stdout, bytes)


class TestGCRunnerContextManager:
    """Tests for GCRunner context manager."""

    def test_context_manager_enters(self, tmp_path: Path) -> None:
        """Test context manager __enter__."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(0.5)")

        with GCRunner(str(script_file), is_module=False) as runner:
            assert runner.is_running is True
            assert runner.pid is not None

    def test_context_manager_exits(self, tmp_path: Path) -> None:
        """Test context manager __exit__ terminates process."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(10)")

        with GCRunner(str(script_file), is_module=False) as runner:
            assert runner.is_running is True

        # After context exit, process should be terminated
        assert runner.is_running is False

    def test_context_manager_with_exception(
        self, tmp_path: Path
    ) -> None:
        """Test context manager cleans up on exception."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(10)")

        try:
            with GCRunner(str(script_file), is_module=False) as runner:
                assert runner.is_running is True
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Process should still be terminated
        assert runner.is_running is False


class TestGCRunnerTerminateUsesTerminateProcess:
    """Tests to verify terminate() uses terminate_process correctly."""

    def test_terminate_calls_terminate_process(
        self, tmp_path: Path
    ) -> None:
        """Test that terminate() calls terminate_process."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("import time; time.sleep(10)")

        runner = GCRunner(str(script_file), is_module=False)
        runner.start()

        try:
            with patch(
                "gc_monitor._runner.terminate_process"
            ) as mock_terminate:
                mock_terminate.return_value = (b"stdout", b"stderr")

                with patch(
                    "gc_monitor._runner.log_process_output"
                ) as mock_log:
                    stdout, stderr = runner.terminate(verbose=True)

                    mock_terminate.assert_called_once()
                    mock_log.assert_called_once()
        finally:
            if runner.is_running:
                runner.terminate()
