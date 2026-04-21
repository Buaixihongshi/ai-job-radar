"""Unit tests for src/db.py (SQLite layer)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.db import init_db, upsert_jobs, load_active_jobs, load_all_jobs, log_scrape_run, get_run_history
from src.models import JobPosting


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test_jobs.db"


@pytest.fixture
def sample_jobs():
    return [
        JobPosting(
            job_id="j001", platform="tencent", company="腾讯",
            title="AI测试工程师", location="北京", category="大模型/AI测试",
        ),
        JobPosting(
            job_id="j002", platform="baidu", company="百度",
            title="大模型评测研究员", location="上海", category="Agent评测",
        ),
    ]


class TestInit:
    def test_creates_db(self, tmp_db):
        init_db(tmp_db)
        assert tmp_db.exists()

    def test_idempotent(self, tmp_db):
        init_db(tmp_db)
        init_db(tmp_db)  # should not raise


class TestUpsert:
    def test_inserts_new_jobs(self, tmp_db, sample_jobs):
        counts = upsert_jobs(sample_jobs, tmp_db)
        assert counts["inserted"] == 2
        assert counts["updated"] == 0

    def test_updates_existing_jobs(self, tmp_db, sample_jobs):
        upsert_jobs(sample_jobs, tmp_db)
        counts = upsert_jobs(sample_jobs, tmp_db)
        assert counts["inserted"] == 0
        assert counts["updated"] == 2

    def test_marks_removed_inactive(self, tmp_db, sample_jobs):
        upsert_jobs(sample_jobs, tmp_db)
        # Only pass first job in next run
        upsert_jobs([sample_jobs[0]], tmp_db)
        all_jobs = load_all_jobs(tmp_db)
        active = [j for j in all_jobs if True]  # all returned
        assert len(all_jobs) == 2
        active_jobs = load_active_jobs(tmp_db)
        assert len(active_jobs) == 1
        assert active_jobs[0].unique_key == "tencent:j001"


class TestLoad:
    def test_load_active_empty(self, tmp_db):
        assert load_active_jobs(tmp_db) == []

    def test_load_returns_job_postings(self, tmp_db, sample_jobs):
        upsert_jobs(sample_jobs, tmp_db)
        jobs = load_active_jobs(tmp_db)
        assert len(jobs) == 2
        titles = {j.title for j in jobs}
        assert "AI测试工程师" in titles

    def test_load_all_includes_inactive(self, tmp_db, sample_jobs):
        upsert_jobs(sample_jobs, tmp_db)
        upsert_jobs([sample_jobs[0]], tmp_db)
        all_jobs = load_all_jobs(tmp_db)
        assert len(all_jobs) == 2


class TestRunLog:
    def test_log_and_retrieve(self, tmp_db):
        log_scrape_run("tencent", raw_count=50, filtered_count=10, duration=12.5, db_path=tmp_db)
        history = get_run_history(days=30, db_path=tmp_db)
        assert len(history) >= 1
        assert history[0]["platform"] == "tencent"
        assert history[0]["raw_count"] == 50
        assert history[0]["status"] == "success"

    def test_error_status(self, tmp_db):
        log_scrape_run(
            "meituan", raw_count=0, filtered_count=0, duration=5.0,
            status="error", error_msg="page load failed",
            db_path=tmp_db,
        )
        history = get_run_history(days=30, db_path=tmp_db)
        assert history[0]["status"] == "error"
        assert "page load" in history[0]["error_msg"]
