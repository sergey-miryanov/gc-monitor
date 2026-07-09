"""Tests for process termination utilities."""

import os
import signal
import subprocess
from unittest.mock import patch

import pytest

from gcmon.utils.process_terminator import _is_signal_exit_code, terminate_process


@pytest.fixture
def unix_terminator(mock_process, mock_logger):
    mock_process.returncode = None
    mock_process.poll.side_effect = lambda: mock_process.returncode
    with (
        patch.object(os, "name", "posix"),
        patch("gcmon.utils.process_terminator._logger", mock_logger),
    ):
        yield mock_process


@pytest.fixture
def nt_terminator(mock_process, mock_logger):
    mock_process.returncode = None
    mock_process.poll.side_effect = lambda: mock_process.returncode
    with (
        patch.object(os, "name", "nt"),
        patch("gcmon.utils.process_terminator._logger", mock_logger),
    ):
        yield mock_process


@pytest.mark.skipif(os.name == "nt", reason="Unix-specific tests")
class TestTerminateProcessUnix:
    """Unix-specific terminate_process tests."""

    def test_graceful_exit(self, unix_terminator):
        mock_process = unix_terminator
        mock_process.poll.side_effect = [None, 0]

        with patch.object(mock_process, "send_signal") as mock_send_signal:
            result = terminate_process(
                process=mock_process,
                graceful_timeout=5.0,
                force_timeout=2.0,
            )

            mock_send_signal.assert_called_once_with(signal.SIGINT)
            mock_process.communicate.assert_called_once_with(timeout=5.0)
            assert result == (b"stdout data", b"stderr data")

    def test_timeout_then_sigterm(self, unix_terminator):
        mock_process = unix_terminator
        mock_process.poll.side_effect = [None, None, 0]

        def _communicate_side_effect(timeout=None):
            if timeout == 5.0:
                raise subprocess.TimeoutExpired(cmd="test", timeout=5.0)
            mock_process.returncode = 0
            return (b"stdout after sigterm", b"stderr after sigterm")

        mock_process.communicate.side_effect = _communicate_side_effect

        with patch.object(mock_process, "send_signal") as mock_send_signal:
            result = terminate_process(
                process=mock_process,
                graceful_timeout=5.0,
                force_timeout=2.0,
            )

            mock_send_signal.assert_called_once_with(signal.SIGINT)
            mock_process.terminate.assert_called_once()
            assert result == (b"stdout after sigterm", b"stderr after sigterm")

    def test_timeout_then_sigkill(self, unix_terminator):
        mock_process = unix_terminator
        mock_process.poll.side_effect = [None, None, None, -9]
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            (b"stdout after sigkill", b"stderr after sigkill"),
        ]

        with patch.object(mock_process, "send_signal") as mock_send_signal:
            with patch.object(mock_process, "kill") as mock_kill:
                result = terminate_process(
                    process=mock_process,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                mock_send_signal.assert_called_once_with(signal.SIGINT)
                mock_process.terminate.assert_called_once()
                mock_kill.assert_called_once()
                assert result == (b"stdout after sigkill", b"stderr after sigkill")

    def test_zombie(self, unix_terminator):
        mock_process = unix_terminator
        mock_process.poll.side_effect = [None, None, None, None]
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            (b"final output", b""),
        ]

        with patch.object(mock_process, "send_signal"):
            with patch.object(mock_process, "kill"):
                result = terminate_process(
                    process=mock_process,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                assert mock_process.communicate.call_count == 4
                mock_process.communicate.assert_called_with(timeout=None)
                assert result == (b"final output", b"")


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific tests")
class TestTerminateProcessWindows:
    """Windows-specific terminate_process tests."""

    def test_graceful_exit(self, nt_terminator):
        mock_process = nt_terminator
        mock_process.poll.side_effect = [None, 0]

        with patch.object(mock_process, "send_signal") as mock_send_signal:
            result = terminate_process(
                process=mock_process,
                graceful_timeout=5.0,
                force_timeout=2.0,
            )

            mock_send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
            mock_process.communicate.assert_called_once_with(timeout=5.0)
            assert result == (b"stdout data", b"stderr data")

    def test_timeout_then_kill(self, nt_terminator):
        mock_process = nt_terminator
        mock_process.poll.side_effect = [None, None, None, -1]
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            (b"stdout after kill", b"stderr after kill"),
        ]

        with patch.object(mock_process, "send_signal") as mock_send_signal:
            with patch.object(mock_process, "kill") as mock_kill:
                result = terminate_process(
                    process=mock_process,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                mock_send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
                mock_process.terminate.assert_called_once()
                mock_kill.assert_called_once()
                assert result == (b"stdout after kill", b"stderr after kill")

    def test_zombie(self, nt_terminator):
        mock_process = nt_terminator
        mock_process.poll.side_effect = [None, None, None, None]
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            (b"final output", b""),
            (b"final output", b""),
        ]

        with patch.object(mock_process, "send_signal"):
            with patch.object(mock_process, "kill"):
                result = terminate_process(
                    process=mock_process,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                assert mock_process.communicate.call_count == 4
                mock_process.communicate.assert_called_with(timeout=None)
                assert result == (b"final output", b"")


class TestTerminateProcessCommon:
    """Platform-independent terminate_process tests."""

    def test_verbose_logging(self, mock_process, mock_logger):
        mock_process.returncode = None
        mock_process.poll.side_effect = lambda: mock_process.returncode

        with patch("gcmon.utils.process_terminator._logger", mock_logger):
            with patch.object(mock_process, "send_signal"):
                terminate_process(
                    process=mock_process,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                assert mock_logger.debug.call_count >= 2

    def test_signal_failure(self, mock_process, mock_logger):
        mock_process.returncode = None
        mock_process.poll.side_effect = lambda: mock_process.returncode

        with patch("gcmon.utils.process_terminator._logger", mock_logger):
            with patch.object(mock_process, "send_signal", side_effect=ProcessLookupError("Process not found")):
                result = terminate_process(
                    process=mock_process,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                mock_logger.warning.assert_called()
                mock_process.communicate.assert_called()
                assert result == (b"stdout data", b"stderr data")

    def test_default_logger(self, mock_process):
        mock_process.returncode = None
        mock_process.poll.side_effect = lambda: mock_process.returncode

        with patch.object(mock_process, "send_signal"):
            result = terminate_process(
                process=mock_process,
                graceful_timeout=5.0,
                force_timeout=2.0,
            )
            assert result == (b"stdout data", b"stderr data")


class TestCrossPlatform:
    """Cross-platform compatibility tests."""

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
    def test_windows_signal_constants(self, mock_process) -> None:
        assert hasattr(signal, "CTRL_BREAK_EVENT")

    @pytest.mark.skipif(os.name != "posix", reason="Unix-specific test")
    def test_unix_signal_constants(self, mock_process) -> None:
        assert hasattr(signal, "SIGINT")
        assert hasattr(signal, "SIGTERM")
        assert hasattr(signal, "SIGKILL")

    def test_os_name_detection(self) -> None:
        assert os.name in ("nt", "posix")


class TestIsSignalExitCode:
    """Direct tests for _is_signal_exit_code()."""

    def test_windows_status_control_c_exit(self) -> None:
        assert _is_signal_exit_code(0xC000013A) is True

    def test_unix_negative_sigint(self) -> None:
        assert _is_signal_exit_code(-signal.SIGINT) is True

    def test_unix_negative_sigterm(self) -> None:
        assert _is_signal_exit_code(-signal.SIGTERM) is True

    def test_normal_zero_exit(self) -> None:
        assert _is_signal_exit_code(0) is False

    def test_normal_positive_exit(self) -> None:
        assert _is_signal_exit_code(1) is False

    def test_arbitrary_nonzero(self) -> None:
        assert _is_signal_exit_code(42) is False
        assert _is_signal_exit_code(255) is False

    def test_unix_positive_signal_number_not_a_signal_exit(self) -> None:
        assert _is_signal_exit_code(signal.SIGINT) is False
        assert _is_signal_exit_code(signal.SIGTERM) is False

    def test_windows_status_value_with_different_bits(self) -> None:
        assert _is_signal_exit_code(0xC000013B) is False
        assert _is_signal_exit_code(0xC0000000) is False
