from __future__ import annotations

import re

from src.models import JobPosting

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("test", [
        "测试", "QA", "质量保障", "质量", "评测", "benchmark", "badcase",
        "测试开发", "测试工程", "自动化测试", "性能测试", "安全测试",
        "test", "quality", "大模型评测", "模型评估",
    ]),
    ("agent", [
        "agent", "智能体", "LLM应用", "AI应用", "RAG",
        "大模型", "LLM", "AIGC", "GPT", "NLP", "多模态",
        "算法", "推理", "训练", "模型",
    ]),
    ("product", [
        "产品经理", "产品运营", "策略产品", "产品策划",
        "产品", "product",
    ]),
    ("dev", [
        "开发", "工程师", "研发", "架构", "前端", "后端",
        "运维", "SRE", "DevOps", "CI/CD",
        "工具开发", "平台开发", "自动化开发", "效能",
    ]),
]

PLATFORM_CATEGORY_MAP = {
    "技术": "dev",
    "产品": "product",
    "设计": "other",
    "市场": "other",
    "运营": "other",
    "销售": "other",
    "职能": "other",
}


def classify_job(job: JobPosting, categories: dict) -> str:
    text = f"{job.title} {job.department} {job.description} {job.requirements}".lower()

    if job.category and job.category in PLATFORM_CATEGORY_MAP:
        mapped = PLATFORM_CATEGORY_MAP[job.category]
        for cat_id, kw_list in CATEGORY_RULES:
            if any(kw.lower() in text for kw in kw_list):
                return cat_id
        return mapped

    for cat_id, kw_list in CATEGORY_RULES:
        if any(kw.lower() in text for kw in kw_list):
            return cat_id

    if categories:
        for cat_id, cat_cfg in categories.items():
            cat_keywords = cat_cfg.get("keywords", [])
            if any(kw.lower() in text for kw in cat_keywords):
                return cat_id

    return "other"


def normalize_job(job: JobPosting, categories: dict) -> JobPosting:
    job.title = _clean_text(job.title)
    job.description = _clean_text(job.description)
    job.requirements = _clean_text(job.requirements)
    job.location = _normalize_location(job.location)

    job.category = classify_job(job, categories)
    return job


def normalize_jobs(jobs: list[JobPosting], categories: dict) -> list[JobPosting]:
    return [normalize_job(j, categories) for j in jobs]


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_location(loc: str) -> str:
    if not loc:
        return ""
    loc = loc.replace("，", ",").replace("、", ",")
    for suffix in ["市", "区"]:
        loc = loc.replace(suffix, "")
    return loc.strip()
