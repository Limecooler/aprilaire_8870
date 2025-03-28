"""Data update coordinator for the Aprilaire integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
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

# Storage version for state persistence
STORAGE_VERSION = 1
# Storage key for state persistence
STORAGE_KEY = f"{DOMAIN}.state"


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
            """Initialize the coordinator."""
            self.connection = connection
            self.devices = devices or {}  # Ensure devices is always a dictionary
            self.device_manager = device_manager
            # Initialize empty data structure
            self._device_data = {}
            # Initialize coordinator data with empty dictionary
            self.data = {}  # Initialize as empty dict rather than None
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
            self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
            self._cached_state = None
            self._state_loaded = False
            
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
                device_id_str = str(device_id)
                self._device_data[device_id_str] = {"available": device.available}
                # Also initialize in the main data structure
                self.data[device_id_str] = {"available": device.available}

    async def _async_load_stored_state(self) -> None:
        """Load stored state from disk."""
        try:
            stored_state = await self._store.async_load()
            if stored_state:
                _LOGGER.debug("Loaded stored state: %s devices", 
                            len(stored_state.get("devices", {})))
                self._cached_state = stored_state
                self._state_loaded = True
                
                # Initialize data dictionary if None
                if self._device_data is None:
                    self._device_data = {}
                if self.data is None:
                    self.data = {}
                
                # Populate device data with cached state
                devices_data = stored_state.get("devices", {})
                for device_id, state in devices_data.items():
                    self._device_data[device_id] = state
                    self._device_data[device_id]["from_cache"] = True
                    # Also update main data structure
                    self.data[device_id] = state.copy()
                    self.data[device_id]["from_cache"] = True
                
                # Update listeners to reflect cached state immediately
                self.async_update_listeners()
            else:
                _LOGGER.debug("No stored state found")
        except Exception as ex:
            _LOGGER.error("Error loading stored state: %s", ex)

    async def _async_save_state(self) -> None:
        """Save current state to storage."""
        try:
            # Prepare state data
            state_data = {
                "last_updated": dt_util.utcnow().isoformat(),
                "devices": {},
            }
            
            # Add device data
            for device_id, data in self._device_data.items():
                # Don't store the "from_cache" marker
                device_data = {k: v for k, v in data.items() if k != "from_cache"}
                if device_data:
                    state_data["devices"][device_id] = device_data
            
            # Save to disk
            await self._store.async_save(state_data)
            _LOGGER.debug("Saved state to disk for %d devices", 
                        len(state_data["devices"]))
        except Exception as ex:
            _LOGGER.error("Error saving state: %s", ex)

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
            
            # Initialize device data if needed
            if self._device_data is None:
                self._device_data = {}
            if self.data is None:
                self.data = {}
                
            for device_id in self._device_data:
                self._device_data[device_id]["available"] = False
                # Also update main data structure
                if device_id in self.data:
                    self.data[device_id]["available"] = False
            
            self.async_update_listeners()

    async def _async_apply_state_to_device(self, device_id: str, state_data: Dict[str, Any]) -> None:
        """Apply stored state to a device.
        
        This is called when a device is initialized to apply stored state
        from a previous run.
        
        Args:
            device_id: The device ID
            state_data: Stored state data for this device
        """
        if self.devices is None or device_id not in self.devices:
            _LOGGER.debug("Cannot apply state to device %s - not found", device_id)
            return
            
        device = self.devices[device_id]
        
        # Apply state properties
        for key, value in state_data.items():
            if key != "from_cache" and key != "available":
                if hasattr(device, f"_state"):
                    device._state[key] = value
                    
        _LOGGER.debug("Applied stored state to device %s", device_id)

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
            
            # Ensure data dictionaries are initialized
            if self._device_data is None:
                self._device_data = {}
            if self.data is None:
                self.data = {}
                
            # Update device data
            if device_id in self._device_data:
                device = None
                if self.devices is not None:
                    device = self.devices.get(int(device_id))
                    
                if device:
                    # Use the device's method to process the COS message
                    device.process_cos_message(command, value)
                    self._device_data[device_id]["available"] = True
                    
                    # Also update main data structure
                    if device_id not in self.data:
                        self.data[device_id] = {}
                    self.data[device_id]["available"] = True
                    
                    # Remove the "from_cache" flag if present
                    if "from_cache" in self._device_data[device_id]:
                        del self._device_data[device_id]["from_cache"]
                    if device_id in self.data and "from_cache" in self.data[device_id]:
                        del self.data[device_id]["from_cache"]
                    
                    # Schedule a state save
                    self.hass.async_create_task(self._async_save_state())
                    
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
        if self.devices is None:
            _LOGGER.warning("No devices available to verify COS functionality")
            return False
            
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
                
            # Initialize data dictionary if needed
            if self._device_data is None:
                self._device_data = {}
            if self.data is None:
                self.data = {}
                
            # Update each device
            if self.devices is not None:
                for device_id, device in self.devices.items():
                    try:
                        # Poll device state
                        await device.async_update()
                        device_state = device.get_state()
                        
                        # Store device state in our data
                        device_id_str = str(device_id)
                        self._device_data[device_id_str] = device_state
                        self._device_data[device_id_str]["available"] = device.available
                        
                        # Also update main data structure
                        self.data[device_id_str] = device_state.copy()
                        self.data[device_id_str]["available"] = device.available
                        
                        # Remove the "from_cache" flag if present
                        if "from_cache" in self._device_data[device_id_str]:
                            del self._device_data[device_id_str]["from_cache"]
                        if "from_cache" in self.data[device_id_str]:
                            del self.data[device_id_str]["from_cache"]
                        
                    except Exception as ex:  # pylint: disable=broad-except
                        _LOGGER.error("Error updating device %s: %s", device_id, ex)
                        device_id_str = str(device_id)
                        if device_id_str in self._device_data:
                            self._device_data[device_id_str]["available"] = False
                        if device_id_str in self.data:
                            self.data[device_id_str]["available"] = False
            
            # Save state after updates
            await self._async_save_state()
            
            return self._device_data
            
        except Exception as ex:
            _LOGGER.error("Error updating data: %s", ex)
            raise UpdateFailed(f"Error communicating with Aprilaire devices: {ex}")

    async def async_set_heat_setpoint(self, device_id: str, temperature: float) -> None:
        """Set the heat setpoint for a device."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return
            
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_temperature(temperature, "HEAT")
            # Save state after setting temperature
            await self._async_save_state()
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_set_cool_setpoint(self, device_id: str, temperature: float) -> None:
        """Set the cool setpoint for a device."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return
            
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_temperature(temperature, "COOL")
            # Save state after setting temperature
            await self._async_save_state()
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_set_hvac_mode(self, device_id: str, mode: str) -> None:
        """Set the HVAC mode for a device."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return
            
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_hvac_mode(mode)
            # Save state after setting mode
            await self._async_save_state()
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_set_fan_mode(self, device_id: str, mode: str) -> None:
        """Set the fan mode for a device."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return
            
        device = self.devices.get(int(device_id))
        if device:
            await device.async_set_fan_mode(mode)
            # Save state after setting fan mode
            await self._async_save_state()
        else:
            _LOGGER.error("Device not found: %s", device_id)

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        # Save final state
        await self._async_save_state()
        
        # Cancel the COS processor task
        if self._cos_processor_task is not None:
            self._cos_processor_task.cancel()
            try:
                await self._cos_processor_task
            except asyncio.CancelledError:
                pass