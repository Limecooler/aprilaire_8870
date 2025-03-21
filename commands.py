"""Command implementation for Aprilaire 8870 thermostats."""
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from homeassistant.const import (
    ATTR_TEMPERATURE,
    HVACMode,
    FAN_AUTO,
    FAN_ON,
    PRECISION_WHOLE,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)

from .const import (
    LOGGER_NAME,
    COS_FLAG_MAPPING,
    FAN_MODE_APRILAIRE_TO_HA,
    FAN_MODE_HA_TO_APRILAIRE,
    HVAC_MODE_APRILAIRE_TO_HA,
    HVAC_MODE_HA_TO_APRILAIRE,
)

_LOGGER = logging.getLogger(LOGGER_NAME)

async def async_execute_query_command(
    device, command: str, timeout: Optional[float] = None
) -> str:
    """Execute a query command and return the response.
    
    Args:
        device: The device to send the command to
        command: The command to execute
        timeout: Optional timeout override
        
    Returns:
        The command response
        
    Raises:
        CommandError: If the command fails
    """
    _LOGGER.debug(
        "Executing query command '%s' for device %s", command, device.device_id
    )
    
    return await device.async_send_command(command, timeout=timeout)

async def async_execute_assignment_command(
    device, command: str, value: str, timeout: Optional[float] = None
) -> str:
    """Execute an assignment command and return the response.
    
    Args:
        device: The device to send the command to
        command: The command to execute
        value: The value to assign
        timeout: Optional timeout override
        
    Returns:
        The command response
        
    Raises:
        CommandError: If the command fails
    """
    _LOGGER.debug(
        "Executing assignment command '%s=%s' for device %s",
        command,
        value,
        device.device_id,
    )
    
    return await device.async_send_command(command, value=value, timeout=timeout)

async def async_query_device_info(device) -> Dict[str, Any]:
    """Query device information from the thermostat.
    
    Args:
        device: The device to query
        
    Returns:
        Dictionary containing device information
        
    Raises:
        CommandError: If the command fails
    """
    # Get model and firmware info
    id_response = await async_execute_query_command(device, "ID")
    
    # Get equipment configuration
    equipconfig_response = await async_execute_query_command(device, "EQUIPCONFIG")
    
    # Get controller type (thermostat or humidistat)
    ct_response = await async_execute_query_command(device, "CT")
    
    # Parse responses and return a complete info dictionary
    info = {
        "id_response": id_response,
        "equipconfig_response": equipconfig_response,
        "ct_response": ct_response,
    }
    
    # Extract model from ID response if possible
    if "MODEL#" in id_response:
        model_parts = id_response.split("MODEL#")[1].split("REV:")
        if model_parts:
            info["model"] = model_parts[0].strip()
            rev_parts = model_parts[1].split("RPC")
            if rev_parts:
                info["firmware"] = rev_parts[0].strip()
                if len(rev_parts) > 1:
                    info["year"] = rev_parts[1].strip()
    
    # Parse equipment configuration
    if len(equipconfig_response) >= 4:
        # The format is a 4-digit string like "0101"
        config_value = equipconfig_response[-4:]
        info["is_master"] = config_value[0] == "1"
        info["is_gas"] = config_value[1] == "1"
        info["is_multi_stage"] = config_value[2] == "1"
        info["is_heat_pump"] = config_value[3] == "0"  # 0=heat pump, 1=heat/cool
    
    # Parse controller type
    if "CT=" in ct_response:
        ct_value = ct_response.split("CT=")[1].strip()
        info["is_humidistat"] = ct_value == "1"
    
    return info

