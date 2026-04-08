"""JSONL session file parser."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Prompt, SessionData, TokenUsage
from .pricing import PricingService


def _extract_text_content(content) -> str:
    """Extract text from message content (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()
    return ""


def _is_human_prompt(msg_obj: dict) -> bool:
    """Return False if the message consists entirely of tool_result blocks."""
    content = msg_obj.get("message", {}).get("content", "")
    if isinstance(content, list):
        types = [i.get("type") for i in content if isinstance(i, dict)]
        if types and all(t == "tool_result" for t in types):
            return False
    return True


class SessionParser:
    """Parses a single JSONL session file into a SessionData object."""

    def __init__(self, pricing_service: PricingService):
        self._pricing = pricing_service

    def parse(self, jsonl_path: Path, is_subagent: bool = False) -> SessionData | None:
        """
        Parse a JSONL session file. Returns None only on file read errors.
        Sessions with zero tokens are returned; callers filter them if needed.
        """
        try:
            with open(jsonl_path) as f:
                lines = f.readlines()
        except Exception:
            return None

        usage = TokenUsage()
        prompts: list[Prompt] = []
        agent_id: str | None = None
        session_id: str | None = None
        model: str | None = None
        models_seen: list[str] = []
        timestamp_start: str | None = None

        # New fields
        entrypoint: str | None = None
        cwd: str | None = None
        git_branch: str | None = None
        stop_reason: str | None = None
        first_user_seen = False

        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")
            ts = obj.get("timestamp")
            if ts and not timestamp_start:
                timestamp_start = ts

            if not agent_id:
                agent_id = obj.get("agentId")
            if not session_id:
                session_id = obj.get("sessionId")

            if msg_type == "assistant":
                msg = obj.get("message", {})
                m = msg.get("model")
                if m and m not in models_seen:
                    models_seen.append(m)
                if not model:
                    model = m
                raw = msg.get("usage", {})
                usage = usage + TokenUsage(
                    input_tokens=raw.get("input_tokens", 0),
                    cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
                    cache_read_input_tokens=raw.get("cache_read_input_tokens", 0),
                    output_tokens=raw.get("output_tokens", 0),
                )
                # Track stop_reason from the last assistant message
                sr = msg.get("stop_reason")
                if sr:
                    stop_reason = sr

            elif msg_type == "user":
                # Extract session-level metadata from the first user message
                if not first_user_seen:
                    first_user_seen = True
                    entrypoint = obj.get("entrypoint") or None
                    cwd = obj.get("cwd") or None
                    git_branch = obj.get("gitBranch") or None

                if (
                    not obj.get("isSidechain", False)
                    and obj.get("userType", "") != "tool"
                    and _is_human_prompt(obj)
                ):
                    content = obj.get("message", {}).get("content", "")
                    text = _extract_text_content(content)
                    if text:
                        prompts.append(Prompt(
                            text=text,
                            timestamp=obj.get("timestamp", ""),
                            entrypoint=obj.get("entrypoint", ""),
                        ))

        subagent_sessions: list[SessionData] = []
        session_dir = jsonl_path.parent / jsonl_path.stem
        subagents_dir = session_dir / "subagents"
        if subagents_dir.is_dir():
            for sub_file in sorted(subagents_dir.glob("*.jsonl")):
                sub = self.parse(sub_file, is_subagent=True)
                if sub:
                    # Read .meta.json sidecar for agent type/description
                    meta_file = sub_file.with_suffix(".meta.json")
                    if meta_file.exists():
                        try:
                            with open(meta_file) as f:
                                meta = json.load(f)
                            sub.agent_type = meta.get("agentType") or None
                            sub.agent_description = meta.get("description") or None
                        except Exception:
                            pass
                    subagent_sessions.append(sub)

        return SessionData(
            file=str(jsonl_path),
            session_id=session_id or jsonl_path.stem,
            agent_id=agent_id,
            is_subagent=is_subagent,
            model=model,
            models=models_seen,
            timestamp_start=timestamp_start,
            usage=usage,
            cost=self._pricing.calculate_cost(usage, model),
            prompts=prompts,
            subagent_sessions=subagent_sessions,
            entrypoint=entrypoint,
            cwd=cwd,
            git_branch=git_branch,
            stop_reason=stop_reason,
        )
