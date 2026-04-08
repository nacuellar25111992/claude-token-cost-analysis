"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    tz_local: ZoneInfo
    home: Path
    username: str
    projects_dir: Path
    output_dir: Path
    database_dir: Path
    email_recipient: str
    email_enabled: bool
    slack_bot_token: str
    slack_channel_id: str
    slack_enabled: bool
    open_browser: bool
    project_strip_prefix: str
    project_include_prefix: str
    since_date: str
    since_days: int
    cutoff: datetime | None

    @classmethod
    def from_env(cls) -> "Config":
        home = Path.home()
        username = home.name
        base_dir = home / ".claude" / "token-cost-analysis"

        load_dotenv(base_dir / ".env", override=False)

        since_date = os.environ.get("SINCE_DATE", "")
        since_days = int(os.environ.get("SINCE_DAYS", "1"))

        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        cutoff: datetime | None = None
        if since_date:
            cutoff = datetime.fromisoformat(since_date).replace(tzinfo=timezone.utc)
        elif since_days > 0:
            local_now = datetime.now(tz)
            local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff = (local_midnight - timedelta(days=since_days)).astimezone(timezone.utc)

        return cls(
            tz_local=ZoneInfo("America/Argentina/Buenos_Aires"),
            home=home,
            username=username,
            projects_dir=home / ".claude" / "projects",
            output_dir=home / "tuin" / "analysis" / "tokens",
            database_dir=base_dir / "database",
            email_recipient=os.environ.get("EMAIL_RECIPIENT", ""),
            email_enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
            slack_channel_id=os.environ.get("SLACK_CHANNEL_ID", ""),
            slack_enabled=os.environ.get("SLACK_ENABLED", "true").lower() == "true",
            open_browser=os.environ.get("OPEN_BROWSER", "false").lower() == "true",
            project_strip_prefix=os.environ.get(
                "PROJECT_STRIP_PREFIX", ""
            ),
            project_include_prefix=os.environ.get(
                "PROJECT_INCLUDE_PREFIX", ""
            ),
            since_date=since_date,
            since_days=since_days,
            cutoff=cutoff,
        )
