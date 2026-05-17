"""Switch platform for Aprilaire 8870 thermostat integration."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AprilaireDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# RS-485 bus is single-master half-duplex — serialize platform updates.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire switch entities based on a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    devices = hass.data[DOMAIN][config_entry.entry_id].get("devices", {})
    discovered_addresses = hass.data[DOMAIN][config_entry.entry_id].get("discovered_addresses", [])

    entities: list[SwitchEntity] = []
    
    # Create entities for both initialized devices and discovered addresses
    all_device_ids = set(devices.keys()) | set(discovered_addresses)
    
    for device_id in all_device_ids:
        # If device is already initialized, use the device object
        if device_id in devices:
            device = devices[device_id]
            
            # Fan override switch
            entities.append(AprilaireFanOverrideSwitch(coordinator, device))
            
            # Network override switch (HOLD functionality)
            # Only add if NETLK = 0 (network override enabled)
            if hasattr(device, "network_override_enabled") and device.network_override_enabled:
                entities.append(AprilaireNetworkOverrideSwitch(coordinator, device))
            else:
                # Add anyway, since we don't know yet if it's supported
                entities.append(AprilaireNetworkOverrideSwitch(coordinator, device))
            
            # Constant backlight switch
            entities.append(AprilaireBacklightSwitch(coordinator, device))
        else:
            # Device not fully initialized yet, use minimal placeholder
            from .device import AprilaireDevice
            
            try:
                # Create minimal device
                minimal_device = AprilaireDevice(
                    address=device_id,
                    coordinator=coordinator,
                    protocol=None  # Will be set later during initialization
                )
                minimal_device.name = f"Aprilaire {device_id}"
                minimal_device.unique_id = f"{DOMAIN}_{device_id}"
                minimal_device.network_override_enabled = True  # Assume enabled until we know otherwise
                
                # Create entities with minimal device
                entities.append(AprilaireFanOverrideSwitch(coordinator, minimal_device))
                entities.append(AprilaireNetworkOverrideSwitch(coordinator, minimal_device))
                entities.append(AprilaireBacklightSwitch(coordinator, minimal_device))
            except Exception as ex:
                _LOGGER.error("Error creating minimal device for switch entities: %s", ex)

    async_add_entities(entities)

class AprilaireSwitch(CoordinatorEntity, SwitchEntity):
    """Base class for Aprilaire switch entities."""

    def __init__(
        self, 
        coordinator: AprilaireDataUpdateCoordinator, 
        device,
        name_suffix: str,
        unique_id_suffix: str,
        entity_category: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        # Safely get device_id
        if device is not None and hasattr(device, "address"):
            self._device_id = str(device.address)
        else:
            # Generate a fallback ID if device doesn't have an address
            self._device_id = f"unknown_{id(self)}"
        
        if device is not None:
            device_unique_id = getattr(device, "unique_id", f"{DOMAIN}_{self._device_id}")
        else:
            device_unique_id = f"{DOMAIN}_{self._device_id}"

        # With _attr_has_entity_name=True, HA prepends the device name automatically;
        # _attr_name carries only the entity-specific suffix.
        self._attr_name = name_suffix
        self._attr_unique_id = f"{device_unique_id}_{unique_id_suffix}"
        self._attr_entity_category = entity_category
        self._attr_icon = icon
        self._attr_has_entity_name = True
        
    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for this device."""
        # If device is None, return basic info
        if self._device is None:
            return {
                "identifiers": {(DOMAIN, self._device_id)},
                "name": f"Aprilaire {self._device_id}",
                "manufacturer": "Aprilaire",
                "model": "8870",
            }
        
        # Return device info for a valid device
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.name if hasattr(self._device, "name") else f"Aprilaire {self._device_id}",
            "manufacturer": "Aprilaire",
            "model": getattr(self._device, "model", "8870"),
            "sw_version": getattr(self._device, "firmware_version", None),
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check if coordinator or data is missing
        if not self.coordinator or not hasattr(self.coordinator, "data"):
            return False
            
        # Check if we have data for this device
        device_data = self.coordinator.data.get(self._device_id, {})
        
        # If data is from cache but device is not available, still return True
        if device_data.get("from_cache") and not device_data.get("available", False):
            return True
            
        # Otherwise, follow standard availability logic
        return self.coordinator.last_update_success and device_data.get("available", False)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes about the switch."""
        device_data = self.coordinator.data.get(self._device_id, {})
        
        attrs = {}
        # Add indicator if data is from cache
        if device_data.get("from_cache", False):
            attrs["from_cache"] = True
            
        return attrs
        
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on with cache state handling."""
        # For cached-only state, mark operation as pending but don't actually execute
        device_data = self.coordinator.data.get(self._device_id, {})
        if device_data.get("from_cache", False) and not device_data.get("available", False):
            _LOGGER.info(
                "Device is operating from cached state and unavailable, "
                "command will be applied when device becomes available"
            )
            return
        
        # Call the implementation in the subclass
        await self._async_turn_on_impl(**kwargs)
        
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off with cache state handling."""
        # For cached-only state, mark operation as pending but don't actually execute
        device_data = self.coordinator.data.get(self._device_id, {})
        if device_data.get("from_cache", False) and not device_data.get("available", False):
            _LOGGER.info(
                "Device is operating from cached state and unavailable, "
                "command will be applied when device becomes available"
            )
            return
            
        # Call the implementation in the subclass
        await self._async_turn_off_impl(**kwargs)
        
    async def _async_turn_on_impl(self, **kwargs: Any) -> None:
        """Implementation of turn on logic - to be overridden by subclasses."""
        raise NotImplementedError("Subclasses must implement this method")
        
    async def _async_turn_off_impl(self, **kwargs: Any) -> None:
        """Implementation of turn off logic - to be overridden by subclasses."""
        raise NotImplementedError("Subclasses must implement this method")

class AprilaireFanOverrideSwitch(AprilaireSwitch):
    """Switch to control fan mode (AUTO/ON)."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the fan override switch."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Fan Override",
            unique_id_suffix="fan_override",
            icon="mdi:fan",
        )

    @property
    def is_on(self) -> bool:
        """Return true if fan mode is ON (versus AUTO)."""
        device_data = self.coordinator.data.get(self._device_id, {})
        fan_mode = device_data.get("fan_mode", "AUTO")
        return fan_mode == "ON"

    async def _async_turn_on_impl(self, **kwargs: Any) -> None:
        """Turn on fan override (set fan mode to ON)."""
        await self._device.async_set_fan_mode("ON")
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle
    
    async def _async_turn_off_impl(self, **kwargs: Any) -> None:
        """Turn off fan override (set fan mode to AUTO)."""
        await self._device.async_set_fan_mode("AUTO")
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle

class AprilaireNetworkOverrideSwitch(AprilaireSwitch):
    """Switch to control network override (HOLD) functionality."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the network override switch."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Network Override",
            unique_id_suffix="network_override",
            entity_category=EntityCategory.CONFIG,
            icon="mdi:network-off",
        )

    @property
    def is_on(self) -> bool:
        """Return true if network override is active."""
        device_data = self.coordinator.data.get(self._device_id, {})
        return device_data.get("hold_status", "OFF") == "ON"

    async def _async_turn_on_impl(self, **kwargs: Any) -> None:
        """Turn on network override (set HOLD to ON)."""
        if hasattr(self._device, "async_set_hold"):
            await self._device.async_set_hold(True)
        # Coordinator will be updated when the COS message is received or during polling
    
    async def _async_turn_off_impl(self, **kwargs: Any) -> None:
        """Turn off network override (set HOLD to OFF)."""
        if hasattr(self._device, "async_set_hold"):
            await self._device.async_set_hold(False)
        # Coordinator will be updated when the COS message is received or during polling

class AprilaireBacklightSwitch(AprilaireSwitch):
    """Switch to control thermostat constant backlight."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the backlight switch."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Constant Backlight",
            unique_id_suffix="constant_backlight",
            entity_category=EntityCategory.CONFIG,
            icon="mdi:lightbulb",
        )

    @property
    def is_on(self) -> bool:
        """Return true if constant backlight is enabled."""
        device_data = self.coordinator.data.get(self._device_id, {})
        return device_data.get("constant_backlight", "OFF") == "ON"

    async def _async_turn_on_impl(self, **kwargs: Any) -> None:
        """Turn on constant backlight."""
        if hasattr(self._device, "async_set_constant_backlight"):
            await self._device.async_set_constant_backlight(True)
        # Coordinator will be updated during the next polling cycle
    
    async def _async_turn_off_impl(self, **kwargs: Any) -> None:
        """Turn off constant backlight."""
        if hasattr(self._device, "async_set_constant_backlight"):
            await self._device.async_set_constant_backlight(False)
        # Coordinator will be updated during the next polling cycle