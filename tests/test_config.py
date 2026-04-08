"""Tests for token_cost_analysis.config."""

import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.script import Config


class TestConfigFromEnv:
    def _make(self, env: dict | None = None) -> Config:
        base = {
            "SINCE_DATE": "",
            "SINCE_DAYS": "1",
            "EMAIL_RECIPIENT": "",
            "EMAIL_ENABLED": "false",
            "SLACK_BOT_TOKEN": "",
            "SLACK_CHANNEL_ID": "",
            "SLACK_ENABLED": "true",
            "PROJECT_STRIP_PREFIX": "",
            "PROJECT_INCLUDE_PREFIX": "",
        }
        if env:
            base.update(env)
        with patch.dict(os.environ, base, clear=False):
            return Config.from_env()

    def test_defaults(self):
        cfg = self._make()
        assert cfg.email_enabled is False
        assert cfg.slack_enabled is True
        assert cfg.since_days == 1
        assert cfg.since_date == ""

    def test_since_date_takes_precedence_over_since_days(self):
        cfg = self._make({"SINCE_DATE": "2026-03-01", "SINCE_DAYS": "7"})
        assert cfg.cutoff is not None
        assert cfg.cutoff.year == 2026
        assert cfg.cutoff.month == 3
        assert cfg.cutoff.day == 1

    def test_since_days_sets_cutoff(self):
        before = datetime.now(timezone.utc)
        cfg = self._make({"SINCE_DATE": "", "SINCE_DAYS": "7"})

        assert cfg.cutoff is not None
        expected_approx = before - timedelta(days=7)
        # Allow a few seconds of clock drift
        assert abs((cfg.cutoff - expected_approx).total_seconds()) < 5

    def test_since_days_zero_means_no_cutoff(self):
        cfg = self._make({"SINCE_DATE": "", "SINCE_DAYS": "0"})
        assert cfg.cutoff is None

    def test_email_enabled_flag(self):
        cfg = self._make({"EMAIL_ENABLED": "true", "EMAIL_RECIPIENT": "x@y.com"})
        assert cfg.email_enabled is True
        assert cfg.email_recipient == "x@y.com"

    def test_slack_disabled_flag(self):
        cfg = self._make({"SLACK_ENABLED": "false"})
        assert cfg.slack_enabled is False

    def test_project_strip_prefix(self):
        cfg = self._make({"PROJECT_STRIP_PREFIX": "my-prefix-"})
        assert cfg.project_strip_prefix == "my-prefix-"

    def test_project_include_prefix_empty_means_no_filter(self):
        cfg = self._make({"PROJECT_INCLUDE_PREFIX": ""})
        assert cfg.project_include_prefix == ""

    def test_paths_are_based_on_home(self):
        cfg = self._make()
        import pathlib
        assert cfg.home == pathlib.Path.home()
        assert cfg.projects_dir == cfg.home / ".claude" / "projects"
        assert cfg.database_dir == cfg.home / ".claude" / "token-cost-analysis" / "database"

    def test_username_derived_from_home(self):
        cfg = self._make()
        import pathlib
        assert cfg.username == pathlib.Path.home().name

    def test_tz_local_is_buenos_aires(self):
        cfg = self._make()
        assert str(cfg.tz_local) == "America/Argentina/Buenos_Aires"

    def test_config_is_frozen(self):
        cfg = self._make()
        with pytest.raises((AttributeError, TypeError)):
            cfg.slack_enabled = False  # type: ignore[misc]