async def async_query_thermostat_state(device) -> Dict[str, Any]:
    """Query the current state of the thermostat.
    
    Args:
        device: The device to query
        
    Returns:
        Dictionary containing current thermostat state
        
    Raises:
        CommandError: If any command fails
    """
    state = {}
    
    # Query mode
    try:
        mode_response = await async_execute_query_command(device, "MODE")
        if "M=" in mode_response:
            mode_value = mode_response.split("M=")[1].strip()
            state["mode"] = mode_value
            state["hvac_mode"] = HVAC_MODE_APRILAIRE_TO_HA.get(mode_value)
    except Exception as ex:
        _LOGGER.error("Failed to query mode: %s", ex)
    
    # Query fan mode
    try:
        fan_response = await async_execute_query_command(device, "FAN")
        if "F=" in fan_response:
            fan_value = fan_response.split("F=")[1].strip()
            state["fan_mode"] = fan_value
            state["fan_mode_ha"] = FAN_MODE_APRILAIRE_TO_HA.get(fan_value, FAN_AUTO)
    except Exception as ex:
        _LOGGER.error("Failed to query fan mode: %s", ex)
    
    # Query temperature
    try:
        temp_response = await async_execute_query_command(device, "TEMP")
        if "T=" in temp_response:
            temp_parts = temp_response.split("T=")[1].strip()
            temp_value = temp_parts[:-1]  # Remove F or C
            temp_unit = temp_parts[-1]  # Get F or C
            try:
                state["temperature"] = float(temp_value)
                state["temperature_unit"] = (
                    TEMP_FAHRENHEIT if temp_unit == "F" else TEMP_CELSIUS
                )
            except ValueError:
                _LOGGER.error("Invalid temperature value: %s", temp_value)
    except Exception as ex:
        _LOGGER.error("Failed to query temperature: %s", ex)
    
    # Query heat setpoint
    try:
        heat_sp_response = await async_execute_query_command(device, "SH")
        if "SH=" in heat_sp_response:
            heat_sp_parts = heat_sp_response.split("SH=")[1].strip()
            heat_sp_value = heat_sp_parts[:-1]  # Remove F or C
            try:
                state["heat_setpoint"] = float(heat_sp_value)
            except ValueError:
                _LOGGER.error("Invalid heat setpoint value: %s", heat_sp_value)
    except Exception as ex:
        _LOGGER.error("Failed to query heat setpoint: %s", ex)
    
    # Query cool setpoint
    try:
        cool_sp_response = await async_execute_query_command(device, "SC")
        if "SC=" in cool_sp_response:
            cool_sp_parts = cool_sp_response.split("SC=")[1].strip()
            cool_sp_value = cool_sp_parts[:-1]  # Remove F or C
            try:
                state["cool_setpoint"] = float(cool_sp_value)
            except ValueError:
                _LOGGER.error("Invalid cool setpoint value: %s", cool_sp_value)
    except Exception as ex:
        _LOGGER.error("Failed to query cool setpoint: %s", ex)
    
    # Query HVAC status
    try:
        hvac_response = await async_execute_query_command(device, "HVAC")
        if "HVAC=" in hvac_response:
            hvac_value = hvac_response.split("HVAC=")[1].strip()
            state["hvac_status"] = hvac_value
            
            # Parse relay status
            relay_states = {}
            for i, relay in enumerate(["G", "Y1", "W1", "Y2", "W2", "B", "O"]):
                if i < len(hvac_value):
                    relay_states[relay] = hvac_value[i*2 + 1] == "+"
            state["relay_states"] = relay_states
            
            # Determine HVAC action based on relay states
            if relay_states.get("W1") or relay_states.get("W2"):
                state["hvac_action"] = "heating"
            elif relay_states.get("Y1") or relay_states.get("Y2"):
                state["hvac_action"] = "cooling"
            else:
                state["hvac_action"] = "idle"
    except Exception as ex:
        _LOGGER.error("Failed to query HVAC status: %s", ex)
    
    # Query humidity if available
    try:
        hum_response = await async_execute_query_command(device, "HUM")
        if "HUM=" in hum_response:
            hum_parts = hum_response.split("HUM=")[1].strip()
            hum_value = hum_parts[:-1]  # Remove %
            try:
                if hum_value != "--":
                    state["humidity"] = float(hum_value)
            except ValueError:
                _LOGGER.error("Invalid humidity value: %s", hum_value)
    except Exception as ex:
        _LOGGER.warning("Failed to query humidity: %s", ex)
    
    # Query outdoor temperature if available
    try:
        ot_response = await async_execute_query_command(device, "OT")
        if "OT=" in ot_response:
            ot_parts = ot_response.split("OT=")[1].strip()
            if ot_parts != "--F" and ot_parts != "--C":
                ot_value = ot_parts[:-1]  # Remove F or C
                try:
                    state["outdoor_temperature"] = float(ot_value)
                except ValueError:
                    _LOGGER.error("Invalid outdoor temperature value: %s", ot_value)
    except Exception as ex:
        _LOGGER.warning("Failed to query outdoor temperature: %s", ex)
    
    return state

