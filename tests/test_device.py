"""Tests for device.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aprilaire_8870.device import (
    AprilaireDevice,
    AprilaireDeviceManager,
)
from custom_components.aprilaire_8870.protocol import AprilaireProtocol


# ---- Helpers ----------------------------------------------------------------


def make_device(address=1, responses=None):
    """Build a device wired to a stub-backed AprilaireProtocol."""
    from tests.conftest import StubConnection
    conn = StubConnection(responses or {})
    proto = AprilaireProtocol(connection=conn)
    coord = MagicMock()
    dev = AprilaireDevice(address, coord, proto)
    return dev, conn, proto


# ---- Basic init / properties ------------------------------------------------


def test_device_init_defaults() -> None:
    dev, _, _ = make_device(5)
    assert dev.address == 5
    assert dev.name == "Aprilaire 5"
    assert dev.model == "8870"
    assert dev.available is False
    assert dev.device_id == 5
    assert dev._cos_enabled is False
    assert dev._cos_flags == set()


def test_get_state_returns_copy() -> None:
    dev, _, _ = make_device()
    s = dev.get_state()
    assert isinstance(s, dict)
    s["temperature"] = 99
    assert dev._state.get("temperature") != 99


# ---- unsupported-command tracking (v0.2.7) ---------------------------------


def test_note_optional_failure_marks_unsupported_after_threshold() -> None:
    dev, _, _ = make_device()
    # Threshold is 2 cycles — two consecutive failures trip the skip.
    dev._note_optional_failure("FLTALM")
    dev._note_optional_failure("FLTALM")
    assert "FLTALM" in dev._unsupported_commands
    # Counter cleared once the command is marked unsupported.
    assert "FLTALM" not in dev._optional_failure_counts


def test_note_optional_failure_below_threshold_doesnt_mark() -> None:
    dev, _, _ = make_device()
    dev._note_optional_failure("FLTALM")
    assert "FLTALM" not in dev._unsupported_commands
    assert dev._optional_failure_counts["FLTALM"] == 1


def test_note_optional_failure_no_op_once_unsupported() -> None:
    dev, _, _ = make_device()
    dev._unsupported_commands.add("FLTALM")
    dev._note_optional_failure("FLTALM")
    # Counter not touched; command stays in unsupported set.
    assert "FLTALM" not in dev._optional_failure_counts


def test_reset_unsupported_commands_clears_state() -> None:
    dev, _, _ = make_device()
    dev._unsupported_commands.update({"FLTALM", "WPALM"})
    dev._optional_failure_counts["ERROR"] = 2
    dev.reset_unsupported_commands()
    assert dev._unsupported_commands == set()
    assert dev._optional_failure_counts == {}


async def test_async_update_skips_unsupported_after_two_cycles() -> None:
    """Two consecutive poll cycles where HUM times out → HUM stops being queried."""
    from custom_components.aprilaire_8870.device import AprilaireDevice
    from custom_components.aprilaire_8870.protocol import AprilaireProtocol
    from tests.conftest import StubConnection
    conn = StubConnection({})
    proto = AprilaireProtocol(connection=conn)
    # monitor_humidity=True so HUM is in the optional list; OT off so we
    # have exactly one optional command to track.
    dev = AprilaireDevice(
        1, MagicMock(), proto,
        monitor_humidity=True, monitor_outdoor_temp=False,
    )
    dev.available = True
    # Essential commands all succeed; HUM never responds.
    conn.responses["SN1 TEMP?"] = "SN1 TEMP=72F"
    conn.responses["SN1 MODE?"] = "SN1 MODE=COOL"
    conn.responses["SN1 FAN?"] = "SN1 FAN=AUTO"
    conn.responses["SN1 HVAC?"] = "SN1 HVAC=G-Y1-W1-Y2-W2-B-O-"
    conn.responses["SN1 HOLD?"] = "SN1 HOLD=OFF"
    conn.responses["SN1 SC?"] = "SN1 SC=75F"
    # No "SN1 HUM?" response → query times out.
    dev._state["mode"] = "COOL"

    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev.async_update()
    # First cycle: HUM tried, failed, counter at 1.
    assert dev._optional_failure_counts.get("HUM") == 1
    assert "HUM" not in dev._unsupported_commands
    hum_calls_after_cycle_1 = sum(1 for c in conn.sent if "HUM" in c)

    conn.sent.clear()
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev.async_update()
    # Second cycle: counter reached threshold (2), HUM marked unsupported.
    assert "HUM" in dev._unsupported_commands

    conn.sent.clear()
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev.async_update()
    # Third cycle: HUM is unsupported, no longer queried.
    assert not any("HUM" in c for c in conn.sent)


def test_get_state_handles_missing_state() -> None:
    dev, _, _ = make_device()
    del dev._state
    assert dev.get_state() == {}


def test_get_capabilities_returns_copy() -> None:
    dev, _, _ = make_device()
    caps = dev.get_capabilities()
    caps["foo"] = "bar"
    assert "foo" not in dev.capabilities


def test_is_cos_enabled_default() -> None:
    dev, _, _ = make_device()
    assert dev.is_cos_enabled() is False


def test_get_cos_flags_returns_copy() -> None:
    dev, _, _ = make_device()
    dev._cos_flags = {"c1"}
    f = dev.get_cos_flags()
    f.add("c2")
    assert "c2" not in dev._cos_flags


def test_update_from_real_device() -> None:
    dev1, _, _ = make_device(1)
    dev2, _, _ = make_device(2)
    dev2.model = "MODELX"
    dev2.firmware_version = "9.9"
    dev2.available = True
    dev2.capabilities["is_heat_pump"] = True
    dev2._state["temperature"] = 75
    dev2._cos_enabled = True
    dev2._cos_flags = {"c1"}
    dev1.update_from_real_device(dev2)
    assert dev1.model == "MODELX"
    assert dev1.firmware_version == "9.9"
    assert dev1.available is True
    assert dev1._cos_enabled is True
    assert dev1._cos_flags == {"c1"}


# ---- _extract_device_name_from_response -------------------------------------


def test_extract_device_name_present() -> None:
    dev, _, _ = make_device()
    # Regex greedily captures any A-Za-z0-9 and spaces, including any post-prefix
    # tokens like "CR". We only assert the prefix part is preserved.
    extracted = dev._extract_device_name_from_response("SN1KITCHEN CR=NORMAL")
    assert extracted is not None
    assert extracted.startswith("KITCHEN")


def test_extract_device_name_empty_response() -> None:
    dev, _, _ = make_device()
    assert dev._extract_device_name_from_response("") is None


def test_extract_device_name_just_address() -> None:
    dev, _, _ = make_device()
    # No alpha/digit characters after the address → falsy capture.
    assert dev._extract_device_name_from_response("SN1") is None


# ---- _process_state_response ------------------------------------------------


def test_process_state_response_temperature() -> None:
    dev, _, _ = make_device()
    dev._process_state_response("TEMP", "SN1 TEMP=72F")
    assert dev._state["temperature"] == 72.0


def test_process_state_response_no_value() -> None:
    dev, _, _ = make_device()
    dev._process_state_response("TEMP", "garbage")
    assert dev._state["temperature"] is None


def test_process_state_response_empty_after_equals() -> None:
    dev, _, _ = make_device()
    dev._process_state_response("TEMP", "SN1 TEMP=")
    # value is empty, no update
    assert dev._state["temperature"] is None


def test_process_state_response_all_branches() -> None:
    dev, _, _ = make_device()
    for cmd, val, key, expected in [
        ("HUM", "45%", "humidity", 45),
        ("OT", "50F", "outdoor_temperature", 50.0),
        ("MODE", "HEAT", "mode", "HEAT"),
        ("FAN", "AUTO", "fan_mode", "AUTO"),
        ("SH", "68F", "heat_setpoint", 68.0),
        ("SC", "74F", "cool_setpoint", 74.0),
        ("HVAC", "G+Y-W-Y-W-B-O-", "hvac_status", "G+Y-W-Y-W-B-O-"),
        ("HOLD", "ON", "hold_status", "ON"),
        ("FLTALM", "ON", "filter_alarm", True),
        ("WPALM", "OFF", "water_panel_alarm", False),
        ("SYSALM", "ON", "system_alarm", True),
        ("DEHALM", "ON", "dehumidifier_alarm", True),
        ("ERROR", "000000", "error_status", "000000"),
    ]:
        dev._process_state_response(cmd, f"SN1 {cmd}={val}")
        assert dev._state[key] == expected, f"{cmd} failed: got {dev._state[key]}"


# ---- _parse_temperature / _parse_humidity ----------------------------------


def test_parse_temperature_F() -> None:
    dev, _, _ = make_device()
    assert dev._parse_temperature("72F") == 72.0


def test_parse_temperature_C() -> None:
    dev, _, _ = make_device()
    assert dev._parse_temperature("22C") == 22.0


def test_parse_temperature_no_unit() -> None:
    dev, _, _ = make_device()
    assert dev._parse_temperature("72") == 72.0


def test_parse_temperature_placeholder() -> None:
    dev, _, _ = make_device()
    assert dev._parse_temperature("--F") is None
    assert dev._parse_temperature("--C") is None
    assert dev._parse_temperature("--") is None


def test_parse_temperature_invalid() -> None:
    dev, _, _ = make_device()
    assert dev._parse_temperature("xyz") is None


def test_parse_temperature_value_error_branch() -> None:
    dev, _, _ = make_device()
    # "abc" passes the .endswith() guards then trips float().
    assert dev._parse_temperature("abc") is None


def test_parse_humidity_with_percent() -> None:
    dev, _, _ = make_device()
    assert dev._parse_humidity("45%") == 45


def test_parse_humidity_without_percent() -> None:
    dev, _, _ = make_device()
    assert dev._parse_humidity("50") == 50


def test_parse_humidity_placeholder() -> None:
    dev, _, _ = make_device()
    assert dev._parse_humidity("--%") is None
    assert dev._parse_humidity("--") is None


def test_parse_humidity_invalid() -> None:
    dev, _, _ = make_device()
    assert dev._parse_humidity("xx%") is None


def test_parse_humidity_value_error_branch() -> None:
    dev, _, _ = make_device()
    assert dev._parse_humidity("abc") is None


# ---- _parse_model_info ------------------------------------------------------


def test_parse_model_info() -> None:
    dev, _, _ = make_device()
    dev._parse_model_info("MODEL# 8870 REV: 1.5 RPC 2023")
    assert dev.model == "8870"
    assert dev.firmware_version == "1.5"


def test_parse_model_info_short() -> None:
    dev, _, _ = make_device()
    dev._parse_model_info("MODEL# 8870")  # too few parts
    # Original model remains.
    assert dev.model == "8870"


def test_parse_model_info_garbage() -> None:
    dev, _, _ = make_device()
    # split() on a string never raises; exception path requires a non-string.
    dev._parse_model_info(None)  # type: ignore[arg-type]


def test_parse_model_info_with_sn_prefix_and_location_name() -> None:
    """Full response from the bus: SN<addr><name>  MODEL# ..."""
    dev, _, _ = make_device(address=3)
    dev._parse_model_info("SN3Master Bedroom  MODEL# 8870 REV: V1.2 - RPC 2002")
    assert dev.name == "Master Bedroom"
    assert dev.model == "8870"
    # Firmware: parts[3] is "V1.2"; lstripping V → "1.2".
    assert dev.firmware_version == "1.2"


