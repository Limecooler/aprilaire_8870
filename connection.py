"""Connection handling for Aprilaire 8870 thermostats."""
import asyncio
import logging
from abc import ABC, abstractmethod
import async_timeout
from typing import Any, Callable, Dict, List, Optional
import time
import random

import telnetlib3
import serial_asyncio

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
            self._connection_changed_callback(value)
            
        # Notify using Home Assistant dispatcher
        async_dispatcher_send(
            self.hass, SIGNAL_CONNECTION_STATE_CHANGED, self.config, value
        )
        
    def register_message_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback for received messages."""
        self._message_callback = callback
        
    def register_connection_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback for connection state changes."""
        self._connection_changed_callback = callback
        
    def is_connected(self) -> bool:
        """Return True if the connection is established."""
        return self.state == STATE_CONNECTED
        
    @abstractmethod
    async def async_connect(self) -> bool:
        """Establish the connection."""
        pass
        
    @abstractmethod
    async def async_disconnect(self) -> None:
        """Close the connection."""
        pass
        
    @abstractmethod
    async def async_send_command(self, command: str) -> Optional[str]:
        """Send a command to the thermostat."""
        pass
        
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
        pass
        
    async def _async_process_data(self, data: str) -> None:
        """Process incoming data."""
        self._buffer += data
        
        # Split by carriage return (message delimiter)
        lines = self._buffer.split("\r")
        
        # Process complete messages (all but the last one)
        for line in lines[:-1]:
            if line.strip():
                _LOGGER.debug("Received message: %s", line)
                # Store received message
                self._received_messages.append(line)
                
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
        """Send a command to the thermostat network."""
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
                
            return data
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error reading data: %s", err)
            self.state = STATE_ERROR
            await self.async_reconnect()
            return None


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
            except asyncio.TimeoutError:
                # No data received within timeout, which is normal
                return None
                
            # Get data and clear buffer
            data = self._serial_protocol.data
            self._serial_protocol.data = b""
            
            if data:
                return data.decode("ascii", errors="replace")
            return None
            
        except Exception as err:  # pylint: disable=broad-except
            if not isinstance(err, asyncio.TimeoutError):
                _LOGGER.error("Error reading data: %s", err)
                self.state = STATE_ERROR
                await self.async_reconnect()
            return None

    class ConnectionManager:
        """Manage connections to Aprilaire thermostats."""
        
        def __init__(self, hass: HomeAssistant):
            """Initialize the connection manager."""
            self.hass = hass
            self._connections = {}
            
        async def async_get_connection(self, config: Dict[str, Any]) -> AprilaireConnectionBase:
            """Get or create a connection based on configuration."""
            # Generate connection key
            conn_type = config.get("connection_type")
            
            if conn_type == CONN_TYPE_SERIAL_SERVER:
                key = f"serial_server_{config['host']}_{config.get('port', DEFAULT_PORT)}"
            elif conn_type == CONN_TYPE_SERIAL_PORT:
                key = f"serial_port_{config['port_name']}"
            else:
                raise ValueError(f"Unsupported connection type: {conn_type}")
                
            # Return existing connection if available
            if key in self._connections:
                connection = self._connections[key]
                if not connection.is_connected():
                    await connection.async_connect()
                return connection
                
            # Create new connection
            if conn_type == CONN_TYPE_SERIAL_SERVER:
                connection = SerialServerConnection(self.hass, config)
            elif conn_type == CONN_TYPE_SERIAL_PORT:
                connection = ComPortConnection(self.hass, config)
            else:
                raise ValueError(f"Unsupported connection type: {conn_type}")
                
            # Connect
            await connection.async_connect()
            
            # Store connection
            self._connections[key] = connection
            return connection
            
        async def async_close_connection(self, connection: AprilaireConnectionBase) -> None:
            """Close a specific connection.
            
            Args:
                connection: The connection to close
            """
            # Find the connection in our dictionary
            for key, stored_connection in list(self._connections.items()):
                if stored_connection is connection:
                    await connection.async_disconnect()
                    del self._connections[key]
                    return
                    
        async def async_close_all(self) -> None:
            """Close all connections."""
            for key, connection in list(self._connections.items()):
                await connection.async_disconnect()
                del self._connections[key]
                
        async def async_shutdown(self) -> None:
            """Shutdown the connection manager."""
            await self.async_close_all()

        def get_received_messages(self) -> List[str]:
            """Get all received messages and clear the buffer."""
            messages = self._received_messages.copy()
            self._received_messages.clear()
            return messages
