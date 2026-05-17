"""Diagnostics support for the Aprilaire 8870 integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Keys redacted from config-entry data when downloaded for support tickets.
REDACT_KEYS = {"host", "port_name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "coordinator", None) if runtime else None
    devices = getattr(runtime, "devices", {}) if runtime else {}
    connection = getattr(runtime, "connection", None) if runtime else None

    devices_diag: dict[str, dict[str, Any]] = {}
    for address, device in devices.items():
        devices_diag[str(address)] = {
            "model": getattr(device, "model", None),
            "firmware_version": getattr(device, "firmware_version", None),
            "available": getattr(device, "available", None),
            "cos_enabled": getattr(device, "is_cos_enabled", lambda: None)(),
            "cos_flags": sorted(getattr(device, "get_cos_flags", lambda: set())()),
            "capabilities": getattr(device, "get_capabilities", lambda: {})(),
            "state": getattr(device, "get_state", lambda: {})(),
        }

    coordinator_diag: dict[str, Any] = {}
    if coordinator is not None:
        coordinator_diag = {
            "update_interval_seconds":
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval else None,
            "last_update_success": bool(coordinator.last_update_success),
            "connection_state": getattr(coordinator, "_connection_state", None),
            "cos_enabled": getattr(coordinator, "_cos_enabled", None),
            "cos_verified": getattr(coordinator, "_cos_verified", None),
            "device_count": len(devices),
        }

    connection_diag: dict[str, Any] = {}
    if connection is not None:
        connection_diag = {
            "type": entry.data.get("connection_type"),
            "state": getattr(connection, "state", None),
            "is_connected": connection.is_connected()
                if hasattr(connection, "is_connected") else None,
            "connect_error_count": getattr(connection, "_connect_error_count", None),
        }

    return {
        "entry": async_redact_data(entry.as_dict(), REDACT_KEYS),
        "connection": connection_diag,
        "coordinator": coordinator_diag,
        "devices": devices_diag,
    }