def test_parse_model_info_with_sn_prefix_no_location_name() -> None:
    """Some thermostats have no location set — no two-space gap, no name."""
    dev, _, _ = make_device(address=1)
    original_name = dev.name
    dev._parse_model_info("SN1 MODEL# 8870 REV: V1.2 - RPC 2002")
    # Name unchanged (still the address-based default).
    assert dev.name == original_name
    assert dev.model == "8870"


def test_parse_model_info_with_sn_prefix_other_address_does_not_apply() -> None:
    """Prefix for a different address must not be misattributed."""
    dev, _, _ = make_device(address=5)
    original_name = dev.name
    dev._parse_model_info("SN3Master Bedroom  MODEL# 8870 REV: V1.2 - RPC 2002")
    # Different SN<addr> in prefix → no location name extracted.
    assert dev.name == original_name


def test_parse_model_info_idempotent_on_same_name() -> None:
    dev, _, _ = make_device(address=3)
    dev._parse_model_info("SN3Master Bedroom  MODEL# 8870 REV: V1.2 - RPC 2002")
    first = dev.name
    dev._parse_model_info("SN3Master Bedroom  MODEL# 8870 REV: V1.2 - RPC 2002")
    assert dev.name == first == "Master Bedroom"


# ---- _parse_equipment_config ------------------------------------------------


