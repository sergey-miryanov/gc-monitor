"""Tests for log_process_output utility function."""

from unittest.mock import patch

import pytest

from gcmon.utils.process_terminator import log_process_output


@pytest.fixture(autouse=True)
def logger_env(mock_logger):
    with patch("gcmon.utils.process_terminator._logger", mock_logger):
        yield


class TestLogProcessOutput:
    def test_success(self, mock_logger, mock_process):
        mock_process.returncode = 0
        log_process_output(process=mock_process, stdout_data=b"stdout content")
        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_not_called()

    def test_returncode_none(self, mock_logger, mock_process):
        mock_process.returncode = None
        mock_process.poll.return_value = None
        log_process_output(process=mock_process, stdout_data=b"stdout content")
        mock_logger.warning.assert_called_once()
        assert "has not terminated" in mock_logger.warning.call_args[0][0]

    def test_error_always_logs(self, mock_logger, mock_process):
        mock_process.returncode = 1
        log_process_output(process=mock_process, stdout_data=b"error output")
        assert mock_logger.warning.call_count >= 1

    def test_empty_output(self, mock_logger, mock_process):
        mock_process.returncode = 0
        log_process_output(process=mock_process, stdout_data=b"")
        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_not_called()

    def test_stdout_only(self, mock_logger, mock_process):
        mock_process.returncode = 1
        log_process_output(process=mock_process, stdout_data=b"output content")
        mock_logger.warning.assert_called()
        assert "stdout" in mock_logger.warning.call_args[0][0].lower()

    def test_with_pid(self, mock_logger, mock_process):
        mock_process.returncode = 1
        mock_process.pid = 99999
        log_process_output(process=mock_process, stdout_data=b"output")
        assert mock_logger.warning.call_args[0][1] == 99999

    def test_with_returncode(self, mock_logger, mock_process):
        mock_process.returncode = 42
        log_process_output(process=mock_process, stdout_data=b"output")
        assert mock_logger.warning.call_args[0][2] == 42

    def test_decoding_errors(self, mock_logger, mock_process):
        mock_process.returncode = 1
        log_process_output(process=mock_process, stdout_data=b"\xff\xfe\x00\x01")
        mock_logger.warning.assert_called()

    def test_default_logger(self, mock_process):
        mock_process.returncode = 0
        log_process_output(process=mock_process, stdout_data=b"output")
