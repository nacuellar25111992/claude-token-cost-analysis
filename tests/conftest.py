"""Shared fixtures for all test modules."""

import json
from pathlib import Path

from src.script.pricing import ModelPricing, PricingService

SAMPLE_PRICING = {
    "claude-sonnet-4-6": ModelPricing(input=3.0, cache_write=3.75, cache_read=0.3, output=15.0),
    "claude-haiku-4-5": ModelPricing(input=0.8, cache_write=1.0, cache_read=0.08, output=4.0),
}


def make_pricing_service(tmp_path: Path, pricing: dict | None = None) -> PricingService:
    """Return a PricingService pre-loaded with SAMPLE_PRICING (no CSV, no scrape)."""
    svc = PricingService(tmp_path / "pricing.csv")
    svc._pricing = pricing if pricing is not None else dict(SAMPLE_PRICING)
    return svc


def make_jsonl(events: list[dict]) -> str:
    """Serialize a list of dicts to JSONL."""
    return "\n".join(json.dumps(e) for e in events)


SAMPLE_SESSION_JSONL_EVENTS = [
    {
        "type": "user",
        "sessionId": "sess-abc123",
        "agentId": "agent-001",
        "timestamp": "2026-04-07T10:00:00Z",
        "userType": "human",
        "isSidechain": False,
        "message": {"content": "Hello, can you help me?"},
        "entrypoint": "cli",
    },
    {
        "type": "assistant",
        "timestamp": "2026-04-07T10:00:05Z",
        "message": {
            "model": "claude-sonnet-4-6-20250519",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 200,
                "output_tokens": 80,
            },
            "content": [{"type": "text", "text": "Sure, I can help!"}],
        },
    },
    {
        "type": "user",
        "sessionId": "sess-abc123",
        "timestamp": "2026-04-07T10:01:00Z",
        "userType": "human",
        "isSidechain": False,
        "message": {"content": "Thanks!"},
        "entrypoint": "cli",
    },
    {
        "type": "assistant",
        "timestamp": "2026-04-07T10:01:05Z",
        "message": {
            "model": "claude-sonnet-4-6-20250519",
            "usage": {
                "input_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 300,
                "output_tokens": 40,
            },
        },
    },
]
