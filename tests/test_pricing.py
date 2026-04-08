"""Tests for token_cost_analysis.pricing."""

import csv
import pytest
from datetime import datetime, timezone
from pathlib import Path

from src.script.pricing import ModelPricing, PricingService
from src.script import TokenUsage
from .conftest import make_pricing_service


class TestModelPricing:
    def test_fields(self):
        p = ModelPricing(input=3.0, cache_write=3.75, cache_read=0.3, output=15.0)
        assert p.input == 3.0
        assert p.cache_write == 3.75
        assert p.cache_read == 0.3
        assert p.output == 15.0

    def test_frozen(self):
        p = ModelPricing(input=1.0, cache_write=1.0, cache_read=1.0, output=1.0)
        with pytest.raises((AttributeError, TypeError)):
            p.input = 99.0  # type: ignore[misc]


class TestPricingServiceGet:
    def test_returns_pricing_for_exact_prefix(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        p = svc.get("claude-sonnet-4-6-20250519")
        assert p.input == pytest.approx(3.0)
        assert p.output == pytest.approx(15.0)

    def test_returns_pricing_for_partial_prefix_match(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        # haiku match
        p = svc.get("claude-haiku-4-5-anything")
        assert p.input == pytest.approx(0.8)

    def test_raises_for_unknown_model(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        with pytest.raises(RuntimeError, match="No pricing found"):
            svc.get("claude-unknown-99-0")

    def test_returns_zeros_for_none(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        p = svc.get(None)
        assert p.input == 0.0
        assert p.output == 0.0

    def test_returns_zeros_for_synthetic_placeholder(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        p = svc.get("<synthetic>")
        assert p.input == 0.0


class TestPricingServiceCalculateCost:
    def test_basic_cost(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)
        cost = svc.calculate_cost(usage, "claude-sonnet-4-6-20250519")
        assert cost == pytest.approx(3.0)  # $3 / 1M input

    def test_output_cost(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        usage = TokenUsage(output_tokens=1_000_000)
        cost = svc.calculate_cost(usage, "claude-sonnet-4-6-20250519")
        assert cost == pytest.approx(15.0)

    def test_cache_write_cost(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        usage = TokenUsage(cache_creation_input_tokens=1_000_000)
        cost = svc.calculate_cost(usage, "claude-sonnet-4-6-20250519")
        assert cost == pytest.approx(3.75)

    def test_cache_read_cost(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        usage = TokenUsage(cache_read_input_tokens=1_000_000)
        cost = svc.calculate_cost(usage, "claude-sonnet-4-6-20250519")
        assert cost == pytest.approx(0.3)

    def test_combined_cost(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        usage = TokenUsage(
            input_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        cost = svc.calculate_cost(usage, "claude-sonnet-4-6-20250519")
        expected = 3.0 + 3.75 + 0.3 + 15.0
        assert cost == pytest.approx(expected)

    def test_zero_cost_for_none_model(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = svc.calculate_cost(usage, None)
        assert cost == pytest.approx(0.0)


class TestPricingServiceCsvCache:
    def _write_csv(self, path: Path, today: str, rows: list[dict]) -> None:
        columns = [
            "scraped_at", "model_prefix", "input_per_mtok",
            "cache_write_per_mtok", "cache_read_per_mtok", "output_per_mtok",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_load_returns_none_when_file_missing(self, tmp_path):
        svc = PricingService(tmp_path / "pricing.csv")
        assert svc._load_csv() is None

    def test_load_returns_none_when_no_todays_rows(self, tmp_path):
        csv_path = tmp_path / "pricing.csv"
        self._write_csv(csv_path, "2020-01-01", [{
            "scraped_at": "2020-01-01T10:00:00Z",
            "model_prefix": "claude-sonnet-4-6",
            "input_per_mtok": "3.0",
            "cache_write_per_mtok": "3.75",
            "cache_read_per_mtok": "0.3",
            "output_per_mtok": "15.0",
        }])
        svc = PricingService(csv_path)
        assert svc._load_csv() is None

    def test_load_returns_todays_rows(self, tmp_path):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_path = tmp_path / "pricing.csv"
        self._write_csv(csv_path, today, [{
            "scraped_at": f"{today}T10:00:00Z",
            "model_prefix": "claude-sonnet-4-6",
            "input_per_mtok": "3.0",
            "cache_write_per_mtok": "3.75",
            "cache_read_per_mtok": "0.3",
            "output_per_mtok": "15.0",
        }])
        svc = PricingService(csv_path)
        result = svc._load_csv()
        assert result is not None
        assert "claude-sonnet-4-6" in result
        assert result["claude-sonnet-4-6"].input == pytest.approx(3.0)

    def test_save_csv_writes_new_rows(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        svc.save_csv()
        assert (tmp_path / "pricing.csv").exists()
        rows = list(csv.DictReader(open(tmp_path / "pricing.csv")))
        prefixes = {r["model_prefix"] for r in rows}
        assert "claude-sonnet-4-6" in prefixes
        assert "claude-haiku-4-5" in prefixes

    def test_save_csv_skips_already_saved_today(self, tmp_path):
        svc = make_pricing_service(tmp_path)
        svc.save_csv()
        svc.save_csv()  # second call should skip
        rows = list(csv.DictReader(open(tmp_path / "pricing.csv")))
        # Should still only have original rows (no duplicates)
        prefixes = [r["model_prefix"] for r in rows]
        assert len(prefixes) == len(set(prefixes))

    def test_load_uses_cache_over_scrape(self, tmp_path):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_path = tmp_path / "pricing.csv"
        self._write_csv(csv_path, today, [{
            "scraped_at": f"{today}T10:00:00Z",
            "model_prefix": "claude-sonnet-4-6",
            "input_per_mtok": "99.0",
            "cache_write_per_mtok": "99.0",
            "cache_read_per_mtok": "99.0",
            "output_per_mtok": "99.0",
        }])
        svc = PricingService(csv_path)
        result = svc.load()
        # Should get cached value, not scrape
        assert result["claude-sonnet-4-6"].input == pytest.approx(99.0)
