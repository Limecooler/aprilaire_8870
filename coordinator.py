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
        devices,
        device_manager,
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
            devices: Dictionary of initialized devices
            device_manager: Device manager instance
            update_interval: Normal polling interval when COS is working
            fallback_scan_interval: Faster polling when COS is not working
            cos_verification_interval: How often to verify COS functionality
            enable_cos: Whether to enable Change of State functionality
            cos_flags: Which COS flags to enable on thermostats
        """
        self.connection = connection
        self.devices = devices
        self.device_manager = device_manager
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

        # Initialize device data
        for device_id, device in devices.items():
            self._device_data[device_id] = {"available": device.available}

    async def async_setup(self) -> None:
        """Set up the coordinator."""
        # Register connection state listener
        self.connection.register_connection_callback(self._connection_state_changed)
        
        # Start COS message processor
        self._cos_processor_task = asyncio.create_task(self._process_cos_messages())
        
        # Set up COS message listener
        self.connection.register_message_callback(self._handle_cos_message)

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
                device = self.devices.get(int(device_id))
                if device:
                    # Use the device's method to process the COS message
                    device.process_cos_message(command, value)
                    self._device_data[device_id]["available"] = True
                    
                    # Notify listeners
                    self.async_update_listeners()
                else:
                    _LOGGER.warning("Device object not found for ID: %s", device_id)
            else:
                _LOGGER.warning("Received COS message for unknown device: %s", device_id)
                
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Error parsing COS message: %s - %s", message, ex)

    async def async_verify_cos_functionality(self) -> bool:
        """Verify COS functionality is working for all devices."""
        if not self._cos_enabled:
            return False
            
        _LOGGER.debug("Verifying COS functionality for all devices")
        
        all_verified = True
        for device_id, device in self.devices.items():
            try:
                # Verify COS functionality for this device
                cos_verified = await device.async_verify_cos()
                if not cos_verified:
                    _LOGGER.warning(
                        "COS functionality verification failed for device %s", device_id
                    )
                    all_verified = False
                
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
                    await device.async_update()
                    self._device_data[str(device_id)] = device.get_state()
                    self._device_data[str(device_id)]["available"] = device.available
                except Exception as ex:  # pylint: disable=broad-except
                    _LOGGER.error("Error updating device %s: %s", device_id, ex)
                    if str(device_id) in self._device_data:
                        self._device_data[str(device_id)]["available"] = False
            
            return self._device_data
            
        except Exception as ex:
            _LOGGER.error("Error updating data: %s", ex)
            raise UpdateFailed(f"Error communicating with Aprilaire devices: {ex}")

    async def async_set_heat_setpoint(self, device_id: str, temperature: float) -> None:
        """Set the heat setpoint for a device."""
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_temperature(temperature, "HEAT")
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_set_cool_setpoint(self, device_id: str, temperature: float) -> None:
        """Set the cool setpoint for a device."""
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_temperature(temperature, "COOL")
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_set_hvac_mode(self, device_id: str, mode: str) -> None:
        """Set the HVAC mode for a device."""
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_hvac_mode(mode)
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_set_fan_mode(self, device_id: str, mode: str) -> None:
        """Set the fan mode for a device."""
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_fan_mode(mode)
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        # Cancel the COS processor task
        if self._cos_processor_task is not None:
            self._cos_processor_task.cancel()
            try:
                await self._cos_processor_task
            except asyncio.CancelledError:
                pass

