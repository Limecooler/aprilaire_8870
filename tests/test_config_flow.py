"""Tests for config_flow.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow

from custom_components.aprilaire_8870 import config_flow as cf
from custom_components.aprilaire_8870.const import DOMAIN


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    """Make HA's loader aware of our custom integration."""
    yield


# ---- ConfigFlow tests -----------------------------------------------------


async def test_user_step_shows_connection_type(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "connection_type"


async def test_connection_type_chooses_serial_server(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    next_result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"type": "serial_server"}
    )
    assert next_result["step_id"] == "serial_server"


async def test_connection_type_chooses_serial_port(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    next_result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"type": "serial_port"}
    )
    assert next_result["step_id"] == "serial_port"


def _patch_discovery(
    addresses=None,
    model_response="MODEL 8870",
    names=None,
):
    """Returns a context manager that stubs out network IO during discovery.

    `names` is an optional {address: name} dict — when set, each per-address
    ID? response includes the name in the SN<addr> prefix so the config flow's
    name probe picks it up. Addresses not in the dict get a plain response.
    """
    addresses = addresses if addresses is not None else [1, 2]
    names = names or {}

    # The flow reads once for the SN? broadcast, then once per address for ID?.
    received_seq: list[list[str]] = [
        # SN? broadcast responses
        [f"SN{a}" for a in addresses],
    ]
    for addr in addresses:
        name = names.get(addr, "")
        prefix = f"SN{addr}{name} " if name else f"SN{addr} "
        # ID? response includes the model if model_response is set; otherwise
        # an empty list simulates no reply.
        if model_response:
            received_seq.append([f"{prefix}ID={model_response}"])
        else:
            received_seq.append([])

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        self._state = "disconnected"

    async def stub_start_reading(self):
        pass

    async def stub_stop_reading(self):
        pass

    async def stub_send_command(self, command):
        return None

    # The new future-registry-based send-with-response (v0.3.0) waits up to
    # `timeout` seconds for the read loop to deliver a matching response.
    # Tests don't run a real read loop, so without this stub the background
    # device-init task blocks for 3s × N devices on every test and trips
    # pytest's lingering-task detector. Returning None here matches the
    # observable behavior of "no response within timeout".
    async def stub_send_with_response(self, command, timeout=3.0):
        return None

    # v0.4.0: config_flow now bulk-probes ID? via SN0. Stub returns the
    # model line per discovered address, embedding the configured name in
    # the prefix when one is set. Matches the real 8870 ID response shape:
    # ``SN<addr><name>  MODEL# 8870 REV: V1.2 - RPC 2002`` — note the `#`
    # boundary (not `=`) on ID responses, and the two-space gap separating
    # the optional name from the response code.
    async def stub_send_global_command(self, command, expected_addresses, timeout=5.0):
        if "ID" not in command:
            return {}
        # If the caller asked for a non-8870 model, omit the '#' so the
        # not_aprilaire_8870 check trips correctly in test_serial_server_not_aprilaire.
        if "8870" in model_response:
            payload = f"MODEL# 8870 REV: V1.2 - RPC 2002"
        else:
            payload = model_response  # e.g. "MODEL OTHER"
        result = {}
        for addr in expected_addresses:
            name = names.get(addr, "")
            prefix = f"SN{addr}{name}" if name else f"SN{addr}"
            result[addr] = f"{prefix}  {payload}"
        return result

    def stub_get_received_messages(self):
        return received_seq.pop(0) if received_seq else []

    patches = [
        patch.object(cf.SerialServerConnection, "async_connect", new=stub_connect),
        patch.object(cf.SerialServerConnection, "async_disconnect", new=stub_disconnect),
        patch.object(cf.SerialServerConnection, "async_start_reading", new=stub_start_reading),
        patch.object(cf.SerialServerConnection, "async_stop_reading", new=stub_stop_reading),
        patch.object(cf.SerialServerConnection, "async_send_command", new=stub_send_command),
        patch.object(
            cf.SerialServerConnection,
            "async_send_command_with_response",
            new=stub_send_with_response,
        ),
        patch.object(
            cf.SerialServerConnection,
            "async_send_global_command",
            new=stub_send_global_command,
        ),
        patch.object(
            cf.SerialServerConnection,
            "get_received_messages",
            new=stub_get_received_messages,
        ),
        patch(
            "custom_components.aprilaire_8870.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ),
    ]
    return patches


async def _start_serial_server_flow(hass, host="1.2.3.4", port=23):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"type": "serial_server"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": host, "port": port}
    )


