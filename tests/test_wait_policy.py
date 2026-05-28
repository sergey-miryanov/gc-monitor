from unittest.mock import patch

import pytest

from gc_monitor.poll_status import PollStatus
from gc_monitor.wait_policy import NoWaitPolicy, StartupTimeoutPolicy


@pytest.fixture
def no_wait_policy():
    return NoWaitPolicy()


@pytest.fixture
def make_policy():
    def _make(timeout=5):
        return StartupTimeoutPolicy(timeout)

    return _make


class TestNoWaitPolicy:
    def test_ok_returns_true(self, no_wait_policy):
        assert no_wait_policy.wait(PollStatus.OK) is True

    @pytest.mark.parametrize("status", [PollStatus.FAIL, PollStatus.INVALID_PROCESS, PollStatus.INVALID_PYTHON])
    def test_others_return_false(self, no_wait_policy, status):
        assert no_wait_policy.wait(status) is False


class TestStartupTimeoutPolicy:
    def test_ok_returns_true_sets_alive(self, make_policy):
        policy = make_policy()
        assert policy.wait(PollStatus.OK) is True
        assert policy._has_seen_alive

    def test_fail_returns_false(self, make_policy):
        assert make_policy().wait(PollStatus.FAIL) is False

    def test_invalid_python_returns_false(self, make_policy):
        assert make_policy().wait(PollStatus.INVALID_PYTHON) is False

    def test_invalid_process_before_timeout(self, make_policy):
        with patch("time.monotonic", side_effect=[100.0, 103.0]):
            policy = make_policy(5)
            assert policy.wait(PollStatus.INVALID_PROCESS) is True

    def test_invalid_process_after_timeout(self, make_policy):
        with patch("time.monotonic", side_effect=[100.0, 110.0]):
            policy = make_policy(5)
            assert policy.wait(PollStatus.INVALID_PROCESS) is False

    def test_invalid_process_at_exact_timeout(self, make_policy):
        with patch("time.monotonic", side_effect=[100.0, 105.0]):
            policy = make_policy(5)
            # 105.0 - 100.0 = 5.0, not strictly < 5, so False
            assert policy.wait(PollStatus.INVALID_PROCESS) is False

    def test_invalid_process_after_seen_alive(self, make_policy):
        policy = make_policy()
        policy.wait(PollStatus.OK)
        assert policy.wait(PollStatus.INVALID_PROCESS) is False

    def test_timeout_zero(self, make_policy):
        with patch("time.monotonic", side_effect=[100.0, 100.0]):
            policy = make_policy(0)
            assert policy.wait(PollStatus.INVALID_PROCESS) is False

    def test_unknown_status_raises_value_error(self, make_policy):
        with pytest.raises(ValueError, match="Unknown status"):
            make_policy().wait(999)

    def test_multiple_ok(self, make_policy):
        policy = make_policy()
        assert policy.wait(PollStatus.OK) is True
        assert policy.wait(PollStatus.OK) is True

    def test_float_timeout(self, make_policy):
        with patch("time.monotonic", side_effect=[100.0, 102]):
            policy = make_policy(3)
            assert policy.wait(PollStatus.INVALID_PROCESS) is True

    def test_float_timeout_expired(self, make_policy):
        with patch("time.monotonic", side_effect=[100.0, 104.0]):
            policy = make_policy(3)
            assert policy.wait(PollStatus.INVALID_PROCESS) is False
