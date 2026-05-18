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


async def test_update_data_does_not_trigger_cos_verification_inline(hass) -> None:
    """v0.3.0: COS verification moved to its own timer, not the poll path."""
    dev = make_dev(1)
    coord = make_coord(hass, devices={1: dev})
    coord._connection_state = True
    coord._cos_enabled = True
    coord._last_cos_verification = None
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    with patch.object(coord, "async_verify_cos_functionality", new=AsyncMock()) as v:
        with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
            await coord._async_update_data()
    # The inline path is gone — verification fires from its own scheduler.
    v.assert_not_called()


async def test_start_cos_verification_scheduler_subscribes_to_time_interval(hass) -> None:
    coord = make_coord(hass)
    coord._cos_enabled = True
    with patch(
        "custom_components.aprilaire_8870.coordinator.async_track_time_interval"
    ) as mock_track:
        mock_track.return_value = MagicMock()
        coord.async_start_cos_verification_scheduler()
    assert mock_track.called
    assert coord._cos_verification_unsub is not None
    # Idempotent — second call is a no-op.
    with patch(
        "custom_components.aprilaire_8870.coordinator.async_track_time_interval"
    ) as mock_track2:
        coord.async_start_cos_verification_scheduler()
    mock_track2.assert_not_called()


async def test_start_cos_verification_scheduler_no_op_when_disabled(hass) -> None:
    coord = make_coord(hass)
    coord._cos_enabled = False
    coord.async_start_cos_verification_scheduler()
    assert coord._cos_verification_unsub is None


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


async def test_async_sync_time_sends_TIME_and_DATE_globals(hass) -> None:
    """v0.4.0: time sync issues SN0 TIME=HHMM + SN0 DATE=MMDDYY."""
    import datetime
    from unittest.mock import patch as _patch
    coord = make_coord(hass, devices={1: MagicMock(), 2: MagicMock()})
    coord.connection.is_connected = MagicMock(return_value=True)
    coord.connection.async_send_global_command = AsyncMock(return_value={})
    fixed_now = datetime.datetime(2026, 3, 7, 14, 5)  # March 7 2026, 2:05pm
    with _patch(
        "custom_components.aprilaire_8870.coordinator.dt_util.now",
        return_value=fixed_now,
    ):
        ok = await coord.async_sync_time_to_thermostats()
    assert ok is True
    calls = coord.connection.async_send_global_command.call_args_list
    sent_commands = [c.args[0] for c in calls]
    assert "TIME=1405" in sent_commands
    assert "DATE=030726" in sent_commands


async def test_async_sync_time_pads_single_digit_values(hass) -> None:
    """Leading zeros required for hour/minute/month/day/year."""
    import datetime
    from unittest.mock import patch as _patch
    coord = make_coord(hass, devices={1: MagicMock()})
    coord.connection.is_connected = MagicMock(return_value=True)
    coord.connection.async_send_global_command = AsyncMock(return_value={})
    fixed_now = datetime.datetime(2009, 1, 5, 6, 3)  # Jan 5 2009, 6:03am
    with _patch(
        "custom_components.aprilaire_8870.coordinator.dt_util.now",
        return_value=fixed_now,
    ):
        await coord.async_sync_time_to_thermostats()
    calls = [c.args[0] for c in coord.connection.async_send_global_command.call_args_list]
    assert "TIME=0603" in calls
    assert "DATE=010509" in calls


async def test_async_sync_time_skips_when_disconnected(hass) -> None:
    coord = make_coord(hass, devices={1: MagicMock()})
    coord.connection.is_connected = MagicMock(return_value=False)
    coord.connection.async_send_global_command = AsyncMock()
    ok = await coord.async_sync_time_to_thermostats()
    assert ok is False
    coord.connection.async_send_global_command.assert_not_called()


