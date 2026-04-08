"""Tests for token_cost_analysis.notifier_slack."""

from pathlib import Path
from zoneinfo import ZoneInfo

from src.script import TokenUsage, SessionData, Prompt, ProjectSummary
from src.script import SlackNotifier, _shorten_project, _shorten_model
from .conftest import make_pricing_service


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _make_session(
    session_id: str = "sess-001",
    cost: float = 0.50,
    usage: TokenUsage | None = None,
    prompts: list | None = None,
    model: str = "claude-sonnet-4-6-20250519",
) -> SessionData:
    return SessionData(
        file="/tmp/x.jsonl",
        session_id=session_id,
        agent_id=None,
        is_subagent=False,
        model=model,
        models=[model],
        timestamp_start="2026-04-07T10:00:00Z",
        usage=usage or TokenUsage(
            input_tokens=1000,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=500,
            output_tokens=300,
        ),
        cost=cost,
        prompts=prompts or [Prompt(text="Hello Claude", timestamp="2026-04-07T10:00:00Z", entrypoint="")],
        subagent_sessions=[],
    )


def _make_summary(project: str = "my-proj", cost: float = 0.50) -> ProjectSummary:
    return ProjectSummary(
        project=project,
        sessions=2,
        usage=TokenUsage(
            input_tokens=1000, cache_creation_input_tokens=200,
            cache_read_input_tokens=500, output_tokens=300,
        ),
        total_cost=cost,
        subagent_count=0,
        subagent_tokens=0,
    )


def _make_notifier(tmp_path: Path) -> SlackNotifier:
    svc = make_pricing_service(tmp_path)
    return SlackNotifier("fake-token", "C123456", svc, TZ)


class TestShortenProject:
    def test_returns_last_segment_after_triple_dash(self):
        assert _shorten_project("domains---credit-cards---core") == "core"

    def test_returns_full_name_when_no_triple_dash(self):
        assert _shorten_project("my-project") == "my-project"

    def test_handles_empty_string(self):
        assert _shorten_project("") == ""


class TestShortenModel:
    def test_shortens_versioned_model(self):
        result = _shorten_model("claude-sonnet-4-6-20250519")
        assert result == "sonnet-4.6"

    def test_shortens_model_without_patch(self):
        # claude-haiku-4-20250101 → the two-number regex matches (4, 20250101)
        result = _shorten_model("claude-haiku-4-20250101")
        assert result == "haiku-4.20250101"


class TestSlackNotifierBuildBlocks:
    def test_blocks_list_is_non_empty(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary()]
        projects = {"my-proj": [_make_session()]}
        blocks = notifier._build_blocks(
            summaries, projects, {}, {}, cache_hit_pct=35.0, cutoff=None
        )
        assert len(blocks) > 0

    def test_header_block_is_first(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary()]
        projects = {"my-proj": [_make_session()]}
        blocks = notifier._build_blocks(
            summaries, projects, {}, {}, cache_hit_pct=35.0, cutoff=None
        )
        assert blocks[0]["type"] == "header"
        assert "Reporte de tokens" in blocks[0]["text"]["text"]

    def test_summary_fields_in_spanish(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary(cost=1.23)]
        projects = {"my-proj": [_make_session()]}
        blocks = notifier._build_blocks(
            summaries, projects, {}, {}, cache_hit_pct=42.0, cutoff=None
        )
        # Find the section with fields
        fields_block = next(
            (b for b in blocks if b.get("type") == "section" and "fields" in b), None
        )
        assert fields_block is not None
        field_texts = [f["text"] for f in fields_block["fields"]]
        assert any("Costo estimado" in t for t in field_texts)
        assert any("Tokens usados" in t for t in field_texts)
        assert any("Sesiones" in t for t in field_texts)
        assert any("Caché hit" in t for t in field_texts)
        assert any("42%" in t for t in field_texts)

    def test_project_blocks_present(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary("my-proj")]
        projects = {"my-proj": [_make_session()]}
        blocks = notifier._build_blocks(
            summaries, projects, {}, {}, cache_hit_pct=0, cutoff=None
        )
        all_text = " ".join(
            str(b.get("text", {}).get("text", "")) for b in blocks
        )
        assert "my-proj" in all_text

    def test_trend_section_shows_sin_datos_when_no_comparisons(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary()]
        projects = {"my-proj": [_make_session()]}
        blocks = notifier._build_blocks(
            summaries, projects, {}, {}, cache_hit_pct=0, cutoff=None
        )
        trend_block = next(
            (b for b in blocks if b.get("type") == "section"
             and "Tendencia" in b.get("text", {}).get("text", "")),
            None,
        )
        assert trend_block is not None

    def test_trend_section_includes_delta_when_comparisons_present(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary()]
        projects = {"my-proj": [_make_session()]}
        comparisons = {
            "yesterday": {
                "delta_cost": 0.25,
                "delta_tokens": 500,
                "cache_hit_pct": 40.0,
                "pct": 20.0,
            }
        }
        blocks = notifier._build_blocks(
            summaries, projects, comparisons, {}, cache_hit_pct=0, cutoff=None
        )
        trend_block = next(
            (b for b in blocks if b.get("type") == "section"
             and "Tendencia" in b.get("text", {}).get("text", "")),
            None,
        )
        assert trend_block is not None
        fields = trend_block.get("fields", [])
        assert any("+$0.25" in f.get("text", "") for f in fields)

    def test_no_session_blocks_when_no_projects(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        blocks = notifier._build_blocks(
            [], {}, {}, {}, cache_hit_pct=0, cutoff=None
        )
        # Should not raise; header block should still be there
        assert any(b["type"] == "header" for b in blocks)

    def test_pricing_footnote_in_last_blocks(self, tmp_path):
        notifier = _make_notifier(tmp_path)
        summaries = [_make_summary()]
        blocks = notifier._build_blocks(
            summaries, {"my-proj": [_make_session()]}, {}, {}, 0, cutoff=None
        )
        context_texts = [
            e.get("text", "")
            for b in blocks if b.get("type") == "context"
            for e in b.get("elements", [])
        ]
        assert any("caché hit" in t.lower() for t in context_texts)

    def test_dm_channel_detected_by_u_prefix(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        notifier = SlackNotifier("tok", "U789012", svc, TZ)
        assert notifier._channel_id.startswith("U")


class TestSlackNotifierFmtHelpers:
    def _n(self, tmp_path):
        return _make_notifier(tmp_path)

    def test_fmt_tokens_millions(self, tmp_path):
        assert self._n(tmp_path)._fmt_tokens(2_000_000) == "2.0M"

    def test_fmt_tokens_thousands(self, tmp_path):
        assert self._n(tmp_path)._fmt_tokens(3_500) == "4K"

    def test_fmt_tokens_small(self, tmp_path):
        assert self._n(tmp_path)._fmt_tokens(42) == "42"

    def test_fmt_tok_delta_positive_millions(self, tmp_path):
        result = self._n(tmp_path)._fmt_tok_delta(2_000_000)
        assert result == "+2.0M tok"

    def test_fmt_tok_delta_negative_thousands(self, tmp_path):
        result = self._n(tmp_path)._fmt_tok_delta(-5_000)
        assert result == "-5K tok"

    def test_fmt_tok_delta_small_positive(self, tmp_path):
        result = self._n(tmp_path)._fmt_tok_delta(7)
        assert result == "+7 tok"
