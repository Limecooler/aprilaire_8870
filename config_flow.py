"""Config flow for Aprilaire 8870 Thermostat integration."""
from __future__ import annotations

import asyncio
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    CONF_DEVICE,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONNECTION_TYPE_SERIAL_SERVER,
    CONNECTION_TYPE_SERIAL_PORT,
    CONF_CONNECTION_TYPE,
    CONF_SERIAL_PORT,
    CONF_BAUDRATE,
    DEFAULT_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_FALLBACK_SCAN_INTERVAL,
    DEFAULT_COS_VERIFICATION_INTERVAL,
    CONF_FALLBACK_SCAN_INTERVAL,
    CONF_ENABLE_COS,
    CONF_COS_FLAGS,
    CONF_COS_VERIFICATION_INTERVAL,
    CONF_TEMPERATURE_UNIT,
    CONF_COMMAND_RETRY_COUNT,
    CONF_ENABLE_COMMAND_BATCHING,
    CONF_DEBUG_MODE,
    CONF_CONNECTION_BACKOFF_MAX,
    COMMAND_TIMEOUT,
)
from .connection import (
    AprilaireConnectionBase,
    SerialServerConnection,
    ComPortConnection,
)

_LOGGER = logging.getLogger(__name__)

# Constants for the config flow
# These can be moved to const.py if they're used elsewhere in the integration
COS_FLAGS_OPTIONS = {
    "c1": "HVAC relay status changes",
    "c2": "Temperature/humidity changes",
    "c5": "Setpoint changes",
    "c7": "Mode changes",
    "c8": "Fan state changes",
    "c14": "Alarm status changes",
    "c19": "Error status changes",
}

TEMPERATURE_UNIT_OPTIONS = [
    "auto",
    "C",
    "F",
]


class ConnectionException(Exception):
    """Exception for connection issues."""


class AprilaireConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aprilaire 8870 Thermostat."""

    VERSION = 1
    connection_type = None
    connection_config = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AprilaireOptionsFlowHandler:
        """Get the options flow for this handler."""
        return AprilaireOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        return await self.async_step_connection_type()

    async def async_step_connection_type(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle connection type selection."""
        if user_input is not None:
            connection_type = user_input[CONF_TYPE]
            self.connection_type = connection_type
            
            if connection_type == CONNECTION_TYPE_SERIAL_SERVER:
                return await self.async_step_serial_server()
            elif connection_type == CONNECTION_TYPE_SERIAL_PORT:
                return await self.async_step_serial_port()
        
        return self.async_show_form(
            step_id="connection_type",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TYPE): vol.In(
                        [CONNECTION_TYPE_SERIAL_SERVER, CONNECTION_TYPE_SERIAL_PORT]
                    ),
                }
            ),
        )

    async def async_step_serial_server(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure serial server connection."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            
            # Check if we already have this combination configured
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            
            # Test connection
            connection_config = {
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL_SERVER,
                CONF_HOST: host,
                CONF_PORT: port,
            }
            
            connection_valid, connection_error = await self._async_test_connection(connection_config)
            
            if connection_valid:
                return await self.async_step_discovery(connection_config)
            else:
                errors["base"] = connection_error
        
        return self.async_show_form(
            step_id="serial_server",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )

    async def async_step_serial_port(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure serial port connection."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            device = user_input[CONF_DEVICE]
            baud_rate = user_input[CONF_BAUDRATE]
            
            # Check if we already have this combination configured
            await self.async_set_unique_id(device)
            self._abort_if_unique_id_configured()
            
            # Test connection
            connection_config = {
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL_PORT,
                CONF_SERIAL_PORT: device,
                CONF_BAUDRATE: baud_rate,
            }
            
            connection_valid, connection_error = await self._async_test_connection(connection_config)
            
            if connection_valid:
                return await self.async_step_discovery(connection_config)
            else:
                errors["base"] = connection_error
        
        return self.async_show_form(
            step_id="serial_port",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): str,
                    vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(
                        [9600, 19200]
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_discovery(
        self, connection_config: dict[str, Any]
    ) -> FlowResult:
        """Discover thermostats on the network."""
        # Create connection based on config
        connection = self._create_connection(connection_config)
        discovered_thermostats = []
        error = None
        
        try:
            # Connect to the device
            await connection.async_connect()
            
            # Start the read task to receive responses
            await connection.async_start_reading()
            
            # Wait a moment for the read task to start
            await asyncio.sleep(0.5)
            
            # Send SN? command to discover thermostats and wait for response
            _LOGGER.debug("Sending discovery command SN?")
            await connection.async_send_command("SN?")
            
            # Wait for responses to come in (thermostats respond in sequence)
            # This timeout should be sufficient for several thermostats to respond
            await asyncio.sleep(3)
            
            # Get any responses that were received
            responses = await self._get_responses_from_connection(connection)
            _LOGGER.debug("Discovery responses received: %s", responses)
            
            # Parse response to get thermostat addresses
            if responses:
                for line in responses:
                    if line.startswith("SN"):
                        address = line[2:].strip()
                        if address.isdigit():
                            discovered_thermostats.append(int(address))
            
            # Sort addresses
            discovered_thermostats.sort()
            _LOGGER.debug("Discovered thermostats: %s", discovered_thermostats)
            
            # Get info about first thermostat to confirm it's an Aprilaire 8870
            if discovered_thermostats:
                model_cmd = f"SN{discovered_thermostats[0]} ID?"
                _LOGGER.debug("Sending model query: %s", model_cmd)
                await connection.async_send_command(model_cmd)
                
                # Wait for response
                await asyncio.sleep(1)
                
                # Get response
                model_responses = await self._get_responses_from_connection(connection)
                _LOGGER.debug("Model responses received: %s", model_responses)
                
                model_response = model_responses[0] if model_responses else ""
                
                if not model_response or "8870" not in model_response:
                    _LOGGER.warning("Not an Aprilaire 8870 model: %s", model_response)
                    error = "not_aprilaire_8870"
        except Exception as ex:
            _LOGGER.exception("Error during discovery: %s", ex)
            error = str(ex)
        finally:
            # Stop the read task
            await connection.async_stop_reading()
            
            # Disconnect
            await connection.async_disconnect()
        
        if error:
            return self.async_abort(reason=error)
        
        if not discovered_thermostats:
            return self.async_abort(reason="no_thermostats_found")
        
        # Add discovered thermostats to config
        connection_config["discovered_thermostats"] = discovered_thermostats
        
        # Proceed to setting up basic config options
        return await self.async_step_basic_config(connection_config)

    async def _get_responses_from_connection(self, connection) -> list[str]:
        """Get accumulated responses from the connection."""
        # Access the buffer where responses are stored
        # This will depend on how your connection class is implemented
        if hasattr(connection, "_received_messages"):
            # If the connection has a message collection attribute
            messages = connection._received_messages.copy()
            connection._received_messages.clear()
            return messages
        elif hasattr(connection, "_buffer"):
            # If the connection stores responses in a buffer
            buffer = connection._buffer
            messages = buffer.split("\r")
            connection._buffer = ""
            return [msg for msg in messages if msg.strip()]
        else:
            # Fallback (implementation specific)
            _LOGGER.warning("Could not retrieve messages from connection")
            return []

    async def async_step_basic_config(
        self, connection_config: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set up basic configuration options."""
        if connection_config is None:
            connection_config = self.connection_config
        else:
            self.connection_config = connection_config
        
        thermostats = connection_config.get("discovered_thermostats", [])
        _LOGGER.info("Setting up configuration for %d discovered thermostats", len(thermostats))
        
        return self.async_show_form(
            step_id="basic_config",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, 
                        default=DEFAULT_SCAN_INTERVAL
                    ): cv.positive_int,
                    vol.Required(
                        CONF_ENABLE_COS, 
                        default=True
                    ): cv.boolean,
                }
            ),
        )

    async def async_step_basic_config_2(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle basic configuration input and setup."""
        if user_input is not None:
            # Merge user input with connection config
            config = {**self.connection_config, **user_input}
            
            # Create entry
            title = f"Aprilaire Thermostat ({len(config['discovered_thermostats'])} devices)"
            return self.async_create_entry(title=title, data=config)
        
        return self.async_abort(reason="unknown_error")

    def _create_connection(self, config: dict[str, Any]) -> AprilaireConnectionBase:
        """Create appropriate connection instance based on config."""
        connection_type = config[CONF_CONNECTION_TYPE]
        
        if connection_type == CONNECTION_TYPE_SERIAL_SERVER:
            return SerialServerConnection(
                self.hass,
                config,
            )
        elif connection_type == CONNECTION_TYPE_SERIAL_PORT:
            return ComPortConnection(
                self.hass,
                config,
            )
        
        raise ValueError(f"Unsupported connection type: {connection_type}")

    async def _async_test_connection(
        self, connection_config: dict[str, Any]
    ) -> tuple[bool, str]:
        """Test connection to verify it works."""
        connection = self._create_connection(connection_config)
        try:
            # Connect to the device
            await connection.async_connect()
            
            # Start the read task to receive the response
            await connection.async_start_reading()
            
            # Wait a moment for the read task to start
            await asyncio.sleep(0.5)
            
            # Send a simple command to verify communication
            await connection.async_send_command("\r")
            
            # Wait a moment for any response
            await asyncio.sleep(1)
            
            # Stop the read task
            await connection.async_stop_reading()
            
            # Success
            return True, ""
        except ConnectionException as ex:
            _LOGGER.error("Connection test failed: %s", str(ex))
            return False, "cannot_connect"
        except asyncio.TimeoutError:
            _LOGGER.error("Connection test timed out")
            return False, "timeout"
        except Exception as ex:
            _LOGGER.exception("Unexpected error testing connection: %s", str(ex))
            return False, "unknown"
        finally:
            # Always disconnect when done
            await connection.async_disconnect()


class AprilaireOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Aprilaire options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self.data = dict(config_entry.data)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage basic options."""
        if user_input is not None:
            self.options.update(user_input)
            
            if user_input.get(CONF_ENABLE_COS, True):
                return await self.async_step_cos_config()
            else:
                return await self.async_step_advanced()
        
        # Combine data and options for defaults
        combined = {**self.data, **self.options}
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=combined.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): cv.positive_int,
                    vol.Required(
                        CONF_FALLBACK_SCAN_INTERVAL,
                        default=combined.get(CONF_FALLBACK_SCAN_INTERVAL, DEFAULT_FALLBACK_SCAN_INTERVAL),
                    ): cv.positive_int,
                    vol.Required(
                        CONF_ENABLE_COS,
                        default=combined.get(CONF_ENABLE_COS, True),
                    ): cv.boolean,
                }
            ),
        )

    async def async_step_cos_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure COS settings."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_advanced()
        
        # Combine data and options for defaults
        combined = {**self.data, **self.options}
        
        current_cos_flags = combined.get(CONF_COS_FLAGS, ["c1", "c2", "c5", "c7", "c8", "c19"])
        
        return self.async_show_form(
            step_id="cos_config",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COS_FLAGS, 
                        default=current_cos_flags,
                    ): cv.multi_select(COS_FLAGS_OPTIONS),
                    vol.Required(
                        CONF_COS_VERIFICATION_INTERVAL,
                        default=combined.get(CONF_COS_VERIFICATION_INTERVAL, DEFAULT_COS_VERIFICATION_INTERVAL),
                    ): cv.positive_int,
                }
            ),
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure advanced settings."""
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)
        
        # Combine data and options for defaults
        combined = {**self.data, **self.options}
        
        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TEMPERATURE_UNIT,
                        default=combined.get(CONF_TEMPERATURE_UNIT, "auto"),
                    ): vol.In(TEMPERATURE_UNIT_OPTIONS),
                    vol.Required(
                        CONF_COMMAND_RETRY_COUNT,
                        default=combined.get(CONF_COMMAND_RETRY_COUNT, 3),
                    ): cv.positive_int,
                    vol.Required(
                        CONF_ENABLE_COMMAND_BATCHING,
                        default=combined.get(CONF_ENABLE_COMMAND_BATCHING, True),
                    ): cv.boolean,
                    vol.Required(
                        CONF_DEBUG_MODE,
                        default=combined.get(CONF_DEBUG_MODE, False),
                    ): cv.boolean,
                    vol.Required(
                        CONF_CONNECTION_BACKOFF_MAX,
                        default=combined.get(CONF_CONNECTION_BACKOFF_MAX, 300),
                    ): cv.positive_int,
                }
            ),
        )

