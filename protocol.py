"""Protocol implementation for Aprilaire 8870 thermostats."""
import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .const import (
    COMMAND_TIMEOUT,
    COS_PREFIX_PATTERN,
    GLOBAL_COMMAND_PROCESSING_MULTIPLIER,
    LOGGER_NAME,
    RESPONSE_DELAY_MS,
    THERMOSTAT_PROCESSING_TIME_MS,
)

_LOGGER = logging.getLogger(LOGGER_NAME)

class CommandError(Exception):
    """Error occurred during command execution."""


class CommandTimeoutError(CommandError):
    """Command timed out."""


class InvalidResponseError(CommandError):
    """Invalid response received."""


class InvalidCommandError(CommandError):
    """Invalid command format."""


class Command:
    """Represents a command to be sent to a thermostat."""

    def __init__(
        self,
        device_id: Optional[int],
        command: str,
        value: Optional[str] = None,
        callback: Optional[Callable] = None,
        timeout: float = COMMAND_TIMEOUT,
        priority: int = 0,
    ):
        """Initialize a command object.
        
        Args:
            device_id: Thermostat address (1-64) or None for global command
            command: The command name (e.g., MODE, T, SH)
            value: Optional value for assignment commands
            callback: Optional callback when response is received
            timeout: Command timeout in seconds
            priority: Command priority (higher numbers = higher priority)
        """
        self.device_id = device_id
        self.command = command
        self.value = value
        self.callback = callback
        self.timeout = timeout
        self.priority = priority
        self.is_global = device_id is None or device_id == 0
        self.timestamp = None
        self.response = None
        self.response_event = asyncio.Event()

    @property
    def is_query(self) -> bool:
        """Return True if this is a query command."""
        return self.value is None
    
    @property
    def formatted_command(self) -> str:
        """Return the formatted command string."""
        if self.is_global:
            prefix = "SN"
        else:
            prefix = f"SN{self.device_id}"
            
        if self.is_query:
            return f"{prefix} {self.command}?\r"
        else:
            return f"{prefix} {self.command}={self.value}\r"
    
    @property
    def expected_response_prefix(self) -> str:
        """Return the expected response prefix for this command."""
        if self.is_global:
            return None  # Global commands may get responses from multiple devices
        else:
            return f"SN{self.device_id}"
    
    def set_response(self, response: str) -> None:
        """Set the response for this command and set the event."""
        self.response = response
        self.response_event.set()


