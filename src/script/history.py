"""SQLite history store for runs, project runs, sessions, and prompts."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import ProjectSummary, SessionData

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_at              TEXT PRIMARY KEY,
    cutoff_date         TEXT,
    projects            INTEGER,
    sessions            INTEGER,
    input_tokens        INTEGER,
    cache_creation_tokens INTEGER,
    cache_read_tokens   INTEGER,
    output_tokens       INTEGER,
    total_tokens        INTEGER,
    total_cost_usd      REAL,
    subagent_sessions   INTEGER,
    subagent_tokens     INTEGER,
    cache_hit_pct       REAL
);

CREATE TABLE IF NOT EXISTS runs_projects (
    run_at                  TEXT NOT NULL,
    project                 TEXT NOT NULL,
    sessions                INTEGER,
    input_tokens            INTEGER,
    cache_creation_tokens   INTEGER,
    cache_read_tokens       INTEGER,
    output_tokens           INTEGER,
    total_tokens            INTEGER,
    total_cost_usd          REAL,
    subagent_sessions       INTEGER,
    subagent_tokens         INTEGER,
    cache_hit_pct           REAL,
    PRIMARY KEY (run_at, project)
);

CREATE TABLE IF NOT EXISTS sessions (
    file_id                 TEXT PRIMARY KEY,  -- JSONL filename stem (unique per file)
    session_id              TEXT,              -- Claude session UUID (may be shared with subagents)
    project                 TEXT,
    is_subagent             INTEGER DEFAULT 0,
    parent_file_id          TEXT,              -- file_id of parent session (for subagents)
    agent_type              TEXT,
    agent_description       TEXT,
    model                   TEXT,
    timestamp_start         TEXT,
    entrypoint              TEXT,
    cwd                     TEXT,
    git_branch              TEXT,
    input_tokens            INTEGER,
    cache_creation_tokens   INTEGER,
    cache_read_tokens       INTEGER,
    output_tokens           INTEGER,
    total_tokens            INTEGER,
    cost_usd                REAL,
    cache_hit_pct           REAL,
    stop_reason             TEXT,
    subagent_count          INTEGER,
    prompt_count            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project    ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_timestamp  ON sessions(timestamp_start);

CREATE TABLE IF NOT EXISTS prompts (
    file_id         TEXT NOT NULL,   -- FK to sessions.file_id
    timestamp       TEXT NOT NULL,
    project         TEXT,
    entrypoint      TEXT,
    prompt_length   INTEGER,
    prompt_text     TEXT,
    PRIMARY KEY (file_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_prompts_project ON prompts(project);
"""


