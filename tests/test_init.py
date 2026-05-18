"""Tests for __init__.py (integration setup/teardown)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aprilaire_8870 import (
    AprilaireRuntimeData,
    _async_backfill_and_apply_device_names,
    async_initialize_devices_background,
    async_register_services,
    async_setup,
    async_setup_cos_background,
    async_setup_entry,
    async_unload_entry,
    async_update_options,
)
from custom_components.aprilaire_8870.const import DOMAIN


def _attach_runtime_data(entry, **overrides):
    """Test helper: attach a minimal AprilaireRuntimeData to an entry."""
    defaults = {
        "coordinator": MagicMock(),
        "connection": MagicMock(),
        "device_manager": MagicMock(),
        "discovered_addresses": [],
        "devices": {},
    }
    defaults.update(overrides)
    entry.runtime_data = AprilaireRuntimeData(**defaults)
    return entry.runtime_data


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


# ---- async_setup -----------------------------------------------------------


async def test_async_setup(hass) -> None:
    # async_setup is a no-op since v0.3.0 (runtime_data migration).
    assert await async_setup(hass, {}) is True


# ---- async_register_services ----------------------------------------------


async def test_register_services_happy(hass) -> None:
    await async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "set_text_message")


async def test_register_services_swallows_exception(hass) -> None:
    with patch(
        "custom_components.aprilaire_8870.services.async_setup_services",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await async_register_services(hass)  # error logged, no raise


# ---- async_initialize_devices_background ----------------------------------


async def test_initialize_devices_background_happy(hass) -> None:
    coord = MagicMock()
    coord.data = None
    coord.async_refresh = AsyncMock()
    coord.async_update_listeners = MagicMock()
    coord.connection = MagicMock()
    coord.connection.is_connected = MagicMock(return_value=True)

    new_device = MagicMock()
    new_device.get_state = MagicMock(return_value={"x": 1})
    new_device.available = True
    new_device.async_enable_cos = AsyncMock()

    dm = MagicMock()
    dm.async_setup_device = AsyncMock(return_value=new_device)

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)

    with patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        await async_initialize_devices_background(hass, entry, coord, dm, [1, 2])

    dm.async_setup_device.assert_called()
    coord.async_refresh.assert_called()


async def test_initialize_devices_runs_in_parallel(hass) -> None:
    """v0.3.0: per-device init fans out via asyncio.gather, not serial."""
    coord = MagicMock()
    coord.data = None
    coord.async_refresh = AsyncMock()
    coord.async_update_listeners = MagicMock()
    coord.connection = MagicMock()
    coord.connection.is_connected = MagicMock(return_value=True)

    in_flight = 0
    max_in_flight = 0

    async def slow_setup(address):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        d = MagicMock()
        d.get_state = MagicMock(return_value={})
        d.available = True
        return d

    dm = MagicMock()
    dm.async_setup_device = AsyncMock(side_effect=slow_setup)

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)

    await async_initialize_devices_background(hass, entry, coord, dm, [1, 2, 3, 4, 5])
    # All five should have overlapped — serial would max out at 1.
    assert max_in_flight >= 3


async def test_initialize_devices_handles_disconnected(hass) -> None:
    coord = MagicMock()
    coord.data = {}
    coord.async_refresh = AsyncMock()
    coord.async_update_listeners = MagicMock()
    coord.connection = MagicMock()
    coord.connection.is_connected = MagicMock(side_effect=[False, False])
    coord.connection.async_reconnect_with_backoff = AsyncMock(return_value=False)

    dm = MagicMock()
    dm.async_setup_device = AsyncMock(return_value=None)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)

    with patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        await async_initialize_devices_background(hass, entry, coord, dm, [1])

    coord.connection.async_reconnect_with_backoff.assert_called_once()


async def test_initialize_devices_setup_failure_continues(hass) -> None:
    coord = MagicMock()
    coord.data = {}
    coord.async_refresh = AsyncMock()
    coord.async_update_listeners = MagicMock()
    coord.connection = MagicMock()
    coord.connection.is_connected = MagicMock(return_value=True)

    dm = MagicMock()
    dm.async_setup_device = AsyncMock(side_effect=[RuntimeError("setup broke"), MagicMock()])

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)

    with patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        await async_initialize_devices_background(hass, entry, coord, dm, [1, 2])


async def test_initialize_devices_refresh_exception(hass) -> None:
    coord = MagicMock()
    coord.data = {}
    coord.async_refresh = AsyncMock(side_effect=RuntimeError("refresh broke"))
    coord.async_update_listeners = MagicMock()
    coord.connection = MagicMock()
    coord.connection.is_connected = MagicMock(return_value=True)

    dm = MagicMock()
    dm.async_setup_device = AsyncMock(return_value=MagicMock(get_state=MagicMock(return_value={}), available=True))

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)

    with patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        await async_initialize_devices_background(hass, entry, coord, dm, [1])


async def test_initialize_devices_outer_exception(hass) -> None:
    """Force an early failure to hit the outer except."""
    coord = MagicMock()
    coord.connection = None  # no connection
    dm = MagicMock()
    dm.async_setup_device = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)

    # Force an exception via a magic attribute access on data.
    with patch("custom_components.aprilaire_8870.asyncio.sleep",
               side_effect=RuntimeError("outer")):
        await async_initialize_devices_background(hass, entry, coord, dm, [1])


# ---- async_setup_cos_background -------------------------------------------


async def test_setup_cos_background_uses_bulk_globals(hass) -> None:
    """v0.4.0: COS setup uses SN0 global commands, not per-device async_enable_cos."""
    dev1 = MagicMock()
    dev2 = MagicMock()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    runtime = _attach_runtime_data(entry)
    runtime.connection.is_connected = MagicMock(return_value=True)
    runtime.connection.async_send_global_command = AsyncMock(
        return_value={1: "SN1 CR=NORMAL", 2: "SN2Kitchen  CR=NORMAL"}
    )
    await async_setup_cos_background(hass, entry, {1: dev1, 2: dev2})
    # CR=NORMAL plus 7 default flags = 8 global commands total.
    assert runtime.connection.async_send_global_command.call_count >= 8
    # Devices marked as cos_enabled.
    assert dev1._cos_enabled is True
    assert dev2._cos_enabled is True
    # Per-device async_enable_cos no longer called (it's now an unused path).
    assert not hasattr(dev1.async_enable_cos, "assert_called") or \
        not dev1.async_enable_cos.called


async def test_setup_cos_background_no_devices_returns(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    _attach_runtime_data(entry)
    await async_setup_cos_background(hass, entry, {})


async def test_setup_cos_background_no_connection_returns(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    runtime = _attach_runtime_data(entry)
    runtime.connection.is_connected = MagicMock(return_value=False)
    # Should not raise; just no-op.
    await async_setup_cos_background(hass, entry, {1: MagicMock()})


async def test_setup_cos_background_swallows_exceptions(hass) -> None:
    dev = MagicMock()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    runtime = _attach_runtime_data(entry)
    runtime.connection.is_connected = MagicMock(return_value=True)
    runtime.connection.async_send_global_command = AsyncMock(side_effect=RuntimeError("boom"))
    # Should not raise.
    await async_setup_cos_background(hass, entry, {1: dev})


# ---- async_setup_entry ----------------------------------------------------


def _make_entry(hass, connection_type="serial_server", discovered=None):
    # Explicit `[1]` only when discovered is None; honour an empty list literally.
    data = {
        "connection_type": connection_type,
        "host": "1.2.3.4",
        "port": 23,
        "discovered_thermostats": [1] if discovered is None else discovered,
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


async def test_setup_entry_invalid_connection_type(hass) -> None:
    data = {"connection_type": "bogus"}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    assert await async_setup_entry(hass, entry) is False


async def test_setup_entry_no_discovered_thermostats(hass) -> None:
    from homeassistant.exceptions import ConfigEntryNotReady
    entry = _make_entry(hass, discovered=[])

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        pass

    async def stub_start_reading(self):
        pass

    with patch.object(
        __import__("custom_components.aprilaire_8870.connection",
                   fromlist=["SerialServerConnection"]).SerialServerConnection,
        "async_connect", new=stub_connect,
    ), patch.object(
        __import__("custom_components.aprilaire_8870.connection",
                   fromlist=["SerialServerConnection"]).SerialServerConnection,
        "async_disconnect", new=stub_disconnect,
    ), patch.object(
        __import__("custom_components.aprilaire_8870.connection",
                   fromlist=["SerialServerConnection"]).SerialServerConnection,
        "async_start_reading", new=stub_start_reading,
    ), patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_setup_entry_connect_keeps_retrying(hass) -> None:
    """Each connect attempt fails; eventually raises ConfigEntryNotReady."""
    from homeassistant.exceptions import ConfigEntryNotReady
    entry = _make_entry(hass)
    from custom_components.aprilaire_8870.connection import SerialServerConnection
    with patch.object(SerialServerConnection, "async_connect", new=AsyncMock(return_value=False)), \
         patch.object(SerialServerConnection, "async_disconnect", new=AsyncMock()), \
         patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_setup_entry_full_happy_path(hass) -> None:
    entry = _make_entry(hass, discovered=[1])
    from custom_components.aprilaire_8870.connection import SerialServerConnection

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        self._state = "disconnected"

    async def stub_start_reading(self):
        pass

    with patch.object(SerialServerConnection, "async_connect", new=stub_connect), \
         patch.object(SerialServerConnection, "async_disconnect", new=stub_disconnect), \
         patch.object(SerialServerConnection, "async_start_reading", new=stub_start_reading), \
         patch.object(SerialServerConnection, "is_connected", new=lambda self: True), \
         patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        ok = await async_setup_entry(hass, entry)
    assert ok is True
    assert isinstance(entry.runtime_data, AprilaireRuntimeData)


async def test_setup_entry_connection_lost_after_read(hass) -> None:
    from homeassistant.exceptions import ConfigEntryNotReady
    entry = _make_entry(hass)
    from custom_components.aprilaire_8870.connection import SerialServerConnection

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        pass

    async def stub_start_reading(self):
        # Simulate connection drop right after starting.
        self._state = "disconnected"

    with patch.object(SerialServerConnection, "async_connect", new=stub_connect), \
         patch.object(SerialServerConnection, "async_disconnect", new=stub_disconnect), \
         patch.object(SerialServerConnection, "async_start_reading", new=stub_start_reading), \
         patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_setup_entry_serial_port_branch(hass) -> None:
    """Cover the serial_port branch of connection-type selection."""
    data = {
        "connection_type": "serial_port",
        "port_name": "/dev/null",
        "baud_rate": 9600,
        "discovered_thermostats": [1],
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    from custom_components.aprilaire_8870.connection import ComPortConnection

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        pass

    async def stub_start(self):
        pass

    with patch.object(ComPortConnection, "async_connect", new=stub_connect), \
         patch.object(ComPortConnection, "async_disconnect", new=stub_disconnect), \
         patch.object(ComPortConnection, "async_start_reading", new=stub_start), \
         patch.object(ComPortConnection, "is_connected", new=lambda self: True), \
         patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        ok = await async_setup_entry(hass, entry)
    assert ok is True


# ---- async_update_options / async_unload_entry ----------------------------


async def test_update_options_reloads(hass) -> None:
    entry = _make_entry(hass)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload_mock:
        await async_update_options(hass, entry)
    reload_mock.assert_called_with(entry.entry_id)


async def test_unload_entry_removes_data(hass) -> None:
    entry = _make_entry(hass)
    from custom_components.aprilaire_8870.connection import SerialServerConnection

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        self._state = "disconnected"

    async def stub_start(self):
        pass

    with patch.object(SerialServerConnection, "async_connect", new=stub_connect), \
         patch.object(SerialServerConnection, "async_disconnect", new=stub_disconnect), \
         patch.object(SerialServerConnection, "async_start_reading", new=stub_start), \
         patch.object(SerialServerConnection, "is_connected", new=lambda self: True), \
         patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        await async_setup_entry(hass, entry)

    with patch.object(hass.config_entries, "async_unload_platforms",
                      new=AsyncMock(return_value=True)):
        result = await async_unload_entry(hass, entry)
    assert result is True
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_setup_entry_outer_exception(hass) -> None:
    from homeassistant.exceptions import ConfigEntryNotReady
    entry = _make_entry(hass)
    from custom_components.aprilaire_8870.connection import SerialServerConnection
    with patch.object(SerialServerConnection, "async_connect",
                      new=AsyncMock(side_effect=RuntimeError("outer"))), \
         patch.object(SerialServerConnection, "async_disconnect", new=AsyncMock()), \
         patch("custom_components.aprilaire_8870.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


# ---- device-name backfill (v0.2.6) ----------------------------------------


async def test_backfill_renames_from_device_name(hass) -> None:
    """Backfill reads device.name (set by _parse_model_info) and pushes to registry."""
    from homeassistant.helpers import device_registry as dr_helpers

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "serial_server", "discovered_thermostats": [1, 2]},
    )
    entry.add_to_hass(hass)

    registry = dr_helpers.async_get(hass)
    for addr in (1, 2):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, str(addr))},
            name=f"Aprilaire {addr}",
            manufacturer="Aprilaire",
            model="8870",
        )

    device1 = MagicMock(address=1)
    device1.name = "Master Bedroom"
    device2 = MagicMock(address=2)
    device2.name = "Kitchen"
    device_manager = MagicMock()
    device_manager.device_names = {}

    await _async_backfill_and_apply_device_names(
        hass, entry, MagicMock(), device_manager, {1: device1, 2: device2}
    )

    assert entry.data["device_names"] == {"1": "Master Bedroom", "2": "Kitchen"}
    assert registry.async_get_device(identifiers={(DOMAIN, "1")}).name == "Master Bedroom"
    assert registry.async_get_device(identifiers={(DOMAIN, "2")}).name == "Kitchen"


async def test_backfill_skips_default_placeholder_names(hass) -> None:
    """Devices that never picked up a name keep the 'Aprilaire <addr>' fallback."""
    from homeassistant.helpers import device_registry as dr_helpers

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "serial_server", "discovered_thermostats": [1, 2]},
    )
    entry.add_to_hass(hass)
    registry = dr_helpers.async_get(hass)
    for addr in (1, 2):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, str(addr))},
            name=f"Aprilaire {addr}",
            manufacturer="Aprilaire",
            model="8870",
        )

    device1 = MagicMock(address=1)
    device1.name = "Master Bedroom"
    device2 = MagicMock(address=2)
    device2.name = "Aprilaire 2"  # default placeholder, no name on device side

    await _async_backfill_and_apply_device_names(
        hass, entry, MagicMock(), MagicMock(), {1: device1, 2: device2}
    )

    # Only the real name persists.
    assert entry.data["device_names"] == {"1": "Master Bedroom"}
    assert registry.async_get_device(identifiers={(DOMAIN, "2")}).name == "Aprilaire 2"


async def test_backfill_respects_user_set_name(hass) -> None:
    """Devices the user already renamed in HA's UI are never overridden."""
    from homeassistant.helpers import device_registry as dr_helpers

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "serial_server", "discovered_thermostats": [1]},
    )
    entry.add_to_hass(hass)

    registry = dr_helpers.async_get(hass)
    dev = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "1")},
        name="Aprilaire 1",
        manufacturer="Aprilaire",
        model="8870",
    )
    registry.async_update_device(dev.id, name_by_user="My Special Name")

    device1 = MagicMock(address=1)
    device1.name = "Master Bedroom"

    await _async_backfill_and_apply_device_names(
        hass, entry, MagicMock(), MagicMock(), {1: device1}
    )

    after = registry.async_get_device(identifiers={(DOMAIN, "1")})
    assert after.name_by_user == "My Special Name"
    assert after.name == "Aprilaire 1"


async def test_backfill_no_devices_returns_quickly(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    # Empty devices dict — should no-op, not raise.
    await _async_backfill_and_apply_device_names(
        hass, entry, MagicMock(), MagicMock(), {}
    )

