"""Tests for token_cost_analysis.analysis."""

import pytest
from datetime import datetime, timezone
from pathlib import Path

from src.script.analysis import ProjectAnalyzer
from src.script import Config
from src.script import TokenUsage, SessionData, Prompt
from src.script import SessionParser
from .conftest import make_pricing_service, make_jsonl, SAMPLE_SESSION_JSONL_EVENTS


def _make_config(projects_dir: Path, **kwargs) -> Config:
    defaults = dict(
        tz_local=None,
        home=Path.home(),
        username="testuser",
        projects_dir=projects_dir,
        output_dir=projects_dir / "output",
        database_dir=projects_dir / "db",
        email_recipient="",
        email_enabled=False,
        slack_bot_token="",
        slack_channel_id="",
        slack_enabled=False,
        project_strip_prefix="mycompany-",
        project_include_prefix="mycompany-",
        since_date="",
        since_days=1,
        cutoff=None,
    )
    defaults.update(kwargs)
    return Config(**defaults)


def _make_session(
    session_id: str = "s1",
    cost: float = 0.05,
    usage: TokenUsage | None = None,
    timestamp_start: str = "2026-04-07T10:00:00Z",
    subagent_sessions: list | None = None,
) -> SessionData:
    return SessionData(
        file="/tmp/x.jsonl",
        session_id=session_id,
        agent_id=None,
        is_subagent=False,
        model="claude-sonnet-4-6-20250519",
        models=["claude-sonnet-4-6-20250519"],
        timestamp_start=timestamp_start,
        usage=usage or TokenUsage(input_tokens=100, output_tokens=50),
        cost=cost,
        prompts=[Prompt(text="hello", timestamp=timestamp_start, entrypoint="cli")],
        subagent_sessions=subagent_sessions or [],
    )


