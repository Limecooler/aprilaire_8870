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
from .connection import AprilaireConnectionBase, SerialServerConnection, ComPortConnection

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
    from .services import async_setup_services
    from .device import AprilaireDeviceManager, AprilaireProtocol
    
    # Create connection based on config entry
    connection = None
    try:
        connection_type = entry.data.get("connection_type")
        
        if connection_type == "serial_server":
            connection = SerialServerConnection(hass, entry.data)
        elif connection_type == "serial_port":
            connection = ComPortConnection(hass, entry.data)
        else:
            _LOGGER.error("Unsupported connection type: %s", connection_type)
            return False
            
        # Connect to the device
        await connection.async_connect()
        
        # Create protocol instance with connection
        protocol = AprilaireProtocol(connection)    

        # Start reading from the connection
        await connection.async_start_reading()
        
        # Create update coordinator - only pass parameters that the constructor accepts
        coordinator = AprilaireDataUpdateCoordinator(
            hass,
            _LOGGER,
            connection
            # Removed entry=entry parameter
        )
        
        # Create device manager after coordinator is created
        device_manager = AprilaireDeviceManager(coordinator, protocol)
        
        # Discover devices on the network
        discovered_addresses = await device_manager.async_discover_devices(connection)
        
        if not discovered_addresses:
            _LOGGER.warning("No Aprilaire 8870 thermostats discovered on the network")
            # Properly close the connection
            if connection:
                await connection.async_disconnect()
            raise ConfigEntryNotReady("No thermostats discovered")
            
        # Setup devices
        discovered_devices = {}
        for address in discovered_addresses:
            device = await device_manager.async_setup_device(address)
            if device:
                discovered_devices[address] = device
        
        # Perform initial data update
        await coordinator.async_refresh()
        
        # Store everything in hass data
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "connection": connection,
            "device_manager": device_manager,
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
            await connection.async_disconnect()
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
        connection = entry_data["connection"]
        
        # Stop coordinator
        await coordinator.async_shutdown()
        
        # Close connection
        await connection.async_disconnect()
        
        # Check if this is the last entry for this domain
        if not hass.data[DOMAIN]:
            # Unregister services
            await async_unregister_services(hass)
            
            # Delete the domain data completely if empty
            del hass.data[DOMAIN]
    
    return unload_ok