def test_parse_equipment_config_heat_pump() -> None:
    dev, _, _ = make_device()
    dev._parse_equipment_config("0010")  # 4th char "0" → is_heat_pump True
    assert dev.capabilities["is_heat_pump"] is True
    assert dev.capabilities["has_emergency_heat"] is True


def test_parse_equipment_config_heat_cool_multistage() -> None:
    dev, _, _ = make_device()
    dev._parse_equipment_config("0011")  # multi-stage AND heat/cool
    assert dev.capabilities["is_heat_pump"] is False
    assert dev.capabilities["stages_heat"] == 2
    assert dev.capabilities["stages_cool"] == 2


def test_parse_equipment_config_short() -> None:
    dev, _, _ = make_device()
    dev._parse_equipment_config("01")  # too short, ignored
    assert dev.capabilities["is_heat_pump"] is False


def test_parse_equipment_config_garbage() -> None:
    dev, _, _ = make_device()
    dev._parse_equipment_config(None)  # type: ignore[arg-type]


# ---- _parse_controller_type -------------------------------------------------


def test_parse_controller_type_humidity() -> None:
    dev, _, _ = make_device()
    dev._parse_controller_type("SN1 CT=1")
    assert dev.capabilities["controller_type"] == 1


def test_parse_controller_type_missing() -> None:
    dev, _, _ = make_device()
    # No CT= → no change.
    dev._parse_controller_type("SN1 OTHER=x")
    assert dev.capabilities["controller_type"] == "0"  # default constant


def test_parse_controller_type_garbage() -> None:
    dev, _, _ = make_device()
    dev._parse_controller_type("CT=NOTAINT")  # int() raises → swallowed


# ---- _parse_support_modules -------------------------------------------------


def test_parse_support_modules_with_humidity() -> None:
    dev, _, _ = make_device()
    dev._parse_support_modules("M1:CH,RH M2:00,00")
    assert dev.capabilities["has_humidifier"] is True
    assert dev.capabilities["has_dehumidifier"] is True
    mods = dev.capabilities["support_modules"]
    assert len(mods) == 2


def test_parse_support_modules_garbage() -> None:
    dev, _, _ = make_device()
    dev._parse_support_modules(None)  # type: ignore[arg-type]


def test_parse_support_modules_no_colons() -> None:
    dev, _, _ = make_device()
    dev._parse_support_modules("no_colons here")
    assert dev.capabilities["support_modules"] == []


# ---- async_send_command -----------------------------------------------------


async def test_async_send_command_unavailable() -> None:
    dev, _, _ = make_device()
    dev.available = False
    assert await dev.async_send_command("TEMP") is None


async def test_async_send_command_query() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 TEMP?"] = "SN1 TEMP=72F"
    result = await dev.async_send_command("TEMP")
    assert result == "72F"


async def test_async_send_command_assignment() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 MODE=HEAT"] = "SN1 MODE=HEAT"
    result = await dev.async_send_command("MODE", "HEAT")
    assert result == "SN1 MODE=HEAT"


async def test_async_send_command_exception() -> None:
    dev, _, proto = make_device()
    dev.available = True
    proto.execute_query_command = AsyncMock(side_effect=RuntimeError("boom"))
    assert await dev.async_send_command("TEMP") is None


# ---- async_enable_cos (the fixed function) ---------------------------------


async def test_async_enable_cos_succeeds() -> None:
    dev, conn, _ = make_device(1)
    conn.responses["SN1 CR=NORMAL"] = "SN1KITCHEN CR=NORMAL"
    # All default flags accepted.
    from custom_components.aprilaire_8870.const import DEFAULT_COS_FLAGS
    for flag in DEFAULT_COS_FLAGS:
        conn.responses[f"SN1 {flag}=ON"] = f"SN1 {flag}=ON"

    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_enable_cos()
    assert result is True
    assert dev._cos_enabled is True
    assert dev._cos_flags == set(DEFAULT_COS_FLAGS)
    # Name should be extracted from the CR response.
    assert dev.name == "KITCHEN"


async def test_async_enable_cos_cr_no_response() -> None:
    dev, _, _ = make_device(1)
    # No scripted CR response → returns None → method bails False.
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_enable_cos()
    assert result is False
    assert dev._cos_enabled is False


async def test_async_enable_cos_cr_unexpected_response() -> None:
    dev, conn, _ = make_device(1)
    conn.responses["SN1 CR=NORMAL"] = "SN1 CR=DEBUG"  # not NORMAL
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_enable_cos()
    assert result is False


async def test_async_enable_cos_cr_exception() -> None:
    dev, _, proto = make_device(1)
    proto.execute_assignment_command = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_enable_cos()
    assert result is False


