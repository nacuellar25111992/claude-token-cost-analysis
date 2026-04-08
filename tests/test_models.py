"""Tests for token_cost_analysis.models."""

import pytest
from src.script import TokenUsage, Prompt, SessionData, ProjectSummary


class TestTokenUsage:
    def test_defaults_are_zero(self):
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.cache_creation_input_tokens == 0
        assert u.cache_read_input_tokens == 0
        assert u.output_tokens == 0

    def test_total_sums_all_fields(self):
        u = TokenUsage(input_tokens=100, cache_creation_input_tokens=50,
                       cache_read_input_tokens=200, output_tokens=80)
        assert u.total == 430

    def test_total_input_excludes_output(self):
        u = TokenUsage(input_tokens=100, cache_creation_input_tokens=50,
                       cache_read_input_tokens=200, output_tokens=999)
        assert u.total_input == 350

    def test_cache_hit_pct_calculates_correctly(self):
        u = TokenUsage(input_tokens=100, cache_creation_input_tokens=100,
                       cache_read_input_tokens=200, output_tokens=0)
        # total_input = 400, cache_read = 200 → 50%
        assert u.cache_hit_pct == pytest.approx(50.0)

    def test_cache_hit_pct_zero_when_no_input(self):
        u = TokenUsage()
        assert u.cache_hit_pct == 0.0

    def test_addition(self):
        a = TokenUsage(input_tokens=10, cache_creation_input_tokens=5,
                       cache_read_input_tokens=20, output_tokens=8)
        b = TokenUsage(input_tokens=1, cache_creation_input_tokens=2,
                       cache_read_input_tokens=3, output_tokens=4)
        c = a + b
        assert c.input_tokens == 11
        assert c.cache_creation_input_tokens == 7
        assert c.cache_read_input_tokens == 23
        assert c.output_tokens == 12

    def test_addition_does_not_mutate(self):
        a = TokenUsage(input_tokens=10)
        b = TokenUsage(input_tokens=5)
        _ = a + b
        assert a.input_tokens == 10

    def test_to_dict_round_trips(self):
        u = TokenUsage(input_tokens=1, cache_creation_input_tokens=2,
                       cache_read_input_tokens=3, output_tokens=4)
        d = u.to_dict()
        assert d["input_tokens"] == 1
        assert d["cache_creation_input_tokens"] == 2
        assert d["cache_read_input_tokens"] == 3
        assert d["output_tokens"] == 4


class TestPrompt:
    def test_fields(self):
        p = Prompt(text="hello", timestamp="2026-01-01T00:00:00Z", entrypoint="cli")
        assert p.text == "hello"
        assert p.timestamp == "2026-01-01T00:00:00Z"
        assert p.entrypoint == "cli"


class TestSessionData:
    def _make(self, **kwargs):
        defaults = dict(
            file="/tmp/sess.jsonl",
            session_id="abc",
            agent_id=None,
            is_subagent=False,
            model="claude-sonnet-4-6-20250519",
            models=["claude-sonnet-4-6-20250519"],
            timestamp_start="2026-04-07T10:00:00Z",
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            cost=0.01,
            prompts=[],
        )
        defaults.update(kwargs)
        return SessionData(**defaults)

    def test_basic_construction(self):
        s = self._make()
        assert s.session_id == "abc"
        assert s.usage.total == 150

    def test_subagent_sessions_default_empty(self):
        s = self._make()
        assert s.subagent_sessions == []

    def test_subagent_sessions_stored(self):
        sub = self._make(session_id="sub1", is_subagent=True)
        parent = self._make(subagent_sessions=[sub])
        assert len(parent.subagent_sessions) == 1
        assert parent.subagent_sessions[0].session_id == "sub1"


class TestProjectSummary:
    def test_fields(self):
        u = TokenUsage(input_tokens=500, output_tokens=200)
        s = ProjectSummary(
            project="my-project",
            sessions=3,
            usage=u,
            total_cost=0.05,
            subagent_count=2,
            subagent_tokens=1000,
        )
        assert s.project == "my-project"
        assert s.sessions == 3
        assert s.usage.total == 700
        assert s.total_cost == pytest.approx(0.05)
        assert s.subagent_count == 2
        assert s.subagent_tokens == 1000
