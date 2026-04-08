"""
Backfill historical run data from all JSONL sessions found on disk.

Groups every session by calendar date and writes one run entry per day
to runs / runs_projects, skipping dates that already exist in the DB.
Also upserts all sessions and prompts into the sessions/prompts tables.

Usage:
    cd ~/.claude/token-cost-analysis
    .venv/bin/python3 -m src.script.backfill

Env vars:
    BACKFILL_SINCE_DATE   Only backfill from this date onwards (ISO, e.g. 2026-04-01).
                          Default: 2026-04-01.

Future improvements:
    - Add PROJECT_INCLUDE_PREFIX filtering to runs/runs_projects so that only matching
      projects are included in the aggregated history. Currently all projects are
      included regardless of account. This would let you cleanly separate personal
      vs. work account costs in the DB.
"""

from __future__ import annotations

import dataclasses
import os
from collections import defaultdict
from datetime import datetime

from .analysis import ProjectAnalyzer
from .config import Config
from .history import HistoryStore
from .models import ProjectSummary, SessionData, TokenUsage
from .parser import SessionParser
from .pricing import PricingService

_DEFAULT_SINCE_DATE = "2026-04-01"


def _session_date(session: SessionData) -> str | None:
    if not session.timestamp_start:
        return None
    try:
        ts = datetime.fromisoformat(session.timestamp_start.replace("Z", "+00:00"))
        return ts.date().isoformat()
    except ValueError:
        return None


def _build_dated_summaries(
    all_projects: dict[str, list[SessionData]],
    strip_prefix: str,
    since_date: str | None,
) -> dict[str, list[ProjectSummary]]:
    """Group sessions by calendar date and build per-project summaries per day."""

    def _normalize(name: str) -> str:
        return name[len(strip_prefix):] if strip_prefix and name.startswith(strip_prefix) else name

    daily: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {
        "usage": TokenUsage(), "cost": 0.0, "sessions": 0,
        "subagent_count": 0, "subagent_tokens": 0,
    }))

    for intermediate, sessions in all_projects.items():
        display = _normalize(intermediate)
        for session in sessions:
            date = _session_date(session)
            if not date:
                continue
            if since_date and date < since_date:
                continue
            p = daily[date][display]
            p["usage"] = p["usage"] + session.usage
            p["cost"] += session.cost
            p["sessions"] += 1
            for sub in session.subagent_sessions:
                p["usage"] = p["usage"] + sub.usage
                p["cost"] += sub.cost
                p["subagent_count"] += 1
                p["subagent_tokens"] += sub.usage.total

    result: dict[str, list[ProjectSummary]] = {}
    for date, projects in daily.items():
        summaries = [
            ProjectSummary(
                project=proj,
                sessions=data["sessions"],
                usage=data["usage"],
                total_cost=data["cost"],
                subagent_count=data["subagent_count"],
                subagent_tokens=data["subagent_tokens"],
            )
            for proj, data in projects.items()
        ]
        summaries.sort(key=lambda x: x.usage.total, reverse=True)
        result[date] = summaries

    return result


def backfill() -> None:
    since_date = os.environ.get("BACKFILL_SINCE_DATE", _DEFAULT_SINCE_DATE)
    print(f"Backfilling from {since_date} (set BACKFILL_SINCE_DATE to override)")

    config = dataclasses.replace(
        Config.from_env(),
        cutoff=None,
        project_include_prefix="",  # include all projects when parsing
    )

    db_path = config.database_dir / "claude.db"
    pricing = PricingService(db_path)
    pricing.load()

    parser = SessionParser(pricing)
    analyzer = ProjectAnalyzer(config, parser)

    print("Scanning ALL sessions (no date filter)...")
    all_projects, _ = analyzer.analyze()

    total_sessions = sum(len(v) for v in all_projects.values())
    print(f"Found {total_sessions} sessions across {len(all_projects)} projects")

    history = HistoryStore(db_path, config.project_strip_prefix)

    # Write all individual sessions (upsert — safe to run multiple times)
    history.write_sessions(all_projects)

    # Build per-day summaries filtered by since_date and backfill runs table
    dated_summaries = _build_dated_summaries(
        all_projects, config.project_strip_prefix, since_date
    )
    print(f"Detected {len(dated_summaries)} unique dates with activity from {since_date}")

    written = history.write_historical_runs(dated_summaries)
    skipped = len(dated_summaries) - written
    print(f"Backfill complete: {written} new dates written, {skipped} already existed")


if __name__ == "__main__":
    backfill()
