"""Tests for sensor.py, binary_sensor.py, switch.py — the entity platforms.

These tests don't go through the full HA platform setup machinery; they
exercise each entity class directly with a lightweight fake coordinator.
That keeps coverage focused on the integration's own code rather than HA's
plumbing.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aprilaire_8870 import binary_sensor as bs
from custom_components.aprilaire_8870 import sensor as sensor_mod
from custom_components.aprilaire_8870 import switch as sw
from custom_components.aprilaire_8870.const import DOMAIN


# ---------- Shared helpers --------------------------------------------------


def make_coordinator(data: dict | None = None, devices: dict | None = None):
    """Return a fake coordinator with the small surface our entities use."""
    coord = MagicMock()
    coord.data = data if data is not None else {}
    coord.devices = devices or {}
    coord.last_update_success = True
    # CoordinatorEntity needs these:
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    coord.last_update_success_time = None
    return coord


def make_device(address=1, **kwargs):
    """Cheap device stub with `.address`, `.name`, `.unique_id`, etc."""
    d = SimpleNamespace(
        address=address,
        device_id=str(address),  # binary_sensor uses .device_id as the data-key
        name=kwargs.get("name", f"Aprilaire {address}"),
        unique_id=kwargs.get("unique_id", f"{DOMAIN}_{address}"),
        model="8870",
        firmware_version=kwargs.get("firmware_version", "1.0"),
        is_heat_pump=kwargs.get("is_heat_pump", False),
        network_override_enabled=kwargs.get("network_override_enabled", True),
        async_set_fan_mode=AsyncMock(),
        async_set_hold=AsyncMock(),
        async_set_constant_backlight=AsyncMock(),
    )
    return d


# ===========================================================================
# sensor.py
# ===========================================================================


def test_temperature_sensor_value() -> None:
    coord = make_coordinator(data={"1": {"temperature": 72.5, "available": True}})
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    assert s.native_value == 72.5
    assert s.available is True
    assert s.unique_id == f"{DOMAIN}_1_temperature"


def test_temperature_sensor_no_data() -> None:
    coord = make_coordinator(data={})
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    assert s.native_value is None
    assert s.available is False


def test_temperature_sensor_data_is_none() -> None:
    coord = make_coordinator(data=None)
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    # native_value short-circuits on falsy data.
    assert s.native_value is None


def test_humidity_sensor_value() -> None:
    coord = make_coordinator(data={"1": {"humidity": 42, "available": True}})
    s = sensor_mod.AprilaireHumiditySensor(coord, "1")
    assert s.native_value == 42


def test_humidity_sensor_no_data() -> None:
    coord = make_coordinator(data=None)
    s = sensor_mod.AprilaireHumiditySensor(coord, "1")
    assert s.native_value is None


def test_outdoor_temperature_sensor_value() -> None:
    coord = make_coordinator(data={"1": {"outdoor_temperature": 50.0, "available": True}})
    s = sensor_mod.AprilaireOutdoorTemperatureSensor(coord, "1")
    assert s.native_value == 50.0


def test_outdoor_temperature_sensor_no_data() -> None:
    coord = make_coordinator(data=None)
    s = sensor_mod.AprilaireOutdoorTemperatureSensor(coord, "1")
    assert s.native_value is None


def test_outdoor_humidity_sensor_value() -> None:
    coord = make_coordinator(data={"1": {"outdoor_humidity": 55, "available": True}})
    s = sensor_mod.AprilaireOutdoorHumiditySensor(coord, "1")
    assert s.native_value == 55


def test_outdoor_humidity_sensor_no_data() -> None:
    coord = make_coordinator(data=None)
    s = sensor_mod.AprilaireOutdoorHumiditySensor(coord, "1")
    assert s.native_value is None


def test_remote_sensor_temperature() -> None:
    coord = make_coordinator(
        data={
            "1": {
                "available": True,
                "remote_sensors": {
                    "s1": {"value": 70.0, "address": 5, "module_address": 1,
                           "sensor_number": 1, "is_control": True},
                },
            }
        }
    )
    s = sensor_mod.AprilaireRemoteSensor(coord, "1", "s1", "Remote 1", "temperature")
    assert s.native_value == 70.0
    attrs = s.extra_state_attributes
    assert attrs["address"] == 5
    assert attrs["module_address"] == 1
    assert attrs["sensor_number"] == 1
    assert attrs["is_control"] is True


def test_remote_sensor_humidity() -> None:
    coord = make_coordinator(
        data={"1": {"available": True, "remote_sensors": {"s1": {"value": 33}}}}
    )
    s = sensor_mod.AprilaireRemoteSensor(coord, "1", "s1", "Remote 1", "humidity")
    assert s.native_value == 33


def test_remote_sensor_no_data() -> None:
    coord = make_coordinator(data=None)
    s = sensor_mod.AprilaireRemoteSensor(coord, "1", "s1", "Remote 1", "temperature")
    assert s.native_value is None
    assert s.extra_state_attributes == {}


def test_sensor_device_info_with_device() -> None:
    dev = make_device(address=1)
    coord = make_coordinator(data={"1": {"available": True}}, devices={"1": dev})
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    info = s.device_info
    assert info["name"] == "Aprilaire 1"
    assert info["model"] == "8870"
    assert info["sw_version"] == "1.0"


def test_sensor_device_info_without_device() -> None:
    coord = make_coordinator(data={"1": {"available": True}}, devices={})
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    info = s.device_info
    assert info["name"] == "Aprilaire 1"
    assert info["model"] == "8870"
    assert "sw_version" not in info


def test_sensor_device_info_no_devices_attr() -> None:
    coord = MagicMock()
    coord.data = {"1": {"available": True}}
    coord.devices = None  # explicitly None to hit the fallback branch
    coord.last_update_success = True
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    info = s.device_info
    assert info["name"] == "Aprilaire 1"


def test_sensor_available_from_cache() -> None:
    coord = make_coordinator(
        data={"1": {"from_cache": True, "available": False}}
    )
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    assert s.available is True


def test_sensor_extra_state_attributes_cache() -> None:
    coord = make_coordinator(data={"1": {"from_cache": True}})
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    assert s.extra_state_attributes == {"from_cache": True}


def test_sensor_extra_state_attributes_default() -> None:
    coord = make_coordinator(data={"1": {}})
    s = sensor_mod.AprilaireTemperatureSensor(coord, "1")
    assert s.extra_state_attributes == {}


def test_sensor_setup_entry_creates_entities(hass) -> None:
    coord = make_coordinator(data={"1": {}, "2": {}})
    hass.data[DOMAIN] = {
        "abc": {"coordinator": coord, "devices": {"1": make_device(1)}, "discovered_addresses": ["1", "2"]}
    }
    entry = MagicMock()
    entry.entry_id = "abc"
    added = []

    def fake_add(entities):
        added.extend(entities)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        sensor_mod.async_setup_entry(hass, entry, fake_add)
    )
    assert len(added) == 6  # temp+hum+outdoor for each of 2 devices


# ===========================================================================
# binary_sensor.py
# ===========================================================================


# HVAC_RELAY_INDICES has positions 1,3,5,7,9,11,13 — the format alternates
# label / state where label is a single char (G,Y,W,Y,W,B,O).
HVAC_STATUS_ALL_ON = "G+Y+W+Y+W+B+O+"
HVAC_STATUS_ALL_OFF = "G-Y-W-Y-W-B-O-"


def _bs_data(**state):
    base = {"available": True, "hvac_status": HVAC_STATUS_ALL_OFF}
    base.update(state)
    return {"1": base}


def test_binary_sensor_name_is_suffix_only() -> None:
    """Regression test for the doubled-name bug we fixed."""
    coord = make_coordinator(data=_bs_data())
    s = bs.AprilaireHeatingStatusSensor(coord, make_device(1))
    assert s.name == "Heating"  # not "Aprilaire 1 Heating"


def test_binary_sensor_device_id_when_no_address() -> None:
    """Device with no address attribute falls back to a synthesised id."""
    coord = make_coordinator(data={})
    dev = SimpleNamespace(name="x", unique_id="x")
    s = bs.AprilaireHeatingStatusSensor(coord, dev)
    assert s._device_id.startswith("unknown_")


def test_binary_sensor_none_device() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireHeatingStatusSensor(coord, None)
    assert s._device_id.startswith("unknown_")
    info = s.device_info
    assert info["model"] == "8870"


def test_binary_sensor_device_info_with_device() -> None:
    coord = make_coordinator(data=_bs_data())
    s = bs.AprilaireHeatingStatusSensor(coord, make_device(1))
    info = s.device_info
    assert info["name"] == "Aprilaire 1"
    assert info["sw_version"] == "1.0"


def test_binary_sensor_heating_on() -> None:
    coord = make_coordinator(data=_bs_data(hvac_status=HVAC_STATUS_ALL_ON))
    s = bs.AprilaireHeatingStatusSensor(coord, make_device(1))
    assert s.is_on is True


def test_binary_sensor_heating_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireHeatingStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_cooling_on() -> None:
    coord = make_coordinator(data=_bs_data(hvac_status=HVAC_STATUS_ALL_ON))
    s = bs.AprilaireCoolingStatusSensor(coord, make_device(1))
    assert s.is_on is True


def test_binary_sensor_cooling_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireCoolingStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_fan_on() -> None:
    coord = make_coordinator(data=_bs_data(hvac_status=HVAC_STATUS_ALL_ON))
    s = bs.AprilaireFanStatusSensor(coord, make_device(1))
    assert s.is_on is True


def test_binary_sensor_fan_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireFanStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_emergency_heat_on() -> None:
    coord = make_coordinator(data=_bs_data(mode="EMHT", hvac_status=HVAC_STATUS_ALL_ON))
    s = bs.AprilaireEmergencyHeatStatusSensor(coord, make_device(1))
    assert s.is_on is True


def test_binary_sensor_emergency_heat_off_mode() -> None:
    coord = make_coordinator(data=_bs_data(mode="HEAT", hvac_status=HVAC_STATUS_ALL_ON))
    s = bs.AprilaireEmergencyHeatStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_emergency_heat_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireEmergencyHeatStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_filter_on() -> None:
    coord = make_coordinator(data=_bs_data(filter_alarm="ON"))
    s = bs.AprilaireFilterStatusSensor(coord, make_device(1))
    assert s.is_on is True


def test_binary_sensor_filter_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireFilterStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_system_error_present() -> None:
    coord = make_coordinator(data=_bs_data(error="120000"))
    s = bs.AprilaireSystemErrorStatusSensor(coord, make_device(1))
    assert s.is_on is True
    attrs = s.extra_state_attributes
    assert attrs["temperature_sensor"] == "1"
    assert attrs["remote_temp_sensor"] == "2"


def test_binary_sensor_system_error_absent() -> None:
    coord = make_coordinator(data=_bs_data(error="000000"))
    s = bs.AprilaireSystemErrorStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_system_error_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireSystemErrorStatusSensor(coord, make_device(1))
    assert s.is_on is False
    assert s.extra_state_attributes == {}


def test_binary_sensor_network_override_on() -> None:
    coord = make_coordinator(data=_bs_data(hold="ON"))
    s = bs.AprilaireNetworkOverrideStatusSensor(coord, make_device(1))
    assert s.is_on is True


def test_binary_sensor_network_override_no_data() -> None:
    coord = make_coordinator(data={})
    s = bs.AprilaireNetworkOverrideStatusSensor(coord, make_device(1))
    assert s.is_on is False


def test_binary_sensor_available_cache_branch() -> None:
    coord = make_coordinator(data={"1": {"from_cache": True, "available": False}})
    s = bs.AprilaireFilterStatusSensor(coord, make_device(1))
    assert s.available is True


def test_binary_sensor_available_no_device_data() -> None:
    coord = make_coordinator(data={"99": {"available": True}})
    s = bs.AprilaireFilterStatusSensor(coord, make_device(1))
    assert s.available is False


def test_binary_sensor_available_success_path() -> None:
    coord = make_coordinator(data={"1": {"available": True}})
    s = bs.AprilaireFilterStatusSensor(coord, make_device(1))
    # standard "available + last_update_success" path returns True
    assert s.available is True


def test_sensor_with_entity_category() -> None:
    """AprilaireSensor accepts an entity_category — covers the trailing branch."""
    from homeassistant.helpers.entity import EntityCategory
    coord = make_coordinator(data={})
    s = sensor_mod.AprilaireSensor(
        coordinator=coord,
        device_id="1",
        name="X",
        unique_id_suffix="x",
        device_class="temperature",
        state_class="measurement",
        unit_of_measurement="F",
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    assert s.entity_category == EntityCategory.DIAGNOSTIC


def test_binary_sensor_extra_state_attributes_cache() -> None:
    coord = make_coordinator(data={"1": {"from_cache": True}})
    s = bs.AprilaireFilterStatusSensor(coord, make_device(1))
    assert s.extra_state_attributes == {"from_cache": True}


def test_binary_sensor_setup_entry(hass) -> None:
    coord = make_coordinator(data={})
    dev = make_device(1)
    dev.is_heat_pump = True  # add emergency-heat entity
    hass.data[DOMAIN] = {
        "abc": {"coordinator": coord, "devices": {"1": dev}, "discovered_addresses": ["1", "2"]}
    }
    entry = MagicMock()
    entry.entry_id = "abc"
    added = []

    def fake_add(entities):
        added.extend(entities)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        bs.async_setup_entry(hass, entry, fake_add)
    )
    # Device 1 (initialized + heat pump): 7 entities; Device 2 (placeholder): 6.
    assert len(added) == 13


# ===========================================================================
# switch.py
# ===========================================================================


def test_switch_name_is_suffix_only() -> None:
    """Regression test for the doubled-name bug we fixed."""
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.name == "Fan Override"  # not "Aprilaire 1 Fan Override"


def test_switch_none_device() -> None:
    coord = make_coordinator(data={})
    s = sw.AprilaireFanOverrideSwitch(coord, None)
    assert s._device_id.startswith("unknown_")


def test_switch_no_address_attr() -> None:
    coord = make_coordinator(data={})
    dev = SimpleNamespace(name="x", unique_id="x")
    s = sw.AprilaireFanOverrideSwitch(coord, dev)
    assert s._device_id.startswith("unknown_")


def test_switch_device_info_none_device() -> None:
    coord = make_coordinator(data={})
    s = sw.AprilaireFanOverrideSwitch(coord, None)
    info = s.device_info
    assert info["model"] == "8870"


def test_switch_device_info_with_device() -> None:
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    info = s.device_info
    assert info["name"] == "Aprilaire 1"


def test_switch_fan_override_is_on() -> None:
    coord = make_coordinator(data={"1": {"fan_mode": "ON", "available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.is_on is True


def test_switch_fan_override_is_off_default() -> None:
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.is_on is False


async def test_switch_fan_override_turn_on(hass) -> None:
    dev = make_device(1)
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, dev)
    await s.async_turn_on()
    dev.async_set_fan_mode.assert_called_with("ON")


async def test_switch_fan_override_turn_off(hass) -> None:
    dev = make_device(1)
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, dev)
    await s.async_turn_off()
    dev.async_set_fan_mode.assert_called_with("AUTO")


async def test_switch_turn_on_blocked_when_cache_unavailable() -> None:
    dev = make_device(1)
    coord = make_coordinator(data={"1": {"from_cache": True, "available": False}})
    s = sw.AprilaireFanOverrideSwitch(coord, dev)
    await s.async_turn_on()
    dev.async_set_fan_mode.assert_not_called()


async def test_switch_turn_off_blocked_when_cache_unavailable() -> None:
    dev = make_device(1)
    coord = make_coordinator(data={"1": {"from_cache": True, "available": False}})
    s = sw.AprilaireFanOverrideSwitch(coord, dev)
    await s.async_turn_off()
    dev.async_set_fan_mode.assert_not_called()


def test_switch_available_no_data() -> None:
    coord = make_coordinator(data={"99": {"available": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.available is False


def test_switch_available_cache_branch() -> None:
    coord = make_coordinator(data={"1": {"from_cache": True, "available": False}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.available is True


def test_switch_available_no_coordinator() -> None:
    """coordinator missing -> available=False."""
    s = sw.AprilaireFanOverrideSwitch.__new__(sw.AprilaireFanOverrideSwitch)
    s.coordinator = None
    s._device_id = "1"
    assert s.available is False


def test_switch_extra_state_attributes_cache() -> None:
    coord = make_coordinator(data={"1": {"from_cache": True}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.extra_state_attributes == {"from_cache": True}


def test_switch_extra_state_attributes_default() -> None:
    coord = make_coordinator(data={"1": {}})
    s = sw.AprilaireFanOverrideSwitch(coord, make_device(1))
    assert s.extra_state_attributes == {}


def test_switch_network_override_is_on() -> None:
    coord = make_coordinator(data={"1": {"hold_status": "ON", "available": True}})
    s = sw.AprilaireNetworkOverrideSwitch(coord, make_device(1))
    assert s.is_on is True


async def test_switch_network_override_turn_on_and_off() -> None:
    dev = make_device(1)
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireNetworkOverrideSwitch(coord, dev)
    await s.async_turn_on()
    dev.async_set_hold.assert_called_with(True)
    await s.async_turn_off()
    dev.async_set_hold.assert_called_with(False)


async def test_switch_network_override_device_without_set_hold() -> None:
    dev = SimpleNamespace(address=1, name="x", unique_id="x")
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireNetworkOverrideSwitch(coord, dev)
    # No async_set_hold attr — should be a no-op (no exception).
    await s.async_turn_on()
    await s.async_turn_off()


def test_switch_backlight_is_on() -> None:
    coord = make_coordinator(data={"1": {"constant_backlight": "ON", "available": True}})
    s = sw.AprilaireBacklightSwitch(coord, make_device(1))
    assert s.is_on is True


async def test_switch_backlight_turn_on_and_off() -> None:
    dev = make_device(1)
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireBacklightSwitch(coord, dev)
    await s.async_turn_on()
    dev.async_set_constant_backlight.assert_called_with(True)
    await s.async_turn_off()
    dev.async_set_constant_backlight.assert_called_with(False)


async def test_switch_backlight_device_without_method() -> None:
    dev = SimpleNamespace(address=1, name="x", unique_id="x")
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireBacklightSwitch(coord, dev)
    await s.async_turn_on()
    await s.async_turn_off()


async def test_switch_subclass_not_implemented() -> None:
    coord = make_coordinator(data={"1": {"available": True}})
    s = sw.AprilaireSwitch(coord, make_device(1), name_suffix="X", unique_id_suffix="x")
    with pytest.raises(NotImplementedError):
        await s._async_turn_on_impl()
    with pytest.raises(NotImplementedError):
        await s._async_turn_off_impl()


def test_switch_setup_entry_with_devices(hass) -> None:
    coord = make_coordinator(data={"1": {}, "2": {}})
    hass.data[DOMAIN] = {
        "abc": {"coordinator": coord,
                "devices": {"1": make_device(1)},
                "discovered_addresses": ["1", "2"]}
    }
    entry = MagicMock()
    entry.entry_id = "abc"
    added = []

    def fake_add(entities):
        added.extend(entities)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        sw.async_setup_entry(hass, entry, fake_add)
    )
    # Device 1: 3 entities, Device 2 placeholder: 3 entities = 6.
    assert len(added) == 6


def test_switch_setup_entry_device_creation_error(hass, monkeypatch) -> None:
    coord = make_coordinator(data={})
    hass.data[DOMAIN] = {
        "abc": {"coordinator": coord, "devices": {}, "discovered_addresses": ["1"]}
    }
    entry = MagicMock()
    entry.entry_id = "abc"
    added = []

    def boom(*args, **kwargs):
        raise RuntimeError("can't construct")

    monkeypatch.setattr(
        "custom_components.aprilaire_8870.device.AprilaireDevice.__init__", boom
    )

    def fake_add(entities):
        added.extend(entities)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        sw.async_setup_entry(hass, entry, fake_add)
    )
    # Construction blew up for the placeholder; no entities added for that
    # device but setup didn't crash.
    assert added == []


def test_switch_setup_entry_with_network_override_disabled(hass) -> None:
    coord = make_coordinator(data={"1": {}})
    dev = make_device(1)
    dev.network_override_enabled = False  # exercises the conditional branch
    hass.data[DOMAIN] = {
        "abc": {"coordinator": coord, "devices": {"1": dev}, "discovered_addresses": []}
    }
    entry = MagicMock()
    entry.entry_id = "abc"
    added = []

    def fake_add(entities):
        added.extend(entities)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        sw.async_setup_entry(hass, entry, fake_add)
    )
    # Even with network_override_enabled=False, the integration always adds the switch.
    assert len(added) == 3
