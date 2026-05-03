from agent.sleep_mode import (
    apply_sleep_mode_to_maintenance_config,
    resolve_sleep_mode,
)


def test_resolve_sleep_mode_balanced_defaults():
    mode = resolve_sleep_mode({})

    assert mode["enabled"] is True
    assert mode["profile"] == "balanced"
    assert mode["memory_review_interval"] == 8
    assert mode["skill_review_interval"] == 12
    assert mode["background_review"] is True


def test_resolve_sleep_mode_off_disables_background_work():
    mode = resolve_sleep_mode({"sleep_mode": {"profile": "off"}})

    assert mode["enabled"] is False
    assert mode["memory_review_interval"] == 0
    assert mode["skill_review_interval"] == 0
    assert mode["external_memory_sync"] is False
    assert mode["l4_compaction"] is False


def test_sleep_mode_off_profile_overrides_stale_enabled_true():
    mode = resolve_sleep_mode({
        "sleep_mode": {
            "profile": "off",
            "enabled": True,
            "report_actions": True,
        }
    })

    assert mode["enabled"] is False
    assert mode["profile"] == "off"
    assert mode["report_actions"] is False
    assert mode["maintenance_interval_seconds"] == 0


def test_sleep_mode_explicit_overrides_profile():
    mode = resolve_sleep_mode({
        "sleep_mode": {
            "profile": "light",
            "memory_review_interval": 3,
            "background_review": False,
        }
    })

    assert mode["profile"] == "light"
    assert mode["memory_review_interval"] == 3
    assert mode["skill_review_interval"] == 20
    assert mode["background_review"] is False


def test_sleep_mode_applies_gateway_maintenance_cadence():
    maintenance = apply_sleep_mode_to_maintenance_config({
        "maintenance": {"retention_days": 7},
        "sleep_mode": {"profile": "deep"},
    })

    assert maintenance["retention_days"] == 7
    assert maintenance["enabled"] is True
    assert maintenance["l4_periodic_archive"] is True
    assert maintenance["l4_compaction"] is True
    assert maintenance["interval_seconds"] == 7200
    assert maintenance["l4_interval_seconds"] == 3600
    assert maintenance["idle_before_maintenance_seconds"] == 1800


def test_sleep_mode_idle_threshold_override_is_normalized():
    mode = resolve_sleep_mode({
        "sleep_mode": {
            "profile": "balanced",
            "idle_before_maintenance_seconds": "900",
        }
    })

    assert mode["idle_before_maintenance_seconds"] == 900


def test_sleep_mode_off_disables_gateway_maintenance():
    maintenance = apply_sleep_mode_to_maintenance_config({
        "maintenance": {"enabled": True, "retention_loop": True},
        "sleep_mode": {"enabled": False},
    })

    assert maintenance["enabled"] is False
    assert maintenance["retention_loop"] is False
    assert maintenance["l4_periodic_archive"] is False
    assert maintenance["l4_compaction"] is False
