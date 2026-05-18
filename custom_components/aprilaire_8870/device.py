"""Device representation of Aprilaire 8870 thermostats."""
import asyncio
import logging
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CMD_ID,
    CMD_EQUIPCONFIG,
    CMD_CT,
    CMD_MODE,
    CMD_FAN,
    CMD_TEMP,
    CMD_HUM,
    CMD_OT,
    CMD_HVAC,
    CMD_SH,
    CMD_SC,
    CMD_HOLD,
    CMD_BIHUM,
    CMD_CR,
    CMD_RSM,
    COS_HVAC_RELAYS,
    COS_TEMPERATURE,
    COS_SETPOINTS,
    COS_MODE,
    COS_FAN,
    COS_ALARMS,
    COS_ERRORS,
    MODE_OFF,
    MODE_HEAT,
    MODE_COOL,
    MODE_AUTO,
    MODE_EMHT,
    CONTROLLER_TYPE_TEMP,
    CONTROLLER_TYPE_HUMID,
    DEFAULT_COS_FLAGS,
    EQUIPMENT_TYPE_HEAT_COOL,
    EQUIPMENT_TYPE_HEAT_PUMP,
    THERMOSTAT_PROCESSING_TIME_MS,
)
from .protocol import AprilaireProtocol

# After this many consecutive failed POLL CYCLES on the same optional
# command we mark it as unsupported for that device and stop polling it
# until the next connection. Each poll cycle attempts the command up to
# (1 + retries) times — with the optional path's retries=1 that's 2
# tries per cycle. Threshold of 2 means we give up after 2 cycles where
# both tries failed (4 total attempts, ~4 wasted seconds per cycle).
_UNSUPPORTED_THRESHOLD = 2

# Circuit breaker for an unresponsive device. After this many consecutive
# poll cycles where EVERY essential command timed out, drop the device to
# a slow keep-alive (one TEMP? per cycle) until it answers again. Prevents
# an offline thermostat from chewing ~45s/cycle on retries × essential
# commands while keeping at least a heartbeat path for recovery.
_CIRCUIT_BREAKER_THRESHOLD = 5

_LOGGER = logging.getLogger(__name__)