async def test_async_enable_cos_no_flags_accepted() -> None:
    """CR succeeds but every flag write returns None — function returns False."""
    dev, conn, _ = make_device(1)
    conn.responses["SN1 CR=NORMAL"] = "SN1 CR=NORMAL"
    # No flag responses → each individual flag call returns None.
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_enable_cos()
    assert result is False
    assert dev._cos_enabled is False


async def test_async_enable_cos_partial_flag_acceptance() -> None:
    """Only one flag accepts → True, _cos_flags populated."""
    dev, conn, _ = make_device(1)
    conn.responses["SN1 CR=NORMAL"] = "SN1 CR=NORMAL"
    conn.responses["SN1 c1=ON"] = "SN1 c1=ON"
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_enable_cos(flags={"c1", "c2"})
    assert result is True
    assert dev._cos_flags == {"c1"}


async def test_async_enable_cos_flag_exception_skipped() -> None:
    """An exception during one flag write doesn't fail the others."""
    dev, conn, proto = make_device(1)
    conn.responses["SN1 CR=NORMAL"] = "SN1 CR=NORMAL"

    real_exec = proto.execute_assignment_command
    call_count = {"n": 0}

    async def patched(addr, cmd, value, timeout=None):
        call_count["n"] += 1
        if cmd == "c1":
            raise RuntimeError("flag broke")
        return await real_exec(addr, cmd, value, timeout=timeout)

    conn.responses["SN1 c2=ON"] = "SN1 c2=ON"
    proto.execute_assignment_command = patched
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev.async_enable_cos(flags={"c1", "c2"})


# ---- async_verify_cos ------------------------------------------------------


async def test_async_verify_cos_not_enabled() -> None:
    dev, _, _ = make_device()
    assert await dev.async_verify_cos() is False


async def test_async_verify_cos_no_flags() -> None:
    dev, _, _ = make_device()
    dev._cos_enabled = True
    assert await dev.async_verify_cos() is False


async def test_async_verify_cos_success() -> None:
    dev, conn, _ = make_device()
    dev._cos_enabled = True
    dev._cos_flags = {"c1"}
    conn.responses["SN1 CR?"] = "SN1 CR=NORMAL"
    conn.responses["SN1 c1?"] = "SN1 c1=ON"
    assert await dev.async_verify_cos() is True


async def test_async_verify_cos_cr_mismatch() -> None:
    dev, conn, _ = make_device()
    dev._cos_enabled = True
    dev._cos_flags = {"c1"}
    conn.responses["SN1 CR?"] = "SN1 CR=DEBUG"
    assert await dev.async_verify_cos() is False


async def test_async_verify_cos_flag_off() -> None:
    dev, conn, _ = make_device()
    dev._cos_enabled = True
    dev._cos_flags = {"c1"}
    conn.responses["SN1 CR?"] = "SN1 CR=NORMAL"
    conn.responses["SN1 c1?"] = "SN1 c1=OFF"
    assert await dev.async_verify_cos() is False


async def test_async_verify_cos_exception() -> None:
    dev, _, proto = make_device()
    dev._cos_enabled = True
    dev._cos_flags = {"c1"}
    proto.execute_query_command = AsyncMock(side_effect=RuntimeError("boom"))
    assert await dev.async_verify_cos() is False


# ---- async_set_temperature -------------------------------------------------


async def test_set_temperature_unavailable() -> None:
    dev, _, _ = make_device()
    dev.available = False
    assert await dev.async_set_temperature(72) is False


async def test_set_temperature_humidity_controller() -> None:
    dev, _, _ = make_device()
    dev.available = True
    dev.capabilities["controller_type"] = "1"
    assert await dev.async_set_temperature(72) is False


async def test_set_temperature_no_mode_no_arg() -> None:
    dev, _, _ = make_device()
    dev.available = True
    dev._state["mode"] = None
    assert await dev.async_set_temperature(72) is False


async def test_set_temperature_off_mode() -> None:
    dev, _, _ = make_device()
    dev.available = True
    dev._state["mode"] = "OFF"
    assert await dev.async_set_temperature(72) is False


async def test_set_temperature_heat_success() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 SH=72"] = "SN1 SH=72"
    assert await dev.async_set_temperature(72, "HEAT") is True
    assert dev._state["heat_setpoint"] == 72


async def test_set_temperature_cool_success() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 SC=72"] = "SN1 SC=72"
    assert await dev.async_set_temperature(72, "COOL") is True
    assert dev._state["cool_setpoint"] == 72


async def test_set_temperature_auto_closer_to_heat() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    dev._state["heat_setpoint"] = 70
    dev._state["cool_setpoint"] = 80
    conn.responses["SN1 SH=71"] = "SN1 SH=71"
    assert await dev.async_set_temperature(71, "AUTO") is True


async def test_set_temperature_auto_closer_to_cool() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    dev._state["heat_setpoint"] = 70
    dev._state["cool_setpoint"] = 80
    conn.responses["SN1 SC=79"] = "SN1 SC=79"
    assert await dev.async_set_temperature(79, "AUTO") is True


async def test_set_temperature_auto_only_heat_known() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    dev._state["heat_setpoint"] = 70
    dev._state["cool_setpoint"] = None
    conn.responses["SN1 SH=72"] = "SN1 SH=72"
    assert await dev.async_set_temperature(72, "AUTO") is True


async def test_set_temperature_auto_only_cool_known() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    dev._state["heat_setpoint"] = None
    dev._state["cool_setpoint"] = 75
    conn.responses["SN1 SC=72"] = "SN1 SC=72"
    assert await dev.async_set_temperature(72, "AUTO") is True


async def test_set_temperature_auto_no_setpoints() -> None:
    dev, _, _ = make_device()
    dev.available = True
    dev._state["heat_setpoint"] = None
    dev._state["cool_setpoint"] = None
    assert await dev.async_set_temperature(72, "AUTO") is False


