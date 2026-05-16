"""Tests for the const module — covers all top-level definitions."""
from __future__ import annotations

from custom_components.aprilaire_8870 import const


def test_basic_constants() -> None:
    assert const.DOMAIN == "aprilaire_8870"
    assert const.DEFAULT_PORT == 23
    assert const.DEFAULT_BAUDRATE == 9600
    assert const.DEFAULT_SCAN_INTERVAL == 600


def test_default_cos_flags_subset() -> None:
    for flag in const.DEFAULT_COS_FLAGS:
        assert flag.startswith("c")


def test_cos_prefix_pattern_coverage() -> None:
    for flag in const.DEFAULT_COS_FLAGS:
        # Every default flag must have a matching prefix pattern.
        assert flag in const.COS_PREFIX_PATTERN


def test_hvac_mode_round_trip() -> None:
    # Every Aprilaire value maps to an HA mode, and vice versa.
    for ap, ha in const.HVAC_MODE_APRILAIRE_TO_HA.items():
        assert ha in const.HA_TO_APRILAIRE_HVAC_MODE


def test_fan_mode_round_trip() -> None:
    for ap, ha in const.FAN_MODE_APRILAIRE_TO_HA.items():
        assert ha in const.HA_TO_APRILAIRE_FAN_MODE


def test_cos_aliases_match_originals() -> None:
    assert const.COS_HVAC_RELAYS == const.COS_FLAG_HVAC_RELAYS
    assert const.COS_TEMPERATURE == const.COS_FLAG_TEMPERATURE
    assert const.COS_ERRORS == const.COS_FLAG_ERRORS


def test_signals_have_domain_prefix() -> None:
    assert const.SIGNAL_CONNECTION_STATE_CHANGED.startswith(const.DOMAIN)
    assert const.SERVICE_SIGNAL_SET_TEXT_MESSAGE.startswith(const.DOMAIN)


def test_platforms_list() -> None:
    from homeassistant.const import Platform
    expected = {Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH}
    assert set(const.PLATFORMS) == expected
