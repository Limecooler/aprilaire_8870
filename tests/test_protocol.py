"""Tests for protocol.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aprilaire_8870.protocol import (
    AprilaireProtocol,
    Command,
    CommandError,
    CommandQueue,
    CommandTimeoutError,
    InvalidCommandError,
    InvalidResponseError,
)


# ---------- Exception hierarchy ----------------------------------------------


def test_exception_hierarchy() -> None:
    assert issubclass(CommandTimeoutError, CommandError)
    assert issubclass(InvalidResponseError, CommandError)
    assert issubclass(InvalidCommandError, CommandError)


# ---------- Command -----------------------------------------------------------


def test_command_query_format() -> None:
    cmd = Command(device_id=5, command="TEMP")
    assert cmd.is_query
    assert cmd.formatted_command == "SN5 TEMP?\r"
    assert cmd.expected_response_prefix == "SN5"


def test_command_assignment_format() -> None:
    cmd = Command(device_id=2, command="MODE", value="HEAT")
    assert not cmd.is_query
    assert cmd.formatted_command == "SN2 MODE=HEAT\r"


def test_command_global_format() -> None:
    cmd = Command(device_id=None, command="ID")
    assert cmd.is_global
    assert cmd.formatted_command == "SN ID?\r"
    assert cmd.expected_response_prefix is None


def test_command_global_explicit_zero() -> None:
    cmd = Command(device_id=0, command="X")
    assert cmd.is_global


def test_command_set_response_sets_event() -> None:
    cmd = Command(device_id=1, command="X")
    assert not cmd.response_event.is_set()
    cmd.set_response("hi")
    assert cmd.response == "hi"
    assert cmd.response_event.is_set()


# ---------- AprilaireProtocol — pure parsing ---------------------------------


def test_format_command_valid(mock_protocol) -> None:
    assert mock_protocol.format_command(1, "TEMP") == "SN1 TEMP?\r"
    assert mock_protocol.format_command(1, "MODE", "HEAT") == "SN1 MODE=HEAT\r"


def test_format_command_invalid_name(mock_protocol) -> None:
    with pytest.raises(InvalidCommandError):
        mock_protocol.format_command(1, "")
    with pytest.raises(InvalidCommandError):
        mock_protocol.format_command(1, "  ")


def test_format_command_invalid_device_id(mock_protocol) -> None:
    with pytest.raises(InvalidCommandError):
        mock_protocol.format_command(0, "TEMP")
    with pytest.raises(InvalidCommandError):
        mock_protocol.format_command(65, "TEMP")


def test_is_valid_command(mock_protocol) -> None:
    assert mock_protocol.is_valid_command("TEMP")
    assert not mock_protocol.is_valid_command("")
    assert not mock_protocol.is_valid_command(None)
    assert not mock_protocol.is_valid_command("   ")


def test_parse_response_assignment(mock_protocol) -> None:
    device_id, cmd, val = mock_protocol.parse_response("SN3 TEMP=72F")
    assert device_id == 3
    assert cmd == "TEMP"
    assert val == "72F"


def test_parse_response_simple(mock_protocol) -> None:
    device_id, cmd, val = mock_protocol.parse_response("SN3KITCHEN")
    assert device_id == 3
    assert cmd == "NAME"
    assert val == "KITCHEN"


def test_parse_response_empty(mock_protocol) -> None:
    with pytest.raises(InvalidResponseError):
        mock_protocol.parse_response("")


def test_parse_response_garbage(mock_protocol) -> None:
    with pytest.raises(InvalidResponseError):
        mock_protocol.parse_response("totally not a response")


def test_is_cos_message_true(mock_protocol) -> None:
    assert mock_protocol.is_cos_message("SN1 T=72F")
    assert mock_protocol.is_cos_message("SN1 M=HEAT")


def test_is_cos_message_false(mock_protocol) -> None:
    assert not mock_protocol.is_cos_message("")
    assert not mock_protocol.is_cos_message("SN1 UNKNOWN=foo")


def test_parse_cos_message(mock_protocol) -> None:
    parsed = mock_protocol.parse_cos_message("SN5 T=72F")
    assert parsed["device_id"] == 5
    assert parsed["value"] == "72F"


def test_parse_cos_message_empty(mock_protocol) -> None:
    with pytest.raises(InvalidResponseError):
        mock_protocol.parse_cos_message("")


def test_parse_cos_message_unknown_pattern(mock_protocol) -> None:
    with pytest.raises(InvalidResponseError):
        mock_protocol.parse_cos_message("SN1 ZZZ=foo")


def test_calculate_command_timeout_device(mock_protocol) -> None:
    cmd = Command(device_id=1, command="X", timeout=5.0)
    assert mock_protocol.calculate_command_timeout(cmd) == 5.0


def test_calculate_command_timeout_global(mock_protocol) -> None:
    cmd = Command(device_id=None, command="X", timeout=1.0)
    # 64-device multiplier per const.GLOBAL_COMMAND_PROCESSING_MULTIPLIER.
    assert mock_protocol.calculate_command_timeout(cmd) == 64.0


# ---------- execute_query_command -------------------------------------------


async def test_execute_query_no_connection() -> None:
    proto = AprilaireProtocol(connection=None)
    assert await proto.execute_query_command(1, "TEMP") is None


async def test_execute_query_uses_response_path(mock_protocol, stub_connection) -> None:
    stub_connection.responses["SN1 TEMP?"] = "SN1 TEMP=72F"
    result = await mock_protocol.execute_query_command(1, "TEMP")
    assert result == "72F"


async def test_execute_query_response_no_equals(mock_protocol, stub_connection) -> None:
    stub_connection.responses["SN1 NAME?"] = "SN1KITCHEN"
    result = await mock_protocol.execute_query_command(1, "NAME")
    assert result == "SN1KITCHEN"


async def test_execute_query_no_response(mock_protocol, stub_connection) -> None:
    # No scripted response → returns None
    assert await mock_protocol.execute_query_command(1, "TEMP") is None


async def test_execute_query_fallback_path() -> None:
    """Connection without async_send_command_with_response — uses polling fallback."""
    conn = MagicMock()
    conn.async_send_command = AsyncMock(return_value=None)
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1 TEMP=72F"]])
    # Make sure hasattr() returns False for the response path.
    del conn.async_send_command_with_response
    proto = AprilaireProtocol(connection=conn)
    # Use a very short timeout to keep the test fast.
    result = await proto.execute_query_command(1, "TEMP", timeout=0.01)
    assert result == "72F"


async def test_execute_query_fallback_lenient_match() -> None:
    """Fallback path returns a response for the device even without command match."""
    conn = MagicMock()
    conn.async_send_command = AsyncMock(return_value=None)
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1 ANYTHING=val"]])
    del conn.async_send_command_with_response
    proto = AprilaireProtocol(connection=conn)
    result = await proto.execute_query_command(1, "TEMP", timeout=0.01)
    assert result == "val"


async def test_execute_query_exception_handled() -> None:
    conn = MagicMock()
    conn.async_send_command_with_response = AsyncMock(side_effect=RuntimeError("boom"))
    proto = AprilaireProtocol(connection=conn)
    assert await proto.execute_query_command(1, "TEMP") is None


# ---------- execute_assignment_command (the fixed version) -------------------


async def test_execute_assignment_uses_response_path(mock_protocol, stub_connection) -> None:
    stub_connection.responses["SN1 CR=NORMAL"] = "SN1 CR=NORMAL"
    result = await mock_protocol.execute_assignment_command(1, "CR", "NORMAL")
    assert result == "SN1 CR=NORMAL"


async def test_execute_assignment_no_connection() -> None:
    proto = AprilaireProtocol(connection=None)
    assert await proto.execute_assignment_command(1, "X", "Y") is None


async def test_execute_assignment_fallback_path() -> None:
    conn = MagicMock()
    conn.async_send_command = AsyncMock(return_value=None)
    # First call (drain) returns []; second (match) returns the right line.
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1 CR=NORMAL"]])
    del conn.async_send_command_with_response
    proto = AprilaireProtocol(connection=conn)
    result = await proto.execute_assignment_command(1, "CR", "NORMAL", timeout=0.01)
    assert result == "SN1 CR=NORMAL"


async def test_execute_assignment_fallback_no_match() -> None:
    conn = MagicMock()
    conn.async_send_command = AsyncMock(return_value=None)
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN5 X=Y"]])
    del conn.async_send_command_with_response
    proto = AprilaireProtocol(connection=conn)
    assert await proto.execute_assignment_command(1, "CR", "NORMAL", timeout=0.01) is None


async def test_execute_assignment_fallback_no_received_messages_method() -> None:
    conn = MagicMock(spec=[])  # no methods at all
    conn.async_send_command = AsyncMock(return_value=None)
    proto = AprilaireProtocol(connection=conn)
    assert await proto.execute_assignment_command(1, "CR", "NORMAL", timeout=0.01) is None


async def test_execute_assignment_exception() -> None:
    conn = MagicMock()
    conn.async_send_command_with_response = AsyncMock(side_effect=RuntimeError("boom"))
    proto = AprilaireProtocol(connection=conn)
    assert await proto.execute_assignment_command(1, "X", "Y") is None


async def test_execute_assignment_serializes_via_lock(mock_protocol, stub_connection) -> None:
    """Two concurrent assignment calls must not interleave on the wire."""
    stub_connection.responses["SN1 A=1"] = "SN1 A=1"
    stub_connection.responses["SN1 B=2"] = "SN1 B=2"
    a, b = await asyncio.gather(
        mock_protocol.execute_assignment_command(1, "A", "1"),
        mock_protocol.execute_assignment_command(1, "B", "2"),
    )
    assert a == "SN1 A=1"
    assert b == "SN1 B=2"
    assert stub_connection.sent == ["SN1 A=1", "SN1 B=2"] or stub_connection.sent == [
        "SN1 B=2",
        "SN1 A=1",
    ]


# ---------- CommandQueue ----------------------------------------------------


async def test_command_queue_add_and_process() -> None:
    send_calls: list[str] = []

    async def fake_send(cmd: str) -> None:
        send_calls.append(cmd)

    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=fake_send, protocol=proto)

    async def trigger_response() -> None:
        await asyncio.sleep(0.05)
        await queue.async_handle_response("SN1 TEMP=72F")

    trigger_task = asyncio.create_task(trigger_response())
    try:
        result = await queue.async_add_command(device_id=1, command="TEMP", timeout=1.0)
        assert result == "SN1 TEMP=72F"
        assert send_calls == ["SN1 TEMP?\r"]
    finally:
        await trigger_task
        await queue.async_stop()
        # Drain whatever background process_queue task remains.
        await asyncio.sleep(0)
        for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def test_command_queue_handle_response_no_current() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    assert await queue.async_handle_response("SN1 TEMP=72F") is False


async def test_command_queue_handle_response_invalid() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    # Set a current command but pass a garbage response.
    queue._current_command = Command(device_id=1, command="TEMP")
    assert await queue.async_handle_response("garbage") is False


async def test_command_queue_handle_response_empty() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    assert await queue.async_handle_response("") is False


async def test_command_queue_handle_response_mismatch() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    queue._current_command = Command(device_id=1, command="TEMP")
    assert await queue.async_handle_response("SN2 MODE=HEAT") is False


async def test_command_queue_handle_response_global() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    queue._current_command = Command(device_id=None, command="ID")
    assert await queue.async_handle_response("SN3 ID=abc") is True


async def test_command_queue_clear() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    cmd = Command(device_id=1, command="X")
    await queue._queue.put((0, cmd))
    await queue.async_clear_queue()
    assert queue._queue.empty()
    assert cmd.response is None


async def test_command_queue_stop_clears_queue() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    cmd = Command(device_id=1, command="X")
    await queue._queue.put((0, cmd))
    await queue.async_stop()
    assert queue._stopping is True
    assert queue._queue.empty()


async def test_command_queue_process_send_failure() -> None:
    send_calls: list[str] = []

    async def failing_send(cmd: str) -> None:
        send_calls.append(cmd)
        raise RuntimeError("send broke")

    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=failing_send, protocol=proto)
    cmd = Command(device_id=1, command="X", timeout=0.1)
    await queue._queue.put((0, cmd))
    await queue.async_process_queue()
    assert cmd.response is None
    assert send_calls == ["SN1 X?\r"]


async def test_command_queue_double_process_noop() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)
    queue._processing = True
    # Should bail immediately.
    await queue.async_process_queue()


async def test_execute_query_fallback_response_without_equals() -> None:
    conn = MagicMock()
    conn.async_send_command = AsyncMock(return_value=None)
    # Strict-match path: response starts with SN1 and contains "TEMP" but no '='.
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1 TEMP raw"]])
    del conn.async_send_command_with_response
    proto = AprilaireProtocol(connection=conn)
    result = await proto.execute_query_command(1, "TEMP", timeout=0.01)
    assert result == "SN1 TEMP raw"


async def test_execute_query_fallback_lenient_response_without_equals() -> None:
    conn = MagicMock()
    conn.async_send_command = AsyncMock(return_value=None)
    # First read returns one msg that matches device but NOT the command, so
    # the lenient loop returns it without splitting on '='.
    conn.get_received_messages = MagicMock(side_effect=[[], ["SN1 raw"]])
    del conn.async_send_command_with_response
    proto = AprilaireProtocol(connection=conn)
    result = await proto.execute_query_command(1, "TEMP", timeout=0.01)
    assert result == "SN1 raw"


async def test_command_queue_timeout() -> None:
    """async_add_command raises CommandTimeoutError when the command doesn't respond."""
    sent: list[str] = []

    async def slow_send(cmd: str) -> None:
        sent.append(cmd)

    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=slow_send, protocol=proto)
    try:
        with pytest.raises(CommandTimeoutError):
            await queue.async_add_command(device_id=1, command="X", timeout=0.05)
    finally:
        await queue.async_stop()
        await asyncio.sleep(0)
        for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def test_command_queue_clear_swallows_exception() -> None:
    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=AsyncMock(), protocol=proto)

    class ExplodingQueue:
        def __init__(self) -> None:
            self.calls = 0

        def empty(self):
            # Stay non-empty for the first iteration to trigger get(), then drain.
            return self.calls >= 1

        async def get(self):
            self.calls += 1
            raise RuntimeError("queue broke")

    queue._queue = ExplodingQueue()
    await queue.async_clear_queue()


async def test_command_queue_global_command_processing_time() -> None:
    """Cover the global-command branch of timeout math."""
    sent: list[str] = []

    async def send(cmd: str) -> None:
        sent.append(cmd)

    proto = AprilaireProtocol(connection=None)
    queue = CommandQueue(send_func=send, protocol=proto)
    cmd = Command(device_id=None, command="ID", timeout=0.01)
    await queue._queue.put((0, cmd))
    await queue.async_process_queue()
    assert sent == ["SN ID?\r"]
