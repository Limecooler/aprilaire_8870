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
from .const import DOMAIN


def _relay_on(hvac_status: Optional[str], relay: str) -> bool:
    """Return True iff ``relay`` is followed by ``+`` in ``hvac_status``.

    Robust to the two firmware formats we've seen
    (``G-Y-W-Y-W-B-O-`` vs ``G-Y1-W1-Y2-W2-B+O-``) and to a ``None`` /
    empty hvac_status from a freshly-added device that hasn't been polled
    yet. v0.4.3 fix — the previous index-based lookup crashed with
    ``TypeError: 'NoneType' object is not subscriptable`` whenever the
    coordinator delivered an entry whose hvac_status hadn't populated.
    """
    if not hvac_status:
        return False
    idx = hvac_status.find(relay)
    if idx < 0:
        return False
    sign_idx = idx + len(relay)
    if sign_idx >= len(hvac_status):
        return False
    return hvac_status[sign_idx] == "+"

_LOGGER = logging.getLogger(__name__)

# RS-485 bus is single-master half-duplex — serialize platform updates.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire binary sensor entities based on a config entry."""
    runtime = config_entry.runtime_data
    coordinator = runtime.coordinator
    devices = runtime.devices
    discovered_addresses = runtime.discovered_addresses

    entities: list[BinarySensorEntity] = []
    
    # Create entities for both initialized devices and discovered addresses
    all_device_ids = set(devices.keys()) | set(discovered_addresses)
    
    for device_id in all_device_ids:
        # If device is already initialized, use the device object
        if device_id in devices:
            device = devices[device_id]
            
            # Add sensors based on device capabilities
            entities.append(AprilaireHeatingStatusSensor(coordinator, device))
            entities.append(AprilaireCoolingStatusSensor(coordinator, device))
            entities.append(AprilaireFanStatusSensor(coordinator, device))
            
            # Emergency heat - only for heat pumps. v0.4.6: is_heat_pump
            # lives in device.capabilities, never as a direct attribute on
            # AprilaireDevice — the previous ``device.is_heat_pump`` access
            # would AttributeError if this code path were reachable at
            # platform-setup time (it isn't today because runtime_data.devices
            # is empty until the background init task populates it).
            if getattr(device, "capabilities", {}).get("is_heat_pump"):
                entities.append(AprilaireEmergencyHeatStatusSensor(coordinator, device))
            
            # Filter status
            entities.append(AprilaireFilterStatusSensor(coordinator, device))
            
            # System error status
            entities.append(AprilaireSystemErrorStatusSensor(coordinator, device))
            
            # Network override status
            entities.append(AprilaireNetworkOverrideStatusSensor(coordinator, device))
        else:
            # Device not fully initialized yet, use minimal placeholder
            from .device import AprilaireDevice
            
            # Create minimal device
            minimal_device = AprilaireDevice(
                address=device_id,
                coordinator=coordinator,
                protocol=None  # Will be set later during initialization
            )
            minimal_device.name = f"Aprilaire {device_id}"
            minimal_device.unique_id = f"{DOMAIN}_{device_id}"
            minimal_device.is_heat_pump = False  # Assume not a heat pump until we know
            
            # Add basic sensors that don't depend on specific capabilities
            entities.append(AprilaireHeatingStatusSensor(coordinator, minimal_device))
            entities.append(AprilaireCoolingStatusSensor(coordinator, minimal_device))
            entities.append(AprilaireFanStatusSensor(coordinator, minimal_device))
            entities.append(AprilaireFilterStatusSensor(coordinator, minimal_device))
            entities.append(AprilaireSystemErrorStatusSensor(coordinator, minimal_device))
            entities.append(AprilaireNetworkOverrideStatusSensor(coordinator, minimal_device))

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
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._attr_has_entity_name = True
        
    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for this device."""
        # Check if device is None
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
            "name": getattr(self._device, "name", f"Aprilaire {self._device_id}"),
            "manufacturer": "Aprilaire",
            "model": getattr(self._device, "model", "8870"),
            "sw_version": getattr(self._device, "firmware_version", None),
        }
        
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
        hvac_status = (self.coordinator.data.get(self._device_id) or {}).get("hvac_status")
        return _relay_on(hvac_status, "W1") or _relay_on(hvac_status, "W2")


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
        hvac_status = (self.coordinator.data.get(self._device_id) or {}).get("hvac_status")
        return _relay_on(hvac_status, "Y1") or _relay_on(hvac_status, "Y2")


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
        hvac_status = (self.coordinator.data.get(self._device_id) or {}).get("hvac_status")
        return _relay_on(hvac_status, "G")


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
        data = self.coordinator.data.get(self._device_id) or {}
        mode = data.get("mode") or ""
        hvac_status = data.get("hvac_status")
        return mode == "EMHT" and (
            _relay_on(hvac_status, "W1") or _relay_on(hvac_status, "W2")
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
        if not self.coordinator.data.get(self._device_id):
            return False

        # device.py stores filter_alarm as a bool (FLTALM=ON/OFF parsing).
        # v0.4.2 fix: was comparing to the string "ON" — always False.
        return bool(self.coordinator.data[self._device_id].get("filter_alarm"))


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
        if not self.coordinator.data.get(self._device_id):
            return False

        # v0.4.6: ``.get(key, default)`` returns the default only when
        # the key is MISSING; if the key exists with value ``None``
        # (the initial state in device.py:136 before ERROR has ever
        # been polled) ``.get`` returns None, and ``None != "000000"``
        # evaluates True — so the sensor reported a false-positive
        # error on every device until the first ERROR query landed.
        # With monitor_alarms=False (the default), ERROR is never
        # polled, so the sensor was permanently stuck "on".
        error_status = self.coordinator.data[self._device_id].get("error_status") or "000000"
        return error_status != "000000"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes about the error."""
        if not self.coordinator.data.get(self._device_id):
            return {}

        error_status = self.coordinator.data[self._device_id].get("error_status") or "000000"
        # Pad/truncate defensively — firmware nominally returns 6 chars
        # (one digit per subsystem) but absent/empty values would IndexError.
        error_status = error_status.ljust(6, "0")[:6]

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
        if not self.coordinator.data.get(self._device_id):
            return False

        # v0.4.2 fix: was reading "hold"; coordinator stores "hold_status"
        # (device.py:296). Always reported off before.
        return self.coordinator.data[self._device_id].get("hold_status", "OFF") == "ON"

