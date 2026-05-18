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
from homeassistant.helpers.dispatcher import async_dispatcher_connect
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
    SERVICE_SIGNAL_SET_TEXT_MESSAGE,
    SERVICE_SIGNAL_SET_BACKLIGHT,
    SERVICE_SIGNAL_RESET_FILTER,
    SERVICE_SIGNAL_SET_LOCKOUT,
    SERVICE_SIGNAL_CONFIGURE_COS,
)

_LOGGER = logging.getLogger(__name__)

# RS-485 is a single-master half-duplex bus; HA must not run platform updates
# in parallel or commands will collide on the wire.
PARALLEL_UPDATES = 1

# Define custom fan mode for circulation since it's not in Home Assistant constants
FAN_CIRCULATE = "circulate"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aprilaire climate based on config_entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    devices = runtime.devices
    discovered_addresses = runtime.discovered_addresses

    entities: list[ClimateEntity] = []
    
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
        # Safely get device_id
        if device is not None and hasattr(device, "address"):
            self._device_id = str(device.address)
        else:
            # Generate a fallback ID if device doesn't have an address
            self._device_id = f"unknown_{id(self)}"
        
        # Set unique_id based on device address
        self._attr_unique_id = f"{DOMAIN}_{self._device_id}_climate"
        
        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=getattr(device, "name", f"Aprilaire {self._device_id}"),
            manufacturer="Aprilaire",
            model=getattr(device, "model", "8870"),
            sw_version=getattr(device, "firmware_version", None),
        )

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
        device_data = None
        if self.coordinator and hasattr(self.coordinator, "data"):
            device_data = self.coordinator.data.get(self._device_id, {})
        
        # If no data found, the entity is not available
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
        """Return the current HVAC action derived from the relay-status byte.

        The 8870 reports relay state as ``<RELAY><+|->`` segments concatenated
        together — e.g. ``G+Y1+W1-Y2-W2-B-O+`` means fan on, Y1 (cool stage 1)
        on, W1 (heat stage 1) off, etc. Earlier code checked substrings like
        ``"+W1" in relay_status`` which produced a false positive whenever a
        preceding relay's ``+`` sign was followed by ``W1`` — most visibly
        when Y1 was active (``Y1+W1`` makes ``+W1`` match even though W1 is
        actually off), so every cooling cycle was misreported as heating.
        """
        device_data = self.coordinator.data.get(self._device_id, {})
        relay_status: str = device_data.get("hvac_status") or ""

        if not relay_status:
            return HVACAction.IDLE

        def is_on(relay: str) -> bool:
            """Return True only if the named relay is followed by '+'."""
            idx = relay_status.find(relay)
            if idx < 0:
                return False
            sign_idx = idx + len(relay)
            if sign_idx >= len(relay_status):
                return False
            return relay_status[sign_idx] == "+"

        heat_strip = is_on("W1") or is_on("W2")
        compressor = is_on("Y1") or is_on("Y2")
        fan = is_on("G")

        if heat_strip:
            return HVACAction.HEATING
        if compressor:
            # On a heat pump in HEAT mode the compressor runs to deliver heat
            # via the reversing valve, so a compressor-on/no-strip state should
            # render as HEATING. Cool/Auto/Off all treat it as cooling.
            mode = device_data.get("mode")
            if mode in ("HEAT", "EMHT"):
                return HVACAction.HEATING
            return HVACAction.COOLING
        if fan:
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

    async def async_added_to_hass(self) -> None:
        """Subscribe to per-entity service-dispatcher signals.

        v0.4.0: services in services.py have been dispatching per-entity
        signals (``f"{SIGNAL}_{entity_id}"``) since v0.2.0 with no
        subscriber on the entity side — every service call silently
        no-op'd. This wires the climate entity up to actually receive
        and execute the dispatched commands.
        """
        await super().async_added_to_hass()

        @callback
        def _handle_set_text_message(message: str, message_type: str) -> None:
            self.hass.async_create_task(
                self._device.async_set_text_message(message, message_type)
            )

        @callback
        def _handle_set_backlight(state: bool, duration: Optional[int]) -> None:
            # BLTON has no off/duration parameters per the protocol;
            # state=False is a no-op.
            if state is False:
                return
            self.hass.async_create_task(self._device.async_set_backlight())

        @callback
        def _handle_reset_filter() -> None:
            self.hass.async_create_task(self._device.async_reset_filter())

        @callback
        def _handle_set_lockout(
            fan_lockout: Optional[int],
            mode_lockout: Optional[int],
            setpoint_lockout: Optional[int],
            network_lockout: Optional[int],
            lockout_time: Optional[int],
            lockout_limit: Optional[int],
        ) -> None:
            self.hass.async_create_task(
                self._device.async_set_lockout(
                    fan_lockout=fan_lockout,
                    mode_lockout=mode_lockout,
                    setpoint_lockout=setpoint_lockout,
                    network_lockout=network_lockout,
                    lockout_time=lockout_time,
                    lockout_limit=lockout_limit,
                )
            )

        @callback
        def _handle_configure_cos(cos_flags: List[str]) -> None:
            self.hass.async_create_task(self._device.async_configure_cos(cos_flags))

        for signal, handler in (
            (SERVICE_SIGNAL_SET_TEXT_MESSAGE, _handle_set_text_message),
            (SERVICE_SIGNAL_SET_BACKLIGHT, _handle_set_backlight),
            (SERVICE_SIGNAL_RESET_FILTER, _handle_reset_filter),
            (SERVICE_SIGNAL_SET_LOCKOUT, _handle_set_lockout),
            (SERVICE_SIGNAL_CONFIGURE_COS, _handle_configure_cos),
        ):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, f"{signal}_{self.entity_id}", handler,
                )
            )

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

        # v0.4.7: only request a refresh if the underlying set actually
        # succeeded. Previously every failed set_temperature kicked off an
        # immediate full poll cycle of all 11 devices — a controller-type
        # mismatch on one device caused ~30s of redundant bus traffic
        # each time and drowned out the next user-initiated command.
        if self.hvac_mode == HVACMode.HEAT:
            ok = await self.coordinator.async_set_heat_setpoint(self._device_id, temperature)
        elif self.hvac_mode == HVACMode.COOL:
            ok = await self.coordinator.async_set_cool_setpoint(self._device_id, temperature)
        elif self.hvac_mode == HVACMode.AUTO or self.hvac_mode == HVACMode.HEAT_COOL:
            # In AUTO mode, adjust the active setpoint based on current operation
            if self.hvac_action == HVACAction.HEATING:
                ok = await self.coordinator.async_set_heat_setpoint(self._device_id, temperature)
            elif self.hvac_action == HVACAction.COOLING:
                ok = await self.coordinator.async_set_cool_setpoint(self._device_id, temperature)
            else:
                # Default to heat setpoint if system is idle
                ok = await self.coordinator.async_set_heat_setpoint(self._device_id, temperature)
        else:
            ok = False

        # v0.4.8: coordinator.async_set_*_setpoint now does a targeted
        # single-device state publish on success — no full bulk poll
        # needed. Setting a setpoint on Billy Bedroom previously kicked
        # off a ~30s 9-command poll of all 11 devices for no good reason.

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

        # Coordinator publishes a targeted single-device update on success.
        await self.coordinator.async_set_hvac_mode(self._device_id, hvac_mode)

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

        # Coordinator publishes a targeted single-device update on success.
        await self.coordinator.async_set_fan_mode(self._device_id, fan_mode)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        # If current mode is OFF, set to AUTO, otherwise keep current mode
        if self.hvac_mode == HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self.async_set_hvac_mode(HVACMode.OFF)