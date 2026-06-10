import pytest

from gcmon.poll_status import PollStatus


@pytest.fixture
def poll_status_list():
    return list(PollStatus)


class TestPollStatusMembers:
    def test_all_members_present(self, poll_status_list):
        assert poll_status_list == [
            PollStatus.OK,
            PollStatus.FAIL,
            PollStatus.INVALID_PROCESS,
        ]

    def test_repr(self):
        assert repr(PollStatus.OK) == "<PollStatus.OK: 1>"
