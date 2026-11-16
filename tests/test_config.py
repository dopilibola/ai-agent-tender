import pytest
from pydantic import ValidationError

from app.config import AppConfig, PlatformConfig


def test_shipped_yaml_files_load(settings):
    assert settings.owner_chat_id == 42
    assert settings.config.timezone == "Asia/Tashkent"
    assert settings.config.gemini.fast_model
    assert "Python" in settings.profile.skills
    assert settings.config.platforms["truelancer"].max_project_budget_usd == 100
    # Every platform ships in dry-run mode.
    assert all(p.dry_run for p in settings.config.platforms.values())


def test_model_names_required():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"gemini": {"fast_model": " ", "strong_model": "x"}})


def test_bad_timezone_and_time_rejected():
    base = {"gemini": {"fast_model": "a", "strong_model": "b"}}
    with pytest.raises(ValidationError):
        AppConfig.model_validate({**base, "timezone": "Mars/Base"})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({**base, "daily_summary_time": "25:00"})


def test_action_delay_order():
    with pytest.raises(ValidationError):
        PlatformConfig(min_action_delay_s=50, max_action_delay_s=40)
