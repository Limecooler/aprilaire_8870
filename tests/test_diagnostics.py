"""Tests for diagnostics.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aprilaire_8870 import AprilaireRuntimeData, diagnostics
from custom_components.aprilaire_8870.const import DOMAIN


def _attach_runtime(entry, *, coordinator=None, connection=None, devices=None):
    entry.runtime_data = AprilaireRuntimeData(
        coordinator=coordinator if coordinator is not None else MagicMock(),
        connection=connection if connection is not None else MagicMock(),
        device_manager=MagicMock(),
        discovered_addresses=[],
        devices=devices if devices is not None else {},
    )


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _make_device(address=1):
    d = MagicMock()
    d.model = "8870"
    d.firmware_version = "1.0"
    d.available = True
    d.is_cos_enabled = MagicMock(return_value=True)
    d.get_cos_flags = MagicMock(return_value={"c1", "c2"})
    d.get_capabilities = MagicMock(return_value={"is_heat_pump": False})
    d.get_state = MagicMock(return_value={"temperature": 70})
    return d


async def test_diagnostics_redacts_host(hass) -> None:
    from datetime import timedelta

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "serial_server", "host": "10.0.0.5", "port": 23},
    )
    entry.add_to_hass(hass)

    coord = MagicMock()
    coord.update_interval = timedelta(seconds=300)
    coord.last_update_success = True
    coord._connection_state = True
    coord._cos_enabled = True
    coord._cos_verified = True

    conn = MagicMock()
    conn.is_connected = MagicMock(return_value=True)
    conn.state = "connected"
    conn._connect_error_count = 0

    _attach_runtime(entry, coordinator=coord, devices={1: _make_device(1)}, connection=conn)

    diag = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    # Host is redacted.
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["coordinator"]["device_count"] == 1
    assert diag["devices"]["1"]["model"] == "8870"
    assert diag["connection"]["type"] == "serial_server"


async def test_diagnostics_missing_entry_data(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"connection_type": "serial_port"})
    entry.add_to_hass(hass)
    # No hass.data entry — diagnostics should still return safely.
    diag = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert diag["coordinator"] == {}
    assert diag["connection"] == {}
    assert diag["devices"] == {}


async def test_diagnostics_redacts_port_name(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "serial_port", "port_name": "/dev/ttyUSB0"},
    )
    entry.add_to_hass(hass)
    diag = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["port_name"] == "**REDACTED**"


async def test_diagnostics_handles_missing_update_interval(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={"connection_type": "serial_server", "host": "x", "port": 23}
    )
    entry.add_to_hass(hass)
    coord = MagicMock()
    coord.update_interval = None
    coord.last_update_success = False
    _attach_runtime(
        entry, coordinator=coord, devices={},
        connection=MagicMock(is_connected=MagicMock(return_value=False), state="error"),
    )
    diag = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert diag["coordinator"]["update_interval_seconds"] is None
