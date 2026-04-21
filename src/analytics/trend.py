"""Longitudinal trend analysis for AI Job Radar.

Answers questions like:
- Which jobs have been active longest? (persistent demand / hard to fill)
- Which jobs appear and disappear quickly? (competitive, likely filled fast)
- Which companies are aggressively hiring right now?
- Which skills are most frequently required this month?

Usage
-----
    from src.analytics.trend import TrendAnalyzer
    analyzer = TrendAnalyzer()
    report = analyzer.generate_markdown_report()
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from src.db import DB_PATH, init_db

# ── Skill keyword extractor ──────────────────────────────────────────────────

SKILL_PATTERNS = re.compile(
    r"\b(Python|Pytest|pytest|Java|Go|Golang|C\+\+|TypeScript|JavaScript|"
    r"Playwright|Selenium|Appium|"
    r"LLM|大模型|GPT|AIGC|Agent|RAG|Prompt|NLP|多模态|"
    r"Benchmark|benchmark|badcase|评测框架|评测平台|"
    r"Docker|K8s|Kubernetes|CI/CD|Jenkins|"
    r"SQL|MySQL|PostgreSQL|MongoDB|Redis|"
    r"数据分析|数据标注|数据挖掘)\b",
    re.IGNORECASE,
)

CITY_PAT = re.compile(r"(北京|上海|杭州|深圳|广州|成都|武汉|南京|西安|重庆)")


class TrendAnalyzer:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        init_db(db_path)

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    # ── Core metric helpers ──────────────────────────────────────────────────

    def job_lifetimes(self) -> list[dict]:
        """Return each job with computed active_days and is_active flag."""
        today = datetime.now().date()
        with self._conn() as con:
            rows = con.execute(
                "SELECT unique_key, title, company, platform, category,"
                " first_seen, last_seen, is_active FROM jobs"
            ).fetchall()

        result = []
        for r in rows:
            first = datetime.strptime(r["first_seen"], "%Y-%m-%d").date()
            last  = datetime.strptime(r["last_seen"],  "%Y-%m-%d").date()
            active_days = (last - first).days + 1
            result.append({
                "unique_key":  r["unique_key"],
                "title":       r["title"],
                "company":     r["company"] or r["platform"],
                "platform":    r["platform"],
                "category":    r["category"],
                "first_seen":  r["first_seen"],
                "last_seen":   r["last_seen"],
                "is_active":   bool(r["is_active"]),
                "active_days": active_days,
            })
        return result

    def long_lived_jobs(self, min_days: int = 21) -> list[dict]:
        """Jobs still active after min_days — persistent demand or hard to fill."""
        return sorted(
            [j for j in self.job_lifetimes() if j["is_active"] and j["active_days"] >= min_days],
            key=lambda j: j["active_days"],
            reverse=True,
        )

    def quick_filled_jobs(self, max_days: int = 7) -> list[dict]:
        """Jobs removed within max_days — likely filled fast (high competition)."""
        return sorted(
            [j for j in self.job_lifetimes() if not j["is_active"] and j["active_days"] <= max_days],
            key=lambda j: j["first_seen"],
            reverse=True,
        )[:30]

    def company_activity(self, days: int = 30) -> list[tuple[str, int]]:
        """Count new job postings per company in the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as con:
            rows = con.execute(
                "SELECT company, platform, COUNT(*) as cnt FROM jobs"
                " WHERE first_seen >= ? GROUP BY coalesce(nullif(company,''), platform)"
                " ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
        return [(r["company"] or r["platform"], r["cnt"]) for r in rows]

    def skill_frequency(self, days: int = 30, active_only: bool = True) -> list[tuple[str, int]]:
        """Extract and count skill keywords from recent job descriptions."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        active_clause = "AND is_active=1" if active_only else ""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT description, requirements FROM jobs"
                f" WHERE last_seen >= ? {active_clause}",
                (cutoff,),
            ).fetchall()

        counter: Counter = Counter()
        for r in rows:
            text = f"{r['description']} {r['requirements']}"
            for match in SKILL_PATTERNS.findall(text):
                counter[match.lower()] += 1
        return counter.most_common(25)

    def category_trend(self, days: int = 60) -> dict[str, list[tuple[str, int]]]:
        """Weekly new job count per category for the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as con:
            rows = con.execute(
                "SELECT category, first_seen FROM jobs WHERE first_seen >= ?",
                (cutoff,),
            ).fetchall()

        weekly: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            week = datetime.strptime(r["first_seen"], "%Y-%m-%d").strftime("%Y-W%W")
            weekly[r["category"]][week] += 1

        return {cat: sorted(weeks.items()) for cat, weeks in weekly.items()}

    # ── Report generator ────────────────────────────────────────────────────

    def generate_markdown_report(self) -> str:
        lines: list[str] = []
        today_str = datetime.now().strftime("%Y-%m-%d")

        lines += [f"# AI 招聘趋势报告 ({today_str})\n"]

        # ── 1. 持续在招（竞争度低 / 扩招信号）
        persistent = self.long_lived_jobs(min_days=21)[:15]
        lines += [
            "## 📌 持续在招岗位（≥21天未下架）\n",
            "> 这些岗位长期存在，可能是持续扩招或较难招到合适人选，投递成功率相对较高。\n",
            "| 岗位 | 公司 | 分类 | 已上线天数 | 首次发现 |",
            "|------|------|------|-----------|---------|",
        ]
        for j in persistent:
            lines.append(
                f"| {j['title']} | {j['company']} | {j['category']} "
                f"| {j['active_days']}天 | {j['first_seen']} |"
            )

        lines.append("")

        # ── 2. 快速下架（竞争激烈）
        quick = self.quick_filled_jobs(max_days=7)[:10]
        lines += [
            "## ⚡ 快速下架岗位（≤7天已消失）\n",
            "> 这些岗位出现后迅速消失，说明竞争激烈已被快速填充，投递需要更快行动。\n",
            "| 岗位 | 公司 | 在线天数 | 首次发现 |",
            "|------|------|---------|---------|",
        ]
        for j in quick:
            lines.append(
                f"| {j['title']} | {j['company']} | {j['active_days']}天 | {j['first_seen']} |"
            )

        lines.append("")

        # ── 3. 近30天公司招聘热度
        activity = self.company_activity(days=30)[:10]
        lines += [
            "## 🔥 近30天公司招聘热度\n",
            "| 公司 | 新增岗位数 |",
            "|------|-----------|",
        ]
        for company, cnt in activity:
            lines.append(f"| {company} | {cnt} |")

        lines.append("")

        # ── 4. 高频技能词
        skills = self.skill_frequency(days=30)[:20]
        lines += [
            "## 🛠️ 近30天 JD 高频技能词\n",
            "> 出现次数越高，简历/面试中越需要体现。\n",
            "| 技能 | 出现次数 |",
            "|------|---------|",
        ]
        for skill, cnt in skills:
            lines.append(f"| {skill} | {cnt} |")

        lines.append("")

        # ── 5. 分类趋势摘要
        cat_trend = self.category_trend(days=60)
        lines += ["## 📈 分类招聘趋势（近60天）\n"]
        for cat, weekly in sorted(cat_trend.items()):
            total = sum(c for _, c in weekly)
            recent = sum(c for w, c in weekly if w >= (datetime.now() - timedelta(days=30)).strftime("%Y-W%W"))
            trend_arrow = "📈" if recent > total / 2 else ("📉" if recent < total / 4 else "➡️")
            lines.append(f"- **{cat}**: 共 {total} 个岗位，近30天 {recent} 个 {trend_arrow}")

        lines.append("\n---\n*由 ai-job-radar 自动生成*")
        return "\n".join(lines)

    def print_summary(self) -> None:
        """Print a compact summary to stdout (used in CI health report)."""
        lifetimes = self.job_lifetimes()
        active = [j for j in lifetimes if j["is_active"]]
        removed = [j for j in lifetimes if not j["is_active"]]

        print(f"\n{'='*50}")
        print(f"TREND SUMMARY")
        print(f"  Active jobs:              {len(active)}")
        print(f"  Historical (removed):     {len(removed)}")
        if active:
            avg_days = sum(j["active_days"] for j in active) / len(active)
            print(f"  Avg active days:          {avg_days:.1f}")
        persistent = self.long_lived_jobs(min_days=21)
        print(f"  Persistent (≥21d):        {len(persistent)}")
        quick = self.quick_filled_jobs(max_days=7)
        print(f"  Quick-filled (≤7d):       {len(quick)}")
        print(f"{'='*50}\n")
