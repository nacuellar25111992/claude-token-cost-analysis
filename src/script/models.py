"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
            + self.output_tokens
        )

    @property
    def total_input(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    @property
    def cache_hit_pct(self) -> float:
        ti = self.total_input
        return (self.cache_read_input_tokens / ti * 100) if ti > 0 else 0.0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class Prompt:
    text: str
    timestamp: str
    entrypoint: str


@dataclass
class SessionData:
    file: str
    session_id: str
    agent_id: str | None
    is_subagent: bool
    model: str | None
    models: list[str]
    timestamp_start: str | None
    usage: TokenUsage
    cost: float
    prompts: list[Prompt]
    subagent_sessions: list["SessionData"] = field(default_factory=list)
    # Session context (extracted from JSONL metadata)
    entrypoint: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    stop_reason: str | None = None
    # Subagent metadata (from .meta.json sidecar)
    agent_type: str | None = None
    agent_description: str | None = None


@dataclass
class ProjectSummary:
    project: str
    sessions: int
    usage: TokenUsage
    total_cost: float
    subagent_count: int
    subagent_tokens: int
