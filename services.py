"""Services for the Aprilaire 8870 thermostat integration."""
import logging
import voluptuous as vol
import traceback  # Add missing import

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
    COS_FLAG_HVAC_RELAYS,
    COS_FLAG_TEMPERATURE,
    COS_FLAG_SETPOINTS,
    COS_FLAG_MODE,
    COS_FLAG_FAN,
    COS_FLAG_ALARMS,
    COS_FLAG_ERRORS,
)

_LOGGER = logging.getLogger(__name__)

# Define the schema for the set_text_message service
SET_TEXT_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Required(ATTR_MESSAGE_TYPE): vol.In(
            [
                MESSAGE_TYPE_TEMPORARY,
                MESSAGE_TYPE_PERMANENT_1,
                MESSAGE_TYPE_PERMANENT_2,
                MESSAGE_TYPE_PERMANENT_3,
                MESSAGE_TYPE_PERMANENT_4,
            ]
        ),
    }
)

# Define the schema for the set_backlight service
SET_BACKLIGHT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_STATE): cv.boolean,
        vol.Optional(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=255)
        ),
    }
)

# Define the schema for the reset_filter service
RESET_FILTER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)

# Define the schema for the set_lockout service
SET_LOCKOUT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_FAN_LOCKOUT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=2)
        ),
        vol.Optional(ATTR_MODE_LOCKOUT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=2)
        ),
        vol.Optional(ATTR_SETPOINT_LOCKOUT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=4)
        ),
        vol.Optional(ATTR_NETWORK_LOCKOUT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1)
        ),
        vol.Optional(ATTR_LOCKOUT_TIME): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=255)
        ),
        vol.Optional(ATTR_LOCKOUT_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=20)
        ),
    }
)

# Define the schema for the configure_cos service
CONFIGURE_COS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_COS_FLAGS): vol.All(
            cv.ensure_list, 
            [vol.In([
                COS_FLAG_HVAC_RELAYS,
                COS_FLAG_TEMPERATURE,
                COS_FLAG_SETPOINTS,
                COS_FLAG_MODE,
                COS_FLAG_FAN,
                COS_FLAG_ALARMS,
                COS_FLAG_ERRORS,
            ])]
        ),
    }
)

