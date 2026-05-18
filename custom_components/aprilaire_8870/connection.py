"""Connection handling for Aprilaire 8870 thermostats."""
import asyncio
import logging
import re
from abc import ABC, abstractmethod
import async_timeout
from typing import Any, Callable, Dict, List, Optional, Tuple
import time
import random

import telnetlib3
import serial_asyncio_fast as serial_asyncio

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    CONN_TYPE_SERIAL_SERVER,
    CONN_TYPE_SERIAL_PORT,
    DEFAULT_PORT,
    DEFAULT_BAUDRATE,
    TIMEOUT,
    CONNECTION_BACKOFF_MAX,
    CONNECTION_BACKOFF_FACTOR,
    CONNECTION_BACKOFF_JITTER,
)

_LOGGER = logging.getLogger(__name__)

# Connection state constants
STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_ERROR = "error"

# Event signals
SIGNAL_CONNECTION_STATE_CHANGED = f"{DOMAIN}_connection_state_changed"
SIGNAL_MESSAGE_RECEIVED = f"{DOMAIN}_message_received"

# Match any prefixed thermostat response: ``SN<addr>...`` — used to figure
# out which pending request a given inbound line should resolve.
_ADDRESS_RE = re.compile(r"^SN(\d+)")

# Parse a command being sent to extract its addressed target and the request
# code, e.g. ``SN3 TEMP?`` → (3, "TEMP"), ``SN1 CR=NORMAL`` → (1, "CR").
# Case-insensitive: COS-flag codes (c1..c8) are conventionally written lowercase
# by callers even though the wire format is uppercase. v0.4.2 fix — previously
# the lowercase form fell through to the send-only branch with no response
# future, silently breaking COS verification and per-flag enable.
_COMMAND_RE = re.compile(r"^SN(\d+)\s+([A-Za-z][A-Za-z0-9]*)")


