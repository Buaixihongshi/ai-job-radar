"""Strict title-based filtering.

Only 4 categories are kept:
  1. 测试      - title contains 测试 but NOT 测试开发
  2. 测试开发  - title contains 测试开发
  3. Agent评测 - title contains 评测 AND (Agent/AI/大模型/LLM/AIGC)
  4. Agent产品 - title contains 产品 AND (Agent/AI/大模型/LLM/AIGC)
"""
from __future__ import annotations

import logging
import re

from src.models import JobPosting

logger = logging.getLogger(__name__)

BLACKLIST_TITLE_PATTERNS = re.compile(
    r"(安全渗透|安全攻防|红队|渗透测试|安全工程师|"
    r"设计师|美术|编剧|导演|运营|销售|市场|财务|商务|法务|行政|"
    r"标注|数据服务|数据标注|"
    r"短剧|版权|内容创作|"
    r"实习生)",
    re.IGNORECASE,
)


def classify_strict(job: JobPosting) -> str | None:
    """Return category or None if job should be excluded."""
    title = job.title.strip()

    if not title or len(title) < 4:
        return None

    if title.startswith("script>"):
        return None
    if BLACKLIST_TITLE_PATTERNS.search(title):
        return None

    title_lower = title.lower()

    if "测试开发" in title or "测试工具开发" in title:
        return "测试开发"

    ai_context = any(kw in title_lower for kw in [
        "agent", "ai", "大模型", "llm", "aigc", "智能", "模型",
    ])

    if "评测" in title:
        if ai_context:
            return "Agent评测"
        return "Agent评测"

    if "测试" in title:
        return "测试"

    if "产品" in title and ai_context:
        return "Agent产品"

    if "qa" in title_lower or "质量保障" in title or "质量" in title:
        return "测试"

    return None


def filter_strict(jobs: list[JobPosting]) -> list[JobPosting]:
    """Keep only jobs matching the 4 target categories, based on title."""
    result = []
    for job in jobs:
        cat = classify_strict(job)
        if cat:
            job.category = cat
            result.append(job)

    logger.info(
        "Strict filter: %d/%d jobs passed (%.0f%% removed)",
        len(result), len(jobs),
        (1 - len(result) / max(len(jobs), 1)) * 100,
    )
    return result
