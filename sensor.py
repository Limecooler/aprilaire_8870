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
    
    entities = []
    for device_id, device in devices.items():
        # Always add temperature sensor
        entities.append(AprilaireTemperatureSensor(coordinator, device))
        
        # Add humidity sensor if capability exists
        if device.capabilities.get("has_humidity_sensor", False):
            entities.append(AprilaireHumiditySensor(coordinator, device))
        
        # Add outdoor temperature sensor if available
        if device.capabilities.get("has_outdoor_temp_sensor", False):
            entities.append(AprilaireOutdoorTemperatureSensor(coordinator, device))
        
        # Add outdoor humidity sensor if available
        if device.capabilities.get("has_outdoor_humidity_sensor", False):
            entities.append(AprilaireOutdoorHumiditySensor(coordinator, device))
        
        # Add remote temperature sensors if available
        remote_sensors = device.capabilities.get("support_modules", {})
        for sensor_id, sensor_info in remote_sensors.items():
            if isinstance(sensor_info, dict):  # Ensure it's a dictionary before accessing
                entities.append(AprilaireRemoteSensor(
                    coordinator, 
                    device, 
                    sensor_id, 
                    sensor_info.get("name", f"Remote Sensor {sensor_id}"),
                    sensor_info.get("type", "temperature")
                ))
    
    async_add_entities(entities, True)

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
        return self.coordinator.last_update_success and self._device.get("available", False)


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

