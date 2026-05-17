"""Tests for connection.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aprilaire_8870.connection import (
    AprilaireConnectionBase,
    ComPortConnection,
    ConnectionManager,
    SerialProtocol,
    SerialServerConnection,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_DISCONNECTED,
    STATE_ERROR,
)


# Helpers -------------------------------------------------------------------


class _FakeWriter:
    def __init__(self):
        self.written: list[str] = []
        self.closed = False

    def write(self, data) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeReader:
    def __init__(self, chunks: list[bytes | str] | None = None):
        self._chunks = list(chunks or [])

    async def read(self, n: int):
        if not self._chunks:
            await asyncio.sleep(0.01)
            return ""
        return self._chunks.pop(0)


@pytest.fixture
def fake_hass(hass):
    """Just an alias — we use the pytest_homeassistant_custom_component `hass` fixture."""
    return hass


def _make_serial_conn(hass, **overrides) -> SerialServerConnection:
    config = {"host": "1.2.3.4", "port": 23}
    config.update(overrides)
    return SerialServerConnection(hass, config)


def _make_com_conn(hass, **overrides) -> ComPortConnection:
    config = {"port_name": "/dev/null", "baud_rate": 9600}
    config.update(overrides)
    return ComPortConnection(hass, config)


# Base class ----------------------------------------------------------------


async def test_state_setter_short_circuits_when_unchanged(hass) -> None:
    conn = _make_serial_conn(hass)
    callback = MagicMock()
    conn.register_connection_callback(callback)
    conn.state = STATE_DISCONNECTED  # already disconnected — no change
    callback.assert_not_called()


async def test_state_setter_triggers_callback(hass) -> None:
    conn = _make_serial_conn(hass)
    callback = MagicMock()
    conn.register_connection_callback(callback)
    conn.state = STATE_CONNECTED
    callback.assert_called_with(STATE_CONNECTED)


async def test_register_message_callback(hass) -> None:
    conn = _make_serial_conn(hass)
    cb = MagicMock()
    conn.register_message_callback(cb)
    assert conn._message_callback is cb


async def test_is_connected(hass) -> None:
    conn = _make_serial_conn(hass)
    assert conn.is_connected() is False
    conn._state = STATE_CONNECTED
    assert conn.is_connected() is True


# _async_process_data -------------------------------------------------------


async def test_process_data_decodes_binary(hass) -> None:
    conn = _make_serial_conn(hass)
    msgs: list[str] = []
    conn.register_message_callback(lambda m: msgs.append(m))
    # ASCII printable bytes + a CR delimiter at the end of a SN1 message.
    payload = b"SN1 TEMP=72F\r"
    await conn._async_process_data(payload)
    assert msgs == ["SN1 TEMP=72F"]
    assert conn._received_messages == ["SN1 TEMP=72F"]


async def test_process_data_ignores_non_printable_bytes(hass) -> None:
    conn = _make_serial_conn(hass)
    msgs: list[str] = []
    conn.register_message_callback(lambda m: msgs.append(m))
    # 0x00 should be skipped.
    payload = b"\x00SN1 X=1\r"
    await conn._async_process_data(payload)
    assert msgs == ["SN1 X=1"]


async def test_process_data_str_input(hass) -> None:
    conn = _make_serial_conn(hass)
    msgs: list[str] = []
    conn.register_message_callback(lambda m: msgs.append(m))
    await conn._async_process_data("SN1 A=1\rextra")
    assert msgs == ["SN1 A=1"]
    assert conn._buffer == "extra"


async def test_process_data_lf_treated_as_cr(hass) -> None:
    conn = _make_serial_conn(hass)
    msgs: list[str] = []
    conn.register_message_callback(lambda m: msgs.append(m))
    # Line feed (0x0A) gets normalized to \r by the byte filter.
    await conn._async_process_data(b"SN1 X=1\n")
    assert msgs == ["SN1 X=1"]


async def test_process_data_skips_non_sn_lines(hass) -> None:
    conn = _make_serial_conn(hass)
    msgs: list[str] = []
    conn.register_message_callback(lambda m: msgs.append(m))
    await conn._async_process_data("noise without SN prefix\r")
    assert msgs == []


async def test_process_data_no_callback(hass) -> None:
    conn = _make_serial_conn(hass)
    # No message callback registered — should still buffer.
    await conn._async_process_data("SN1 X=1\r")
    assert conn._received_messages == ["SN1 X=1"]


async def test_process_data_buffer_capped(hass) -> None:
    conn = _make_serial_conn(hass)
    # Feed > 4096 chars of garbage (no CR so nothing flushes).
    await conn._async_process_data("X" * 5000)
    assert len(conn._buffer) == 4096


async def test_process_data_unconvertible_input(hass) -> None:
    conn = _make_serial_conn(hass)

    class _Bad:
        def __str__(self):
            raise RuntimeError("nope")

    # Should log and return without raising.
    await conn._async_process_data(_Bad())
    assert conn._buffer == ""


# get_received_messages -----------------------------------------------------


async def test_get_received_messages_clears(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._received_messages = ["a", "b"]
    assert conn.get_received_messages() == ["a", "b"]
    assert conn._received_messages == []


# Backoff -------------------------------------------------------------------


async def test_calculate_backoff_delay_uses_factor(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._connect_error_count = 3
    # base = 2^3 = 8; jitter ±10% so delay ∈ [7.2, 8.8]
    delay = conn._calculate_backoff_delay()
    assert 7.2 <= delay <= 8.8


async def test_calculate_backoff_delay_caps_at_max(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._connect_error_count = 100  # 2^100 dwarfs CONNECTION_BACKOFF_MAX
    delay = conn._calculate_backoff_delay()
    assert delay <= 330  # 300 + 10% jitter


async def test_async_reconnect_with_backoff_succeeds(hass) -> None:
    conn = _make_serial_conn(hass)
    with patch.object(conn, "async_connect", new=AsyncMock(return_value=True)):
        assert await conn.async_reconnect_with_backoff() is True


async def test_async_reconnect_with_backoff_gives_up(hass) -> None:
    conn = _make_serial_conn(hass)
    # Speed the test up: patch asyncio.sleep
    with patch.object(conn, "async_connect", new=AsyncMock(return_value=False)), \
         patch("custom_components.aprilaire_8870.connection.asyncio.sleep", new=AsyncMock()):
        assert await conn.async_reconnect_with_backoff() is False


# async_start/stop_reading --------------------------------------------------


async def test_start_reading_is_idempotent(hass) -> None:
    conn = _make_serial_conn(hass)

    async def read_loop_stub() -> None:
        await asyncio.sleep(0.01)

    conn._read_task = hass.loop.create_task(read_loop_stub())
    try:
        await conn.async_start_reading()  # already running — no-op
    finally:
        await conn._read_task
    conn._read_task = None


async def test_start_reading_creates_task(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_DISCONNECTED  # so the loop exits immediately
    await conn.async_start_reading()
    try:
        assert conn._read_task is not None
        await asyncio.wait_for(conn._read_task, timeout=1.0)
    finally:
        conn._read_task = None


async def test_stop_reading_when_idle(hass) -> None:
    conn = _make_serial_conn(hass)
    await conn.async_stop_reading()  # no task — no error


async def test_stop_reading_cancels(hass) -> None:
    conn = _make_serial_conn(hass)

    async def long_loop() -> None:
        await asyncio.sleep(10)

    conn._read_task = hass.loop.create_task(long_loop())
    await conn.async_stop_reading()
    assert conn._read_task is None


# SerialServerConnection ----------------------------------------------------


def test_safe_decode_ascii(hass) -> None:
    conn = _make_serial_conn(hass)
    assert conn._safe_decode(b"") == ""
    assert conn._safe_decode(b"hello") == "hello"


def test_safe_decode_filters_non_ascii(hass) -> None:
    conn = _make_serial_conn(hass)
    # The implementation tries .decode("ascii", errors="replace") first;
    # that succeeds (replacements only), so we just sanity-check it returns a str.
    out = conn._safe_decode(b"\xff\xfehi")
    assert isinstance(out, str)
    assert "hi" in out


async def test_async_connect_telnet_success(hass) -> None:
    conn = _make_serial_conn(hass)
    reader, writer = _FakeReader(), _FakeWriter()
    with patch(
        "custom_components.aprilaire_8870.connection.telnetlib3.open_connection",
        new=AsyncMock(return_value=(reader, writer)),
    ):
        assert await conn.async_connect() is True
    assert conn.is_connected()
    # Calling again is a no-op when already connected.
    assert await conn.async_connect() is True


async def test_async_connect_telnet_timeout(hass) -> None:
    conn = _make_serial_conn(hass)
    with patch(
        "custom_components.aprilaire_8870.connection.telnetlib3.open_connection",
        new=AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        assert await conn.async_connect() is False
    assert conn.state == STATE_ERROR
    assert conn._connect_error_count == 1


async def test_async_connect_telnet_generic_error(hass) -> None:
    conn = _make_serial_conn(hass)
    with patch(
        "custom_components.aprilaire_8870.connection.telnetlib3.open_connection",
        new=AsyncMock(side_effect=OSError("refused")),
    ):
        assert await conn.async_connect() is False


async def test_async_disconnect_serial_server(hass) -> None:
    conn = _make_serial_conn(hass)
    writer = _FakeWriter()
    conn._writer = writer
    conn._state = STATE_CONNECTED
    await conn.async_disconnect()
    assert writer.closed
    assert conn.state == STATE_DISCONNECTED


async def test_async_disconnect_swallows_close_errors(hass) -> None:
    conn = _make_serial_conn(hass)
    writer = MagicMock()
    writer.close = MagicMock(side_effect=RuntimeError("nope"))
    conn._writer = writer
    conn._state = STATE_CONNECTED
    await conn.async_disconnect()
    assert conn.state == STATE_DISCONNECTED


async def test_async_disconnect_when_no_writer(hass) -> None:
    conn = _make_serial_conn(hass)
    await conn.async_disconnect()
    assert conn.state == STATE_DISCONNECTED


async def test_async_send_command_not_connected(hass) -> None:
    conn = _make_serial_conn(hass)
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await conn.async_send_command("SN1 X?")


async def test_async_send_command_writes(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()
    await conn.async_send_command("SN1 X?")
    assert conn._writer.written == ["SN1 X?\r"]


async def test_async_send_command_already_has_cr(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()
    await conn.async_send_command("SN1 X?\r")
    assert conn._writer.written == ["SN1 X?\r"]


async def test_async_send_command_write_error_reconnects(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    bad_writer = MagicMock()
    bad_writer.write = MagicMock(side_effect=RuntimeError("write failed"))
    conn._writer = bad_writer
    from homeassistant.exceptions import HomeAssistantError

    with patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        with pytest.raises(HomeAssistantError):
            await conn.async_send_command("SN1 X?")


# _async_read_data on SerialServerConnection --------------------------------


async def test_serial_server_read_data_returns_bytes(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._reader = _FakeReader([b"SN1 X=1\r"])
    data = await conn._async_read_data()
    assert data == b"SN1 X=1\r"


async def test_serial_server_read_data_eof_triggers_reconnect(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._reader = MagicMock()
    conn._reader.read = AsyncMock(return_value=b"")
    with patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        data = await conn._async_read_data()
    assert data is None
    assert conn.state == STATE_ERROR


async def test_serial_server_read_data_not_connected(hass) -> None:
    conn = _make_serial_conn(hass)
    assert await conn._async_read_data() is None


async def test_serial_server_read_data_exception(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._reader = MagicMock()
    conn._reader.read = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        assert await conn._async_read_data() is None
    assert conn.state == STATE_ERROR


# async_send_command_with_response ------------------------------------------


async def test_send_with_response_not_connected(hass) -> None:
    conn = _make_serial_conn(hass)
    assert await conn.async_send_command_with_response("SN1 X?", timeout=0.01) is None


async def test_send_with_response_matches_device_and_command(hass) -> None:
    """Future registry resolves on matching address."""
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()

    async def deliver() -> None:
        await asyncio.sleep(0.05)
        # Mimic the read loop: append AND resolve the matching pending future.
        conn._received_messages.append("SN1 TEMP=72F")
        conn._try_resolve_pending("SN1 TEMP=72F")

    asyncio.create_task(deliver())
    result = await conn.async_send_command_with_response("SN1 TEMP?", timeout=1.0)
    assert result == "SN1 TEMP=72F"


async def test_send_with_response_lenient_fallback(hass) -> None:
    """Any response from the correct address satisfies the pending request.

    Address-only matching is intentional: handles oddball commands like ID?
    whose response code (MODEL#) doesn't match the request code.
    """
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()

    async def deliver() -> None:
        await asyncio.sleep(0.05)
        conn._received_messages.append("SN1 OTHER=X")
        conn._try_resolve_pending("SN1 OTHER=X")

    asyncio.create_task(deliver())
    result = await conn.async_send_command_with_response("SN1 TEMP?", timeout=1.0)
    assert result == "SN1 OTHER=X"


async def test_send_with_response_no_device_id(hass) -> None:
    """Non-SN<digits> commands take the send-only fallback path."""
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()

    # "SN ID?" doesn't match the (address, cmd) command-parse regex, so the
    # send-only path is used and returns None. The caller is expected to
    # read responses out of get_received_messages() themselves (this is how
    # the SN? discovery probe in config_flow works).
    result = await conn.async_send_command_with_response("SN ID?", timeout=0.05)
    assert result is None


async def test_send_with_response_no_message(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()
    result = await conn.async_send_command_with_response("SN1 TEMP?", timeout=0.05)
    assert result is None


async def test_send_with_response_wrong_device(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()
    conn._received_messages.append("SN9 TEMP=72F")
    result = await conn.async_send_command_with_response("SN1 TEMP?", timeout=0.05)
    assert result is None


async def test_send_with_response_exception(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    bad_writer = MagicMock()
    bad_writer.write = MagicMock(side_effect=RuntimeError("broken"))
    conn._writer = bad_writer
    with patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        assert await conn.async_send_command_with_response("SN1 X?", timeout=0.01) is None


# _async_read_loop ----------------------------------------------------------


async def test_read_loop_dispatches(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    seen: list[bytes] = []

    async def fake_read():
        if seen:
            conn._state = STATE_DISCONNECTED  # break the loop
            return None
        seen.append(b"x")
        return b"SN1 X=1\r"

    with patch.object(conn, "_async_read_data", side_effect=fake_read), \
         patch.object(conn, "_async_process_data", new=AsyncMock()) as proc:
        await conn._async_read_loop()
        proc.assert_called_once_with(b"SN1 X=1\r")


async def test_read_loop_handles_error(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    calls = {"n": 0}

    async def fake_read():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        conn._state = STATE_DISCONNECTED
        return None

    with patch.object(conn, "_async_read_data", side_effect=fake_read), \
         patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        await conn._async_read_loop()
    # We saw STATE_ERROR during the loop.
    assert calls["n"] >= 1


async def test_read_loop_handles_cancellation(hass) -> None:
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    with patch.object(conn, "_async_read_data", side_effect=asyncio.CancelledError):
        await conn._async_read_loop()


# async_reconnect (base class) ----------------------------------------------


async def test_async_reconnect_calls_disconnect_and_connect(hass) -> None:
    conn = _make_serial_conn(hass)
    with patch.object(conn, "async_disconnect", new=AsyncMock()) as disc, \
         patch.object(conn, "async_connect", new=AsyncMock(return_value=True)) as conn_mock, \
         patch("custom_components.aprilaire_8870.connection.asyncio.sleep", new=AsyncMock()):
        assert await conn.async_reconnect() is True
    disc.assert_called_once()
    conn_mock.assert_called_once()


# ComPortConnection ---------------------------------------------------------


def test_com_safe_decode(hass) -> None:
    conn = _make_com_conn(hass)
    assert conn._safe_decode(b"") == ""
    assert conn._safe_decode(b"hello") == "hello"


async def test_com_async_connect_success(hass) -> None:
    conn = _make_com_conn(hass)
    transport = MagicMock()
    protocol_obj = MagicMock()
    with patch(
        "custom_components.aprilaire_8870.connection.serial_asyncio.create_serial_connection",
        new=AsyncMock(return_value=(transport, protocol_obj)),
    ):
        assert await conn.async_connect() is True
    assert conn.is_connected()
    # Already connected — short circuit.
    assert await conn.async_connect() is True


async def test_com_async_connect_failure(hass) -> None:
    conn = _make_com_conn(hass)
    with patch(
        "custom_components.aprilaire_8870.connection.serial_asyncio.create_serial_connection",
        new=AsyncMock(side_effect=OSError("port busy")),
    ):
        assert await conn.async_connect() is False
    assert conn.state == STATE_ERROR


async def test_com_disconnect(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_transport = MagicMock()
    await conn.async_disconnect()
    assert conn._serial_transport is None
    assert conn.state == STATE_DISCONNECTED


async def test_com_disconnect_when_no_transport(hass) -> None:
    conn = _make_com_conn(hass)
    await conn.async_disconnect()
    assert conn.state == STATE_DISCONNECTED


async def test_com_send_command_not_connected(hass) -> None:
    conn = _make_com_conn(hass)
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await conn.async_send_command("SN1 X?")


async def test_com_send_command_writes(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_transport = MagicMock()
    await conn.async_send_command("SN1 X?")
    conn._serial_transport.write.assert_called_with(b"SN1 X?\r")


async def test_com_send_command_already_has_cr(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_transport = MagicMock()
    await conn.async_send_command("SN1 X?\r")
    conn._serial_transport.write.assert_called_with(b"SN1 X?\r")


async def test_com_send_command_write_error(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_transport = MagicMock()
    conn._serial_transport.write.side_effect = RuntimeError("bad")
    from homeassistant.exceptions import HomeAssistantError
    with patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        with pytest.raises(HomeAssistantError):
            await conn.async_send_command("SN1 X?")


async def test_com_read_data_not_connected(hass) -> None:
    conn = _make_com_conn(hass)
    assert await conn._async_read_data() is None


async def test_com_read_data_no_data(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_protocol = MagicMock()
    conn._serial_protocol.data = b""
    # Set the read_event so the wait returns instantly.
    conn._read_event.set()
    assert await conn._async_read_data() is None


async def test_com_read_data_returns_bytes(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_protocol = MagicMock()
    conn._serial_protocol.data = b"SN1 X=1\r"
    conn._read_event.set()
    result = await conn._async_read_data()
    assert result == b"SN1 X=1\r"


async def test_com_read_data_exception(hass) -> None:
    conn = _make_com_conn(hass)
    conn._state = STATE_CONNECTED
    conn._serial_protocol = MagicMock()
    # Force an error during data access by raising in the property setter.
    with patch.object(conn._read_event, "set", side_effect=RuntimeError("boom")), \
         patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        assert await conn._async_read_data() is None
    assert conn.state == STATE_ERROR


# SerialProtocol ------------------------------------------------------------


def test_serial_protocol_connection_made(hass) -> None:
    conn = _make_com_conn(hass)
    proto = SerialProtocol(conn)
    transport = MagicMock()
    proto.connection_made(transport)
    assert proto.transport is transport


def test_serial_protocol_data_received_appends(hass) -> None:
    conn = _make_com_conn(hass)
    proto = SerialProtocol(conn)
    proto.data_received(b"a")
    proto.data_received(b"b")
    assert proto.data == b"ab"


def test_serial_protocol_data_received_clears_event(hass) -> None:
    conn = _make_com_conn(hass)
    conn._read_event.set()
    proto = SerialProtocol(conn)
    proto.data_received(b"x")
    assert not conn._read_event.is_set()


async def test_serial_protocol_connection_lost_triggers_reconnect(hass) -> None:
    conn = _make_com_conn(hass)
    proto = SerialProtocol(conn)
    with patch.object(conn, "async_reconnect", new=AsyncMock(return_value=False)):
        proto.connection_lost(RuntimeError("lost"))
        # Yield once so the scheduled reconnect task runs.
        await asyncio.sleep(0)
    assert conn.state == STATE_ERROR


# ConnectionManager (nested under ComPortConnection in the source) ----------


async def test_connection_manager_reconnects_cached(hass) -> None:
    mgr = ConnectionManager(hass)
    fake = MagicMock()
    fake.is_connected = MagicMock(return_value=False)
    fake.async_connect = AsyncMock(return_value=True)
    mgr._connections["serial_server_1.2.3.4_23"] = fake
    same = await mgr.async_get_connection(
        {"connection_type": "serial_server", "host": "1.2.3.4", "port": 23}
    )
    assert same is fake
    fake.async_connect.assert_called_once()


async def test_send_with_response_ignores_other_address(hass) -> None:
    """A response for a different address doesn't satisfy our future."""
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()

    async def deliver() -> None:
        await asyncio.sleep(0.05)
        # SN9 — different address, no pending future for it. Ignored.
        conn._try_resolve_pending("SN9 NOISE=1")
        # SN1 — matches our request, resolves the future.
        conn._try_resolve_pending("SN1 TEMP=72F")

    asyncio.create_task(deliver())
    result = await conn.async_send_command_with_response("SN1 TEMP?", timeout=1.0)
    assert result == "SN1 TEMP=72F"


async def test_try_resolve_pending_no_match_when_no_pending(hass) -> None:
    """Stray messages with no pending request are no-ops."""
    conn = _make_serial_conn(hass)
    # No pending future for any address.
    conn._try_resolve_pending("SN1 TEMP=72F")
    # Garbage in shouldn't raise.
    conn._try_resolve_pending("not a thermostat line")
    conn._try_resolve_pending("")


async def test_cancel_all_pending_unblocks_waiters(hass) -> None:
    """Disconnect-time cancellation unblocks any in-flight requests."""
    conn = _make_serial_conn(hass)
    conn._state = STATE_CONNECTED
    conn._writer = _FakeWriter()

    async def send_then_check():
        return await conn.async_send_command_with_response("SN1 TEMP?", timeout=2.0)

    task = asyncio.create_task(send_then_check())
    await asyncio.sleep(0.05)  # let the request register
    assert 1 in conn._pending
    conn._cancel_all_pending()
    result = await task
    assert result is None
    assert 1 not in conn._pending


async def test_connection_manager_serial_server(hass) -> None:
    mgr = ConnectionManager(hass)
    with patch(
        "custom_components.aprilaire_8870.connection.telnetlib3.open_connection",
        new=AsyncMock(return_value=(_FakeReader(), _FakeWriter())),
    ):
        conn = await mgr.async_get_connection(
            {"connection_type": "serial_server", "host": "1.2.3.4", "port": 23}
        )
    assert isinstance(conn, SerialServerConnection)
    # Same args should return the cached instance.
    with patch(
        "custom_components.aprilaire_8870.connection.telnetlib3.open_connection",
        new=AsyncMock(return_value=(_FakeReader(), _FakeWriter())),
    ):
        same = await mgr.async_get_connection(
            {"connection_type": "serial_server", "host": "1.2.3.4", "port": 23}
        )
    assert same is conn


async def test_connection_manager_serial_port(hass) -> None:
    mgr = ConnectionManager(hass)
    with patch(
        "custom_components.aprilaire_8870.connection.serial_asyncio.create_serial_connection",
        new=AsyncMock(return_value=(MagicMock(), MagicMock())),
    ):
        conn = await mgr.async_get_connection(
            {"connection_type": "serial_port", "port_name": "/dev/null"}
        )
    assert isinstance(conn, ComPortConnection)


async def test_connection_manager_bad_type(hass) -> None:
    mgr = ConnectionManager(hass)
    with pytest.raises(ValueError):
        await mgr.async_get_connection({"connection_type": "bogus"})


async def test_connection_manager_close_specific(hass) -> None:
    mgr = ConnectionManager(hass)
    fake = MagicMock()
    fake.async_disconnect = AsyncMock()
    mgr._connections["k"] = fake
    await mgr.async_close_connection(fake)
    assert "k" not in mgr._connections


async def test_connection_manager_close_unknown(hass) -> None:
    mgr = ConnectionManager(hass)
    # No-op — never raises.
    await mgr.async_close_connection(MagicMock())


async def test_connection_manager_close_all_and_shutdown(hass) -> None:
    mgr = ConnectionManager(hass)
    fake = MagicMock()
    fake.async_disconnect = AsyncMock()
    mgr._connections["k"] = fake
    await mgr.async_shutdown()
    assert mgr._connections == {}
