"""AI Job Radar - main entry point.

Usage:
    python -m src.main              # run all enabled scrapers
    python -m src.main --platform tencent  # run single platform
    python -m src.main --dry-run    # scrape only, don't commit/notify
    python -m src.main --tier 1     # run only Tier 1 scrapers
"""
from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import load_config, get_data_dir, get_feishu_webhook_url
from src.db import upsert_jobs, log_scrape_run
from src.models import JobPosting, load_jobs_from_json, save_jobs_to_json
from src.pipeline.normalizer import normalize_jobs
from src.pipeline.dedup import deduplicate
from src.pipeline.detail_fetcher import enrich_with_details
from src.pipeline.diff import compute_diff
from src.pipeline.filter import filter_strict
from src.report import generate_readme
from src.notifiers.feishu import send_feishu_notification

# Tier 1: 公司官网 API
from src.scrapers.tencent import TencentScraper
from src.scrapers.baidu import BaiduScraper
from src.scrapers.netease import NeteaseScraper

# Tier 1: 公司官网 Playwright (legacy, kept for fallback)
from src.scrapers.bytedance import BytedanceScraper

# Tier 2: 第三方招聘平台
from src.scrapers.boss import BossScraper
from src.scrapers.liepin import LiepinScraper
from src.scrapers.zhilian import ZhilianScraper
from src.scrapers.job51 import Job51Scraper
from src.scrapers.lagou import LagouScraper

# Tier 3: 浏览器自动化
from src.scrapers.linkedin import LinkedInScraper
from src.scrapers.maimai import MaimaiScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ai-job-radar")


# ── Per-platform health tracking ─────────────────────────────────────────────

@dataclass
class PlatformResult:
    platform: str
    status: str = "pending"       # pending / success / timeout / error
    raw_count: int = 0
    filtered_count: int = 0
    duration: float = 0.0
    error_msg: str = ""


def _print_health_report(results: list[PlatformResult]) -> None:
    """Print a structured health report to stdout; always shown in CI logs."""
    total_raw = sum(r.raw_count for r in results)
    total_filtered = sum(r.filtered_count for r in results)
    ok = [r for r in results if r.status == "success"]
    fail = [r for r in results if r.status in ("timeout", "error")]

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  SCRAPER HEALTH REPORT")
    print(f"  Platforms: {len(results)}  OK: {len(ok)}  FAILED: {len(fail)}")
    print(f"  Raw jobs: {total_raw}   Filtered jobs: {total_filtered}")
    print(sep)
    for r in sorted(results, key=lambda x: x.status):
        icon = "✓" if r.status == "success" else ("⏱" if r.status == "timeout" else "✗")
        line = (
            f"  {icon} {r.platform:<20} "
            f"raw={r.raw_count:>4}  filtered={r.filtered_count:>4}  "
            f"time={r.duration:>6.1f}s  [{r.status}]"
        )
        if r.error_msg:
            line += f"\n       └─ {r.error_msg[:120]}"
        print(line)
    print(sep + "\n")

SCRAPER_REGISTRY = {
    # Tier 1: API
    "tencent": TencentScraper,
    "baidu": BaiduScraper,
    "netease": NeteaseScraper,
    # Tier 1: Playwright (legacy fallback)
    # "bytedance": BytedanceScraper,
    # Tier 2 (browser-first for anti-bot)
    "boss": BossScraper,
    "liepin": LiepinScraper,
    "zhilian": ZhilianScraper,
    "job51": Job51Scraper,
    "lagou": LagouScraper,
    # Tier 3
    "linkedin": LinkedInScraper,
    "maimai": MaimaiScraper,
}

# Standalone scrapers (function-based, not class-based)
STANDALONE_SCRAPERS = {
    "quark": ("src.scrapers.quark", "scrape_quark"),
    "alibaba": ("src.scrapers.alibaba", "scrape_alibaba"),
    "antgroup": ("src.scrapers.antgroup", "scrape_antgroup"),
    "meituan": ("src.scrapers.meituan", "scrape_meituan"),
    "kuaishou": ("src.scrapers.kuaishou", "scrape_kuaishou"),
    "xiaohongshu": ("src.scrapers.xiaohongshu", "scrape_xiaohongshu"),
    "jd": ("src.scrapers.jd", "scrape_jd"),
    "huawei": ("src.scrapers.huawei", "scrape_huawei"),
    # Feishu Jobs (飞书招聘) - MiniMax, 智谱AI
    "feishu": ("src.scrapers.feishu", "scrape_feishu"),
    # MokaHR - DeepSeek, Kimi
    "moka": ("src.scrapers.moka", "scrape_moka"),
    # bb-browser powered (requires local Chrome + daemon)
    "bytedance": ("src.scrapers.bytedance_bb", "scrape_bytedance"),
    "didi": ("src.scrapers.didi_bb", "scrape_didi_bb"),
}

