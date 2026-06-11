"""Shared fixtures for control-plane tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_exporter():
    return MagicMock()


@pytest.fixture
def control_server(mock_exporter):
    from gcmon.control.control_server import ControlServer

    server = ControlServer(mock_exporter)
    try:
        server.start()
        yield server
    finally:
        server.close()
