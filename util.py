"""Utility functions for the Aprilaire 8870 thermostat integration."""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from homeassistant.const import (
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)

from .const import (
    VALID_HVAC_MODES,
    VALID_FAN_MODES,
    MIN_TEMP_F,
    MAX_TEMP_F,
    MIN_TEMP_C,
    MAX_TEMP_C,
    MIN_HUMIDITY,
    MAX_HUMIDITY,
)

_LOGGER = logging.getLogger(__name__)

# Regular expression patterns for response validation
RESPONSE_PATTERN = re.compile(r"SN(\d+)\s+([A-Z0-9]+)=(.+)")
COS_PATTERN = re.compile(r"SN(\d+)\s+([A-Z0-9]+)=(.+)")
HVAC_STATUS_PATTERN = re.compile(r"G([\+\-])Y1([\+\-])W1([\+\-])Y2([\+\-])W2([\+\-])B([\+\-])O([\+\-])")


def format_address(address: Union[int, str]) -> str:
    """Format thermostat address to ensure proper format.
    
    For addresses 1-9, prepend with a zero to create a two-digit address
    for consistent formatting in commands.
    
    Args:
        address: The thermostat address (1-64)
        
    Returns:
        Formatted address string
    """
    address_int = int(address)
    if address_int < 1 or address_int > 64:
        raise ValueError(f"Invalid thermostat address: {address}. Must be between 1 and 64.")
    
    return f"{address_int:02d}" if address_int < 10 else str(address_int)


def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    """Convert temperature between Fahrenheit and Celsius.
    
    Args:
        value: Temperature value to convert
        from_unit: Source unit (TEMP_FAHRENHEIT or TEMP_CELSIUS)
        to_unit: Target unit (TEMP_FAHRENHEIT or TEMP_CELSIUS)
        
    Returns:
        Converted temperature value
    """
    if from_unit == to_unit:
        return value
    
    if from_unit == TEMP_FAHRENHEIT and to_unit == TEMP_CELSIUS:
        return round((value - 32) * 5 / 9, 1)
    
    if from_unit == TEMP_CELSIUS and to_unit == TEMP_FAHRENHEIT:
        return round((value * 9 / 5) + 32, 1)
    
    raise ValueError(f"Invalid temperature conversion: {from_unit} to {to_unit}")


def validate_temperature(value: float, unit: str) -> bool:
    """Validate temperature value based on unit and allowed ranges.
    
    Args:
        value: Temperature value to validate
        unit: Temperature unit (TEMP_FAHRENHEIT or TEMP_CELSIUS)
        
    Returns:
        True if valid, False otherwise
    """
    if unit == TEMP_FAHRENHEIT:
        return MIN_TEMP_F <= value <= MAX_TEMP_F
    
    if unit == TEMP_CELSIUS:
        return MIN_TEMP_C <= value <= MAX_TEMP_C
    
    return False


def validate_humidity(value: int) -> bool:
    """Validate humidity value based on allowed range.
    
    Args:
        value: Humidity value to validate
        
    Returns:
        True if valid, False otherwise
    """
    return MIN_HUMIDITY <= value <= MAX_HUMIDITY


def parse_hvac_status(status_string: str) -> Dict[str, bool]:
    """Parse HVAC status string into component states.
    
    The status string format is: G+Y1+W1-Y2-W2-B-O+
    Where + indicates ON and - indicates OFF
    
    Args:
        status_string: HVAC status string from thermostat
        
    Returns:
        Dictionary with relay states
    """
    match = HVAC_STATUS_PATTERN.match(status_string)
    if not match:
        _LOGGER.error("Invalid HVAC status string: %s", status_string)
        return {}
    
    # Extract relay states from pattern match
    g_state, y1_state, w1_state, y2_state, w2_state, b_state, o_state = match.groups()
    
    return {
        "fan": g_state == "+",            # Fan relay
        "compressor_1": y1_state == "+",  # First stage compressor
        "heat_1": w1_state == "+",        # First stage heat
        "compressor_2": y2_state == "+",  # Second stage compressor
        "heat_2": w2_state == "+",        # Second stage heat
        "rev_valve_heat": b_state == "+", # Reversing valve (heat mode)
        "rev_valve_cool": o_state == "+", # Reversing valve (cool mode)
    }


