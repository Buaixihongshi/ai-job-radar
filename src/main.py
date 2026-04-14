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
import sys
from datetime import datetime
from pathlib import Path

from src.config import load_config, get_data_dir, get_feishu_webhook_url
from src.models import JobPosting, load_jobs_from_json, save_jobs_to_json
from src.pipeline.normalizer import normalize_jobs
from src.pipeline.dedup import deduplicate
from src.pipeline.diff import compute_diff
from src.pipeline.filter import filter_strict
from src.report import generate_readme
from src.notifiers.feishu import send_feishu_notification

# Tier 1: 公司官网
from src.scrapers.tencent import TencentScraper
from src.scrapers.alibaba import AlibabaScraper
from src.scrapers.bytedance import BytedanceScraper
from src.scrapers.baidu import BaiduScraper
from src.scrapers.netease import NeteaseScraper

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

SCRAPER_REGISTRY = {
    # Tier 1
    "tencent": TencentScraper,
    "alibaba": AlibabaScraper,
    "bytedance": BytedanceScraper,
    "baidu": BaiduScraper,
    "netease": NeteaseScraper,
    # Tier 2
    "boss": BossScraper,
    "liepin": LiepinScraper,
    "zhilian": ZhilianScraper,
    "job51": Job51Scraper,
    "lagou": LagouScraper,
    # Tier 3
    "linkedin": LinkedInScraper,
    "maimai": MaimaiScraper,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Job Radar")
    parser.add_argument("--platform", type=str, help="Run single platform only")
    parser.add_argument("--tier", type=int, help="Run only scrapers of this tier (1/2/3)")
    parser.add_argument("--dry-run", action="store_true", help="Skip notification and report generation")
    parser.add_argument("--config", type=str, help="Path to config.yaml")
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
            and name in SCRAPER_REGISTRY
        ]
    else:
        platform_names = [
            name for name, cfg in platforms_cfg.items()
            if cfg.get("enabled", False) and name in SCRAPER_REGISTRY
        ]

    # Sort by tier: Tier 1 first (most stable)
    platform_names.sort(key=lambda n: platforms_cfg.get(n, {}).get("tier", 99))
    logger.info("Platforms to scrape: %s", platform_names)

    # --- Phase 1: Scrape ---
    all_raw_jobs: list[JobPosting] = []
    for pname in platform_names:
        scraper_cls = SCRAPER_REGISTRY.get(pname)
        if not scraper_cls:
            logger.warning("No scraper for platform: %s", pname)
            continue

        tier = platforms_cfg.get(pname, {}).get("tier", "?")
        logger.info("=== Scraping [Tier %s]: %s ===", tier, pname)
        scraper = scraper_cls(config)
        try:
            jobs = scraper.scrape()
            logger.info("[%s] raw jobs: %d", pname, len(jobs))
            all_raw_jobs.extend(jobs)
        except Exception:
            logger.error("[%s] scraper failed", pname, exc_info=True)
        finally:
            scraper.close()

    logger.info("Total raw jobs from all platforms: %d", len(all_raw_jobs))

    # --- Phase 2: Process ---
    categories = config.get("categories", {})

    processed = normalize_jobs(all_raw_jobs, categories)
    processed = deduplicate(processed)
    processed = filter_strict(processed)

    logger.info("After processing: %d jobs", len(processed))

    # --- Phase 3: Diff ---
    jobs_file = data_dir / "jobs.json"
    previous_jobs = load_jobs_from_json(str(jobs_file))

    if processed:
        diff = compute_diff(processed, previous_jobs)
    else:
        logger.warning("No jobs scraped. Keeping previous data unchanged.")
        diff = compute_diff(previous_jobs, previous_jobs)
        processed = previous_jobs

    logger.info("Diff result: %s", diff.summary())

    # --- Phase 4: Save ---
    save_jobs_to_json(processed, str(jobs_file))

    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    save_jobs_to_json(processed, str(daily_dir / f"{today}.json"))

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
        generate_readme(processed, project_root / "README.md")

    # --- Phase 6: Notify ---
    if not args.dry_run:
        webhook_url = get_feishu_webhook_url()
        if webhook_url and diff.has_changes:
            send_feishu_notification(webhook_url, diff, total_active=len(processed))
        elif not webhook_url:
            logger.info("FEISHU_WEBHOOK_URL not set, skipping notification")

    logger.info("Done. %d active jobs, %s", len(processed), diff.summary())


if __name__ == "__main__":
    main()
