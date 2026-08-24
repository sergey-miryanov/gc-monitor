"""The hook module's environment reading.

Everything else about the hook is behaviour, and lives in
``test_pyperf_marks.py``.
"""

import os
from unittest.mock import patch

from gcmon.pyperf.hook import _get_env_pyperf_hook_control_timeout


class TestGetEnvControlTimeout:
    def test_default_value(self) -> None:
        with patch.dict(os.environ, clear=True):
            assert _get_env_pyperf_hook_control_timeout() == 10.0

    def test_custom_value(self) -> None:
        with patch.dict(os.environ, {"GCMON_PYPERF_HOOK_CONTROL_TIMEOUT": "30"}):
            assert _get_env_pyperf_hook_control_timeout() == 30.0

    def test_invalid_value_returns_default(self) -> None:
        with patch.dict(os.environ, {"GCMON_PYPERF_HOOK_CONTROL_TIMEOUT": "not-a-number"}):
            assert _get_env_pyperf_hook_control_timeout() == 10.0
