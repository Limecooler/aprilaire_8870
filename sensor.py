"""Sensor platform for Aprilaire 8870 thermostat integration."""
from __future__ import annotations

import logging
from typing import Any, Optional, Dict, List, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AprilaireDataUpdateCoordinator
from .const import (
    DOMAIN,
    SENSOR_TEMPERATURE,
    SENSOR_OUTDOOR_TEMPERATURE,
    SENSOR_HUMIDITY,
    SENSOR_OUTDOOR_HUMIDITY,
    SENSOR_REMOTE_TEMPERATURE,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire sensors based on config_entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    discovered_addresses = hass.data[DOMAIN][entry.entry_id]["discovered_addresses"]
    
    entities = []
    
    # Create entities for both initialized devices and discovered addresses
    all_device_ids = set(devices.keys()) | set(discovered_addresses)
    
    for device_id in all_device_ids:
        # Always add temperature sensor
        entities.append(AprilaireTemperatureSensor(coordinator, str(device_id)))
        
        # Add humidity sensor for all devices initially
        # This will be hidden if the device doesn't support it
        entities.append(AprilaireHumiditySensor(coordinator, str(device_id)))
        
        # Add outdoor temperature sensor for all devices initially
        entities.append(AprilaireOutdoorTemperatureSensor(coordinator, str(device_id)))
    
    async_add_entities(entities)

class AprilaireSensor(CoordinatorEntity, SensorEntity):
    """Base class for Aprilaire sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, 
        coordinator: AprilaireDataUpdateCoordinator,
        device_id: str,
        name: str,
        unique_id_suffix: str,
        device_class: str,
        state_class: str,
        unit_of_measurement: str,
        entity_category: Optional[str] = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device = coordinator.devices[device_id]
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{unique_id_suffix}"
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_native_unit_of_measurement = unit_of_measurement
        
        if entity_category:
            self._attr_entity_category = entity_category
        
        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=self._device.get("name", f"Aprilaire {device_id}"),
            manufacturer="Aprilaire",
            model="8870",
            sw_version=self._device.get("firmware_version", ""),
        )
    
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
        """Return additional attributes about the sensor."""
        device_data = self.coordinator.data.get(self._device_id, {})
        
        attrs = {}
        # Add indicator if data is from cache
        if device_data.get("from_cache", False):
            attrs["from_cache"] = True
            
        return attrs

class AprilaireTemperatureSensor(AprilaireSensor):
    """Representation of an Aprilaire temperature sensor."""

    def __init__(self, coordinator: AprilaireDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the temperature sensor."""
        super().__init__(
            coordinator=coordinator,
            device_id=device_id,
            name="Temperature",
            unique_id_suffix=SENSOR_TEMPERATURE,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        )
    
    @property
    def native_value(self) -> Optional[float]:
        """Return the current temperature."""
        return self._device.get("current_temperature")


class AprilaireHumiditySensor(AprilaireSensor):
    """Representation of an Aprilaire humidity sensor."""

    def __init__(self, coordinator: AprilaireDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the humidity sensor."""
        super().__init__(
            coordinator=coordinator,
            device_id=device_id,
            name="Humidity",
            unique_id_suffix=SENSOR_HUMIDITY,
            device_class=SensorDeviceClass.HUMIDITY,
            state_class=SensorStateClass.MEASUREMENT,
            unit_of_measurement=PERCENTAGE,
        )
    
    @property
    def native_value(self) -> Optional[int]:
        """Return the current humidity."""
        return self._device.get("current_humidity")


class AprilaireOutdoorTemperatureSensor(AprilaireSensor):
    """Representation of an Aprilaire outdoor temperature sensor."""

    def __init__(self, coordinator: AprilaireDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the outdoor temperature sensor."""
        super().__init__(
            coordinator=coordinator,
            device_id=device_id,
            name="Outdoor Temperature",
            unique_id_suffix=SENSOR_OUTDOOR_TEMPERATURE,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        )
    
    @property
    def native_value(self) -> Optional[float]:
        """Return the outdoor temperature."""
        return self._device.get("outdoor_temperature")


class AprilaireOutdoorHumiditySensor(AprilaireSensor):
    """Representation of an Aprilaire outdoor humidity sensor."""

    def __init__(self, coordinator: AprilaireDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the outdoor humidity sensor."""
        super().__init__(
            coordinator=coordinator,
            device_id=device_id,
            name="Outdoor Humidity",
            unique_id_suffix=SENSOR_OUTDOOR_HUMIDITY,
            device_class=SensorDeviceClass.HUMIDITY,
            state_class=SensorStateClass.MEASUREMENT,
            unit_of_measurement=PERCENTAGE,
        )
    
    @property
    def native_value(self) -> Optional[int]:
        """Return the outdoor humidity."""
        return self._device.get("outdoor_humidity")


class AprilaireRemoteSensor(AprilaireSensor):
    """Representation of an Aprilaire remote sensor."""

    def __init__(
        self, 
        coordinator: AprilaireDataUpdateCoordinator, 
        device_id: str,
        sensor_id: str,
        name: str,
        sensor_type: str,
    ) -> None:
        """Initialize the remote sensor."""
        self._sensor_id = sensor_id
        self._sensor_type = sensor_type
        
        if sensor_type == "temperature":
            device_class = SensorDeviceClass.TEMPERATURE
            unit_of_measurement = UnitOfTemperature.FAHRENHEIT
            unique_id_suffix = f"{SENSOR_REMOTE_TEMPERATURE}_{sensor_id}"
        else:  # humidity
            device_class = SensorDeviceClass.HUMIDITY
            unit_of_measurement = PERCENTAGE
            unique_id_suffix = f"remote_humidity_{sensor_id}"
        
        super().__init__(
            coordinator=coordinator,
            device_id=device_id,
            name=name,
            unique_id_suffix=unique_id_suffix,
            device_class=device_class,
            state_class=SensorStateClass.MEASUREMENT,
            unit_of_measurement=unit_of_measurement,
        )
    
    @property
    def native_value(self) -> Optional[float]:
        """Return the remote sensor value."""
        remote_sensors = self._device.get("remote_sensors", {})
        sensor_data = remote_sensors.get(self._sensor_id, {})
        return sensor_data.get("value")
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes about the remote sensor."""
        remote_sensors = self._device.get("remote_sensors", {})
        sensor_data = remote_sensors.get(self._sensor_id, {})
        
        attrs = {}
        if (address := sensor_data.get("address")) is not None:
            attrs["address"] = address
        if (module_address := sensor_data.get("module_address")) is not None:
            attrs["module_address"] = module_address
        if (sensor_number := sensor_data.get("sensor_number")) is not None:
            attrs["sensor_number"] = sensor_number
        if (is_control := sensor_data.get("is_control")) is not None:
            attrs["is_control"] = is_control
            
        return attrs

