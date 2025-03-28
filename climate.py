"""Climate platform for Aprilaire 8870 thermostat integration."""
from __future__ import annotations

import logging
from typing import Any, Optional, List, Dict

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.components.climate.const import (
    FAN_AUTO, 
    FAN_ON,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_WHOLE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import (
    DOMAIN,
    ATTR_HVAC_RELAY_STATUS,
    ATTR_OUTDOOR_TEMPERATURE,
    ATTR_INDOOR_HUMIDITY,
    ATTR_FILTER_STATUS,
    ATTR_HOLD_STATUS,
    HVAC_MODE_APRILAIRE_TO_HA,
    HA_TO_APRILAIRE_HVAC_MODE,
    FAN_MODE_APRILAIRE_TO_HA,
    HA_TO_APRILAIRE_FAN_MODE,
)

_LOGGER = logging.getLogger(__name__)

# Define custom fan mode for circulation since it's not in Home Assistant constants
FAN_CIRCULATE = "circulate"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire climate based on config_entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    discovered_addresses = hass.data[DOMAIN][entry.entry_id]["discovered_addresses"]
    
    entities = []
    
    # Create entities for both initialized devices and discovered addresses
    all_device_ids = set(devices.keys()) | set(discovered_addresses)
    
    for device_id in all_device_ids:
        # If device is already initialized, use the device object
        if device_id in devices:
            device = devices[device_id]
            entities.append(AprilaireClimate(coordinator, device))
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
            minimal_device.model = "8870"
            
            entities.append(AprilaireClimate(coordinator, minimal_device))
    
    async_add_entities(entities)

class AprilaireClimate(CoordinatorEntity, ClimateEntity):
    """Representation of an Aprilaire Thermostat."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.AUTO,
        HVACMode.HEAT_COOL,  # Used for Auto mode
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE |
        ClimateEntityFeature.FAN_MODE |
        ClimateEntityFeature.TURN_OFF |
        ClimateEntityFeature.TURN_ON
    )
    _attr_fan_modes = [FAN_AUTO, FAN_ON, FAN_CIRCULATE]
    _attr_precision = PRECISION_WHOLE
    _attr_min_temp = 40
    _attr_max_temp = 90

    def __init__(
        self,
        coordinator,
        device,
    ) -> None:
        """Initialize the thermostat."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device.address)
        
        # Set unique_id based on device address
        self._attr_unique_id = f"{DOMAIN}_{device.address}_climate"
        
        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device.address))},
            name=device.name,
            manufacturer="Aprilaire",
            model=device.model,
            sw_version=device.firmware_version,
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
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        device_data = self.coordinator.data.get(self._device_id, {})
        return device_data.get("temperature")

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the temperature we try to reach."""
        device_data = self.coordinator.data.get(self._device_id, {})
        if self.hvac_mode == HVACMode.HEAT:
            return device_data.get("heat_setpoint")
        if self.hvac_mode == HVACMode.COOL:
            return device_data.get("cool_setpoint")
        return None

    @property
    def current_humidity(self) -> Optional[int]:
        """Return the current humidity."""
        device_data = self.coordinator.data.get(self._device_id, {})
        return device_data.get("humidity")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return hvac operation mode."""
        device_data = self.coordinator.data.get(self._device_id, {})
        mode = device_data.get("mode")
        return HVAC_MODE_APRILAIRE_TO_HA.get(mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> Optional[HVACAction]:
        """Return the current HVAC action."""
        device_data = self.coordinator.data.get(self._device_id, {})
        relay_status = device_data.get("hvac_status", "")
        
        # Determine action based on relay status
        if relay_status:
            if "+W1" in relay_status or "+W2" in relay_status:
                return HVACAction.HEATING
            if "+Y1" in relay_status or "+Y2" in relay_status:
                return HVACAction.COOLING
            if "+G" in relay_status:
                return HVACAction.FAN
                
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> Optional[str]:
        """Return the fan setting."""
        device_data = self.coordinator.data.get(self._device_id, {})
        fan_mode = device_data.get("fan_mode")
        return FAN_MODE_APRILAIRE_TO_HA.get(fan_mode, FAN_AUTO)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the device."""
        device_data = self.coordinator.data.get(self._device_id, {})
        
        attrs = {}
        
        # Add outdoor temperature if available
        if (outdoor_temp := device_data.get("outdoor_temperature")) is not None:
            attrs[ATTR_OUTDOOR_TEMPERATURE] = outdoor_temp
            
        # Add indoor humidity if available
        if (indoor_humidity := device_data.get("humidity")) is not None:
            attrs[ATTR_INDOOR_HUMIDITY] = indoor_humidity
            
        # Add HVAC relay status
        if (relay_status := device_data.get("hvac_status")) is not None:
            attrs[ATTR_HVAC_RELAY_STATUS] = relay_status
            
        # Add filter status
        if (filter_status := device_data.get("filter_alarm")) is not None:
            attrs[ATTR_FILTER_STATUS] = filter_status
            
        # Add hold status
        if (hold_status := device_data.get("hold_status")) is not None:
            attrs[ATTR_HOLD_STATUS] = hold_status
            
        # Add indicator if data is from cache
        if device_data.get("from_cache", False):
            attrs["from_cache"] = True
            
        return attrs

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            return
            
        temperature = kwargs[ATTR_TEMPERATURE]
        
        # For cached-only state, mark operation as pending but don't actually execute
        device_data = self.coordinator.data.get(self._device_id, {})
        if device_data.get("from_cache", False) and not device_data.get("available", False):
            _LOGGER.info(
                "Device is operating from cached state and unavailable, "
                "command will be applied when device becomes available"
            )
            return
            
        if self.hvac_mode == HVACMode.HEAT:
            await self.coordinator.async_set_heat_setpoint(self._device_id, temperature)
        elif self.hvac_mode == HVACMode.COOL:
            await self.coordinator.async_set_cool_setpoint(self._device_id, temperature)
        elif self.hvac_mode == HVACMode.AUTO or self.hvac_mode == HVACMode.HEAT_COOL:
            # In AUTO mode, adjust the active setpoint based on current operation
            if self.hvac_action == HVACAction.HEATING:
                await self.coordinator.async_set_heat_setpoint(self._device_id, temperature)
            elif self.hvac_action == HVACAction.COOLING:
                await self.coordinator.async_set_cool_setpoint(self._device_id, temperature)
            else:
                # Default to heat setpoint if system is idle
                await self.coordinator.async_set_heat_setpoint(self._device_id, temperature)
                
        # Request data update
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        aprilaire_mode = HA_TO_APRILAIRE_HVAC_MODE.get(hvac_mode)
        if aprilaire_mode is None:
            _LOGGER.error("Unsupported HVAC mode: %s", hvac_mode)
            return
        
        # For cached-only state, mark operation as pending but don't actually execute
        device_data = self.coordinator.data.get(self._device_id, {})
        if device_data.get("from_cache", False) and not device_data.get("available", False):
            _LOGGER.info(
                "Device is operating from cached state and unavailable, "
                "command will be applied when device becomes available"
            )
            return
            
        await self.coordinator.async_set_hvac_mode(self._device_id, hvac_mode)
        
        # Request data update
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        aprilaire_fan_mode = HA_TO_APRILAIRE_FAN_MODE.get(fan_mode)
        if aprilaire_fan_mode is None:
            _LOGGER.error("Unsupported fan mode: %s", fan_mode)
            return
        
        # For cached-only state, mark operation as pending but don't actually execute
        device_data = self.coordinator.data.get(self._device_id, {})
        if device_data.get("from_cache", False) and not device_data.get("available", False):
            _LOGGER.info(
                "Device is operating from cached state and unavailable, "
                "command will be applied when device becomes available"
            )
            return
            
        await self.coordinator.async_set_fan_mode(self._device_id, fan_mode)
        
        # Request data update
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        # If current mode is OFF, set to AUTO, otherwise keep current mode
        if self.hvac_mode == HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self.async_set_hvac_mode(HVACMode.OFF)