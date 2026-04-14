"""Quick scrape - run all platforms with minimal keyword/city combos for fast first data."""
import json
import logging
import sys
from pathlib import Path
from datetime import datetime

from src.models import JobPosting, save_jobs_to_json
from src.pipeline.normalizer import normalize_jobs
from src.pipeline.dedup import deduplicate
from src.pipeline.filter import filter_by_keywords
from src.report import generate_readme

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("quick-scrape")

QUICK_CONFIG = {
    "keywords": [
        "大模型测试",
        "AI测试",
        "自动化测试开发",
        "Agent",
        "AIGC",
        "LLM",
    ],
    "cities": ["北京", "上海", "深圳"],
    "categories": {
        "product": {"name": "产品类", "keywords": ["产品经理", "策略产品", "AIGC产品", "Agent产品", "AI产品"]},
        "test": {"name": "测试类", "keywords": ["测试", "质量保障", "QA", "评测", "质量"]},
        "agent": {"name": "Agent类", "keywords": ["Agent开发", "Agent工程师", "LLM应用", "Agent"]},
        "dev": {"name": "开发类", "keywords": ["测试开发", "自动化", "工具开发", "平台开发"]},
    },
}

# Use a smaller subset: 3 core keywords x 2 cities per platform for speed
FAST_CONFIG = {
    **QUICK_CONFIG,
    "keywords": ["AI测试", "Agent", "LLM"],
    "cities": ["北京", "上海"],
}


def run_scraper(scraper_cls, config, label):
    logger.info("=== %s ===", label)
    scraper = scraper_cls(config)
    try:
        jobs = scraper.scrape()
        logger.info("[%s] got %d jobs", label, len(jobs))
        return jobs
    except Exception:
        logger.error("[%s] failed", label, exc_info=True)
        return []
    finally:
        scraper.close()


def main():
    all_jobs: list[JobPosting] = []

    # Tier 1 - use quick config (more keywords)
    from src.scrapers.tencent import TencentScraper
    from src.scrapers.alibaba import AlibabaScraper

    all_jobs += run_scraper(TencentScraper, QUICK_CONFIG, "腾讯")
    all_jobs += run_scraper(AlibabaScraper, QUICK_CONFIG, "阿里")

    # Tier 1 - bytedance/baidu use fast config (fewer combos, SSR parsing is slower)
    from src.scrapers.bytedance import BytedanceScraper
    from src.scrapers.baidu import BaiduScraper

    all_jobs += run_scraper(BytedanceScraper, FAST_CONFIG, "字节")
    all_jobs += run_scraper(BaiduScraper, FAST_CONFIG, "百度")

    # Tier 2 - use fast config
    from src.scrapers.boss import BossScraper
    from src.scrapers.zhilian import ZhilianScraper
    from src.scrapers.liepin import LiepinScraper
    from src.scrapers.job51 import Job51Scraper
    from src.scrapers.lagou import LagouScraper

    all_jobs += run_scraper(BossScraper, FAST_CONFIG, "Boss直聘")
    all_jobs += run_scraper(ZhilianScraper, FAST_CONFIG, "智联")
    all_jobs += run_scraper(LiepinScraper, FAST_CONFIG, "猎聘")
    all_jobs += run_scraper(Job51Scraper, FAST_CONFIG, "前程无忧")
    all_jobs += run_scraper(LagouScraper, FAST_CONFIG, "拉勾")

    logger.info("Total raw: %d", len(all_jobs))

    # Process
    categories = QUICK_CONFIG["categories"]
    keywords = QUICK_CONFIG["keywords"]

    processed = normalize_jobs(all_jobs, categories)
    processed = deduplicate(processed)
    processed = filter_by_keywords(processed, keywords)
    logger.info("After processing: %d jobs", len(processed))

    # Save
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    (data_dir / "daily").mkdir(exist_ok=True)

    save_jobs_to_json(processed, str(data_dir / "jobs.json"))
    today = datetime.now().strftime("%Y-%m-%d")
    save_jobs_to_json(processed, str(data_dir / "daily" / f"{today}.json"))

    # Generate README
    generate_readme(processed, Path("README.md"))

    logger.info("Done! %d jobs saved.", len(processed))


if __name__ == "__main__":
    main()