async def test_async_sync_time_swallows_exceptions(hass) -> None:
    coord = make_coord(hass, devices={1: MagicMock()})
    coord.connection.is_connected = MagicMock(return_value=True)
    coord.connection.async_send_global_command = AsyncMock(side_effect=RuntimeError("bus"))
    # Should not raise.
    ok = await coord.async_sync_time_to_thermostats()
    assert ok is False


async def test_start_time_sync_scheduler_idempotent(hass) -> None:
    coord = make_coord(hass)
    with patch(
        "custom_components.aprilaire_8870.coordinator.async_track_time_interval"
    ) as mock_track:
        mock_track.return_value = MagicMock()
        coord.async_start_time_sync_scheduler()
        # Second call no-ops.
        coord.async_start_time_sync_scheduler()
    # Called exactly once.
    assert mock_track.call_count == 1
    assert coord._time_sync_unsub is not None


async def test_bulk_poll_essentials_routes_responses_per_device(hass) -> None:
    """v0.4.0: SN0 globals dispatch responses into per-device state."""
    dev1 = MagicMock()
    dev1._process_state_response = MagicMock()
    dev2 = MagicMock()
    dev2._process_state_response = MagicMock()
    coord = make_coord(hass, devices={1: dev1, 2: dev2})
    coord.connection.is_connected = MagicMock(return_value=True)
    coord.connection.async_send_global_command = AsyncMock(side_effect=[
        {1: "SN1 TEMP=72F", 2: "SN2 TEMP=70F"},     # TEMP
        {1: "SN1 MODE=COOL", 2: "SN2 MODE=HEAT"},    # MODE
        {1: "SN1 FAN=AUTO"},                          # FAN (only dev 1)
        {1: "SN1 HVAC=G-Y1-W1-Y2-W2-B-O-",
         2: "SN2 HVAC=G+Y1+W1-Y2-W2-B-O+"},          # HVAC
        {1: "SN1 HOLD=OFF", 2: "SN2 HOLD=OFF"},      # HOLD
        {},                                            # SH (no responses)
        {1: "SN1 SC=75F", 2: "SN2 SC=78F"},          # SC
    ])

    await coord._async_bulk_poll_essentials()

    # Each device received one routed call per response, NOT per command.
    # dev1 got 6 responses (TEMP, MODE, FAN, HVAC, HOLD, SC).
    # dev2 got 5 (TEMP, MODE, HVAC, HOLD, SC) — missed FAN and SH.
    assert dev1._process_state_response.call_count == 6
    assert dev2._process_state_response.call_count == 5
    # Spot-check the routing: dev1's first call should be TEMP=72F.
    first_dev1 = dev1._process_state_response.call_args_list[0]
    assert first_dev1.args == ("TEMP", "SN1 TEMP=72F")


async def test_bulk_poll_essentials_returns_early_when_disconnected(hass) -> None:
    coord = make_coord(hass, devices={1: MagicMock()})
    coord.connection.is_connected = MagicMock(return_value=False)
    coord.connection.async_send_global_command = AsyncMock()
    await coord._async_bulk_poll_essentials()
    coord.connection.async_send_global_command.assert_not_called()


async def test_bulk_poll_essentials_swallows_global_command_exceptions(hass) -> None:
    dev = MagicMock()
    dev._process_state_response = MagicMock()
    coord = make_coord(hass, devices={1: dev})
    coord.connection.is_connected = MagicMock(return_value=True)
    coord.connection.async_send_global_command = AsyncMock(
        side_effect=RuntimeError("bus glitch")
    )
    # Should not raise.
    await coord._async_bulk_poll_essentials()


