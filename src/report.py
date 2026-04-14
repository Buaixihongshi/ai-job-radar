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
    "job51": "前程无忧",
    "lagou": "拉勾",
    "linkedin": "LinkedIn",
    "maimai": "脉脉",
}

CATEGORY_ORDER = ["测试", "测试开发", "Agent评测", "Agent产品"]


def _truncate(text: str, max_len: int = 500) -> str:
    if not text:
        return "暂无"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def _clean_for_md(text: str) -> str:
    """Escape and clean text for markdown display."""
    if not text:
        return "暂无"
    text = text.replace("|", "\\|")
    text = text.replace("\n\n", "<br>").replace("\n", "<br>")
    return text


def generate_readme(jobs: list[JobPosting], output_path: str | Path) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    by_category: dict[str, list[JobPosting]] = defaultdict(list)
    by_platform: dict[str, int] = defaultdict(int)
    by_location: dict[str, int] = defaultdict(int)

    for j in jobs:
        by_category[j.category].append(j)
        by_platform[j.platform] += 1
        if j.location:
            primary_loc = j.location.split(",")[0].strip()
            if primary_loc:
                by_location[primary_loc] += 1

    lines = [
        "# AI 岗位雷达",
        "",
        f"> 自动更新时间: {now} | 活跃岗位总数: **{len(jobs)}**",
        "",
        "本仓库自动追踪以下四类 AI 相关岗位，数据来源于各大互联网公司招聘官网，每日自动更新。",
        "",
        "**目标岗位类型：** 测试 | 测试开发 | Agent评测 | Agent产品",
        "",
        "## 数据概览",
        "",
        "### 按来源",
        "",
        "| 来源 | 岗位数 |",
        "| --- | --- |",
    ]

    for platform, count in sorted(by_platform.items(), key=lambda x: -x[1]):
        name = PLATFORM_NAMES.get(platform, platform)
        lines.append(f"| {name} | {count} |")

    lines.extend([
        "",
        "### 按城市",
        "",
        "| 城市 | 岗位数 |",
        "| --- | --- |",
    ])

    for loc, count in sorted(by_location.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"| {loc} | {count} |")

    lines.extend([
        "",
        "### 按类型",
        "",
        "| 类型 | 岗位数 |",
        "| --- | --- |",
    ])

    for cat in CATEGORY_ORDER:
        cat_jobs = by_category.get(cat, [])
        if cat_jobs:
            lines.append(f"| {cat} | {len(cat_jobs)} |")

    lines.extend(["", "---", ""])

    for cat in CATEGORY_ORDER:
        cat_jobs = by_category.get(cat, [])
        if not cat_jobs:
            continue

        lines.extend([
            f"## {cat}（{len(cat_jobs)} 个岗位）",
            "",
        ])

        by_company: dict[str, list[JobPosting]] = defaultdict(list)
        for j in cat_jobs:
            display_company = j.company or PLATFORM_NAMES.get(j.platform, j.platform)
            by_company[display_company].append(j)

        for company in sorted(by_company.keys()):
            company_jobs = by_company[company]
            lines.append(f"### {company}（{len(company_jobs)}）")
            lines.append("")

            for j in sorted(company_jobs, key=lambda x: x.publish_date or "", reverse=True):
                title_display = f"[{j.title}]({j.url})" if j.url else j.title
                meta_parts = []
                if j.location:
                    meta_parts.append(f"📍 {j.location}")
                if j.department:
                    meta_parts.append(f"🏢 {j.department}")
                if j.salary:
                    meta_parts.append(f"💰 {j.salary}")
                if j.experience:
                    meta_parts.append(f"📅 {j.experience}")
                if j.education:
                    meta_parts.append(f"🎓 {j.education}")

                meta_str = " | ".join(meta_parts) if meta_parts else ""
                source = PLATFORM_NAMES.get(j.platform, j.platform)

                lines.append(f"#### {j.title}")
                lines.append("")
                if j.url:
                    lines.append(f"🔗 [投递链接]({j.url}) &nbsp;&nbsp; 来源: {source}")
                else:
                    lines.append(f"来源: {source}")
                if meta_str:
                    lines.append(f"")
                    lines.append(meta_str)
                lines.append("")

                desc = _truncate(j.description)
                req = _truncate(j.requirements)

                if desc and desc != "暂无":
                    lines.append("**岗位职责：**")
                    lines.append("")
                    for line in desc.split("\n"):
                        line = line.strip()
                        if line:
                            lines.append(f"{line}")
                            lines.append("")

                if req and req != "暂无":
                    lines.append("**岗位要求：**")
                    lines.append("")
                    for line in req.split("\n"):
                        line = line.strip()
                        if line:
                            lines.append(f"{line}")
                            lines.append("")

                if desc == "暂无" and req == "暂无":
                    lines.append("*详情请点击投递链接查看*")
                    lines.append("")

                lines.append("---")
                lines.append("")

    lines.extend([
        "",
        f"*数据自动采集，更新于 {now}。仅供求职参考，以各公司官网为准。*",
        "",
    ])

    output_path = Path(output_path)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("README generated: %s (%d jobs)", output_path, len(jobs))
