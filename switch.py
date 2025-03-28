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

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire switch entities based on a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    devices = hass.data[DOMAIN][config_entry.entry_id].get("devices", {})
    discovered_addresses = hass.data[DOMAIN][config_entry.entry_id].get("discovered_addresses", [])
    
    entities = []
    
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
        self._device_id = str(device.address) if device else ""
        self._attr_name = f"{device.name if device else f'Aprilaire {self._device_id}'} {name_suffix}"
        self._attr_unique_id = f"{device.unique_id if device else f'{DOMAIN}_{self._device_id}'}__{unique_id_suffix}"
        self._attr_entity_category = entity_category
        self._attr_icon = icon
        self._attr_has_entity_name = True
        
    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for this device."""
        # Add null check to avoid None errors
        if not self._device:
            return {
                "identifiers": {(DOMAIN, self._device_id)},
                "name": f"Aprilaire {self._device_id}",
                "manufacturer": "Aprilaire",
                "model": "8870",
            }
        
        # Return the device info if device is available
        return self._device.device_info
        
    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check if we have data for this device
        device_data = self.coordinator.data.get(self._device_id)
        if not device_data:
            return False
            
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
        return device_data.get("hold", "OFF") == "ON"

    async def _async_turn_on_impl(self, **kwargs: Any) -> None:
        """Turn on network override (set HOLD to ON)."""
        await self._device.async_set_hold(True)
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle
    
    async def _async_turn_off_impl(self, **kwargs: Any) -> None:
        """Turn off network override (set HOLD to OFF)."""
        await self._device.async_set_hold(False)
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle
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
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        return self.coordinator.data[self._device.device_id].get("hold", "OFF") == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on network override (set HOLD to ON)."""
        await self._device.async_set_network_override(True)
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off network override (set HOLD to OFF)."""
        await self._device.async_set_network_override(False)
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle


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
        await self._device.async_set_constant_backlight(True)
        # Coordinator will be updated during the next polling cycle
    
    async def _async_turn_off_impl(self, **kwargs: Any) -> None:
        """Turn off constant backlight."""
        await self._device.async_set_constant_backlight(False)
        # Coordinator will be updated during the next polling cycle
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
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        return self.coordinator.data[self._device.device_id].get("constant_backlight", "OFF") == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on constant backlight."""
        await self._device.async_set_constant_backlight(True)
        # Coordinator will be updated during the next polling cycle
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off constant backlight."""
        await self._device.async_set_constant_backlight(False)
        # Coordinator will be updated during the next polling cycle