async def test_set_temperature_bad_mode() -> None:
    dev, _, _ = make_device()
    dev.available = True
    assert await dev.async_set_temperature(72, "WEIRD") is False


async def test_set_temperature_send_returns_none() -> None:
    dev, _, _ = make_device()
    dev.available = True
    # No scripted response → execute_assignment_command returns None.
    assert await dev.async_set_temperature(72, "HEAT") is False


# ---- async_set_hvac_mode ---------------------------------------------------


async def test_set_hvac_mode_unavailable() -> None:
    dev, _, _ = make_device()
    dev.available = False
    assert await dev.async_set_hvac_mode("heat") is False


async def test_set_hvac_mode_unknown() -> None:
    dev, _, _ = make_device()
    dev.available = True
    assert await dev.async_set_hvac_mode("weird") is False


async def test_set_hvac_mode_emht_not_supported() -> None:
    dev, _, _ = make_device()
    dev.available = True
    dev.capabilities["has_emergency_heat"] = False
    assert await dev.async_set_hvac_mode("emergency_heat") is False


async def test_set_hvac_mode_success() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 MODE=HEAT"] = "SN1 MODE=HEAT"
    assert await dev.async_set_hvac_mode("heat") is True
    assert dev._state["mode"] == "HEAT"


async def test_set_hvac_mode_send_fails() -> None:
    dev, _, _ = make_device()
    dev.available = True
    # No scripted response.
    assert await dev.async_set_hvac_mode("heat") is False


# ---- async_set_fan_mode ----------------------------------------------------


async def test_set_fan_mode_unavailable() -> None:
    dev, _, _ = make_device()
    dev.available = False
    assert await dev.async_set_fan_mode("auto") is False


async def test_set_fan_mode_unknown() -> None:
    dev, _, _ = make_device()
    dev.available = True
    assert await dev.async_set_fan_mode("weird") is False


async def test_set_fan_mode_success() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 FAN=auto"] = "SN1 FAN=auto"
    assert await dev.async_set_fan_mode("auto") is True


async def test_set_fan_mode_send_fails() -> None:
    dev, _, _ = make_device()
    dev.available = True
    assert await dev.async_set_fan_mode("auto") is False


# ---- async_set_hold ---------------------------------------------------------


async def test_set_hold_unavailable() -> None:
    dev, _, _ = make_device()
    dev.available = False
    assert await dev.async_set_hold(True) is False


async def test_set_hold_on() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 HOLD=ON"] = "SN1 HOLD=ON"
    assert await dev.async_set_hold(True) is True
    assert dev._state["hold_status"] == "ON"


async def test_set_hold_off_send_fails() -> None:
    dev, _, _ = make_device()
    dev.available = True
    assert await dev.async_set_hold(False) is False


# ---- process_cos_message ----------------------------------------------------


def test_process_cos_temperature() -> None:
    dev, _, _ = make_device()
    assert dev.process_cos_message("TEMP", "72F") is True
    assert dev._state["temperature"] == 72.0


def test_process_cos_all_branches() -> None:
    dev, _, _ = make_device()
    for cmd, val, key in [
        ("HUM", "45%", "humidity"),
        ("OT", "50F", "outdoor_temperature"),
        ("SH", "68F", "heat_setpoint"),
        ("SC", "74F", "cool_setpoint"),
        ("MODE", "HEAT", "mode"),
        ("FAN", "AUTO", "fan_mode"),
        ("HVAC", "G+", "hvac_status"),
        ("HOLD", "ON", "hold_status"),
    ]:
        assert dev.process_cos_message(cmd, val) is True


def test_process_cos_specialized_dispatch() -> None:
    dev, _, _ = make_device()
    assert dev.process_cos_message("FLTALM", "ON") is True
    assert dev._state["filter_alarm"] is True


def test_process_cos_specialized_unknown() -> None:
    dev, _, _ = make_device()
    assert dev.process_cos_message("BOGUS", "x") is False


def test_process_cos_exception() -> None:
    dev, _, _ = make_device()
    # Force _parse_temperature to raise.
    with patch.object(dev, "_parse_temperature", side_effect=RuntimeError("boom")):
        assert dev.process_cos_message("TEMP", "x") is False


def test_handle_specialized_all_branches() -> None:
    dev, _, _ = make_device()
    assert dev._handle_specialized_cos_message("FLTALM", "ON") is True
    assert dev._handle_specialized_cos_message("WPALM", "OFF") is True
    assert dev._handle_specialized_cos_message("SYSALM", "ON") is True
    assert dev._handle_specialized_cos_message("DEHALM", "ON") is True
    assert dev._handle_specialized_cos_message("ERROR", "010000") is True
    assert dev._handle_specialized_cos_message("UNKNOWN", "x") is False


def test_handle_specialized_exception() -> None:
    dev, _, _ = make_device()
    # _state assignment shouldn't normally throw — force one via property.
    class BoomDict(dict):
        def __setitem__(self, k, v):
            raise RuntimeError("boom")
    dev._state = BoomDict()
    assert dev._handle_specialized_cos_message("FLTALM", "ON") is False


# ---- async_update (the new retried version) --------------------------------


async def test_async_update_unavailable() -> None:
    dev, _, _ = make_device()
    dev.available = False
    assert await dev.async_update() is False


async def test_async_update_happy_path() -> None:
    dev, conn, _ = make_device()
    dev.available = True
    for cmd, val in [
        ("TEMP", "72F"),
        ("MODE", "HEAT"),
        ("FAN", "AUTO"),
        ("HVAC", "G-Y-W-Y-W-B-O-"),
        ("HOLD", "OFF"),
        ("SH", "68F"),
    ]:
        conn.responses[f"SN1 {cmd}?"] = f"SN1 {cmd}={val}"
    # set initial mode so SH gets queried; SC is COOL/AUTO so skipped
    dev._state["mode"] = "HEAT"
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_update()
    assert result is True
    assert dev._state["temperature"] == 72.0
    assert dev._state["mode"] == "HEAT"
    assert dev._state["heat_setpoint"] == 68.0


