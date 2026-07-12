"""Shared fixtures for control-plane tests."""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest

from gcmon.control.control_server import ControlServer


@pytest.fixture
def mock_exporter() -> MagicMock:
    return MagicMock()


@pytest.fixture
def control_server(mock_exporter: MagicMock) -> Generator[ControlServer]:
    server = ControlServer(mock_exporter)
    try:
        server.start()
        yield server
    finally:
        server.close()
