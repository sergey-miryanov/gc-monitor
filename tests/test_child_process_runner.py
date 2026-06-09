import os
import subprocess
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from gcmon.child_process_runner import ChildProcess, ChildProcessRunner, ProcessStdoutReader


@pytest.fixture
def mock_popen():
    popen = Mock(spec=subprocess.Popen)
    popen.pid = 99999
    popen.poll.return_value = None
    popen.stdout = None
    return popen


@pytest.fixture
def runner(tmp_path):
    script = tmp_path / "test_script.py"
    script.write_text("print('hello')")
    return ChildProcessRunner(str(script))


@pytest.fixture
def module_runner():
    return ChildProcessRunner("my_module", is_module=True)


@pytest.fixture
def runner_with_args(runner):
    runner._passthrough_args = ["--verbose", "--output=file.json"]
    return runner


class TestChildProcess:
    def test_pid(self):
        cp = ChildProcess(pid=12345)
        assert cp.pid == 12345


class TestChildProcessRunnerInit:
    def test_stores_target(self, runner):
        assert runner._target.endswith("test_script.py")

    def test_module_mode(self, module_runner):
        assert module_runner._target == "my_module"
        assert module_runner._is_module

    def test_passthrough_args(self, runner_with_args):
        assert runner_with_args._passthrough_args == ["--verbose", "--output=file.json"]

    def test_custom_env(self):
        runner = ChildProcessRunner("script.py", env={"VAR": "val"})
        assert runner._env == {"VAR": "val"}

    def test_default_values(self):
        runner = ChildProcessRunner("script.py")
        assert runner._is_module is False
        assert runner._passthrough_args == []
        assert runner._process is None


class TestValidateTarget:
    def test_script_exists(self, runner):
        runner._validate_target()

    def test_script_not_found(self):
        runner = ChildProcessRunner("/nonexistent/script.py")
        with pytest.raises(FileNotFoundError, match="Script not found"):
            runner._validate_target()

    def test_not_a_file(self, tmp_path):
        d = tmp_path / "a_directory"
        d.mkdir()
        runner = ChildProcessRunner(str(d))
        with pytest.raises(ValueError, match="not a file"):
            runner._validate_target()

    def test_module_valid(self, module_runner):
        module_runner._validate_target()

    def test_module_empty(self):
        runner = ChildProcessRunner("  ", is_module=True)
        with pytest.raises(ValueError, match="cannot be empty"):
            runner._validate_target()


class TestBuildCommand:
    def test_script_mode(self, runner, tmp_path):
        cmd = runner._build_command()
        assert cmd[0] == subprocess.sys.executable
        assert "-u" in cmd
        script_path = str((tmp_path / "test_script.py").resolve())
        assert script_path in cmd
        assert "-m" not in cmd

    def test_module_mode(self, module_runner):
        cmd = module_runner._build_command()
        assert "-m" in cmd
        assert "my_module" in cmd

    def test_with_passthrough_args(self, runner_with_args):
        cmd = runner_with_args._build_command()
        assert "--verbose" in cmd
        assert "--output=file.json" in cmd

    def test_module_mode_with_args(self):
        runner = ChildProcessRunner("http.server", is_module=True, passthrough_args=["8080"])
        cmd = runner._build_command()
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "http.server"
        assert cmd[-1] == "8080"


class TestBuildEnv:
    def test_inherits_os_environ(self, runner):
        with patch.dict(os.environ, {"EXISTING": "value"}, clear=True):
            env = runner._build_env()
            assert env["EXISTING"] == "value"

    def test_merges_custom_env(self):
        runner = ChildProcessRunner("script.py", env={"CUSTOM": "val"})
        with patch.dict(os.environ, {"BASE": "base_val"}, clear=True):
            env = runner._build_env()
            assert env["BASE"] == "base_val"
            assert env["CUSTOM"] == "val"


class TestProperties:
    def test_process_none_before_start(self, runner):
        assert runner.process is None

    def test_pid_none_before_start(self, runner):
        assert runner.pid is None

    def test_is_running_false_before_start(self, runner):
        assert runner.is_running is False

    def test_returncode_none_before_start(self, runner):
        assert runner.returncode is None

    def test_is_running_true_when_running(self, runner, mock_popen):
        runner._process = mock_popen
        assert runner.is_running is True

    def test_returncode_after_terminate(self, runner, mock_popen):
        mock_popen.poll.return_value = 0
        runner._process = mock_popen
        assert runner.returncode == 0


class TestStart:
    def test_spawns_subprocess(self, runner, mock_popen):
        with patch("subprocess.Popen", return_value=mock_popen) as mock_popen_cls:
            with patch("gcmon.child_process_runner.ProcessStdoutReader"):
                result = runner.start()

        assert isinstance(result, ChildProcess)
        assert result.pid == 99999
        mock_popen_cls.assert_called_once()

    def test_immediate_exit_raises(self, runner, mock_popen):
        mock_popen.poll.return_value = 0
        mock_popen.communicate.return_value = (b"output", None)

        with patch("subprocess.Popen", return_value=mock_popen):
            with pytest.raises(RuntimeError, match="exited immediately"):
                runner.start()

    def test_os_error_raises(self, runner):
        with patch("subprocess.Popen", side_effect=OSError("permission denied")):
            with pytest.raises(RuntimeError, match="Failed to start"):
                runner.start()


class TestTerminate:
    def test_stdout_thread_stopped(self, runner):
        thread = Mock(spec=ProcessStdoutReader)
        runner._process = Mock(spec=subprocess.Popen)
        runner._stdout_thread = thread

        with patch(
            "gcmon.child_process_runner.terminate_process",
            return_value=(b"stdout", b"stderr"),
        ):
            with patch("gcmon.child_process_runner.log_process_output"):
                runner.terminate()

        thread.stop.assert_called_once()
        assert runner._stdout_thread is None

    def test_terminates_process(self, runner):
        runner._process = Mock(spec=subprocess.Popen)

        with patch(
            "gcmon.child_process_runner.terminate_process",
            return_value=(b"stdout", b"stderr"),
        ) as mock_term:
            with patch("gcmon.child_process_runner.log_process_output"):
                runner.terminate()

        mock_term.assert_called_once()

    def test_no_process_returns_empty(self, runner):
        result = runner.terminate()
        assert result == b""


class TestClose:
    def test_close_delegates_to_terminate(self, runner):
        runner._process = Mock()

        with patch.object(runner, "terminate") as mock_term:
            runner.close()
            mock_term.assert_called_once()


class TestContextManager:
    def test_enters_and_exits(self, runner, mock_popen):
        runner._process = mock_popen
        with patch.object(runner, "terminate") as mock_term:
            with runner:
                assert runner.is_running
            mock_term.assert_called_once()

    def test_cleanup_on_exception(self, runner, mock_popen):
        runner._process = mock_popen
        with patch.object(runner, "terminate") as mock_term:
            try:
                with runner:
                    raise ValueError("test error")
            except ValueError:
                pass
            mock_term.assert_called_once()


class TestProcessStdoutReader:
    def test_start(self):
        process = Mock(spec=subprocess.Popen)
        process.stdout = MagicMock()
        process.stdout.readline.return_value = b"data\n"
        reader = ProcessStdoutReader(process)
        reader.start()
        assert reader._thread.is_alive()
        reader.stop()

    def test_stop(self):
        process = Mock(spec=subprocess.Popen)
        process.stdout = MagicMock()
        process.stdout.readline.return_value = b"data\n"
        reader = ProcessStdoutReader(process)
        reader.start()
        reader.stop()
        assert not reader._thread.is_alive()
