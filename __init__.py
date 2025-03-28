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
from .protocol import AprilaireProtocol
from .coordinator import AprilaireDataUpdateCoordinator
from .device import AprilaireDeviceManager

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Aprilaire 8870 Thermostat component from yaml configuration."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_initialize_devices_background(
    hass: HomeAssistant, 
    entry: ConfigEntry, 
    coordinator, 
    device_manager, 
    discovered_addresses
) -> None:
    """Initialize devices in the background after config is complete."""
    _LOGGER.debug("Starting background initialization of %d thermostats", len(discovered_addresses))
    
    # Dictionary to store initialized devices
    initialized_devices = {}
    
    # Pre-initialize empty data structures for all discovered devices
    # This ensures coordinator data exists before any device tries to access it
    for address in discovered_addresses:
        device_id = str(address)
        if device_id not in coordinator.data:
            coordinator.data[device_id] = {
                "available": False,
                "from_cache": False,
            }
    
    # Setup devices with proper spacing between initializations
    for address in discovered_addresses:
        try:
            # Add delay between device initializations to prevent overwhelming the network
            await asyncio.sleep(1.0)
            
            _LOGGER.debug("Initializing thermostat %s in background", address)
            device = await device_manager.async_setup_device(address)
            if device:
                initialized_devices[address] = device
                # Update the data store as each device is initialized
                hass.data[DOMAIN][entry.entry_id]["devices"][address] = device
                
                # Update coordinator data structure with device state
                device_state = device.get_state() if hasattr(device, "get_state") else {}
                device_id = str(address)
                
                # Ensure device state has minimum required fields
                device_state["available"] = device.available
                coordinator.data[device_id] = device_state
                
                # Immediately trigger a coordinator update for this device
                # to refresh entity states for just this device
                coordinator.async_update_listeners()
        except Exception as dev_ex:
            _LOGGER.error("Error initializing device %s in background: %s", address, dev_ex)
    
    # Update the coordinator with all discovered devices
    coordinator.devices = initialized_devices
    
    # Perform initial data update for all devices
    await coordinator.async_refresh()
    
    _LOGGER.info("Background initialization complete for %d thermostats", len(initialized_devices))
    
    # After all devices are initialized, set up COS functionality in background
    hass.async_create_task(async_setup_cos_background(hass, entry, initialized_devices))

async def async_setup_cos_background(
    hass: HomeAssistant, 
    entry: ConfigEntry,
    devices
) -> None:
    """Set up COS functionality in the background."""
    _LOGGER.debug("Setting up COS functionality in background for %d thermostats", len(devices))
    
    for address, device in devices.items():
        try:
            # Add delay between devices to prevent overwhelming the network
            await asyncio.sleep(2.0)
            
            # Enable COS with retry and handling for unsupported flags
            await device.async_enable_cos()
            
            # Small delay after COS setup
            await asyncio.sleep(0.5)
        except Exception as cos_ex:
            _LOGGER.warning("Error setting up COS for device %s: %s", address, cos_ex)
            # Continue with other devices even if one fails
    
    _LOGGER.info("COS setup complete for all thermostats")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aprilaire 8870 Thermostat from a config entry with deferred initialization."""
    _LOGGER.debug("Setting up Aprilaire 8870 integration with entry: %s", entry.entry_id)
    
    hass.data.setdefault(DOMAIN, {})
    
    # Import services here to avoid circular imports
    from .services import async_setup_services
    
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
            
        # Connect to the device with retry
        connected = False
        for attempt in range(3):
            try:
                if await connection.async_connect():
                    connected = True
                    break
                _LOGGER.warning("Connection attempt %d failed, retrying...", attempt + 1)
                await asyncio.sleep(2)
            except Exception as conn_ex:
                _LOGGER.warning("Connection error on attempt %d: %s", attempt + 1, conn_ex)
                await asyncio.sleep(2)
                
        if not connected:
            _LOGGER.error("Failed to connect after 3 attempts")
            if connection:
                await connection.async_disconnect()
            raise ConfigEntryNotReady("Failed to connect to Aprilaire network")
        
        # Start reading from the connection
        await connection.async_start_reading()
        
        # Add delay after starting read loop
        await asyncio.sleep(1.0)
        
        # Get discovered thermostats from entry config
        discovered_addresses = entry.data.get("discovered_thermostats", [])
        
        if not discovered_addresses:
            _LOGGER.warning("No Aprilaire 8870 thermostats in configuration")
            # Properly close the connection
            if connection:
                await connection.async_disconnect()
            raise ConfigEntryNotReady("No thermostats in configuration")
        
        # Create protocol instance with connection
        protocol = AprilaireProtocol(connection)
        
        # Create update coordinator with minimal initial state
        coordinator = AprilaireDataUpdateCoordinator(
            hass,
            connection=connection,
            devices={},  # Will be populated during initialization
            device_manager=None  # Will be set after device_manager is created
        )
        
        # Create device manager with protocol and coordinator
        device_manager = AprilaireDeviceManager(coordinator, protocol)
        
        # Set the device_manager in the coordinator
        coordinator.device_manager = device_manager
        
        # Store initialized components in hass data
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "connection": connection,
            "device_manager": device_manager,
            "devices": {},  # Will be populated during background setup
            "discovered_addresses": discovered_addresses,
        }
        
        # Set up services
        await async_setup_services(hass)
        
        # Set up platforms first with minimal info
        try:
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        except Exception as ex:
            _LOGGER.error("Error setting up platforms: %s", ex)

        # Register update listener to track config entry changes
        entry.async_on_unload(entry.add_update_listener(async_update_options))
        
        # Schedule background task for detailed device initialization
        hass.async_create_task(
            async_initialize_devices_background(hass, entry, coordinator, device_manager, discovered_addresses)
        )
        
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