class AprilaireDevice:
    """Representation of an Aprilaire thermostat device."""

    def __init__(
        self,
        address: int,
        coordinator: DataUpdateCoordinator,
        protocol: AprilaireProtocol,
        preset_name: Optional[str] = None,
        monitor_alarms: bool = False,
        monitor_humidity: bool = True,
        monitor_outdoor_temp: bool = True,
    ) -> None:
        """Initialize the Aprilaire device.

        Args:
            address: The RS-485 network address of the thermostat (1-64)
            coordinator: The data update coordinator
            protocol: The protocol implementation for command execution
            preset_name: Optional location name discovered during config flow.
                Used as the initial device name so HA's Name & Assign step
                shows it before runtime name-discovery refines it.
            monitor_alarms: If True, poll FLTALM/WPALM/SYSALM/DEHALM/ERROR
                each cycle. Defaults to False because most firmwares NACK
                these and they consume the bulk of bus time when polled
                every cycle. Unsolicited COS broadcasts still flow through
                regardless of this flag, so a real filter-alarm change
                still reaches HA via the message listener.
            monitor_humidity: If True, poll HUM each cycle. Default True.
            monitor_outdoor_temp: If True, poll OT each cycle. Default True.
        """
        self.address = address
        self.coordinator = coordinator
        self.protocol = protocol
        self.name = preset_name or f"Aprilaire {address}"
        self.monitor_alarms = monitor_alarms
        self.monitor_humidity = monitor_humidity
        self.monitor_outdoor_temp = monitor_outdoor_temp
        self.model = "8870"
        self.firmware_version = None
        self.available = False
        
        # Device capabilities - discovered during initialization
        self.capabilities = {
            "controller_type": CONTROLLER_TYPE_TEMP,  # Default to temperature controller
            "equipment_type": EQUIPMENT_TYPE_HEAT_COOL,  # Default to standard heat/cool
            "is_heat_pump": False,
            "stages_heat": 1,
            "stages_cool": 1,
            "has_emergency_heat": False,
            "has_humidifier": False,
            "has_dehumidifier": False,
            "support_modules": [],
        }
        
        # Current device state
        self._state = {
            "temperature": None,
            "humidity": None,
            "outdoor_temperature": None,
            "mode": None,
            "fan_mode": None,
            "heat_setpoint": None,
            "cool_setpoint": None,
            "hvac_status": None,
            "hold_status": None,
            "filter_alarm": None,
            "system_alarm": None,
            "water_panel_alarm": None,
            "dehumidifier_alarm": None,
            "error_status": None,
        }
        
        # Initialize state for COS tracking
        self._last_cos_check = 0
        self._cos_enabled = False
        self._cos_flags = set()

        # Per-device tracking of optional commands the thermostat doesn't
        # respond to. After UNSUPPORTED_THRESHOLD consecutive failures we
        # stop sending the command to this device — it'll be retried on
        # the next connection (state is cleared via reset_unsupported).
        # Keeps the bus quiet on alarm queries that older firmware doesn't
        # implement.
        self._optional_failure_counts: Dict[str, int] = {}
        self._unsupported_commands: Set[str] = set()
        # Circuit-breaker state: count of consecutive poll cycles where
        # nothing essential came back. Once it crosses _CIRCUIT_BREAKER_THRESHOLD
        # the device enters a slow keep-alive mode (TEMP-only) until it
        # answers again. Reset on any successful essential-command response.
        self._consecutive_full_poll_failures: int = 0
        self._slow_keepalive_mode: bool = False

    def update_from_real_device(self, real_device):
        """Update properties from a fully initialized device.
        
        This is used when a placeholder device is replaced with a real one
        after background initialization.
        
        Args:
            real_device: The fully initialized device
        """
        self.model = real_device.model
        self.firmware_version = real_device.firmware_version
        self.available = real_device.available
        self.capabilities = real_device.capabilities
        self.protocol = real_device.protocol
        self._state = real_device._state
        self._cos_enabled = real_device._cos_enabled
        self._cos_flags = real_device._cos_flags
        # Carry over v0.2.7 unsupported-command tracking so a placeholder
        # swap doesn't re-discover the same NACKs from scratch.
        self._optional_failure_counts = dict(real_device._optional_failure_counts)
        self._unsupported_commands = set(real_device._unsupported_commands)

    async def _update_with_delays(self) -> None:
        """Update device state with delays between commands."""
        # Sequential command execution with delays
        essential_commands = ["TEMP", "MODE", "FAN", "HVAC", "HOLD"]
        optional_commands = ["HUM", "OT", "FLTALM", "WPALM", "SYSALM", "DEHALM", "ERROR"]
        
        # First handle essential commands that should not be skipped
        for cmd in essential_commands:
            # Skip setpoint commands if not in appropriate mode
            if cmd == "SH" and self._state.get("mode") not in ["HEAT", "AUTO", "EMHT"]:
                continue
            if cmd == "SC" and self._state.get("mode") not in ["COOL", "AUTO"]:
                continue
                
            response = await self._send_command_with_retry(f"SN{self.address} {cmd}?")
            
            # Process the response to update state
            if response:
                self._process_state_response(cmd, response)
                
            # Small delay between commands
            await asyncio.sleep(0.3)
        
        # Then handle optional commands that can be skipped if they fail
        for cmd in optional_commands:
            if cmd in self._unsupported_commands:
                continue
            try:
                response = await self._send_command_with_retry(
                    f"SN{self.address} {cmd}?",
                    retries=1,  # Reduced retries for optional commands
                    allow_skip=True  # Allow skipping on failure
                )

                # Process the response to update state
                if response:
                    self._process_state_response(cmd, response)
                    self._optional_failure_counts.pop(cmd, None)
                else:
                    self._note_optional_failure(cmd)

                # Small delay between commands
                await asyncio.sleep(0.3)
            except Exception as err:
                _LOGGER.debug(f"Skipping optional command {cmd} after failure: {err}")
                self._note_optional_failure(cmd)
                continue

    @property
    def device_id(self) -> int:
        """Return the device ID (alias for address)."""
        return self.address

    def _extract_device_name_from_response(self, response: str) -> Optional[str]:
        """Extract device name/location from a response if present.
        
        Args:
            response: Response string from thermostat
            
        Returns:
            Extracted name or None if not found
        """
        if not response:
            return None
            
        # Extract location name from response if present
        # Format is typically SN<address><location> <command>=<value>
        import re
        name_match = re.match(r'^SN\d+([A-Za-z0-9 ]+)', response)
        if name_match and name_match.group(1).strip():
            location_name = name_match.group(1).strip()
            if location_name:
                return location_name
                
        return None

    def _process_state_response(self, command: str, response: str) -> None:
        """Process a state query response to update internal state."""
        # Extract value from response
        value = None
        if "=" in response:
            parts = response.split("=", 1)
            if len(parts) > 1:
                value = parts[1].strip()
        
        if not value:
            return
            
        # Update state based on command
        if command == "TEMP":
            self._state["temperature"] = self._parse_temperature(value)
        elif command == "HUM":
            self._state["humidity"] = self._parse_humidity(value)
        elif command == "OT":
            self._state["outdoor_temperature"] = self._parse_temperature(value)
        elif command == "MODE":
            self._state["mode"] = value
        elif command == "FAN":
            self._state["fan_mode"] = value
        elif command == "SH":
            self._state["heat_setpoint"] = self._parse_temperature(value)
        elif command == "SC":
            self._state["cool_setpoint"] = self._parse_temperature(value)
        elif command == "HVAC":
            self._state["hvac_status"] = value
        elif command == "HOLD":
            self._state["hold_status"] = value
        elif command == "FLTALM":
            self._state["filter_alarm"] = (value == "ON")
        elif command == "WPALM":
            self._state["water_panel_alarm"] = (value == "ON")
        elif command == "SYSALM":
            self._state["system_alarm"] = (value == "ON")
        elif command == "DEHALM":
            self._state["dehumidifier_alarm"] = (value == "ON")
        elif command == "ERROR":
            self._state["error_status"] = value

    async def async_initialize(self) -> bool:
        """Initialize the device with improved error handling and minimal required commands."""
        _LOGGER.debug("Beginning initialization for thermostat %s", self.address)
        try:
            # Query basic device information - this is essential.
            # v0.3.0: dropped the inter-command 0.5s sleep padding that used
            # to live here. Bus serialization happens inside the connection
            # layer (_send_lock + per-address future registry), so per-device
            # init can run in parallel across devices and the only required
            # pacing is the bus's own response time.
            _LOGGER.debug("Querying device information for thermostat %s", self.address)
            model_info = await self._send_command_with_retry(f"SN{self.address} ID?", retries=3)

            if not model_info:
                _LOGGER.error("Failed to query device information for thermostat %s", self.address)
                return False

            # Parse model info — also extracts the location-name prefix into
            # self.name and the model + firmware_version fields.
            _LOGGER.debug("Parsing model info: %s", model_info)
            self._parse_model_info(model_info)

            # v0.3.0 capability cache hit: if the coordinator has a cached
            # snapshot of capabilities for this address AND the model/firmware
            # match what we just read off the bus, skip EQUIPCONFIG?/CT? and
            # use the cached values. Saves ~1s per device on warm starts.
            if self._try_hydrate_from_capability_cache():
                _LOGGER.debug(
                    "Hydrated thermostat %s capabilities from cache (model=%s fw=%s)",
                    self.address, self.model, self.firmware_version,
                )
                # Skip ahead to the essential-state queries.
                return await self._async_query_initial_state()
            
            # Query essential device capabilities
            # These queries are important but we continue even if they fail
            try:
                # Equipment configuration
                _LOGGER.debug("Querying equipment configuration for thermostat %s", self.address)
                equip_config = await self._send_command_with_retry(
                    f"SN{self.address} EQUIPCONFIG?", 
                    retries=2,
                    allow_skip=True
                )
                if equip_config:
                    _LOGGER.debug("Received equipment config: %s", equip_config)
                    self._parse_equipment_config(equip_config)
                else:
                    _LOGGER.warning("No equipment configuration received for thermostat %s", self.address)
                    
                # Controller type
                _LOGGER.debug("Querying controller type for thermostat %s", self.address)
                controller_type = await self._send_command_with_retry(
                    f"SN{self.address} CT?", 
                    retries=2,
                    allow_skip=True
                )
                if controller_type:
                    _LOGGER.debug("Received controller type: %s", controller_type)
                    self._parse_controller_type(controller_type)
                else:
                    _LOGGER.warning("No controller type received for thermostat %s", self.address)
            except Exception as cap_ex:
                _LOGGER.error("Error querying capabilities for thermostat %s: %s", self.address, cap_ex)
                _LOGGER.error("Traceback: %s", traceback.format_exc())
                # Continue with default capabilities
                
            # Persist capabilities so the next startup can skip these
            # commands. Fire-and-forget; failure here doesn't affect init.
            await self._async_persist_capabilities()

            return await self._async_query_initial_state()

        except Exception as err:
            _LOGGER.error("Error initializing thermostat %s: %s", self.address, err)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            self.available = False
            return False

    def _try_hydrate_from_capability_cache(self) -> bool:
        """If the coordinator has a matching cached entry, hydrate self.

        Cache match requires both model and firmware_version to equal what
        we just parsed off ID? — protects against silent thermostat swaps
        on the same address.
        """
        entry_id = getattr(self.coordinator, "config_entry_id", None) or getattr(
            self.coordinator, "entry_id", None
        )
        getter = getattr(self.coordinator, "get_cached_capabilities", None)
        if entry_id is None or not callable(getter):
            return False
        cached = getter(entry_id, self.address)
        if not cached:
            return False
        if cached.get("model") != self.model:
            return False
        if cached.get("firmware_version") != self.firmware_version:
            return False
        caps = cached.get("capabilities") or {}
        if not caps:
            return False
        self.capabilities = dict(caps)
        return True

    async def _async_persist_capabilities(self) -> None:
        """Save the current capability snapshot via the coordinator."""
        entry_id = getattr(self.coordinator, "config_entry_id", None) or getattr(
            self.coordinator, "entry_id", None
        )
        saver = getattr(self.coordinator, "async_save_capability_cache_entry", None)
        if entry_id is None or not callable(saver):
            return
        try:
            await saver(
                entry_id, self.address, self.model, self.firmware_version,
                self.capabilities,
            )
        except Exception as save_ex:  # pragma: no cover  (storage layer)
            _LOGGER.debug(
                "Failed to persist capabilities for thermostat %s: %s",
                self.address, save_ex,
            )

    async def _async_query_initial_state(self) -> bool:
        """Query the bare-minimum runtime state needed to declare the device available.

        Split out of async_initialize so the capability-cache fast-path can
        skip the EQUIPCONFIG/CT queries while still doing the state queries
        (which always need to happen — state isn't cached).
        """
        try:
            _LOGGER.debug("Querying temperature for thermostat %s", self.address)
            temp_response = await self._send_command_with_retry(
                f"SN{self.address} TEMP?", retries=2,
            )
            if temp_response:
                self._process_state_response("TEMP", temp_response)
            else:
                _LOGGER.warning("No temperature response received for thermostat %s", self.address)

            mode_response = await self._send_command_with_retry(
                f"SN{self.address} MODE?", retries=2,
            )
            if mode_response:
                self._process_state_response("MODE", mode_response)
            else:
                _LOGGER.warning("No mode response received for thermostat %s", self.address)

            if self._state.get("mode") in ["HEAT", "AUTO", "EMHT"]:
                heat_sp = await self._send_command_with_retry(
                    f"SN{self.address} SH?", retries=2, allow_skip=True,
                )
                if heat_sp:
                    self._process_state_response("SH", heat_sp)

            if self._state.get("mode") in ["COOL", "AUTO"]:
                cool_sp = await self._send_command_with_retry(
                    f"SN{self.address} SC?", retries=2, allow_skip=True,
                )
                if cool_sp:
                    self._process_state_response("SC", cool_sp)
        except Exception as state_ex:
            _LOGGER.error("Error getting initial state for thermostat %s: %s", self.address, state_ex)
            _LOGGER.error("Traceback: %s", traceback.format_exc())

        self.available = True
        _LOGGER.debug("Completed initialization for thermostat %s", self.address)
        return True

    async def _async_send_with_jitter_retry(
        self,
        send_coro_factory,
        *,
        retries: int,
        description: str,
    ) -> Optional[str]:
        """Shared retry-with-jitter loop used by every command path.

        v0.3.0: unified primitive for both ``_query_with_retries`` (extracts
        value-after-equals) and ``_send_command_with_retry`` (returns raw
        response line). Callers pass a no-arg factory that returns the
        underlying coroutine — invoked fresh for each attempt so any
        per-attempt state (future registration, etc.) is reset cleanly.

        Jitter prevents N devices from synchronizing their retries when
        the bus is briefly saturated. Returns the first non-None response
        or None after ``retries+1`` total attempts. Never raises.
        """
        import random

        for attempt in range(retries + 1):
            try:
                response = await send_coro_factory()
            except Exception as ex:
                _LOGGER.debug(
                    "Exception during %s on thermostat %s (attempt %d): %s",
                    description, self.address, attempt + 1, ex,
                )
                response = None

            if response is not None:
                return response

            if attempt < retries:
                # 0.5s base + small jitter so devices don't synchronize their retries.
                await asyncio.sleep(0.5 + random.uniform(0, 0.2))

        return None

    async def _query_with_retries(
        self,
        command: str,
        *,
        retries: int,
        timeout: Optional[float] = None,
    ) -> Optional[str]:
        """Query the thermostat for a value (e.g. TEMP, MODE) with retries.

        ``command`` is the bare command code (no SN prefix, no '?'); the
        protocol layer formats and extracts the value-after-equals from the
        response. Returns the extracted value or None.
        """
        return await self._async_send_with_jitter_retry(
            lambda: self.protocol.execute_query_command(
                self.address, command, timeout=timeout
            ),
            retries=retries,
            description=f"query {command}",
        )

    def _note_optional_failure(self, cmd_name: str) -> None:
        """Track a consecutive failure on an optional command.

        Once we've timed out ``_UNSUPPORTED_THRESHOLD`` times in a row,
        mark the command as unsupported on this device and stop polling
        it. The state is per-device and per-connection: it'll get cleared
        on the next reconnect via ``reset_unsupported_commands``.
        """
        if cmd_name in self._unsupported_commands:
            return
        count = self._optional_failure_counts.get(cmd_name, 0) + 1
        self._optional_failure_counts[cmd_name] = count
        if count >= _UNSUPPORTED_THRESHOLD:
            self._unsupported_commands.add(cmd_name)
            self._optional_failure_counts.pop(cmd_name, None)
            _LOGGER.info(
                "Marking %s as unsupported on thermostat %s after %d consecutive timeouts; "
                "skipping in future polls until next reconnect.",
                cmd_name, self.address, count,
            )

    def reset_unsupported_commands(self) -> None:
        """Forget which optional commands were marked unsupported.

        Called on a fresh connection so a firmware update that adds
        support for a previously-failing command starts working again
        without requiring a HA restart. Also clears the circuit breaker
        — a freshly-connected bus should poll the device normally again.
        """
        if self._unsupported_commands or self._optional_failure_counts:
            _LOGGER.debug(
                "Resetting unsupported-command tracking for thermostat %s "
                "(was: %s)", self.address, sorted(self._unsupported_commands),
            )
        self._unsupported_commands.clear()
        self._optional_failure_counts.clear()
        self._consecutive_full_poll_failures = 0
        self._slow_keepalive_mode = False

    async def async_update(self, skip_essentials: bool = False) -> bool:
        """Update device state by querying the thermostat with error handling.

        ``skip_essentials`` (v0.4.1): when the coordinator's bulk SN0 pass
        already collected this address's essentials (TEMP/MODE/FAN/HVAC/HOLD/
        SH/SC) and bulk HUM/OT, skip those per-device queries entirely.
        Only the alarm-only optional pass runs, since alarms are still
        per-device (most firmwares NACK SN0 alarm queries). Circuit-breaker
        accounting is handled by the bulk path in that case.
        """
        if not self.available:
            return False

        success = False

        try:
            if skip_essentials:
                # Bulk pass handled essentials + HUM/OT. Mark success so
                # the coordinator's "any device updated?" check still sees
                # this device as fresh.
                success = True
            else:
                # Circuit-breaker: if recent cycles all timed out, drop to a
                # cheap TEMP-only keep-alive. A single successful response
                # exits the slow mode and the next cycle restores the full
                # query set.
                if self._slow_keepalive_mode:
                    essential_commands = [("TEMP", CMD_TEMP)]
                else:
                    essential_commands = [
                        ("TEMP", CMD_TEMP),
                        ("MODE", CMD_MODE),
                        ("FAN", CMD_FAN),
                        ("HVAC", CMD_HVAC),
                        ("HOLD", CMD_HOLD)
                    ]
                    # Add setpoint queries based on current mode
                    mode = self._state.get("mode")
                    if mode in [MODE_HEAT, MODE_AUTO, MODE_EMHT]:
                        essential_commands.append(("SH", CMD_SH))
                    if mode in [MODE_COOL, MODE_AUTO]:
                        essential_commands.append(("SC", CMD_SC))

                essential_responses_received = 0

                # Process essential commands with retries to mask transient bus glitches.
                for cmd_name, cmd in essential_commands:
                    response = await self._query_with_retries(cmd, retries=2)
                    if response is None:
                        _LOGGER.warning(
                            "No response after retries querying %s for device %s",
                            cmd, self.address,
                        )
                        continue
                    essential_responses_received += 1
                    if cmd_name == "TEMP":
                        self._state["temperature"] = self._parse_temperature(response)
                        success = True
                    elif cmd_name == "MODE":
                        self._state["mode"] = response
                        success = True
                    elif cmd_name == "FAN":
                        self._state["fan_mode"] = response
                    elif cmd_name == "HVAC":
                        self._state["hvac_status"] = response
                    elif cmd_name == "HOLD":
                        self._state["hold_status"] = response
                    elif cmd_name == "SH":
                        self._state["heat_setpoint"] = self._parse_temperature(response)
                    elif cmd_name == "SC":
                        self._state["cool_setpoint"] = self._parse_temperature(response)

                # Circuit-breaker update. Any successful essential response
                # resets the counter; a totally-silent cycle increments it.
                if essential_responses_received > 0:
                    if self._slow_keepalive_mode:
                        _LOGGER.info(
                            "Thermostat %s answered after slow-keepalive; "
                            "resuming full poll cycle",
                            self.address,
                        )
                    self._consecutive_full_poll_failures = 0
                    self._slow_keepalive_mode = False
                else:
                    self._consecutive_full_poll_failures += 1
                    if (
                        not self._slow_keepalive_mode
                        and self._consecutive_full_poll_failures
                        >= _CIRCUIT_BREAKER_THRESHOLD
                    ):
                        self._slow_keepalive_mode = True
                        _LOGGER.warning(
                            "Thermostat %s unresponsive for %d cycles; dropping "
                            "to slow TEMP-only keep-alive until it answers.",
                            self.address,
                            self._consecutive_full_poll_failures,
                        )

            # Optional items - one retry with shorter timeout, allow skipping.
            # Each command group is gated on its own user-controlled toggle
            # (config_flow options). Alarms default off because most firmwares
            # NACK them; the unsolicited COS-broadcast listener still picks
            # up real alarm transitions regardless.
            #
            # v0.4.1: HUM/OT are also handled by the bulk pass when
            # skip_essentials is set; only alarms remain per-device.
            optional_commands: List[Tuple[str, str]] = []
            if not skip_essentials:
                if self.monitor_humidity:
                    optional_commands.append(("HUM", CMD_HUM))
                if self.monitor_outdoor_temp:
                    optional_commands.append(("OT", CMD_OT))
            if self.monitor_alarms:
                optional_commands.extend([
                    ("FLTALM", "FLTALM"),
                    ("WPALM", "WPALM"),
                    ("SYSALM", "SYSALM"),
                    ("DEHALM", "DEHALM"),
                    ("ERROR", "ERROR"),
                ])

            for cmd_name, cmd in optional_commands:
                if cmd_name in self._unsupported_commands:
                    continue
                response = await self._query_with_retries(cmd, retries=1, timeout=2.0)
                if response is None:
                    self._note_optional_failure(cmd_name)
                    continue
                self._optional_failure_counts.pop(cmd_name, None)
                if cmd_name == "HUM":
                    self._state["humidity"] = self._parse_humidity(response)
                elif cmd_name == "OT":
                    self._state["outdoor_temperature"] = self._parse_temperature(response)
                elif cmd_name == "FLTALM":
                    self._state["filter_alarm"] = (response == "ON")
                elif cmd_name == "WPALM":
                    self._state["water_panel_alarm"] = (response == "ON")
                elif cmd_name == "SYSALM":
                    self._state["system_alarm"] = (response == "ON")
                elif cmd_name == "DEHALM":
                    self._state["dehumidifier_alarm"] = (response == "ON")
                elif cmd_name == "ERROR":
                    self._state["error_status"] = response

            return success
        except Exception as err:
            _LOGGER.error("Error updating thermostat %s: %s", self.address, err)
            return False

    async def _send_command_with_retry(
        self,
        command: str,
        retries: int = 2,
        timeout: float = 3.0,
        allow_skip: bool = False,
    ) -> Optional[str]:
        """Send a fully-formatted command (e.g. ``SN1 TEMP?`` or ``SN1 CR=NORMAL``).

        Returns the raw response line (with SN prefix and any embedded
        location name) or None if all attempts failed. The ``allow_skip``
        param is preserved for source compatibility but ALWAYS treated as
        True now — callers reliably handled None already, and an
        exception path here just creates noisy tracebacks for an
        already-handled timeout.

        Wraps _async_send_with_jitter_retry around the connection's
        async_send_command_with_response, which (since v0.3.0) uses the
        per-address future registry.
        """
        connection = self.protocol._connection
        if not hasattr(connection, "async_send_command_with_response"):
            # No connection support — nothing to send. Fail fast.
            _LOGGER.debug("Connection lacks async_send_command_with_response; %s skipped", command)
            return None

        return await self._async_send_with_jitter_retry(
            lambda: connection.async_send_command_with_response(command, timeout),
            retries=retries,
            description=f"send {command}",
        )

    async def async_send_command(self, command: str, value: Any = None) -> Any:
        """Send a command to the thermostat.
        
        Args:
            command: The command to send
            value: The value to set (for assignment commands)
            
        Returns:
            The command response or None if the command failed
        """
        if not self.available:
            _LOGGER.warning("Cannot send command to unavailable thermostat %s", self.address)
            return None
            
        try:
            if value is not None:
                return await self.protocol.execute_assignment_command(self.address, command, value)
            else:
                return await self.protocol.execute_query_command(self.address, command)
        except Exception as err:
            _LOGGER.error("Error sending command %s to thermostat %s: %s", command, self.address, err)
            return None

    async def async_enable_cos(self, flags: Set[str] = None) -> bool:
        """Enable COS — one-shot CR=NORMAL + bulk flag fire-and-forget.

        v0.3.1 fix: this is the method actually called from
        async_setup_cos_background (the v0.3.0 simplification was
        accidentally applied to a different unused helper). Also fixes
        the case-sensitivity bug where the integration sent ``c1=ON``
        but the device echoed ``C1=ON``, causing every per-flag verify
        to fail and "no COS flags were accepted" for every device.

        Approach: send CR=NORMAL once (the only hard requirement to
        receive broadcasts), then blast the flag set without verifying.
        Whatever the firmware accepts will start broadcasting; whatever
        it doesn't, the dispatcher-based message listener in the
        coordinator ignores unrecognized codes anyway.
        """
        if flags is None:
            flags = DEFAULT_COS_FLAGS

        # CR=NORMAL — the only hard requirement. Up to 2 attempts.
        cr_ok = False
        for _ in range(2):
            try:
                cr_result = await self.protocol.execute_assignment_command(
                    self.address, CMD_CR, "NORMAL"
                )
            except Exception as cr_ex:
                _LOGGER.debug(
                    "CR=NORMAL exception on thermostat %s: %s",
                    self.address, cr_ex,
                )
                cr_result = None
            if cr_result and "CR=NORMAL" in cr_result.upper():
                cr_ok = True
                break

        if not cr_ok:
            _LOGGER.info(
                "CR=NORMAL did not stick for thermostat %s — broadcasts "
                "may not arrive but polling still keeps state fresh.",
                self.address,
            )
            self._cos_enabled = False
            return False

        # Best-effort flag enable. Fire each, accept any echo with
        # "<FLAG>=ON" in it (case-insensitive — the 8870 firmware uppercases
        # the response code regardless of the request case).
        supported_flags: Set[str] = set()
        for flag in flags:
            await asyncio.sleep(THERMOSTAT_PROCESSING_TIME_MS / 1000)
            try:
                flag_result = await self.protocol.execute_assignment_command(
                    self.address, flag, "ON"
                )
            except Exception as flag_ex:
                _LOGGER.debug(
                    "Exception enabling COS flag %s on thermostat %s: %s",
                    flag, self.address, flag_ex,
                )
                continue

            if flag_result and f"{flag.upper()}=ON" in flag_result.upper():
                supported_flags.add(flag)

        # Either way we recorded CR=NORMAL ok, so broadcasts should flow.
        # Record whatever the firmware echoed back as "supported"; the
        # dispatcher listener routes any broadcast it receives regardless.
        self._cos_flags = supported_flags if supported_flags else set(flags)
        self._cos_enabled = True
        if supported_flags:
            _LOGGER.info(
                "Enabled COS with %d flag(s) on thermostat %s: %s",
                len(supported_flags), self.address, sorted(supported_flags),
            )
        else:
            _LOGGER.debug(
                "CR=NORMAL set on thermostat %s; no per-flag echoes matched "
                "but unsolicited broadcasts may still arrive.",
                self.address,
            )
        return True

    async def async_verify_cos(self) -> bool:
        """Verify that COS functionality is still active.
        
        Returns:
            True if COS functionality is active, False otherwise
        """
        if not self._cos_enabled or not self._cos_flags:
            return False
            
        try:
            # Check CR setting
            cr = await self.protocol.execute_query_command(self.address, CMD_CR)
            if cr != "NORMAL":
                _LOGGER.warning("Command Response not set to NORMAL on thermostat %s", self.address)
                return False
            
            # Check first enabled COS flag
            if len(self._cos_flags) > 0:
                flag = next(iter(self._cos_flags))
                flag_status = await self.protocol.execute_query_command(self.address, flag)
                if flag_status != "ON":
                    _LOGGER.warning("COS flag %s not enabled on thermostat %s", flag, self.address)
                    return False
            
            return True
            
        except Exception as err:
            _LOGGER.error("Error verifying COS on thermostat %s: %s", self.address, err)
            return False

    async def async_set_temperature(self, target_temp: float, mode: str = None) -> bool:
        """Set the target temperature on the thermostat.
        
        Args:
            target_temp: The target temperature to set
            mode: The mode to use for setting the temperature (heat or cool)
                  If None, use the current mode
                  
        Returns:
            True if the temperature was set successfully, False otherwise
        """
        if not self.available:
            return False
        
        if self.capabilities["controller_type"] != CONTROLLER_TYPE_TEMP:
            _LOGGER.error("Cannot set temperature on humidity controller")
            return False
            
        # If mode not specified, use current mode
        if mode is None:
            mode = self._state.get("mode")
            if mode is None or mode == MODE_OFF:
                _LOGGER.error("Cannot set temperature when mode is OFF or unknown")
                return False
        
        # Determine which setpoint to adjust based on mode
        if mode in [MODE_HEAT, MODE_EMHT]:
            command = CMD_SH
        elif mode == MODE_COOL:
            command = CMD_SC
        elif mode == MODE_AUTO:
            # In auto mode, adjust the setpoint closest to the target temperature
            heat_setpoint = self._state.get("heat_setpoint")
            cool_setpoint = self._state.get("cool_setpoint")
            
            if heat_setpoint is not None and cool_setpoint is not None:
                if abs(target_temp - heat_setpoint) <= abs(target_temp - cool_setpoint):
                    command = CMD_SH
                else:
                    command = CMD_SC
            elif heat_setpoint is not None:
                command = CMD_SH
            elif cool_setpoint is not None:
                command = CMD_SC
            else:
                _LOGGER.error("Cannot determine which setpoint to adjust in AUTO mode")
                return False
        else:
            _LOGGER.error("Cannot set temperature in mode %s", mode)
            return False
        
        # Send the command to set the temperature
        result = await self.protocol.execute_assignment_command(self.address, command, str(int(target_temp)))
        if result:
            # Update the local state
            if command == CMD_SH:
                self._state["heat_setpoint"] = target_temp
            else:
                self._state["cool_setpoint"] = target_temp
            return True
        
        return False

    async def async_set_hvac_mode(self, hvac_mode: str) -> bool:
        """Set the HVAC mode on the thermostat.
        
        Args:
            hvac_mode: The HVAC mode to set
            
        Returns:
            True if the mode was set successfully, False otherwise
        """
        if not self.available:
            return False
            
        # Map HA HVAC modes AND already-wire-format values to Aprilaire wire
        # modes. v0.4.2: case-insensitive to match the fan_mode fix — both
        # "heat" (HA enum) and "HEAT" (wire format) route to MODE_HEAT.
        mode_map = {
            "off": MODE_OFF,
            "heat": MODE_HEAT,
            "cool": MODE_COOL,
            "auto": MODE_AUTO,
            "heat_cool": MODE_AUTO,
            "emergency_heat": MODE_EMHT,
            "emht": MODE_EMHT,
        }

        mode = mode_map.get((hvac_mode or "").lower())
        if not mode:
            _LOGGER.error("Invalid HVAC mode: %s", hvac_mode)
            return False
            
        # Check if emergency heat is supported for heat pump systems
        if mode == MODE_EMHT and not self.capabilities.get("has_emergency_heat", False):
            _LOGGER.error("Emergency heat mode not supported on this device")
            return False
            
        # Send the command to set the mode
        result = await self.protocol.execute_assignment_command(self.address, CMD_MODE, mode)
        if result:
            self._state["mode"] = mode
            return True
            
        return False

    async def async_set_fan_mode(self, fan_mode: str) -> bool:
        """Set the fan mode on the thermostat.

        Accepts either HA fan-mode constants (``"auto"``, ``"on"``,
        ``"circulate"``) or Aprilaire wire values (``"AUTO"``, ``"ON"``,
        ``"CIRC"``). Case-insensitive: a switch entity calling with
        ``"ON"`` and the climate entity calling with ``"on"`` both work.

        v0.4.2 bug fix: the previous case-sensitive lowercase-only map
        silently rejected every call from the fan-override switch
        (``switch.py`` passes uppercase ``"ON"``/``"AUTO"``), and the
        climate path stored an HA-format string in ``_state`` that didn't
        match wire-format echoes from bulk polling.
        """
        if not self.available:
            return False

        # Accept any case + both HA-format and wire-format inputs, normalize
        # to the uppercase Aprilaire wire value.
        normalized_map = {
            "auto": "AUTO",
            "on": "ON",
            "circulate": "CIRC",
            "circ": "CIRC",
        }
        wire_value = normalized_map.get((fan_mode or "").lower())
        if wire_value is None:
            _LOGGER.error("Invalid fan mode: %s", fan_mode)
            return False

        result = await self.protocol.execute_assignment_command(
            self.address, CMD_FAN, wire_value
        )
        if result:
            # Store the wire value so subsequent bulk-poll echoes match
            # and switch.is_on (which compares against "ON") sees the
            # change immediately.
            self._state["fan_mode"] = wire_value
            return True

        return False

    async def async_set_hold(self, hold_status: bool) -> bool:
        """Set the hold status (network override) on the thermostat.

        Args:
            hold_status: True to enable hold, False to disable

        Returns:
            True if the hold status was set successfully, False otherwise
        """
        if not self.available:
            return False

        value = "ON" if hold_status else "OFF"
        result = await self.protocol.execute_assignment_command(self.address, CMD_HOLD, value)
        if result:
            self._state["hold_status"] = value
            return True

        return False

    # v0.4.0: actual protocol implementations for the services that have been
    # registered (and dispatching signals) since v0.2.0 but had no subscriber
    # on the entity side. Climate entity now subscribes in async_added_to_hass
    # and routes to these methods.

    async def async_set_text_message(self, text: str, message_type: str) -> bool:
        """Write a text message to the thermostat's display.

        ``message_type`` is one of: ``pmes1``..``pmes4`` (rotating permanent
        messages displayed for ~2-5s each) or ``tmpmes`` (temporary "user
        reset" message that flashes the backlight 10× and persists until
        the ENTER button is pressed).

        Maps to PMES1/PMES2/PMES3/PMES4/TMPMES commands per the
        programmer's manual. Text is truncated to 31 chars to fit the
        two-line display (16 + 15 chars per 8800 docs; 32 per 8870 docs —
        31 is the safe intersection).
        """
        if not self.available:
            return False
        if text is None:
            text = ""
        # Trim — display is 31 chars max (8800) / 32 (8870); spaces count.
        text = text[:31]
        cmd_map = {
            "tmpmes": "TMPMES",
            "pmes1": "PMES1",
            "pmes2": "PMES2",
            "pmes3": "PMES3",
            "pmes4": "PMES4",
        }
        cmd = cmd_map.get(message_type)
        if cmd is None:
            _LOGGER.error("Unknown message_type %r for thermostat %s", message_type, self.address)
            return False
        result = await self.protocol.execute_assignment_command(
            self.address, cmd, text,
        )
        return result is not None

    async def async_set_backlight(self) -> bool:
        """Turn on the thermostat's backlight for 10 seconds.

        Maps to ``BLTON`` (the only backlight control command per the
        programmer's manual). The ``duration`` parameter from the service
        schema is accepted but has no protocol effect — the 8870 always
        holds the backlight on for 10s after BLTON.
        """
        if not self.available:
            return False
        # BLTON takes no value; use the connection layer directly so we
        # don't append "=ON" via execute_assignment_command.
        result = await self._send_command_with_retry(
            f"SN{self.address} BLTON", retries=1, allow_skip=True,
        )
        return result is not None

    async def async_reset_filter(self) -> bool:
        """Clear the filter-change alarm.

        Maps to ``FLTALM=OFF`` per the programmer's manual: ``When an
        assignment command is received with the value parameter set to
        OFF and the specified alarm is active, the respective alarm is
        cleared and reset``.
        """
        if not self.available:
            return False
        result = await self.protocol.execute_assignment_command(
            self.address, "FLTALM", "OFF",
        )
        if result is not None:
            self._state["filter_alarm"] = False
            return True
        return False

    async def async_set_lockout(
        self,
        fan_lockout: Optional[int] = None,
        mode_lockout: Optional[int] = None,
        setpoint_lockout: Optional[int] = None,
        network_lockout: Optional[int] = None,
        lockout_time: Optional[int] = None,
        lockout_limit: Optional[int] = None,
    ) -> bool:
        """Configure keypad lockout settings.

        Maps to the FANLK/MODELK/UPDNLK/NETLK/LKTIME/LKLIMIT commands per
        the programmer's manual. Any None parameter is left unchanged
        (no command sent for that field).
        """
        if not self.available:
            return False
        any_ok = False
        for cmd, value in (
            ("FANLK", fan_lockout),
            ("MODELK", mode_lockout),
            ("UPDNLK", setpoint_lockout),
            ("NETLK", network_lockout),
            ("LKTIME", lockout_time),
            ("LKLIMIT", lockout_limit),
        ):
            if value is None:
                continue
            result = await self.protocol.execute_assignment_command(
                self.address, cmd, str(value),
            )
            if result is not None:
                any_ok = True
        return any_ok

    async def async_configure_cos(self, flags: List[str]) -> bool:
        """Re-issue CR=NORMAL + the requested COS flag set.

        Allows the user to override the default flag set at runtime via
        the configure_cos service. Uses the same one-shot pattern as the
        startup COS setup: CR=NORMAL, then bulk flag fire-and-forget.
        """
        if not self.available:
            return False
        return await self.async_enable_cos(flags=set(flags))

    def get_state(self) -> Dict[str, Any]:
        """Return the current state of the device.
        
        Returns:
            A dictionary containing the current state
        """
        # Make sure we're always returning a valid dictionary, 
        # never None or something that's not a dictionary
        if not hasattr(self, "_state") or self._state is None:
            return {}
        
        return self._state.copy()

    def get_capabilities(self) -> Dict[str, Any]:
        """Return the capabilities of the device.
        
        Returns:
            A dictionary containing device capabilities
        """
        return self.capabilities.copy()

    def is_cos_enabled(self) -> bool:
        """Return whether Change of State (COS) is enabled.
        
        Returns:
            True if COS is enabled, False otherwise
        """
        return self._cos_enabled

    def get_cos_flags(self) -> Set[str]:
        """Return the set of enabled COS flags.
        
        Returns:
            A set of enabled COS flags
        """
        return self._cos_flags.copy()

    def process_cos_message(self, command: str, value: Any) -> bool:
        """Process a Change of State message from the thermostat.
        
        Args:
            command: The command that changed
            value: The new value
            
        Returns:
            True if the message was processed successfully, False otherwise
        """
        try:
            if command == CMD_TEMP:
                self._state["temperature"] = self._parse_temperature(value)
            elif command == CMD_HUM:
                self._state["humidity"] = self._parse_humidity(value)
            elif command == CMD_OT:
                self._state["outdoor_temperature"] = self._parse_temperature(value)
            elif command == CMD_SH:
                self._state["heat_setpoint"] = self._parse_temperature(value)
            elif command == CMD_SC:
                self._state["cool_setpoint"] = self._parse_temperature(value)
            elif command == CMD_MODE:
                self._state["mode"] = value
            elif command == CMD_FAN:
                self._state["fan_mode"] = value
            elif command == CMD_HVAC:
                self._state["hvac_status"] = value
            elif command == CMD_HOLD:
                self._state["hold_status"] = value
            else:
                # Handle other COS messages (alarms, errors, etc.)
                return self._handle_specialized_cos_message(command, value)
                
            return True
            
        except Exception as err:
            _LOGGER.error("Error processing COS message %s=%s for thermostat %s: %s", 
                         command, value, self.address, err)
            return False

    def _parse_model_info(self, model_info: str) -> None:
        """Parse model information (and any location-name prefix) from ID response.

        Two response shapes from the 8870:
          ``SN1 MODEL# 8870 REV: V1.2 - RPC 2002``                 (no location name set)
          ``SN1Master Bedroom  MODEL# 8870 REV: V1.2 - RPC 2002``  (location name set; two spaces before MODEL#)

        We extract the location name (if any) into ``self.name`` so HA's
        device registry can pick it up via DeviceInfo — no separate bus probe
        needed, the bytes were already on the wire as part of the ID query
        every device runs during init.
        """
        try:
            import re
            # Strip "SN<addr>" prefix and capture any location name that sits
            # between it and the MODEL# marker (separated by 2+ spaces, which
            # is how the thermostat formats the boundary).
            location_match = re.match(
                rf"^SN{self.address}(.*?)\s{{2,}}MODEL#", model_info
            )
            if location_match:
                location_name = location_match.group(1).strip()
                if location_name and location_name != self.name:
                    self.name = location_name
                    _LOGGER.info(
                        "Discovered location name for thermostat %s: %r",
                        self.address,
                        self.name,
                    )

            # Strip the SN<addr><name> prefix before parsing model/firmware so
            # they don't get shifted by the location-name tokens. Look for the
            # MODEL# marker and parse from there.
            marker = model_info.find("MODEL#")
            payload = model_info[marker:] if marker >= 0 else model_info
            parts = payload.split()
            # Expected: ["MODEL#", "8870", "REV:", "V1.2", "-", "RPC", "2002"]
            if len(parts) >= 4 and parts[0] == "MODEL#":
                self.model = parts[1]
                self.firmware_version = parts[3].lstrip("V").rstrip(":")
        except Exception as err:
            _LOGGER.error("Error parsing model info for thermostat %s: %s", self.address, err)

    def _parse_equipment_config(self, config: str) -> None:
        """Parse equipment configuration from EQUIPCONFIG command response.
        
        Args:
            config: The response from the EQUIPCONFIG command
        """
        # Expected format: wxyz where each character is 0 or 1
        try:
            if len(config) >= 4:
                # Parse is_heat_pump (4th digit)
                self.capabilities["is_heat_pump"] = (config[3] == "0")
                self.capabilities["equipment_type"] = (
                    EQUIPMENT_TYPE_HEAT_PUMP if self.capabilities["is_heat_pump"] 
                    else EQUIPMENT_TYPE_HEAT_COOL
                )
                
                # Parse multi-stage capability (3rd digit)
                is_multi_stage = (config[2] == "1")
                if is_multi_stage:
                    self.capabilities["stages_heat"] = 2
                    self.capabilities["stages_cool"] = 2
                    
                # Set emergency heat capability for heat pumps
                if self.capabilities["is_heat_pump"]:
                    self.capabilities["has_emergency_heat"] = True
        except Exception as err:
            _LOGGER.error("Error parsing equipment config for thermostat %s: %s", self.address, err)

    def _parse_controller_type(self, controller_type: str) -> None:
        """Parse controller type from CT command response.
        
        Args:
            controller_type: The response from the CT command
        """
        try:
            # Extract the value part (after "CT=")
            if "CT=" in controller_type:
                value = controller_type.split("CT=")[1].strip()
                # Now convert just the extracted value to int
                ct = int(value)
                self.capabilities["controller_type"] = ct
        except Exception as err:
            _LOGGER.error("Error parsing controller type for thermostat %s: %s", self.address, err)

    def _parse_support_modules(self, modules_info: str) -> None:
        """Parse support modules information from RSM command response.
        
        Args:
            modules_info: The response from the RSM command
        """
        # Expected format: "M1:XX,XX M2:XX,XX" where XX is sensor type
        support_modules = []
        
        try:
            modules = modules_info.split()
            for module in modules:
                if ":" in module:
                    address, sensors = module.split(":")
                    
                    # Extract module address from "M1", "M2", etc.
                    if address.startswith("M") and len(address) > 1:
                        module_address = int(address[1:])
                        
                        # Parse sensor types
                        if "," in sensors:
                            sensor_types = sensors.split(",")
                            support_modules.append({
                                "address": module_address,
                                "sensor1_type": sensor_types[0],
                                "sensor2_type": sensor_types[1] if len(sensor_types) > 1 else None
                            })
                            
                            # Check for humidity sensors
                            if "CH" in sensor_types or "RH" in sensor_types:
                                self.capabilities["has_humidifier"] = True
                                self.capabilities["has_dehumidifier"] = True
            
            self.capabilities["support_modules"] = support_modules
        except Exception as err:
            _LOGGER.error("Error parsing support modules for thermostat %s: %s", self.address, err)
        
    def _parse_temperature(self, temp_str: str) -> Optional[float]:
        """Parse temperature value from response.
        
        Args:
            temp_str: The temperature string from a response
            
        Returns:
            Parsed temperature as a float, or None if parsing failed or placeholder
        """
        try:
            # Check for placeholder values
            if temp_str in ["--F", "--C", "--"]:
                return None
                
            # Expected format: "72F" or "22C"
            if temp_str.endswith("F") or temp_str.endswith("C"):
                return float(temp_str[:-1])
            else:
                return float(temp_str)
        except (ValueError, TypeError):
            _LOGGER.error("Error parsing temperature value: %s", temp_str)
            return None

    def _parse_humidity(self, humidity_str: str) -> Optional[int]:
        """Parse humidity value from response.
        
        Args:
            humidity_str: The humidity string from a response
            
        Returns:
            Parsed humidity as an integer, or None if parsing failed or placeholder
        """
        try:
            # Check for placeholder values
            if humidity_str in ["--%", "--"]:
                return None
                
            # Expected format: "45%" or just "45"
            if humidity_str.endswith("%"):
                return int(humidity_str[:-1])
            else:
                return int(humidity_str)
        except (ValueError, TypeError):
            _LOGGER.error("Error parsing humidity value: %s", humidity_str)
            return None

    def _handle_specialized_cos_message(self, command: str, value: Any) -> bool:
        """Handle specialized COS messages (alarms, errors, etc.).
        
        Args:
            command: The command that changed
            value: The new value
            
        Returns:
            True if the message was processed successfully, False otherwise
        """
        try:
            # Handle alarm status changes
            if command == "FLTALM":
                self._state["filter_alarm"] = (value == "ON")
            elif command == "WPALM":
                self._state["water_panel_alarm"] = (value == "ON")
            elif command == "SYSALM":
                self._state["system_alarm"] = (value == "ON")
            elif command == "DEHALM":
                self._state["dehumidifier_alarm"] = (value == "ON")
            # Handle error status changes
            elif command == "ERROR":
                self._state["error_status"] = value
            else:
                _LOGGER.debug("Unhandled COS message %s=%s for thermostat %s", 
                             command, value, self.address)
                return False
                
            return True
        except Exception as err:
            _LOGGER.error("Error handling specialized COS message %s=%s for thermostat %s: %s", 
                         command, value, self.address, err)
            return False