async def test_async_update_with_optional_responses() -> None:
    """With monitor_alarms opted in, the alarm queries fire and populate state."""
    from custom_components.aprilaire_8870.device import AprilaireDevice
    from custom_components.aprilaire_8870.protocol import AprilaireProtocol
    from tests.conftest import StubConnection
    conn = StubConnection({})
    proto = AprilaireProtocol(connection=conn)
    dev = AprilaireDevice(1, MagicMock(), proto, monitor_alarms=True)
    dev.available = True
    conn.responses["SN1 TEMP?"] = "SN1 TEMP=72F"
    conn.responses["SN1 MODE?"] = "SN1 MODE=COOL"
    conn.responses["SN1 SC?"] = "SN1 SC=75F"
    conn.responses["SN1 HUM?"] = "SN1 HUM=42%"
    conn.responses["SN1 OT?"] = "SN1 OT=50F"
    conn.responses["SN1 FLTALM?"] = "SN1 FLTALM=ON"
    conn.responses["SN1 WPALM?"] = "SN1 WPALM=OFF"
    conn.responses["SN1 SYSALM?"] = "SN1 SYSALM=OFF"
    conn.responses["SN1 DEHALM?"] = "SN1 DEHALM=OFF"
    conn.responses["SN1 ERROR?"] = "SN1 ERROR=000000"
    dev._state["mode"] = "COOL"
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_update()
    assert result is True
    assert dev._state["humidity"] == 42
    assert dev._state["filter_alarm"] is True


async def test_async_update_skips_alarms_by_default() -> None:
    """monitor_alarms=False (default) means no alarm queries hit the bus."""
    dev, conn, _ = make_device()
    dev.available = True
    conn.responses["SN1 TEMP?"] = "SN1 TEMP=72F"
    conn.responses["SN1 MODE?"] = "SN1 MODE=COOL"
    conn.responses["SN1 SC?"] = "SN1 SC=75F"
    conn.responses["SN1 HUM?"] = "SN1 HUM=42%"
    conn.responses["SN1 OT?"] = "SN1 OT=50F"
    # Alarm responses available — but they should NOT be queried.
    conn.responses["SN1 FLTALM?"] = "SN1 FLTALM=ON"
    dev._state["mode"] = "COOL"
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev.async_update()
    # State did not absorb the alarm value because the query wasn't sent.
    assert dev._state.get("filter_alarm") is None
    # Confirm no FLTALM command on the wire.
    assert not any("FLTALM" in c for c in conn.sent)


async def test_async_update_skips_humidity_when_disabled() -> None:
    """monitor_humidity=False means HUM is skipped."""
    dev, conn, _ = make_device()
    dev.monitor_humidity = False
    dev.available = True
    conn.responses["SN1 TEMP?"] = "SN1 TEMP=72F"
    conn.responses["SN1 MODE?"] = "SN1 MODE=COOL"
    conn.responses["SN1 SC?"] = "SN1 SC=75F"
    conn.responses["SN1 HUM?"] = "SN1 HUM=42%"
    conn.responses["SN1 OT?"] = "SN1 OT=50F"
    dev._state["mode"] = "COOL"
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev.async_update()
    assert dev._state.get("humidity") is None
    assert not any("HUM" in c for c in conn.sent)


async def test_async_update_retries_then_succeed() -> None:
    dev, _, proto = make_device()
    dev.available = True
    sequence = [None, "72F"]

    async def flaky(*args, **kwargs):
        return sequence.pop(0) if sequence else None

    proto.execute_query_command = flaky
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_update()
    assert result is True


async def test_async_update_all_retries_exhausted() -> None:
    dev, _, proto = make_device()
    dev.available = True
    proto.execute_query_command = AsyncMock(return_value=None)
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_update()
    # Without TEMP/MODE, success=False
    assert result is False


async def test_async_update_query_exception() -> None:
    dev, _, proto = make_device()
    dev.available = True
    proto.execute_query_command = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev.async_update()
    assert result is False


async def test_async_update_outer_exception() -> None:
    dev, _, _ = make_device()
    dev.available = True
    # Cause an exception during sleep to escape the inner try/except.
    with patch.object(dev, "_query_with_retries", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await dev.async_update()
    # Exception propagates out of _query_with_retries, caught by outer handler.
    assert result is False


# ---- async_initialize -------------------------------------------------------


async def test_async_initialize_no_model() -> None:
    dev, _, _ = make_device()
    # No scripted ID response → _send_command_with_retry returns None or raises.
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()), \
         patch.object(dev, "_send_command_with_retry", new=AsyncMock(return_value=None)):
        assert await dev.async_initialize() is False


async def test_async_initialize_happy_path() -> None:
    dev, _, _ = make_device()
    # Script the whole initialization path.
    seq = [
        "MODEL# 8870 REV: 1.0 RPC 2023",  # ID
        "0010",                            # EQUIPCONFIG
        "SN1 CT=0",                        # CT
        "SN1 TEMP=72F",                    # TEMP
        "SN1 MODE=HEAT",                   # MODE
        "SN1 SH=68F",                      # SH (because mode=HEAT)
    ]
    async def replies(*args, **kwargs):
        return seq.pop(0) if seq else None
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()), \
         patch.object(dev, "_send_command_with_retry", new=replies):
        assert await dev.async_initialize() is True
    assert dev.available is True


async def test_async_initialize_capability_exception() -> None:
    dev, _, _ = make_device()
    # ID returns; EQUIPCONFIG raises.
    counter = {"n": 0}

    async def replies(*args, **kwargs):
        counter["n"] += 1
        if counter["n"] == 1:
            return "MODEL# 8870 REV: 1.0 RPC 2023"
        if counter["n"] == 2:
            raise RuntimeError("equip broke")
        # subsequent state queries return None (skipped via allow_skip).
        return None

    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()), \
         patch.object(dev, "_send_command_with_retry", new=replies):
        await dev.async_initialize()


