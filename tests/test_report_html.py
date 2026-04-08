"""Tests for token_cost_analysis.report_html."""

from datetime import datetime, timezone
from pathlib import Path

from src.script.analysis import ProjectAnalyzer
from src.script import Config
from src.script import TokenUsage, SessionData, Prompt, ProjectSummary
from src.script import SessionParser
from src.script import HTMLReporter
from .conftest import make_pricing_service


def _make_config(tmp_path: Path) -> Config:
    return Config(
        tz_local=None,
        home=Path.home(),
        username="testuser",
        projects_dir=tmp_path / "projects",
        output_dir=tmp_path / "output",
        database_dir=tmp_path / "db",
        email_recipient="",
        email_enabled=False,
        slack_bot_token="",
        slack_channel_id="",
        slack_enabled=False,
        project_strip_prefix="",
        project_include_prefix="",
        since_date="",
        since_days=1,
        cutoff=None,
    )


def _make_session(cost: float = 0.50, usage: TokenUsage | None = None) -> SessionData:
    return SessionData(
        file="/tmp/x.jsonl",
        session_id="sess-001",
        agent_id=None,
        is_subagent=False,
        model="claude-sonnet-4-6-20250519",
        models=["claude-sonnet-4-6-20250519"],
        timestamp_start="2026-04-07T10:00:00Z",
        usage=usage or TokenUsage(input_tokens=1000, output_tokens=500),
        cost=cost,
        prompts=[Prompt(text="What is 2+2?", timestamp="2026-04-07T10:00:00Z", entrypoint="")],
        subagent_sessions=[],
    )


def _make_reporter(tmp_path: Path) -> HTMLReporter:
    svc = make_pricing_service(tmp_path)
    cfg = _make_config(tmp_path)
    analyzer = ProjectAnalyzer(cfg, SessionParser(svc))
    return HTMLReporter(analyzer)


class TestHTMLReporterGenerate:
    def test_returns_valid_html_structure(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="my-proj", sessions=1, usage=session.usage,
            total_cost=session.cost, subagent_count=0, subagent_tokens=0,
        )]
        html = reporter.generate(summaries, {"my-proj": [session]}, {}, cutoff=None)
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html

    def test_contains_cost(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session(cost=2.34)
        summaries = [ProjectSummary(
            project="my-proj", sessions=1, usage=session.usage,
            total_cost=2.34, subagent_count=0, subagent_tokens=0,
        )]
        html = reporter.generate(summaries, {"my-proj": [session]}, {}, cutoff=None)
        assert "$2.34" in html

    def test_contains_project_name_escaped(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        proj_name = "my <proj> & test"
        summaries = [ProjectSummary(
            project=proj_name, sessions=1, usage=session.usage,
            total_cost=session.cost, subagent_count=0, subagent_tokens=0,
        )]
        html = reporter.generate(summaries, {proj_name: [session]}, {}, cutoff=None)
        # HTML-escaped version should appear, not raw HTML
        assert "my &lt;proj&gt; &amp; test" in html
        assert "<proj>" not in html  # raw unescaped should not appear as tag

    def test_contains_date_range_when_cutoff_set(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
        session = _make_session()
        summaries = [ProjectSummary(
            project="p", sessions=1, usage=session.usage,
            total_cost=session.cost, subagent_count=0, subagent_tokens=0,
        )]
        html = reporter.generate(summaries, {"p": [session]}, {}, cutoff=cutoff)
        assert "2026-04-01" in html

    def test_trend_block_rendered_when_comparisons_present(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="p", sessions=1, usage=session.usage,
            total_cost=session.cost, subagent_count=0, subagent_tokens=0,
        )]
        comparisons = {
            "yesterday": {
                "run_at": datetime(2026, 4, 6, tzinfo=timezone.utc),
                "delta_cost": 0.10,
                "delta_tokens": 500,
                "cache_hit_pct": 30.0,
                "pct": 10.0,
            }
        }
        html = reporter.generate(summaries, {"p": [session]}, comparisons, cutoff=None)
        assert "Trend" in html
        assert "+$0.10" in html

    def test_trend_block_empty_when_no_comparisons(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="p", sessions=1, usage=session.usage,
            total_cost=session.cost, subagent_count=0, subagent_tokens=0,
        )]
        html = reporter.generate(summaries, {"p": [session]}, {}, cutoff=None)
        assert "Trend" not in html

    def test_all_time_label_when_no_cutoff(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="p", sessions=1, usage=session.usage,
            total_cost=session.cost, subagent_count=0, subagent_tokens=0,
        )]
        html = reporter.generate(summaries, {"p": [session]}, {}, cutoff=None)
        assert "All time" in html


class TestHTMLReporterFmtHelpers:
    def _reporter(self, tmp_path):
        return _make_reporter(tmp_path)

    def test_fmt_tokens_millions(self, tmp_path):
        r = self._reporter(tmp_path)
        assert r._fmt_tokens(1_500_000) == "1.5M"

    def test_fmt_tokens_thousands(self, tmp_path):
        r = self._reporter(tmp_path)
        assert r._fmt_tokens(5_000) == "5K"

    def test_fmt_tokens_small(self, tmp_path):
        r = self._reporter(tmp_path)
        assert r._fmt_tokens(42) == "42"

    def test_delta_parts_positive_cost(self, tmp_path):
        r = self._reporter(tmp_path)
        cost_str, color, tok_str = r._delta_parts(0.50, 1000)
        assert cost_str == "+$0.50"
        assert color == "#e67e22"  # orange for positive delta

    def test_delta_parts_negative_cost(self, tmp_path):
        r = self._reporter(tmp_path)
        cost_str, color, tok_str = r._delta_parts(-0.50, -1000)
        assert "0.50" in cost_str  # sign + value
        assert color == "#27ae60"  # green for negative delta
