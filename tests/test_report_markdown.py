"""Tests for token_cost_analysis.report_markdown."""

from pathlib import Path

from src.script.analysis import ProjectAnalyzer
from src.script import Config
from src.script import TokenUsage, SessionData, Prompt, ProjectSummary
from src.script import SessionParser
from src.script.report_markdown import MarkdownReporter, _fmt_tokens, _fmt_cost
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
        project_strip_prefix="mycompany-",
        project_include_prefix="mycompany-",
        since_date="",
        since_days=1,
        cutoff=None,
    )


_DEFAULT_PROMPTS = [Prompt(text="What is 2+2?", timestamp="2026-04-07T10:00:00Z", entrypoint="cli")]


def _make_session(
    session_id: str = "sess-001",
    cost: float = 0.50,
    usage: TokenUsage | None = None,
    prompts: list | None = None,
) -> SessionData:
    return SessionData(
        file="/tmp/x.jsonl",
        session_id=session_id,
        agent_id=None,
        is_subagent=False,
        model="claude-sonnet-4-6-20250519",
        models=["claude-sonnet-4-6-20250519"],
        timestamp_start="2026-04-07T10:00:00Z",
        usage=usage or TokenUsage(input_tokens=1000, output_tokens=500),
        cost=cost,
        prompts=_DEFAULT_PROMPTS if prompts is None else prompts,
        subagent_sessions=[],
    )


def _make_reporter(tmp_path: Path) -> MarkdownReporter:
    svc = make_pricing_service(tmp_path)
    cfg = _make_config(tmp_path)
    parser = SessionParser(svc)
    analyzer = ProjectAnalyzer(cfg, parser)
    return MarkdownReporter(cfg, analyzer, svc)


class TestFmtHelpers:
    def test_fmt_tokens(self):
        assert _fmt_tokens(1234567) == "1,234,567"
        assert _fmt_tokens(0) == "0"

    def test_fmt_cost(self):
        assert _fmt_cost(1.5) == "$1.50"
        assert _fmt_cost(0.001) == "$0.00"


class TestMarkdownReporterWriteReport:
    def test_creates_file(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="my-proj", sessions=1,
            usage=session.usage, total_cost=session.cost,
            subagent_count=0, subagent_tokens=0,
        )]
        path = reporter.write_report({"my-proj": [session]}, summaries)
        assert path.exists()

    def test_report_contains_grand_totals_header(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="my-proj", sessions=1,
            usage=session.usage, total_cost=session.cost,
            subagent_count=0, subagent_tokens=0,
        )]
        path = reporter.write_report({"my-proj": [session]}, summaries)
        content = path.read_text()
        assert "Grand Totals" in content
        assert "By Project" in content
        assert "Most Costly Sessions" in content

    def test_report_lists_project(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        summaries = [ProjectSummary(
            project="my-proj", sessions=1,
            usage=session.usage, total_cost=session.cost,
            subagent_count=0, subagent_tokens=0,
        )]
        path = reporter.write_report({"my-proj": [session]}, summaries)
        assert "my-proj" in path.read_text()

    def test_report_includes_cost(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session(cost=1.23)
        summaries = [ProjectSummary(
            project="my-proj", sessions=1,
            usage=session.usage, total_cost=1.23,
            subagent_count=0, subagent_tokens=0,
        )]
        path = reporter.write_report({"my-proj": [session]}, summaries)
        assert "$1.23" in path.read_text()

    def test_report_first_prompt_included(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session(prompts=[
            Prompt(text="Explain quantum entanglement", timestamp="2026-04-07T10:00:00Z", entrypoint="")
        ])
        summaries = [ProjectSummary(
            project="my-proj", sessions=1,
            usage=session.usage, total_cost=session.cost,
            subagent_count=0, subagent_tokens=0,
        )]
        path = reporter.write_report({"my-proj": [session]}, summaries)
        assert "Explain quantum entanglement" in path.read_text()


class TestMarkdownReporterWritePrompts:
    def test_creates_prompt_file_per_project(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        reporter.write_prompts({"my-project": [session]})
        prompt_file = tmp_path / "output" / "prompts" / "my-project.md"
        assert prompt_file.exists()

    def test_prompt_file_contains_prompt_text(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session(prompts=[
            Prompt(text="Tell me about black holes", timestamp="2026-04-07T10:00:00Z", entrypoint="cli")
        ])
        reporter.write_prompts({"science": [session]})
        content = (tmp_path / "output" / "prompts" / "science.md").read_text()
        assert "Tell me about black holes" in content

    def test_skips_project_with_no_prompts(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session(prompts=[])
        reporter.write_prompts({"empty-proj": [session]})
        assert not (tmp_path / "output" / "prompts" / "empty-proj.md").exists()

    def test_safe_filename_for_special_chars(self, tmp_path):
        reporter = _make_reporter(tmp_path)
        session = _make_session()
        reporter.write_prompts({"my/project name": [session]})
        # Should replace / and spaces
        files = list((tmp_path / "output" / "prompts").glob("*.md"))
        assert len(files) == 1
        assert "/" not in files[0].name