async def test_async_initialize_outer_exception() -> None:
    dev, _, _ = make_device()
    with patch.object(dev, "_send_command_with_retry", side_effect=RuntimeError("outer")), \
         patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        assert await dev.async_initialize() is False
    assert dev.available is False


# ---- _send_command_with_retry ---------------------------------------------


async def test_send_command_retry_uses_response_method() -> None:
    dev, conn, _ = make_device()
    conn.async_send_command_with_response = AsyncMock(return_value="SN1 TEMP=72F")
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._send_command_with_retry("SN1 TEMP?")
    assert result == "SN1 TEMP=72F"


async def test_send_command_retry_fallback_path() -> None:
    dev, _, proto = make_device()
    conn = MagicMock()
    conn.async_send_command = AsyncMock()
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1 TEMP=72F"]])
    del conn.async_send_command_with_response  # force the fallback branch
    proto._connection = conn
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._send_command_with_retry("SN1 TEMP?")
    assert result == "SN1 TEMP=72F"


async def test_send_command_retry_eventually_fails_allow_skip() -> None:
    dev, conn, _ = make_device()
    conn.async_send_command_with_response = AsyncMock(return_value=None)
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._send_command_with_retry("SN1 TEMP?", allow_skip=True)
    assert result is None


async def test_send_command_retry_eventually_fails_raises() -> None:
    dev, conn, _ = make_device()
    conn.async_send_command_with_response = AsyncMock(return_value=None)
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(Exception):
            await dev._send_command_with_retry("SN1 TEMP?")


async def test_send_command_retry_exception_in_send() -> None:
    dev, conn, _ = make_device()
    conn.async_send_command_with_response = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._send_command_with_retry("SN1 TEMP?", allow_skip=True)
    assert result is None


# ---- _find_matching_response -----------------------------------------------


def test_find_matching_response_empty() -> None:
    dev, _, _ = make_device()
    assert dev._find_matching_response([], "SN1 TEMP?") is None


def test_find_matching_response_short_command() -> None:
    dev, _, _ = make_device()
    assert dev._find_matching_response(["x"], "SN1") is None


def test_find_matching_response_exact_match() -> None:
    dev, _, _ = make_device()
    out = dev._find_matching_response(["SN1 TEMP=72F"], "SN1 TEMP?")
    assert out == "SN1 TEMP=72F"


def test_find_matching_response_partial_device_match() -> None:
    dev, _, _ = make_device()
    out = dev._find_matching_response(["SN1 OTHER=X"], "SN1 TEMP?")
    assert out == "SN1 OTHER=X"


def test_find_matching_response_no_device_match() -> None:
    dev, _, _ = make_device()
    out = dev._find_matching_response(["SN9 TEMP=72F"], "SN1 TEMP?")
    assert out is None


def test_find_matching_response_device_id_unparsable() -> None:
    dev, _, _ = make_device()
    # "SNx" → int("x") raises → device_id stays None → no matches.
    out = dev._find_matching_response(["SN1 TEMP=72F"], "SNx TEMP?")
    # Even with device_id=None, the partial loop returns first response that
    # starts with whatever — but the device_match block needs an int.
    # In the integer-fallback, regex still pulls SN1, doesn't match None → no result.
    assert out is None


# ---- _enable_cos_with_retry -----------------------------------------------


async def test_enable_cos_with_retry_succeeds() -> None:
    dev, conn, _ = make_device()

    async def reply(cmd, timeout=None):
        if "CR=NORMAL" in cmd:
            return "SN1 CR=NORMAL"
        if "c1=ON" in cmd or "c2=ON" in cmd:
            return cmd.strip()
        return None

    conn.async_send_command_with_response = reply
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._enable_cos_with_retry({"c1", "c2"})
    assert result is True
    assert "c1" in dev._cos_flags or "c2" in dev._cos_flags


async def test_enable_cos_with_retry_cr_fails() -> None:
    dev, conn, _ = make_device()
    conn.async_send_command_with_response = AsyncMock(return_value=None)
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._enable_cos_with_retry({"c1"})
    assert result is False


async def test_enable_cos_with_retry_flag_exception() -> None:
    dev, conn, _ = make_device()

    async def replies(cmd, timeout=None):
        if "CR" in cmd:
            return "SN1 CR=NORMAL"
        if "c1" in cmd:
            raise RuntimeError("flag broke")
        if "c2" in cmd:
            return "SN1 c2=ON"
        return None

    conn.async_send_command_with_response = replies
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._enable_cos_with_retry({"c1", "c2"})
    # c2 worked → True
    assert result is True


async def test_enable_cos_with_retry_outer_exception() -> None:
    dev, _, _ = make_device()
    with patch.object(dev, "_send_command_with_retry", side_effect=RuntimeError("boom")), \
         patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        result = await dev._enable_cos_with_retry({"c1"})
    assert result is False


# ---- _update_with_delays / _update_alarm_statuses / _update_error_status --


async def test_update_with_delays(monkeypatch) -> None:
    dev, _, _ = make_device()

    async def stub_send(cmd, retries=2, allow_skip=False, timeout=3.0):
        if "TEMP" in cmd:
            return "SN1 TEMP=72F"
        return None

    monkeypatch.setattr(dev, "_send_command_with_retry", stub_send)
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev._update_with_delays()
    assert dev._state["temperature"] == 72.0


async def test_update_with_delays_optional_exception(monkeypatch) -> None:
    dev, _, _ = make_device()
    calls = {"n": 0}

    async def stub_send(cmd, retries=2, allow_skip=False, timeout=3.0):
        calls["n"] += 1
        if "HUM" in cmd:
            raise RuntimeError("hum broke")
        return None

    monkeypatch.setattr(dev, "_send_command_with_retry", stub_send)
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        await dev._update_with_delays()
    assert calls["n"] > 1  # continued past the failure


