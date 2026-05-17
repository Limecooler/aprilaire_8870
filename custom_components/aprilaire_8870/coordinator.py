"""Data update coordinator for the Aprilaire integration."""
from __future__ import annotations

import asyncio
import logging
import re
import traceback
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .connection import SIGNAL_MESSAGE_RECEIVED
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
    RESPONSE_CODE_TO_COMMAND,
    SIGNAL_CONNECTION_STATE_CHANGED,
)

# Parses any prefixed thermostat response: ``SN<addr>[<name with spaces>]
#  <CMD>=<value>``. Group 1 = address (digits), group 2 = response code
# (TEMP shows as "T", MODE as "M", FAN as "F" — these are mapped via
# RESPONSE_CODE_TO_COMMAND), group 3 = value.
_UNSOLICITED_RE = re.compile(r"^SN(\d+).*?\s+([A-Z][A-Z0-9]*)=(.*)$")

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
        # Start with fallback interval until COS is verified
        current_update_interval = timedelta(seconds=fallback_scan_interval)
        
        # Call parent init first
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=current_update_interval,
        )
        
        # Now do our own initialization
        self.connection = connection
        self.devices = devices or {}  # Ensure devices is always a dictionary
        self.device_manager = device_manager
        
        # Initialize our data structures
        # If self.data wasn't initialized by parent, initialize it now
        if self.data is None:
            self.data = {}
            
        self._device_data = {}
        
        # Initialize device data
        for device_id, device in devices.items():
            device_id_str = str(device_id)
            self._device_data[device_id_str] = {"available": device.available}
            # Also initialize in the main data structure
            self.data[device_id_str] = {"available": device.available}
        
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
        self._poll_backstop = fallback_scan_interval
        self._poll_healthy = update_interval
        self._cos_verification_interval = cos_verification_interval
        self._last_cos_verification = None
        self._cos_verified = False
        self._connection_state = False
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._cached_state = None
        self._state_loaded = False

        # Register for connection state change events
        self._connection_state_unsub = async_dispatcher_connect(
            hass, SIGNAL_CONNECTION_STATE_CHANGED, self._handle_connection_state_change
        )

        # Listen for any message that arrives on the bus — these come in
        # for two reasons: (a) responses to our own polls, and (b)
        # unsolicited COS-style broadcasts when someone touches the
        # thermostat directly. We re-route both into device state so the
        # UI updates in real time, regardless of whether the COS flag
        # subscription was accepted by the device.
        self._message_unsub = async_dispatcher_connect(
            hass, SIGNAL_MESSAGE_RECEIVED, self._handle_bus_message
        )

        # Explicitly register callback with the connection
        if connection is not None:
            _LOGGER.debug("Registering connection callback with the connection object")
            connection.register_connection_callback(self._connection_state_changed)
            # Initialize connection state from current connection status
            self._connection_state = connection.is_connected()
            _LOGGER.debug("Initial connection state from connection object: %s", self._connection_state)

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

    @callback
    def _handle_connection_state_change(self, config, state: str) -> None:
        """Handle connection state changes from dispatcher."""
        _LOGGER.debug("Received connection state change from dispatcher: %s", state)
        is_connected = (state == "connected")
        self._connection_state_changed(is_connected)

    @callback
    def _connection_state_changed(self, connected: bool) -> None:
        """Handle connection state changes."""
        if self._connection_state == connected:
            return
            
        self._connection_state = connected
        
        if connected:
            # Connection re-established. The next scheduled update will refresh
            # state; we don't kick off an extra refresh here to avoid racing the
            # entry-setup / unload paths that toggle connection state.
            _LOGGER.debug("Connection established — awaiting next scheduled refresh")
            # Reset per-device unsupported-command tracking — a firmware update
            # that happened over the bus reset could add support we'd otherwise
            # never re-discover.
            for device in (self.devices or {}).values():
                reset = getattr(device, "reset_unsupported_commands", None)
                if callable(reset):
                    reset()
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

    async def async_verify_cos_functionality(self) -> bool:
        """Verify COS functionality per-device.

        Returns True if a majority of devices have COS working — in that case
        we switch to the long-poll interval and trust COS broadcasts to keep
        state fresh for everyone. If most devices lack COS, we stay on the
        fallback (more frequent) polling interval but log per-device so the
        problem stays visible without spamming a single noisy WARNING.
        """
        if not self._cos_enabled:
            return False

        _LOGGER.debug("Verifying COS functionality for all devices")

        if self.devices is None:
            _LOGGER.warning("No devices available to verify COS functionality")
            return False

        total = 0
        verified = 0
        for device_id, device in self.devices.items():
            total += 1
            try:
                if await device.async_verify_cos():
                    verified += 1
                else:
                    _LOGGER.info(
                        "COS not active on device %s — will continue polling it",
                        device_id,
                    )
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error(
                    "Failed to verify COS functionality for device %s: %s", device_id, ex
                )

        self._last_cos_verification = dt_util.utcnow()
        # "Mostly verified" = at least half of the devices broadcast COS.
        cos_healthy = total > 0 and verified * 2 >= total
        self._cos_verified = cos_healthy

        if cos_healthy:
            _LOGGER.info(
                "COS active on %d/%d devices — using normal scan interval", verified, total
            )
            self.update_interval = timedelta(seconds=self._poll_healthy)
        else:
            _LOGGER.warning(
                "COS active on only %d/%d devices — staying on fallback scan interval",
                verified, total,
            )
            self.update_interval = timedelta(seconds=self._poll_backstop)

        return cos_healthy

    async def _async_update_data(self) -> Dict[str, Dict[str, Any]]:
        """Update data via polling with improved error handling."""
        _LOGGER.debug("Beginning data update")
        if not self._connection_state:
            _LOGGER.error("No connection to Aprilaire network")
            raise UpdateFailed("No connection to Aprilaire network")
            
        # Check if connection object agrees with our state
        if self.connection and not self.connection.is_connected():
            _LOGGER.warning("Connection state mismatch! Coordinator thinks connected but connection reports disconnected")
            self._connection_state = False
            raise UpdateFailed("Connection state mismatch")
            
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
                try:
                    _LOGGER.debug("Verifying COS functionality")
                    await self.async_verify_cos_functionality()
                except Exception as cos_ex:
                    _LOGGER.error("Error verifying COS functionality: %s", cos_ex)
                    _LOGGER.error("Traceback: %s", traceback.format_exc())
                    
            # Initialize data dictionary if needed
            if self._device_data is None:
                _LOGGER.debug("Initializing device_data dictionary")
                self._device_data = {}
            if self.data is None:
                _LOGGER.debug("Initializing data dictionary")
                self.data = {}
                
            # Update each device. Pace requests so we don't crowd the RS-485 bus
            # — every command needs ~265ms processing time on the thermostat,
            # and back-to-back polls starve higher-addressed devices first.
            from .const import THERMOSTAT_PROCESSING_TIME_MS
            inter_device_delay = THERMOSTAT_PROCESSING_TIME_MS / 1000

            if self.devices is not None:
                for idx, (device_id, device) in enumerate(self.devices.items()):
                    if idx > 0:
                        await asyncio.sleep(inter_device_delay)
                    _LOGGER.debug("Updating device %s", device_id)
                    try:
                        # Poll device state
                        await device.async_update()
                        
                        try:
                            device_state = device.get_state()
                            
                            # Store device state in our data
                            device_id_str = str(device_id)
                            if device_state is not None:
                                _LOGGER.debug("Updated state for device %s: %s", device_id, device_state)
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
                            else:
                                _LOGGER.warning("Device %s returned None state", device_id)
                        except Exception as state_ex:
                            _LOGGER.error("Error getting device state for device %s: %s", device_id, state_ex)
                            _LOGGER.error("Traceback: %s", traceback.format_exc())
                            device_id_str = str(device_id)
                            if device_id_str in self._device_data:
                                self._device_data[device_id_str]["available"] = False
                            if device_id_str in self.data:
                                self.data[device_id_str]["available"] = False
                            
                    except Exception as ex:
                        _LOGGER.error("Error updating device %s: %s", device_id, ex)
                        _LOGGER.error("Traceback: %s", traceback.format_exc())
                        device_id_str = str(device_id)
                        if device_id_str in self._device_data:
                            self._device_data[device_id_str]["available"] = False
                        if device_id_str in self.data:
                            self.data[device_id_str]["available"] = False
            else:
                _LOGGER.warning("No devices available to update")
            
            # Save state after updates
            try:
                _LOGGER.debug("Saving state after update")
                await self._async_save_state()
            except Exception as save_ex:
                _LOGGER.error("Error saving state: %s", save_ex)
                _LOGGER.error("Traceback: %s", traceback.format_exc())
            
            _LOGGER.debug("Data update completed successfully")
            return self._device_data
            
        except Exception as ex:
            _LOGGER.error("Error updating data: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
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

    @callback
    def _handle_bus_message(self, config: Any, line: str) -> None:
        """Decode any inbound thermostat message into device state.

        Fired by the connection's read loop for every parsed message.
        Solicited (response to one of our polls) and unsolicited
        (broadcast from a user-side change) messages share the same
        wire format, so a single handler suffices. State updates are
        idempotent — if the poller also handles the same response we
        just write the same value twice, no harm done.

        Only messages whose ``config`` matches the connection this
        coordinator owns are processed (lets multiple aprilaire entries
        coexist without crosstalk).
        """
        if not line or not isinstance(line, str):
            return
        if self.connection is not None and config is not getattr(
            self.connection, "config", config
        ):
            # Different connection — ignore.
            return
        match = _UNSOLICITED_RE.match(line)
        if not match:
            return
        address_str, code, value = match.groups()
        try:
            address = int(address_str)
        except ValueError:
            return
        device = self.devices.get(address)
        if device is None:
            return
        command = RESPONSE_CODE_TO_COMMAND.get(code)
        if command is None:
            return
        # Reconstruct a "<CMD>=<value>" string for _process_state_response,
        # which splits on the first "=" and uses everything before/after.
        try:
            device._process_state_response(command, f"{command}={value}")
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug(
                "Error applying unsolicited %s update for thermostat %s: %s",
                command, address, err,
            )
            return

        # Sync the coordinator's data view so subscribed entities pick
        # up the change on the next listener notification.
        device_id_str = str(address)
        try:
            new_state = device.get_state()
        except Exception:  # pragma: no cover - defensive
            return
        if new_state is None:
            return
        if self.data is None:
            self.data = {}
        existing = self.data.get(device_id_str) or {}
        merged = {**existing, **new_state, "available": device.available}
        merged.pop("from_cache", None)
        # Only notify listeners when something actually changed — avoids
        # spamming entity state writes when the unsolicited message just
        # echoes the value we already had.
        if merged == existing:
            return
        self.data[device_id_str] = merged
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        # Unsubscribe from dispatcher connections
        if hasattr(self, "_connection_state_unsub") and self._connection_state_unsub:
            self._connection_state_unsub()
        if hasattr(self, "_message_unsub") and self._message_unsub:
            self._message_unsub()
            
        # Save final state
        await self._async_save_state()