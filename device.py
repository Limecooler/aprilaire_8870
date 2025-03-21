"""Device representation of Aprilaire 8870 thermostats."""
import asyncio
import logging
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
    FAN_AUTO,
    FAN_ON,
    FAN_CIRC,
    CONTROLLER_TYPE_TEMP,
    CONTROLLER_TYPE_HUMID,
    DEFAULT_COS_FLAGS,
    EQUIPMENT_TYPE_HEAT_COOL,
    EQUIPMENT_TYPE_HEAT_PUMP,
)
from .protocol import AprilaireProtocol

_LOGGER = logging.getLogger(__name__)


class AprilaireDevice:
    """Representation of an Aprilaire thermostat device."""

    def __init__(
        self, 
        address: int, 
        coordinator: DataUpdateCoordinator,
        protocol: AprilaireProtocol
    ) -> None:
        """Initialize the Aprilaire device.
        
        Args:
            address: The RS-485 network address of the thermostat (1-64)
            coordinator: The data update coordinator
            protocol: The protocol implementation for command execution
        """
        self.address = address
        self.coordinator = coordinator
        self.protocol = protocol
        self.name = f"Aprilaire {address}"
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

    async def async_initialize(self) -> bool:
        """Initialize the device by querying its capabilities and current state.
        
        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            # Query basic device information
            model_info = await self.protocol.execute_query_command(self.address, CMD_ID)
            if not model_info:
                _LOGGER.error("Failed to query device information for thermostat %s", self.address)
                return False
                
            # Parse model info
            self._parse_model_info(model_info)
            
            # Query equipment configuration
            equip_config = await self.protocol.execute_query_command(self.address, CMD_EQUIPCONFIG)
            if equip_config:
                self._parse_equipment_config(equip_config)
                
            # Query controller type
            controller_type = await self.protocol.execute_query_command(self.address, CMD_CT)
            if controller_type:
                self._parse_controller_type(controller_type)
                
            # Query support modules if available
            support_modules = await self.protocol.execute_query_command(self.address, CMD_RSM)
            if support_modules:
                self._parse_support_modules(support_modules)
                
            # Get initial state values
            await self.async_update()
            
            # Enable COS functionality
            await self.async_enable_cos(DEFAULT_COS_FLAGS)
            
            self.available = True
            return True
            
        except Exception as err:
            _LOGGER.error("Error initializing thermostat %s: %s", self.address, err)
            self.available = False
            return False

    async def async_update(self) -> bool:
        """Update device state by querying the thermostat.
        
        Returns:
            True if update was successful, False otherwise
        """
        try:
            # Query current temperature
            if self.capabilities["controller_type"] == CONTROLLER_TYPE_TEMP:
                temp = await self.protocol.execute_query_command(self.address, CMD_TEMP)
                if temp is not None:
                    self._state["temperature"] = self._parse_temperature(temp)
            
            # Query current humidity if available
            if self.capabilities["controller_type"] == CONTROLLER_TYPE_HUMID:
                hum = await self.protocol.execute_query_command(self.address, CMD_HUM)
                if hum is not None:
                    self._state["humidity"] = self._parse_humidity(hum)
            else:
                # Try to get built-in humidity sensor reading
                hum = await self.protocol.execute_query_command(self.address, CMD_BIHUM)
                if hum is not None:
                    self._state["humidity"] = self._parse_humidity(hum)
            
            # Query outdoor temperature if available
            ot = await self.protocol.execute_query_command(self.address, CMD_OT)
            if ot is not None:
                self._state["outdoor_temperature"] = self._parse_temperature(ot)
            
            # Query current mode
            mode = await self.protocol.execute_query_command(self.address, CMD_MODE)
            if mode is not None:
                self._state["mode"] = mode
            
            # Query current fan mode
            fan = await self.protocol.execute_query_command(self.address, CMD_FAN)
            if fan is not None:
                self._state["fan_mode"] = fan
            
            # Query setpoints based on controller type
            if self.capabilities["controller_type"] == CONTROLLER_TYPE_TEMP:
                # Query heat setpoint if in heat or auto mode
                if self._state["mode"] in [MODE_HEAT, MODE_AUTO, MODE_EMHT]:
                    sh = await self.protocol.execute_query_command(self.address, CMD_SH)
                    if sh is not None:
                        self._state["heat_setpoint"] = self._parse_temperature(sh)
                
                # Query cool setpoint if in cool or auto mode
                if self._state["mode"] in [MODE_COOL, MODE_AUTO]:
                    sc = await self.protocol.execute_query_command(self.address, CMD_SC)
                    if sc is not None:
                        self._state["cool_setpoint"] = self._parse_temperature(sc)
            
            # Query HVAC status (relay outputs)
            hvac = await self.protocol.execute_query_command(self.address, CMD_HVAC)
            if hvac is not None:
                self._state["hvac_status"] = hvac
            
            # Query hold status
            hold = await self.protocol.execute_query_command(self.address, CMD_HOLD)
            if hold is not None:
                self._state["hold_status"] = hold
            
            # Update alarm statuses (filter, water panel, etc.)
            await self._update_alarm_statuses()
            
            # Update error status
            await self._update_error_status()
            
            self.available = True
            return True
            
        except Exception as err:
            _LOGGER.error("Error updating thermostat %s: %s", self.address, err)
            self.available = False
            return False

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
        """Enable Change of State (COS) functionality for real-time updates.
        
        Args:
            flags: Set of COS flags to enable. If None, use defaults.
            
        Returns:
            True if COS was enabled successfully, False otherwise
        """
        if flags is None:
            flags = DEFAULT_COS_FLAGS
            
        try:
            # Set Command Response (CR) to NORMAL to enable COS
            cr_result = await self.protocol.execute_assignment_command(self.address, CMD_CR, "NORMAL")
            if not cr_result:
                _LOGGER.error("Failed to set Command Response to NORMAL on thermostat %s", self.address)
                return False
            
            # Enable each specified COS flag
            success = True
            for flag in flags:
                result = await self.protocol.execute_assignment_command(self.address, flag, "ON")
                if not result:
                    _LOGGER.error("Failed to enable COS flag %s on thermostat %s", flag, self.address)
                    success = False
                else:
                    self._cos_flags.add(flag)
            
            self._cos_enabled = success
            return success
            
        except Exception as err:
            _LOGGER.error("Error enabling COS on thermostat %s: %s", self.address, err)
            self._cos_enabled = False
            return False

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
            
        # Map Home Assistant HVAC modes to Aprilaire modes
        mode_map = {
            "off": MODE_OFF,
            "heat": MODE_HEAT,
            "cool": MODE_COOL,
            "auto": MODE_AUTO,
            "heat_cool": MODE_AUTO,
            "emergency_heat": MODE_EMHT,
        }
        
        mode = mode_map.get(hvac_mode)
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
        
        Args:
            fan_mode: The fan mode to set
            
        Returns:
            True if the fan mode was set successfully, False otherwise
        """
        if not self.available:
            return False
            
        # Map Home Assistant fan modes to Aprilaire fan modes
        mode_map = {
            "auto": FAN_AUTO,
            "on": FAN_ON,
            "circulate": FAN_CIRC,
        }
        
        mode = mode_map.get(fan_mode)
        if not mode:
            _LOGGER.error("Invalid fan mode: %s", fan_mode)
            return False
            
        # Send the command to set the fan mode
        result = await self.protocol.execute_assignment_command(self.address, CMD_FAN, mode)
        if result:
            self._state["fan_mode"] = mode
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

    def get_state(self) -> Dict[str, Any]:
        """Return the current state of the device.
        
        Returns:
            A dictionary containing the current state
        """
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
        """Parse model information from ID command response.
        
        Args:
            model_info: The response from the ID command
        """
        # Expected format: "MODEL# 8870 REV: x.x RPC yyyy"
        try:
            parts = model_info.split()
            if len(parts) >= 6:
                self.model = parts[1]
                self.firmware_version = parts[3]
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
            ct = int(controller_type)
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
            Parsed temperature as a float, or None if parsing failed
        """
        try:
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
            Parsed humidity as an integer, or None if parsing failed
        """
        try:
            # Expected format: "45%" or just "45"
            if humidity_str.endswith("%"):
                return int(humidity_str[:-1])
            else:
                return int(humidity_str)
        except (ValueError, TypeError):
            _LOGGER.error("Error parsing humidity value: %s", humidity_str)
            return None

    async def _update_alarm_statuses(self) -> None:
        """Update the alarm statuses (filter, water panel, system, dehumidifier)."""
        try:
            # Query filter alarm status
            fltalm = await self.protocol.execute_query_command(self.address, "FLTALM")
            if fltalm is not None:
                self._state["filter_alarm"] = (fltalm == "ON")
                
            # Query water panel alarm status
            wpalm = await self.protocol.execute_query_command(self.address, "WPALM")
            if wpalm is not None:
                self._state["water_panel_alarm"] = (wpalm == "ON")
                
            # Query system alarm status
            sysalm = await self.protocol.execute_query_command(self.address, "SYSALM")
            if sysalm is not None:
                self._state["system_alarm"] = (sysalm == "ON")
                
            # Query dehumidifier alarm status
            dehalm = await self.protocol.execute_query_command(self.address, "DEHALM")
            if dehalm is not None:
                self._state["dehumidifier_alarm"] = (dehalm == "ON")
        except Exception as err:
            _LOGGER.error("Error updating alarm statuses for thermostat %s: %s", self.address, err)

    async def _update_error_status(self) -> None:
        """Update the error status."""
        try:
            error = await self.protocol.execute_query_command(self.address, "ERROR")
            if error is not None:
                self._state["error_status"] = error
        except Exception as err:
            _LOGGER.error("Error updating error status for thermostat %s: %s", self.address, err)

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
    
    def __init__(self, coordinator: DataUpdateCoordinator, protocol: AprilaireProtocol) -> None:
        """Initialize the device manager.
        
        Args:
            coordinator: The data update coordinator
            protocol: The protocol implementation for command execution
        """
        self.coordinator = coordinator
        self.protocol = protocol
        self.devices = {}  # address -> AprilaireDevice

    async def async_discover_devices(self, connection) -> List[int]:
        """Discover thermostats on the network.
        
        Args:
            connection: The connection to use for discovery
            
        Returns:
            List of discovered thermostat addresses
        """
        discovered_addresses = []
        
        try:
            # Send global discovery command (SN?)
            await connection.async_send_command("SN?")
            
            # Wait for responses
            await asyncio.sleep(3)
            
            # Get responses from the connection
            messages = connection.get_received_messages()
            
            # Parse addresses from responses
            for message in messages:
                if message.startswith("SN"):
                    try:
                        # Extract address from "SN1", "SN2", etc.
                        address = int(message[2:])
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
            
        # Create new device
        device = AprilaireDevice(address, self.coordinator, self.protocol)
        
        # Initialize device
        if await device.async_initialize():
            self.devices[address] = device
            _LOGGER.info("Successfully initialized thermostat %s", address)
            return device
        else:
            _LOGGER.error("Failed to initialize thermostat %s", address)
            return None

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