async def test_serial_server_success(hass) -> None:
    patches = _patch_discovery(addresses=[1, 2])
    for p in patches:
        p.start()
    try:
        result = await _start_serial_server_flow(hass)
    finally:
        for p in patches:
            p.stop()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Aprilaire Thermostat (2 devices)"
    assert result["data"]["discovered_thermostats"] == [1, 2]
    # No names configured on the thermostats — device_names is an empty dict.
    assert result["data"]["device_names"] == {}


async def test_serial_server_picks_up_device_names(hass) -> None:
    """Thermostats with a location name return it in the response prefix."""
    patches = _patch_discovery(
        addresses=[1, 2, 3],
        names={1: "Master Bedroom", 3: "Living Room"},
    )
    for p in patches:
        p.start()
    try:
        result = await _start_serial_server_flow(hass)
    finally:
        for p in patches:
            p.stop()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    # Only addresses with names appear; unnamed ones stay numbered.
    assert result["data"]["device_names"] == {
        "1": "Master Bedroom",
        "3": "Living Room",
    }


def test_parse_location_name_extracts_name() -> None:
    assert cf._parse_location_name(1, ["SN1Master Bedroom ID=8870"]) == "Master Bedroom"
    assert cf._parse_location_name(2, ["SN2 ID=8870"]) is None
    assert cf._parse_location_name(7, ["SN7Kitchen TEMP=72.5"]) == "Kitchen"


def test_parse_location_name_handles_model_hash_format() -> None:
    """Real-firmware ID responses use ``MODEL#`` (not ``=``) as the boundary."""
    assert cf._parse_location_name(
        3, ["SN3Master Bedroom  MODEL# 8870 REV: V1.2 - RPC 2002"]
    ) == "Master Bedroom"
    # No name set — empty result.
    assert cf._parse_location_name(
        1, ["SN1  MODEL# 8870 REV: V1.2 - RPC 2002"]
    ) is None


def test_parse_location_name_handles_single_letter_codes() -> None:
    """Real-firmware short-form responses (T=, M=, F=) also have valid prefixes."""
    assert cf._parse_location_name(11, ["SN11Basement Utility  T=76F"]) == "Basement Utility"
    assert cf._parse_location_name(7, ["SN7Billy Bedroom  M=COOL"]) == "Billy Bedroom"
    # Wrong address — no match.
    assert cf._parse_location_name(99, ["SN1Master Bedroom ID=8870"]) is None
    # Empty input.
    assert cf._parse_location_name(1, []) is None
    assert cf._parse_location_name(1, [None]) is None  # type: ignore[list-item]


async def test_serial_server_connect_fails(hass) -> None:
    async def boom(self):
        raise RuntimeError("connect failed")

    async def stub_disconnect(self):
        pass

    with patch.object(cf.SerialServerConnection, "async_connect", new=boom), \
         patch.object(cf.SerialServerConnection, "async_disconnect", new=stub_disconnect):
        result = await _start_serial_server_flow(hass)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_serial_server_already_configured(hass) -> None:
    # Pre-create an entry with the same unique_id.
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    MockConfigEntry(
        domain=DOMAIN, unique_id="1.2.3.4:23", data={"connection_type": "serial_server"}
    ).add_to_hass(hass)

    result = await _start_serial_server_flow(hass)
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_serial_server_no_devices(hass) -> None:
    patches = _patch_discovery(addresses=[], model_response="")
    for p in patches:
        p.start()
    try:
        result = await _start_serial_server_flow(hass)
    finally:
        for p in patches:
            p.stop()
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_serial_server_not_aprilaire(hass) -> None:
    patches = _patch_discovery(addresses=[1], model_response="MODEL OTHER")
    for p in patches:
        p.start()
    try:
        result = await _start_serial_server_flow(hass)
    finally:
        for p in patches:
            p.stop()
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "not_aprilaire_8870"


