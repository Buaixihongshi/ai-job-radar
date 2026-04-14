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
    "boss": "Boss直聘",
    "liepin": "猎聘",
    "zhilian": "智联招聘",
    "job51": "前程无忧",
    "lagou": "拉勾",
    "linkedin": "LinkedIn",
    "maimai": "脉脉",
    "meituan": "美团",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "netease": "网易",
    "huawei": "华为",
}

CATEGORY_NAMES = {
    "product": "产品类",
    "test": "测试类",
    "agent": "Agent类",
    "dev": "开发类",
    "other": "其他",
}


def generate_readme(jobs: list[JobPosting], output_path: str | Path) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    by_category: dict[str, list[JobPosting]] = defaultdict(list)
    by_platform: dict[str, int] = defaultdict(int)
    by_location: dict[str, int] = defaultdict(int)

    for j in jobs:
        cat = j.category if j.category in CATEGORY_NAMES else "other"
        by_category[cat].append(j)
        by_platform[j.platform] += 1
        if j.location:
            primary_loc = j.location.split(",")[0].strip()
            by_location[primary_loc] += 1

    lines = [
        "# AI 岗位雷达",
        "",
        f"> 自动更新时间: {now} | 活跃岗位总数: **{len(jobs)}**",
        "",
        "本仓库自动追踪 AI 相关岗位（大模型测试、自动化测试开发、Agent 产品等），",
        "数据来源于各大互联网公司招聘官网及主流招聘平台，每日自动更新。",
        "",
        "## 数据来源",
        "",
        "| 层级 | 来源 | 方式 |",
        "| --- | --- | --- |",
        "| Tier 1 | 腾讯、百度、网易 | 公司官网 API |",
        "| Tier 1 | 字节跳动 | 公司官网 + Playwright |",
        "",
        "---",
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

    lines.extend(["", "---", ""])

    category_order = ["test", "agent", "product", "dev", "other"]
    for cat in category_order:
        cat_jobs = by_category.get(cat, [])
        if not cat_jobs:
            continue
        cat_name = CATEGORY_NAMES.get(cat, cat)
        lines.extend([
            f"## {cat_name}（{len(cat_jobs)} 个岗位）",
            "",
        ])

        by_company: dict[str, list[JobPosting]] = defaultdict(list)
        for j in cat_jobs:
            display_company = j.company or PLATFORM_NAMES.get(j.platform, j.platform)
            by_company[display_company].append(j)

        for company in sorted(by_company.keys()):
            company_jobs = by_company[company]
            lines.append(f"### {company}")
            lines.append("")
            lines.append("| 岗位 | 部门 | 城市 | 薪资 | 经验 | 来源 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")

            for j in sorted(company_jobs, key=lambda x: x.publish_date or "", reverse=True):
                title = f"[{j.title}]({j.url})" if j.url else j.title
                source = PLATFORM_NAMES.get(j.platform, j.platform)
                lines.append(
                    f"| {title} | {j.department} | {j.location} | "
                    f"{j.salary} | {j.experience} | {source} |"
                )
            lines.append("")

    lines.extend([
        "---",
        "",
        f"*数据自动采集，更新于 {now}。仅供求职参考，以各公司官网为准。*",
        "",
    ])

    output_path = Path(output_path)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("README generated: %s (%d jobs)", output_path, len(jobs))
