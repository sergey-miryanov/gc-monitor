import pytest

from gc_monitor.target_process import ExternalProcess, TargetProcess


@pytest.fixture
def external_process():
    return ExternalProcess(pid=12345)


@pytest.fixture
def zero_process():
    return ExternalProcess(pid=0)


class TestExternalProcess:
    def test_pid(self, external_process):
        assert external_process.pid == 12345

    def test_pid_zero(self, zero_process):
        assert zero_process.pid == 0

    def test_negative_pid(self):
        proc = ExternalProcess(pid=-1)
        assert proc.pid == -1

    def test_is_target_process_protocol(self, external_process):
        assert isinstance(external_process, TargetProcess)
