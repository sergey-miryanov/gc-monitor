"""Tests for process termination utilities."""

import logging
import os
import signal
import subprocess
import sys
from typing import Any
from unittest.mock import Mock, patch

import pytest

from gc_monitor._process_terminator import log_process_output, terminate_process


class TestTerminateProcess:
    """Tests for terminate_process function."""

    @pytest.fixture
    def mock_process(self) -> Mock:
        """Create a mock subprocess.Popen instance."""
        process = Mock(spec=subprocess.Popen)
        process.pid = 12345
        process.returncode = 0
        process.communicate.return_value = (b"stdout data", b"stderr data")
        return process

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger instance."""
        return Mock(spec=logging.Logger)

    def testterminate_process_unix_graceful_exit(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test graceful termination on Unix with SIGINT."""
        with patch.object(os, "name", "posix"):
            with patch("os.kill") as mock_kill:
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should send SIGINT
                mock_kill.assert_called_once_with(12345, signal.SIGINT)
                # Should call communicate with timeout
                mock_process.communicate.assert_called_once_with(timeout=5.0)
                assert result == (b"stdout data", b"stderr data")

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
    def testterminate_process_windows_graceful_exit(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test graceful termination on Windows with CTRL_BREAK_EVENT."""
        with patch.object(os, "name", "nt"):
            with patch.object(mock_process, "send_signal") as mock_send_signal:
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should send CTRL_BREAK_EVENT
                mock_send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
                # Should call communicate with timeout
                mock_process.communicate.assert_called_once_with(timeout=5.0)
                assert result == (b"stdout data", b"stderr data")

    def testterminate_process_unix_timeout_then_sigterm(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test Unix termination with timeout followed by SIGTERM."""
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),  # First timeout
            (b"stdout after sigterm", b"stderr after sigterm"),  # Success after SIGTERM
        ]

        with patch.object(os, "name", "posix"):
            with patch("os.kill") as mock_kill:
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should send SIGINT first, then SIGTERM
                assert mock_kill.call_count == 2
                mock_kill.assert_any_call(12345, signal.SIGINT)
                mock_kill.assert_any_call(12345, signal.SIGTERM)
                assert result == (b"stdout after sigterm", b"stderr after sigterm")

    @pytest.mark.skipif(os.name == "nt", reason="Unix-only test")
    def testterminate_process_unix_timeout_then_sigkill(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test Unix termination with timeout followed by SIGKILL."""
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),  # First timeout
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),  # Second timeout
            (b"stdout after sigkill", b"stderr after sigkill"),  # Success after SIGKILL
        ]

        with patch.object(os, "name", "posix"):
            with patch("os.kill") as mock_kill:
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should send SIGINT, SIGTERM, then SIGKILL
                assert mock_kill.call_count == 3
                mock_kill.assert_any_call(12345, signal.SIGINT)
                mock_kill.assert_any_call(12345, signal.SIGTERM)
                mock_kill.assert_any_call(12345, signal.SIGKILL)
                assert result == (b"stdout after sigkill", b"stderr after sigkill")

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
    def testterminate_process_windows_timeout_then_kill(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test Windows termination with timeout followed by kill()."""
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),  # First timeout
            (b"stdout after kill", b"stderr after kill"),  # Success after kill
        ]

        with patch.object(os, "name", "nt"):
            with patch.object(mock_process, "send_signal") as mock_send_signal:
                with patch.object(mock_process, "kill") as mock_kill:
                    result = terminate_process(
                        process=mock_process,
                        verbose=False,
                        logger=mock_logger,
                        graceful_timeout=5.0,
                        force_timeout=2.0,
                    )

                    # Should send CTRL_BREAK_EVENT first, then kill
                    mock_send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
                    mock_kill.assert_called_once()
                    assert result == (b"stdout after kill", b"stderr after kill")

    def testterminate_process_verbose_logging(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test that verbose mode logs progress."""
        with patch.object(os, "name", "posix"):
            with patch("os.kill"):
                terminate_process(
                    process=mock_process,
                    verbose=True,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should log debug messages for verbose-only output
                assert mock_logger.debug.call_count >= 2

    def testterminate_process_signal_failure(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test handling of signal sending failures."""
        with patch.object(os, "name", "posix"):
            with patch("os.kill", side_effect=ProcessLookupError("Process not found")):
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should log warning but continue
                mock_logger.warning.assert_called()
                # Should still try to communicate
                mock_process.communicate.assert_called()
                assert result == (b"stdout data", b"stderr data")

    @pytest.mark.skipif(os.name == "nt", reason="Unix-only test")
    def testterminate_process_zombie_unix(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test handling of zombie process on Unix."""
        # After all timeouts expire, communicate(timeout=None) is called
        # which will eventually return (process is reaped)
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            (b"final output", b""),  # communicate(timeout=None) succeeds
        ]

        with patch.object(os, "name", "posix"):
            with patch("os.kill"):
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=mock_logger,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )

                # Should call communicate 4 times (graceful, force, force, infinite)
                assert mock_process.communicate.call_count == 4
                # Last call should be with timeout=None to prevent zombie
                mock_process.communicate.assert_called_with(timeout=None)
                assert result == (b"final output", b"")

    def testterminate_process_zombie_windows(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test handling of zombie process on Windows."""
        # After timeout, communicate(timeout=None) is called to prevent zombie
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5.0),
            subprocess.TimeoutExpired(cmd="test", timeout=2.0),
            (b"final output", b""),  # communicate(timeout=None) succeeds
        ]

        with patch.object(os, "name", "nt"):
            with patch.object(mock_process, "send_signal"):
                with patch.object(mock_process, "kill"):
                    result = terminate_process(
                        process=mock_process,
                        verbose=False,
                        logger=mock_logger,
                        graceful_timeout=5.0,
                        force_timeout=2.0,
                    )

                    # Should call communicate 3 times (graceful, force, infinite)
                    assert mock_process.communicate.call_count == 3
                    # Last call should be with timeout=None to prevent zombie
                    mock_process.communicate.assert_called_with(timeout=None)
                    assert result == (b"final output", b"")

    def testterminate_process_default_logger(
        self, mock_process: Mock
    ) -> None:
        """Test that default logger is used when none provided."""
        with patch.object(os, "name", "posix"):
            with patch("os.kill"):
                # Should not raise when logger is None
                result = terminate_process(
                    process=mock_process,
                    verbose=False,
                    logger=None,
                    graceful_timeout=5.0,
                    force_timeout=2.0,
                )
                assert result == (b"stdout data", b"stderr data")


