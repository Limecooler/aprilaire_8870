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
    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]
    
    entities = []
    
    for device_id, device in devices.items():
        # Fan override switch
        entities.append(AprilaireFanOverrideSwitch(coordinator, device))
        
        # Network override switch (HOLD functionality)
        # Only add if NETLK = 0 (network override enabled)
        if device.network_override_enabled:
            entities.append(AprilaireNetworkOverrideSwitch(coordinator, device))
        
        # Constant backlight switch
        entities.append(AprilaireBacklightSwitch(coordinator, device))

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
        self._attr_name = f"{device.name} {name_suffix}"
        self._attr_unique_id = f"{device.unique_id}_{unique_id_suffix}"
        self._attr_entity_category = entity_category
        self._attr_icon = icon
        self._attr_has_entity_name = True
        
    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for this device."""
        return self._device.device_info


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
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        fan_mode = self.coordinator.data[self._device.device_id].get("fan_mode", "AUTO")
        return fan_mode == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on fan override (set fan mode to ON)."""
        await self._device.async_set_fan_mode("ON")
        # Coordinator will be updated when the COS message is received
        # or during the next polling cycle
    
    async def async_turn_off(self, **kwargs: Any) -> None:
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