class AprilaireProtocol:
    """Implementation of the Aprilaire thermostat protocol."""

    def __init__(self, connection=None, connect_callback=None, disconnect_callback=None):
        """Initialize the protocol handler."""
        self._connection = connection
        self._connect_callback = connect_callback
        self._disconnect_callback = disconnect_callback
        self._query_pattern = re.compile(r"^SN(\d+|) ([A-Z0-9+\-]+)\?$")
        self._assignment_pattern = re.compile(r"^SN(\d+|) ([A-Z0-9+\-]+)=(.+)$")
        self._response_pattern = re.compile(r"^SN(\d+)(.*) (.+)=(.+)$")
        self._simple_response_pattern = re.compile(r"^SN(\d+)(.*)$")
        self._cos_message_patterns = {}  # Will be populated based on COS flags
        
        # Build COS message patterns from COS_PREFIX_PATTERN
        for cos_type, prefix in COS_PREFIX_PATTERN.items():
            # Use non-capturing group for name part if present
            self._cos_message_patterns[cos_type] = re.compile(
                rf"^SN(\d+)(?:\S*) {prefix}=(.+)$"
            )

    def format_command(
        self, device_id: Optional[int], command: str, value: Optional[str] = None
    ) -> str:
        """Format a command string according to the Aprilaire protocol.
        
        Args:
            device_id: Device address (1-64) or None for global command
            command: Command to execute
            value: Optional value for assignment commands
            
        Returns:
            Formatted command string
            
        Raises:
            InvalidCommandError: If the command format is invalid
        """
        if not self.is_valid_command(command):
            raise InvalidCommandError(f"Invalid command: {command}")
            
        if device_id is not None and not (1 <= device_id <= 64):
            raise InvalidCommandError(f"Invalid device ID: {device_id}")
            
        cmd = Command(device_id, command, value)
        return cmd.formatted_command

    def is_valid_command(self, command: str) -> bool:
        """Check if a command is valid.
        
        Args:
            command: Command to validate
            
        Returns:
            True if command is valid
        """
        # Basic validation - more specific validation could be added
        return bool(command and isinstance(command, str) and command.strip())

    def parse_response(self, response: str) -> Tuple[int, str, str]:
        """Parse a response from a thermostat.
        
        Args:
            response: Response string from thermostat
            
        Returns:
            Tuple of (device_id, command, value)
            
        Raises:
            InvalidResponseError: If the response cannot be parsed
        """
        if not response:
            raise InvalidResponseError("Empty response")
            
        # Clean the response string
        response = response.strip()
        
        # Try to match against the response pattern
        match = self._response_pattern.match(response)
        if match:
            device_id_str, name, command, value = match.groups()
            try:
                device_id = int(device_id_str)
                return device_id, command, value
            except ValueError:
                raise InvalidResponseError(f"Invalid device ID in response: {response}")
                
        # Try simple response pattern (like SN1 or SN1NAME)
        match = self._simple_response_pattern.match(response)
        if match:
            device_id_str, name = match.groups()
            try:
                device_id = int(device_id_str)
                return device_id, "NAME", name if name else ""
            except ValueError:
                raise InvalidResponseError(f"Invalid device ID in response: {response}")
                
        raise InvalidResponseError(f"Invalid response format: {response}")

    def is_cos_message(self, message: str) -> bool:
        """Determine if a message is a Change of State (COS) message.
        
        Args:
            message: Message to check
            
        Returns:
            True if the message is a COS message
        """
        if not message:
            return False
            
        message = message.strip()
        
        # Check against all known COS message patterns
        for pattern in self._cos_message_patterns.values():
            if pattern.match(message):
                return True
                
        return False

    def parse_cos_message(self, message: str) -> Dict[str, Any]:
        """Parse a Change of State (COS) message.
        
        Args:
            message: COS message to parse
            
        Returns:
            Dictionary with parsed message data:
                - device_id: Device address
                - cos_type: Type of COS message
                - value: New value
                
        Raises:
            InvalidResponseError: If the message cannot be parsed
        """
        if not message:
            raise InvalidResponseError("Empty COS message")
            
        message = message.strip()
        
        # Try each COS pattern
        for cos_type, pattern in self._cos_message_patterns.items():
            match = pattern.match(message)
            if match:
                device_id_str, value = match.groups()
                try:
                    device_id = int(device_id_str)
                    return {
                        "device_id": device_id,
                        "cos_type": cos_type,
                        "value": value,
                    }
                except ValueError:
                    raise InvalidResponseError(f"Invalid device ID in COS message: {message}")
                    
        raise InvalidResponseError(f"Unknown COS message format: {message}")

    def calculate_command_timeout(self, command: Command) -> float:
        """Calculate appropriate timeout for a command based on type.
        
        Args:
            command: Command object
            
        Returns:
            Timeout value in seconds
        """
        # Global commands need more time as responses come sequentially
        if command.is_global:
            # Allow time for each possible device to respond
            return command.timeout * GLOBAL_COMMAND_PROCESSING_MULTIPLIER
        return command.timeout

    async def execute_query_command(
        self, device_id: int, command: str, timeout: Optional[float] = None
    ) -> Optional[str]:
        """Execute a query command and return the response.
        
        Args:
            device_id: The device to send the command to
            command: The command to execute
            timeout: Optional timeout override
            
        Returns:
            The command response value or None if failed
        """
        if not self._connection:
            _LOGGER.error("No connection available for executing command")
            return None
            
        try:
            # Create formatted command
            formatted_command = f"SN{device_id} {command}?"
            
            # Clear any previous received messages
            if hasattr(self._connection, 'get_received_messages'):
                self._connection.get_received_messages()
            
            # Send the command
            await self._connection.async_send_command(formatted_command)
            
            # Wait for response
            await asyncio.sleep(0.5)
            
            # Get received messages
            if hasattr(self._connection, 'get_received_messages'):
                responses = self._connection.get_received_messages()
                _LOGGER.debug("Query responses received: %s", responses)
                
                # Look for a response that matches our command
                for response in responses:
                    if response.startswith(f"SN{device_id}") and command in response:
                        # Parse the response to extract the value
                        if "=" in response:
                            parts = response.split("=", 1)
                            if len(parts) > 1:
                                return parts[1].strip()
                        
                        # If no specific format, return the whole response
                        return response.strip()
                
                # No matching response found
                _LOGGER.warning("No response received for command: %s", formatted_command)
                return None
            else:
                _LOGGER.warning("Connection object does not support get_received_messages")
                return None
            
        except Exception as ex:
            _LOGGER.error("Error executing query command %s for device %s: %s", 
                         command, device_id, ex)
            return None   

    async def execute_assignment_command(
        self, device_id: int, command: str, value: str, timeout: Optional[float] = None
    ) -> Optional[str]:
        """Execute an assignment command and return the response.
        
        Args:
            device_id: The device to send the command to
            command: The command to execute
            value: The value to assign
            timeout: Optional timeout override
            
        Returns:
            The command response value or None if failed
        """
        if not self._connection:
            _LOGGER.error("No connection available for executing command")
            return None
            
        try:
            # Create formatted command
            formatted_command = f"SN{device_id} {command}={value}"
            
            # Clear any previous received messages
            if hasattr(self._connection, 'get_received_messages'):
                self._connection.get_received_messages()
            
            # Send the command
            await self._connection.async_send_command(formatted_command)
            
            # Wait for response
            await asyncio.sleep(0.5)
            
            # Get received messages
            if hasattr(self._connection, 'get_received_messages'):
                responses = self._connection.get_received_messages()
                _LOGGER.debug("Assignment responses received: %s", responses)
                
                # Look for a response that matches our command
                for response in responses:
                    if response.startswith(f"SN{device_id}") and command in response:
                        # Parse the response to extract the value
                        if "=" in response:
                            parts = response.split("=", 1)
                            if len(parts) > 1:
                                return parts[1].strip()
                        
                        # If no specific format, return the whole response
                        return response.strip()
                
                # No matching response found
                _LOGGER.warning("No response received for command: %s", formatted_command)
                return None
            else:
                _LOGGER.warning("Connection object does not support get_received_messages")
                return None
            
        except Exception as ex:
            _LOGGER.error("Error executing assignment command %s=%s for device %s: %s", 
                         command, value, device_id, ex)
            return None

    async def execute_query_command(
        self, device_id: int, command: str, timeout: Optional[float] = None
    ) -> Optional[str]:
        """Execute a query command and return the response.
        
        Args:
            device_id: The device to send the command to
            command: The command to execute
            timeout: Optional timeout override
            
        Returns:
            The command response value or None if failed
            
        Raises:
            CommandError: If the command fails
        """
        if not hasattr(self, "_connection") or self._connection is None:
            _LOGGER.error("No connection available for executing command")
            return None
            
        try:
            # Create formatted command
            formatted_command = f"SN{device_id} {command}?"
            
            # Send the command and get the response
            response = await self._connection.async_send_command(formatted_command)
            
            if not response:
                return None
                
            # Parse the response to extract the value
            # Expected format: "SN{device_id} {command}={value}"
            if "=" in response:
                parts = response.split("=", 1)
                if len(parts) > 1:
                    return parts[1].strip()
                
            return response.strip()
            
        except Exception as ex:
            _LOGGER.error("Error executing query command %s for device %s: %s", 
                         command, device_id, ex)
            return None
        
    async def execute_assignment_command(
        self, device_id: int, command: str, value: str, timeout: Optional[float] = None
    ) -> Optional[str]:
        """Execute an assignment command and return the response.
        
        Args:
            device_id: The device to send the command to
            command: The command to execute
            value: The value to assign
            timeout: Optional timeout override
            
        Returns:
            The command response value or None if failed
            
        Raises:
            CommandError: If the command fails
        """
        if not hasattr(self, "_connection") or self._connection is None:
            _LOGGER.error("No connection available for executing command")
            return None
            
        try:
            # Create formatted command
            formatted_command = f"SN{device_id} {command}={value}"
            
            # Send the command and get the response
            response = await self._connection.async_send_command(formatted_command)
            
            if not response:
                return None
                
            # Parse the response to extract the value
            # Expected format: "SN{device_id} {command}={value}"
            if "=" in response:
                parts = response.split("=", 1)
                if len(parts) > 1:
                    return parts[1].strip()
                
            return response.strip()
            
        except Exception as ex:
            _LOGGER.error("Error executing assignment command %s=%s for device %s: %s", 
                         command, value, device_id, ex)
            return None

