"""Data update coordinator for the Aprilaire integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_FALLBACK_SCAN_INTERVAL,
    DEFAULT_COS_VERIFICATION_INTERVAL,
    COS_FLAG_HVAC_RELAYS,
    COS_FLAG_TEMPERATURE,
    COS_FLAG_SETPOINTS,
    COS_FLAG_MODE,
    COS_FLAG_FAN,
    COS_FLAG_ALARMS,
    COS_FLAG_ERRORS,
)

_LOGGER = logging.getLogger(__name__)


class AprilaireDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Aprilaire thermostat data."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
        fallback_scan_interval: int = DEFAULT_FALLBACK_SCAN_INTERVAL,
        cos_verification_interval: int = DEFAULT_COS_VERIFICATION_INTERVAL,
        enable_cos: bool = True,
        cos_flags: Set[str] = None,
    ) -> None:
        """Initialize the coordinator.
        
        Args:
            hass: Home Assistant instance
            connection: Connection to the Aprilaire network
            update_interval: Normal polling interval when COS is working
            fallback_scan_interval: Faster polling when COS is not working
            cos_verification_interval: How often to verify COS functionality
            enable_cos: Whether to enable Change of State functionality
            cos_flags: Which COS flags to enable on thermostats
        """
        self.connection = connection
        self.devices = {}
        self._device_data = {}
        self._cos_enabled = enable_cos
        self._cos_flags = cos_flags or {
            COS_FLAG_HVAC_RELAYS,
            COS_FLAG_TEMPERATURE,
            COS_FLAG_SETPOINTS,
            COS_FLAG_MODE,
            COS_FLAG_FAN,
            COS_FLAG_ALARMS,
            COS_FLAG_ERRORS,
        }
        self._fallback_scan_interval = fallback_scan_interval
        self._normal_scan_interval = update_interval
        self._cos_verification_interval = cos_verification_interval
        self._last_cos_verification = None
        self._cos_verified = False
        self._connection_state = False
        self._cos_message_queue = asyncio.Queue()
        self._cos_processor_task = None
        
        # Start with fallback interval until COS is verified
        current_update_interval = timedelta(seconds=fallback_scan_interval)
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=current_update_interval,
        )

    async def async_setup(self) -> None:
        """Set up the coordinator."""
        # Register connection state listener
        self.connection.add_state_listener(self._connection_state_changed)
        
        # Start COS message processor
        self._cos_processor_task = asyncio.create_task(self._process_cos_messages())
        
        # Set up COS message listener
        self.connection.add_message_listener(self._handle_cos_message)

    async def _process_cos_messages(self) -> None:
        """Process COS messages from the queue."""
        try:
            while True:
                message = await self._cos_message_queue.get()
                try:
                    await self._process_cos_message(message)
                except Exception as ex:  # pylint: disable=broad-except
                    _LOGGER.error("Error processing COS message: %s - %s", message, ex)
                finally:
                    self._cos_message_queue.task_done()
        except asyncio.CancelledError:
            _LOGGER.debug("COS message processor task cancelled")
            raise
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("COS message processor task failed: %s", ex)

    @callback
    def _handle_cos_message(self, message: str) -> None:
        """Handle a COS message from the connection."""
        if not self._cos_enabled:
            return
            
        # Queue the message for processing
        self._cos_message_queue.put_nowait(message)

    @callback
    def _connection_state_changed(self, connected: bool) -> None:
        """Handle connection state changes."""
        if self._connection_state == connected:
            return
            
        self._connection_state = connected
        
        if connected:
            # Connection established, schedule an immediate update
            _LOGGER.debug("Connection established, requesting immediate update")
            self.async_request_refresh()
        else:
            # Connection lost, mark devices as unavailable
            _LOGGER.debug("Connection lost, marking devices as unavailable")
            for device_id in self._device_data:
                self._device_data[device_id]["available"] = False
            
            self.async_update_listeners()

    async def _process_cos_message(self, message: str) -> None:
        """Process a COS message and update device state."""
        _LOGGER.debug("Processing COS message: %s", message)
        
        # Basic format check
        if not message.startswith("SN"):
            _LOGGER.warning("Invalid COS message format: %s", message)
            return
            
        try:
            # Extract device address and command
            parts = message.split()
            if len(parts) < 2:
                _LOGGER.warning("Invalid COS message format: %s", message)
                return
                
            # Parse the address - format is SN<address>
            device_id = parts[0][2:]
            
            # Parse the command and value
            command_parts = parts[1].split("=")
            if len(command_parts) != 2:
                _LOGGER.warning("Invalid COS command format: %s", message)
                return
                
            command = command_parts[0]
            value = command_parts[1]
            
            # Update device data
            if device_id in self._device_data:
                self._update_device_data(device_id, command, value)
                
                # Mark device as available since we received a COS message
                self._device_data[device_id]["available"] = True
                
                # Notify listeners
                self.async_update_listeners()
            else:
                _LOGGER.warning("Received COS message for unknown device: %s", device_id)
                
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Error parsing COS message: %s - %s", message, ex)

    def _update_device_data(self, device_id: str, command: str, value: str) -> None:
        """Update device data based on command and value."""
        if device_id not in self._device_data:
            self._device_data[device_id] = {"available": False}
            
        device_data = self._device_data[device_id]
        
        # Process different command types
        if command in ("T", "TEMP"):
            # Temperature update
            self._update_temperature(device_data, value)
        elif command == "HUM":
            # Humidity update
            self._update_humidity(device_data, value)
        elif command in ("HVAC", "H"):
            # HVAC status update
            self._update_hvac_status(device_data, value)
        elif command in ("MODE", "M"):
            # Mode update
            device_data["hvac_mode"] = value
        elif command in ("FAN", "F"):
            # Fan mode update
            device_data["fan_mode"] = value
        elif command == "SH":
            # Heat setpoint update
            self._update_setpoint(device_data, "heat_setpoint", value)
        elif command == "SC":
            # Cool setpoint update
            self._update_setpoint(device_data, "cool_setpoint", value)
        elif command == "ERROR":
            # Error status update
            device_data["error_status"] = value
        elif command == "HOLD":
            # Hold status update
            device_data["hold_status"] = value == "ON"
        elif command == "FLTALM":
            # Filter alarm update
            device_data["filter_alarm"] = value == "ON"
        elif command == "HOLDSTAT":
            # Detailed hold status update
            device_data["holdstat"] = value
        elif command == "RECOVSTAT":
            # Progressive recovery status update
            device_data["recovery_active"] = value == "ON"
        else:
            # Other COS messages - store the raw value
            device_data[f"raw_{command.lower()}"] = value
            
        _LOGGER.debug("Updated %s for device %s: %s", command, device_id, value)

    def _update_temperature(self, device_data: Dict[str, Any], value: str) -> None:
        """Update temperature value."""
        # Remove the temperature unit (F or C)
        temp_value = value.rstrip("FC")
        try:
            device_data["temperature"] = float(temp_value)
        except ValueError:
            _LOGGER.warning("Invalid temperature value: %s", value)

    def _update_humidity(self, device_data: Dict[str, Any], value: str) -> None:
        """Update humidity value."""
        # Remove the % sign
        hum_value = value.rstrip("%")
        try:
            device_data["humidity"] = float(hum_value)
        except ValueError:
            _LOGGER.warning("Invalid humidity value: %s", value)

    def _update_hvac_status(self, device_data: Dict[str, Any], value: str) -> None:
        """Update HVAC status and parse relay states."""
        device_data["hvac_status"] = value
        
        # Parse the HVAC status string to determine active equipment
        # Format is G±Y1±W1±Y2±W2±B±O±
        # + indicates ON, - indicates OFF
        if len(value) >= 15:  # Make sure we have enough characters
            device_data["fan_running"] = '+' in value[1:2]  # G+ means fan is running
            device_data["heating"] = '+' in value[5:6]  # W1+ means heating is running
            device_data["cooling"] = '+' in value[3:4]  # Y1+ means cooling is running
            device_data["aux_heat"] = '+' in value[7:8]  # W2+ means aux heat is running

    def _update_setpoint(self, device_data: Dict[str, Any], key: str, value: str) -> None:
        """Update temperature setpoint value."""
        # Remove the temperature unit (F or C)
        setpoint_value = value.rstrip("FC")
        try:
            device_data[key] = float(setpoint_value)
        except ValueError:
            _LOGGER.warning("Invalid setpoint value: %s", value)

    async def async_verify_cos_functionality(self) -> bool:
        """Verify COS functionality is working for all devices."""
        if not self._cos_enabled:
            return False
            
        _LOGGER.debug("Verifying COS functionality for all devices")
        
        all_verified = True
        for device_id, device in self.devices.items():
            try:
                # Check if CR (Command Response) is set to NORMAL
                cr_response = await device.async_send_command("CR?")
                if cr_response != "NORMAL":
                    _LOGGER.warning(
                        "Device %s has CR not set to NORMAL. Fixing...", device_id
                    )
                    await device.async_send_command("CR", "NORMAL")
                    
                # Check COS flags
                for flag in self._cos_flags:
                    flag_response = await device.async_send_command(f"{flag}?")
                    if flag_response != "ON":
                        _LOGGER.warning(
                            "Device %s has %s flag not enabled. Fixing...", device_id, flag
                        )
                        await device.async_send_command(flag, "ON")
                
                # Mark device as verified
                self._device_data[device_id]["cos_verified"] = True
                
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error(
                    "Failed to verify COS functionality for device %s: %s", device_id, ex
                )
                all_verified = False
                
        self._cos_verified = all_verified
        self._last_cos_verification = dt_util.utcnow()
        
        # If COS is verified for all devices, switch to normal scan interval
        if all_verified:
            _LOGGER.info("COS functionality verified for all devices")
            self.update_interval = timedelta(seconds=self._normal_scan_interval)
        else:
            _LOGGER.warning("COS functionality could not be verified for all devices")
            self.update_interval = timedelta(seconds=self._fallback_scan_interval)
            
        return all_verified

    def register_device(self, device) -> None:
        """Register a device with the coordinator."""
        self.devices[device.device_id] = device
        self._device_data[device.device_id] = {"available": False}

    def get_device_data(self, device_id: str) -> Dict[str, Any]:
        """Get the current data for a device."""
        return self._device_data.get(device_id, {})

    async def _async_update_data(self) -> Dict[str, Dict[str, Any]]:
        """Update data via polling."""
        if not self._connection_state:
            raise UpdateFailed("No connection to Aprilaire network")
            
        try:
            # Check if we need to verify COS functionality
            if (
                self._cos_enabled
                and (
                    self._last_cos_verification is None
                    or (dt_util.utcnow() - self._last_cos_verification).total_seconds()
                    > self._cos_verification_interval
                )
            ):
                await self.async_verify_cos_functionality()
                
            # Update each device
            for device_id, device in self.devices.items():
                try:
                    # Poll device state
                    await self._update_device(device)
                except Exception as ex:  # pylint: disable=broad-except
                    _LOGGER.error("Error updating device %s: %s", device_id, ex)
                    self._device_data[device_id]["available"] = False
            
            return self._device_data
            
        except Exception as ex:
            _LOGGER.error("Error updating data: %s", ex)
            raise UpdateFailed(f"Error communicating with Aprilaire devices: {ex}")

    async def _update_device(self, device) -> None:
        """Update a specific device by polling key values."""
        device_id = device.device_id
        
        try:
            # Get current temperature
            temp_response = await device.async_send_command("T?")
            self._update_device_data(device_id, "T", temp_response)
            
            # Get current mode
            mode_response = await device.async_send_command("M?")
            self._update_device_data(device_id, "M", mode_response)
            
            # Get fan mode
            fan_response = await device.async_send_command("F?")
            self._update_device_data(device_id, "F", fan_response)
            
            # Get HVAC status
            hvac_response = await device.async_send_command("HVAC?")
            self._update_device_data(device_id, "HVAC", hvac_response)
            
            # Get heat setpoint if applicable
            if mode_response in ["HEAT", "AUTO"]:
                sh_response = await device.async_send_command("SH?")
                self._update_device_data(device_id, "SH", sh_response)
                
            # Get cool setpoint if applicable
            if mode_response in ["COOL", "AUTO"]:
                sc_response = await device.async_send_command("SC?")
                self._update_device_data(device_id, "SC", sc_response)
                
            # Get error status
            error_response = await device.async_send_command("ERROR?")
            self._update_device_data(device_id, "ERROR", error_response)
            
            # Mark device as available
            self._device_data[device_id]["available"] = True
            
        except Exception as ex:
            _LOGGER.error("Error polling device %s: %s", device_id, ex)
            self._device_data[device_id]["available"] = False
            raise

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        # Cancel the COS processor task
        if self._cos_processor_task is not None:
            self._cos_processor_task.cancel()
            try:
                await self._cos_processor_task
            except asyncio.CancelledError:
                pass