class AprilaireConnectionBase(ABC):
    """Base class for Aprilaire thermostat connections."""

    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]):
        """Initialize the connection.

        Args:
            hass: HomeAssistant instance
            config: Connection configuration
        """
        self.hass = hass
        self.config = config
        self._state = STATE_DISCONNECTED
        self._read_task = None
        self._message_callback = None
        self._connection_changed_callback = None
        self._buffer = ""
        self._received_messages = []  # Store received messages
        self._connect_error_count = 0
        # v0.3.0 future-registry: per-address single-slot future used by
        # async_send_command_with_response to await the read loop resolving
        # the matching response. Eliminates the racy "clear messages then
        # poll" pattern and makes parallel per-device commands safe.
        self._pending: Dict[int, asyncio.Future] = {}
        # Serializes the WRITE side so two coroutines can't interleave
        # bytes mid-command. The WAIT for response happens outside this
        # lock so requests to different devices can overlap on the bus.
        self._send_lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Return the current connection state."""
        return self._state
        
    @state.setter
    def state(self, value: str) -> None:
        """Set the connection state and trigger callbacks."""
        if value == self._state:
            return
            
        _LOGGER.debug("Connection state changed from %s to %s", self._state, value)
        self._state = value
        
        if self._connection_changed_callback is not None:
            _LOGGER.debug("Calling connection state change callback with state: %s", value)
            self._connection_changed_callback(value)
            
        # Notify using Home Assistant dispatcher
        _LOGGER.debug("Dispatching connection state change: %s", value)
        async_dispatcher_send(
            self.hass, SIGNAL_CONNECTION_STATE_CHANGED, self.config, value
        )
        
    def register_message_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback for received messages."""
        _LOGGER.debug("Registering message callback")
        self._message_callback = callback
        
    def register_connection_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback for connection state changes."""
        _LOGGER.debug("Registering connection state callback")
        self._connection_changed_callback = callback
        
    def is_connected(self) -> bool:
        """Return True if the connection is established."""
        return self.state == STATE_CONNECTED
        
    @abstractmethod
    async def async_connect(self) -> bool:
        """Establish the connection."""

    @abstractmethod
    async def async_disconnect(self) -> None:
        """Close the connection."""
    
    async def async_reconnect_with_backoff(self) -> bool:
        """Attempt to reconnect with backoff strategy.
        
        Returns:
            True if reconnection was successful, False otherwise
        """
        backoff_time = 1
        max_backoff = 60  # Maximum backoff time in seconds
        attempt = 1
        max_attempts = 5
        
        while attempt <= max_attempts:
            _LOGGER.debug(
                "Reconnection attempt %d with backoff time %d seconds",
                attempt,
                backoff_time
            )
            
            # Try to connect
            connected = await self.async_connect()
            if connected:
                _LOGGER.info("Successfully reconnected on attempt %d", attempt)
                return True
                
            # Wait with backoff
            await asyncio.sleep(backoff_time)
            
            # Increase backoff time for next attempt
            backoff_time = min(backoff_time * 2, max_backoff)
            attempt += 1
            
        _LOGGER.error("Failed to reconnect after %d attempts", max_attempts)
        return False
        
    @abstractmethod
    async def async_send_command(self, command: str) -> Optional[str]:
        """Send a command to the thermostat."""

    def _try_resolve_pending(self, line: str) -> None:
        """If `line` matches an in-flight request, resolve its future.

        Called from the read loop for every parsed message. Address-based
        single-slot matching: whatever response comes back first for an
        address with a pending request satisfies that request. This mirrors
        the prior fallback behavior (matched the first SN<addr>... line)
        and handles oddballs like ID? whose response code (``MODEL#``)
        doesn't match the request code (``ID``).

        Spontaneous broadcasts arriving while a request is pending will
        also resolve that request. That's an acceptable trade-off: in
        practice broadcasts are rare and command round-trips are fast
        (~50ms), so the window for collision is tiny.
        """
        match = _ADDRESS_RE.match(line)
        if not match:
            return
        try:
            address = int(match.group(1))
        except ValueError:  # pragma: no cover  (regex captures \d+)
            return
        future = self._pending.get(address)
        if future is None or future.done():
            return
        future.set_result(line)

    def _cancel_all_pending(self) -> None:
        """Cancel any in-flight request futures on disconnect/error."""
        for address, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def async_send_global_command(
        self,
        command: str,
        expected_addresses: List[int],
        timeout: float = 5.0,
    ) -> Dict[int, str]:
        """Send ``SN0 <command>`` and collect responses from every expected address.

        The 8870 protocol supports SN0 (or blank-address) as a global broadcast
        per the install manual appendix: ``SN? or SN0? will respond with all
        connected thermostats returning their address``. Same applies to reads
        and writes — one wire-level command, N responses (one per device).

        Pre-registers a future for each expected address, sends the global
        once, awaits up to ``timeout`` for each response, returns whatever
        arrived. Missing addresses are silently omitted (caller logs).

        For writes the firmware may or may not echo a per-device confirmation.
        Caller treats the result as "best effort, what came back came back".

        ``command`` should be the command body without the SN prefix —
        e.g. ``"TEMP?"`` or ``"CR=NORMAL"``.
        """
        if not self.is_connected():
            _LOGGER.debug("Cannot send global command, not connected")
            return {}
        if not expected_addresses:
            return {}

        # v0.4.5: hold _send_lock across the ENTIRE request + response
        # window, not just the write. The bulk SN0 response window is up
        # to 9 seconds (TDMA 265ms × 32 slots per the install manual);
        # previously the lock was released after the write, so a
        # concurrent task (most commonly the COS background setup's
        # per-device retries) could fire a SN<addr> write into the
        # middle of that window and collide with a response that was
        # still being TDMA-streamed back. Holding the lock for the full
        # transaction is the only way to make the bus genuinely serial.

        responses: Dict[int, str] = {}
        async with self._send_lock:
            # Pre-register one future per expected address. Any address that
            # already has a pending request (e.g. an unsolicited COS
            # broadcast that the read loop is mid-resolving) gets serialized
            # — wait for it to complete first so we don't collide on the
            # per-address slot.
            futures: Dict[int, asyncio.Future] = {}
            for address in expected_addresses:
                existing = self._pending.get(address)
                if existing is not None and not existing.done():
                    try:
                        await existing
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                f: asyncio.Future = self.hass.loop.create_future()
                self._pending[address] = f
                futures[address] = f

            try:
                try:
                    formatted = f"SN0 {command}"
                    if not formatted.endswith("\r"):
                        formatted += "\r"
                    await self.async_send_command(formatted)
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.error("Error sending global command %s: %s", command, err)
                    return {}

                # Wait for all-or-timeout. We use a single timeout window for the
                # whole batch — individual devices' jitter just consumes part of it.
                done, pending = await asyncio.wait(
                    set(futures.values()),
                    timeout=timeout,
                    return_when=asyncio.ALL_COMPLETED,
                )
                for address, future in futures.items():
                    if future in done and not future.cancelled():
                        try:
                            responses[address] = future.result()
                        except Exception:  # pragma: no cover - defensive
                            continue
                if len(responses) < len(expected_addresses):
                    _LOGGER.debug(
                        "Global %s: got %d/%d responses (missing %s)",
                        command,
                        len(responses),
                        len(expected_addresses),
                        sorted(set(expected_addresses) - set(responses)),
                    )
            finally:
                for address, future in futures.items():
                    if self._pending.get(address) is future:
                        self._pending.pop(address, None)
                    if not future.done():
                        future.cancel()
        return responses

    async def async_send_command_with_response(
        self, command: str, timeout: float = 3.0
    ) -> Optional[str]:
        """Send `command` and await the matching response line.

        Uses the per-address future registry: registers a future, sends
        the command (serialized via _send_lock), waits up to `timeout`
        seconds for the read loop to resolve it, cleans up on the way out.

        Returns the raw response line (including SN<addr> prefix and any
        location name) or None on timeout / send failure.

        For non-SN<addr>-prefixed commands (e.g. the global ``SN?`` probe
        used in discovery), falls through to send-only — the caller is
        expected to read the response itself via get_received_messages().
        """
        if not self.is_connected():
            _LOGGER.error("Cannot send command, not connected")
            return None

        match = _COMMAND_RE.match(command.strip())
        if not match:
            # Send-only: caller handles response collection (e.g. SN? probe).
            try:
                await self.async_send_command(command)
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Error sending command: %s", err)
            return None

        try:
            address = int(match.group(1))
        except ValueError:  # pragma: no cover  (regex captures \d+)
            return None

        # v0.4.5: hold _send_lock for the FULL request+response cycle.
        # Same reasoning as async_send_global_command: releasing after the
        # write lets a concurrent task (bulk SN0 poll, COS background
        # setup, service call) interleave its own write into our response
        # window. With per-address futures correlation could in theory
        # survive that, but TDMA timing on the bus can't — a concurrent
        # SN<other-addr> write physically corrupts the response stream.
        async with self._send_lock:
            # If something is already pending for THIS address (e.g. an
            # in-flight COS broadcast), wait for it to settle before we
            # claim the slot — keeps per-address future correlation clean.
            existing = self._pending.get(address)
            if existing is not None and not existing.done():
                try:
                    await existing
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            future: asyncio.Future = self.hass.loop.create_future()
            self._pending[address] = future

            try:
                try:
                    await self.async_send_command(command)
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.error("Error sending command: %s", err)
                    return None

                try:
                    return await asyncio.wait_for(future, timeout=timeout)
                except asyncio.TimeoutError:
                    _LOGGER.warning("No response received for command: %s", command.strip())
                    return None
                except asyncio.CancelledError:
                    return None
            finally:
                # Always pop our own slot — but don't clobber a different
                # future that happens to have replaced ours.
                if self._pending.get(address) is future:
                    self._pending.pop(address, None)

    async def async_start_reading(self) -> None:
        """Start the continuous reading task."""
        if self._read_task is not None and not self._read_task.done():
            _LOGGER.debug("Read task already running")
            return
            
        _LOGGER.debug("Starting read task")
        self._read_task = self.hass.loop.create_task(self._async_read_loop())
        
    async def async_stop_reading(self) -> None:
        """Stop the continuous reading task."""
        if self._read_task is not None and not self._read_task.done():
            _LOGGER.debug("Stopping read task")
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
            
    async def _async_read_loop(self) -> None:
        """Read data from the connection continuously."""
        while self.is_connected():
            try:
                data = await self._async_read_data()
                if data:
                    await self._async_process_data(data)
            except asyncio.CancelledError:
                # Task cancelled, stop reading
                break
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Error reading data: %s", err)
                self.state = STATE_ERROR
                await self.async_reconnect()
                
    @abstractmethod
    async def _async_read_data(self) -> Optional[str]:
        """Read data from the connection."""
                   
    async def _async_process_data(self, data: Any) -> None:
        """Process incoming data with improved binary handling."""
        # Convert binary data to string if needed
        if isinstance(data, bytes):
            try:
                # More robust decoding
                data_str = ""
                for byte in data:
                    if 32 <= byte <= 126:  # Printable ASCII range
                        data_str += chr(byte)
                    elif byte in (10, 13):  # Line feed or carriage return
                        data_str += '\r'
                    # Ignore other non-printable bytes
                data = data_str
            except UnicodeDecodeError:  # pragma: no cover  (per-byte loop above can't raise)
                _LOGGER.warning("Unable to decode binary data to ASCII")
                return
        
        # Ensure data is a string
        if not isinstance(data, str):
            try:
                data = str(data)
            except Exception:
                _LOGGER.warning("Unable to convert data to string: %s", type(data))
                return
                
        self._buffer += data
        # Cap the buffer so a stuck connection can't grow it unboundedly.
        if len(self._buffer) > 4096:
            self._buffer = self._buffer[-4096:]

        # Split by carriage return (message delimiter)
        lines = self._buffer.split("\r")
        
        # Process complete messages (all but the last one)
        for line in lines[:-1]:
            line = line.strip()
            if line:
                # Additional filtering for thermostat responses
                # Look for SN followed by numbers, which is the expected format
                if line.startswith("SN") and any(c.isdigit() for c in line):
                    _LOGGER.debug("Received message: %s", line)
                    # Store received message
                    self._received_messages.append(line)

                    # Resolve any in-flight request awaiting this address.
                    self._try_resolve_pending(line)

                    if self._message_callback is not None:
                        self._message_callback(line)

                    # Notify using Home Assistant dispatcher
                    async_dispatcher_send(
                        self.hass, SIGNAL_MESSAGE_RECEIVED, self.config, line
                    )
        
        # Keep the last incomplete message (if any) in the buffer
        self._buffer = lines[-1]

    async def async_reconnect(self) -> bool:
        """Attempt to reconnect with backoff."""
        await self.async_disconnect()
        
        # Calculate backoff delay
        delay = self._calculate_backoff_delay()
        
        _LOGGER.info(
            "Waiting %.1f seconds before reconnection attempt %d",
            delay,
            self._connect_error_count + 1,
        )
        
        # Wait for the backoff period
        await asyncio.sleep(delay)
        
        # Attempt reconnection
        return await self.async_connect()
        
    def _calculate_backoff_delay(self) -> float:
        """Calculate the backoff delay with jitter."""
        # Exponential backoff with jitter
        base_delay = min(
            CONNECTION_BACKOFF_MAX,
            CONNECTION_BACKOFF_FACTOR ** self._connect_error_count,
        )
        jitter = random.uniform(
            -CONNECTION_BACKOFF_JITTER * base_delay,
            CONNECTION_BACKOFF_JITTER * base_delay,
        )
        return base_delay + jitter

    def get_received_messages(self) -> List[str]:
        """Get all received messages and clear the buffer."""
        messages = self._received_messages.copy()
        self._received_messages.clear()
        return messages


class SerialServerConnection(AprilaireConnectionBase):
    """Connection to Aprilaire thermostats through a serial server."""
    
    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]):
        """Initialize the serial server connection.
        
        Args:
            hass: HomeAssistant instance
            config: Connection configuration including host and port
        """
        super().__init__(hass, config)
        self._host = config["host"]
        self._port = config.get("port", DEFAULT_PORT)
        self._reader = None
        self._writer = None

    def _safe_decode(self, data: bytes) -> str:
        """Safely decode binary data to string, filtering out problematic bytes."""
        if not data:
            return ""
            
        # Convert to string, ignoring problematic characters
        try:
            # First try standard decoding
            return data.decode("ascii", errors="replace")
        except UnicodeDecodeError:  # pragma: no cover  (errors='replace' never raises)
            # Fall back to manual character filtering
            result = ""
            for byte in data:
                if 32 <= byte <= 126 or byte in (10, 13):  # Printable ASCII or newline/CR
                    result += chr(byte)
                else:
                    result += "?"  # Replace non-printable bytes
            return result

    async def async_connect(self) -> bool:
        """Establish connection to the serial server."""
        if self.is_connected():
            return True
            
        self.state = STATE_CONNECTING
        
        try:
            _LOGGER.debug(
                "Connecting to serial server at %s:%s", self._host, self._port
            )
            
            async with async_timeout.timeout(TIMEOUT):
                # Create telnet connection using telnetlib3
                self._reader, self._writer = await telnetlib3.open_connection(
                    self._host, self._port
                )
                
            self.state = STATE_CONNECTED
            self._connect_error_count = 0
            _LOGGER.info("Connected to serial server at %s:%s", self._host, self._port)
            
            return True
            
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Timeout connecting to serial server at %s:%s", self._host, self._port
            )
            self.state = STATE_ERROR
            self._connect_error_count += 1
            return False
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error(
                "Error connecting to serial server at %s:%s: %s",
                self._host,
                self._port,
                err,
            )
            self.state = STATE_ERROR
            self._connect_error_count += 1
            return False
            
    async def async_disconnect(self) -> None:
        """Close the connection to the serial server."""
        await self.async_stop_reading()
        # Unblock any caller still waiting on an in-flight request.
        self._cancel_all_pending()

        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Error closing telnet connection: %s", err)

            self._reader = None
            self._writer = None

        self.state = STATE_DISCONNECTED
        _LOGGER.debug("Disconnected from serial server")
    
    async def async_send_command(self, command: str) -> None:
        """Send a command with improved error handling."""
        if not self.is_connected():
            _LOGGER.error("Cannot send command, not connected")
            raise HomeAssistantError("Not connected to serial server")

        try:
            # Ensure command ends with carriage return
            if not command.endswith("\r"):
                command += "\r"

            _LOGGER.debug("Sending command: %s", command.strip())
            self._writer.write(command)
            await self._writer.drain()

            # Small delay after sending to avoid overwhelming the connection
            await asyncio.sleep(0.1)

        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error sending command: %s", err)
            self.state = STATE_ERROR
            await self.async_reconnect()
            raise HomeAssistantError(f"Failed to send command: {err}")
    
    async def _async_read_data(self) -> Optional[str]:
        """Read data from the telnet connection."""
        if not self.is_connected() or self._reader is None:
            return None
            
        try:
            data = await self._reader.read(1024)
            if not data:
                _LOGGER.warning("Connection closed by remote host")
                self.state = STATE_ERROR
                await self.async_reconnect()
                return None
            
            # Handle binary data properly
            _LOGGER.debug("Raw data received: %r", data)
            return data
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error reading data: %s", err)
            self.state = STATE_ERROR
            await self.async_reconnect()
            return None

    # async_send_command_with_response now lives on AprilaireConnectionBase.


class SerialProtocol(asyncio.Protocol):
    """Serial protocol for reading and writing."""
    
    def __init__(self, conn):
        """Initialize with a reference to the connection object."""
        self.conn = conn
        self.transport = None
        self.data = b""
        
    def connection_made(self, transport):
        """Store transport when connection is established."""
        self.transport = transport
        
    def data_received(self, data):
        """Store received data."""
        self.data += data
        if self.conn._read_event.is_set():
            self.conn._read_event.clear()
            
    def connection_lost(self, exc):
        """Handle connection lost."""
        _LOGGER.error("Serial connection lost: %s", exc)
        self.conn.state = STATE_ERROR
        asyncio.create_task(self.conn.async_reconnect())


class ComPortConnection(AprilaireConnectionBase):
    """Connection to Aprilaire thermostats through a direct COM port."""
    
    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]):
        """Initialize the COM port connection.
        
        Args:
            hass: HomeAssistant instance
            config: Connection configuration including port_name and baud_rate
        """
        super().__init__(hass, config)
        self._port_name = config["port_name"]
        self._baud_rate = config.get("baud_rate", DEFAULT_BAUDRATE)
        self._serial_transport = None
        self._serial_protocol = None
        self._read_event = asyncio.Event()
        
    def _safe_decode(self, data: bytes) -> str:
        """Safely decode binary data to string, filtering out problematic bytes."""
        if not data:
            return ""
            
        # Convert to string, ignoring problematic characters
        try:
            # First try standard decoding
            return data.decode("ascii", errors="replace")
        except UnicodeDecodeError:  # pragma: no cover  (errors='replace' never raises)
            # Fall back to manual character filtering
            result = ""
            for byte in data:
                if 32 <= byte <= 126 or byte in (10, 13):  # Printable ASCII or newline/CR
                    result += chr(byte)
                else:
                    result += "?"  # Replace non-printable bytes
            return result

    async def async_connect(self) -> bool:
        """Establish connection to the COM port."""
        if self.is_connected():
            return True
            
        self.state = STATE_CONNECTING
        
        try:
            _LOGGER.debug(
                "Connecting to COM port %s at %s baud",
                self._port_name,
                self._baud_rate,
            )
            
            # Create serial connection
            self._serial_transport, self._serial_protocol = await serial_asyncio.create_serial_connection(
                self.hass.loop,
                lambda: SerialProtocol(self),
                self._port_name,
                baudrate=self._baud_rate,
                bytesize=serial_asyncio.serial.EIGHTBITS,
                parity=serial_asyncio.serial.PARITY_NONE,
                stopbits=serial_asyncio.serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            
            self.state = STATE_CONNECTED
            self._connect_error_count = 0
            _LOGGER.info(
                "Connected to COM port %s at %s baud", self._port_name, self._baud_rate
            )
            
            return True
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error(
                "Error connecting to COM port %s: %s", self._port_name, err
            )
            self.state = STATE_ERROR
            self._connect_error_count += 1
            return False
            
    async def async_disconnect(self) -> None:
        """Close the connection to the COM port."""
        await self.async_stop_reading()
        # Unblock any caller still waiting on an in-flight request.
        self._cancel_all_pending()

        if self._serial_transport is not None:
            self._serial_transport.close()
            self._serial_transport = None
            self._serial_protocol = None

        self.state = STATE_DISCONNECTED
        _LOGGER.debug("Disconnected from COM port")
        
    async def async_send_command(self, command: str) -> None:
        """Send a command to the thermostat network."""
        if not self.is_connected() or self._serial_transport is None:
            _LOGGER.error("Cannot send command, not connected")
            raise HomeAssistantError("Not connected to COM port")
            
        try:
            # Ensure command ends with carriage return
            if not command.endswith("\r"):
                command += "\r"
                
            _LOGGER.debug("Sending command: %s", command.strip())
            self._serial_transport.write(command.encode("ascii"))
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error sending command: %s", err)
            self.state = STATE_ERROR
            await self.async_reconnect()
            raise HomeAssistantError(f"Failed to send command: {err}")
            
    async def _async_read_data(self) -> Optional[str]:
        """Read data from the serial connection."""
        if not self.is_connected() or self._serial_protocol is None:
            return None
            
        try:
            # Wait for data with timeout
            self._read_event.set()
            try:
                async with async_timeout.timeout(0.1):
                    await self._read_event.wait()
            except asyncio.TimeoutError:  # pragma: no cover  (event is set just above)
                # No data received within timeout, which is normal
                return None
                
            # Get data and clear buffer
            data = self._serial_protocol.data
            self._serial_protocol.data = b""
            
            if data:
                # We'll handle the binary-to-string conversion in _async_process_data
                return data
            return None
            
        except Exception as err:  # pylint: disable=broad-except
            if not isinstance(err, asyncio.TimeoutError):
                _LOGGER.error("Error reading data: %s", err)
                self.state = STATE_ERROR
                await self.async_reconnect()
            return None



class ConnectionManager:
    """Manage Aprilaire bus connections, keyed by connection type and identity."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._connections: Dict[str, AprilaireConnectionBase] = {}

    async def async_get_connection(self, config: Dict[str, Any]) -> AprilaireConnectionBase:
        """Get or create a connection based on configuration."""
        conn_type = config.get("connection_type")
        if conn_type == CONN_TYPE_SERIAL_SERVER:
            key = f"serial_server_{config['host']}_{config.get('port', DEFAULT_PORT)}"
        elif conn_type == CONN_TYPE_SERIAL_PORT:
            key = f"serial_port_{config['port_name']}"
        else:
            raise ValueError(f"Unsupported connection type: {conn_type}")

        if key in self._connections:
            connection = self._connections[key]
            if not connection.is_connected():
                await connection.async_connect()
            return connection

        if conn_type == CONN_TYPE_SERIAL_SERVER:
            connection = SerialServerConnection(self.hass, config)
        else:
            connection = ComPortConnection(self.hass, config)
        await connection.async_connect()
        self._connections[key] = connection
        return connection

    async def async_close_connection(self, connection: AprilaireConnectionBase) -> None:
        for key, stored in list(self._connections.items()):
            if stored is connection:
                await connection.async_disconnect()
                del self._connections[key]
                return

    async def async_close_all(self) -> None:
        for key, connection in list(self._connections.items()):
            await connection.async_disconnect()
            del self._connections[key]

    async def async_shutdown(self) -> None:
        await self.async_close_all()