class AprilaireDeviceManager:
    """Manager for Aprilaire thermostat devices."""
    
    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        protocol: AprilaireProtocol,
        device_names: Optional[Dict[str, str]] = None,
        monitor_alarms: bool = False,
        monitor_humidity: bool = True,
        monitor_outdoor_temp: bool = True,
    ) -> None:
        """Initialize the device manager.

        Args:
            coordinator: The data update coordinator
            protocol: The protocol implementation for command execution
            device_names: Optional mapping of address (as string) to a location
                name discovered during config flow. Used to pre-populate each
                device's ``name`` so HA's Name & Assign UI shows the user's
                names from the start.
            monitor_alarms / monitor_humidity / monitor_outdoor_temp:
                Forwarded to each new AprilaireDevice; gate which optional
                command groups are polled. See AprilaireDevice.__init__ for
                rationale on defaults.
        """
        self.coordinator = coordinator
        self.protocol = protocol
        self.devices = {}  # address -> AprilaireDevice
        self.device_names: Dict[str, str] = dict(device_names or {})
        self.monitor_alarms = monitor_alarms
        self.monitor_humidity = monitor_humidity
        self.monitor_outdoor_temp = monitor_outdoor_temp

    async def async_discover_devices(self, connection) -> List[int]:
        """Discover thermostats on the network.
        
        Args:
            connection: The connection to use for discovery
            
        Returns:
            List of discovered thermostat addresses
        """
        discovered_addresses = []
        
        try:
            # Clear any previous received messages
            if hasattr(connection, 'get_received_messages'):
                connection.get_received_messages()
                
            # Send global discovery command (SN?)
            await connection.async_send_command("SN?")
            
            # Wait longer for responses
            await asyncio.sleep(3)
            
            # Get all received messages
            if hasattr(connection, 'get_received_messages'):
                messages = connection.get_received_messages()
                _LOGGER.debug("Discovery responses received: %s", messages)
            else:
                # Fallback if the method doesn't exist on this connection object
                _LOGGER.warning("Connection object does not support get_received_messages")
                return []
                
            # Parse addresses from responses
            for message in messages:
                if isinstance(message, str) and message.startswith("SN"):
                    try:
                        # Extract address from "SN1", "SN2", etc.
                        address_str = message[2:].strip()
                        if address_str and address_str.isdigit():
                            address = int(address_str)
                            discovered_addresses.append(address)
                    except ValueError:
                        pass
                        
            _LOGGER.info("Discovered %d thermostats: %s", 
                        len(discovered_addresses), discovered_addresses)
                        
        except Exception as err:
            _LOGGER.error("Error discovering thermostats: %s", err)
            
        return discovered_addresses

    async def async_setup_device(self, address: int) -> Optional[AprilaireDevice]:
        """Set up a thermostat device at the specified address.
        
        Args:
            address: The thermostat address
            
        Returns:
            The initialized device, or None if setup failed
        """
        # Check if device already exists
        if address in self.devices:
            return self.devices[address]
            
        # Create new device, seeding name from config-flow discovery if present.
        preset_name = self.device_names.get(str(address))
        device = AprilaireDevice(
            address, self.coordinator, self.protocol,
            preset_name=preset_name,
            monitor_alarms=self.monitor_alarms,
            monitor_humidity=self.monitor_humidity,
            monitor_outdoor_temp=self.monitor_outdoor_temp,
        )
        
        # Initialize device
        if await device.async_initialize():
            self.devices[address] = device
            _LOGGER.info("Successfully initialized thermostat %s", address)
            return device
        else:
            _LOGGER.error("Failed to initialize thermostat %s", address)
            return None
    
    async def update_placeholder_device(self, address: int, real_device) -> None:
        """Update a placeholder device with a fully initialized one.
        
        Args:
            address: The device address
            real_device: The fully initialized device
        """
        # Check if we have an existing placeholder
        if address in self.devices and not self.devices[address].protocol:
            # Update the placeholder with real device properties
            self.devices[address].update_from_real_device(real_device)
            _LOGGER.debug("Updated placeholder device %s with real device", address)
        else:
            # Replace the device
            self.devices[address] = real_device
            _LOGGER.debug("Replaced placeholder device %s with real device", address)

    async def async_update_all(self) -> None:
        """Update all managed devices."""
        for address, device in self.devices.items():
            try:
                await device.async_update()
            except Exception as err:
                _LOGGER.error("Error updating thermostat %s: %s", address, err)

    def get_device(self, address: int) -> Optional[AprilaireDevice]:
        """Get device by address.
        
        Args:
            address: The thermostat address
            
        Returns:
            The device instance, or None if not found
        """
        return self.devices.get(address)

    def get_all_devices(self) -> List[AprilaireDevice]:
        """Get all managed devices.
        
        Returns:
            List of all devices
        """
        return list(self.devices.values())

