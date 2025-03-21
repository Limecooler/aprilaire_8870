"""Services for the Aprilaire 8870 thermostat integration."""
import logging
import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.const import ATTR_ENTITY_ID

from .const import (
    DOMAIN,
    SERVICE_SET_TEXT_MESSAGE,
    SERVICE_SET_BACKLIGHT,
    SERVICE_RESET_FILTER,
    SERVICE_SET_LOCKOUT,
    SERVICE_CONFIGURE_COS,
    SERVICE_SIGNAL_SET_TEXT_MESSAGE,
    SERVICE_SIGNAL_SET_BACKLIGHT,
    SERVICE_SIGNAL_RESET_FILTER,
    SERVICE_SIGNAL_SET_LOCKOUT,
    SERVICE_SIGNAL_CONFIGURE_COS,
    ATTR_MESSAGE,
    ATTR_MESSAGE_TYPE,
    ATTR_STATE,
    ATTR_DURATION,
    ATTR_FAN_LOCKOUT,
    ATTR_MODE_LOCKOUT,
    ATTR_SETPOINT_LOCKOUT,
    ATTR_NETWORK_LOCKOUT,
    ATTR_LOCKOUT_TIME,
    ATTR_LOCKOUT_LIMIT,
    ATTR_COS_FLAGS,
    MESSAGE_TYPE_TEMPORARY,
    MESSAGE_TYPE_PERMANENT_1,
    MESSAGE_TYPE_PERMANENT_2,
    MESSAGE_TYPE_PERMANENT_3,
    MESSAGE_TYPE_PERMANENT_4,
)

_LOGGER = logging.getLogger(__name__)

# Schema definitions for service calls

SET_TEXT_MESSAGE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Required(ATTR_MESSAGE): cv.string,
    vol.Required(ATTR_MESSAGE_TYPE): vol.In([
        MESSAGE_TYPE_TEMPORARY,
        MESSAGE_TYPE_PERMANENT_1,
        MESSAGE_TYPE_PERMANENT_2,
        MESSAGE_TYPE_PERMANENT_3,
        MESSAGE_TYPE_PERMANENT_4,
    ]),
})

SET_BACKLIGHT_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Optional(ATTR_STATE): cv.boolean,
    vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
})

RESET_FILTER_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
})

SET_LOCKOUT_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Optional(ATTR_FAN_LOCKOUT): vol.All(vol.Coerce(int), vol.In([0, 1, 2])),
    vol.Optional(ATTR_MODE_LOCKOUT): vol.All(vol.Coerce(int), vol.In([0, 2])),
    vol.Optional(ATTR_SETPOINT_LOCKOUT): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3, 4])),
    vol.Optional(ATTR_NETWORK_LOCKOUT): vol.All(vol.Coerce(int), vol.In([0, 1])),
    vol.Optional(ATTR_LOCKOUT_TIME): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    vol.Optional(ATTR_LOCKOUT_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
})

CONFIGURE_COS_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Required(ATTR_COS_FLAGS): vol.All(
        cv.ensure_list,
        [vol.In(["c1", "c2", "c3", "c5", "c6", "c7", "c8", "c9", "c10", 
                "c11", "c12", "c13", "c14", "c15", "c16", "c17", "c19"])],
    ),
})

async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Aprilaire 8870 services."""
    
    async def async_handle_set_text_message(service):
        """Handle the service call to set a text message on the thermostat display."""
        entity_id = service.data[ATTR_ENTITY_ID]
        message = service.data[ATTR_MESSAGE]
        message_type = service.data[ATTR_MESSAGE_TYPE]
        
        # Limit message length to 31 characters per documentation
        if len(message) > 31:
            _LOGGER.warning(
                "Message too long (max 31 characters). Truncating: %s", message
            )
            message = message[:31]
        
        # Dispatch the message update to the entity
        async_dispatcher_send(
            hass,
            f"{SERVICE_SIGNAL_SET_TEXT_MESSAGE}_{entity_id}",
            message,
            message_type,
        )
    
    async def async_handle_set_backlight(service):
        """Handle the service call to control the thermostat backlight."""
        entity_id = service.data[ATTR_ENTITY_ID]
        state = service.data.get(ATTR_STATE)
        duration = service.data.get(ATTR_DURATION)
        
        # Dispatch the backlight command to the entity
        async_dispatcher_send(
            hass,
            f"{SERVICE_SIGNAL_SET_BACKLIGHT}_{entity_id}",
            state,
            duration,
        )
    
    async def async_handle_reset_filter(service):
        """Handle the service call to reset the thermostat filter timer."""
        entity_id = service.data[ATTR_ENTITY_ID]
        
        # Dispatch the filter reset command to the entity
        async_dispatcher_send(
            hass,
            f"{SERVICE_SIGNAL_RESET_FILTER}_{entity_id}",
        )
    
    async def async_handle_set_lockout(service):
        """Handle the service call to configure lockout settings."""
        entity_id = service.data[ATTR_ENTITY_ID]
        fan_lockout = service.data.get(ATTR_FAN_LOCKOUT)
        mode_lockout = service.data.get(ATTR_MODE_LOCKOUT)
        setpoint_lockout = service.data.get(ATTR_SETPOINT_LOCKOUT)
        network_lockout = service.data.get(ATTR_NETWORK_LOCKOUT)
        lockout_time = service.data.get(ATTR_LOCKOUT_TIME)
        lockout_limit = service.data.get(ATTR_LOCKOUT_LIMIT)
        
        # Dispatch the lockout settings to the entity
        async_dispatcher_send(
            hass,
            f"{SERVICE_SIGNAL_SET_LOCKOUT}_{entity_id}",
            fan_lockout,
            mode_lockout,
            setpoint_lockout,
            network_lockout,
            lockout_time,
            lockout_limit,
        )
    
    async def async_handle_configure_cos(service):
        """Handle the service call to configure COS (Change of State) settings."""
        entity_id = service.data[ATTR_ENTITY_ID]
        cos_flags = service.data[ATTR_COS_FLAGS]
        
        # Dispatch the COS configuration to the entity
        async_dispatcher_send(
            hass,
            f"{SERVICE_SIGNAL_CONFIGURE_COS}_{entity_id}",
            cos_flags,
        )
    
    # Register all the services
    hass.services.async_register(
        DOMAIN, 
        SERVICE_SET_TEXT_MESSAGE, 
        async_handle_set_text_message, 
        schema=SET_TEXT_MESSAGE_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, 
        SERVICE_SET_BACKLIGHT, 
        async_handle_set_backlight, 
        schema=SET_BACKLIGHT_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, 
        SERVICE_RESET_FILTER, 
        async_handle_reset_filter, 
        schema=RESET_FILTER_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, 
        SERVICE_SET_LOCKOUT, 
        async_handle_set_lockout, 
        schema=SET_LOCKOUT_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, 
        SERVICE_CONFIGURE_COS, 
        async_handle_configure_cos, 
        schema=CONFIGURE_COS_SCHEMA
    )

async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister Aprilaire 8870 services."""
    services = [
        SERVICE_SET_TEXT_MESSAGE,
        SERVICE_SET_BACKLIGHT,
        SERVICE_RESET_FILTER,
        SERVICE_SET_LOCKOUT,
        SERVICE_CONFIGURE_COS,
    ]
    
    for service in services:
        hass.services.async_remove(DOMAIN, service)

