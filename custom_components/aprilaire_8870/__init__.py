"""The Aprilaire 8870 Thermostat integration."""
import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_MONITOR_ALARMS,
    CONF_MONITOR_HUMIDITY,
    CONF_MONITOR_OUTDOOR_TEMP,
    DEFAULT_MONITOR_ALARMS,
    DEFAULT_MONITOR_HUMIDITY,
    DEFAULT_MONITOR_OUTDOOR_TEMP,
    DOMAIN,
    PLATFORMS,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
from .config_flow import _parse_location_name
from .connection import AprilaireConnectionBase, SerialServerConnection, ComPortConnection
from .protocol import AprilaireProtocol
from .coordinator import AprilaireDataUpdateCoordinator
from .device import AprilaireDeviceManager


@dataclass
class AprilaireRuntimeData:
    """Per-config-entry runtime state, attached to ``ConfigEntry.runtime_data``.

    Closes the v0.3.0 Bronze-rule ``runtime-data`` gap and removes the
    stringly-keyed ``hass.data[DOMAIN][entry.entry_id]`` dict that every
    platform was reaching into. Typed access, automatic lifecycle.
    """

    coordinator: "AprilaireDataUpdateCoordinator"
    connection: "AprilaireConnectionBase"
    device_manager: "AprilaireDeviceManager"
    discovered_addresses: List[int]
    devices: Dict[int, Any] = field(default_factory=dict)