class HistoryStore:
    """SQLite-backed store for run history, sessions, and prompts."""

    def __init__(self, db_path: Path, project_strip_prefix: str = ""):
        self._db_path = db_path
        self._strip_prefix = project_strip_prefix
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._maybe_migrate_csvs()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_comparisons(self, current_cost: float, current_tokens: int) -> dict:
        """
        Compute cost/token deltas vs 1/7/30/365 days ago.
        Must be called BEFORE write_runs() so the current run isn't included.
        """
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT run_at, total_cost_usd, total_tokens, cache_hit_pct
                   FROM runs WHERE run_at < ?""",
                (today + "T",),
            ).fetchall()

        if not rows:
            return {}

        totals = []
        for row in rows:
            try:
                run_at = datetime.fromisoformat(row["run_at"].replace("Z", "+00:00"))
                totals.append({
                    "run_at": run_at,
                    "cost": float(row["total_cost_usd"] or 0),
                    "tokens": int(row["total_tokens"] or 0),
                    "cache_hit_pct": float(row["cache_hit_pct"] or 0),
                })
            except (ValueError, TypeError):
                continue

        if not totals:
            return {}

        today_date = now.date()
        prev = [r for r in totals if r["run_at"].date() < today_date]
        if not prev:
            return {}

        def closest(target_dt):
            return min(prev, key=lambda r: abs((r["run_at"] - target_dt).total_seconds()))

        result = {}
        for label, days, tolerance in [
            ("yesterday", 1, 1),
            ("last_3_days", 3, 2),
            ("last_week", 7, 3),
            ("last_month", 30, 7),
            ("last_year", 365, 30),
        ]:
            target = now - timedelta(days=days)
            ref = closest(target)
            distance = abs((ref["run_at"] - target).total_seconds()) / 86400
            if distance > tolerance:
                result[label] = None
                continue
            delta_cost = current_cost - ref["cost"]
            pct = (delta_cost / ref["cost"] * 100) if ref["cost"] > 0 else 0.0
            result[label] = {
                "run_at": ref["run_at"],
                "delta_cost": delta_cost,
                "delta_tokens": current_tokens - ref["tokens"],
                "cache_hit_pct": ref["cache_hit_pct"],
                "pct": pct,
            }
        return result

    def load_project_comparisons(
        self, summaries: list[ProjectSummary]
    ) -> dict[str, dict]:
        """
        Compute per-project cost deltas vs 1/7/30 days ago.
        Must be called BEFORE write_runs() so the current run isn't included.
        """
        now = datetime.now(timezone.utc)
        today_date = now.date()
        today = today_date.isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT run_at, project, total_cost_usd, total_tokens,
                          cache_read_tokens, input_tokens, cache_creation_tokens
                   FROM runs_projects WHERE run_at < ?""",
                (today + "T",),
            ).fetchall()

        if not rows:
            return {}

        history: dict[str, list] = {}
        for row in rows:
            try:
                run_at = datetime.fromisoformat(row["run_at"].replace("Z", "+00:00"))
                display = self._normalize_project(row["project"])
                total_inp = (
                    int(row["input_tokens"] or 0)
                    + int(row["cache_creation_tokens"] or 0)
                    + int(row["cache_read_tokens"] or 0)
                )
                ref_hit = (
                    int(row["cache_read_tokens"] or 0) / total_inp * 100
                    if total_inp > 0 else 0.0
                )
                history.setdefault(display, []).append((
                    run_at,
                    float(row["total_cost_usd"] or 0),
                    int(row["total_tokens"] or 0),
                    ref_hit,
                ))
            except (ValueError, TypeError):
                continue

        if not history:
            return {}

        result: dict[str, dict] = {}
        for summary in summaries:
            entries = history.get(summary.project)
            if not entries:
                continue

            u = summary.usage
            cur_inp = u.total_input
            current_hit = (u.cache_read_input_tokens / cur_inp * 100) if cur_inp > 0 else 0.0

            prev_entries = [e for e in entries if e[0].date() < today_date]
            if not prev_entries:
                continue

            proj_comparisons = {}
            for label, days, tolerance in [
                ("yesterday", 1, 1), ("last_week", 7, 3), ("last_month", 30, 7),
            ]:
                target = now - timedelta(days=days)
                ref = min(prev_entries, key=lambda r: abs((r[0] - target).total_seconds()))
                ref_run_at, ref_cost, ref_tokens, ref_hit = ref
                distance = abs((ref_run_at - target).total_seconds()) / 86400
                if distance > tolerance:
                    proj_comparisons[label] = None
                    continue
                delta = summary.total_cost - ref_cost
                pct = (delta / ref_cost * 100) if ref_cost > 0 else 0.0
                proj_comparisons[label] = {
                    "delta_cost": delta,
                    "delta_tokens": summary.usage.total - ref_tokens,
                    "pct": pct,
                    "delta_hit": current_hit - ref_hit,
                }
            result[summary.project] = proj_comparisons

        return result

    def write_runs(
        self,
        summaries: list[ProjectSummary],
        cutoff: datetime | None = None,
    ) -> None:
        """Write run aggregates to runs and runs_projects, replacing any rows from today."""
        run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today = run_at[:10]
        cutoff_str = cutoff.strftime("%Y-%m-%d") if cutoff else ""

        grand_cr = sum(s.usage.cache_read_input_tokens for s in summaries)
        grand_inp = sum(s.usage.total_input for s in summaries)
        grand_hit = round((grand_cr / grand_inp * 100) if grand_inp > 0 else 0.0, 4)

        with self._conn() as conn:
            conn.execute("DELETE FROM runs WHERE run_at LIKE ?", (today + "%",))
            conn.execute("DELETE FROM runs_projects WHERE run_at LIKE ?", (today + "%",))

            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_at, cutoff_str,
                    sum(1 for s in summaries if s.sessions > 0),
                    sum(s.sessions for s in summaries),
                    sum(s.usage.input_tokens for s in summaries),
                    sum(s.usage.cache_creation_input_tokens for s in summaries),
                    sum(s.usage.cache_read_input_tokens for s in summaries),
                    sum(s.usage.output_tokens for s in summaries),
                    sum(s.usage.total for s in summaries),
                    round(sum(s.total_cost for s in summaries), 6),
                    sum(s.subagent_count for s in summaries),
                    sum(s.subagent_tokens for s in summaries),
                    grand_hit,
                ),
            )

            for s in summaries:
                u = s.usage
                ti = u.total_input
                hit = round((u.cache_read_input_tokens / ti * 100) if ti > 0 else 0.0, 4)
                conn.execute(
                    "INSERT INTO runs_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_at, s.project, s.sessions,
                        u.input_tokens, u.cache_creation_input_tokens,
                        u.cache_read_input_tokens, u.output_tokens, u.total,
                        round(s.total_cost, 6),
                        s.subagent_count, s.subagent_tokens,
                        hit,
                    ),
                )

        print(f"History written: runs, runs_projects ({len(summaries)} projects)")

    def write_sessions(self, all_projects: dict[str, list[SessionData]]) -> None:
        """
        Upsert per-session and prompt data for all projects.
        all_projects keys are intermediate names; display names are derived via _normalize_project.
        Subagents are written as separate rows with parent_session_id set.
        """
        session_rows = []
        prompt_rows = []

        for intermediate_name, sessions in all_projects.items():
            display = self._normalize_project(intermediate_name)
            for session in sessions:
                parent_file_id = Path(session.file).stem
                session_rows.append(self._session_row(session, display, None))
                for prompt in session.prompts:
                    prompt_rows.append((
                        parent_file_id, prompt.timestamp, display,
                        prompt.entrypoint, len(prompt.text), prompt.text,
                    ))
                for sub in session.subagent_sessions:
                    sub_file_id = Path(sub.file).stem
                    session_rows.append(self._session_row(sub, display, parent_file_id))
                    for prompt in sub.prompts:
                        prompt_rows.append((
                            sub_file_id, prompt.timestamp, display,
                            prompt.entrypoint, len(prompt.text), prompt.text,
                        ))

        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                session_rows,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO prompts VALUES (?,?,?,?,?,?)",
                prompt_rows,
            )

        print(f"Sessions written: {len(session_rows)} sessions, {len(prompt_rows)} prompts")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _maybe_migrate_csvs(self) -> None:
        """One-time import of legacy CSV data into SQLite (runs and runs_projects)."""
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        if count > 0:
            return  # Already have data, skip migration

        db_dir = self._db_path.parent
        runs_csv = db_dir / "runs.csv"
        projects_csv = db_dir / "runs_projects.csv"
        migrated_runs = 0

        with self._conn() as conn:
            if runs_csv.exists() and runs_csv.stat().st_size > 0:
                with open(runs_csv, newline="") as f:
                    for row in csv.DictReader(f):
                        try:
                            u_inp = int(row.get("input_tokens", 0) or 0)
                            u_cc = int(row.get("cache_creation_tokens", 0) or 0)
                            u_cr = int(row.get("cache_read_tokens", 0) or 0)
                            total_inp = u_inp + u_cc + u_cr
                            hit = round((u_cr / total_inp * 100) if total_inp > 0 else 0.0, 4)
                            conn.execute(
                                "INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    row["run_at"], row.get("cutoff_date", ""),
                                    int(row.get("projects", 0) or 0),
                                    int(row.get("sessions", 0) or 0),
                                    u_inp, u_cc, u_cr,
                                    int(row.get("output_tokens", 0) or 0),
                                    int(row.get("total_tokens", 0) or 0),
                                    float(row.get("total_cost_usd", 0) or 0),
                                    int(row.get("subagent_sessions", 0) or 0),
                                    int(row.get("subagent_tokens", 0) or 0),
                                    hit,
                                ),
                            )
                            migrated_runs += 1
                        except (ValueError, KeyError):
                            continue

            if projects_csv.exists() and projects_csv.stat().st_size > 0:
                with open(projects_csv, newline="") as f:
                    for row in csv.DictReader(f):
                        try:
                            u_inp = int(row.get("input_tokens", 0) or 0)
                            u_cc = int(row.get("cache_creation_tokens", 0) or 0)
                            u_cr = int(row.get("cache_read_tokens", 0) or 0)
                            total_inp = u_inp + u_cc + u_cr
                            hit = round((u_cr / total_inp * 100) if total_inp > 0 else 0.0, 4)
                            conn.execute(
                                "INSERT OR IGNORE INTO runs_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    row["run_at"], row["project"],
                                    int(row.get("sessions", 0) or 0),
                                    u_inp, u_cc, u_cr,
                                    int(row.get("output_tokens", 0) or 0),
                                    int(row.get("total_tokens", 0) or 0),
                                    float(row.get("total_cost_usd", 0) or 0),
                                    int(row.get("subagent_sessions", 0) or 0),
                                    int(row.get("subagent_tokens", 0) or 0),
                                    hit,
                                ),
                            )
                        except (ValueError, KeyError):
                            continue

        if migrated_runs > 0:
            print(f"Migrated {migrated_runs} historical runs from CSV → SQLite")

    def _session_row(
        self,
        s: SessionData,
        project: str,
        parent_file_id: str | None,
    ) -> tuple:
        u = s.usage
        ti = u.total_input
        hit = round((u.cache_read_input_tokens / ti * 100) if ti > 0 else 0.0, 4)
        return (
            Path(s.file).stem,  # file_id: unique per JSONL file (subagents use "agent-{uuid}")
            s.session_id,       # session_id: Claude UUID (may be shared with parent)
            project,
            1 if s.is_subagent else 0,
            parent_file_id,
            s.agent_type,
            s.agent_description,
            s.model,
            s.timestamp_start,
            s.entrypoint,
            s.cwd,
            s.git_branch,
            u.input_tokens, u.cache_creation_input_tokens, u.cache_read_input_tokens,
            u.output_tokens, u.total,
            round(s.cost, 6),
            hit,
            s.stop_reason,
            len(s.subagent_sessions),
            len(s.prompts),
        )

    def count_runs(self) -> int:
        """Return the total number of run entries in the DB."""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def write_historical_runs(
        self, dated_summaries: dict[str, list[ProjectSummary]]
    ) -> int:
        """
        Backfill runs/runs_projects for dates not already present in the DB.

        dated_summaries: maps 'YYYY-MM-DD' → list of ProjectSummary for that day.
        Returns the number of new dates written.
        """
        with self._conn() as conn:
            existing_dates = {
                row[0][:10]
                for row in conn.execute("SELECT run_at FROM runs").fetchall()
            }

        written = 0
        with self._conn() as conn:
            for date_str, summaries in sorted(dated_summaries.items()):
                if date_str in existing_dates:
                    continue

                run_at = f"{date_str}T09:00:00Z"
                grand_cr = sum(s.usage.cache_read_input_tokens for s in summaries)
                grand_inp = sum(s.usage.total_input for s in summaries)
                grand_hit = round((grand_cr / grand_inp * 100) if grand_inp > 0 else 0.0, 4)

                conn.execute(
                    "INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_at, "",
                        sum(1 for s in summaries if s.sessions > 0),
                        sum(s.sessions for s in summaries),
                        sum(s.usage.input_tokens for s in summaries),
                        sum(s.usage.cache_creation_input_tokens for s in summaries),
                        sum(s.usage.cache_read_input_tokens for s in summaries),
                        sum(s.usage.output_tokens for s in summaries),
                        sum(s.usage.total for s in summaries),
                        round(sum(s.total_cost for s in summaries), 6),
                        sum(s.subagent_count for s in summaries),
                        sum(s.subagent_tokens for s in summaries),
                        grand_hit,
                    ),
                )

                for s in summaries:
                    u = s.usage
                    ti = u.total_input
                    hit = round((u.cache_read_input_tokens / ti * 100) if ti > 0 else 0.0, 4)
                    conn.execute(
                        "INSERT OR IGNORE INTO runs_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            run_at, s.project, s.sessions,
                            u.input_tokens, u.cache_creation_input_tokens,
                            u.cache_read_input_tokens, u.output_tokens, u.total,
                            round(s.total_cost, 6),
                            s.subagent_count, s.subagent_tokens,
                            hit,
                        ),
                    )

                written += 1

        return written

    def _normalize_project(self, name: str) -> str:
        if self._strip_prefix and name.startswith(self._strip_prefix):
            return name[len(self._strip_prefix):]
        return name