async def test_bulk_poll_essentials_returns_responder_set(hass) -> None:
    """v0.4.1: bulk pass returns set of addresses that answered."""
    dev1 = MagicMock()
    dev1._process_state_response = MagicMock()
    dev1.monitor_humidity = False
    dev1.monitor_outdoor_temp = False
    dev1._consecutive_full_poll_failures = 0
    dev1._slow_keepalive_mode = False
    dev2 = MagicMock()
    dev2._process_state_response = MagicMock()
    dev2.monitor_humidity = False
    dev2.monitor_outdoor_temp = False
    dev2._consecutive_full_poll_failures = 0
    dev2._slow_keepalive_mode = False
    coord = make_coord(hass, devices={1: dev1, 2: dev2})
    coord.connection.is_connected = MagicMock(return_value=True)
    # dev1 responds to TEMP; dev2 responds to MODE — both alive on bulk.
    coord.connection.async_send_global_command = AsyncMock(side_effect=[
        {1: "SN1 TEMP=72F"},
        {2: "SN2 MODE=COOL"},
        {}, {}, {}, {}, {},
    ])
    responded = await coord._async_bulk_poll_essentials()
    assert responded == {1, 2}


async def test_bulk_poll_essentials_includes_hum_ot_when_flags_on(hass) -> None:
    """v0.4.1: bulk HUM/OT when monitor flags are set on devices."""
    dev = MagicMock()
    dev._process_state_response = MagicMock()
    dev.monitor_humidity = True
    dev.monitor_outdoor_temp = True
    dev._humidity_supported = None  # unknown — should query
    dev._outdoor_temp_supported = None
    dev._consecutive_full_poll_failures = 0
    dev._slow_keepalive_mode = False
    coord = make_coord(hass, devices={1: dev})
    coord.connection.is_connected = MagicMock(return_value=True)
    send_mock = AsyncMock(return_value={})
    coord.connection.async_send_global_command = send_mock
    await coord._async_bulk_poll_essentials()
    sent_commands = [c.args[0] for c in send_mock.call_args_list]
    # Must include HUM? and OT? alongside the essentials.
    assert "HUM?" in sent_commands
    assert "OT?" in sent_commands


async def test_bulk_poll_essentials_skips_hum_ot_when_all_devices_unsupported(hass) -> None:
    """v0.4.7: don't waste ~6s of bus time per cycle querying HUM/OT when
    every device has already reported the firmware "no sensor wired"
    sentinel (--%/--F). Resumes querying if any device flips back to
    supported (e.g. unsolicited HUM= broadcast with a real value).
    """
    dev_a = MagicMock()
    dev_a._process_state_response = MagicMock()
    dev_a.monitor_humidity = True
    dev_a.monitor_outdoor_temp = True
    dev_a._humidity_supported = False  # confirmed --%
    dev_a._outdoor_temp_supported = False
    dev_a._consecutive_full_poll_failures = 0
    dev_a._slow_keepalive_mode = False
    dev_b = MagicMock()
    dev_b._process_state_response = MagicMock()
    dev_b.monitor_humidity = True
    dev_b.monitor_outdoor_temp = True
    dev_b._humidity_supported = False
    dev_b._outdoor_temp_supported = False
    dev_b._consecutive_full_poll_failures = 0
    dev_b._slow_keepalive_mode = False
    coord = make_coord(hass, devices={1: dev_a, 2: dev_b})
    coord.connection.is_connected = MagicMock(return_value=True)
    send_mock = AsyncMock(return_value={})
    coord.connection.async_send_global_command = send_mock
    await coord._async_bulk_poll_essentials()
    sent_commands = [c.args[0] for c in send_mock.call_args_list]
    # Essentials still flow.
    assert "TEMP?" in sent_commands
    # HUM/OT suppressed because every device says "no sensor wired".
    assert "HUM?" not in sent_commands
    assert "OT?" not in sent_commands