async def test_update_alarm_statuses() -> None:
    dev, conn, _ = make_device()
    conn.responses["SN1 FLTALM?"] = "SN1 FLTALM=ON"
    conn.responses["SN1 WPALM?"] = "SN1 WPALM=OFF"
    conn.responses["SN1 SYSALM?"] = "SN1 SYSALM=ON"
    conn.responses["SN1 DEHALM?"] = "SN1 DEHALM=OFF"
    await dev._update_alarm_statuses()
    assert dev._state["filter_alarm"] is True
    assert dev._state["water_panel_alarm"] is False
    assert dev._state["system_alarm"] is True


async def test_update_alarm_statuses_exception() -> None:
    dev, _, proto = make_device()
    proto.execute_query_command = AsyncMock(side_effect=RuntimeError("boom"))
    await dev._update_alarm_statuses()  # swallowed


async def test_update_error_status() -> None:
    dev, conn, _ = make_device()
    conn.responses["SN1 ERROR?"] = "SN1 ERROR=001000"
    await dev._update_error_status()
    assert dev._state["error_status"] == "001000"


async def test_update_error_status_exception() -> None:
    dev, _, proto = make_device()
    proto.execute_query_command = AsyncMock(side_effect=RuntimeError("boom"))
    await dev._update_error_status()  # swallowed


# ---- AprilaireDeviceManager ------------------------------------------------


async def test_discover_devices_happy_path() -> None:
    coord = MagicMock()
    proto = MagicMock()
    mgr = AprilaireDeviceManager(coord, proto)

    conn = MagicMock()
    conn.async_send_command = AsyncMock()
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1", "SN3", "SN9"]])
    with patch("custom_components.aprilaire_8870.device.asyncio.sleep", new=AsyncMock()):
        addrs = await mgr.async_discover_devices(conn)
    assert addrs == [1, 3, 9]


async def test_discover_devices_no_messages_method() -> None:
    coord = MagicMock()
    mgr = AprilaireDeviceManager(coord, MagicMock())
    conn = MagicMock(spec=[])
    conn.async_send_command = AsyncMock()
    addrs = await mgr.async_discover_devices(conn)
    assert addrs == []


async def test_discover_devices_exception() -> None:
    coord = MagicMock()
    mgr = AprilaireDeviceManager(coord, MagicMock())
    conn = MagicMock()
    conn.async_send_command = AsyncMock(side_effect=RuntimeError("boom"))
    conn.get_received_messages = MagicMock(return_value=[])
    addrs = await mgr.async_discover_devices(conn)
    assert addrs == []


async def test_setup_device_returns_existing() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    existing = MagicMock()
    mgr.devices[1] = existing
    assert (await mgr.async_setup_device(1)) is existing


async def test_setup_device_success() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    with patch.object(AprilaireDevice, "async_initialize", new=AsyncMock(return_value=True)):
        dev = await mgr.async_setup_device(2)
    assert dev is not None
    assert 2 in mgr.devices


def test_device_uses_preset_name_when_provided() -> None:
    """preset_name from the config-flow probe seeds device.name."""
    dev = AprilaireDevice(5, MagicMock(), MagicMock(), preset_name="Den")
    assert dev.name == "Den"


def test_device_falls_back_to_address_name_when_no_preset() -> None:
    dev = AprilaireDevice(5, MagicMock(), MagicMock())
    assert dev.name == "Aprilaire 5"


async def test_manager_threads_preset_name_through_to_device() -> None:
    """Manager wires device_names dict into AprilaireDevice.preset_name."""
    mgr = AprilaireDeviceManager(
        MagicMock(), MagicMock(), device_names={"3": "Living Room"}
    )
    with patch.object(AprilaireDevice, "async_initialize", new=AsyncMock(return_value=True)):
        dev = await mgr.async_setup_device(3)
    assert dev is not None
    assert dev.name == "Living Room"


async def test_manager_uses_default_name_for_unnamed_address() -> None:
    mgr = AprilaireDeviceManager(
        MagicMock(), MagicMock(), device_names={"3": "Living Room"}
    )
    with patch.object(AprilaireDevice, "async_initialize", new=AsyncMock(return_value=True)):
        dev = await mgr.async_setup_device(4)
    assert dev is not None
    assert dev.name == "Aprilaire 4"


async def test_setup_device_failure() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    with patch.object(AprilaireDevice, "async_initialize", new=AsyncMock(return_value=False)):
        dev = await mgr.async_setup_device(2)
    assert dev is None


async def test_update_placeholder_existing() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    placeholder = AprilaireDevice(1, MagicMock(), None)
    real = AprilaireDevice(1, MagicMock(), MagicMock())
    real.model = "REAL"
    mgr.devices[1] = placeholder
    await mgr.update_placeholder_device(1, real)
    assert mgr.devices[1] is placeholder
    assert placeholder.model == "REAL"


async def test_update_placeholder_replaces_when_missing() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    real = MagicMock()
    await mgr.update_placeholder_device(5, real)
    assert mgr.devices[5] is real


async def test_async_update_all_happy_and_error() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    ok = MagicMock()
    ok.async_update = AsyncMock(return_value=True)
    bad = MagicMock()
    bad.async_update = AsyncMock(side_effect=RuntimeError("boom"))
    mgr.devices[1] = ok
    mgr.devices[2] = bad
    await mgr.async_update_all()
    ok.async_update.assert_called_once()
    bad.async_update.assert_called_once()


def test_get_device_and_all() -> None:
    mgr = AprilaireDeviceManager(MagicMock(), MagicMock())
    d = MagicMock()
    mgr.devices[1] = d
    assert mgr.get_device(1) is d
    assert mgr.get_device(99) is None
    assert mgr.get_all_devices() == [d]
