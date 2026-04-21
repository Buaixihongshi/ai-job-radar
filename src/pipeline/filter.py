"""Precision filtering for AI testing/evaluation job search.

Target directions (from user's WPS JD reference):
  1. 大模型/AI测试    - 大模型测试/AI测试/算法测试 (title must have AI context)
  2. 测试开发(AI方向)  - 测试开发 with AI/大模型/Agent context in title or description
  3. Agent评测        - 大模型评测/Agent评测/模型评估
  4. AI/Agent产品     - ONLY Agent产品/AIGC产品/评测产品 (tight scope)

All keyword lists are configured in config.yaml under ``filter_rules``.
Edit config.yaml to tune filtering; no Python changes needed.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.models import JobPosting

logger = logging.getLogger(__name__)


# ── Default keyword lists (used when config.yaml is unavailable) ─────────────

_DEFAULT_RULES: dict[str, Any] = {
    "exclude_education": ["硕士", "博士", "研究生", "Master", "PhD"],
    "exclude_experience_field": ["五年以上", "八年以上", "十年以上", "5-10年", "10年以上"],
    "max_experience_years": 3,
    "campus_patterns": ["校招", "应届", "届+", "27届", "28届", "26届", "毕业生", "campus"],
    "exclude_title": [
        "硬件", "嵌入式", "芯片", "射频", "FPGA", "驱动工程", "电气", "机械", "光学",
        "相机测试", "传感器测试", "基带测试", "可靠性测试", "认证测试", "近距通信", "显示测试",
        "PQE", "结构工艺", "质量PQA", "制造质量", "电子件",
        "数据库内核", "DBA", "存储引擎", "SRE", "运维工程",
        "安全渗透", "红队", "攻防", "漏洞挖掘", "安全合规", "安全评测", "安全产品",
        "设计师", "美术", "编剧", "导演", "短剧", "版权",
        "标注主管", "数据标注",
        "MicroLED", "显示驱动", "ATE测试",
        "实习生", "后台开发工程师", "架构师", "解决方案",
    ],
    "ai_title_keywords": [
        "AI", "人工智能", "大模型", "LLM", "Agent", "AIGC", "算法", "多模态", "NLP",
        "模型评测", "模型评估", "智能体", "GPT", "RAG", "Prompt",
        "千问", "元宝", "混元", "文心", "通义", "copilot", "ima", "CodeBuddy",
    ],
    "ai_desc_keywords": [
        "大模型", "LLM", "Agent", "AIGC", "算法测试", "多模态", "NLP",
        "模型评测", "模型评估", "智能体", "GPT", "RAG", "Prompt",
        "Benchmark", "badcase", "模型效果", "模型质量", "AI质量",
        "算法效果", "算法质量", "评测平台", "评测框架",
    ],
    "exclude_game_title": [
        "SLG", "MMORPG", "单机", "FPS", "竞速", "赛车", "格斗", "RTS", "卡牌",
        "引擎测试", "引擎开发", "遗忘之海", "大世界", "天美中台", "无限大",
    ],
    "exclude_product_title": [
        "外贸", "社交", "音乐", "写歌", "推荐", "分发", "投放", "广告", "OCR",
        "招聘系统", "地图", "云AI-ToB", "轻量云", "WeGame",
        "具身智能", "数据", "文档产品", "平台产品经理", "计算平台",
    ],
}


# ── Compiled regex cache ─────────────────────────────────────────────────────

class _FilterRules:
    """Compiled regex patterns built from a rules dict."""

    def __init__(self, rules: dict[str, Any]):
        self.max_exp_years: int = int(rules.get("max_experience_years", 3))

        self.HIGH_EDU_REQUIRED = self._compile(rules["exclude_education"], "i")
        self.HIGH_EXP_FIELD = self._compile(rules["exclude_experience_field"])
        self.CAMPUS = self._compile(rules["campus_patterns"], "i")
        self.EXCLUDE_TITLE = self._compile(rules["exclude_title"], "i")
        self.AI_IN_TITLE = self._compile(rules["ai_title_keywords"], "i")
        self.AI_IN_DESC = self._compile(rules["ai_desc_keywords"], "i")
        self.GAME = self._compile(rules["exclude_game_title"], "i")
        self.PRODUCT_EXCLUDE = self._compile(rules["exclude_product_title"], "i")

        # Static patterns that don't change with config
        self.HIGH_EXP_TEXT = re.compile(r"(\d+)\s*年以?上.{0,6}(工作|经验|经历)")
        self.HIGH_EDU_IN_REQ = re.compile(
            r"硕士及以上|硕士以上|研究生及以上|研究生以上|硕士学历"
        )

    @staticmethod
    def _compile(keywords: list[str], flags: str = "") -> re.Pattern:
        escaped = [re.escape(k) for k in keywords]
        pattern = "(" + "|".join(escaped) + ")"
        rf = re.IGNORECASE if "i" in flags else 0
        return re.compile(pattern, rf)


@lru_cache(maxsize=1)
def _load_rules_from_config() -> _FilterRules:
    """Load filter rules from config.yaml; falls back to defaults on any error."""
    try:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        import yaml  # type: ignore[import]
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        rules = cfg.get("filter_rules", {})
        merged = {**_DEFAULT_RULES, **rules}
        logger.debug("Filter rules loaded from config.yaml")
        return _FilterRules(merged)
    except Exception as exc:
        logger.warning("Cannot load filter_rules from config (%s); using defaults", exc)
        return _FilterRules(_DEFAULT_RULES)


def reload_filter_rules(config: dict | None = None) -> None:
    """Force-reload filter rules (useful after config changes or in tests).

    Pass a config dict directly to bypass file loading (handy in tests)::

        reload_filter_rules({"filter_rules": {"max_experience_years": 5}})
    """
    _load_rules_from_config.cache_clear()
    if config is not None:
        rules = {**_DEFAULT_RULES, **config.get("filter_rules", {})}
        # Inject a pre-built instance so the lru_cache returns it on next call
        _load_rules_from_config.cache_clear()
        _INJECTED_RULES.clear()
        _INJECTED_RULES.append(_FilterRules(rules))


_INJECTED_RULES: list[_FilterRules] = []


def _get_rules() -> _FilterRules:
    if _INJECTED_RULES:
        return _INJECTED_RULES[0]
    return _load_rules_from_config()


# ── Eligibility helpers ──────────────────────────────────────────────────────

def _title_has_ai(title: str) -> bool:
    return bool(_get_rules().AI_IN_TITLE.search(title))


def _desc_has_ai(job: JobPosting) -> bool:
    text = f"{job.description} {job.requirements}"
    return bool(_get_rules().AI_IN_DESC.search(text))


def _check_eligibility(job: JobPosting) -> str | None:
    """Return rejection reason string, or None if eligible."""
    r = _get_rules()
    title = job.title

    if r.CAMPUS.search(title):
        return "campus"

    edu = (job.education or "").strip()
    if edu and r.HIGH_EDU_REQUIRED.search(edu):
        return "edu_high"

    req_text = job.requirements or ""
    if r.HIGH_EDU_IN_REQ.search(req_text):
        return "edu_high_in_req"

    exp = str(job.experience or "").strip()
    if exp and r.HIGH_EXP_FIELD.search(exp):
        return "exp_high"
    if exp:
        m_exp = re.search(r"(\d+)\s*年", exp)
        if m_exp and int(m_exp.group(1)) > r.max_exp_years:
            return "exp_high"

    m = r.HIGH_EXP_TEXT.search(req_text)
    if m and int(m.group(1)) > r.max_exp_years:
        return "exp_high_in_req"

    return None


# ── Main classification logic ────────────────────────────────────────────────

def classify_strict(job: JobPosting) -> str | None:
    """Return category string for a matching job, or None to reject it."""
    r = _get_rules()
    title = job.title.strip()

    if not title or len(title) < 4:
        return None
    if title.startswith(("script>", "window.")):
        return None
    if r.EXCLUDE_TITLE.search(title):
        return None
    if r.GAME.search(title):
        return None

    if _check_eligibility(job):
        return None

    title_ai = _title_has_ai(title)
    desc_ai = _desc_has_ai(job)

    # --- 测试开发(AI方向) ---
    if "测试开发" in title or "自动化测试" in title:
        return "测试开发(AI方向)" if (title_ai or desc_ai) else None

    # --- Agent评测 ---
    if "评测" in title or ("评估" in title and ("模型" in title or "agent" in title.lower())):
        if re.search(r"算法(工程师|研究员)", title) and "评测" not in title:
            return None
        return "Agent评测"

    # --- 大模型/AI测试 ---
    # "质量" alone requires AI context (title_ai) to avoid broad matching
    if any(kw in title for kw in ["测试", "质量保障", "质量"]) or "QA" in title.upper():
        if "游戏" in title and not title_ai:
            return None
        return "大模型/AI测试" if (title_ai or desc_ai) else None

    # --- AI/Agent产品 (tight scope) ---
    if "产品" in title:
        title_lower = title.lower()
        is_agent_product = (
            "agent" in title_lower
            or "评测" in title
            or "评估" in title
            or (
                "策略产品" in title
                and any(brand in title for brand in ["元宝", "ima", "CodeBuddy", "WorkBuddy"])
            )
            or "AIGC产品" in title
        )
        if is_agent_product and not r.PRODUCT_EXCLUDE.search(title):
            return "AI/Agent产品"
        return None

    return None


def filter_strict(jobs: list[JobPosting]) -> list[JobPosting]:
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
