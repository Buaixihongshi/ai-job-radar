"""Generate README index + per-company job detail files."""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from src.models import JobPosting

logger = logging.getLogger(__name__)

PLATFORM_NAMES = {
    "tencent": "腾讯",
    "quark": "阿里巴巴(千问/夸克)",
    "alibaba": "阿里巴巴",
    "antgroup": "蚂蚁集团",
    "bytedance": "字节跳动",
    "baidu": "百度",
    "netease": "网易",
    "meituan": "美团",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "jd": "京东",
    "didi": "滴滴",
    "huawei": "华为",
    "boss": "Boss直聘",
    "liepin": "猎聘",
    "zhilian": "智联招聘",
    "job51": "前程无忧",
    "lagou": "拉勾",
    "linkedin": "LinkedIn",
    "maimai": "脉脉",
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


DISPLAY_NAME = {
    "tencent": "腾讯",
    "quark": "阿里巴巴",
    "alibaba": "阿里巴巴(集团主站)",
    "antgroup": "蚂蚁集团",
    "bytedance": "字节跳动",
    "baidu": "百度",
    "netease": "网易",
    "meituan": "美团",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "jd": "京东",
    "didi": "滴滴",
    "huawei": "华为",
    "boss": "Boss直聘",
    "liepin": "猎聘",
    "zhilian": "智联招聘",
    "job51": "前程无忧",
    "lagou": "拉勾",
    "linkedin": "LinkedIn",
    "maimai": "脉脉",
}


def _company_display_name(jobs: list[JobPosting], platform_key: str) -> str:
    """Resolve the display name used in jobs/ files for a given platform."""
    for j in jobs:
        if j.platform == platform_key and j.company:
            return j.company
    return PLATFORM_NAMES.get(platform_key, DISPLAY_NAME.get(platform_key, platform_key))


def _generate_overview_section(
    jobs: list[JobPosting],
    platforms_cfg: dict,
) -> list[str]:
    """Generate filter criteria + data source coverage sections."""
    job_counts: Counter[str] = Counter()
    for j in jobs:
        job_counts[j.platform] += 1

    active: list[tuple[str, str, str, int]] = []    # (key, display, file_name, count)
    debugging: list[tuple[str, str, int]] = []
    planned: list[tuple[str, str]] = []

    for key, cfg in platforms_cfg.items():
        name = DISPLAY_NAME.get(key, cfg.get("name", key))
        enabled = cfg.get("enabled", False)
        count = job_counts.get(key, 0)
        if enabled and count > 0:
            file_name = _company_display_name(jobs, key)
            active.append((key, name, file_name, count))
        elif enabled and count == 0:
            debugging.append((key, name, count))
        else:
            planned.append((key, name))

    active.sort(key=lambda x: -x[3])
    debugging.sort(key=lambda x: x[1])
    planned.sort(key=lambda x: x[1])

    lines: list[str] = []

    # -- 筛选条件 --
    lines.extend([
        "## 筛选条件",
        "",
        "本项目仅追踪符合以下条件的岗位：",
        "",
        "- **招聘类型**: 仅社招，排除校招 / 实习 / 应届",
        "- **学历要求**: 本科及以下，排除硕士 / 博士硬性要求",
        "- **经验要求**: 3 年及以下，排除「五年以上」等高年限要求",
        "- **岗位方向**: 必须与 AI / 大模型 / Agent 相关（标题或描述中包含关键词）",
        "- **排除方向**: 硬件 / 嵌入式 / 芯片 / 安全攻防 / 游戏纯开发 / 运营 / 销售 / 行政等非目标方向",
        "",
    ])

    # -- 数据源覆盖 --
    lines.extend([
        "## 数据源覆盖",
        "",
        "| 公司 | 状态 | 岗位数 |",
        "| --- | --- | --- |",
    ])
    for _key, name, file_name, count in active:
        link = f"[{name}](jobs/{file_name}.md)"
        lines.append(f"| {link} | ✅ 已接入 | {count} |")
    for _key, name, count in debugging:
        lines.append(f"| {name} | 🔧 调试中 | {count} |")
    for _key, name in planned:
        lines.append(f"| {name} | 📋 计划中 | - |")
    lines.append("")

    # -- 分区列表 --
    if active:
        names = "、".join(f"[{n}](jobs/{fn}.md)" for _, n, fn, _ in active)
        lines.append(f"**✅ 已接入（{len(active)} 家）**：{names}")
        lines.append("")
    if debugging:
        names = "、".join(n for _, n, _ in debugging)
        lines.append(f"**🔧 调试中（{len(debugging)} 家）**：{names}（爬虫已编写，数据接入调试中）")
        lines.append("")
    if planned:
        names = "、".join(n for _, n in planned)
        lines.append(f"**📋 计划中（{len(planned)} 家）**：{names}")
        lines.append("")

    return lines


def _generate_trend_section(db_path: Path) -> list[str]:
    """Generate a trend analysis section from SQLite history.

    Returns an empty list if the DB doesn't exist or has insufficient history
    (< 2 distinct dates), so the README degrades gracefully on first run.
    """
    if not db_path.exists():
        return []
    try:
        from src.analytics.trend import TrendAnalyzer
        analyzer = TrendAnalyzer(db_path)
        lifetimes = analyzer.job_lifetimes()
        if not lifetimes:
            return []

        # Need at least 2 different first_seen dates to show meaningful trends
        dates = {j["first_seen"] for j in lifetimes}
        if len(dates) < 2:
            return []

        lines: list[str] = ["## 📈 招聘趋势分析", ""]

        # ── 1. 持续在招
        persistent = analyzer.long_lived_jobs(min_days=14)[:10]
        if persistent:
            lines += [
                "### 持续在招岗位（≥14天，竞争相对较低）",
                "",
                "| 岗位 | 公司 | 已上线天数 | 首次发现 |",
                "| --- | --- | --- | --- |",
            ]
            for j in persistent:
                lines.append(
                    f"| {j['title']} | {j['company']} | {j['active_days']}天 | {j['first_seen']} |"
                )
            lines.append("")

        # ── 2. 快速下架
        quick = analyzer.quick_filled_jobs(max_days=7)[:8]
        if quick:
            lines += [
                "### 快速下架岗位（≤7天已消失，竞争激烈）",
                "",
                "| 岗位 | 公司 | 在线天数 | 下架时间 |",
                "| --- | --- | --- | --- |",
            ]
            for j in quick:
                lines.append(
                    f"| {j['title']} | {j['company']} | {j['active_days']}天 | {j['last_seen']} |"
                )
            lines.append("")

        # ── 3. 高频技能词
        skills = analyzer.skill_frequency(days=30, active_only=True)[:15]
        if skills:
            lines += [
                "### JD 高频技能词（近30天在招岗位）",
                "",
                "| 技能 | 出现次数 |",
                "| --- | --- |",
            ]
            for skill, cnt in skills:
                lines.append(f"| {skill} | {cnt} |")
            lines.append("")

        lines += ["---", ""]
        return lines

    except Exception:
        logger.debug("Trend section generation failed (non-fatal)", exc_info=True)
        return []


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


def generate_readme(
    jobs: list[JobPosting],
    output_path: str | Path,
    config: dict | None = None,
    db_path: Path | None = None,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    output_path = Path(output_path)
    project_root = output_path.parent
    jobs_dir = project_root / "jobs"

    if db_path is None:
        db_path = project_root / "data" / "jobs.db"

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

    lines.append("")

    # -- 信息总览：筛选条件 + 数据源覆盖 --
    platforms_cfg = (config or {}).get("platforms", {})
    if platforms_cfg:
        lines.extend(_generate_overview_section(jobs, platforms_cfg))
        lines.append("---")
        lines.append("")

    # -- 趋势分析（从 SQLite 历史数据生成，初次运行自动跳过）--
    trend_lines = _generate_trend_section(db_path)
    if trend_lines:
        lines.extend(trend_lines)

    lines.extend([
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

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("README generated: %s (%d jobs, %d companies)", output_path, len(jobs), len(company_files))
