"""Binary sensor platform for Aprilaire 8870 thermostat integration."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AprilaireDataUpdateCoordinator
from .const import DOMAIN, HVAC_RELAY_INDICES

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire binary sensor entities based on a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]
    
    entities = []
    
    for device_id, device in devices.items():
        # Only add sensors for capabilities the device has
        
        # HVAC status sensors
        entities.append(AprilaireHeatingStatusSensor(coordinator, device))
        entities.append(AprilaireCoolingStatusSensor(coordinator, device))
        
        # Fan status
        entities.append(AprilaireFanStatusSensor(coordinator, device))
        
        # Emergency heat - only for heat pumps
        if device.is_heat_pump:
            entities.append(AprilaireEmergencyHeatStatusSensor(coordinator, device))
        
        # Filter status
        entities.append(AprilaireFilterStatusSensor(coordinator, device))
        
        # System error status
        entities.append(AprilaireSystemErrorStatusSensor(coordinator, device))
        
        # Network override status
        entities.append(AprilaireNetworkOverrideStatusSensor(coordinator, device))

    async_add_entities(entities)


class AprilaireBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for Aprilaire binary sensor entities."""

    def __init__(
        self, 
        coordinator: AprilaireDataUpdateCoordinator, 
        device,
        name_suffix: str,
        unique_id_suffix: str,
        device_class: Optional[str] = None,
        entity_category: Optional[str] = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_name = f"{device.name} {name_suffix}"
        self._attr_unique_id = f"{device.unique_id}_{unique_id_suffix}"
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._attr_has_entity_name = True
        
    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for this device."""
        return self._device.device_info


class AprilaireHeatingStatusSensor(AprilaireBinarySensor):
    """Binary sensor for heating status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the heating status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Heating",
            unique_id_suffix="heating",
            device_class=BinarySensorDeviceClass.HEAT,
        )

    @property
    def is_on(self) -> bool:
        """Return true if heating is active."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        # Check W1 (first stage heat) and W2 (second stage heat) relay status
        hvac_status = self.coordinator.data[self._device.device_id].get("hvac_status", "")
        return '+' in hvac_status[HVAC_RELAY_INDICES["W1"]] or '+' in hvac_status[HVAC_RELAY_INDICES["W2"]]


class AprilaireCoolingStatusSensor(AprilaireBinarySensor):
    """Binary sensor for cooling status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the cooling status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Cooling",
            unique_id_suffix="cooling",
            device_class=BinarySensorDeviceClass.COLD,
        )

    @property
    def is_on(self) -> bool:
        """Return true if cooling is active."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        # Check Y1 (first stage cool) and Y2 (second stage cool) relay status
        hvac_status = self.coordinator.data[self._device.device_id].get("hvac_status", "")
        return '+' in hvac_status[HVAC_RELAY_INDICES["Y1"]] or '+' in hvac_status[HVAC_RELAY_INDICES["Y2"]]


class AprilaireFanStatusSensor(AprilaireBinarySensor):
    """Binary sensor for fan status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the fan status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Fan Running",
            unique_id_suffix="fan_running",
            device_class=BinarySensorDeviceClass.RUNNING,
        )

    @property
    def is_on(self) -> bool:
        """Return true if fan is running."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        # Check G (fan) relay status
        hvac_status = self.coordinator.data[self._device.device_id].get("hvac_status", "")
        return '+' in hvac_status[HVAC_RELAY_INDICES["G"]]


class AprilaireEmergencyHeatStatusSensor(AprilaireBinarySensor):
    """Binary sensor for emergency heat status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the emergency heat status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Emergency Heat",
            unique_id_suffix="emergency_heat",
            device_class=BinarySensorDeviceClass.HEAT,
        )

    @property
    def is_on(self) -> bool:
        """Return true if emergency heat is active."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        # Emergency heat is determined by the mode being EMHT and aux heat (W1/W2) being on
        mode = self.coordinator.data[self._device.device_id].get("mode", "")
        hvac_status = self.coordinator.data[self._device.device_id].get("hvac_status", "")
        
        return (
            mode == "EMHT" and 
            ('+' in hvac_status[HVAC_RELAY_INDICES["W1"]] or '+' in hvac_status[HVAC_RELAY_INDICES["W2"]])
        )


class AprilaireFilterStatusSensor(AprilaireBinarySensor):
    """Binary sensor for filter status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the filter status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Filter Alert",
            unique_id_suffix="filter_alert",
            device_class=BinarySensorDeviceClass.PROBLEM,
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def is_on(self) -> bool:
        """Return true if filter needs attention."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        return self.coordinator.data[self._device.device_id].get("filter_alarm", "OFF") == "ON"


class AprilaireSystemErrorStatusSensor(AprilaireBinarySensor):
    """Binary sensor for system error status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the system error status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="System Error",
            unique_id_suffix="system_error",
            device_class=BinarySensorDeviceClass.PROBLEM,
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def is_on(self) -> bool:
        """Return true if system has an error."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        # If any of the error codes are non-zero, there's an error
        error_status = self.coordinator.data[self._device.device_id].get("error", "000000")
        return error_status != "000000"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes about the error."""
        if not self.coordinator.data.get(self._device.device_id):
            return {}
        
        error_status = self.coordinator.data[self._device.device_id].get("error", "000000")
        
        # Decode error status into component errors
        error_types = {
            "temperature_sensor": error_status[0],
            "remote_temp_sensor": error_status[1],
            "outdoor_temp_sensor": error_status[2],
            "humidity_sensor": error_status[3],
            "communication": error_status[4],
            "eeprom": error_status[5],
        }
        
        return error_types


class AprilaireNetworkOverrideStatusSensor(AprilaireBinarySensor):
    """Binary sensor for network override status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the network override status sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            name_suffix="Network Override",
            unique_id_suffix="network_override",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def is_on(self) -> bool:
        """Return true if network override is active."""
        if not self.coordinator.data.get(self._device.device_id):
            return False
        
        return self.coordinator.data[self._device.device_id].get("hold", "OFF") == "ON"