async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Aprilaire 8870 services."""
    
    _LOGGER.debug("Setting up Aprilaire 8870 services")
    
    async def async_handle_set_text_message(service):
        """Handle the service call to set a text message on the thermostat display."""
        try:
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
            _LOGGER.debug("Dispatching text message service: %s", entity_id)
            async_dispatcher_send(
                hass,
                f"{SERVICE_SIGNAL_SET_TEXT_MESSAGE}_{entity_id}",
                message,
                message_type,
            )
        except Exception as ex:
            _LOGGER.error("Error handling set_text_message service: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
    
    # Define other service handlers with similar try/except blocks
    async def async_handle_set_backlight(service):
        """Handle the service call to control the thermostat backlight."""
        try:
            entity_id = service.data[ATTR_ENTITY_ID]
            state = service.data.get(ATTR_STATE)
            duration = service.data.get(ATTR_DURATION)
            
            # Dispatch the backlight command to the entity
            _LOGGER.debug("Dispatching backlight service: %s", entity_id)
            async_dispatcher_send(
                hass,
                f"{SERVICE_SIGNAL_SET_BACKLIGHT}_{entity_id}",
                state,
                duration,
            )
        except Exception as ex:
            _LOGGER.error("Error handling set_backlight service: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
    
    async def async_handle_reset_filter(service):
        """Handle the service call to reset the thermostat filter timer."""
        try:
            entity_id = service.data[ATTR_ENTITY_ID]
            
            # Dispatch the filter reset command to the entity
            _LOGGER.debug("Dispatching reset_filter service: %s", entity_id)
            async_dispatcher_send(
                hass,
                f"{SERVICE_SIGNAL_RESET_FILTER}_{entity_id}",
            )
        except Exception as ex:
            _LOGGER.error("Error handling reset_filter service: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
    
    async def async_handle_set_lockout(service):
        """Handle the service call to configure lockout settings."""
        try:
            entity_id = service.data[ATTR_ENTITY_ID]
            fan_lockout = service.data.get(ATTR_FAN_LOCKOUT)
            mode_lockout = service.data.get(ATTR_MODE_LOCKOUT)
            setpoint_lockout = service.data.get(ATTR_SETPOINT_LOCKOUT)
            network_lockout = service.data.get(ATTR_NETWORK_LOCKOUT)
            lockout_time = service.data.get(ATTR_LOCKOUT_TIME)
            lockout_limit = service.data.get(ATTR_LOCKOUT_LIMIT)
            
            # Dispatch the lockout settings to the entity
            _LOGGER.debug("Dispatching set_lockout service: %s", entity_id)
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
        except Exception as ex:
            _LOGGER.error("Error handling set_lockout service: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
    
    async def async_handle_configure_cos(service):
        """Handle the service call to configure COS (Change of State) settings."""
        try:
            entity_id = service.data[ATTR_ENTITY_ID]
            cos_flags = service.data[ATTR_COS_FLAGS]
            
            # Dispatch the COS configuration to the entity
            _LOGGER.debug("Dispatching configure_cos service: %s", entity_id)
            async_dispatcher_send(
                hass,
                f"{SERVICE_SIGNAL_CONFIGURE_COS}_{entity_id}",
                cos_flags,
            )
        except Exception as ex:
            _LOGGER.error("Error handling configure_cos service: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
    
    # Register all the services with try/except blocks
    try:
        _LOGGER.debug("Registering service: %s", SERVICE_SET_TEXT_MESSAGE)
        hass.services.async_register(
            DOMAIN, 
            SERVICE_SET_TEXT_MESSAGE, 
            async_handle_set_text_message, 
            schema=SET_TEXT_MESSAGE_SCHEMA
        )
        
        _LOGGER.debug("Registering service: %s", SERVICE_SET_BACKLIGHT)
        hass.services.async_register(
            DOMAIN, 
            SERVICE_SET_BACKLIGHT, 
            async_handle_set_backlight, 
            schema=SET_BACKLIGHT_SCHEMA
        )
        
        _LOGGER.debug("Registering service: %s", SERVICE_RESET_FILTER)
        hass.services.async_register(
            DOMAIN, 
            SERVICE_RESET_FILTER, 
            async_handle_reset_filter, 
            schema=RESET_FILTER_SCHEMA
        )
        
        _LOGGER.debug("Registering service: %s", SERVICE_SET_LOCKOUT)
        hass.services.async_register(
            DOMAIN, 
            SERVICE_SET_LOCKOUT, 
            async_handle_set_lockout, 
            schema=SET_LOCKOUT_SCHEMA
        )
        
        _LOGGER.debug("Registering service: %s", SERVICE_CONFIGURE_COS)
        hass.services.async_register(
            DOMAIN, 
            SERVICE_CONFIGURE_COS, 
            async_handle_configure_cos, 
            schema=CONFIGURE_COS_SCHEMA
        )
        _LOGGER.debug("All services registered successfully")
    except Exception as ex:
        _LOGGER.error("Failed to register services: %s", ex)
        _LOGGER.error("Traceback: %s", traceback.format_exc())
        raise

async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister Aprilaire 8870 services."""
    try:
        _LOGGER.debug("Unregistering Aprilaire 8870 services")
        services = [
            SERVICE_SET_TEXT_MESSAGE,
            SERVICE_SET_BACKLIGHT,
            SERVICE_RESET_FILTER,
            SERVICE_SET_LOCKOUT,
            SERVICE_CONFIGURE_COS,
        ]
        
        for service in services:
            _LOGGER.debug("Removing service: %s", service)
            hass.services.async_remove(DOMAIN, service)
        _LOGGER.debug("All services unregistered successfully")
    except Exception as ex:
        _LOGGER.error("Error unregistering services: %s", ex)
        _LOGGER.error("Traceback: %s", traceback.format_exc())