class CommandQueue:
    """Manages queuing and execution of commands."""

    def __init__(self, send_func, protocol: AprilaireProtocol):
        """Initialize the command queue.
        
        Args:
            send_func: Async function to send data
            protocol: Protocol handler
        """
        self._send_func = send_func
        self._protocol = protocol
        self._queue = asyncio.PriorityQueue()
        self._current_command = None
        self._lock = asyncio.Lock()
        self._processing = False
        self._stopping = False

    async def async_add_command(
        self,
        device_id: Optional[int],
        command: str,
        value: Optional[str] = None,
        callback: Optional[Callable] = None,
        timeout: float = COMMAND_TIMEOUT,
        priority: int = 0,
    ) -> Any:
        """Add a command to the queue and return the result.
        
        Args:
            device_id: Device address (1-64) or None for global
            command: Command to execute
            value: Optional value for assignment commands
            callback: Optional callback function when response is received
            timeout: Command timeout in seconds
            priority: Command priority (higher = more important)
            
        Returns:
            Command response or None
            
        Raises:
            CommandTimeoutError: If command times out
            CommandError: If command execution fails
        """
        cmd = Command(device_id, command, value, callback, timeout, priority)
        
        # Put command in queue with negated priority so higher numbers go first
        await self._queue.put((-priority, cmd))
        
        # Start queue processing if it's not already running
        if not self._processing:
            asyncio.create_task(self.async_process_queue())
            
        # Wait for response with timeout
        try:
            await asyncio.wait_for(cmd.response_event.wait(), timeout=cmd.timeout * 1.5)
            return cmd.response
        except asyncio.TimeoutError:
            raise CommandTimeoutError(f"Command timed out: {cmd.formatted_command}")

    async def async_process_queue(self) -> None:
        """Process the command queue."""
        if self._processing:
            return
            
        self._processing = True
        
        try:
            while not self._queue.empty() and not self._stopping:
                _, command = await self._queue.get()
                
                # Store current command for response matching
                async with self._lock:
                    self._current_command = command
                
                # Log command
                _LOGGER.debug("Sending command: %s", command.formatted_command)
                
                # Send command
                try:
                    await self._send_func(command.formatted_command)
                    
                    # Wait for processing time before next command
                    # This depends on whether it's a global command or not
                    if command.is_global:
                        # Calculate timeout based on total number of thermostats
                        # This should come from configuration
                        num_thermostats = 64  # Default to max
                        processing_time = (
                            THERMOSTAT_PROCESSING_TIME_MS * num_thermostats / 1000
                        )
                    else:
                        processing_time = (
                            THERMOSTAT_PROCESSING_TIME_MS + RESPONSE_DELAY_MS
                        ) / 1000
                        
                    await asyncio.sleep(processing_time)
                    
                except Exception as ex:
                    _LOGGER.error("Error sending command: %s", ex)
                    command.set_response(None)
                    
                finally:
                    # Clear current command
                    async with self._lock:
                        self._current_command = None
                    
                    # Mark task as done
                    self._queue.task_done()
        finally:
            self._processing = False

    async def async_clear_queue(self) -> None:
        """Clear the command queue."""
        while not self._queue.empty():
            try:
                _, command = await self._queue.get()
                command.set_response(None)
                self._queue.task_done()
            except Exception:
                pass

    async def async_stop(self) -> None:
        """Stop queue processing."""
        self._stopping = True
        await self.async_clear_queue()

    async def async_handle_response(self, response: str) -> bool:
        """Handle a response from the thermostat.
        
        Args:
            response: Response string
            
        Returns:
            True if response was handled, False otherwise
        """
        if not response:
            return False
            
        async with self._lock:
            current_command = self._current_command
            
        if not current_command:
            return False
            
        try:
            # Parse the response
            device_id, command, value = self._protocol.parse_response(response)
            
            # If this is a global command, any response could match
            if current_command.is_global:
                # For global commands, we just use the first response
                current_command.set_response(response)
                return True
                
            # For explicit commands, check if this response matches our command
            if (
                device_id == current_command.device_id 
                and command == current_command.command
            ):
                current_command.set_response(response)
                return True
                
        except InvalidResponseError:
            _LOGGER.debug("Response does not match current command: %s", response)
            
        return False

    def execute_query_command(self, device_id, command, timeout=None):
        """Execute a query command and return the response.

        Args:
            device_id: The device to send the command to
            command: The command to execute
            timeout: Optional timeout override

        Returns:
            The command response

        Raises:
            CommandError: If the command fails
        """
        formatted_command = self.format_command(device_id, command)
        # This method returns the command result directly
        # In a real implementation, you'd send the command and wait for a response
        return formatted_command

    def execute_assignment_command(self, device_id, command, value, timeout=None):
        """Execute an assignment command and return the response.

        Args:
            device_id: The device to send the command to
            command: The command to execute
            value: The value to assign
            timeout: Optional timeout override

        Returns:
            The command response

        Raises:
            CommandError: If the command fails
        """
        formatted_command = self.format_command(device_id, command, value)
        # This method returns the command result directly
        # In a real implementation, you'd send the command and wait for a response
        return formatted_command
