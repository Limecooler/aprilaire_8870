"""Constants for the Aprilaire 8870 Thermostat integration."""
from typing import Final
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    Platform,
)

# Integration domain
DOMAIN: Final = "aprilaire_8870"

# Connection types
CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_SERIAL_PORT: Final = "port_name"
CONF_BAUDRATE: Final = "baud_rate"

# Connection type values
CONNECTION_TYPE_SERIAL_SERVER: Final = "serial_server"
CONNECTION_TYPE_SERIAL_PORT: Final = "serial_port"

# Default configuration values
DEFAULT_PORT: Final = 23
DEFAULT_BAUDRATE: Final = 9600
DEFAULT_SCAN_INTERVAL: Final = 300  # 5 minutes
DEFAULT_FALLBACK_SCAN_INTERVAL: Final = 60  # 1 minute
DEFAULT_NAME: Final = "Aprilaire 8870 Thermostat"

# Timeout values
TIMEOUT_CONNECTION: Final = 10
TIMEOUT_COMMAND: Final = 3
CONNECTION_BACKOFF_MAX: Final = 300  # 5 minutes

# Additional configuration keys
CONF_FALLBACK_SCAN_INTERVAL: Final = "fallback_scan_interval"
CONF_ENABLE_COS: Final = "enable_cos"
CONF_COS_FLAGS: Final = "cos_flags"
CONF_COS_VERIFICATION_INTERVAL: Final = "cos_verification_interval"
CONF_TEMPERATURE_UNIT: Final = "temperature_unit"
CONF_COMMAND_RETRY_COUNT: Final = "command_retry_count"
CONF_ENABLE_COMMAND_BATCHING: Final = "enable_command_batching"
CONF_DEBUG_MODE: Final = "debug_mode"
CONF_CONNECTION_BACKOFF_MAX: Final = "connection_backoff_max"

# Default options values
DEFAULT_ENABLE_COS: Final = True
DEFAULT_COS_VERIFICATION_INTERVAL: Final = 1800  # 30 minutes
DEFAULT_COMMAND_RETRY_COUNT: Final = 3
DEFAULT_ENABLE_COMMAND_BATCHING: Final = True
DEFAULT_DEBUG_MODE: Final = False

# Service constants
SERVICE_SET_TEXT_MESSAGE: Final = "set_text_message"
SERVICE_SET_BACKLIGHT: Final = "set_backlight"
SERVICE_RESET_FILTER: Final = "reset_filter"
SERVICE_SET_LOCKOUT: Final = "set_lockout"
SERVICE_CONFIGURE_COS: Final = "configure_cos"

# Platforms used in the integration
PLATFORMS: Final = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]

# Protocol constants
PROTOCOL_TERMINATOR: Final = "\r"
PROTOCOL_COMMAND_PREFIX: Final = "SN"

# Command types
COMMAND_QUERY: Final = "?"
COMMAND_ASSIGNMENT: Final = "="

# COS flag constants
COS_HVAC_RELAYS: Final = "c1"  # HVAC relay status changes
COS_TEMPERATURE: Final = "c2"  # Temperature/humidity changes
COS_OUTDOOR_TEMP: Final = "c3"  # Remote temp/humidity changes
COS_CONTACT_CLOSURES: Final = "c4"  # Contact closures (not used in 8870)
COS_SETPOINTS: Final = "c5"  # Setpoint changes
COS_NETWORK_OVERRIDE: Final = "c6"  # Network override changes
COS_MODE: Final = "c7"  # Mode changes
COS_FAN: Final = "c8"  # Fan state changes
COS_SCROLL_UP: Final = "c9"  # Scroll Up button status
COS_SCROLL_DOWN: Final = "c10"  # Scroll Down button status
COS_ENTER: Final = "c11"  # Enter button status
COS_BACKLIGHT: Final = "c12"  # Backlight ready status
COS_SETUP: Final = "c13"  # Configuration/Setup changes
COS_ALARMS: Final = "c14"  # Alarm status changes
COS_RECOVERY: Final = "c15"  # Progressive recovery status
COS_SCHEDULE: Final = "c16"  # Schedule changes
COS_HOLD_STATUS: Final = "c17"  # Hold status changes
COS_ERROR: Final = "c19"  # Error status changes