async def async_set_temperature(
    device, temperature: float, mode: Optional[str] = None
) -> None:
    """Set the target temperature on the thermostat.
    
    Args:
        device: The device to control
        temperature: The target temperature
        mode: Optional HVAC mode (heat, cool, auto)
        
    Raises:
        CommandError: If the command fails
    """
    current_mode = mode
    
    # If mode not provided, query current mode
    if not current_mode:
        mode_response = await async_execute_query_command(device, "MODE")
        if "M=" in mode_response:
            current_mode = mode_response.split("M=")[1].strip()
    
    if not current_mode:
        _LOGGER.error("Cannot set temperature - unknown current mode")
        return
    
    # Determine which setpoint to set based on mode
    if current_mode in ["HEAT", "EMHT"]:
        command = "SH"
    elif current_mode == "COOL":
        command = "SC"
    elif current_mode == "AUTO":
        # In AUTO mode, we need to set both, but we'll use the active setpoint
        # To determine the active setpoint, we need to check the current temperature
        temp_response = await async_execute_query_command(device, "TEMP")
        if "T=" in temp_response:
            temp_parts = temp_response.split("T=")[1].strip()
            temp_value = temp_parts[:-1]  # Remove F or C
            try:
                current_temp = float(temp_value)
                
                # Get current setpoints
                heat_sp_response = await async_execute_query_command(device, "SH")
                cool_sp_response = await async_execute_query_command(device, "SC")
                
                heat_sp = None
                cool_sp = None
                
                if "SH=" in heat_sp_response:
                    heat_sp_parts = heat_sp_response.split("SH=")[1].strip()
                    heat_sp_value = heat_sp_parts[:-1]  # Remove F or C
                    try:
                        heat_sp = float(heat_sp_value)
                    except ValueError:
                        pass
                
                if "SC=" in cool_sp_response:
                    cool_sp_parts = cool_sp_response.split("SC=")[1].strip()
                    cool_sp_value = cool_sp_parts[:-1]  # Remove F or C
                    try:
                        cool_sp = float(cool_sp_value)
                    except ValueError:
                        pass
                
                # Determine which setpoint to adjust based on current temperature
                if heat_sp is not None and cool_sp is not None:
                    if abs(current_temp - heat_sp) <= abs(current_temp - cool_sp):
                        command = "SH"
                    else:
                        command = "SC"
                elif heat_sp is not None:
                    command = "SH"
                elif cool_sp is not None:
                    command = "SC"
                else:
                    command = "S"  # Use generic setpoint command
            except ValueError:
                command = "S"  # Use generic setpoint command
        else:
            command = "S"  # Use generic setpoint command
    else:
        _LOGGER.error("Cannot set temperature in mode: %s", current_mode)
        return
    
    # Send the command with integer temperature value
    int_temp = int(round(temperature))
    await async_execute_assignment_command(device, command, str(int_temp))

async def async_set_hvac_mode(device, hvac_mode: str) -> None:
    """Set the HVAC mode on the thermostat.
    
    Args:
        device: The device to control
        hvac_mode: The HVAC mode to set
        
    Raises:
        CommandError: If the command fails
    """
    # Convert from HA mode to Aprilaire mode
    aprilaire_mode = HVAC_MODE_HA_TO_APRILAIRE.get(hvac_mode)
    if not aprilaire_mode:
        _LOGGER.error("Invalid HVAC mode: %s", hvac_mode)
        return
    
    await async_execute_assignment_command(device, "MODE", aprilaire_mode)

async def async_set_fan_mode(device, fan_mode: str) -> None:
    """Set the fan mode on the thermostat.
    
    Args:
        device: The device to control
        fan_mode: The fan mode to set
        
    Raises:
        CommandError: If the command fails
    """
    # Convert from HA fan mode to Aprilaire fan mode
    aprilaire_fan_mode = FAN_MODE_HA_TO_APRILAIRE.get(fan_mode)
    if not aprilaire_fan_mode:
        _LOGGER.error("Invalid fan mode: %s", fan_mode)
        return
    
    await async_execute_assignment_command(device, "FAN", aprilaire_fan_mode)

async def async_set_hold(device, hold_state: bool) -> None:
    """Set the network override (hold) state on the thermostat.
    
    Args:
        device: The device to control
        hold_state: True to enable hold, False to disable
        
    Raises:
        CommandError: If the command fails
    """
    value = "ON" if hold_state else "OFF"
    await async_execute_assignment_command(device, "HOLD", value)

