"""Project analysis: aggregate sessions, build summaries, find costly items."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .config import Config
from .models import ProjectSummary, SessionData, TokenUsage
from .parser import SessionParser


class ProjectAnalyzer:
    """Scans all JSONL project directories and builds per-project summaries."""

    def __init__(self, config: Config, parser: SessionParser):
        self._config = config
        self._parser = parser

    def analyze(self) -> tuple[dict[str, list[SessionData]], dict[str, list[SessionData]]]:
        """
        Scan all project directories.

        Returns:
            all_projects: every project keyed by intermediate name (OS-prefix stripped,
                          PROJECT_STRIP_PREFIX not yet removed) — used for CSV history.
            display_projects: filtered projects keyed by display name (both prefixes
                              stripped) — used for output, Slack, email.
        """
        all_projects: dict[str, list[SessionData]] = defaultdict(list)
        display_projects: dict[str, list[SessionData]] = defaultdict(list)
        cfg = self._config
        os_prefix = f"-Users-{cfg.username.replace('.', '-')}-"

        for project_dir in sorted(cfg.projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            dir_name = project_dir.name
            intermediate = (
                dir_name[len(os_prefix):] if dir_name.startswith(os_prefix) else dir_name
            )
            display_name = self._to_display_name(intermediate)
            passes_filter = (
                not cfg.project_include_prefix
                or intermediate.startswith(cfg.project_include_prefix)
            )

            has_any_tokens = False
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                session = self._parser.parse(jsonl_file)
                if session and session.usage.total > 0:
                    has_any_tokens = True
                    if self._in_range(session):
                        all_projects[intermediate].append(session)
                        if passes_filter:
                            display_projects[display_name].append(session)

            # Ensure the project key exists in all_projects even if no sessions
            # fall within the current date range, so CSV captures all known projects.
            if has_any_tokens:
                _ = all_projects[intermediate]

        return dict(all_projects), dict(display_projects)

    def summarize(self, projects: dict[str, list[SessionData]]) -> list[ProjectSummary]:
        """Build per-project summaries sorted by total tokens descending."""
        summaries = []
        for project_name, sessions in projects.items():
            usage = TokenUsage()
            total_cost = 0.0
            subagent_tokens = 0
            subagent_count = 0

            for session in sessions:
                usage = usage + session.usage
                total_cost += session.cost
                for sub in session.subagent_sessions:
                    usage = usage + sub.usage
                    subagent_tokens += sub.usage.total
                    subagent_count += 1
                    total_cost += sub.cost

            summaries.append(ProjectSummary(
                project=project_name,
                sessions=len(sessions),
                usage=usage,
                total_cost=total_cost,
                subagent_count=subagent_count,
                subagent_tokens=subagent_tokens,
            ))

        summaries.sort(key=lambda x: x.usage.total, reverse=True)
        return summaries

    def find_costly_sessions(
        self,
        projects: dict[str, list[SessionData]],
        top_n: int = 20,
    ) -> list[tuple[str, SessionData]]:
        """Return the top N sessions by cost across all projects."""
        all_sessions = [
            (proj, session)
            for proj, sessions in projects.items()
            for session in sessions
        ]
        all_sessions.sort(key=lambda x: x[1].cost, reverse=True)
        return all_sessions[:top_n]

    def find_costly_subagents(
        self,
        projects: dict[str, list[SessionData]],
        top_n: int = 20,
    ) -> list[tuple[str, str, SessionData]]:
        """Return the top N subagent sessions by cost."""
        all_subs = [
            (proj, session.session_id, sub)
            for proj, sessions in projects.items()
            for session in sessions
            for sub in session.subagent_sessions
        ]
        all_subs.sort(key=lambda x: x[2].cost, reverse=True)
        return all_subs[:top_n]

    def _to_display_name(self, intermediate: str) -> str:
        prefix = self._config.project_strip_prefix
        if prefix and intermediate.startswith(prefix):
            return intermediate[len(prefix):]
        return intermediate

    def _in_range(self, session: SessionData) -> bool:
        cutoff = self._config.cutoff
        if not cutoff or not session.timestamp_start:
            return True
        try:
            ts = datetime.fromisoformat(session.timestamp_start.replace("Z", "+00:00"))
            return ts >= cutoff
        except ValueError:
            return True