CITIES = ["北京", "上海", "杭州", "深圳", "广州", "成都", "武汉", "南京"]


def _fix_bytedance_data(jobs: list[JobPosting]) -> None:
    city_pat = re.compile(r"(" + "|".join(CITIES) + r")")
    for j in jobs:
        if j.platform != "bytedance":
            continue
        dept = j.department or ""
        if "职位 ID" in dept or "职位ID" in dept:
            m = city_pat.search(dept)
            if m:
                j.location = m.group(1)
            clean = re.sub(r"^(北京|上海|杭州|深圳|广州|成都|武汉)(正式|实习)?", "", dept)
            clean = re.sub(r"职位\s*ID[：:]\w+", "", clean).strip()
            j.department = clean
        if j.location and len(j.location) > 15:
            m2 = city_pat.search(j.location)
            j.location = m2.group(1) if m2 else ""
        if j.department and len(j.department) > 50:
            j.department = ""


def _fix_baidu_titles(jobs: list[JobPosting]) -> None:
    for j in jobs:
        if j.platform == "baidu" and j.title.startswith("script>"):
            m = re.search(r'"name":"([^"]+)"', j.title)
            j.title = m.group(1) if m else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Job Radar")
    parser.add_argument("--platform", type=str, help="Run single platform only")
    parser.add_argument("--tier", type=int, help="Run only scrapers of this tier (1/2/3)")
    parser.add_argument("--dry-run", action="store_true", help="Skip notification and report generation")
    parser.add_argument("--config", type=str, help="Path to config.yaml")
    parser.add_argument("--enrich-details", action="store_true", help="Fetch full JD from detail pages after filtering")
    args = parser.parse_args()

    config = load_config(args.config)
    data_dir = get_data_dir()
    today = datetime.now().strftime("%Y-%m-%d")

    platforms_cfg = config.get("platforms", {})

    if args.platform:
        platform_names = [args.platform]
    elif args.tier:
        platform_names = [
            name for name, cfg in platforms_cfg.items()
            if cfg.get("enabled", False)
            and cfg.get("tier") == args.tier
            and (name in SCRAPER_REGISTRY or name in STANDALONE_SCRAPERS)
        ]
    else:
        platform_names = [
            name for name, cfg in platforms_cfg.items()
            if cfg.get("enabled", False)
            and (name in SCRAPER_REGISTRY or name in STANDALONE_SCRAPERS)
        ]

    platform_names.sort(key=lambda n: platforms_cfg.get(n, {}).get("tier", 99))
    logger.info("Platforms to scrape: %s", platform_names)

    PLATFORM_TIMEOUT = 300  # 5 minutes max per platform

    def _scrape_one(pname: str) -> list[JobPosting]:
        tier = platforms_cfg.get(pname, {}).get("tier", "?")
        if pname in STANDALONE_SCRAPERS:
            mod_path, func_name = STANDALONE_SCRAPERS[pname]
            logger.info("=== Scraping [Tier %s]: %s (standalone) ===", tier, pname)
            import importlib
            mod = importlib.import_module(mod_path)
            fn = getattr(mod, func_name)
            return fn()

        scraper_cls = SCRAPER_REGISTRY.get(pname)
        if not scraper_cls:
            logger.warning("No scraper for platform: %s", pname)
            return []

        logger.info("=== Scraping [Tier %s]: %s ===", tier, pname)
        scraper = scraper_cls(config)
        try:
            return scraper.scrape()
        finally:
            scraper.close()

    # --- Phase 1: Scrape (with per-platform timeout) ---
    all_raw_jobs: list[JobPosting] = []
    health_results: list[PlatformResult] = []

    for pname in platform_names:
        pr = PlatformResult(platform=pname)
        health_results.append(pr)
        t0 = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_scrape_one, pname)
                jobs = future.result(timeout=PLATFORM_TIMEOUT)
            pr.raw_count = len(jobs)
            pr.duration = time.monotonic() - t0
            pr.status = "success"
            logger.info("[%s] raw jobs: %d", pname, len(jobs))
            all_raw_jobs.extend(jobs)
        except TimeoutError:
            pr.status = "timeout"
            pr.duration = time.monotonic() - t0
            pr.error_msg = f"exceeded {PLATFORM_TIMEOUT}s"
            logger.warning("[%s] TIMEOUT after %ds, skipping", pname, PLATFORM_TIMEOUT)
        except Exception as exc:
            pr.status = "error"
            pr.duration = time.monotonic() - t0
            pr.error_msg = str(exc)[:200]
            logger.error("[%s] scraper failed", pname, exc_info=True)

    logger.info("Total raw jobs from all platforms: %d", len(all_raw_jobs))

    _fix_bytedance_data(all_raw_jobs)
    _fix_baidu_titles(all_raw_jobs)

    # --- Phase 2: Process ---
    categories = config.get("categories", {})

    processed = normalize_jobs(all_raw_jobs, categories)
    processed = deduplicate(processed)
    logger.info("After dedup: %d jobs", len(processed))

    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    save_jobs_to_json(processed, str(daily_dir / f"{today}_raw.json"))

    filtered = filter_strict(processed)
    logger.info("After filter: %d jobs", len(filtered))

    if args.enrich_details and filtered:
        logger.info("Enriching job details...")
        enrich_with_details(filtered, max_workers=3)

    cats = Counter(j.category for j in filtered)
    for c, n in cats.most_common():
        logger.info("  %s: %d", c, n)
    companies = Counter((j.company or j.platform) for j in filtered)
    for p, n in companies.most_common():
        logger.info("  %s: %d", p, n)

    # Update per-platform filtered counts for health report
    filtered_by_platform = Counter((j.company or j.platform) for j in filtered)
    for pr in health_results:
        pr.filtered_count = filtered_by_platform.get(
            platforms_cfg.get(pr.platform, {}).get("name", pr.platform), 0
        )

    # --- Phase 3: Diff ---
    jobs_file = data_dir / "jobs.json"
    previous_jobs = load_jobs_from_json(str(jobs_file))

    if filtered:
        diff = compute_diff(filtered, previous_jobs)
    else:
        logger.warning("No jobs scraped. Keeping previous data unchanged.")
        diff = compute_diff(previous_jobs, previous_jobs)
        filtered = previous_jobs

    logger.info("Diff result: %s", diff.summary())

    # --- Phase 4: Save ---
    save_jobs_to_json(filtered, str(jobs_file))
    save_jobs_to_json(filtered, str(daily_dir / f"{today}.json"))

    # SQLite dual-write
    try:
        db_counts = upsert_jobs(filtered)
        logger.info("SQLite upsert: %s", db_counts)
    except Exception:
        logger.warning("SQLite upsert failed (non-fatal)", exc_info=True)

    # Log per-platform health to DB
    for pr in health_results:
        try:
            log_scrape_run(
                platform=pr.platform,
                raw_count=pr.raw_count,
                filtered_count=pr.filtered_count,
                duration=pr.duration,
                status=pr.status,
                error_msg=pr.error_msg,
            )
        except Exception:
            pass

    if diff.removed_jobs:
        archive_dir = data_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        month = datetime.now().strftime("%Y-%m")
        archive_file = archive_dir / f"{month}.json"
        existing_archive = load_jobs_from_json(str(archive_file))
        existing_keys = {j.unique_key for j in existing_archive}
        new_archived = [j for j in diff.removed_jobs if j.unique_key not in existing_keys]
        save_jobs_to_json(existing_archive + new_archived, str(archive_file))
        logger.info("Archived %d removed jobs", len(new_archived))

    # --- Phase 5: Report ---
    if not args.dry_run:
        project_root = Path(__file__).parent.parent
        generate_readme(filtered, project_root / "README.md", config=config)

    # --- Phase 6: Notify ---
    if not args.dry_run:
        webhook_url = get_feishu_webhook_url()
        if webhook_url and diff.has_changes:
            send_feishu_notification(webhook_url, diff, total_active=len(filtered))
        elif not webhook_url:
            logger.info("FEISHU_WEBHOOK_URL not set, skipping notification")

    # --- Health Report (always printed for CI log visibility) ---
    _print_health_report(health_results)

    logger.info("Done. %d active jobs, %s", len(filtered), diff.summary())


if __name__ == "__main__":
    main()
