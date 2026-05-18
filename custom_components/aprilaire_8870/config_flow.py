"""Config flow for Aprilaire 8870 Thermostat integration."""
from __future__ import annotations

import asyncio
import logging
import re
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
    CONF_MONITOR_ALARMS,
    CONF_MONITOR_HUMIDITY,
    CONF_MONITOR_OUTDOOR_TEMP,
    DEFAULT_MONITOR_ALARMS,
    DEFAULT_MONITOR_HUMIDITY,
    DEFAULT_MONITOR_OUTDOOR_TEMP,
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


def _parse_location_name(address: int, responses: list[str]) -> str | None:
    """Extract a location name from response prefixes for a given address.

    Aprilaire thermostats with a location name configured echo it back in the
    response prefix, e.g. ``SN1Master Bedroom ID=8870`` instead of just
    ``SN1 ID=8870``. This helper returns the trimmed name if present, or None.

    Boundary char between the name and the response code can be ``=`` for
    most commands (TEMP, MODE, HVAC, …) or ``#`` for ID responses
    (``MODEL# 8870 REV: V1.2 - RPC 2002``). Single-letter codes (T=, M=, F=)
    are also accepted since the firmware uppercases the response code
    regardless of the request case.
    """
    if not responses:
        return None
    pattern = re.compile(
        rf"^SN{address}\s*([A-Za-z0-9 ]*?)\s*[A-Z][A-Z0-9]*[=#]"
    )
    for line in responses:
        if not isinstance(line, str):
            continue
        match = pattern.match(line)
        if not match:
            continue
        name = match.group(1).strip()
        if name:
            return name
    return None


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
            
            # v0.4.0: bulk ID? via SN0 instead of N per-device loops.
            # The 8870 protocol uses TDMA slot timing — every connected
            # thermostat replies in its own address-ordered slot. Per the
            # programmer's manual, max wait is 265ms × N where N is the
            # device's "Number of Thermostats on Network" setting (default
            # 32). We use 10s to be safe even on devices still at default.
            device_names: dict[str, str] = {}
            id_responses_per_address: dict[int, str] = {}
            if discovered_thermostats:
                try:
                    bulk_responses = await self.connection.async_send_global_command(
                        "ID?",
                        expected_addresses=discovered_thermostats,
                        timeout=10.0,
                    )
                    id_responses_per_address.update(bulk_responses)
                    _LOGGER.debug(
                        "Bulk ID? returned %d/%d responses",
                        len(bulk_responses), len(discovered_thermostats),
                    )
                except Exception as bulk_ex:
                    _LOGGER.debug("Bulk ID? failed, falling back to per-device: %s", bulk_ex)

                # Per-device fallback for any addresses the bulk missed.
                missing = [a for a in discovered_thermostats if a not in id_responses_per_address]
                for address in missing:
                    await self.connection.async_send_command(f"SN{address} ID?")
                    await asyncio.sleep(1)
                    for line in self.connection.get_received_messages():
                        m = re.match(rf"^SN{address}", line)
                        if m:
                            id_responses_per_address[address] = line
                            break

                # First-device 8870 verification using whatever ID response
                # we got for the lowest address.
                first_addr = discovered_thermostats[0]
                first_response = id_responses_per_address.get(first_addr)
                if not first_response or "8870" not in first_response:
                    _LOGGER.warning(
                        "Not an Aprilaire 8870 model at address %s: %s",
                        first_addr, first_response,
                    )
                    error = "not_aprilaire_8870"

                # Extract location names from whatever we collected.
                if not error:
                    for address, response in id_responses_per_address.items():
                        name = _parse_location_name(address, [response])
                        if name:
                            device_names[str(address)] = name
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
        self.connection_config["device_names"] = device_names

        # Set default values
        self.connection_config[CONF_SCAN_INTERVAL] = DEFAULT_UPDATE_INTERVAL
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
                    # Per-poll-cycle traffic toggles. Alarms default off because
                    # most firmwares NACK them and broadcasts cover real
                    # transitions; humidity & outdoor temp default on.
                    vol.Required(
                        CONF_MONITOR_ALARMS,
                        default=combined.get(CONF_MONITOR_ALARMS, DEFAULT_MONITOR_ALARMS),
                    ): cv.boolean,
                    vol.Required(
                        CONF_MONITOR_HUMIDITY,
                        default=combined.get(CONF_MONITOR_HUMIDITY, DEFAULT_MONITOR_HUMIDITY),
                    ): cv.boolean,
                    vol.Required(
                        CONF_MONITOR_OUTDOOR_TEMP,
                        default=combined.get(CONF_MONITOR_OUTDOOR_TEMP, DEFAULT_MONITOR_OUTDOOR_TEMP),
                    ): cv.boolean,
                }
            ),
        )

