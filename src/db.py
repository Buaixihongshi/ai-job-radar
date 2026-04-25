"""SQLite persistence layer for AI Job Radar.

Provides upsert/query operations on jobs.db.
JSON files are still generated for backward-compatibility and GitHub README rendering.

Schema
------
jobs        - active/historical job postings with first_seen / last_seen tracking
scrape_runs - per-platform run health metrics
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from src.models import JobPosting

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"

_CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    unique_key   TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    job_id       TEXT NOT NULL,
    title        TEXT NOT NULL,
    company      TEXT DEFAULT '',
    department   TEXT DEFAULT '',
    location     TEXT DEFAULT '',
    experience   TEXT DEFAULT '',
    education    TEXT DEFAULT '',
    salary       TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    requirements TEXT DEFAULT '',
    url          TEXT DEFAULT '',
    publish_date TEXT DEFAULT '',
    category     TEXT DEFAULT '',
    scraped_at   TEXT DEFAULT '',
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    is_active    INTEGER DEFAULT 1
);
"""

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date         TEXT NOT NULL,
    platform         TEXT NOT NULL,
    raw_count        INTEGER DEFAULT 0,
    filtered_count   INTEGER DEFAULT 0,
    duration_seconds REAL    DEFAULT 0,
    status           TEXT    DEFAULT 'success',
    error_msg        TEXT    DEFAULT ''
);
"""

_CREATE_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_jobs_platform   ON jobs(platform);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_is_active  ON jobs(is_active);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);",
    "CREATE INDEX IF NOT EXISTS idx_runs_date       ON scrape_runs(run_date);",
]


@contextmanager
def _conn(db_path: Path = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables and indexes if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn(db_path) as con:
        con.execute(_CREATE_JOBS)
        con.execute(_CREATE_RUNS)
        for idx in _CREATE_IDX:
            con.execute(idx)
    logger.debug("DB initialised at %s", db_path)


def upsert_jobs(jobs: list[JobPosting], db_path: Path = DB_PATH) -> dict[str, int]:
    """Insert new jobs or update last_seen for existing ones.

    Returns counts: {inserted, updated, total}.
    """
    init_db(db_path)
    today = datetime.now().strftime("%Y-%m-%d")
    inserted = updated = 0

    with _conn(db_path) as con:
        existing = {
            row["unique_key"]: row["first_seen"]
            for row in con.execute("SELECT unique_key, first_seen FROM jobs")
        }

        for job in jobs:
            key = job.unique_key
            if key in existing:
                con.execute(
                    "UPDATE jobs SET last_seen=?, is_active=1, title=?, location=?,"
                    " description=?, requirements=?, category=?, scraped_at=? WHERE unique_key=?",
                    (today, job.title, job.location, job.description,
                     job.requirements, job.category, job.scraped_at, key),
                )
                updated += 1
            else:
                con.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        key, job.platform, job.job_id, job.title, job.company,
                        job.department, job.location, job.experience, job.education,
                        job.salary, job.description, job.requirements, job.url,
                        job.publish_date, job.category, job.scraped_at, today, today,
                    ),
                )
                inserted += 1

        # Mark jobs not in current run as inactive
        active_keys = {j.unique_key for j in jobs}
        con.execute(
            f"UPDATE jobs SET is_active=0 WHERE is_active=1 AND unique_key NOT IN "
            f"({','.join('?' * len(active_keys))})",
            list(active_keys),
        )

    counts = {"inserted": inserted, "updated": updated, "total": inserted + updated}
    logger.info("DB upsert: %s", counts)
    return counts


def load_active_jobs(db_path: Path = DB_PATH) -> list[JobPosting]:
    """Load all currently active jobs from DB."""
    init_db(db_path)
    with _conn(db_path) as con:
        rows = con.execute("SELECT * FROM jobs WHERE is_active=1").fetchall()
    return [_row_to_job(r) for r in rows]


def load_all_jobs(db_path: Path = DB_PATH) -> list[JobPosting]:
    """Load all jobs (active + inactive) for trend analysis."""
    init_db(db_path)
    with _conn(db_path) as con:
        rows = con.execute("SELECT * FROM jobs").fetchall()
    return [_row_to_job(r) for r in rows]


def log_scrape_run(
    platform: str,
    raw_count: int,
    filtered_count: int,
    duration: float,
    status: str = "success",
    error_msg: str = "",
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn(db_path) as con:
        con.execute(
            "INSERT INTO scrape_runs (run_date, platform, raw_count, filtered_count,"
            " duration_seconds, status, error_msg) VALUES (?,?,?,?,?,?,?)",
            (today, platform, raw_count, filtered_count, duration, status, error_msg),
        )


def check_circuit_breaker(
    platform: str,
    consecutive_days: int = 3,
    db_path: Path = DB_PATH,
) -> bool:
    """Return True if the platform should be **skipped** (circuit open).

    A platform is tripped when its most recent *consecutive_days* runs all
    have ``raw_count == 0`` and ``status == 'success'`` (i.e. the scraper ran
    normally but found nothing — likely anti-scraping block, not a code bug).

    Returns ``False`` (circuit closed, OK to scrape) when:
    - fewer than *consecutive_days* runs exist, or
    - any recent run had ``raw_count > 0``, or
    - any recent run had a non-success status (we only trip on silent failures).
    """
    if not db_path.exists():
        return False
    init_db(db_path)
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT raw_count, status FROM scrape_runs "
            "WHERE platform = ? ORDER BY id DESC LIMIT ?",
            (platform, consecutive_days),
        ).fetchall()

    if len(rows) < consecutive_days:
        return False
    return all(r["raw_count"] == 0 and r["status"] == "success" for r in rows)


def query_jobs(
    platform: str | None = None,
    active_only: bool = True,
    category: str | None = None,
    db_path: Path = DB_PATH,
) -> list[JobPosting]:
    """Flexible query helper.

    All filter values are passed as parameterised arguments — no f-string
    interpolation of user-supplied data — to prevent SQL injection.
    """
    init_db(db_path)
    # Build WHERE clause from a whitelist of static condition fragments.
    # Only the *values* (platform, category) come from callers; the column
    # names and operators are hardcoded here and never interpolated.
    clauses: list[str] = []
    params: list[object] = []

    if active_only:
        clauses.append("is_active = 1")
    if platform is not None:
        clauses.append("platform = ?")
        params.append(platform)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)

    # The WHERE clause is assembled from static string literals only.
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = "SELECT * FROM jobs " + where   # noqa: S608 – clauses are static

    with _conn(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [_row_to_job(r) for r in rows]


def get_run_history(days: int = 30, db_path: Path = DB_PATH) -> list[dict]:
    """Return scrape run history for health reporting."""
    init_db(db_path)
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT * FROM scrape_runs WHERE run_date >= date('now', ?) ORDER BY id DESC",
            (f"-{days} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def get_platform_freshness(db_path: Path = DB_PATH) -> list[dict]:
    """Per-platform freshness summary: latest scrape time, total active jobs, etc.

    Returns a list of dicts sorted by ``last_run`` descending::

        [{"platform": "tencent", "last_run": "2026-04-25 09:00:12",
          "raw_count": 45, "filtered_count": 22, "active_jobs": 88, "status": "success"}, ...]

    Falls back to ``jobs.last_seen`` aggregation when ``scrape_runs`` is empty
    (e.g. fresh local clone without CI history).
    """
    if not db_path.exists():
        return []
    init_db(db_path)
    with _conn(db_path) as con:
        has_runs = con.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0] > 0

        if has_runs:
            sql = """\
            SELECT
                r.platform,
                r.run_date   AS last_run,
                r.raw_count,
                r.filtered_count,
                r.status,
                COALESCE(j.active_jobs, 0) AS active_jobs
            FROM scrape_runs r
            INNER JOIN (
                SELECT platform, MAX(id) AS max_id
                FROM scrape_runs
                GROUP BY platform
            ) latest ON r.id = latest.max_id
            LEFT JOIN (
                SELECT platform, COUNT(*) AS active_jobs
                FROM jobs WHERE is_active = 1
                GROUP BY platform
            ) j ON r.platform = j.platform
            ORDER BY r.run_date DESC
            """
        else:
            sql = """\
            SELECT
                platform,
                MAX(last_seen) AS last_run,
                0              AS raw_count,
                COUNT(*)       AS filtered_count,
                'success'      AS status,
                COUNT(*)       AS active_jobs
            FROM jobs
            WHERE is_active = 1
            GROUP BY platform
            ORDER BY last_run DESC
            """

        rows = con.execute(sql).fetchall()
    return [dict(r) for r in rows]


def _row_to_job(row: sqlite3.Row) -> JobPosting:
    d = dict(row)
    d.pop("first_seen", None)
    d.pop("last_seen", None)
    d.pop("is_active", None)
    return JobPosting.from_dict(d)
