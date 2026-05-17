"""Tests for coordinator.py."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aprilaire_8870.coordinator import (
    AprilaireDataUpdateCoordinator,
)
from custom_components.aprilaire_8870.const import DOMAIN


def make_coord(hass, devices=None, connection=None):
    """Build a coordinator with reasonable defaults."""
    conn = connection
    if conn is None:
        conn = MagicMock()
        conn.is_connected = MagicMock(return_value=True)
        conn.register_connection_callback = MagicMock()
        conn.register_message_callback = MagicMock()
    devices = devices or {}
    return AprilaireDataUpdateCoordinator(
        hass,
        connection=conn,
        devices=devices,
        device_manager=MagicMock(),
    )


def make_dev(address=1, available=True):
    d = MagicMock()
    d.address = address
    d.available = available
    d.get_state = MagicMock(return_value={"temperature": 70, "available": available})
    d.async_update = AsyncMock(return_value=True)
    d.async_set_temperature = AsyncMock()
    d.async_set_hvac_mode = AsyncMock()
    d.async_set_fan_mode = AsyncMock()
    d.async_verify_cos = AsyncMock(return_value=True)
    return d


# ---- init ------------------------------------------------------------------


async def test_init_creates_data_structures(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    assert "1" in coord._device_data
    assert coord._device_data["1"]["available"] is True
    assert coord._connection_state is True


async def test_init_without_connection(hass) -> None:
    coord = AprilaireDataUpdateCoordinator(
        hass, connection=None, devices={}, device_manager=None,
    )
    assert coord._connection_state is False


# ---- _async_load_stored_state ----------------------------------------------


async def test_load_stored_state_present(hass) -> None:
    coord = make_coord(hass)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value={
        "devices": {"1": {"temperature": 70, "available": True}}
    })
    await coord._async_load_stored_state()
    assert coord._state_loaded is True
    assert coord._device_data["1"]["from_cache"] is True


async def test_load_stored_state_initializes_when_data_is_none(hass) -> None:
    coord = make_coord(hass)
    coord._device_data = None
    coord.data = None
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value={
        "devices": {"1": {"temperature": 70}}
    })
    await coord._async_load_stored_state()
    assert isinstance(coord._device_data, dict)


async def test_load_stored_state_none(hass) -> None:
    coord = make_coord(hass)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=None)
    await coord._async_load_stored_state()
    assert coord._state_loaded is False


async def test_load_stored_state_exception(hass) -> None:
    coord = make_coord(hass)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(side_effect=RuntimeError("disk broke"))
    await coord._async_load_stored_state()


# ---- _async_save_state -----------------------------------------------------


async def test_save_state(hass) -> None:
    coord = make_coord(hass, devices={1: make_dev(1)})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    coord._device_data = {"1": {"temperature": 70, "from_cache": True}}
    await coord._async_save_state()
    saved = coord._store.async_save.call_args[0][0]
    # from_cache should be stripped.
    assert "from_cache" not in saved["devices"]["1"]


async def test_save_state_exception(hass) -> None:
    coord = make_coord(hass)
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock(side_effect=RuntimeError("disk full"))
    coord._device_data = {"1": {"temperature": 70}}
    await coord._async_save_state()


async def test_save_state_skips_empty_devices(hass) -> None:
    coord = make_coord(hass)
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    coord._device_data = {"1": {"from_cache": True}}  # only contains stripped key
    await coord._async_save_state()
    saved = coord._store.async_save.call_args[0][0]
    assert "1" not in saved["devices"]


# ---- async_setup -----------------------------------------------------------


# ---- connection state callbacks -------------------------------------------


def test_connection_state_unchanged_noop(hass) -> None:
    coord = make_coord(hass)
    coord._connection_state = True
    coord._device_data = {"1": {}}
    coord._connection_state_changed(True)


def test_connection_state_change_to_disconnected(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._connection_state_changed(False)
    assert coord._device_data["1"]["available"] is False


def test_connection_state_change_handles_none_data(hass) -> None:
    coord = make_coord(hass)
    coord._connection_state = True
    coord._device_data = None
    coord.data = None
    coord._connection_state_changed(False)


def test_connection_state_change_to_connected(hass) -> None:
    coord = make_coord(hass)
    coord._connection_state = False
    coord._connection_state_changed(True)
    # Just verifies no exceptions.


def test_handle_connection_state_change_from_dispatcher(hass) -> None:
    coord = make_coord(hass)
    coord._connection_state = False
    coord._handle_connection_state_change({}, "connected")


# ---- async_verify_cos_functionality ---------------------------------------


async def test_verify_cos_disabled(hass) -> None:
    coord = make_coord(hass)
    coord._cos_enabled = False
    assert await coord.async_verify_cos_functionality() is False


async def test_verify_cos_no_devices(hass) -> None:
    coord = make_coord(hass)
    coord.devices = None
    assert await coord.async_verify_cos_functionality() is False


async def test_verify_cos_all_devices_pass(hass) -> None:
    dev = make_dev(1)
    dev.async_verify_cos = AsyncMock(return_value=True)
    coord = make_coord(hass, devices={1: dev})
    assert await coord.async_verify_cos_functionality() is True
    # Switched to long interval.
    assert coord.update_interval == timedelta(seconds=coord._poll_healthy)


async def test_verify_cos_majority_pass(hass) -> None:
    devs = {1: make_dev(1), 2: make_dev(2), 3: make_dev(3)}
    devs[1].async_verify_cos = AsyncMock(return_value=True)
    devs[2].async_verify_cos = AsyncMock(return_value=True)
    devs[3].async_verify_cos = AsyncMock(return_value=False)
    coord = make_coord(hass, devices=devs)
    assert await coord.async_verify_cos_functionality() is True


async def test_verify_cos_minority_pass(hass) -> None:
    devs = {1: make_dev(1), 2: make_dev(2), 3: make_dev(3)}
    devs[1].async_verify_cos = AsyncMock(return_value=True)
    devs[2].async_verify_cos = AsyncMock(return_value=False)
    devs[3].async_verify_cos = AsyncMock(return_value=False)
    coord = make_coord(hass, devices=devs)
    assert await coord.async_verify_cos_functionality() is False
    assert coord.update_interval == timedelta(seconds=coord._poll_backstop)


async def test_verify_cos_device_raises(hass) -> None:
    devs = {1: make_dev(1)}
    devs[1].async_verify_cos = AsyncMock(side_effect=RuntimeError("boom"))
    coord = make_coord(hass, devices=devs)
    assert await coord.async_verify_cos_functionality() is False


# ---- _async_update_data ----------------------------------------------------


async def test_update_data_no_connection_state(hass) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed
    coord = make_coord(hass)
    coord._connection_state = False
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_update_data_connection_mismatch(hass) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed
    coord = make_coord(hass)
    coord._connection_state = True
    coord.connection.is_connected = MagicMock(return_value=False)
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_update_data_happy_path(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        data = await coord._async_update_data()
    assert "1" in data
    dev.async_update.assert_called_once()


async def test_update_data_inits_when_missing(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._device_data = None
    coord.data = None
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()


async def test_update_data_pacing_called(hass) -> None:
    devs = {1: make_dev(1), 2: make_dev(2), 3: make_dev(3)}
    coord = make_coord(hass, devices=devs)
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    sleep_mock = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=sleep_mock):
        await coord._async_update_data()
    # 3 devices → 2 pacing sleeps (between devices) at minimum.
    assert sleep_mock.call_count >= 2


async def test_update_data_device_returns_none_state(hass) -> None:
    dev = make_dev(1)
    dev.get_state = MagicMock(return_value=None)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()


async def test_update_data_get_state_raises(hass) -> None:
    dev = make_dev(1)
    dev.get_state = MagicMock(side_effect=RuntimeError("state broke"))
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()
    # Device marked unavailable.
    assert coord._device_data["1"]["available"] is False


async def test_update_data_device_update_raises(hass) -> None:
    dev = make_dev(1)
    dev.async_update = AsyncMock(side_effect=RuntimeError("update broke"))
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()
    assert coord._device_data["1"]["available"] is False


async def test_update_data_save_state_exception(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock(side_effect=RuntimeError("disk"))
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()  # error swallowed


async def test_update_data_cos_verification_triggered(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._cos_enabled = True
    coord._last_cos_verification = None  # forces verification
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch.object(coord, "async_verify_cos_functionality", new=AsyncMock()) as v:
        with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
            await coord._async_update_data()
    v.assert_called()


async def test_update_data_cos_verify_exception(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._cos_enabled = True
    coord._last_cos_verification = None
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch.object(coord, "async_verify_cos_functionality",
                      new=AsyncMock(side_effect=RuntimeError("boom"))):
        with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
            await coord._async_update_data()


async def test_update_data_no_devices(hass) -> None:
    coord = make_coord(hass)
    coord._connection_state = True
    coord.devices = None
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    await coord._async_update_data()


async def test_update_data_outer_exception(hass) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed
    coord = make_coord(hass)
    coord._connection_state = True
    # Patch devices.items to raise.
    coord.devices = MagicMock()
    coord.devices.items = MagicMock(side_effect=RuntimeError("boom"))
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


# ---- async_set_heat_setpoint / cool / hvac / fan --------------------------


async def test_set_heat_setpoint(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    await coord.async_set_heat_setpoint("1", 72)
    dev.async_set_temperature.assert_called_with(72, "HEAT")


async def test_set_heat_setpoint_no_devices(hass) -> None:
    coord = make_coord(hass)
    coord.devices = None
    await coord.async_set_heat_setpoint("1", 72)  # logs error, returns


async def test_set_heat_setpoint_unknown(hass) -> None:
    coord = make_coord(hass, devices={1: make_dev(1)})
    await coord.async_set_heat_setpoint("99", 72)  # device not found


async def test_set_cool_setpoint(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    await coord.async_set_cool_setpoint("1", 72)
    dev.async_set_temperature.assert_called_with(72, "COOL")


async def test_set_cool_setpoint_no_devices(hass) -> None:
    coord = make_coord(hass)
    coord.devices = None
    await coord.async_set_cool_setpoint("1", 72)


async def test_set_cool_setpoint_unknown(hass) -> None:
    coord = make_coord(hass, devices={1: make_dev(1)})
    await coord.async_set_cool_setpoint("99", 72)


async def test_set_hvac_mode(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    await coord.async_set_hvac_mode("1", "heat")
    dev.async_set_hvac_mode.assert_called_with("heat")


async def test_set_hvac_mode_no_devices(hass) -> None:
    coord = make_coord(hass)
    coord.devices = None
    await coord.async_set_hvac_mode("1", "heat")


async def test_set_hvac_mode_unknown(hass) -> None:
    coord = make_coord(hass, devices={1: make_dev(1)})
    await coord.async_set_hvac_mode("99", "heat")


async def test_set_fan_mode(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    await coord.async_set_fan_mode("1", "auto")
    dev.async_set_fan_mode.assert_called_with("auto")


async def test_set_fan_mode_no_devices(hass) -> None:
    coord = make_coord(hass)
    coord.devices = None
    await coord.async_set_fan_mode("1", "auto")


async def test_set_fan_mode_unknown(hass) -> None:
    coord = make_coord(hass, devices={1: make_dev(1)})
    await coord.async_set_fan_mode("99", "auto")


# ---- async_shutdown --------------------------------------------------------


async def test_shutdown_runs_cleanly(hass) -> None:
    coord = make_coord(hass)
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    # No setup/teardown of background tasks anymore — shutdown should
    # just unsubscribe from dispatchers and persist state.
    await coord.async_shutdown()


# ---- _async_apply_state_to_device -----------------------------------------


async def test_apply_state_unknown_device(hass) -> None:
    coord = make_coord(hass)
    coord.devices = None
    await coord._async_apply_state_to_device("1", {"temperature": 70})


async def test_apply_state_to_device(hass) -> None:
    dev = make_dev(1)
    dev._state = {}
    coord = make_coord(hass, devices={"1": dev})
    await coord._async_apply_state_to_device("1", {"temperature": 70, "from_cache": True})
    assert dev._state["temperature"] == 70
    assert "from_cache" not in dev._state


async def test_update_data_strips_from_cache(hass) -> None:
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._device_data = {"1": {"from_cache": True}}
    coord.data = {"1": {"from_cache": True}}
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()
    assert "from_cache" not in coord._device_data["1"]
    assert "from_cache" not in coord.data["1"]


async def test_update_data_save_state_outer_exception(hass) -> None:
    """Force the save_state try/except in _async_update_data."""
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock(side_effect=RuntimeError("disk broke"))
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        # Should NOT raise — save error is logged and swallowed.
        await coord._async_update_data()


# ---- unsolicited-message listener (v0.2.7) --------------------------------


def _make_dev_for_unsolicited(address: int) -> MagicMock:
    """Stand-in device that mutates _state via _process_state_response."""
    state = {"temperature": None, "mode": None, "available": True}

    def process(command, response):
        if "=" not in response:
            return
        value = response.split("=", 1)[1].strip()
        if command == "TEMP":
            state["temperature"] = float(value.rstrip("F"))
        elif command == "MODE":
            state["mode"] = value

    dev = MagicMock()
    dev.address = address
    dev.available = True
    dev._process_state_response = MagicMock(side_effect=process)
    dev.get_state = MagicMock(side_effect=lambda: dict(state))
    return dev


async def test_handle_bus_message_updates_state_for_short_code(hass) -> None:
    """Short-form response code ``T=`` maps to TEMP and updates state."""
    dev = _make_dev_for_unsolicited(3)
    coord = make_coord(hass, devices={3: dev})
    coord.async_update_listeners = MagicMock()

    coord._handle_bus_message(coord.connection.config, "SN3Master Bedroom  T=76F")

    dev._process_state_response.assert_called_once_with("TEMP", "TEMP=76F")
    assert coord.data["3"]["temperature"] == 76.0
    coord.async_update_listeners.assert_called_once()


async def test_handle_bus_message_updates_state_for_multi_letter_code(hass) -> None:
    """Multi-letter codes like HVAC pass through unchanged."""
    dev = MagicMock()
    dev.address = 5
    dev.available = True
    dev._process_state_response = MagicMock()
    dev.get_state = MagicMock(return_value={"hvac_status": "G+Y1-W1-Y2-W2-B+O-"})
    coord = make_coord(hass, devices={5: dev})
    coord.async_update_listeners = MagicMock()

    coord._handle_bus_message(
        coord.connection.config, "SN5Office  HVAC=G+Y1-W1-Y2-W2-B+O-"
    )

    dev._process_state_response.assert_called_once_with(
        "HVAC", "HVAC=G+Y1-W1-Y2-W2-B+O-"
    )
    assert coord.data["5"]["hvac_status"] == "G+Y1-W1-Y2-W2-B+O-"


async def test_handle_bus_message_ignores_unknown_address(hass) -> None:
    dev = _make_dev_for_unsolicited(1)
    coord = make_coord(hass, devices={1: dev})
    coord.async_update_listeners = MagicMock()
    coord._handle_bus_message(coord.connection.config, "SN99Ghost  T=70F")
    dev._process_state_response.assert_not_called()
    coord.async_update_listeners.assert_not_called()


async def test_handle_bus_message_ignores_unknown_code(hass) -> None:
    dev = _make_dev_for_unsolicited(1)
    coord = make_coord(hass, devices={1: dev})
    coord.async_update_listeners = MagicMock()
    coord._handle_bus_message(coord.connection.config, "SN1Foo  XYZZY=42")
    dev._process_state_response.assert_not_called()


async def test_handle_bus_message_ignores_other_connection(hass) -> None:
    """A message from a different aprilaire connection must not cross over."""
    dev = _make_dev_for_unsolicited(1)
    coord = make_coord(hass, devices={1: dev})
    coord.async_update_listeners = MagicMock()
    coord._handle_bus_message({"other": "config"}, "SN1Foo  T=70F")
    dev._process_state_response.assert_not_called()


async def test_handle_bus_message_silent_when_state_unchanged(hass) -> None:
    """If the merged data matches what we already have, no listener notify."""
    dev = MagicMock()
    dev.address = 1
    dev.available = True
    dev._process_state_response = MagicMock()
    dev.get_state = MagicMock(return_value={"temperature": 70.0})
    coord = make_coord(hass, devices={1: dev})
    coord.data["1"] = {"temperature": 70.0, "available": True}
    coord.async_update_listeners = MagicMock()

    coord._handle_bus_message(coord.connection.config, "SN1  T=70F")
    coord.async_update_listeners.assert_not_called()


async def test_handle_bus_message_ignores_garbage(hass) -> None:
    dev = _make_dev_for_unsolicited(1)
    coord = make_coord(hass, devices={1: dev})
    coord._handle_bus_message(coord.connection.config, "")
    coord._handle_bus_message(coord.connection.config, None)  # type: ignore[arg-type]
    coord._handle_bus_message(coord.connection.config, "nothing useful here")
    dev._process_state_response.assert_not_called()


async def test_capability_cache_load_filters_by_entry_and_ttl(hass) -> None:
    """Loader drops entries from other config_entries AND stale entries."""
    coord = make_coord(hass)
    now_ts = 1_700_000_000.0  # arbitrary recent
    coord._cap_store = MagicMock()
    coord._cap_store.async_load = AsyncMock(return_value={
        "entryA:1": {"model": "8870", "firmware_version": "1.2",
                     "capabilities": {"is_heat_pump": False},
                     "cached_at_ts": now_ts},
        "entryA:2": {"model": "8870", "firmware_version": "1.2",
                     "capabilities": {"is_heat_pump": True},
                     # Stale: > 30 days old
                     "cached_at_ts": now_ts - (40 * 86400)},
        "entryB:1": {"model": "OTHER", "firmware_version": "x",
                     "capabilities": {},
                     "cached_at_ts": now_ts},
    })
    with patch("custom_components.aprilaire_8870.coordinator.dt_util.utcnow") as mock_now:
        mock_now.return_value.timestamp.return_value = now_ts
        await coord.async_load_capability_cache("entryA")
    # Only the fresh entryA:1 should make it through.
    assert "entryA:1" in coord._capability_cache
    assert "entryA:2" not in coord._capability_cache
    assert "entryB:1" not in coord._capability_cache


def test_get_cached_capabilities_returns_dict_or_none(hass) -> None:
    coord = make_coord(hass)
    coord._capability_cache = {"e1:5": {"model": "8870"}}
    assert coord.get_cached_capabilities("e1", 5) == {"model": "8870"}
    assert coord.get_cached_capabilities("e1", 99) is None
    assert coord.get_cached_capabilities("e2", 5) is None


async def test_save_capability_cache_entry_preserves_other_entries(hass) -> None:
    """Saving one device must not blow away cached entries for other devices."""
    coord = make_coord(hass)
    coord._cap_store = MagicMock()
    coord._cap_store.async_load = AsyncMock(return_value={
        "entryA:2": {"model": "old", "firmware_version": "0.0",
                     "capabilities": {}, "cached_at_ts": 1.0},
    })
    coord._cap_store.async_save = AsyncMock()
    await coord.async_save_capability_cache_entry(
        "entryA", 5, "8870", "1.2", {"is_heat_pump": True}
    )
    saved = coord._cap_store.async_save.call_args.args[0]
    assert "entryA:2" in saved  # preserved
    assert saved["entryA:5"]["model"] == "8870"
    assert saved["entryA:5"]["capabilities"] == {"is_heat_pump": True}
    # In-memory mirror updated too.
    assert coord._capability_cache["entryA:5"]["model"] == "8870"


async def test_connection_reconnect_resets_unsupported_commands(hass) -> None:
    """When the bus reconnects, every device gets its unsupported set cleared."""
    dev1 = MagicMock()
    dev1.reset_unsupported_commands = MagicMock()
    dev2 = MagicMock()
    dev2.reset_unsupported_commands = MagicMock()
    coord = make_coord(hass, devices={1: dev1, 2: dev2})
    coord._connection_state = False  # currently disconnected

    coord._connection_state_changed(True)

    dev1.reset_unsupported_commands.assert_called_once()
    dev2.reset_unsupported_commands.assert_called_once()

