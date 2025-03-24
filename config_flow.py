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


class ConnectionException(Exception):
    """Exception for connection issues."""


class AprilaireConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aprilaire 8870 Thermostat."""

    VERSION = 1
    connection_type = None
    connection_config = None
    connection = None  # Store the connection object between steps

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
                        {
                            CONNECTION_TYPE_SERIAL_SERVER: "Network Serial Server (IP address)",
                            CONNECTION_TYPE_SERIAL_PORT: "Direct Serial Connection (USB/COM port)"
                        }
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
            
            # Create connection config
            connection_config = {
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL_SERVER,
                CONF_HOST: host,
                CONF_PORT: port,
            }
            
            # Create and store connection for later use
            try:
                self.connection = self._create_connection(connection_config)
                await self.connection.async_connect()
                
                # If connection successful, proceed to discovery
                self.connection_config = connection_config
                return await self.async_step_device_discovery()
            except Exception as ex:
                _LOGGER.exception("Error connecting to serial server: %s", ex)
                if hasattr(self, "connection") and self.connection is not None:
                    await self.connection.async_disconnect()
                    self.connection = None
                errors["base"] = "cannot_connect"
        
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
            
            # Create connection config
            connection_config = {
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL_PORT,
                CONF_SERIAL_PORT: device,
                CONF_BAUDRATE: baud_rate,
            }
            
            # Create and store connection for later use
            try:
                self.connection = self._create_connection(connection_config)
                await self.connection.async_connect()
                
                # If connection successful, proceed to discovery
                self.connection_config = connection_config
                return await self.async_step_device_discovery()
            except Exception as ex:
                _LOGGER.exception("Error connecting to serial port: %s", ex)
                if hasattr(self, "connection") and self.connection is not None:
                    await self.connection.async_disconnect()
                    self.connection = None
                errors["base"] = "cannot_connect"
        
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

    async def async_step_device_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Discover thermostats using the established connection, but minimally."""
        discovered_thermostats = []
        error = None
        
        try:
            # Start the read task to receive responses
            await self.connection.async_start_reading()
            
            # Wait a moment for the read task to start
            await asyncio.sleep(0.5)
            
            # Send SN? command to discover thermostats
            _LOGGER.debug("Sending discovery command SN?")
            await self.connection.async_send_command("SN?")
            
            # Wait for responses (thermostats respond in sequence)
            # This timeout should be sufficient for several thermostats to respond
            await asyncio.sleep(3)
            
            # Get received messages
            responses = self.connection.get_received_messages()
            _LOGGER.debug("Discovery responses received: %s", responses)
            
            # Parse responses to get thermostat addresses
            if responses:
                for line in responses:
                    if line.startswith("SN"):
                        address = line[2:].strip()
                        if address.isdigit():
                            discovered_thermostats.append(int(address))
            
            # Sort addresses
            discovered_thermostats.sort()
            _LOGGER.debug("Discovered thermostats: %s", discovered_thermostats)
            
            # Get minimal info about first thermostat to confirm it's an Aprilaire 8870
            # without doing full initialization
            if discovered_thermostats:
                model_cmd = f"SN{discovered_thermostats[0]} ID?"
                _LOGGER.debug("Sending model query: %s", model_cmd)
                await self.connection.async_send_command(model_cmd)
                
                # Wait for response
                await asyncio.sleep(1)
                
                # Get response
                model_responses = self.connection.get_received_messages()
                _LOGGER.debug("Model responses received: %s", model_responses)
                
                model_response = model_responses[0] if model_responses else ""
                
                if not model_response or "8870" not in model_response:
                    _LOGGER.warning("Not an Aprilaire 8870 model: %s", model_response)
                    error = "not_aprilaire_8870"
        except Exception as ex:
            _LOGGER.exception("Error during discovery: %s", ex)
            error = "discovery_error"
        finally:
            # Stop the read task
            if hasattr(self, "connection") and self.connection is not None:
                await self.connection.async_stop_reading()
                await self.connection.async_disconnect()
                self.connection = None
        
        if error:
            return self.async_abort(reason=error)
        
        if not discovered_thermostats:
            return self.async_abort(reason="no_devices_found")
        
        # Add discovered thermostats to config WITHOUT full initialization
        self.connection_config["discovered_thermostats"] = discovered_thermostats
        
        # Set default values
        self.connection_config[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        self.connection_config[CONF_ENABLE_COS] = True
        
        # Create entry
        title = f"Aprilaire Thermostat ({len(discovered_thermostats)} devices)"
        return self.async_create_entry(title=title, data=self.connection_config)

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
        """Manage options."""
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)
        
        # Combine data and options for defaults
        combined = {**self.data, **self.options}
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TEMPERATURE_UNIT,
                        default=combined.get(CONF_TEMPERATURE_UNIT, "auto"),
                    ): vol.In(["auto", "C", "F"]),
                    vol.Required(
                        CONF_COMMAND_RETRY_COUNT,
                        default=combined.get(CONF_COMMAND_RETRY_COUNT, 3),
                    ): cv.positive_int,
                    vol.Required(
                        CONF_CONNECTION_BACKOFF_MAX,
                        default=combined.get(CONF_CONNECTION_BACKOFF_MAX, 300),
                    ): cv.positive_int,
                    vol.Required(
                        CONF_DEBUG_MODE,
                        default=combined.get(CONF_DEBUG_MODE, False),
                    ): cv.boolean,
                }
            ),
        )

