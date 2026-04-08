"""Tests for token_cost_analysis.parser."""

from pathlib import Path

from src.script import SessionParser, _extract_text_content, _is_human_prompt
from .conftest import make_pricing_service, SAMPLE_SESSION_JSONL_EVENTS, make_jsonl


class TestExtractTextContent:
    def test_string_content(self):
        assert _extract_text_content("hello") == "hello"

    def test_list_with_text_block(self):
        content = [{"type": "text", "text": "hello world"}]
        assert _extract_text_content(content) == "hello world"

    def test_list_skips_non_text_blocks(self):
        content = [
            {"type": "tool_result", "content": "ignored"},
            {"type": "text", "text": "kept"},
        ]
        assert _extract_text_content(content) == "kept"

    def test_list_with_string_items(self):
        content = ["part1", "part2"]
        assert _extract_text_content(content) == "part1\npart2"

    def test_empty_list(self):
        assert _extract_text_content([]) == ""

    def test_none_like_input(self):
        assert _extract_text_content(42) == ""


class TestIsHumanPrompt:
    def test_string_content_is_human(self):
        obj = {"message": {"content": "hello"}}
        assert _is_human_prompt(obj) is True

    def test_all_tool_result_is_not_human(self):
        obj = {"message": {"content": [
            {"type": "tool_result", "content": "result"},
        ]}}
        assert _is_human_prompt(obj) is False

    def test_mixed_content_with_tool_result_is_human(self):
        obj = {"message": {"content": [
            {"type": "tool_result", "content": "result"},
            {"type": "text", "text": "some text"},
        ]}}
        assert _is_human_prompt(obj) is True

    def test_empty_content_list_is_human(self):
        obj = {"message": {"content": []}}
        assert _is_human_prompt(obj) is True


class TestSessionParser:
    def _make_parser(self, tmp_path):
        return SessionParser(make_pricing_service(tmp_path))

    def _write_jsonl(self, path: Path, events: list[dict]) -> Path:
        path.write_text(make_jsonl(events))
        return path

    def test_returns_none_on_unreadable_file(self, tmp_path):
        parser = self._make_parser(tmp_path)
        result = parser.parse(tmp_path / "nonexistent.jsonl")
        assert result is None

    def test_parses_basic_session(self, tmp_path):
        f = self._write_jsonl(tmp_path / "sess.jsonl", SAMPLE_SESSION_JSONL_EVENTS)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert session.session_id == "sess-abc123"
        assert session.agent_id == "agent-001"
        assert session.model == "claude-sonnet-4-6-20250519"
        assert session.timestamp_start == "2026-04-07T10:00:00Z"

    def test_accumulates_token_usage(self, tmp_path):
        f = self._write_jsonl(tmp_path / "sess.jsonl", SAMPLE_SESSION_JSONL_EVENTS)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        # Two assistant messages: (100+50, 50+0, 200+300, 80+40)
        assert session.usage.input_tokens == 150
        assert session.usage.cache_creation_input_tokens == 50
        assert session.usage.cache_read_input_tokens == 500
        assert session.usage.output_tokens == 120

    def test_extracts_prompts(self, tmp_path):
        f = self._write_jsonl(tmp_path / "sess.jsonl", SAMPLE_SESSION_JSONL_EVENTS)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert len(session.prompts) == 2
        assert session.prompts[0].text == "Hello, can you help me?"
        assert session.prompts[0].entrypoint == "cli"
        assert session.prompts[1].text == "Thanks!"

    def test_skips_sidechain_messages(self, tmp_path):
        events = [
            {
                "type": "user",
                "sessionId": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
                "userType": "human",
                "isSidechain": True,
                "message": {"content": "should be skipped"},
            },
        ]
        f = self._write_jsonl(tmp_path / "sess.jsonl", events)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert len(session.prompts) == 0

    def test_skips_tool_type_user_messages(self, tmp_path):
        events = [
            {
                "type": "user",
                "sessionId": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
                "userType": "tool",
                "isSidechain": False,
                "message": {"content": "tool output"},
            },
        ]
        f = self._write_jsonl(tmp_path / "sess.jsonl", events)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert len(session.prompts) == 0

    def test_skips_all_tool_result_messages(self, tmp_path):
        events = [
            {
                "type": "user",
                "sessionId": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
                "userType": "human",
                "isSidechain": False,
                "message": {"content": [{"type": "tool_result", "content": "res"}]},
            },
        ]
        f = self._write_jsonl(tmp_path / "sess.jsonl", events)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert len(session.prompts) == 0

    def test_calculates_cost(self, tmp_path):
        f = self._write_jsonl(tmp_path / "sess.jsonl", SAMPLE_SESSION_JSONL_EVENTS)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert session.cost > 0

    def test_returns_session_even_with_zero_tokens(self, tmp_path):
        events = [
            {
                "type": "user",
                "sessionId": "empty-sess",
                "timestamp": "2026-01-01T00:00:00Z",
                "userType": "human",
                "isSidechain": False,
                "message": {"content": "hi"},
            },
        ]
        f = self._write_jsonl(tmp_path / "sess.jsonl", events)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert session.usage.total == 0

    def test_session_id_falls_back_to_stem(self, tmp_path):
        events = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6-x",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                },
            },
        ]
        f = self._write_jsonl(tmp_path / "my-session-file.jsonl", events)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert session.session_id == "my-session-file"

    def test_collects_multiple_models(self, tmp_path):
        events = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6-20250519",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:01:00Z",
                "message": {
                    "model": "claude-haiku-4-5-20251001",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                },
            },
        ]
        f = self._write_jsonl(tmp_path / "sess.jsonl", events)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None
        assert len(session.models) == 2

    def test_parses_subagent_sessions(self, tmp_path):
        # Create parent session
        parent_jsonl = tmp_path / "parent.jsonl"
        self._write_jsonl(parent_jsonl, SAMPLE_SESSION_JSONL_EVENTS)

        # Create subagent directory structure: parent/ subagents/ sub.jsonl
        subagents_dir = tmp_path / "parent" / "subagents"
        subagents_dir.mkdir(parents=True)
        sub_events = [
            {
                "type": "assistant",
                "timestamp": "2026-04-07T10:00:10Z",
                "message": {
                    "model": "claude-sonnet-4-6-20250519",
                    "usage": {"input_tokens": 200, "output_tokens": 100,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                },
            },
        ]
        self._write_jsonl(subagents_dir / "sub001.jsonl", sub_events)

        parser = self._make_parser(tmp_path)
        session = parser.parse(parent_jsonl)
        assert session is not None
        assert len(session.subagent_sessions) == 1
        assert session.subagent_sessions[0].is_subagent is True
        assert session.subagent_sessions[0].usage.input_tokens == 200

    def test_ignores_invalid_json_lines(self, tmp_path):
        content = 'not json\n{"type": "user", "sessionId": "s1", "timestamp": "2026-01-01T00:00:00Z", "userType": "human", "isSidechain": false, "message": {"content": "hi"}}\n'
        f = tmp_path / "sess.jsonl"
        f.write_text(content)
        parser = self._make_parser(tmp_path)
        session = parser.parse(f)
        assert session is not None  # should not crash