# Default COS flags to enable
DEFAULT_COS_FLAGS: Final = [
    COS_HVAC_RELAYS,
    COS_TEMPERATURE,
    COS_SETPOINTS,
    COS_MODE,
    COS_FAN,
    COS_ALARMS,
    COS_ERROR,
]

# Command constants - frequently used commands
CMD_SN: Final = "SN"  # Device discovery command
CMD_ID: Final = "ID"  # Device ID query
CMD_HVAC: Final = "HVAC"  # HVAC status 
CMD_H: Final = "H"  # HVAC status (short form)
CMD_TEMP: Final = "TEMP"  # Temperature query
CMD_T: Final = "T"  # Temperature query (short form)
CMD_HUM: Final = "HUM"  # Humidity query
CMD_OT: Final = "OT"  # Outdoor temperature
CMD_R: Final = "R"  # Outdoor temperature (alternate)
CMD_OH: Final = "OH"  # Outdoor humidity
CMD_MODE: Final = "MODE"  # System mode
CMD_M: Final = "M"  # System mode (short form)
CMD_FAN: Final = "FAN"  # Fan mode
CMD_F: Final = "F"  # Fan mode (short form)
CMD_SH: Final = "SH"  # Heat setpoint
CMD_SC: Final = "SC"  # Cool setpoint
CMD_SHUM: Final = "SHUM"  # Humidification setpoint
CMD_SDEH: Final = "SDEH"  # Dehumidification setpoint
CMD_EQUIPCONFIG: Final = "EQUIPCONFIG"  # Equipment configuration
CMD_EQUIP: Final = "EQUIP"  # Equipment type
CMD_CT: Final = "CT"  # Controller type
CMD_CR: Final = "CR"  # Command response control
CMD_CP: Final = "CP"  # Command configuration pattern
CMD_HOLD: Final = "HOLD"  # Network override status
CMD_NAME: Final = "NAME"  # Thermostat name
CMD_FLTALM: Final = "FLTALM"  # Filter alarm status
CMD_TMPMES: Final = "TMPMES"  # Temporary message
CMD_BLTON: Final = "BLTON"  # Turn backlight on

# Response constants
RESP_CR_NORMAL: Final = "NORMAL"
RESP_CR_QUIET: Final = "QUIET"
RESP_CR_SILENT: Final = "SILENT"

# HVAC modes mapping
HVAC_MODE_OFF: Final = "OFF"
HVAC_MODE_HEAT: Final = "HEAT"
HVAC_MODE_COOL: Final = "COOL"
HVAC_MODE_AUTO: Final = "AUTO"
HVAC_MODE_EMHT: Final = "EMHT"  # Emergency heat

# Fan modes mapping
FAN_MODE_AUTO: Final = "AUTO"
FAN_MODE_ON: Final = "ON"
FAN_MODE_CIRC: Final = "CIRC"  # Circulation

# Controller types
CONTROLLER_TYPE_TEMPERATURE: Final = "0"
CONTROLLER_TYPE_HUMIDITY: Final = "1"

# Entity name suffixes
ENTITY_SENSOR: Final = "Sensor"
ENTITY_BINARY_SENSOR: Final = "Binary Sensor" 
ENTITY_SWITCH: Final = "Switch"
ENTITY_CLIMATE: Final = "Climate"

# Entity attribute keys
ATTR_TEMPERATURE: Final = "temperature"
ATTR_TARGET_TEMP: Final = "target_temperature"
ATTR_HUMIDITY: Final = "humidity"
ATTR_HVAC_MODE: Final = "hvac_mode"
ATTR_FAN_MODE: Final = "fan_mode"
ATTR_OUTDOOR_TEMP: Final = "outdoor_temperature"
ATTR_OUTDOOR_HUMIDITY: Final = "outdoor_humidity"

# Error and connection state constants
STATE_DISCONNECTED: Final = "disconnected"
STATE_CONNECTING: Final = "connecting"
STATE_CONNECTED: Final = "connected"
STATE_ERROR: Final = "error"
STATE_RECOVERY: Final = "recovery"

