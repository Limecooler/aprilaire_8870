"""Smoke test to confirm the integration imports cleanly under the test harness."""
from __future__ import annotations


def test_parallel_updates_pinned_to_one() -> None:
    """Every entity platform must serialize updates — RS-485 is single-master."""
    from custom_components.aprilaire_8870 import binary_sensor, climate, sensor, switch
    for module in (binary_sensor, climate, sensor, switch):
        assert getattr(module, "PARALLEL_UPDATES", None) == 1, (
            f"{module.__name__} must set PARALLEL_UPDATES = 1"
        )


def test_manifest_quality_scale() -> None:
    """Manifest declares the quality scale tier so HA picks it up."""
    import json
    from pathlib import Path
    manifest = json.loads(
        Path("/Users/bill/personal-code/aprilaire_8870/manifest.json").read_text()
    )
    assert manifest["quality_scale"] == "gold"
    assert manifest["integration_type"] == "hub"
    assert "loggers" in manifest


def test_quality_scale_file_present() -> None:
    """quality_scale.yaml lists per-rule status."""
    import yaml
    from pathlib import Path
    data = yaml.safe_load(
        Path("/Users/bill/personal-code/aprilaire_8870/quality_scale.yaml").read_text()
    )
    assert "rules" in data
    # Spot-check key Gold rules.
    assert data["rules"]["diagnostics"] == "done"
    assert data["rules"]["devices"] == "done"


def test_py_typed_marker_present() -> None:
    """Empty py.typed marker file enables type-checker consumption per PEP 561."""
    from pathlib import Path
    assert Path("/Users/bill/personal-code/aprilaire_8870/py.typed").exists()


def test_all_modules_import() -> None:
    """Every module that's part of the integration must import cleanly."""
    from custom_components.aprilaire_8870 import (
        binary_sensor,
        climate,
        config_flow,
        connection,
        const,
        coordinator,
        device,
        protocol,
        sensor,
        services,
        switch,
    )
    import custom_components.aprilaire_8870 as init_module

    assert const.DOMAIN == "aprilaire_8870"
    assert protocol.AprilaireProtocol is not None
    assert connection.SerialServerConnection is not None
    assert connection.ComPortConnection is not None
    assert device.AprilaireDevice is not None
    assert coordinator.AprilaireDataUpdateCoordinator is not None
    assert services.async_setup_services is not None
    assert config_flow.AprilaireConfigFlow is not None
    assert climate.AprilaireClimate is not None
    assert sensor.AprilaireTemperatureSensor is not None
    assert binary_sensor.AprilaireBinarySensor is not None
    assert switch.AprilaireSwitch is not None
    assert init_module.async_setup_entry is not None