async def test_set_heat_setpoint_targeted_update_no_full_poll(hass) -> None:
    """v0.4.8: a successful setpoint change publishes a targeted single-
    device state update instead of triggering a full bulk poll. UI
    sees the new value immediately; the bus is undisturbed.
    """
    dev = make_dev(1)
    dev.async_set_temperature = AsyncMock(return_value=True)
    # Real device.get_state returns _state ONLY (no "available" key —
    # that's a separate attribute). The publish must merge in the
    # available flag itself; reproduces the v0.4.10 bug where omitting
    # it caused the entity to immediately go "unavailable" after every
    # successful setpoint change.
    dev.get_state = MagicMock(return_value={"heat_setpoint": 72.0})
    dev.available = True
    coord = make_coord(hass, devices={1: dev})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    coord.async_update_listeners = MagicMock()
    # Make sure we'd catch a stray full poll.
    coord._async_update_data = AsyncMock(side_effect=AssertionError("must not full-poll"))

    ok = await coord.async_set_heat_setpoint("1", 72.0)
    assert ok is True
    # Targeted publish landed on data and notified.
    assert coord.data["1"]["heat_setpoint"] == 72.0
    # CRITICAL: available must be preserved so the entity doesn't go unavailable.
    assert coord.data["1"]["available"] is True
    coord.async_update_listeners.assert_called_once()


async def test_publish_single_device_state_drops_from_cache(hass) -> None:
    """v0.4.10: after a successful set we've just talked to the device,
    so the from_cache marker is stale — drop it so HA stops gating
    operations behind the from_cache check.
    """
    dev = make_dev(1)
    dev.get_state = MagicMock(return_value={"heat_setpoint": 72.0})
    dev.available = True
    coord = make_coord(hass, devices={1: dev})
    coord._device_data = {"1": {"available": True, "from_cache": True}}
    coord.data = {"1": {"available": True, "from_cache": True}}
    coord.async_update_listeners = MagicMock()
    coord._publish_single_device_state(dev)
    assert "from_cache" not in coord.data["1"]
    assert "from_cache" not in coord._device_data["1"]


async def test_set_hold_targeted_update(hass) -> None:
    """v0.4.9: async_set_hold for the network override switch publishes
    a targeted single-device state update so the switch UI sees the
    change immediately."""
    dev = make_dev(1)
    dev.async_set_hold = AsyncMock(return_value=True)
    dev.get_state = MagicMock(return_value={"hold_status": "ON", "available": True})
    coord = make_coord(hass, devices={1: dev})
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    coord.async_update_listeners = MagicMock()
    ok = await coord.async_set_hold("1", True)
    assert ok is True
    assert coord.data["1"]["hold_status"] == "ON"
    coord.async_update_listeners.assert_called_once()


async def test_set_hold_missing_method_returns_false(hass) -> None:
    """If the device doesn't support HOLD (no async_set_hold attr),
    the coordinator returns False without trying to call it."""
    dev = make_dev(1)
    del dev.async_set_hold  # remove the auto-generated mock
    coord = make_coord(hass, devices={1: dev})
    assert await coord.async_set_hold("1", True) is False


async def test_set_heat_setpoint_failure_skips_publish(hass) -> None:
    """Failed sets must not touch coordinator.data or notify listeners —
    otherwise the UI would briefly show a value the thermostat doesn't
    actually have."""
    dev = make_dev(1)
    dev.async_set_temperature = AsyncMock(return_value=False)
    dev.get_state = MagicMock(return_value={"heat_setpoint": 999.0})
    coord = make_coord(hass, devices={1: dev})
    coord._device_data = {"1": {"heat_setpoint": 70.0, "available": True}}
    coord.data = {"1": {"heat_setpoint": 70.0, "available": True}}
    coord.async_update_listeners = MagicMock()

    ok = await coord.async_set_heat_setpoint("1", 72.0)
    assert ok is False
    # No state mutation, no listener notification.
    assert coord.data["1"]["heat_setpoint"] == 70.0
    coord.async_update_listeners.assert_not_called()