def determine_hvac_action(hvac_status: Dict[str, bool], mode: str) -> str:
    """Determine HVAC action based on relay status and current mode.
    
    Args:
        hvac_status: Dictionary with relay states
        mode: Current HVAC mode
        
    Returns:
        HVAC action string
    """
    from homeassistant.components.climate.const import (
        CURRENT_HVAC_COOL,
        CURRENT_HVAC_HEAT,
        CURRENT_HVAC_IDLE,
        CURRENT_HVAC_FAN,
        CURRENT_HVAC_OFF,
    )
    
    # System is off
    if mode == "OFF":
        return CURRENT_HVAC_OFF
    
    # Heating is active
    if hvac_status.get("heat_1") or hvac_status.get("heat_2"):
        return CURRENT_HVAC_HEAT
    
    # Cooling is active (compressor running with cool reversing valve)
    if (hvac_status.get("compressor_1") or hvac_status.get("compressor_2")) and hvac_status.get("rev_valve_cool"):
        return CURRENT_HVAC_COOL
    
    # Heating with heat pump (compressor running with heat reversing valve)
    if (hvac_status.get("compressor_1") or hvac_status.get("compressor_2")) and hvac_status.get("rev_valve_heat"):
        return CURRENT_HVAC_HEAT
    
    # Only fan is running
    if hvac_status.get("fan") and not any([
        hvac_status.get("compressor_1"),
        hvac_status.get("compressor_2"),
        hvac_status.get("heat_1"),
        hvac_status.get("heat_2"),
    ]):
        return CURRENT_HVAC_FAN
    
    # Default to idle
    return CURRENT_HVAC_IDLE


def parse_response(response: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse a response from the thermostat.
    
    Args:
        response: Response string from thermostat
        
    Returns:
        Tuple of (address, command, value) or (None, None, None) if invalid
    """
    match = RESPONSE_PATTERN.match(response.strip())
    if not match:
        return None, None, None
    
    address, command, value = match.groups()
    return address, command, value


def is_cos_message(message: str) -> bool:
    """Determine if a message is a Change of State (COS) message.
    
    This is a heuristic since there's no definitive way to tell apart
    COS messages from regular responses in the protocol.
    
    Args:
        message: Message string from thermostat
        
    Returns:
        True if the message appears to be a COS message
    """
    # Most COS messages follow standard response format
    if not RESPONSE_PATTERN.match(message.strip()):
        return False
    
    # COS messages typically come without being solicited by a command
    # This is implementation-dependent and might need additional checks
    return True


def validate_command(command: str, value: Optional[Any] = None) -> bool:
    """Validate command and optional value against known constraints.
    
    Args:
        command: Command string
        value: Command value to validate
        
    Returns:
        True if valid, False otherwise
    """
    # Basic command format validation
    if not re.match(r"^[A-Z0-9]+$", command):
        return False
    
    # Command-specific validation
    if command in ["MODE", "M"]:
        return value in VALID_HVAC_MODES
    
    if command in ["FAN", "F"]:
        return value in VALID_FAN_MODES
    
    if command == "SH":
        try:
            temp = float(value)
            return MIN_TEMP_F <= temp <= MAX_TEMP_F
        except (ValueError, TypeError):
            return False
    
    if command == "SC":
        try:
            temp = float(value)
            return MIN_TEMP_F <= temp <= MAX_TEMP_F
        except (ValueError, TypeError):
            return False
    
    if command in ["SHUM", "SDEH"]:
        try:
            humidity = int(value)
            return MIN_HUMIDITY <= humidity <= MAX_HUMIDITY
        except (ValueError, TypeError):
            return False
    
    # For other commands, just check that value is provided when needed
    if command.endswith("?"):  # Query commands don't need values
        return value is None
    
    # Most other commands need values
    return value is not None


def format_command(address: Optional[Union[int, str]], command: str, value: Optional[Any] = None) -> str:
    """Format a command string according to the Aprilaire protocol.
    
    Args:
        address: Thermostat address (1-64) or None for global command
        command: Command string
        value: Optional command value
        
    Returns:
        Formatted command string
    """
    if address is not None:
        address_str = format_address(address)
        prefix = f"SN{address_str}"
    else:
        prefix = "SN"
    
    if value is not None:
        return f"{prefix} {command}={value}"
    
    if command.endswith("?"):
        return f"{prefix} {command}"
    
    return f"{prefix} {command}"


def extract_temp_scale(value: str) -> Tuple[float, str]:
    """Extract temperature value and scale from a response string.
    
    Args:
        value: Temperature value string (e.g., "72F" or "22C")
        
    Returns:
        Tuple of (value, scale)
    """
    if value.endswith("F"):
        return float(value[:-1]), TEMP_FAHRENHEIT
    
    if value.endswith("C"):
        return float(value[:-1]), TEMP_CELSIUS
    
    # Try to convert to float without scale
    try:
        return float(value), None
    except ValueError:
        return None, None


def extract_humidity(value: str) -> Optional[int]:
    """Extract humidity value from a response string.
    
    Args:
        value: Humidity value string (e.g., "45%")
        
    Returns:
        Humidity value as integer or None if invalid
    """
    if value.endswith("%"):
        try:
            return int(value[:-1])
        except ValueError:
            pass
    
    return None