async def async_enable_cos(device, flags: Optional[List[str]] = None) -> None:
    """Enable Change of State (COS) functionality on the thermostat.
    
    Args:
        device: The device to enable COS on
        flags: List of COS flags to enable, or None for defaults
        
    Raises:
        CommandError: If any command fails
    """
    _LOGGER.debug("Enabling COS functionality for device %s", device.device_id)
    
    # Set CR to NORMAL to enable COS
    try:
        await async_execute_assignment_command(device, "CR", "NORMAL")
    except Exception as ex:
        _LOGGER.error("Failed to set CR=NORMAL: %s", ex)
        raise
    
    # Default flags if none provided
    if flags is None:
        flags = list(COS_FLAG_MAPPING.keys())
    
    # Enable specified COS flags
    for flag in flags:
        try:
            # Only enable flags that are in our mapping
            if flag in COS_FLAG_MAPPING:
                await async_execute_assignment_command(device, flag, "ON")
                _LOGGER.debug("Enabled %s for device %s", flag, device.device_id)
        except Exception as ex:
            _LOGGER.error("Failed to enable %s: %s", flag, ex)
            # Continue with other flags even if one fails

async def async_verify_cos(device, flags: Optional[List[str]] = None) -> bool:
    """Verify COS functionality is enabled on the thermostat.
    
    Args:
        device: The device to verify COS on
        flags: List of COS flags to verify, or None for defaults
        
    Returns:
        True if COS is properly enabled, False otherwise
        
    Raises:
        CommandError: If any command fails
    """
    _LOGGER.debug("Verifying COS functionality for device %s", device.device_id)
    
    # Check CR setting
    try:
        cr_response = await async_execute_query_command(device, "CR")
        if "CR=" not in cr_response or "NORMAL" not in cr_response:
            _LOGGER.warning(
                "CR not set to NORMAL for device %s: %s",
                device.device_id,
                cr_response,
            )
            return False
    except Exception as ex:
        _LOGGER.error("Failed to query CR: %s", ex)
        return False
    
    # Default flags if none provided
    if flags is None:
        flags = list(COS_FLAG_MAPPING.keys())
    
    # Check each COS flag
    all_enabled = True
    for flag in flags:
        if flag in COS_FLAG_MAPPING:
            try:
                response = await async_execute_query_command(device, flag)
                if f"{flag}=ON" not in response:
                    _LOGGER.warning(
                        "%s not enabled for device %s: %s",
                        flag,
                        device.device_id,
                        response,
                    )
                    all_enabled = False
            except Exception as ex:
                _LOGGER.error("Failed to query %s: %s", flag, ex)
                all_enabled = False
    
    return all_enabled

async def async_set_temperature_unit(device, unit: str) -> None:
    """Set the temperature unit on the thermostat.
    
    Args:
        device: The device to control
        unit: The temperature unit (F or C)
        
    Raises:
        CommandError: If the command fails
    """
    if unit not in ["F", "C"]:
        _LOGGER.error("Invalid temperature unit: %s", unit)
        return
    
    await async_execute_assignment_command(device, "SCALE", unit)

async def async_send_text_message(device, message: str) -> None:
    """Send a text message to the thermostat display.
    
    Args:
        device: The device to send the message to
        message: The message to display (max 32 chars)
        
    Raises:
        CommandError: If the command fails
    """
    # Truncate message if too long
    if len(message) > 32:
        message = message[:32]
    
    # Send as temporary message
    await async_execute_assignment_command(device, "TMPMES", message)

async def async_reset_filter(device) -> None:
    """Reset the filter alarm on the thermostat.
    
    Args:
        device: The device to reset
        
    Raises:
        CommandError: If the command fails
    """
    await async_execute_assignment_command(device, "FLTALM", "OFF")

async def async_set_backlight(device, on: bool = True) -> None:
    """Control the thermostat backlight.
    
    Args:
        device: The device to control
        on: True to turn on backlight, False to use auto setting
        
    Raises:
        CommandError: If the command fails
    """
    if on:
        # BLTON command has no value
        await device.async_send_command("BLTON")
    else:
        # Set constant backlight to OFF
        await async_execute_assignment_command(device, "CONSTBLT", "OFF")

