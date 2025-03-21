"""The Aprilaire 8870 Thermostat integration."""
import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Aprilaire 8870 Thermostat component from yaml configuration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aprilaire 8870 Thermostat from a config entry."""
    _LOGGER.debug("Setting up Aprilaire 8870 integration with entry: %s", entry.entry_id)
    
    hass.data.setdefault(DOMAIN, {})
    
    # Import modules here to avoid circular imports
    from .coordinator import AprilaireDataUpdateCoordinator
    from .connection import ConnectionManager
    from .services import async_setup_services
    
    # Initialize connection manager if not already initialized
    if "connection_manager" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["connection_manager"] = ConnectionManager(hass)
    
    connection_manager = hass.data[DOMAIN]["connection_manager"]
    connection = None
    
    # Create a connection from the config entry
    try:
        connection = await connection_manager.async_get_connection(entry.data)
        
        # Initialize device manager
        from .device import AprilaireDeviceManager
        
        if "device_manager" not in hass.data[DOMAIN]:
            hass.data[DOMAIN]["device_manager"] = AprilaireDeviceManager(hass, connection_manager)
        
        device_manager = hass.data[DOMAIN]["device_manager"]
        
        # Discover devices on the network
        discovered_devices = await device_manager.async_discover_devices(connection)
        
        if not discovered_devices:
            _LOGGER.warning("No Aprilaire 8870 thermostats discovered on the network")
            # Properly close the connection
            if connection:
                await connection_manager.async_close_connection(connection)
            raise ConfigEntryNotReady("No thermostats discovered")
        
        # Create update coordinator
        coordinator = AprilaireDataUpdateCoordinator(
            hass,
            _LOGGER,
            connection=connection,
            device_manager=device_manager,
            entry=entry,
        )
        
        # Perform initial data update
        await coordinator.async_config_entry_first_refresh()
        
        # Store coordinator in hass data
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "devices": discovered_devices,
        }
        
        # Set up services
        await async_setup_services(hass)
        
        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Register update listener to track config entry changes
        entry.async_on_unload(entry.add_update_listener(async_update_options))
        
        return True
        
    except Exception as ex:
        _LOGGER.error("Error setting up Aprilaire 8870 integration: %s", ex)
        # Properly close the connection if it exists
        if connection:
            await connection_manager.async_close_connection(connection)
        raise ConfigEntryNotReady(f"Error connecting to Aprilaire network: {ex}") from ex


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Aprilaire 8870 integration with entry: %s", entry.entry_id)
    
    # Import services here to avoid circular imports
    from .services import async_unregister_services
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Get coordinator and connection data
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator = entry_data["coordinator"]
        
        # Stop coordinator
        await coordinator.async_shutdown()
        
        # Check if this is the last entry for this domain
        if not hass.data[DOMAIN]:
            # Unregister services
            await async_unregister_services(hass)
            
            # Close all connections 
            if "connection_manager" in hass.data[DOMAIN]:
                connection_manager = hass.data[DOMAIN]["connection_manager"]
                await connection_manager.async_shutdown()
                del hass.data[DOMAIN]["connection_manager"]
            
            # Delete the domain data completely if empty
            del hass.data[DOMAIN]
    
    return unload_ok

