"""Shared test fixtures for the aprilaire_8870 integration."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Ensure the repo root is on sys.path so `custom_components.aprilaire_8870`
# imports resolve to our symlinked package.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


# ---------------------------------------------------------------------------
# Lightweight connection / protocol stand-ins for pure unit tests that do not
# need a full HomeAssistant instance.
# ---------------------------------------------------------------------------


class StubConnection:
    """In-memory stand-in for SerialServerConnection.

    Records every command sent and yields scripted responses from
    `async_send_command_with_response` so we can assert on bus traffic
    without spinning up a network or read loop.
    """

    def __init__(self, responses: dict[str, str] | None = None):
        self.sent: list[str] = []
        self.responses: dict[str, str] = responses or {}
        self._received_messages: list[str] = []
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected

    async def async_send_command_with_response(self, command: str, timeout: float = 3.0):
        self.sent.append(command)
        # Match on the prefix before any "?" or "=" — keys in self.responses
        # are full command strings.
        return self.responses.get(command)

    async def async_send_command(self, command: str):  # legacy fallback path
        self.sent.append(command)
        return None

    def get_received_messages(self) -> list[str]:
        msgs = self._received_messages[:]
        self._received_messages.clear()
        return msgs


@pytest.fixture
def stub_connection() -> StubConnection:
    return StubConnection()


@pytest.fixture
def mock_hass() -> MagicMock:
    """Bare-bones hass mock — just enough surface for connection/services tests."""
    hass = MagicMock()
    hass.loop = asyncio.get_event_loop()
    hass.data = {}
    hass.services = MagicMock()
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    return hass


@pytest.fixture
def mock_protocol(stub_connection):
    """Return a real AprilaireProtocol wrapped around the stub connection."""
    from custom_components.aprilaire_8870.protocol import AprilaireProtocol
    return AprilaireProtocol(connection=stub_connection)
