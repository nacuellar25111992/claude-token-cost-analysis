"""Tests for token_cost_analysis.history."""

import csv
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.script.history import HistoryStore
from src.script import TokenUsage, ProjectSummary


def _make_summary(
    project: str = "my-proj",
    sessions: int = 2,
    total_cost: float = 0.50,
    usage: TokenUsage | None = None,
    subagent_count: int = 0,
    subagent_tokens: int = 0,
) -> ProjectSummary:
    return ProjectSummary(
        project=project,
        sessions=sessions,
        usage=usage or TokenUsage(
            input_tokens=1000,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=500,
            output_tokens=300,
        ),
        total_cost=total_cost,
        subagent_count=subagent_count,
        subagent_tokens=subagent_tokens,
    )


class TestHistoryStoreWriteRuns:
    def test_creates_csv_files(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.write_runs([_make_summary()])
        assert (tmp_path / "runs.csv").exists()
        assert (tmp_path / "runs_projects.csv").exists()

    def test_runs_csv_has_correct_columns(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.write_runs([_make_summary()])
        rows = list(csv.DictReader(open(tmp_path / "runs.csv")))
        assert len(rows) == 1
        row = rows[0]
        assert "run_at" in row
        assert "total_cost_usd" in row
        assert "total_tokens" in row
        assert "sessions" in row

    def test_runs_projects_csv_has_one_row_per_project(self, tmp_path):
        store = HistoryStore(tmp_path)
        summaries = [_make_summary("proj-a"), _make_summary("proj-b")]
        store.write_runs(summaries)
        rows = list(csv.DictReader(open(tmp_path / "runs_projects.csv")))
        assert len(rows) == 2
        projects = {r["project"] for r in rows}
        assert projects == {"proj-a", "proj-b"}

    def test_replaces_todays_rows_on_second_call(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.write_runs([_make_summary(total_cost=0.10)])
        store.write_runs([_make_summary(total_cost=0.20)])  # replace
        rows = list(csv.DictReader(open(tmp_path / "runs.csv")))
        assert len(rows) == 1
        assert float(rows[0]["total_cost_usd"]) == pytest.approx(0.20)

    def test_keeps_historical_rows(self, tmp_path):
        store = HistoryStore(tmp_path)
        # Write a historical row manually
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(tmp_path / "runs.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["run_at", "cutoff_date", "projects", "sessions",
                             "input_tokens", "cache_creation_tokens", "cache_read_tokens",
                             "output_tokens", "total_tokens", "total_cost_usd",
                             "subagent_sessions", "subagent_tokens"])
            writer.writerow([yesterday, "", 1, 1, 100, 0, 0, 50, 150, 0.01, 0, 0])

        store.write_runs([_make_summary()])
        rows = list(csv.DictReader(open(tmp_path / "runs.csv")))
        assert len(rows) == 2  # historical + today

    def test_aggregates_token_counts(self, tmp_path):
        store = HistoryStore(tmp_path)
        s1 = _make_summary(usage=TokenUsage(input_tokens=100, output_tokens=50), total_cost=0.01)
        s2 = _make_summary(usage=TokenUsage(input_tokens=200, output_tokens=100), total_cost=0.02)
        store.write_runs([s1, s2])
        rows = list(csv.DictReader(open(tmp_path / "runs.csv")))
        row = rows[0]
        assert int(row["input_tokens"]) == 300
        assert int(row["output_tokens"]) == 150
        assert float(row["total_cost_usd"]) == pytest.approx(0.03)

    def test_cutoff_stored_in_csv(self, tmp_path):
        store = HistoryStore(tmp_path)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
        store.write_runs([_make_summary()], cutoff=cutoff)
        rows = list(csv.DictReader(open(tmp_path / "runs.csv")))
        assert rows[0]["cutoff_date"] == "2026-04-01"


class TestHistoryStoreLoadComparisons:
    def _write_runs_csv(self, path: Path, rows: list[dict]) -> None:
        columns = ["run_at", "cutoff_date", "projects", "sessions",
                   "input_tokens", "cache_creation_tokens", "cache_read_tokens",
                   "output_tokens", "total_tokens", "total_cost_usd",
                   "subagent_sessions", "subagent_tokens"]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_returns_empty_when_no_file(self, tmp_path):
        store = HistoryStore(tmp_path)
        result = store.load_comparisons(1.0, 1000)
        assert result == {}

    def test_returns_empty_when_only_todays_row(self, tmp_path):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_runs_csv(tmp_path / "runs.csv", [{
            "run_at": today, "cutoff_date": "", "projects": 1, "sessions": 1,
            "input_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 50,
            "output_tokens": 50, "total_tokens": 200, "total_cost_usd": 0.10,
            "subagent_sessions": 0, "subagent_tokens": 0,
        }])
        store = HistoryStore(tmp_path)
        result = store.load_comparisons(1.0, 1000)
        assert result == {}

    def test_computes_delta_vs_yesterday(self, tmp_path):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_runs_csv(tmp_path / "runs.csv", [{
            "run_at": yesterday, "cutoff_date": "", "projects": 1, "sessions": 1,
            "input_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 50,
            "output_tokens": 50, "total_tokens": 200, "total_cost_usd": 0.50,
            "subagent_sessions": 0, "subagent_tokens": 0,
        }])
        store = HistoryStore(tmp_path)
        result = store.load_comparisons(current_cost=0.75, current_tokens=300)
        assert "yesterday" in result
        assert result["yesterday"]["delta_cost"] == pytest.approx(0.25)
        assert result["yesterday"]["delta_tokens"] == 100
        assert result["yesterday"]["pct"] == pytest.approx(50.0)

    def test_computes_cache_hit_pct(self, tmp_path):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_runs_csv(tmp_path / "runs.csv", [{
            "run_at": yesterday, "cutoff_date": "", "projects": 1, "sessions": 1,
            "input_tokens": 100, "cache_creation_tokens": 100, "cache_read_tokens": 200,
            "output_tokens": 50, "total_tokens": 450, "total_cost_usd": 0.50,
            "subagent_sessions": 0, "subagent_tokens": 0,
        }])
        store = HistoryStore(tmp_path)
        result = store.load_comparisons(current_cost=1.0, current_tokens=500)
        # cache_hit_pct = 200 / (100+100+200) * 100 = 50%
        assert result["yesterday"]["cache_hit_pct"] == pytest.approx(50.0)


class TestHistoryStoreLoadProjectComparisons:
    def _write_projects_csv(self, path: Path, rows: list[dict]) -> None:
        columns = ["run_at", "project", "sessions", "input_tokens",
                   "cache_creation_tokens", "cache_read_tokens", "output_tokens",
                   "total_tokens", "total_cost_usd", "subagent_sessions", "subagent_tokens"]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_returns_empty_when_no_file(self, tmp_path):
        store = HistoryStore(tmp_path)
        result = store.load_project_comparisons([_make_summary()])
        assert result == {}

    def test_computes_delta_for_matching_project(self, tmp_path):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_projects_csv(tmp_path / "runs_projects.csv", [{
            "run_at": yesterday, "project": "my-proj", "sessions": 1,
            "input_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 50,
            "output_tokens": 50, "total_tokens": 200, "total_cost_usd": 0.30,
            "subagent_sessions": 0, "subagent_tokens": 0,
        }])
        store = HistoryStore(tmp_path)
        summary = _make_summary(project="my-proj", total_cost=0.50)
        result = store.load_project_comparisons([summary])
        assert "my-proj" in result
        assert result["my-proj"]["yesterday"]["delta_cost"] == pytest.approx(0.20)

    def test_normalizes_project_names_with_strip_prefix(self, tmp_path):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # CSV has intermediate name with prefix
        self._write_projects_csv(tmp_path / "runs_projects.csv", [{
            "run_at": yesterday, "project": "mycompany-my-proj", "sessions": 1,
            "input_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "output_tokens": 50, "total_tokens": 150, "total_cost_usd": 0.20,
            "subagent_sessions": 0, "subagent_tokens": 0,
        }])
        # Summary uses display name (prefix stripped)
        store = HistoryStore(tmp_path, project_strip_prefix="mycompany-")
        summary = _make_summary(project="my-proj", total_cost=0.40)
        result = store.load_project_comparisons([summary])
        assert "my-proj" in result

    def test_skips_projects_not_in_summaries(self, tmp_path):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_projects_csv(tmp_path / "runs_projects.csv", [{
            "run_at": yesterday, "project": "other-project", "sessions": 1,
            "input_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "output_tokens": 50, "total_tokens": 150, "total_cost_usd": 0.20,
            "subagent_sessions": 0, "subagent_tokens": 0,
        }])
        store = HistoryStore(tmp_path)
        result = store.load_project_comparisons([_make_summary(project="my-proj")])
        assert "other-project" not in result


class TestHistoryStoreNormalizeProject:
    def test_strips_prefix(self):
        store = HistoryStore(Path("/tmp"), project_strip_prefix="mycompany-")
        assert store._normalize_project("mycompany-my-proj") == "my-proj"

    def test_no_strip_when_no_match(self):
        store = HistoryStore(Path("/tmp"), project_strip_prefix="mycompany-")
        assert store._normalize_project("other-proj") == "other-proj"

    def test_empty_prefix_no_strip(self):
        store = HistoryStore(Path("/tmp"), project_strip_prefix="")
        assert store._normalize_project("mycompany-my-proj") == "mycompany-my-proj"