# Type alias so platform files can declare typed entry parameters.
AprilaireConfigEntry = ConfigEntry  # ConfigEntry[AprilaireRuntimeData] on py3.12+


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """No YAML configuration; setup happens per-entry via async_setup_entry."""
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
    
        # Parallel per-device init. The bus is naturally serialized inside
        # the connection layer (write side gated by _send_lock; one in-flight
        # request per address gated by the future registry). Per-device
        # async_initialize has internal asyncio.sleep padding that overlaps
        # cleanly across devices, dropping cold-start time from N×~2s to
        # roughly the longest single device's init.
        async def _init_one(address):
            try:
                _LOGGER.debug("Initializing thermostat %s in background", address)
                return address, await device_manager.async_setup_device(address)
            except Exception as dev_ex:
                _LOGGER.exception(
                    "Error initializing device %s in background: %s", address, dev_ex
                )
                return address, None

        init_results = await asyncio.gather(
            *(_init_one(address) for address in discovered_addresses),
            return_exceptions=False,
        )
        for address, device in init_results:
            if device is None:
                continue
            initialized_devices[address] = device
            entry.runtime_data.devices[address] = device

            # Propagate freshly-fetched state into the coordinator so
            # entity listeners see it on the next refresh.
            try:
                if hasattr(device, "get_state"):
                    device_state = device.get_state()
                    device_id = str(address)
                    if coordinator.data is None:
                        coordinator.data = {}
                    if device_id not in coordinator.data:
                        coordinator.data[device_id] = {}
                    if device_state:
                        coordinator.data[device_id].update(device_state)
                    coordinator.data[device_id]["available"] = device.available
            except Exception as state_ex:
                _LOGGER.exception(
                    "Error updating coordinator data for device %s: %s",
                    address, state_ex,
                )
        # Single listener notify after the whole batch — avoids 11 separate
        # refreshes for a single startup pass.
        coordinator.async_update_listeners()

        # v0.3.0: COS verification now runs on its own timer rather than
        # inline during _async_update_data. Start it once we have devices.
        coordinator.async_start_cos_verification_scheduler()

        # v0.4.0: daily TIME/DATE sync via SN0 globals. The 8870 RTC
        # doesn't auto-increment past midnight and the programmer's manual
        # explicitly requires the host to push both values at least once
        # per day. First sync fires 5s after init completes; thereafter
        # every 24 hours.
        coordinator.async_start_time_sync_scheduler()
    
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
    devices,
) -> None:
    """Bulk COS setup using the SN0 global broadcast.

    v0.4.0: send CR=NORMAL + each flag once globally instead of N × (CR + 7
    flags) per-device. The 8870 protocol's global-address support means
    every connected thermostat receives the write in a single bus
    transaction. Drops COS setup from ~25-30s on an 11-device bus to ~2s.

    Best-effort by design: not every firmware echoes a per-device
    confirmation to global writes, and we don't need one — broadcasts
    flow whenever a thermostat sees state change, regardless of whether
    the per-flag handshake echoed back.
    """
    if not devices:
        _LOGGER.debug("No devices initialized; skipping COS setup")
        return

    runtime = getattr(entry, "runtime_data", None)
    connection = runtime.connection if runtime else None
    if connection is None or not getattr(connection, "is_connected", lambda: False)():
        _LOGGER.debug("Connection unavailable; skipping bulk COS setup")
        return

    from .const import DEFAULT_COS_FLAGS
    addresses = sorted(devices.keys())

    try:
        _LOGGER.debug(
            "Bulk COS setup for %d devices via SN0 globals", len(addresses)
        )

        # 1. CR=NORMAL — hard requirement. Short timeout; we accept partial
        # echoes since CR is the "receive broadcasts" mode switch and not
        # every firmware echoes a per-device confirmation to a global write.
        cr_responses = await connection.async_send_global_command(
            "CR=NORMAL", expected_addresses=addresses, timeout=3.0,
        )
        cr_ok = {addr for addr, line in cr_responses.items() if "CR=NORMAL" in line.upper()}
        # Devices that didn't echo are still likely to have accepted —
        # globals are intentionally one-way in many implementations. Treat
        # the union of expected + echoed-back as "CR set".
        for address in addresses:
            device = devices.get(address)
            if device is None:
                continue
            device._cos_enabled = True
            if address in cr_ok:
                _LOGGER.debug("CR=NORMAL echoed by thermostat %s", address)

        # 2. Per-flag: bulk write → bulk readback → per-device retry.
        #
        # v0.4.4: live-log triage showed C1 broadcasts flowing but C2
        # broadcasts sparse — only ~4 unsolicited T= messages across 11
        # devices over 95 minutes despite ~14 polled temperature
        # transitions. Root cause: the previous flow trusted the one-shot
        # SN0 c<n>=ON write whether or not each device echoed back, so
        # any thermostat that missed the global silently never had the
        # flag enabled. Fix: read each flag back with SN0 c<n>? after
        # writing, and per-device retry for any address that reports
        # OFF. Adds ~7 extra SN0 queries + a small handful of per-device
        # retries at startup; ensures all C-flags actually stick.
        enabled_flags_per_device: Dict[int, set] = {addr: set() for addr in addresses}
        retry_total = 0
        for flag in sorted(DEFAULT_COS_FLAGS):
            # Bulk write.
            await connection.async_send_global_command(
                f"{flag}=ON", expected_addresses=addresses, timeout=2.0,
            )
            # Bulk readback. Firmware echoes uppercase ``C<n>=ON``/``=OFF``.
            verify_responses = await connection.async_send_global_command(
                f"{flag}?", expected_addresses=addresses, timeout=3.0,
            )

            needs_retry: list[int] = []
            for addr in addresses:
                line = (verify_responses.get(addr) or "").upper()
                if f"{flag.upper()}=ON" in line:
                    enabled_flags_per_device[addr].add(flag)
                else:
                    # No echo OR echo said OFF — needs a per-device retry.
                    needs_retry.append(addr)

            # Per-device retry for any device that didn't accept the bulk.
            # One write + one readback per address is enough — if it still
            # doesn't take, that's a firmware/wiring problem, not a
            # protocol race.
            for addr in needs_retry:
                device = devices.get(addr)
                if device is None or device.protocol is None:
                    continue
                try:
                    await device.protocol.execute_assignment_command(addr, flag, "ON")
                    verify = await device.protocol.execute_query_command(addr, flag)
                except Exception as retry_ex:  # pragma: no cover (defensive)
                    _LOGGER.debug(
                        "COS retry for %s on thermostat %s raised: %s",
                        flag, addr, retry_ex,
                    )
                    continue
                if verify and verify.upper().strip() == "ON":
                    enabled_flags_per_device[addr].add(flag)
                    retry_total += 1
                else:
                    _LOGGER.info(
                        "COS flag %s did not enable on thermostat %s after retry "
                        "(verify=%s) — broadcasts for this flag won't arrive from "
                        "this device; the bulk poller still keeps state fresh.",
                        flag, addr, verify,
                    )

        # Record per-device enabled flags. _cos_flags is now AUTHORITATIVE —
        # an empty set means COS broadcasts won't arrive for that device,
        # and async_verify_cos can give a real answer instead of guessing.
        enabled_total = 0
        for address in addresses:
            device = devices.get(address)
            if device is None:
                continue
            device._cos_flags = enabled_flags_per_device[address]
            enabled_total += len(enabled_flags_per_device[address])

        expected_total = len(addresses) * len(DEFAULT_COS_FLAGS)
        _LOGGER.info(
            "COS bulk setup complete: %d/%d device-flags enabled (%d retries succeeded)",
            enabled_total, expected_total, retry_total,
        )
    except Exception as ex:
        _LOGGER.exception("Critical error during bulk COS setup: %s", ex)
        # Don't crash the integration even if COS setup fails

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aprilaire 8870 Thermostat from a config entry with deferred initialization."""
    _LOGGER.debug("Setting up Aprilaire 8870 integration with entry: %s", entry.entry_id)

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
        
        # Start reading from the connection. async_start_reading hands the
        # read task off to the event loop and returns immediately; no need
        # to sleep before checking liveness.
        _LOGGER.debug("Starting read loop from connection")
        await connection.async_start_reading()

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
        # Each device looks up cached capabilities by (entry_id, address) on
        # init; pre-load the cache so the lookup is a dict access, not a disk
        # read per device.
        coordinator.config_entry_id = entry.entry_id
        await coordinator.async_load_capability_cache(entry.entry_id)
        device_manager = AprilaireDeviceManager(
            coordinator,
            protocol,
            device_names=device_names,
            monitor_alarms=entry.options.get(CONF_MONITOR_ALARMS, DEFAULT_MONITOR_ALARMS),
            monitor_humidity=entry.options.get(CONF_MONITOR_HUMIDITY, DEFAULT_MONITOR_HUMIDITY),
            monitor_outdoor_temp=entry.options.get(CONF_MONITOR_OUTDOOR_TEMP, DEFAULT_MONITOR_OUTDOOR_TEMP),
        )
        
        # Set the device_manager in the coordinator
        coordinator.device_manager = device_manager
        
        # v0.3.0: typed runtime_data replaces the stringly-keyed
        # hass.data[DOMAIN][entry.entry_id] dict.
        entry.runtime_data = AprilaireRuntimeData(
            coordinator=coordinator,
            connection=connection,
            device_manager=device_manager,
            discovered_addresses=discovered_addresses,
        )
        
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

async def _async_backfill_and_apply_device_names(
    hass: HomeAssistant,
    entry: ConfigEntry,
    connection,
    device_manager,
    devices: dict,
) -> None:
    """Push location names captured during device init into HA's device registry.

    Each AprilaireDevice has its ``name`` set inside ``_parse_model_info`` from
    the response prefix to ``SN<addr> ID?`` (the first command run during
    ``async_initialize``). This backfill collects those names from the already-
    populated ``device.name`` attribute, persists them to ``entry.data`` for
    inspection / future use, and updates HA's device registry — gated on
    ``name_by_user is None`` so user-set names are never overwritten.

    Replaces the v0.2.3 bus-probe approach: that probe ran *after* init had
    already finished, by which point the connection's ``is_connected()`` flag
    was often stale (the integration's init/coordinator updates race on the
    same connection state). Reading from ``device.name`` avoids any new bus
    traffic and any connection-state checks.
    """
    if not devices:
        _LOGGER.debug("Name backfill: no devices initialized, nothing to do")
        return

    discovered: dict[str, str] = {}
    for address, device in devices.items():
        name = getattr(device, "name", None)
        if not name:
            continue
        # Skip the default "Aprilaire <N>" placeholder — only persist names
        # that actually came off the bus.
        if name == f"Aprilaire {address}":
            continue
        discovered[str(address)] = name

    _LOGGER.info(
        "Name backfill: collected %d location names from device init: %s",
        len(discovered),
        discovered,
    )

    # Persist to entry.data so subsequent loads can seed device_manager
    # without waiting for the first init cycle to finish.
    stored = entry.data.get("device_names") or {}
    if discovered and discovered != stored:
        new_data = {**entry.data, "device_names": discovered}
        hass.config_entries.async_update_entry(entry, data=new_data)
        device_manager.device_names = dict(discovered)

    if not discovered:
        return

    registry = dr.async_get(hass)
    renamed = 0
    skipped_user = 0
    skipped_match = 0
    skipped_missing = 0
    for address_str, name in discovered.items():
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
    _LOGGER.info(
        "Name backfill registry pass: renamed=%d skipped_user=%d skipped_unchanged=%d skipped_no_registry_entry=%d",
        renamed, skipped_user, skipped_match, skipped_missing,
    )


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Aprilaire 8870 integration with entry: %s", entry.entry_id)

    from .services import async_unregister_services  # avoid circular import

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        runtime = entry.runtime_data
        await runtime.coordinator.async_shutdown()
        await runtime.connection.async_disconnect()
        # If this was the last loaded entry, drop the shared services too.
        loaded = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id and e.state.recoverable
        ]
        if not loaded:
            await async_unregister_services(hass)

    return unload_ok