async def test_serial_server_discovery_error(hass) -> None:
    async def boom(self):
        raise RuntimeError("connect failed")

    async def stub_disconnect(self):
        pass

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_start_reading(self):
        raise RuntimeError("read failed")

    with patch.object(cf.SerialServerConnection, "async_connect", new=stub_connect), \
         patch.object(cf.SerialServerConnection, "async_disconnect", new=stub_disconnect), \
         patch.object(cf.SerialServerConnection, "async_start_reading", new=stub_start_reading):
        result = await _start_serial_server_flow(hass)
    assert result["reason"] == "discovery_error"


async def _start_serial_port_flow(hass, device="/dev/ttyUSB0", baud=9600):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"type": "serial_port"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": device, "baud_rate": baud}
    )


async def test_serial_port_success(hass) -> None:
    received_seq = [["SN1"], ["MODEL 8870"]]

    async def stub_connect(self):
        self._state = "connected"
        return True

    async def stub_disconnect(self):
        pass

    async def stub_start(self):
        pass

    async def stub_stop(self):
        pass

    async def stub_send(self, cmd):
        pass

    async def stub_send_with_response(self, command, timeout=3.0):
        # See test_config_flow stub_send_with_response for rationale.
        return None

    async def stub_send_global_command(self, command, expected_addresses, timeout=5.0):
        if "ID" in command:
            return {addr: f"SN{addr}  MODEL 8870" for addr in expected_addresses}
        return {}

    def stub_recv(self):
        return received_seq.pop(0) if received_seq else []

    with patch.object(cf.ComPortConnection, "async_connect", new=stub_connect), \
         patch.object(cf.ComPortConnection, "async_disconnect", new=stub_disconnect), \
         patch.object(cf.ComPortConnection, "async_start_reading", new=stub_start), \
         patch.object(cf.ComPortConnection, "async_send_global_command", new=stub_send_global_command), \
         patch.object(cf.ComPortConnection, "async_stop_reading", new=stub_stop), \
         patch.object(cf.ComPortConnection, "async_send_command", new=stub_send), \
         patch.object(cf.ComPortConnection, "async_send_command_with_response", new=stub_send_with_response), \
         patch.object(cf.ComPortConnection, "get_received_messages", new=stub_recv), \
         patch("custom_components.aprilaire_8870.config_flow.asyncio.sleep", new=AsyncMock()):
        result = await _start_serial_port_flow(hass)
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


async def test_serial_port_connect_fails(hass) -> None:
    async def boom(self):
        raise RuntimeError("connect failed")

    async def stub_disconnect(self):
        pass

    with patch.object(cf.ComPortConnection, "async_connect", new=boom), \
         patch.object(cf.ComPortConnection, "async_disconnect", new=stub_disconnect):
        result = await _start_serial_port_flow(hass)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_serial_port_already_configured(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    MockConfigEntry(
        domain=DOMAIN, unique_id="/dev/ttyUSB0", data={"connection_type": "serial_port"}
    ).add_to_hass(hass)
    result = await _start_serial_port_flow(hass)
    assert result["reason"] == "already_configured"


# ---- _create_connection internal ------------------------------------------


def test_create_connection_invalid_type(hass) -> None:
    flow = cf.AprilaireConfigFlow()
    flow.hass = hass
    with pytest.raises(ValueError):
        flow._create_connection({"connection_type": "bogus"})


def test_create_connection_serial_server(hass) -> None:
    flow = cf.AprilaireConfigFlow()
    flow.hass = hass
    conn = flow._create_connection(
        {"connection_type": "serial_server", "host": "h", "port": 1}
    )
    assert isinstance(conn, cf.SerialServerConnection)


def test_create_connection_serial_port(hass) -> None:
    flow = cf.AprilaireConfigFlow()
    flow.hass = hass
    conn = flow._create_connection(
        {"connection_type": "serial_port", "port_name": "/x", "baud_rate": 9600}
    )
    assert isinstance(conn, cf.ComPortConnection)


# ---- Options flow ----------------------------------------------------------


async def test_options_flow_shows_form(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"temperature_unit": "C"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    final = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "temperature_unit": "F",
            "command_retry_count": 5,
            "connection_backoff_max": 200,
            "debug_mode": True,
        },
    )
    assert final["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert final["data"]["temperature_unit"] == "F"


def test_options_flow_get_static(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    assert isinstance(
        cf.AprilaireConfigFlow.async_get_options_flow(entry), cf.AprilaireOptionsFlowHandler
    )
