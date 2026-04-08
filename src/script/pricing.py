"""Pricing service: scrape Anthropic docs, SQLite cache, cost calculation."""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import TokenUsage

PRICING_PAGE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

# Order matters: more specific first (e.g. "4.6" before "4").
_DISPLAY_NAMES: list[str] = [
    "claude opus 4.6",
    "claude opus 4.5",
    "claude opus 4.1",
    "claude opus 4",
    "claude sonnet 4.6",
    "claude sonnet 4.5",
    "claude sonnet 4",
    "claude sonnet 3.7",
    "claude haiku 4.5",
    "claude haiku 3.5",
    "claude haiku 3",
    "claude opus 3",
]

DISPLAY_NAME_TO_MODEL_PREFIX: dict[str, str] = {
    name: name.replace(" ", "-").replace(".", "-") for name in _DISPLAY_NAMES
}

_PRICING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pricing (
    scraped_at          TEXT NOT NULL,
    model_prefix        TEXT NOT NULL,
    input_per_mtok      REAL,
    cache_write_per_mtok REAL,
    cache_read_per_mtok REAL,
    output_per_mtok     REAL,
    PRIMARY KEY (scraped_at, model_prefix)
);
"""


@dataclass(frozen=True)
class ModelPricing:
    input: float
    cache_write: float
    cache_read: float
    output: float


class PricingService:
    """Loads pricing from SQLite cache or scrapes Anthropic docs via Playwright."""

    def __init__(self, db_path: Path, page_url: str = PRICING_PAGE_URL):
        self._db_path = db_path
        self._page_url = page_url
        self._pricing: dict[str, ModelPricing] = {}
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._maybe_migrate_csv()

    @property
    def pricing(self) -> dict[str, ModelPricing]:
        return self._pricing

    def load(self) -> dict[str, ModelPricing]:
        """Return pricing from DB cache if current, else scrape and return."""
        cached = self._load_db()
        if cached:
            print(f"Pricing loaded from DB cache ({self._db_path.name})")
            self._pricing = cached
        else:
            self._pricing = self._scrape()
        return self._pricing

    def get(self, model: str | None) -> ModelPricing:
        """Look up pricing for a model ID, matching by prefix."""
        if model and not model.startswith("<"):
            for prefix, prices in self._pricing.items():
                if model.startswith(prefix):
                    return prices
            raise RuntimeError(
                f"No pricing found for model '{model}'. "
                f"Known prefixes: {list(self._pricing.keys())}. "
                "Add it to DISPLAY_NAME_TO_MODEL_PREFIX in pricing.py."
            )
        return ModelPricing(input=0.0, cache_write=0.0, cache_read=0.0, output=0.0)

    def calculate_cost(self, usage: TokenUsage, model: str | None) -> float:
        """Calculate USD cost for a given TokenUsage and model."""
        p = self.get(model)
        M = 1_000_000
        return (
            usage.input_tokens * p.input / M
            + usage.cache_creation_input_tokens * p.cache_write / M
            + usage.cache_read_input_tokens * p.cache_read / M
            + usage.output_tokens * p.output / M
        )

    def save(self) -> None:
        """Write today's pricing to the DB, skipping models already recorded today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scraped_at = today + "T" + datetime.now(timezone.utc).strftime("%H:%M:%SZ")

        with self._conn() as conn:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT model_prefix FROM pricing WHERE scraped_at LIKE ?",
                    (today + "%",),
                )
            }
            new_rows = [p for p in self._pricing if p not in existing]
            if not new_rows:
                print(f"Pricing already recorded today — skipped ({self._db_path.name})")
                return
            conn.executemany(
                "INSERT OR IGNORE INTO pricing VALUES (?,?,?,?,?,?)",
                [
                    (
                        scraped_at, prefix,
                        self._pricing[prefix].input,
                        self._pricing[prefix].cache_write,
                        self._pricing[prefix].cache_read,
                        self._pricing[prefix].output,
                    )
                    for prefix in new_rows
                ],
            )
        print(f"Pricing saved to DB ({len(new_rows)} models)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_PRICING_SCHEMA)

    def _maybe_migrate_csv(self) -> None:
        """One-time import of legacy pricing.csv into SQLite."""
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
        if count > 0:
            return

        csv_path = self._db_path.parent / "pricing.csv"
        if not csv_path.exists() or csv_path.stat().st_size == 0:
            return

        migrated = 0
        with self._conn() as conn:
            with open(csv_path, newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO pricing VALUES (?,?,?,?,?,?)",
                            (
                                row["scraped_at"], row["model_prefix"],
                                float(row["input_per_mtok"]),
                                float(row["cache_write_per_mtok"]),
                                float(row["cache_read_per_mtok"]),
                                float(row["output_per_mtok"]),
                            ),
                        )
                        migrated += 1
                    except (ValueError, KeyError):
                        continue
        if migrated > 0:
            print(f"Migrated {migrated} pricing rows from CSV → SQLite")

    def _load_db(self) -> dict[str, ModelPricing] | None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pricing: dict[str, ModelPricing] = {}
        with self._conn() as conn:
            for row in conn.execute(
                "SELECT model_prefix, input_per_mtok, cache_write_per_mtok, "
                "cache_read_per_mtok, output_per_mtok FROM pricing WHERE scraped_at LIKE ?",
                (today + "%",),
            ):
                pricing[row["model_prefix"]] = ModelPricing(
                    input=float(row["input_per_mtok"]),
                    cache_write=float(row["cache_write_per_mtok"]),
                    cache_read=float(row["cache_read_per_mtok"]),
                    output=float(row["output_per_mtok"]),
                )
        return pricing if pricing else None

    def _scrape(self) -> dict[str, ModelPricing]:
        """Scrape pricing from Anthropic docs via Playwright. Raises on failure."""
        from playwright.sync_api import sync_playwright

        pricing: dict[str, ModelPricing] = {}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(self._page_url, wait_until="networkidle", timeout=30_000)
            body = page.inner_text("body")
            browser.close()

        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue

            lower = line.lower()
            model_prefix: str | None = None
            for display, prefix in DISPLAY_NAME_TO_MODEL_PREFIX.items():
                if lower.startswith(display):
                    model_prefix = prefix
                    break
            if not model_prefix:
                m = re.match(r"(claude (?:opus|sonnet|haiku) \d+(?:\.\d+)?)", lower)
                if m:
                    model_prefix = m.group(1).replace(" ", "-").replace(".", "-")
            if not model_prefix:
                continue

            # Expected columns: Base Input, 5m Cache Write, 1h Cache Write, Cache Read, Output
            prices = [float(x) for x in re.findall(r"\$(\d+(?:\.\d+)?)\s*/\s*MTok", line)]
            if len(prices) < 5:
                continue

            pricing[model_prefix] = ModelPricing(
                input=prices[0],
                cache_write=prices[1],  # 5-minute cache write
                cache_read=prices[3],   # cache hit/refresh
                output=prices[4],
            )

        if not pricing:
            raise RuntimeError(
                f"Scraped 0 pricing entries from {self._page_url}. "
                "The page structure may have changed — update DISPLAY_NAME_TO_MODEL_PREFIX "
                "or the price regex in pricing.py."
            )

        return pricing