class TestLogProcessOutput:
    """Tests for log_process_output function."""

    @pytest.fixture
    def mock_process(self) -> Mock:
        """Create a mock subprocess.Popen instance."""
        process = Mock(spec=subprocess.Popen)
        process.pid = 12345
        process.returncode = 0
        return process

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger instance."""
        return Mock(spec=logging.Logger)

    def test_log_output_success_verbose(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test logging output on successful exit with verbose mode."""
        mock_process.returncode = 0

        log_process_output(
            process=mock_process,
            stdout_data=b"stdout content",
            stderr_data=b"stderr content",
            verbose=True,
            logger=mock_logger,
        )

        # Should log both stdout and stderr at debug level
        assert mock_logger.debug.call_count >= 1

    def test_log_output_success_quiet(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test logging output on successful exit without verbose mode."""
        mock_process.returncode = 0

        log_process_output(
            process=mock_process,
            stdout_data=b"stdout content",
            stderr_data=b"stderr content",
            verbose=False,
            logger=mock_logger,
        )

        # Should not log anything on success without verbose
        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_not_called()

    def test_log_output_returncode_none(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test logging when returncode is None (process not terminated)."""
        mock_process.returncode = None

        log_process_output(
            process=mock_process,
            stdout_data=b"stdout content",
            stderr_data=b"stderr content",
            verbose=True,
            logger=mock_logger,
        )

        # Should log warning about returncode being None
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "returncode is None" in call_args

    def test_log_output_error_always_logs(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test that error output is always logged regardless of verbose."""
        mock_process.returncode = 1

        log_process_output(
            process=mock_process,
            stdout_data=b"error output",
            stderr_data=b"error details",
            verbose=False,
            logger=mock_logger,
        )

        # Should log at warning level on error
        assert mock_logger.warning.call_count >= 1

    def test_log_output_empty_output(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test logging with empty output."""
        mock_process.returncode = 0

        log_process_output(
            process=mock_process,
            stdout_data=b"",
            stderr_data=b"",
            verbose=True,
            logger=mock_logger,
        )

        # Should not log empty output
        mock_logger.debug.assert_not_called()
        mock_logger.warning.assert_not_called()

    def test_log_output_stderr_only(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test logging when only stderr has content."""
        mock_process.returncode = 1

        log_process_output(
            process=mock_process,
            stdout_data=b"",
            stderr_data=b"error details",
            verbose=False,
            logger=mock_logger,
        )

        # Should log stderr at warning level
        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "stderr" in call_args.lower()

    def test_log_output_stdout_only(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test logging when only stdout has content."""
        mock_process.returncode = 1

        log_process_output(
            process=mock_process,
            stdout_data=b"output content",
            stderr_data=b"",
            verbose=False,
            logger=mock_logger,
        )

        # Should log stdout at warning level
        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "stdout" in call_args.lower()

    def test_log_output_with_pid(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test that PID is included in log messages."""
        mock_process.returncode = 1
        mock_process.pid = 99999

        log_process_output(
            process=mock_process,
            stdout_data=b"output",
            stderr_data=b"",
            verbose=False,
            logger=mock_logger,
        )

        # PID should be in the log message
        call_args = mock_logger.warning.call_args
        assert call_args is not None
        assert call_args[0][1] == 99999  # Second positional arg is pid

    def test_log_output_with_returncode(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test that return code is included in log messages."""
        mock_process.returncode = 42

        log_process_output(
            process=mock_process,
            stdout_data=b"output",
            stderr_data=b"",
            verbose=False,
            logger=mock_logger,
        )

        # Return code should be in the log message
        call_args = mock_logger.warning.call_args
        assert call_args is not None
        assert call_args[0][2] == 42  # Third positional arg is returncode

    def test_log_output_decoding_errors(
        self, mock_process: Mock, mock_logger: Mock
    ) -> None:
        """Test that decoding errors are handled gracefully."""
        mock_process.returncode = 1
        # Invalid UTF-8 bytes
        invalid_utf8 = b"\xff\xfe\x00\x01"

        # Should not raise on invalid UTF-8
        log_process_output(
            process=mock_process,
            stdout_data=invalid_utf8,
            stderr_data=b"",
            verbose=False,
            logger=mock_logger,
        )

        mock_logger.warning.assert_called()

    def test_log_output_default_logger(
        self, mock_process: Mock
    ) -> None:
        """Test that default logger is used when none provided."""
        mock_process.returncode = 0

        # Should not raise when logger is None
        log_process_output(
            process=mock_process,
            stdout_data=b"output",
            stderr_data=b"",
            verbose=False,
            logger=None,
        )


class TestCrossPlatform:
    """Cross-platform compatibility tests."""

    @pytest.fixture
    def mock_process(self) -> Mock:
        """Create a mock subprocess.Popen instance."""
        process = Mock(spec=subprocess.Popen)
        process.pid = 12345
        process.returncode = 0
        process.communicate.return_value = (b"stdout", b"stderr")
        return process

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
    def test_windows_signal_constants(self, mock_process: Mock) -> None:
        """Test that Windows signal constants are available."""
        # This test only runs on Windows
        assert hasattr(signal, "CTRL_BREAK_EVENT")

    @pytest.mark.skipif(os.name != "posix", reason="Unix-specific test")
    def test_unix_signal_constants(self, mock_process: Mock) -> None:
        """Test that Unix signal constants are available."""
        # This test only runs on Unix
        assert hasattr(signal, "SIGINT")
        assert hasattr(signal, "SIGTERM")
        assert hasattr(signal, "SIGKILL")

    def test_os_name_detection(self) -> None:
        """Test that os.name is properly detected."""
        # os.name should be either 'nt' or 'posix'
        assert os.name in ("nt", "posix", "java")
