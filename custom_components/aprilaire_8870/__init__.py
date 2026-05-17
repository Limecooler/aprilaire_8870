"""The Aprilaire 8870 Thermostat integration."""
import asyncio
import logging
import traceback
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
from .config_flow import _parse_location_name
from .connection import AprilaireConnectionBase, SerialServerConnection, ComPortConnection
from .protocol import AprilaireProtocol
from .coordinator import AprilaireDataUpdateCoordinator
from .device import AprilaireDeviceManager

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Aprilaire 8870 Thermostat component from yaml configuration."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_register_services(hass: HomeAssistant) -> None:
    """Register services after initialization is complete."""
    _LOGGER.debug("Registering services now that initialization is complete")
    
    # Import services here to avoid circular imports
    from .services import async_setup_services
    
    try:
        await async_setup_services(hass)
        _LOGGER.debug("Successfully registered services after initialization")
    except Exception as ex:
        _LOGGER.error("Error setting up services after initialization: %s", ex)
        _LOGGER.error("Traceback: %s", traceback.format_exc())

async def async_initialize_devices_background(
    hass: HomeAssistant, 
    entry: ConfigEntry, 
    coordinator, 
    device_manager, 
    discovered_addresses
) -> None:
    """Initialize devices in the background after config is complete."""
    _LOGGER.debug("Starting background initialization of %d thermostats", len(discovered_addresses))
    
    # Verify connection is still active
    connection = coordinator.connection
    if not connection or not connection.is_connected():
        _LOGGER.error("Connection not available or not connected at start of background initialization")
        # Try to reconnect if disconnected
        if connection:
            _LOGGER.debug("Attempting to reconnect...")
            await connection.async_reconnect_with_backoff()
            if not connection.is_connected():
                _LOGGER.error("Reconnection failed, proceeding with limited functionality")
    
    # Dictionary to store initialized devices
    initialized_devices = {}
    
    try:
        # Pre-initialize empty data structures for all discovered devices
        # This ensures coordinator data exists before any device tries to access it
        _LOGGER.debug("Pre-initializing data structures for discovered devices")
        for address in discovered_addresses:
            try:
                device_id = str(address)
                if coordinator.data is None:
                    coordinator.data = {}
                
                # Ensure each device_id has a dictionary, not None
                if device_id not in coordinator.data:
                    coordinator.data[device_id] = {
                        "available": False,
                        "from_cache": False,
                    }
            except Exception as pre_init_ex:
                _LOGGER.exception("Error pre-initializing data for device %s: %s", address, pre_init_ex)
    
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
                    try:
                        if hasattr(device, "get_state"):
                            device_state = device.get_state()
                            device_id = str(address)
                            
                            # Ensure coordinator data is initialized
                            if coordinator.data is None:
                                coordinator.data = {}
                            
                            # Make sure the device_id exists in coordinator.data with a dictionary
                            if device_id not in coordinator.data:
                                coordinator.data[device_id] = {}
                            
                            # Now it's safe to update
                            if device_state:  # Also check if device_state is not None
                                coordinator.data[device_id].update(device_state)
                            
                            # Ensure device availability is set
                            coordinator.data[device_id]["available"] = device.available
                            
                            # Immediately trigger a coordinator update for this device
                            coordinator.async_update_listeners()
                    except Exception as state_ex:
                        _LOGGER.exception("Error updating coordinator data for device %s: %s", address, state_ex)
            except Exception as dev_ex:
                _LOGGER.exception("Error initializing device %s in background: %s", address, dev_ex)
                # Continue with other devices even if one fails
    
        # Update the coordinator with all discovered devices
        _LOGGER.debug("Background initialization completed for %d of %d thermostats",
                    len(initialized_devices), len(discovered_addresses))
        coordinator.devices = initialized_devices

        # Perform initial data update for all devices
        try:
            _LOGGER.debug("Performing initial data refresh for all devices")
            await coordinator.async_refresh()
            _LOGGER.debug("Initial data refresh completed successfully")
        except Exception as refresh_ex:
            _LOGGER.exception("Error performing initial data refresh: %s", refresh_ex)

        _LOGGER.info("Background initialization complete for %d thermostats", len(initialized_devices))
        
        # Register services now that initialization is complete
        await async_register_services(hass)
        
        # Device-name backfill is bus-heavy (~1s/device) and bootstrap-blocking
        # when awaited inline. Fire-and-forget as a background task so it can
        # finish after HA marks startup complete.
        _LOGGER.debug("Scheduling device-name backfill")
        try:
            create_bg = getattr(hass, "async_create_background_task", None)
            backfill_coro = _async_backfill_and_apply_device_names(
                hass, entry, coordinator.connection, device_manager, initialized_devices
            )
            if create_bg is not None:
                create_bg(backfill_coro, name=f"{DOMAIN}_name_backfill_{entry.entry_id}")
            else:
                hass.async_create_task(backfill_coro)
        except Exception as name_ex:
            _LOGGER.exception("Error scheduling device-name backfill: %s", name_ex)

        # After all devices are initialized and services registered, set up COS functionality in background
        _LOGGER.debug("Scheduling COS functionality setup")
        try:
            create_bg = getattr(hass, "async_create_background_task", None)
            cos_coro = async_setup_cos_background(hass, entry, initialized_devices)
            if create_bg is not None:
                create_bg(cos_coro, name=f"{DOMAIN}_cos_setup_{entry.entry_id}")
            else:
                hass.async_create_task(cos_coro)
            _LOGGER.debug("Successfully scheduled COS setup task")
        except Exception as cos_ex:
            _LOGGER.exception("Error scheduling COS setup: %s", cos_ex)
            
    except Exception as ex:
        _LOGGER.exception("Critical error during background initialization: %s", ex)
        # Even if there's a critical error, don't crash the whole integration
        
        # Try to register services even if there was an error with device initialization
        await async_register_services(hass)

