"""Constants for the Aprilaire 8870 Thermostat integration."""
from typing import Final
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    Platform,
    UnitOfTemperature,
)
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
    FAN_AUTO,
    FAN_ON,
)

# Integration domain
DOMAIN: Final = "aprilaire_8870"

# Connection types
CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_SERIAL_PORT: Final = "port_name"
CONF_BAUDRATE: Final = "baud_rate"

# Connection type values - include both naming conventions
CONNECTION_TYPE_SERIAL_SERVER: Final = "serial_server"
CONNECTION_TYPE_SERIAL_PORT: Final = "serial_port"
CONN_TYPE_SERIAL_SERVER: Final = "serial_server"  # Alias for compatibility
CONN_TYPE_SERIAL_PORT: Final = "serial_port"  # Alias for compatibility

# Default configuration values
DEFAULT_PORT: Final = 23
DEFAULT_BAUDRATE: Final = 9600
# Two distinct poll cadences:
# * DEFAULT_UPDATE_INTERVAL — used once COS verification confirms broadcasts
#   are flowing for the majority of devices; trust broadcasts to keep state
#   fresh between these slow polls.
# * DEFAULT_FALLBACK_SCAN_INTERVAL — used at startup and any time broadcasts
#   aren't being received; the integration's primary state path.
DEFAULT_UPDATE_INTERVAL: Final = 600  # 10 minutes — slow poll after COS verified
DEFAULT_FALLBACK_SCAN_INTERVAL: Final = 300  # 5 minutes — backstop poll cadence
DEFAULT_COS_VERIFICATION_INTERVAL: Final = 1800  # 30 minutes
DEFAULT_NAME: Final = "Aprilaire 8870 Thermostat"

# Timeout values
TIMEOUT: Final = 10
COMMAND_TIMEOUT: Final = 3
CONNECTION_BACKOFF_MAX: Final = 300  # 5 minutes
CONNECTION_BACKOFF_FACTOR: Final = 2
CONNECTION_BACKOFF_JITTER: Final = 0.1

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
# v0.3.0: opt-in polling toggles for the optional/expensive query groups.
# Default off for the alarm cluster (FLTALM/WPALM/SYSALM/DEHALM/ERROR) since
# many firmwares NACK them and they consume the bulk of bus time when polled
# every cycle. Humidity and outdoor temp default on because they're cheap
# and answers cleanly on supported devices.
CONF_MONITOR_ALARMS: Final = "monitor_alarms"
CONF_MONITOR_HUMIDITY: Final = "monitor_humidity"
CONF_MONITOR_OUTDOOR_TEMP: Final = "monitor_outdoor_temp"

# Default options values
DEFAULT_COMMAND_RETRY_COUNT: Final = 3
DEFAULT_MONITOR_ALARMS: Final = False
DEFAULT_MONITOR_HUMIDITY: Final = True
DEFAULT_MONITOR_OUTDOOR_TEMP: Final = True

# COS flag constants
COS_FLAG_HVAC_RELAYS: Final = "c1"     # HVAC relay status changes
COS_FLAG_TEMPERATURE: Final = "c2"     # Temperature/humidity changes 
COS_FLAG_OUTDOOR_TEMP: Final = "c3"    # Remote temp/humidity changes
COS_FLAG_CONTACT_CLOSURES: Final = "c4"  # Contact closures (N/A for 8870)
COS_FLAG_SETPOINTS: Final = "c5"       # Setpoint changes
COS_FLAG_NETWORK_OVERRIDE: Final = "c6"  # Network override changes
COS_FLAG_MODE: Final = "c7"           # Mode changes
COS_FLAG_FAN: Final = "c8"            # Fan state changes
COS_FLAG_SCROLL_UP: Final = "c9"      # Scroll Up button status
COS_FLAG_SCROLL_DOWN: Final = "c10"   # Scroll Down button status
COS_FLAG_ENTER: Final = "c11"         # Enter button status
COS_FLAG_BACKLIGHT: Final = "c12"     # Backlight ready status
COS_FLAG_SETUP: Final = "c13"         # Configuration/Setup changes
COS_FLAG_ALARMS: Final = "c14"        # Alarm status changes
COS_FLAG_RECOVERY: Final = "c15"      # Progressive recovery status
COS_FLAG_SCHEDULE: Final = "c16"      # Schedule changes
COS_FLAG_HOLD_STATUS: Final = "c17"   # Hold status changes
COS_FLAG_ERRORS: Final = "c19"        # Error status changes

# Default COS flags to enable.
#
# The 8870 firmware only supports COS flags c1 through c12 per live log
# evidence (v0.4.5): every SN<n> c14=ON / c19=ON write timed out with no
# response on the 11-device bus. c14 (alarms) and c19 (errors) remain
# defined as constants for documentation, but enabling them on a real
# 8870 wedges the bus with retry timeouts and never produces broadcasts.
# Alarm/error state is still picked up by the optional per-device poll
# when monitor_alarms is enabled.
DEFAULT_COS_FLAGS: Final = [
    COS_FLAG_HVAC_RELAYS,      # c1
    COS_FLAG_TEMPERATURE,      # c2
    COS_FLAG_SETPOINTS,        # c5
    COS_FLAG_MODE,             # c7
    COS_FLAG_FAN,              # c8
]

