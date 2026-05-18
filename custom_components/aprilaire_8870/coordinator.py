"""Data update coordinator for the Aprilaire integration."""
from __future__ import annotations

import asyncio
import logging
import re
import traceback
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .connection import SIGNAL_MESSAGE_RECEIVED
from .const import (
    DOMAIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_FALLBACK_SCAN_INTERVAL,
    DEFAULT_COS_VERIFICATION_INTERVAL,
    COS_FLAG_HVAC_RELAYS,
    COS_FLAG_TEMPERATURE,
    COS_FLAG_SETPOINTS,
    COS_FLAG_MODE,
    COS_FLAG_FAN,
    COS_FLAG_ALARMS,
    COS_FLAG_ERRORS,
    RESPONSE_CODE_TO_COMMAND,
    SIGNAL_CONNECTION_STATE_CHANGED,
)

# Parses any prefixed thermostat response: ``SN<addr>[<name with spaces>]
#  <CMD>=<value>``. Group 1 = address (digits), group 2 = response code
# (TEMP shows as "T", MODE as "M", FAN as "F" — these are mapped via
# RESPONSE_CODE_TO_COMMAND), group 3 = value.
_UNSOLICITED_RE = re.compile(r"^SN(\d+).*?\s+([A-Z][A-Z0-9]*)=(.*)$")

_LOGGER = logging.getLogger(__name__)

# Storage version for state persistence
STORAGE_VERSION = 1
# Storage key for state persistence
STORAGE_KEY = f"{DOMAIN}.state"

# v0.3.0 capability cache: model/firmware/capabilities don't change unless
# a thermostat is replaced. Caching them skips EQUIPCONFIG?/CT? on every
# startup (~1.5s × N devices). Keyed by (entry_id, address) so removing
# the integration entry forces a fresh discovery.
CAPABILITY_CACHE_VERSION = 1
CAPABILITY_CACHE_KEY = f"{DOMAIN}.capabilities"
# Maximum cache age before we re-discover from scratch as a sanity check.
CAPABILITY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


class AprilaireDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Aprilaire thermostat data."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection,
        devices,
        device_manager,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
        fallback_scan_interval: int = DEFAULT_FALLBACK_SCAN_INTERVAL,
        cos_verification_interval: int = DEFAULT_COS_VERIFICATION_INTERVAL,
        enable_cos: bool = True,
        cos_flags: Set[str] = None,
    ) -> None:
        """Initialize the coordinator."""
        # Start with fallback interval until COS is verified
        current_update_interval = timedelta(seconds=fallback_scan_interval)
        
        # Call parent init first
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=current_update_interval,
        )
        
        # Now do our own initialization
        self.connection = connection
        self.devices = devices or {}  # Ensure devices is always a dictionary
        self.device_manager = device_manager
        
        # Initialize our data structures
        # If self.data wasn't initialized by parent, initialize it now
        if self.data is None:
            self.data = {}
            
        self._device_data = {}
        
        # Initialize device data
        for device_id, device in devices.items():
            device_id_str = str(device_id)
            self._device_data[device_id_str] = {"available": device.available}
            # Also initialize in the main data structure
            self.data[device_id_str] = {"available": device.available}
        
        self._cos_enabled = enable_cos
        self._cos_flags = cos_flags or {
            COS_FLAG_HVAC_RELAYS,
            COS_FLAG_TEMPERATURE,
            COS_FLAG_SETPOINTS,
            COS_FLAG_MODE,
            COS_FLAG_FAN,
            COS_FLAG_ALARMS,
            COS_FLAG_ERRORS,
        }
        self._poll_backstop = fallback_scan_interval
        self._poll_healthy = update_interval
        self._cos_verification_interval = cos_verification_interval
        self._last_cos_verification = None
        self._cos_verified = False
        self._connection_state = False
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # Capability cache: persists model/firmware/capabilities so per-device
        # init can skip EQUIPCONFIG?/CT? when the device hasn't changed.
        self._cap_store = Store(
            hass, CAPABILITY_CACHE_VERSION, CAPABILITY_CACHE_KEY
        )
        self._capability_cache: Dict[str, Dict[str, Any]] = {}
        # Independent timer for COS verification — moved out of the poll
        # hot path so a long verification can't extend a poll cycle.
        self._cos_verification_unsub: Optional[Callable[[], None]] = None
        # v0.4.0: daily time/date sync. The 8870 has an internal RTC accurate
        # to ~1 min over 24h that doesn't auto-increment past midnight —
        # the programmer's manual explicitly requires the host to push
        # TIME and DATE at least once per day.
        self._time_sync_unsub: Optional[Callable[[], None]] = None
        self._cached_state = None
        self._state_loaded = False

        # Register for connection state change events
        self._connection_state_unsub = async_dispatcher_connect(
            hass, SIGNAL_CONNECTION_STATE_CHANGED, self._handle_connection_state_change
        )

        # Listen for any message that arrives on the bus — these come in
        # for two reasons: (a) responses to our own polls, and (b)
        # unsolicited COS-style broadcasts when someone touches the
        # thermostat directly. We re-route both into device state so the
        # UI updates in real time, regardless of whether the COS flag
        # subscription was accepted by the device.
        self._message_unsub = async_dispatcher_connect(
            hass, SIGNAL_MESSAGE_RECEIVED, self._handle_bus_message
        )

        # Explicitly register callback with the connection
        if connection is not None:
            _LOGGER.debug("Registering connection callback with the connection object")
            connection.register_connection_callback(self._connection_state_changed)
            # Initialize connection state from current connection status
            self._connection_state = connection.is_connected()
            _LOGGER.debug("Initial connection state from connection object: %s", self._connection_state)

    async def _async_load_stored_state(self) -> None:
        """Load stored state from disk."""
        try:
            stored_state = await self._store.async_load()
            if stored_state:
                _LOGGER.debug("Loaded stored state: %s devices", 
                            len(stored_state.get("devices", {})))
                self._cached_state = stored_state
                self._state_loaded = True
                
                # Initialize data dictionary if None
                if self._device_data is None:
                    self._device_data = {}
                if self.data is None:
                    self.data = {}
                
                # Populate device data with cached state
                devices_data = stored_state.get("devices", {})
                for device_id, state in devices_data.items():
                    self._device_data[device_id] = state
                    self._device_data[device_id]["from_cache"] = True
                    # Also update main data structure
                    self.data[device_id] = state.copy()
                    self.data[device_id]["from_cache"] = True
                
                # Update listeners to reflect cached state immediately
                self.async_update_listeners()
            else:
                _LOGGER.debug("No stored state found")
        except Exception as ex:
            _LOGGER.error("Error loading stored state: %s", ex)

    async def async_sync_time_to_thermostats(self) -> bool:
        """Push HA's current time and date to every thermostat via SN0.

        Per the programmer's manual the 8870 has an internal RTC that
        requires the host to push TIME and DATE at least once per day —
        no auto-increment across midnight. Format is `TIME=HHMM` (24-hour
        military, leading zeros required) and `DATE=MMDDYY` (leading
        zeros required for single-digit month/day).

        Uses SN0 global broadcast so one TIME and one DATE write update
        every connected thermostat in two wire commands instead of 2N.

        Fire-and-forget: writes don't reliably echo per-device responses
        on globals, and the values are non-critical — if a sync misses,
        the next one (tomorrow at most) catches up.
        """
        if not self.connection or not getattr(self.connection, "is_connected", lambda: False)():
            _LOGGER.debug("Time sync skipped: not connected")
            return False
        now = dt_util.now()
        time_value = f"{now.hour:02d}{now.minute:02d}"
        date_value = f"{now.month:02d}{now.day:02d}{now.year % 100:02d}"
        addresses = sorted(self.devices.keys()) if self.devices else []
        try:
            await self.connection.async_send_global_command(
                f"TIME={time_value}",
                expected_addresses=addresses,
                timeout=2.0,
            )
            await self.connection.async_send_global_command(
                f"DATE={date_value}",
                expected_addresses=addresses,
                timeout=2.0,
            )
            _LOGGER.info(
                "Pushed TIME=%s DATE=%s to %d thermostats via SN0 globals",
                time_value, date_value, len(addresses),
            )
            return True
        except Exception as ex:  # pragma: no cover (defensive)
            _LOGGER.warning("Time/date sync failed: %s", ex)
            return False

    def async_start_time_sync_scheduler(self) -> None:
        """Schedule daily TIME/DATE pushes to all thermostats.

        Runs once 5s after startup (so the bus has settled), then every
        24 hours. Idempotent — calling twice is a no-op.
        """
        if self._time_sync_unsub is not None:
            return

        async def _tick(_now):
            try:
                await self.async_sync_time_to_thermostats()
            except Exception as ex:  # pragma: no cover (defensive)
                _LOGGER.error("Time sync tick raised: %s", ex)

        async def _initial_sync(_now):
            await _tick(_now)

        # First sync after a 5s settle.
        self.hass.async_create_background_task(
            _initial_sync(None), name=f"{DOMAIN}_initial_time_sync",
        )
        # Recurring daily.
        self._time_sync_unsub = async_track_time_interval(
            self.hass, _tick, timedelta(hours=24),
        )

    def async_start_cos_verification_scheduler(self) -> None:
        """Start the periodic COS-verification timer (idempotent).

        Runs verification on its own schedule (initially every
        ``_cos_verification_interval`` seconds) so it never extends a
        poll cycle. The interval is dynamic: if verification ever returns
        0/N (most firmwares don't accept the flags), it stretches to 6h —
        see async_verify_cos_functionality.
        """
        if self._cos_verification_unsub is not None:
            return
        if not self._cos_enabled:
            return

        async def _tick(_now):
            try:
                await self.async_verify_cos_functionality()
            except Exception as ex:  # pragma: no cover (defensive)
                _LOGGER.error("COS verification tick raised: %s", ex)

        self._cos_verification_unsub = async_track_time_interval(
            self.hass, _tick, timedelta(seconds=self._cos_verification_interval),
        )

    async def async_load_capability_cache(self, entry_id: str) -> None:
        """Load the persisted capability cache for this entry.

        Populates ``self._capability_cache`` with whatever fits the
        ``<entry_id>:<address>`` key prefix; entries older than the TTL
        are silently dropped. Cheap to call repeatedly.
        """
        try:
            stored = await self._cap_store.async_load() or {}
        except Exception as ex:  # pragma: no cover  (storage layer rare path)
            _LOGGER.warning("Error loading capability cache: %s", ex)
            return

        prefix = f"{entry_id}:"
        now_ts = dt_util.utcnow().timestamp()
        cutoff = now_ts - CAPABILITY_CACHE_TTL_SECONDS
        self._capability_cache = {}
        for key, entry in stored.items():
            if not key.startswith(prefix):
                continue
            ts = entry.get("cached_at_ts")
            if isinstance(ts, (int, float)) and ts < cutoff:
                continue
            self._capability_cache[key] = entry

    def get_cached_capabilities(
        self, entry_id: str, address: int
    ) -> Optional[Dict[str, Any]]:
        """Return cached capabilities for (entry, address) or None."""
        return self._capability_cache.get(f"{entry_id}:{address}")

    async def async_save_capability_cache_entry(
        self,
        entry_id: str,
        address: int,
        model: Optional[str],
        firmware_version: Optional[str],
        capabilities: Dict[str, Any],
    ) -> None:
        """Persist one device's capabilities. Reads the on-disk cache first
        so we don't blow away other entries' / other devices' data."""
        try:
            stored = await self._cap_store.async_load() or {}
        except Exception as ex:  # pragma: no cover
            _LOGGER.warning("Error loading capability cache pre-save: %s", ex)
            stored = {}

        key = f"{entry_id}:{address}"
        stored[key] = {
            "model": model,
            "firmware_version": firmware_version,
            "capabilities": dict(capabilities or {}),
            "cached_at_ts": dt_util.utcnow().timestamp(),
        }
        # Mirror into our in-memory view too so subsequent lookups in the
        # same process see the fresh entry without re-reading disk.
        self._capability_cache[key] = stored[key]
        try:
            await self._cap_store.async_save(stored)
        except Exception as ex:  # pragma: no cover
            _LOGGER.warning("Error saving capability cache: %s", ex)

    async def _async_save_state(self) -> None:
        """Save current state to storage."""
        try:
            # Prepare state data
            state_data = {
                "last_updated": dt_util.utcnow().isoformat(),
                "devices": {},
            }
            
            # Add device data
            for device_id, data in self._device_data.items():
                # Don't store the "from_cache" marker
                device_data = {k: v for k, v in data.items() if k != "from_cache"}
                if device_data:
                    state_data["devices"][device_id] = device_data
            
            # Save to disk
            await self._store.async_save(state_data)
            _LOGGER.debug("Saved state to disk for %d devices", 
                        len(state_data["devices"]))
        except Exception as ex:
            _LOGGER.error("Error saving state: %s", ex)

    @callback
    def _handle_connection_state_change(self, config, state: str) -> None:
        """Handle connection state changes from dispatcher."""
        _LOGGER.debug("Received connection state change from dispatcher: %s", state)
        is_connected = (state == "connected")
        self._connection_state_changed(is_connected)

    @callback
    def _connection_state_changed(self, connected: bool) -> None:
        """Handle connection state changes."""
        if self._connection_state == connected:
            return
            
        self._connection_state = connected
        
        if connected:
            # Connection re-established. The next scheduled update will refresh
            # state; we don't kick off an extra refresh here to avoid racing the
            # entry-setup / unload paths that toggle connection state.
            _LOGGER.debug("Connection established — awaiting next scheduled refresh")
            # Reset per-device unsupported-command tracking — a firmware update
            # that happened over the bus reset could add support we'd otherwise
            # never re-discover.
            for device in (self.devices or {}).values():
                reset = getattr(device, "reset_unsupported_commands", None)
                if callable(reset):
                    reset()
        else:
            # Connection lost, mark devices as unavailable
            _LOGGER.debug("Connection lost, marking devices as unavailable")
            
            # Initialize device data if needed
            if self._device_data is None:
                self._device_data = {}
            if self.data is None:
                self.data = {}
                
            for device_id in self._device_data:
                self._device_data[device_id]["available"] = False
                # Also update main data structure
                if device_id in self.data:
                    self.data[device_id]["available"] = False
            
            self.async_update_listeners()

    async def _async_apply_state_to_device(self, device_id: str, state_data: Dict[str, Any]) -> None:
        """Apply stored state to a device.
        
        This is called when a device is initialized to apply stored state
        from a previous run.
        
        Args:
            device_id: The device ID
            state_data: Stored state data for this device
        """
        if self.devices is None or device_id not in self.devices:
            _LOGGER.debug("Cannot apply state to device %s - not found", device_id)
            return
            
        device = self.devices[device_id]
        
        # Apply state properties
        for key, value in state_data.items():
            if key != "from_cache" and key != "available":
                if hasattr(device, f"_state"):
                    device._state[key] = value
                    
        _LOGGER.debug("Applied stored state to device %s", device_id)

    async def async_verify_cos_functionality(self) -> bool:
        """Verify COS functionality per-device.

        Returns True if a majority of devices have COS working — in that case
        we switch to the long-poll interval and trust COS broadcasts to keep
        state fresh for everyone. If most devices lack COS, we stay on the
        fallback (more frequent) polling interval but log per-device so the
        problem stays visible without spamming a single noisy WARNING.
        """
        if not self._cos_enabled:
            return False

        _LOGGER.debug("Verifying COS functionality for all devices")

        if self.devices is None:
            _LOGGER.warning("No devices available to verify COS functionality")
            return False

        total = 0
        verified = 0
        for device_id, device in self.devices.items():
            total += 1
            try:
                if await device.async_verify_cos():
                    verified += 1
                else:
                    _LOGGER.info(
                        "COS not active on device %s — will continue polling it",
                        device_id,
                    )
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error(
                    "Failed to verify COS functionality for device %s: %s", device_id, ex
                )

        self._last_cos_verification = dt_util.utcnow()
        # "Mostly verified" = at least half of the devices broadcast COS.
        cos_healthy = total > 0 and verified * 2 >= total
        self._cos_verified = cos_healthy

        if cos_healthy:
            _LOGGER.info(
                "COS active on %d/%d devices — using normal scan interval", verified, total
            )
            self.update_interval = timedelta(seconds=self._poll_healthy)
        else:
            _LOGGER.warning(
                "COS active on only %d/%d devices — staying on fallback scan interval",
                verified, total,
            )
            self.update_interval = timedelta(seconds=self._poll_backstop)
            # v0.3.0 back-off: if NONE of the devices' flags verified,
            # stretch the verification interval to 6h (was 30min). Most
            # firmwares don't support per-flag COS subscription at all, so
            # re-probing every 30min is bus-noise for no benefit.
            if verified == 0 and self._cos_verification_interval < 6 * 3600:
                self._cos_verification_interval = 6 * 3600
                _LOGGER.info(
                    "0/%d devices accepted COS flags — backing off "
                    "verification to every 6h. Unsolicited broadcasts still "
                    "flow via _handle_bus_message regardless.",
                    total,
                )

        return cos_healthy

    async def _async_update_data(self) -> Dict[str, Dict[str, Any]]:
        """Update data via polling with improved error handling."""
        _LOGGER.debug("Beginning data update")
        if not self._connection_state:
            _LOGGER.error("No connection to Aprilaire network")
            raise UpdateFailed("No connection to Aprilaire network")
            
        # Check if connection object agrees with our state
        if self.connection and not self.connection.is_connected():
            _LOGGER.warning("Connection state mismatch! Coordinator thinks connected but connection reports disconnected")
            self._connection_state = False
            raise UpdateFailed("Connection state mismatch")
            
        try:
            # COS verification used to run inline here, which could extend
            # a poll cycle by 10-30s every 30min. It's now scheduled
            # independently via async_track_time_interval (see
            # async_start_cos_verification_scheduler), so the poll path is
            # device queries only.

            # Initialize data dictionary if needed
            if self._device_data is None:
                _LOGGER.debug("Initializing device_data dictionary")
                self._device_data = {}
            if self.data is None:
                _LOGGER.debug("Initializing data dictionary")
                self.data = {}
                
            # v0.4.0: bulk-poll path. Each essential command goes out once via
            # SN0 global broadcast and collects N responses in one bus round.
            # For 11 devices × 7 essential commands that's 7 wire commands
            # instead of 77.
            # v0.4.1: bulk now returns the set of addresses that responded.
            # Per-device async_update only fires for addresses that didn't
            # respond to bulk (with a full poll including its own retries) and
            # for the alarm-only optional pass that's per-device anyway.
            bulk_responders: set = set()
            if self.devices:
                bulk_responders = await self._async_bulk_poll_essentials()

            # Update each device. Pace requests so we don't crowd the RS-485 bus
            # — every command needs ~265ms processing time on the thermostat,
            # and back-to-back polls starve higher-addressed devices first.
            from .const import THERMOSTAT_PROCESSING_TIME_MS
            inter_device_delay = THERMOSTAT_PROCESSING_TIME_MS / 1000

            if self.devices is not None:
                for idx, (device_id, device) in enumerate(self.devices.items()):
                    if idx > 0:
                        await asyncio.sleep(inter_device_delay)
                    _LOGGER.debug("Updating device %s", device_id)
                    try:
                        # Skip per-device essentials when bulk already got
                        # this address — async_update with skip_essentials=True
                        # only polls the alarm group (if monitor_alarms is on
                        # and not bulked) and lets unsupported-cmd tracking
                        # continue per-device.
                        await device.async_update(
                            skip_essentials=(device_id in bulk_responders),
                        )
                        
                        try:
                            device_state = device.get_state()
                            
                            # Store device state in our data
                            device_id_str = str(device_id)
                            if device_state is not None:
                                _LOGGER.debug("Updated state for device %s: %s", device_id, device_state)
                                self._device_data[device_id_str] = device_state
                                self._device_data[device_id_str]["available"] = device.available
                                
                                # Also update main data structure
                                self.data[device_id_str] = device_state.copy()
                                self.data[device_id_str]["available"] = device.available
                                
                                # Remove the "from_cache" flag if present
                                if "from_cache" in self._device_data[device_id_str]:
                                    del self._device_data[device_id_str]["from_cache"]
                                if "from_cache" in self.data[device_id_str]:
                                    del self.data[device_id_str]["from_cache"]
                            else:
                                _LOGGER.warning("Device %s returned None state", device_id)
                        except Exception as state_ex:
                            _LOGGER.error("Error getting device state for device %s: %s", device_id, state_ex)
                            _LOGGER.error("Traceback: %s", traceback.format_exc())
                            device_id_str = str(device_id)
                            if device_id_str in self._device_data:
                                self._device_data[device_id_str]["available"] = False
                            if device_id_str in self.data:
                                self.data[device_id_str]["available"] = False
                            
                    except Exception as ex:
                        _LOGGER.error("Error updating device %s: %s", device_id, ex)
                        _LOGGER.error("Traceback: %s", traceback.format_exc())
                        device_id_str = str(device_id)
                        if device_id_str in self._device_data:
                            self._device_data[device_id_str]["available"] = False
                        if device_id_str in self.data:
                            self.data[device_id_str]["available"] = False
            else:
                _LOGGER.warning("No devices available to update")
            
            # Save state after updates
            try:
                _LOGGER.debug("Saving state after update")
                await self._async_save_state()
            except Exception as save_ex:
                _LOGGER.error("Error saving state: %s", save_ex)
                _LOGGER.error("Traceback: %s", traceback.format_exc())
            
            _LOGGER.debug("Data update completed successfully")
            return self._device_data
            
        except Exception as ex:
            _LOGGER.error("Error updating data: %s", ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            raise UpdateFailed(f"Error communicating with Aprilaire devices: {ex}")

    async def _async_bulk_poll_essentials(self) -> set:
        """Issue one SN0 global per essential command, route responses per-device.

        Each global reads a single value type from every connected thermostat
        in one bus round-trip — see Aprilaire install manual appendix:
        ``SN0 <command>?`` makes all connected thermostats respond with their
        address prefix. Per-device state is updated via the same path as
        unsolicited broadcasts (device._process_state_response).

        Returns the set of addresses that responded to AT LEAST ONE essential
        command. The caller uses this to skip the per-device essentials loop
        for happy addresses — otherwise every cycle re-polls all 7 essentials
        per device redundantly (~77 wasted commands on 11 devices). Addresses
        that returned nothing on bulk fall through to the per-device loop's
        full poll so retry-with-jitter and the circuit breaker still kick in.

        Optional commands (HUM/OT/alarms) are also bulked here when their
        respective monitor_* flags are on for the integration as a whole.
        Alarms remain per-device because the optional-skip and circuit-breaker
        accounting is per-device.
        """
        responded: set = set()
        if not self.connection or not getattr(self.connection, "is_connected", lambda: False)():
            return responded
        if not self.devices:
            return responded

        addresses = sorted(self.devices.keys())
        # Map each global query command code → device-level command name
        # for routing into _process_state_response.
        essentials = [
            ("TEMP", "TEMP"),
            ("MODE", "MODE"),
            ("FAN", "FAN"),
            ("HVAC", "HVAC"),
            ("HOLD", "HOLD"),
        ]
        # Setpoint queries are mode-dependent — issue both globally; devices
        # in the wrong mode will just not return a meaningful value.
        # (Mode itself is in the same batch, so the per-device loop later
        # is what gates SH/SC against the freshly-polled mode.)
        essentials.append(("SH", "SH"))
        essentials.append(("SC", "SC"))

        # v0.4.1: bulk the optional sensor queries too when enabled. Saves
        # 2 × N commands per cycle on an 11-device bus. Alarms (FLTALM etc.)
        # stay per-device since the optional-skip + circuit-breaker accounting
        # is per-device-per-command.
        # v0.4.7: also skip the SN0 HUM?/OT? globals when every device has
        # reported the firmware "no sensor wired" sentinel (``--%``/``--F``).
        # Most buses with no outdoor sensor and no humidity sensors waste
        # ~6 seconds per cycle on these queries; suppress them entirely
        # once we've confirmed no device supports the reading.
        sample_dev = next(iter(self.devices.values()), None)
        if sample_dev is not None:
            if getattr(sample_dev, "monitor_humidity", False) and any(
                getattr(d, "_humidity_supported", None) is not False
                for d in self.devices.values()
            ):
                essentials.append(("HUM", "HUM"))
            if getattr(sample_dev, "monitor_outdoor_temp", False) and any(
                getattr(d, "_outdoor_temp_supported", None) is not False
                for d in self.devices.values()
            ):
                essentials.append(("OT", "OT"))

        # Per the 8870 programmer's manual: when responses are expected to
        # a global command, the host must wait 265ms × "Number of Thermostats
        # on Network" before sending the next command. That setting defaults
        # to 32 on the device (set via on-thermostat menu), so the last
        # response can arrive at ~8.5s even with fewer actual thermostats.
        # 9s gives us a safety margin without being wastefully long.
        global_response_window = 9.0
        for code, dispatch_name in essentials:
            try:
                responses = await self.connection.async_send_global_command(
                    f"{code}?", expected_addresses=addresses,
                    timeout=global_response_window,
                )
            except Exception as bulk_ex:  # pragma: no cover (defensive)
                _LOGGER.debug("Bulk %s? failed: %s", code, bulk_ex)
                continue

            for address, response_line in responses.items():
                device = self.devices.get(address)
                if device is None:
                    continue
                process = getattr(device, "_process_state_response", None)
                if not callable(process):
                    continue
                try:
                    process(dispatch_name, response_line)
                    responded.add(address)
                except Exception as proc_ex:  # pragma: no cover (defensive)
                    _LOGGER.debug(
                        "Error applying bulk %s for thermostat %s: %s",
                        code, address, proc_ex,
                    )

        # Bulk responses prove these devices are alive — reset their
        # circuit-breaker state so a previously-tripped slow-keepalive
        # device returns to full polling.
        for address in responded:
            device = self.devices.get(address)
            if device is None:
                continue
            if getattr(device, "_slow_keepalive_mode", False):
                _LOGGER.info(
                    "Thermostat %s responded to bulk; clearing slow-keepalive",
                    address,
                )
            if hasattr(device, "_consecutive_full_poll_failures"):
                device._consecutive_full_poll_failures = 0
            if hasattr(device, "_slow_keepalive_mode"):
                device._slow_keepalive_mode = False

        return responded

    def _publish_single_device_state(self, device) -> None:
        """Push a single device's current state into ``data`` and notify.

        v0.4.8: replacement for the post-set ``async_request_refresh()``
        call. ``device.async_set_temperature`` (and the other writers)
        update ``device._state`` immediately on success, so we already
        know the new value — we just need HA to see it. Pulling
        ``device.get_state()`` into ``self.data[device_id]`` and calling
        ``async_update_listeners()`` updates the UI for THIS device only,
        instead of triggering a full 9-command bulk poll of all 11
        devices for the next ~30 seconds.

        v0.4.10: also writes ``available`` and clears ``from_cache`` so
        the entity doesn't go ``unavailable`` after the publish. The bulk
        poll path tracks these out-of-band (device.available is a
        separate attribute from device._state); the targeted publish has
        to mirror the same merge that ``_async_update_data`` does.
        """
        try:
            state = device.get_state()
        except Exception as ex:  # pragma: no cover (defensive)
            _LOGGER.debug("Couldn't fetch state for targeted update: %s", ex)
            return
        if not state:
            return
        device_id_str = str(device.address)
        if self._device_data is None:
            self._device_data = {}
        if self.data is None:
            self.data = {}
        # Mirror _async_update_data's merge: state + available + drop
        # from_cache (we just talked to the device, so the cache marker
        # is stale).
        merged = dict(state)
        merged["available"] = getattr(device, "available", True)
        merged.pop("from_cache", None)
        self._device_data[device_id_str] = merged
        self.data[device_id_str] = merged.copy()
        self.async_update_listeners()

    async def async_set_heat_setpoint(self, device_id: str, temperature: float) -> bool:
        """Set the heat setpoint for a device. Returns success."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return False

        device = self.devices.get(int(device_id))
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return False
        ok = await device.async_set_temperature(temperature, "HEAT")
        if ok:
            # Targeted UI update; avoids ~30s of bulk-poll bus traffic.
            self._publish_single_device_state(device)
            await self._async_save_state()
        return bool(ok)

    async def async_set_cool_setpoint(self, device_id: str, temperature: float) -> bool:
        """Set the cool setpoint for a device. Returns success."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return False

        device = self.devices.get(int(device_id))
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return False
        ok = await device.async_set_temperature(temperature, "COOL")
        if ok:
            self._publish_single_device_state(device)
            await self._async_save_state()
        return bool(ok)

    async def async_set_hvac_mode(self, device_id: str, mode: str) -> bool:
        """Set the HVAC mode for a device. Returns success."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return False

        device = self.devices.get(int(device_id))
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return False
        ok = await device.async_set_hvac_mode(mode)
        if ok:
            # Targeted UI update — see _publish_single_device_state.
            self._publish_single_device_state(device)
            await self._async_save_state()
        return bool(ok)

    async def async_set_fan_mode(self, device_id: str, mode: str) -> bool:
        """Set the fan mode for a device. Returns success."""
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return False

        device = self.devices.get(int(device_id))
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return False
        ok = await device.async_set_fan_mode(mode)
        if ok:
            self._publish_single_device_state(device)
            await self._async_save_state()
        return bool(ok)

    async def async_set_hold(self, device_id: str, on: bool) -> bool:
        """Set the network-override HOLD state for a device. Returns success.

        v0.4.9: wraps device.async_set_hold so the network-override
        switch can publish a targeted single-device state update on
        success (rather than waiting up to 5 min for the next bulk poll
        for HA's switch UI to reflect the change).
        """
        if self.devices is None:
            _LOGGER.error("Device dictionary not initialized")
            return False

        device = self.devices.get(int(device_id))
        if not device or not hasattr(device, "async_set_hold"):
            _LOGGER.error("Device not found or missing async_set_hold: %s", device_id)
            return False
        ok = await device.async_set_hold(bool(on))
        if ok:
            self._publish_single_device_state(device)
            await self._async_save_state()
        return bool(ok)

    @callback
    def _handle_bus_message(self, config: Any, line: str) -> None:
        """Decode any inbound thermostat message into device state.

        Fired by the connection's read loop for every parsed message.
        Solicited (response to one of our polls) and unsolicited
        (broadcast from a user-side change) messages share the same
        wire format, so a single handler suffices. State updates are
        idempotent — if the poller also handles the same response we
        just write the same value twice, no harm done.

        Only messages whose ``config`` matches the connection this
        coordinator owns are processed (lets multiple aprilaire entries
        coexist without crosstalk).
        """
        if not line or not isinstance(line, str):
            return
        if self.connection is not None and config is not getattr(
            self.connection, "config", config
        ):
            # Different connection — ignore.
            return
        match = _UNSOLICITED_RE.match(line)
        if not match:
            return
        address_str, code, value = match.groups()
        try:
            address = int(address_str)
        except ValueError:
            return
        device = self.devices.get(address)
        if device is None:
            return
        command = RESPONSE_CODE_TO_COMMAND.get(code)
        if command is None:
            return
        # Reconstruct a "<CMD>=<value>" string for _process_state_response,
        # which splits on the first "=" and uses everything before/after.
        try:
            device._process_state_response(command, f"{command}={value}")
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug(
                "Error applying unsolicited %s update for thermostat %s: %s",
                command, address, err,
            )
            return

        # Sync the coordinator's data view so subscribed entities pick
        # up the change on the next listener notification.
        device_id_str = str(address)
        try:
            new_state = device.get_state()
        except Exception:  # pragma: no cover - defensive
            return
        if new_state is None:
            return
        if self.data is None:
            self.data = {}
        existing = self.data.get(device_id_str) or {}
        merged = {**existing, **new_state, "available": device.available}
        merged.pop("from_cache", None)
        # Only notify listeners when something actually changed — avoids
        # spamming entity state writes when the unsolicited message just
        # echoes the value we already had.
        if merged == existing:
            return
        self.data[device_id_str] = merged
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        # Unsubscribe from dispatcher connections
        if hasattr(self, "_connection_state_unsub") and self._connection_state_unsub:
            self._connection_state_unsub()
        if hasattr(self, "_message_unsub") and self._message_unsub:
            self._message_unsub()
        if hasattr(self, "_cos_verification_unsub") and self._cos_verification_unsub:
            self._cos_verification_unsub()
            self._cos_verification_unsub = None
        if hasattr(self, "_time_sync_unsub") and self._time_sync_unsub:
            self._time_sync_unsub()
            self._time_sync_unsub = None
            
        # Save final state
        await self._async_save_state()