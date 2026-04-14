"""Final scrape: Tencent + NetEase (full JD) + Baidu + ByteDance, then strict filter."""
import logging, sys, json, re, time, random
from collections import Counter
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler("_final_log.txt", encoding="utf-8")])
logger = logging.getLogger("final")

from src.models import JobPosting, save_jobs_to_json
from src.pipeline.normalizer import normalize_jobs
from src.pipeline.dedup import deduplicate
from src.pipeline.filter import filter_strict
from src.report import generate_readme

CONFIG = {
    "keywords": [
        "大模型测试", "AI测试", "算法测试",
        "自动化测试开发", "测试开发工程师",
        "Agent", "AIGC产品", "AI策略产品",
        "LLM", "大模型评测", "AI质量",
    ],
    "cities": ["北京", "上海", "杭州", "深圳", "广州"],
    "categories": {},
}

def run_api():
    from src.scrapers.tencent import TencentScraper
    from src.scrapers.baidu import BaiduScraper
    from src.scrapers.netease import NeteaseScraper

    all_jobs = []
    for Cls, name in [(TencentScraper, "tencent"), (BaiduScraper, "baidu"), (NeteaseScraper, "netease")]:
        logger.info("=== %s ===", name)
        s = Cls(CONFIG)
        try:
            jobs = s.scrape()
            logger.info("[%s] %d raw", name, len(jobs))
            all_jobs.extend(jobs)
        except Exception:
            logger.error("[%s] failed", name, exc_info=True)
        finally:
            s.close()
    return all_jobs

def run_bytedance():
    logger.info("=== bytedance (Playwright) ===")
    from src.scrapers.bytedance import BytedanceScraper
    fast = {**CONFIG, "keywords": ["AI测试", "大模型", "Agent", "LLM", "AIGC", "测试开发"], "cities": ["北京", "上海", "深圳"]}
    s = BytedanceScraper(fast)
    try:
        jobs = s.scrape()
        logger.info("[bytedance] %d raw", len(jobs))
        return jobs
    except Exception:
        logger.error("[bytedance] failed", exc_info=True)
        return []
    finally:
        s.close()

def fix_bytedance_data(jobs):
    """Clean ByteDance dept/location fields."""
    CITIES = ["北京", "上海", "杭州", "深圳", "广州", "成都", "武汉", "南京"]
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

def fix_baidu_titles(jobs):
    for j in jobs:
        if j.platform == "baidu" and j.title.startswith("script>"):
            m = re.search(r'"name":"([^"]+)"', j.title)
            j.title = m.group(1) if m else ""

def run_quark():
    logger.info("=== quark / 千问 (Playwright) ===")
    from src.scrapers.quark import scrape_quark
    try:
        jobs = scrape_quark()
        logger.info("[quark] %d raw", len(jobs))
        return jobs
    except Exception:
        logger.error("[quark] failed", exc_info=True)
        return []


def main():
    logger.info("Starting final scrape at %s", datetime.now().isoformat())

    api_jobs = run_api()
    bd_jobs = run_bytedance()
    quark_jobs = run_quark()
    all_raw = api_jobs + bd_jobs + quark_jobs

    logger.info("Total raw: %d", len(all_raw))

    fix_bytedance_data(all_raw)
    fix_baidu_titles(all_raw)

    processed = normalize_jobs(all_raw, {})
    processed = deduplicate(processed)
    logger.info("After dedup: %d", len(processed))

    filtered = filter_strict(processed)

    cats = Counter(j.category for j in filtered)
    logger.info("Final categories:")
    for c, n in cats.most_common():
        logger.info("  %s: %d", c, n)

    platforms = Counter(
        (j.company or j.platform) for j in filtered
    )
    logger.info("Final companies:")
    for p, n in platforms.most_common():
        logger.info("  %s: %d", p, n)

    today = datetime.now().strftime("%Y-%m-%d")
    Path("data/daily").mkdir(parents=True, exist_ok=True)
    save_jobs_to_json(processed, f"data/daily/{today}_raw.json")

    save_jobs_to_json(filtered, "data/jobs.json")
    save_jobs_to_json(filtered, f"data/daily/{today}.json")

    generate_readme(filtered, "README.md")
    logger.info("DONE: %d filtered jobs", len(filtered))


if __name__ == "__main__":
    main()