# Map COS flags to their corresponding message patterns
COS_PREFIX_PATTERN: Final = {
    COS_FLAG_HVAC_RELAYS: "HVAC",
    COS_FLAG_TEMPERATURE: "T",
    COS_FLAG_OUTDOOR_TEMP: "OT",
    COS_FLAG_SETPOINTS: "S[HC]",  # Matches SH or SC
    COS_FLAG_NETWORK_OVERRIDE: "HOLD",
    COS_FLAG_MODE: "M",
    COS_FLAG_FAN: "F",
    COS_FLAG_ALARMS: ".*ALM",    # Matches any alarm pattern
    COS_FLAG_ERRORS: "ERROR",
}

# Platforms used in the integration
PLATFORMS: Final = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]

# Protocol constants
THERMOSTAT_PROCESSING_TIME_MS: Final = 265
RESPONSE_DELAY_MS: Final = 20
GLOBAL_COMMAND_PROCESSING_MULTIPLIER: Final = 64

# HVAC relay indices for parsing status
HVAC_RELAY_INDICES: Final = {
    "G": 1,    # Fan
    "Y1": 3,   # First stage compressor/cooling
    "W1": 5,   # First stage heat
    "Y2": 7,   # Second stage compressor/cooling
    "W2": 9,   # Second stage heat
    "B": 11,   # Reversing valve (heat mode)
    "O": 13,   # Reversing valve (cool mode)
}

# Attribute constants for entities
ATTR_HVAC_RELAY_STATUS: Final = "hvac_relay_status"
ATTR_OUTDOOR_TEMPERATURE: Final = "outdoor_temperature"
ATTR_INDOOR_HUMIDITY: Final = "indoor_humidity"
ATTR_FILTER_STATUS: Final = "filter_status"
ATTR_HOLD_STATUS: Final = "hold_status"

# Sensor types
SENSOR_TEMPERATURE: Final = "temperature"
SENSOR_HUMIDITY: Final = "humidity"
SENSOR_OUTDOOR_TEMPERATURE: Final = "outdoor_temperature"
SENSOR_OUTDOOR_HUMIDITY: Final = "outdoor_humidity"
SENSOR_REMOTE_TEMPERATURE: Final = "remote_temperature"

# HVAC Mode constants
MODE_OFF: Final = "OFF"        # System off
MODE_HEAT: Final = "HEAT"      # Heat mode
MODE_COOL: Final = "COOL"      # Cool mode
MODE_AUTO: Final = "AUTO"      # Auto mode
MODE_EMHT: Final = "EMHT"      # Emergency heat mode

# Fan Mode constants
FAN_AUTO_MODE: Final = "AUTO"       # Fan auto mode
FAN_ON_MODE: Final = "ON"           # Fan on mode
FAN_CIRC: Final = "CIRC"       # Fan circulation mode

# Controller Type constants
CONTROLLER_TYPE_TEMP: Final = "0"    # Temperature controller
CONTROLLER_TYPE_HUMID: Final = "1"   # Humidity controller

# Equipment Type constants
EQUIPMENT_TYPE_HEAT_COOL: Final = "heat_cool"
EQUIPMENT_TYPE_HEAT_PUMP: Final = "heat_pump"

# Temperature ranges
MIN_TEMP_F: Final = 40
MAX_TEMP_F: Final = 90
MIN_TEMP_C: Final = 4
MAX_TEMP_C: Final = 32

# Humidity ranges
MIN_HUMIDITY: Final = 10
MAX_HUMIDITY: Final = 90

# Service constants
SERVICE_SET_TEXT_MESSAGE: Final = "set_text_message"
SERVICE_SET_BACKLIGHT: Final = "set_backlight"
SERVICE_RESET_FILTER: Final = "reset_filter"
SERVICE_SET_LOCKOUT: Final = "set_lockout"
SERVICE_CONFIGURE_COS: Final = "configure_cos"
SERVICE_SIGNAL_SET_TEXT_MESSAGE: Final = f"{DOMAIN}_set_text_message"
SERVICE_SIGNAL_SET_BACKLIGHT: Final = f"{DOMAIN}_set_backlight"
SERVICE_SIGNAL_RESET_FILTER: Final = f"{DOMAIN}_reset_filter"
SERVICE_SIGNAL_SET_LOCKOUT: Final = f"{DOMAIN}_set_lockout"
SERVICE_SIGNAL_CONFIGURE_COS: Final = f"{DOMAIN}_configure_cos"

