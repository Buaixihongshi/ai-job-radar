"""Unit tests for src/pipeline/filter.py.

Tests are intentionally independent of config.yaml by using reload_filter_rules()
with an injected config dictionary.
"""
from __future__ import annotations

import pytest

from src.models import JobPosting
from src.pipeline.filter import classify_strict, filter_strict, reload_filter_rules


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_filter_rules():
    """Reset injected rules before each test."""
    reload_filter_rules()
    yield
    reload_filter_rules()


def _job(title: str, desc: str = "", edu: str = "", exp: str = "", req: str = "") -> JobPosting:
    return JobPosting(
        job_id="test-1",
        platform="test",
        company="测试公司",
        title=title,
        description=desc,
        education=edu,
        experience=exp,
        requirements=req,
    )


# ── PASS cases ────────────────────────────────────────────────────────────────

class TestShouldPass:
    def test_ai_test_in_title(self):
        assert classify_strict(_job("AI测试工程师")) is not None

    def test_bigmodel_test(self):
        assert classify_strict(_job("大模型测试工程师")) == "大模型/AI测试"

    def test_auto_test_with_ai_desc(self):
        job = _job("自动化测试工程师", desc="负责大模型能力评测，构建Benchmark体系")
        assert classify_strict(job) == "测试开发(AI方向)"

    def test_test_dev_ai_title(self):
        assert classify_strict(_job("测试开发工程师 (AI方向)")) == "测试开发(AI方向)"

    def test_agent_product(self):
        assert classify_strict(_job("Agent产品经理")) == "AI/Agent产品"

    def test_aigc_product(self):
        assert classify_strict(_job("AIGC产品经理")) == "AI/Agent产品"

    def test_agent_eval(self):
        assert classify_strict(_job("大模型评测工程师")) == "Agent评测"

    def test_model_eval(self):
        assert classify_strict(_job("模型评测研究员")) == "Agent评测"

    def test_quality_ai_title(self):
        assert classify_strict(_job("AI质量工程师")) is not None

    def test_experience_3years_ok(self):
        job = _job("大模型测试工程师", exp="3年", req="3年以上工作经验")
        assert classify_strict(job) is not None

    def test_experience_field_3years(self):
        job = _job("AI测试工程师", exp="3年以内")
        assert classify_strict(job) is not None


# ── REJECT cases ─────────────────────────────────────────────────────────────

class TestShouldReject:
    def test_hardware_engineer(self):
        assert classify_strict(_job("硬件测试工程师")) is None

    def test_embedded(self):
        assert classify_strict(_job("嵌入式软件开发")) is None

    def test_security(self):
        assert classify_strict(_job("安全渗透测试工程师")) is None

    def test_campus_recruit(self):
        assert classify_strict(_job("校招-AI测试工程师")) is None

    def test_phd_required(self):
        job = _job("大模型测试", edu="博士")
        assert classify_strict(job) is None

    def test_master_in_req(self):
        job = _job("AI测试", req="硕士及以上学历，计算机相关专业")
        assert classify_strict(job) is None

    def test_exp_5years(self):
        job = _job("大模型测试工程师", exp="五年以上")
        assert classify_strict(job) is None

    def test_exp_high_in_text(self):
        job = _job("AI测试工程师", req="要求5年以上工作经验")
        assert classify_strict(job) is None

    def test_game_test_no_ai(self):
        assert classify_strict(_job("SLG游戏测试工程师")) is None

    def test_plain_qa_no_ai(self):
        assert classify_strict(_job("QA工程师")) is None

    def test_short_title(self):
        assert classify_strict(_job("AI")) is None

    def test_product_map(self):
        assert classify_strict(_job("地图产品经理")) is None

    def test_product_advertising(self):
        assert classify_strict(_job("广告产品经理")) is None

    def test_data_labeling(self):
        assert classify_strict(_job("数据标注工程师")) is None

    def test_algorithm_engineer_no_eval(self):
        assert classify_strict(_job("算法工程师(NLP方向)")) is None


# ── filter_strict (batch) ─────────────────────────────────────────────────────

class TestFilterStrict:
    def test_batch_filter(self):
        jobs = [
            _job("大模型测试工程师"),
            _job("硬件工程师"),
            _job("AI质量工程师"),
            _job("校招-测试开发"),
            _job("Agent产品经理"),
        ]
        result = filter_strict(jobs)
        titles = [j.title for j in result]
        assert "大模型测试工程师" in titles
        assert "AI质量工程师" in titles
        assert "Agent产品经理" in titles
        assert "硬件工程师" not in titles
        assert "校招-测试开发" not in titles

    def test_category_assigned(self):
        jobs = [_job("大模型测试工程师")]
        result = filter_strict(jobs)
        assert result[0].category == "大模型/AI测试"

    def test_empty_input(self):
        assert filter_strict([]) == []


# ── Config injection ──────────────────────────────────────────────────────────

class TestConfigInjection:
    def test_custom_max_experience(self):
        reload_filter_rules({"filter_rules": {"max_experience_years": 5}})
        job = _job("AI测试工程师", req="要求5年以上工作经验")
        assert classify_strict(job) is not None

    def test_custom_exclude_title(self):
        reload_filter_rules({"filter_rules": {"exclude_title": ["自定义排除"]}})
        assert classify_strict(_job("自定义排除测试")) is None