async def test_bulk_poll_essentials_queries_hum_ot_if_any_device_supports(hass) -> None:
    """If even one device still has the sensor supported (or unknown),
    we keep querying — the bulk SN0 cost is fixed regardless of how
    many devices answer with real values."""
    dev_with = MagicMock()
    dev_with._process_state_response = MagicMock()
    dev_with.monitor_humidity = True
    dev_with.monitor_outdoor_temp = True
    dev_with._humidity_supported = True  # this one has the sensor
    dev_with._outdoor_temp_supported = False
    dev_with._consecutive_full_poll_failures = 0
    dev_with._slow_keepalive_mode = False
    dev_without = MagicMock()
    dev_without._process_state_response = MagicMock()
    dev_without.monitor_humidity = True
    dev_without.monitor_outdoor_temp = True
    dev_without._humidity_supported = False
    dev_without._outdoor_temp_supported = False
    dev_without._consecutive_full_poll_failures = 0
    dev_without._slow_keepalive_mode = False
    coord = make_coord(hass, devices={1: dev_with, 2: dev_without})
    coord.connection.is_connected = MagicMock(return_value=True)
    send_mock = AsyncMock(return_value={})
    coord.connection.async_send_global_command = send_mock
    await coord._async_bulk_poll_essentials()
    sent_commands = [c.args[0] for c in send_mock.call_args_list]
    assert "HUM?" in sent_commands  # dev_with still wants it
    assert "OT?" not in sent_commands  # neither has it


async def test_bulk_poll_essentials_resets_circuit_breaker(hass) -> None:
    """v0.4.1: responder addresses get their slow-keepalive cleared."""
    dev = MagicMock()
    dev._process_state_response = MagicMock()
    dev.monitor_humidity = False
    dev.monitor_outdoor_temp = False
    dev._consecutive_full_poll_failures = 4
    dev._slow_keepalive_mode = True
    coord = make_coord(hass, devices={1: dev})
    coord.connection.is_connected = MagicMock(return_value=True)
    coord.connection.async_send_global_command = AsyncMock(side_effect=[
        {1: "SN1 TEMP=72F"}, {}, {}, {}, {}, {}, {},
    ])
    await coord._async_bulk_poll_essentials()
    assert dev._consecutive_full_poll_failures == 0
    assert dev._slow_keepalive_mode is False


async def test_update_data_skips_essentials_for_bulk_responders(hass) -> None:
    """v0.4.1: per-device async_update is skipped for addresses bulk handled."""
    dev1 = make_dev(1)
    dev2 = make_dev(2)
    coord = make_coord(hass, devices={1: dev1, 2: dev2})
    coord._connection_state = True
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    # Force the bulk pass to claim address 1 only — address 2 falls through.
    coord._async_bulk_poll_essentials = AsyncMock(return_value={1})
    with patch("custom_components.aprilaire_8870.coordinator.asyncio.sleep", new=AsyncMock()):
        await coord._async_update_data()
    # dev1 was bulked — async_update called with skip_essentials=True
    dev1.async_update.assert_called_once_with(skip_essentials=True)
    # dev2 was NOT bulked — full per-device poll
    dev2.async_update.assert_called_once_with(skip_essentials=False)


async def test_cos_verification_backs_off_when_zero_accept(hass) -> None:
    """When NO device accepts COS flags, verification interval stretches to 6h."""
    dev = MagicMock()
    dev.async_verify_cos = AsyncMock(return_value=False)
    coord = make_coord(hass, devices={1: dev, 2: dev})
    coord._cos_enabled = True
    coord._cos_verification_interval = 1800  # default 30 min
    assert await coord.async_verify_cos_functionality() is False
    assert coord._cos_verification_interval == 6 * 3600


async def test_cos_verification_keeps_default_interval_when_some_accept(hass) -> None:
    dev_ok = MagicMock()
    dev_ok.async_verify_cos = AsyncMock(return_value=True)
    dev_bad = MagicMock()
    dev_bad.async_verify_cos = AsyncMock(return_value=False)
    coord = make_coord(hass, devices={1: dev_ok, 2: dev_bad})
    coord._cos_enabled = True
    coord._cos_verification_interval = 1800
    # 1/2 verified = "mostly verified" by the majority rule.
    assert await coord.async_verify_cos_functionality() is True
    # Interval unchanged from default.
    assert coord._cos_verification_interval == 1800


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