# Message type constants
MESSAGE_TYPE_TEMPORARY: Final = "tmpmes"
MESSAGE_TYPE_PERMANENT_1: Final = "pmes1"
MESSAGE_TYPE_PERMANENT_2: Final = "pmes2"
MESSAGE_TYPE_PERMANENT_3: Final = "pmes3"
MESSAGE_TYPE_PERMANENT_4: Final = "pmes4"

# Service attribute constants
ATTR_MESSAGE: Final = "message"
ATTR_MESSAGE_TYPE: Final = "message_type"
ATTR_STATE: Final = "state"
ATTR_DURATION: Final = "duration"
ATTR_FAN_LOCKOUT: Final = "fan_lockout"
ATTR_MODE_LOCKOUT: Final = "mode_lockout"
ATTR_SETPOINT_LOCKOUT: Final = "setpoint_lockout"
ATTR_NETWORK_LOCKOUT: Final = "network_lockout"
ATTR_LOCKOUT_TIME: Final = "lockout_time"
ATTR_LOCKOUT_LIMIT: Final = "lockout_limit"
ATTR_COS_FLAGS: Final = "cos_flags"

# Command constants for the protocol
CMD_ID: Final = "ID"                   # Query device model and firmware info
CMD_EQUIPCONFIG: Final = "EQUIPCONFIG" # Query equipment configuration
CMD_CT: Final = "CT"                   # Query controller type
CMD_MODE: Final = "MODE"               # Set/query HVAC mode
CMD_FAN: Final = "FAN"                 # Set/query fan mode
CMD_TEMP: Final = "TEMP"               # Query room temperature
CMD_HUM: Final = "HUM"                 # Query room humidity
CMD_OT: Final = "OT"                   # Query outdoor temperature
CMD_HVAC: Final = "HVAC"               # Query relay status
CMD_SH: Final = "SH"                   # Set/query heat setpoint
CMD_SC: Final = "SC"                   # Set/query cool setpoint
CMD_HOLD: Final = "HOLD"               # Set/query network override
CMD_BIHUM: Final = "BIHUM"             # Query built-in humidity sensor
CMD_CR: Final = "CR"                   # Set/query command response mode
CMD_RSM: Final = "RSM"                 # Query connected sensor modules

# Maps the short response codes the 8870 actually emits (e.g. "T=76F" for
# what was queried as "TEMP?") to the canonical command names used by
# device._process_state_response. The thermostat also broadcasts these
# short-code messages unsolicited when the user changes anything on the
# unit itself, which is what the coordinator's unsolicited-message
# listener decodes into state updates.
RESPONSE_CODE_TO_COMMAND: Final = {
    "T": CMD_TEMP,
    "M": CMD_MODE,
    "F": CMD_FAN,
    "SH": CMD_SH,
    "SC": CMD_SC,
    "HVAC": CMD_HVAC,
    "HOLD": CMD_HOLD,
    "HUM": CMD_HUM,
    "OT": CMD_OT,
    "FLTALM": "FLTALM",
    "WPALM": "WPALM",
    "SYSALM": "SYSALM",
    "DEHALM": "DEHALM",
    "ERROR": "ERROR",
}

# COS constant aliases for backward compatibility or alternative naming
COS_HVAC_RELAYS: Final = COS_FLAG_HVAC_RELAYS
COS_TEMPERATURE: Final = COS_FLAG_TEMPERATURE
COS_OUTDOOR_TEMP: Final = COS_FLAG_OUTDOOR_TEMP
COS_SETPOINTS: Final = COS_FLAG_SETPOINTS
COS_NETWORK_OVERRIDE: Final = COS_FLAG_NETWORK_OVERRIDE
COS_MODE: Final = COS_FLAG_MODE
COS_FAN: Final = COS_FLAG_FAN
COS_ALARMS: Final = COS_FLAG_ALARMS
COS_ERRORS: Final = COS_FLAG_ERRORS

# Logger name
LOGGER_NAME: Final = f"{DOMAIN}"

# Mode mapping between HA and Aprilaire
HVAC_MODE_APRILAIRE_TO_HA = {
    "OFF": HVACMode.OFF,
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "AUTO": HVACMode.AUTO,
    "EMHT": HVACMode.HEAT_COOL,  # Map Emergency Heat to HEAT_COOL
}

HA_TO_APRILAIRE_HVAC_MODE = {
    HVACMode.OFF: "OFF",
    HVACMode.HEAT: "HEAT",
    HVACMode.COOL: "COOL",
    HVACMode.AUTO: "AUTO",
    HVACMode.HEAT_COOL: "AUTO",  # Both map to AUTO
}

# Fan mode mapping between HA and Aprilaire
FAN_MODE_APRILAIRE_TO_HA = {
    "AUTO": FAN_AUTO,
    "ON": FAN_ON,
    "CIRC": "circulate",
}

HA_TO_APRILAIRE_FAN_MODE = {
    FAN_AUTO: "AUTO",
    FAN_ON: "ON",
    "circulate": "CIRC",
}

# Event signals
SIGNAL_CONNECTION_STATE_CHANGED: Final = f"{DOMAIN}_connection_state_changed"