class TestProjectAnalyzerSummarize:
    def _make_analyzer(self, tmp_path):
        cfg = _make_config(tmp_path)
        svc = make_pricing_service(tmp_path)
        parser = SessionParser(svc)
        return ProjectAnalyzer(cfg, parser)

    def test_empty_projects(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        result = analyzer.summarize({})
        assert result == []

    def test_single_project_single_session(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        session = _make_session(usage=TokenUsage(input_tokens=100, output_tokens=50))
        result = analyzer.summarize({"proj-a": [session]})
        assert len(result) == 1
        s = result[0]
        assert s.project == "proj-a"
        assert s.sessions == 1
        assert s.usage.input_tokens == 100
        assert s.usage.output_tokens == 50
        assert s.total_cost == pytest.approx(0.05)

    def test_multiple_sessions_aggregated(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        s1 = _make_session(usage=TokenUsage(input_tokens=100), cost=0.01)
        s2 = _make_session(usage=TokenUsage(input_tokens=200), cost=0.02)
        result = analyzer.summarize({"proj": [s1, s2]})
        assert result[0].usage.input_tokens == 300
        assert result[0].total_cost == pytest.approx(0.03)
        assert result[0].sessions == 2

    def test_subagent_tokens_included_in_total(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        sub = _make_session(
            session_id="sub1",
            usage=TokenUsage(input_tokens=500),
            cost=0.10,
        )
        parent = _make_session(
            usage=TokenUsage(input_tokens=100),
            cost=0.01,
            subagent_sessions=[sub],
        )
        result = analyzer.summarize({"proj": [parent]})
        s = result[0]
        assert s.usage.input_tokens == 600  # parent + sub
        assert s.total_cost == pytest.approx(0.11)
        assert s.subagent_count == 1
        assert s.subagent_tokens == sub.usage.total

    def test_sorted_by_total_tokens_descending(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        small = _make_session(usage=TokenUsage(input_tokens=10))
        large = _make_session(usage=TokenUsage(input_tokens=1000))
        result = analyzer.summarize({"big": [large], "small": [small]})
        assert result[0].project == "big"
        assert result[1].project == "small"


class TestProjectAnalyzerFindCostly:
    def _make_analyzer(self, tmp_path):
        cfg = _make_config(tmp_path)
        return ProjectAnalyzer(cfg, SessionParser(make_pricing_service(tmp_path)))

    def test_find_costly_sessions_sorted_by_cost(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        cheap = _make_session(session_id="cheap", cost=0.01)
        expensive = _make_session(session_id="expensive", cost=1.00)
        projects = {"proj": [cheap, expensive]}
        result = analyzer.find_costly_sessions(projects, top_n=5)
        assert result[0][1].session_id == "expensive"
        assert result[1][1].session_id == "cheap"

    def test_find_costly_sessions_respects_top_n(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        sessions = [_make_session(session_id=f"s{i}", cost=float(i)) for i in range(10)]
        result = analyzer.find_costly_sessions({"proj": sessions}, top_n=3)
        assert len(result) == 3

    def test_find_costly_subagents(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        sub_cheap = _make_session(session_id="sub-cheap", cost=0.01)
        sub_expensive = _make_session(session_id="sub-expensive", cost=5.0)
        parent = _make_session(subagent_sessions=[sub_cheap, sub_expensive])
        result = analyzer.find_costly_subagents({"proj": [parent]}, top_n=10)
        assert result[0][2].session_id == "sub-expensive"
        assert result[1][2].session_id == "sub-cheap"


class TestProjectAnalyzerInRange:
    def _make_analyzer(self, tmp_path, cutoff=None):
        cfg = _make_config(tmp_path, cutoff=cutoff)
        return ProjectAnalyzer(cfg, SessionParser(make_pricing_service(tmp_path)))

    def test_no_cutoff_means_always_in_range(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path, cutoff=None)
        session = _make_session(timestamp_start="2020-01-01T00:00:00Z")
        assert analyzer._in_range(session) is True

    def test_session_after_cutoff_is_in_range(self, tmp_path):
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
        analyzer = self._make_analyzer(tmp_path, cutoff=cutoff)
        session = _make_session(timestamp_start="2026-04-07T10:00:00Z")
        assert analyzer._in_range(session) is True

    def test_session_before_cutoff_is_out_of_range(self, tmp_path):
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
        analyzer = self._make_analyzer(tmp_path, cutoff=cutoff)
        session = _make_session(timestamp_start="2026-03-15T10:00:00Z")
        assert analyzer._in_range(session) is False

    def test_missing_timestamp_is_always_in_range(self, tmp_path):
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
        analyzer = self._make_analyzer(tmp_path, cutoff=cutoff)
        session = _make_session(timestamp_start=None)
        assert analyzer._in_range(session) is True


class TestProjectAnalyzerToDisplayName:
    def _make_analyzer(self, tmp_path, strip_prefix="mycompany-"):
        cfg = _make_config(tmp_path, project_strip_prefix=strip_prefix)
        return ProjectAnalyzer(cfg, SessionParser(make_pricing_service(tmp_path)))

    def test_strips_prefix(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        assert analyzer._to_display_name("mycompany-my-project") == "my-project"

    def test_no_strip_when_no_match(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path)
        assert analyzer._to_display_name("other-project") == "other-project"

    def test_empty_prefix_no_strip(self, tmp_path):
        analyzer = self._make_analyzer(tmp_path, strip_prefix="")
        assert analyzer._to_display_name("mycompany-my-project") == "mycompany-my-project"


class TestProjectAnalyzerAnalyze:
    """Integration-style tests using real temp files."""

    def _write_jsonl(self, path: Path, events: list[dict]) -> None:
        path.write_text(make_jsonl(events))

    def test_analyze_scans_project_dirs(self, tmp_path):
        projects_dir = tmp_path / "projects"
        # Create project dir with OS-prefix style naming
        proj_dir = projects_dir / "-Users-testuser-mycompany-my-project"
        proj_dir.mkdir(parents=True)
        self._write_jsonl(proj_dir / "sess1.jsonl", SAMPLE_SESSION_JSONL_EVENTS)

        cfg = _make_config(
            projects_dir,
            username="testuser",
            project_strip_prefix="mycompany-",
            project_include_prefix="mycompany-",
        )
        svc = make_pricing_service(tmp_path)
        analyzer = ProjectAnalyzer(cfg, SessionParser(svc))
        all_projects, display_projects = analyzer.analyze()

        # all_projects uses intermediate name
        assert "mycompany-my-project" in all_projects
        # display_projects uses display name (prefix stripped)
        assert "my-project" in display_projects

    def test_analyze_filters_projects_by_include_prefix(self, tmp_path):
        projects_dir = tmp_path / "projects"
        # Two projects: one matching, one not
        for name in ["-Users-testuser-mycompany-included", "-Users-testuser-other-excluded"]:
            d = projects_dir / name
            d.mkdir(parents=True)
            self._write_jsonl(d / "sess.jsonl", SAMPLE_SESSION_JSONL_EVENTS)

        cfg = _make_config(
            projects_dir,
            username="testuser",
            project_strip_prefix="mycompany-",
            project_include_prefix="mycompany-",
        )
        _, display_projects = ProjectAnalyzer(
            cfg, SessionParser(make_pricing_service(tmp_path))
        ).analyze()

        assert "included" in display_projects
        assert "other-excluded" not in display_projects

    def test_analyze_all_projects_includes_non_filtered(self, tmp_path):
        projects_dir = tmp_path / "projects"
        for name in ["-Users-testuser-mycompany-included", "-Users-testuser-other-excluded"]:
            d = projects_dir / name
            d.mkdir(parents=True)
            self._write_jsonl(d / "sess.jsonl", SAMPLE_SESSION_JSONL_EVENTS)

        cfg = _make_config(
            projects_dir,
            username="testuser",
            project_strip_prefix="mycompany-",
            project_include_prefix="mycompany-",
        )
        all_projects, _ = ProjectAnalyzer(
            cfg, SessionParser(make_pricing_service(tmp_path))
        ).analyze()

        assert "mycompany-included" in all_projects
        assert "other-excluded" in all_projects
