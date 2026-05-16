"""Tests for services.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.aprilaire_8870 import services as services_mod
from custom_components.aprilaire_8870.const import (
    DOMAIN,
    SERVICE_CONFIGURE_COS,
    SERVICE_RESET_FILTER,
    SERVICE_SET_BACKLIGHT,
    SERVICE_SET_LOCKOUT,
    SERVICE_SET_TEXT_MESSAGE,
)


# ---- schema validation -----------------------------------------------------


def test_set_text_message_schema_accepts_valid() -> None:
    valid = {"entity_id": "climate.foo", "message": "Hi", "message_type": "tmpmes"}
    assert services_mod.SET_TEXT_MESSAGE_SCHEMA(valid) == valid


def test_set_text_message_schema_rejects_bad_type() -> None:
    with pytest.raises(vol.Invalid):
        services_mod.SET_TEXT_MESSAGE_SCHEMA(
            {"entity_id": "climate.foo", "message": "X", "message_type": "junk"}
        )


def test_set_backlight_schema_accepts_minimal() -> None:
    valid = {"entity_id": "climate.foo"}
    assert services_mod.SET_BACKLIGHT_SCHEMA(valid) == valid


def test_set_backlight_schema_full() -> None:
    valid = {"entity_id": "climate.foo", "state": True, "duration": 30}
    assert services_mod.SET_BACKLIGHT_SCHEMA(valid) == valid


def test_set_backlight_schema_rejects_out_of_range() -> None:
    with pytest.raises(vol.Invalid):
        services_mod.SET_BACKLIGHT_SCHEMA({"entity_id": "climate.foo", "duration": 999})


def test_reset_filter_schema() -> None:
    valid = {"entity_id": "climate.foo"}
    assert services_mod.RESET_FILTER_SCHEMA(valid) == valid


def test_set_lockout_schema_accepts_subset() -> None:
    valid = {"entity_id": "climate.foo", "fan_lockout": 1}
    assert services_mod.SET_LOCKOUT_SCHEMA(valid) == valid


def test_set_lockout_schema_rejects_out_of_range() -> None:
    with pytest.raises(vol.Invalid):
        services_mod.SET_LOCKOUT_SCHEMA(
            {"entity_id": "climate.foo", "setpoint_lockout": 9}
        )


def test_configure_cos_schema_accepts_known_flags() -> None:
    valid = {"entity_id": "climate.foo", "cos_flags": ["c1", "c2"]}
    assert services_mod.CONFIGURE_COS_SCHEMA(valid) == valid


def test_configure_cos_schema_rejects_unknown_flag() -> None:
    with pytest.raises(vol.Invalid):
        services_mod.CONFIGURE_COS_SCHEMA({"entity_id": "climate.foo", "cos_flags": ["bogus"]})


# ---- async_setup_services --------------------------------------------------


async def test_setup_registers_all_five(hass) -> None:
    await services_mod.async_setup_services(hass)
    expected = {
        SERVICE_SET_TEXT_MESSAGE,
        SERVICE_SET_BACKLIGHT,
        SERVICE_RESET_FILTER,
        SERVICE_SET_LOCKOUT,
        SERVICE_CONFIGURE_COS,
    }
    for svc in expected:
        assert hass.services.has_service(DOMAIN, svc), f"Missing service {svc}"


async def test_setup_handles_register_exception(hass) -> None:
    bad_hass = MagicMock()
    bad_hass.services.async_register = MagicMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await services_mod.async_setup_services(bad_hass)


# ---- Service handlers ------------------------------------------------------


async def test_set_text_message_dispatches(hass) -> None:
    await services_mod.async_setup_services(hass)
    received: list[tuple] = []

    @hass.helpers.dispatcher.async_dispatcher_connect.__wrapped__ if False else (
        lambda *args, **kwargs: None
    )
    def _noop(*args, **kwargs):
        pass

    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    def listener(*args):
        received.append(args)

    async_dispatcher_connect(hass, f"{DOMAIN}_set_text_message_climate.aprilaire_1", listener)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_TEXT_MESSAGE,
        {"entity_id": "climate.aprilaire_1", "message": "Hi", "message_type": "tmpmes"},
        blocking=True,
    )
    assert received and received[0] == ("Hi", "tmpmes")


async def test_set_text_message_truncates_long_strings(hass) -> None:
    await services_mod.async_setup_services(hass)
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    captured: list[str] = []

    def listener(message, _type):
        captured.append(message)

    async_dispatcher_connect(hass, f"{DOMAIN}_set_text_message_climate.x", listener)
    long = "A" * 50
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_TEXT_MESSAGE,
        {"entity_id": "climate.x", "message": long, "message_type": "tmpmes"},
        blocking=True,
    )
    assert captured[0] == long[:31]


async def test_set_backlight_handler(hass) -> None:
    await services_mod.async_setup_services(hass)
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    captured: list[tuple] = []
    async_dispatcher_connect(
        hass, f"{DOMAIN}_set_backlight_climate.x",
        lambda *args: captured.append(args),
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_BACKLIGHT,
        {"entity_id": "climate.x", "state": True, "duration": 30},
        blocking=True,
    )
    assert captured == [(True, 30)]


async def test_reset_filter_handler(hass) -> None:
    await services_mod.async_setup_services(hass)
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    fired = []
    async_dispatcher_connect(
        hass, f"{DOMAIN}_reset_filter_climate.x",
        lambda *args: fired.append(args),
    )
    await hass.services.async_call(
        DOMAIN, SERVICE_RESET_FILTER, {"entity_id": "climate.x"}, blocking=True,
    )
    assert fired == [()]


async def test_set_lockout_handler(hass) -> None:
    await services_mod.async_setup_services(hass)
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    captured = []
    async_dispatcher_connect(
        hass, f"{DOMAIN}_set_lockout_climate.x",
        lambda *args: captured.append(args),
    )
    await hass.services.async_call(
        DOMAIN, SERVICE_SET_LOCKOUT,
        {"entity_id": "climate.x", "fan_lockout": 1, "mode_lockout": 2,
         "setpoint_lockout": 3, "network_lockout": 1, "lockout_time": 60, "lockout_limit": 5},
        blocking=True,
    )
    assert captured == [(1, 2, 3, 1, 60, 5)]


async def test_configure_cos_handler(hass) -> None:
    await services_mod.async_setup_services(hass)
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    captured = []
    async_dispatcher_connect(
        hass, f"{DOMAIN}_configure_cos_climate.x",
        lambda *args: captured.append(args),
    )
    await hass.services.async_call(
        DOMAIN, SERVICE_CONFIGURE_COS,
        {"entity_id": "climate.x", "cos_flags": ["c1", "c2"]},
        blocking=True,
    )
    assert captured == [(["c1", "c2"],)]


# ---- Exception handling inside each service handler -----------------------


@pytest.mark.parametrize("svc,payload", [
    (SERVICE_SET_TEXT_MESSAGE, {"entity_id": "climate.x", "message": "x", "message_type": "tmpmes"}),
    (SERVICE_SET_BACKLIGHT, {"entity_id": "climate.x"}),
    (SERVICE_RESET_FILTER, {"entity_id": "climate.x"}),
    (SERVICE_SET_LOCKOUT, {"entity_id": "climate.x"}),
    (SERVICE_CONFIGURE_COS, {"entity_id": "climate.x", "cos_flags": ["c1"]}),
])
async def test_handlers_swallow_dispatcher_exception(hass, svc, payload) -> None:
    """If async_dispatcher_send raises, the service handler logs and returns."""
    await services_mod.async_setup_services(hass)
    with patch(
        "custom_components.aprilaire_8870.services.async_dispatcher_send",
        side_effect=RuntimeError("dispatcher broke"),
    ):
        # Should not raise.
        await hass.services.async_call(DOMAIN, svc, payload, blocking=True)


# ---- async_unregister_services -------------------------------------------


async def test_unregister_removes_services(hass) -> None:
    await services_mod.async_setup_services(hass)
    await services_mod.async_unregister_services(hass)
    for svc in [
        SERVICE_SET_TEXT_MESSAGE,
        SERVICE_SET_BACKLIGHT,
        SERVICE_RESET_FILTER,
        SERVICE_SET_LOCKOUT,
        SERVICE_CONFIGURE_COS,
    ]:
        assert not hass.services.has_service(DOMAIN, svc)


async def test_unregister_swallows_exception() -> None:
    bad_hass = MagicMock()
    bad_hass.services.async_remove = MagicMock(side_effect=RuntimeError("boom"))
    # Should not raise.
    await services_mod.async_unregister_services(bad_hass)
