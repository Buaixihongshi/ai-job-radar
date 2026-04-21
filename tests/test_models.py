"""Unit tests for src/models.py."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.models import JobPosting, load_jobs_from_json, save_jobs_to_json


@pytest.fixture
def sample_job():
    return JobPosting(
        job_id="abc123",
        platform="tencent",
        company="腾讯",
        title="AI测试工程师",
        location="北京",
        description="负责大模型质量保障",
        category="大模型/AI测试",
    )


class TestJobPosting:
    def test_unique_key(self, sample_job):
        assert sample_job.unique_key == "tencent:abc123"

    def test_content_hash_consistent(self, sample_job):
        h1 = sample_job.content_hash
        h2 = sample_job.content_hash
        assert h1 == h2
        assert len(h1) == 12

    def test_content_hash_changes_on_title(self, sample_job):
        h1 = sample_job.content_hash
        sample_job.title = "Changed Title"
        h2 = sample_job.content_hash
        assert h1 != h2

    def test_to_dict_roundtrip(self, sample_job):
        d = sample_job.to_dict()
        restored = JobPosting.from_dict(d)
        assert restored.job_id == sample_job.job_id
        assert restored.title == sample_job.title
        assert restored.unique_key == sample_job.unique_key

    def test_from_dict_ignores_unknown_fields(self):
        d = {
            "job_id": "x1",
            "platform": "baidu",
            "title": "测试",
            "company": "百度",
            "unknown_field": "should be ignored",
        }
        job = JobPosting.from_dict(d)
        assert job.job_id == "x1"

    def test_match_keywords(self, sample_job):
        matches = sample_job.match_keywords(["大模型", "Agent", "Java"])
        assert "大模型" in matches
        assert "Java" not in matches

    def test_classify_by_categories(self, sample_job):
        categories = {
            "test": {"keywords": ["测试", "QA"]},
            "product": {"keywords": ["产品经理"]},
        }
        cat = sample_job.classify(categories)
        assert cat == "test"

    def test_classify_returns_other(self, sample_job):
        sample_job.title = "市场营销专员"
        sample_job.description = ""
        cat = sample_job.classify({"test": {"keywords": ["测试"]}})
        assert cat == "other"


class TestJsonIO:
    def test_save_and_load(self, sample_job):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        save_jobs_to_json([sample_job], path)
        loaded = load_jobs_from_json(path)
        assert len(loaded) == 1
        assert loaded[0].job_id == sample_job.job_id
        assert loaded[0].title == sample_job.title

    def test_load_missing_file(self):
        result = load_jobs_from_json("/nonexistent/path/jobs.json")
        assert result == []

    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("NOT VALID JSON {{{")
            path = f.name
        result = load_jobs_from_json(path)
        assert result == []
