"""Generate README index + per-company job detail files."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.models import JobPosting

logger = logging.getLogger(__name__)

PLATFORM_NAMES = {
    "tencent": "腾讯",
    "alibaba": "阿里巴巴",
    "bytedance": "字节跳动",
    "baidu": "百度",
    "netease": "网易",
    "boss": "Boss直聘",
    "liepin": "猎聘",
    "zhilian": "智联招聘",
}

CATEGORY_ORDER = ["大模型/AI测试", "测试开发(AI方向)", "Agent评测", "AI/Agent产品"]


def _truncate(text: str, max_len: int = 800) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def _render_job(j: JobPosting) -> list[str]:
    """Render a single job posting as markdown lines."""
    lines = []
    lines.append(f"### {j.title}")
    lines.append("")

    meta = []
    if j.location:
        meta.append(f"📍 {j.location}")
    if j.department:
        meta.append(f"🏢 {j.department}")
    if j.salary:
        meta.append(f"💰 {j.salary}")
    if j.experience:
        meta.append(f"📅 {j.experience}")
    if j.education:
        meta.append(f"🎓 {j.education}")

    if meta:
        lines.append(" | ".join(meta))
        lines.append("")

    if j.url:
        lines.append(f"🔗 [投递链接]({j.url})")
        lines.append("")

    desc = _truncate(j.description)
    req = _truncate(j.requirements)

    if desc:
        lines.append("**岗位职责：**")
        lines.append("")
        for line in desc.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
                lines.append("")

    if req:
        lines.append("**岗位要求：**")
        lines.append("")
        for line in req.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
                lines.append("")

    if not desc and not req:
        lines.append("*详情请点击投递链接查看*")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


def generate_company_files(jobs: list[JobPosting], jobs_dir: Path) -> dict[str, Path]:
    """Generate per-company markdown files. Returns {company_name: file_path}."""
    jobs_dir.mkdir(parents=True, exist_ok=True)

    by_company: dict[str, list[JobPosting]] = defaultdict(list)
    for j in jobs:
        company = j.company or PLATFORM_NAMES.get(j.platform, j.platform)
        by_company[company].append(j)

    company_files = {}
    for company, company_jobs in sorted(by_company.items()):
        by_cat: dict[str, list[JobPosting]] = defaultdict(list)
        for j in company_jobs:
            by_cat[j.category].append(j)

        lines = [
            f"# {company} - AI 相关岗位",
            "",
            f"> 岗位数: {len(company_jobs)} | 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        for cat in CATEGORY_ORDER:
            cat_jobs = by_cat.get(cat, [])
            if not cat_jobs:
                continue
            lines.append(f"## {cat}（{len(cat_jobs)}）")
            lines.append("")
            for j in sorted(cat_jobs, key=lambda x: x.title):
                lines.extend(_render_job(j))

        filename = f"{company}.md"
        filepath = jobs_dir / filename
        filepath.write_text("\n".join(lines), encoding="utf-8")
        company_files[company] = filepath
        logger.info("  %s: %d jobs -> %s", company, len(company_jobs), filepath.name)

    return company_files


def generate_readme(jobs: list[JobPosting], output_path: str | Path) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    project_root = Path(output_path).parent
    jobs_dir = project_root / "jobs"

    company_files = generate_company_files(jobs, jobs_dir)

    by_category: dict[str, list[JobPosting]] = defaultdict(list)
    by_company: dict[str, list[JobPosting]] = defaultdict(list)
    by_location: dict[str, int] = defaultdict(int)

    for j in jobs:
        by_category[j.category].append(j)
        company = j.company or PLATFORM_NAMES.get(j.platform, j.platform)
        by_company[company].append(j)
        if j.location:
            primary_loc = j.location.split(",")[0].split("/")[0].strip()
            if primary_loc and len(primary_loc) < 10:
                by_location[primary_loc] += 1

    lines = [
        "# AI 岗位雷达",
        "",
        f"> 更新时间: {now} | 岗位总数: **{len(jobs)}**",
        "",
        "自动追踪大模型测试 / AI测试 / Agent评测 / 测试开发(AI方向) / AI产品 相关岗位。",
        "",
        "每个公司的岗位详情（含岗位职责和要求）在 `jobs/` 目录下单独存放。",
        "",
        "---",
        "",
        "## 目标方向",
        "",
        "| 方向 | 说明 | 岗位数 |",
        "| --- | --- | --- |",
    ]
    for cat in CATEGORY_ORDER:
        desc_map = {
            "大模型/AI测试": "大模型评测、算法测试、AI质量保障",
            "测试开发(AI方向)": "AI方向的测试开发、评测平台、自动化框架",
            "Agent评测": "Agent/大模型效果评测、Benchmark建设",
            "AI/Agent产品": "AI策略产品、Agent产品、AIGC产品",
        }
        count = len(by_category.get(cat, []))
        if count:
            lines.append(f"| {cat} | {desc_map.get(cat, '')} | {count} |")

    lines.extend([
        "",
        "## 各公司岗位",
        "",
    ])

    for company in sorted(by_company.keys()):
        cjobs = by_company[company]
        cat_counts = defaultdict(int)
        for j in cjobs:
            cat_counts[j.category] += 1
        cat_summary = " / ".join(f"{c} {n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]))
        rel_path = f"jobs/{company}.md"
        lines.append(f"### [{company}]({rel_path})（{len(cjobs)} 个岗位）")
        lines.append("")
        lines.append(f"_{cat_summary}_")
        lines.append("")

        lines.append("| 岗位 | 方向 | 城市 | 部门 |")
        lines.append("| --- | --- | --- | --- |")
        for j in sorted(cjobs, key=lambda x: (CATEGORY_ORDER.index(x.category) if x.category in CATEGORY_ORDER else 99, x.title)):
            title_display = f"[{j.title}]({j.url})" if j.url else j.title
            loc = j.location.split(",")[0].split("/")[0].strip() if j.location else ""
            lines.append(f"| {title_display} | {j.category} | {loc} | {j.department} |")
        lines.append("")

    lines.extend([
        "---",
        "",
        "### 按城市分布",
        "",
        "| 城市 | 岗位数 |",
        "| --- | --- |",
    ])
    for loc, count in sorted(by_location.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"| {loc} | {count} |")

    lines.extend([
        "",
        "---",
        "",
        f"*数据自动采集，更新于 {now}。仅供求职参考。*",
        "",
    ])

    output_path = Path(output_path)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("README generated: %s (%d jobs, %d companies)", output_path, len(jobs), len(company_files))