async def async_setup_cos_background(
    hass: HomeAssistant, 
    entry: ConfigEntry,
    devices
) -> None:
    """Set up COS functionality in the background."""
    _LOGGER.debug("Setting up COS functionality in background for %d thermostats", len(devices))
    
    try:
        for address, device in devices.items():
            try:
                # Add delay between devices to prevent overwhelming the network
                await asyncio.sleep(2.0)
                
                _LOGGER.debug("Enabling COS for device %s", address)
                # Enable COS with retry and handling for unsupported flags
                await device.async_enable_cos()
                _LOGGER.debug("Successfully enabled COS for device %s", address)
                
                # Small delay after COS setup
                await asyncio.sleep(0.5)
            except Exception as cos_ex:
                _LOGGER.warning("Error setting up COS for device %s: %s", address, cos_ex)
                # Continue with other devices even if one fails
        
        _LOGGER.info("COS setup complete for all thermostats")
    except Exception as ex:
        _LOGGER.exception("Critical error during COS setup: %s", ex)
        # Don't crash the integration even if COS setup fails

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aprilaire 8870 Thermostat from a config entry with deferred initialization."""
    _LOGGER.debug("Setting up Aprilaire 8870 integration with entry: %s", entry.entry_id)
    
    hass.data.setdefault(DOMAIN, {})
    
    # Create connection based on config entry
    connection = None
    try:
        _LOGGER.debug("Creating connection based on config entry")
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
        _LOGGER.debug("Attempting to connect to Aprilaire network")
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
        
        _LOGGER.debug("Successfully connected to Aprilaire network")
        
        # Start reading from the connection
        _LOGGER.debug("Starting read loop from connection")
        await connection.async_start_reading()
        
        # Add delay after starting read loop
        await asyncio.sleep(1.0)

        # Verify connection is still active after starting read loop
        if not connection.is_connected():
            _LOGGER.error("Connection lost after starting read loop")
            raise ConfigEntryNotReady("Connection lost after starting read loop")
        
        _LOGGER.debug("Getting discovered thermostats from entry config")
        # Get discovered thermostats from entry config
        discovered_addresses = entry.data.get("discovered_thermostats", [])
        
        if not discovered_addresses:
            _LOGGER.warning("No Aprilaire 8870 thermostats in configuration")
            # Properly close the connection
            if connection:
                await connection.async_disconnect()
            raise ConfigEntryNotReady("No thermostats in configuration")
        
        _LOGGER.debug("Creating protocol instance with connection")
        # Create protocol instance with connection
        protocol = AprilaireProtocol(connection)
        
        _LOGGER.debug("Creating update coordinator with minimal initial state")
        # Create update coordinator with minimal initial state
        # Initialize with empty dictionaries to avoid NoneType errors
        coordinator = AprilaireDataUpdateCoordinator(
            hass,
            connection=connection,
            devices={},  # Will be populated during initialization
            device_manager=None  # Will be set after device_manager is created
        )

        # Initialize data structure for all discovered addresses
        if coordinator.data is None:
            coordinator.data = {}
            
        for address in discovered_addresses:
            device_id = str(address)
            coordinator.data[device_id] = {
                "available": False,
                "from_cache": False,
            }
            
        _LOGGER.debug("Creating device manager with protocol and coordinator")
        # device_names was populated by the config flow's name-probe (location
        # name carried in ID? response prefixes). Seeding the manager with it
        # makes AprilaireDevice.name correct from the first DeviceInfo build,
        # so HA's Name & Assign step shows the user's configured names.
        device_names = entry.data.get("device_names", {})
        device_manager = AprilaireDeviceManager(coordinator, protocol, device_names=device_names)
        
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
        
        # Set up platforms first with minimal info
        _LOGGER.debug("Setting up platform entities")
        try:
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
            _LOGGER.debug("Successfully set up platform entities")
        except Exception as platform_ex:
            _LOGGER.exception("Error setting up platforms: %s", platform_ex)
            # This is a critical error, but we'll try to continue

        # Register update listener to track config entry changes
        _LOGGER.debug("Registering update listener for config entry changes")
        entry.async_on_unload(entry.add_update_listener(async_update_options))
        
        # Detailed device init can take 30-120s on busy RS-485 buses with
        # many thermostats. Use async_create_background_task so HA's 60s
        # bootstrap watchdog doesn't trip ("Setup timed out for bootstrap
        # waiting on ...") and surface the "Wrapping up startup" banner.
        # Backwards-compatible: falls back to async_create_task on older
        # HA cores that lack the background-task helper.
        _LOGGER.debug("Scheduling background task for device initialization")
        try:
            create_bg = getattr(hass, "async_create_background_task", None)
            if create_bg is not None:
                create_bg(
                    async_initialize_devices_background(
                        hass, entry, coordinator, device_manager, discovered_addresses
                    ),
                    name=f"{DOMAIN}_init_{entry.entry_id}",
                )
            else:
                hass.async_create_task(
                    async_initialize_devices_background(
                        hass, entry, coordinator, device_manager, discovered_addresses
                    )
                )
            _LOGGER.debug("Successfully scheduled background initialization task")
        except Exception as task_ex:
            _LOGGER.exception("Error scheduling background initialization task: %s", task_ex)
            # Continue despite task scheduling error
        
        return True
        
    except Exception as ex:
        _LOGGER.exception("Error setting up Aprilaire 8870 integration: %s", ex)
        # Properly close the connection if it exists
        if connection:
            try:
                await connection.async_disconnect()
            except Exception as disc_ex:
                _LOGGER.error("Error disconnecting: %s", disc_ex)
                
        raise ConfigEntryNotReady(f"Error connecting to Aprilaire network: {ex}") from ex

async def _async_probe_device_names_live(
    connection, addresses: list[int]
) -> dict[str, str]:
    """Probe each address with ID? and parse out the location-name prefix.

    Mirrors the config_flow probe but runs against an already-connected
    bus during entry setup. Used to backfill entries that were created
    before v0.2.2 added discovery-time name capture.
    """
    if not connection or not addresses:
        _LOGGER.warning(
            "Name probe skipped: connection=%s addresses=%s",
            "set" if connection else "None",
            len(addresses) if addresses else 0,
        )
        return {}
    names: dict[str, str] = {}
    for address in addresses:
        try:
            await asyncio.sleep(0.1)
            await connection.async_send_command(f"SN{address} ID?")
            await asyncio.sleep(1.0)
            responses = (
                connection.get_received_messages()
                if hasattr(connection, "get_received_messages")
                else []
            )
            # Logged at WARNING so it lands in HA's system_log without needing
            # debug logging on the whole integration — diagnostic for users
            # whose thermostat names aren't being picked up.
            _LOGGER.warning(
                "Name probe address=%s responses=%r", address, responses
            )
            name = _parse_location_name(int(address), responses or [])
            if name:
                names[str(address)] = name
                _LOGGER.warning(
                    "Name probe matched address=%s name=%r", address, name
                )
        except Exception as probe_ex:
            _LOGGER.warning(
                "Name probe failed for thermostat %s: %s", address, probe_ex
            )
    return names


async def _async_backfill_and_apply_device_names(
    hass: HomeAssistant,
    entry: ConfigEntry,
    connection,
    device_manager,
    devices: dict,
) -> None:
    """Make sure HA's device registry reflects the on-device location names.

    1. If ``entry.data["device_names"]`` is empty, probe the live bus and
       persist the result so we never have to probe again.
    2. For each in-memory AprilaireDevice, push the current name into the
       device registry — but only when the user hasn't customized the name
       themselves (``name_by_user is None``).
    """
    stored_names: dict[str, str] = dict(entry.data.get("device_names") or {})
    _LOGGER.warning(
        "Name backfill starting: stored_names=%s devices=%d",
        stored_names or "<empty>",
        len(devices) if devices else 0,
    )

    if not stored_names and devices:
        _LOGGER.warning(
            "Entry %s has no stored device names — probing live bus for %d devices",
            entry.entry_id,
            len(devices),
        )
        probed = await _async_probe_device_names_live(connection, list(devices.keys()))
        _LOGGER.warning("Name probe complete: found %d names: %s", len(probed), probed)
        if probed:
            new_data = {**entry.data, "device_names": probed}
            hass.config_entries.async_update_entry(entry, data=new_data)
            stored_names = probed
            # Push into in-memory devices so DeviceInfo follows.
            device_manager.device_names = dict(probed)
            for address_str, name in probed.items():
                try:
                    addr_int = int(address_str)
                except ValueError:
                    continue
                device = devices.get(addr_int)
                if device is not None:
                    device.name = name

    if not stored_names:
        _LOGGER.warning(
            "Name backfill stopping: no stored names and probe returned nothing"
        )
        return

    registry = dr.async_get(hass)
    renamed = 0
    skipped_user = 0
    skipped_match = 0
    skipped_missing = 0
    for address_str, name in stored_names.items():
        entry_in_registry = registry.async_get_device(
            identifiers={(DOMAIN, address_str)}
        )
        if entry_in_registry is None:
            skipped_missing += 1
            continue
        if entry_in_registry.name_by_user is not None:
            # User picked their own name in the UI; don't override it.
            skipped_user += 1
            continue
        if entry_in_registry.name == name:
            skipped_match += 1
            continue
        registry.async_update_device(entry_in_registry.id, name=name)
        renamed += 1
    _LOGGER.warning(
        "Name backfill registry pass: renamed=%d skipped_user=%d skipped_unchanged=%d skipped_no_registry_entry=%d",
        renamed, skipped_user, skipped_match, skipped_missing,
    )


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