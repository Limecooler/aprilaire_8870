"""Tests for climate.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.aprilaire_8870 import climate as climate_mod
from custom_components.aprilaire_8870.const import DOMAIN


def make_coordinator(data: dict | None = None, devices: dict | None = None):
    coord = MagicMock()
    coord.data = data if data is not None else {}
    coord.devices = devices or {}
    coord.last_update_success = True
    coord.async_set_heat_setpoint = AsyncMock()
    coord.async_set_cool_setpoint = AsyncMock()
    coord.async_set_hvac_mode = AsyncMock()
    coord.async_set_fan_mode = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def make_device(address=1, **overrides):
    base = {
        "address": address,
        "name": f"Aprilaire {address}",
        "unique_id": f"{DOMAIN}_{address}",
        "model": "8870",
        "firmware_version": "1.0",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def make_climate(data=None, devices=None, address=1):
    coord = make_coordinator(data=data or {}, devices=devices or {})
    dev = make_device(address=address)
    return climate_mod.AprilaireClimate(coord, dev)


# ---- name behaviour --------------------------------------------------------


def test_primary_entity_name_uses_device_name_only() -> None:
    c = make_climate(data={"1": {"available": True}})
    # With _attr_name = None and _attr_has_entity_name = True, the entity
    # shows up as just the device name.
    assert c.name is None or c.name == "Aprilaire 1"


def test_unique_id_uses_address_and_domain() -> None:
    c = make_climate(data={})
    assert c.unique_id == f"{DOMAIN}_1_climate"


# ---- device_info -----------------------------------------------------------


def test_device_info_with_device() -> None:
    c = make_climate(data={})
    info = c.device_info
    assert info["model"] == "8870"
    assert info["sw_version"] == "1.0"


def test_device_info_without_device() -> None:
    coord = make_coordinator(data={})
    c = climate_mod.AprilaireClimate(coord, None)
    info = c.device_info
    assert info["model"] == "8870"


def test_device_id_falls_back_when_no_address() -> None:
    coord = make_coordinator(data={})
    dev = SimpleNamespace(name="x", unique_id="x")
    c = climate_mod.AprilaireClimate(coord, dev)
    assert c._device_id.startswith("unknown_")


# ---- available -------------------------------------------------------------


def test_available_true() -> None:
    c = make_climate(data={"1": {"available": True}})
    assert c.available is True


def test_available_no_data() -> None:
    c = make_climate(data={})
    assert c.available is False


def test_available_no_coordinator() -> None:
    c = climate_mod.AprilaireClimate.__new__(climate_mod.AprilaireClimate)
    c.coordinator = None
    c._device_id = "1"
    assert c.available is False


def test_available_from_cache() -> None:
    c = make_climate(data={"1": {"from_cache": True, "available": False}})
    assert c.available is True


# ---- state properties ------------------------------------------------------


def test_current_temperature() -> None:
    c = make_climate(data={"1": {"temperature": 70, "mode": "OFF"}})
    assert c.current_temperature == 70


def test_target_temperature_heat() -> None:
    c = make_climate(data={"1": {"mode": "HEAT", "heat_setpoint": 65}})
    assert c.target_temperature == 65


def test_target_temperature_cool() -> None:
    c = make_climate(data={"1": {"mode": "COOL", "cool_setpoint": 74}})
    assert c.target_temperature == 74


def test_target_temperature_other_mode() -> None:
    c = make_climate(data={"1": {"mode": "AUTO", "heat_setpoint": 65, "cool_setpoint": 74}})
    # AUTO maps to HVACMode.AUTO; target_temperature returns None in that case
    assert c.target_temperature is None


def test_current_humidity() -> None:
    c = make_climate(data={"1": {"humidity": 40}})
    assert c.current_humidity == 40


def test_hvac_mode_known() -> None:
    c = make_climate(data={"1": {"mode": "HEAT"}})
    assert c.hvac_mode == HVACMode.HEAT


def test_hvac_mode_unknown_defaults_to_off() -> None:
    c = make_climate(data={"1": {"mode": "UNKNOWN"}})
    assert c.hvac_mode == HVACMode.OFF


def test_hvac_action_heating_w1_on() -> None:
    """Heat-strip W1 active → HEATING regardless of mode."""
    c = make_climate(data={"1": {"hvac_status": "G+Y1-W1+Y2-W2-B-O-", "mode": "HEAT"}})
    assert c.hvac_action == HVACAction.HEATING


def test_hvac_action_cooling_y1_on_in_cool_mode() -> None:
    """Y1 active in cool mode → COOLING. Regression: previously the
    substring '+W1' matched 'Y1+W1' falsely and returned HEATING."""
    c = make_climate(data={"1": {"hvac_status": "G+Y1+W1-Y2-W2-B-O+", "mode": "COOL"}})
    assert c.hvac_action == HVACAction.COOLING


def test_hvac_action_heat_pump_compressor_in_heat_mode() -> None:
    """On a heat pump the compressor (Y) is the heat source in HEAT mode."""
    c = make_climate(data={"1": {"hvac_status": "G+Y1+W1-Y2-W2-B+O-", "mode": "HEAT"}})
    assert c.hvac_action == HVACAction.HEATING


def test_hvac_action_fan_only() -> None:
    """G active, no compressor or heat → FAN."""
    c = make_climate(data={"1": {"hvac_status": "G+Y1-W1-Y2-W2-B-O-", "mode": "FAN"}})
    assert c.hvac_action == HVACAction.FAN


def test_hvac_action_idle_when_no_status() -> None:
    c = make_climate(data={"1": {}})
    assert c.hvac_action == HVACAction.IDLE


def test_hvac_action_idle_when_all_relays_off() -> None:
    """Reversing valve flipped (B+ or O+) on its own is NOT activity."""
    c = make_climate(data={"1": {"hvac_status": "G-Y1-W1-Y2-W2-B+O-", "mode": "COOL"}})
    assert c.hvac_action == HVACAction.IDLE


def test_hvac_action_w2_alone_means_heating() -> None:
    """W2 (heat stage 2) active should be heating even without W1."""
    c = make_climate(data={"1": {"hvac_status": "G+Y1-W1-Y2-W2+B-O-", "mode": "HEAT"}})
    assert c.hvac_action == HVACAction.HEATING


def test_hvac_action_y2_alone_in_auto_mode_is_cooling() -> None:
    """Y2 active in AUTO mode (non-heat) → COOLING."""
    c = make_climate(data={"1": {"hvac_status": "G+Y1-W1-Y2+W2-B-O+", "mode": "AUTO"}})
    assert c.hvac_action == HVACAction.COOLING


def test_fan_mode_default_auto() -> None:
    c = make_climate(data={"1": {}})
    assert c.fan_mode is not None


def test_fan_mode_circulate() -> None:
    c = make_climate(data={"1": {"fan_mode": "CIRC"}})
    assert c.fan_mode == "circulate"


def test_extra_state_attributes_all_present() -> None:
    c = make_climate(data={
        "1": {
            "outdoor_temperature": 50.0,
            "humidity": 40,
            "hvac_status": "abc",
            "filter_alarm": True,
            "hold_status": "ON",
            "from_cache": True,
        }
    })
    attrs = c.extra_state_attributes
    assert "outdoor_temperature" in attrs
    assert "indoor_humidity" in attrs
    assert "hvac_relay_status" in attrs
    assert "filter_status" in attrs
    assert "hold_status" in attrs
    assert attrs["from_cache"] is True


def test_extra_state_attributes_empty_when_no_data() -> None:
    c = make_climate(data={"1": {}})
    assert c.extra_state_attributes == {}


# ---- async setters ---------------------------------------------------------


async def test_set_temperature_no_attr() -> None:
    c = make_climate(data={"1": {"available": True}})
    await c.async_set_temperature()
    c.coordinator.async_set_heat_setpoint.assert_not_called()


async def test_set_temperature_cached_state_skipped() -> None:
    c = make_climate(data={"1": {"from_cache": True, "available": False}})
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_set_heat_setpoint.assert_not_called()


async def test_set_temperature_heat_mode() -> None:
    c = make_climate(data={"1": {"mode": "HEAT", "available": True}})
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_set_heat_setpoint.assert_called_with("1", 72)


async def test_set_temperature_cool_mode() -> None:
    c = make_climate(data={"1": {"mode": "COOL", "available": True}})
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_set_cool_setpoint.assert_called_with("1", 72)


async def test_set_temperature_auto_heating_action() -> None:
    c = make_climate(data={"1": {"mode": "AUTO", "hvac_status": "G+W1+Y-W-B-O-", "available": True}})
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_set_heat_setpoint.assert_called_with("1", 72)


async def test_set_temperature_auto_cooling_action() -> None:
    c = make_climate(data={"1": {"mode": "AUTO", "hvac_status": "G+Y1+W-Y-W-B-O-", "available": True}})
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_set_cool_setpoint.assert_called_with("1", 72)


async def test_set_temperature_auto_idle_action() -> None:
    c = make_climate(data={"1": {"mode": "AUTO", "available": True}})
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_set_heat_setpoint.assert_called_with("1", 72)


async def test_set_temperature_skips_refresh_on_failure() -> None:
    """v0.4.7 regression: failed set_temperature must NOT trigger a
    full coordinator refresh. The pre-v0.4.7 behavior was an
    unconditional ``async_request_refresh()`` after every set, so a
    user clicking the climate slider while one device had a stale
    cached controller_type kicked off a 30s+ full bus poll every
    time — drowning out subsequent commands.
    """
    c = make_climate(data={"1": {"mode": "HEAT", "available": True}})
    c.coordinator.async_set_heat_setpoint = AsyncMock(return_value=False)
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_request_refresh.assert_not_called()


async def test_set_temperature_refreshes_on_success() -> None:
    """Mirror of the above: a successful set still requests a refresh
    so the UI picks up the new value promptly.
    """
    c = make_climate(data={"1": {"mode": "HEAT", "available": True}})
    c.coordinator.async_set_heat_setpoint = AsyncMock(return_value=True)
    await c.async_set_temperature(**{ATTR_TEMPERATURE: 72})
    c.coordinator.async_request_refresh.assert_called_once()


async def test_set_hvac_mode_known() -> None:
    c = make_climate(data={"1": {"available": True}})
    await c.async_set_hvac_mode(HVACMode.HEAT)
    c.coordinator.async_set_hvac_mode.assert_called_with("1", HVACMode.HEAT)


async def test_set_hvac_mode_unknown() -> None:
    c = make_climate(data={"1": {"available": True}})
    # SimpleNamespace as a stand-in for an invalid HA mode
    await c.async_set_hvac_mode("not-a-mode")  # type: ignore[arg-type]
    c.coordinator.async_set_hvac_mode.assert_not_called()


async def test_set_hvac_mode_cached() -> None:
    c = make_climate(data={"1": {"from_cache": True, "available": False}})
    await c.async_set_hvac_mode(HVACMode.HEAT)
    c.coordinator.async_set_hvac_mode.assert_not_called()


async def test_set_fan_mode_known() -> None:
    c = make_climate(data={"1": {"available": True}})
    await c.async_set_fan_mode("auto")
    c.coordinator.async_set_fan_mode.assert_called_with("1", "auto")


async def test_set_fan_mode_unknown() -> None:
    c = make_climate(data={"1": {"available": True}})
    await c.async_set_fan_mode("badmode")
    c.coordinator.async_set_fan_mode.assert_not_called()


async def test_set_fan_mode_cached() -> None:
    c = make_climate(data={"1": {"from_cache": True, "available": False}})
    await c.async_set_fan_mode("auto")
    c.coordinator.async_set_fan_mode.assert_not_called()


async def test_async_turn_on_when_off() -> None:
    c = make_climate(data={"1": {"mode": "OFF", "available": True}})
    await c.async_turn_on()
    c.coordinator.async_set_hvac_mode.assert_called_with("1", HVACMode.AUTO)


async def test_async_turn_on_when_not_off_is_noop() -> None:
    c = make_climate(data={"1": {"mode": "HEAT", "available": True}})
    await c.async_turn_on()
    c.coordinator.async_set_hvac_mode.assert_not_called()


async def test_async_turn_off() -> None:
    c = make_climate(data={"1": {"mode": "HEAT", "available": True}})
    await c.async_turn_off()
    c.coordinator.async_set_hvac_mode.assert_called_with("1", HVACMode.OFF)


# ---- setup_entry -----------------------------------------------------------


def test_climate_setup_entry_creates_entities(hass) -> None:
    from custom_components.aprilaire_8870 import AprilaireRuntimeData
    coord = make_coordinator(data={})
    entry = MagicMock()
    entry.entry_id = "abc"
    entry.runtime_data = AprilaireRuntimeData(
        coordinator=coord,
        connection=MagicMock(),
        device_manager=MagicMock(),
        discovered_addresses=["1", "2"],
        devices={"1": make_device(1)},
    )
    added = []

    def fake_add(entities):
        added.extend(entities)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        climate_mod.async_setup_entry(hass, entry, fake_add)
    )
    assert len(added) == 2  # one initialized, one placeholder


# ---- service-dispatcher wiring (v0.4.0) ------------------------------------


async def test_async_added_to_hass_subscribes_to_all_service_signals(hass) -> None:
    """v0.4.0 service-wiring fix: climate entity subscribes to per-entity
    dispatcher signals so the previously no-op services actually fire."""
    from custom_components.aprilaire_8870.const import (
        SERVICE_SIGNAL_SET_TEXT_MESSAGE,
        SERVICE_SIGNAL_SET_BACKLIGHT,
        SERVICE_SIGNAL_RESET_FILTER,
        SERVICE_SIGNAL_SET_LOCKOUT,
        SERVICE_SIGNAL_CONFIGURE_COS,
    )
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    dev = make_device(address=5)
    dev.async_set_text_message = AsyncMock(return_value=True)
    dev.async_set_backlight = AsyncMock(return_value=True)
    dev.async_reset_filter = AsyncMock(return_value=True)
    dev.async_set_lockout = AsyncMock(return_value=True)
    dev.async_configure_cos = AsyncMock(return_value=True)
    coord = make_coordinator(data={"5": {"available": True}})
    entity = climate_mod.AprilaireClimate(coord, dev)
    entity.hass = hass
    entity.platform = MagicMock()
    entity.entity_id = "climate.aprilaire_5"
    await entity.async_added_to_hass()

    async_dispatcher_send(
        hass,
        f"{SERVICE_SIGNAL_SET_TEXT_MESSAGE}_climate.aprilaire_5",
        "Hello", "tmpmes",
    )
    await hass.async_block_till_done()
    dev.async_set_text_message.assert_called_once_with("Hello", "tmpmes")

    async_dispatcher_send(
        hass, f"{SERVICE_SIGNAL_SET_BACKLIGHT}_climate.aprilaire_5", True, 30,
    )
    await hass.async_block_till_done()
    dev.async_set_backlight.assert_called_once()

    async_dispatcher_send(
        hass, f"{SERVICE_SIGNAL_RESET_FILTER}_climate.aprilaire_5",
    )
    await hass.async_block_till_done()
    dev.async_reset_filter.assert_called_once()

    async_dispatcher_send(
        hass, f"{SERVICE_SIGNAL_SET_LOCKOUT}_climate.aprilaire_5",
        0, 1, 2, 1, 30, 5,
    )
    await hass.async_block_till_done()
    dev.async_set_lockout.assert_called_once_with(
        fan_lockout=0, mode_lockout=1, setpoint_lockout=2,
        network_lockout=1, lockout_time=30, lockout_limit=5,
    )

    async_dispatcher_send(
        hass, f"{SERVICE_SIGNAL_CONFIGURE_COS}_climate.aprilaire_5",
        ["c1", "c2"],
    )
    await hass.async_block_till_done()
    dev.async_configure_cos.assert_called_once_with(["c1", "c2"])


async def test_set_backlight_state_false_is_noop(hass) -> None:
    """BLTON has no off command; state=False shouldn't fire the device method."""
    from custom_components.aprilaire_8870.const import SERVICE_SIGNAL_SET_BACKLIGHT
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    dev = make_device(address=5)
    dev.async_set_backlight = AsyncMock(return_value=True)
    coord = make_coordinator(data={"5": {"available": True}})
    entity = climate_mod.AprilaireClimate(coord, dev)
    entity.hass = hass
    entity.platform = MagicMock()
    entity.entity_id = "climate.aprilaire_5"
    await entity.async_added_to_hass()

    async_dispatcher_send(
        hass, f"{SERVICE_SIGNAL_SET_BACKLIGHT}_climate.aprilaire_5", False, None,
    )
    await hass.async_block_till_done()
    dev.async_set_backlight.assert_not_called()
