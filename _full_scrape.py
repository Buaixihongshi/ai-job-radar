"""Full scrape: Tier 1 API + Playwright for SPA sites, then process and save."""
import json
import logging
import sys
import time
import random
from collections import Counter
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("_scrape_log.txt", encoding="utf-8"),
    ],
)
logger = logging.getLogger("full-scrape")

from src.models import JobPosting, save_jobs_to_json
from src.pipeline.normalizer import normalize_jobs
from src.pipeline.dedup import deduplicate
from src.report import generate_readme

CONFIG = {
    "keywords": [
        "大模型测试", "AI测试", "算法测试",
        "自动化测试开发", "测试开发工程师",
        "Agent产品", "AIGC产品", "AI策略产品",
        "LLM", "Agent开发", "大模型评测",
        "AI质量", "智能测试",
    ],
    "cities": ["北京", "上海", "杭州", "深圳", "广州"],
    "categories": {
        "product": {"name": "产品类", "keywords": ["产品经理", "策略产品", "AIGC产品", "Agent产品", "AI产品"]},
        "test": {"name": "测试类", "keywords": ["测试", "质量保障", "QA", "评测", "质量"]},
        "agent": {"name": "Agent类", "keywords": ["Agent开发", "Agent工程师", "LLM应用", "Agent"]},
        "dev": {"name": "开发类", "keywords": ["测试开发", "自动化", "工具开发", "平台开发"]},
    },
}

FAST_CONFIG = {
    **CONFIG,
    "keywords": ["AI测试", "大模型", "Agent", "LLM", "AIGC", "测试开发"],
    "cities": ["北京", "上海", "深圳"],
}


def run_api_scrapers() -> list[JobPosting]:
    """Run Tier 1 API-based scrapers (Tencent, Baidu)."""
    from src.scrapers.tencent import TencentScraper
    from src.scrapers.baidu import BaiduScraper

    all_jobs = []

    for ScraperCls, name in [(TencentScraper, "tencent"), (BaiduScraper, "baidu")]:
        logger.info("=== Running %s (API) ===", name)
        scraper = ScraperCls(CONFIG)
        try:
            jobs = scraper.scrape()
            logger.info("[%s] got %d raw jobs", name, len(jobs))
            all_jobs.extend(jobs)
        except Exception:
            logger.error("[%s] failed", name, exc_info=True)
        finally:
            scraper.close()

    return all_jobs


def run_zhilian_scraper() -> list[JobPosting]:
    """Run Zhilian with correct city codes via Playwright."""
    logger.info("=== Running zhilian (Browser) ===")
    jobs = []

    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        stealth = Stealth()
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        stealth.apply_stealth_sync(page)

        for kw in FAST_CONFIG["keywords"]:
            for city in FAST_CONFIG["cities"]:
                try:
                    import urllib.parse
                    url = f"https://sou.zhaopin.com/?kw={urllib.parse.quote(kw)}&city={urllib.parse.quote(city)}"
                    logger.info("[zhilian] %s @ %s", kw, city)
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)

                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 500)")
                        page.wait_for_timeout(600)

                    cards = page.query_selector_all(
                        ".joblist-box__item, .positionlist .job-item, "
                        "[class*='job-card'], [class*='jobCard']"
                    )

                    if not cards:
                        cards = page.query_selector_all("a[href*='jobs.zhaopin.com']")

                    for card in cards:
                        try:
                            title_el = card.query_selector(
                                "[class*='iteminfo__line1__jobname'], "
                                "[class*='job-name'], [class*='jobName'], "
                                "h3, .job-title"
                            )
                            company_el = card.query_selector(
                                "[class*='iteminfo__line1__compname'], "
                                "[class*='company-name'], [class*='companyName']"
                            )
                            salary_el = card.query_selector(
                                "[class*='iteminfo__line2__jobdesc__salary'], "
                                "[class*='salary'], [class*='job-salary']"
                            )
                            loc_el = card.query_selector(
                                "[class*='iteminfo__line2__jobdesc__demand__area'], "
                                "[class*='job-area'], [class*='location']"
                            )

                            title = title_el.inner_text().strip() if title_el else ""
                            company = company_el.inner_text().strip() if company_el else ""
                            salary = salary_el.inner_text().strip() if salary_el else ""
                            location = loc_el.inner_text().strip() if loc_el else city

                            if not title or len(title) < 2:
                                continue

                            href = card.get_attribute("href") or ""
                            if not href:
                                link_el = card.query_selector("a[href*='jobs.zhaopin.com']")
                                href = link_el.get_attribute("href") if link_el else ""

                            import re
                            jid_match = re.search(r'/(\w+)\.htm', href)
                            job_id = jid_match.group(1) if jid_match else title[:20]

                            job = JobPosting(
                                job_id=job_id,
                                platform="zhilian",
                                title=title,
                                company=company,
                                location=location,
                                salary=salary,
                                url=href if href.startswith("http") else "",
                            )
                            jobs.append(job)
                        except Exception:
                            continue

                    logger.info("[zhilian] %s @ %s -> %d cards", kw, city, len(cards))
                    time.sleep(random.uniform(2.0, 4.0))
                except Exception:
                    logger.warning("[zhilian] %s @ %s failed", kw, city, exc_info=True)

        context.close()
        browser.close()
        pw.stop()
    except Exception:
        logger.error("[zhilian] browser failed", exc_info=True)

    logger.info("[zhilian] total: %d jobs", len(jobs))
    return jobs


def run_browser_scrapers() -> list[JobPosting]:
    """Run Playwright-based scrapers for ByteDance and Alibaba."""
    from src.scrapers.bytedance import BytedanceScraper
    from src.scrapers.alibaba import AlibabaScraper

    all_jobs = []

    for ScraperCls, name in [
        (BytedanceScraper, "bytedance"),
        (AlibabaScraper, "alibaba"),
    ]:
        logger.info("=== Running %s (Browser) ===", name)
        scraper = ScraperCls(FAST_CONFIG)
        try:
            jobs = scraper.scrape()
            logger.info("[%s] got %d raw jobs", name, len(jobs))
            all_jobs.extend(jobs)
        except Exception:
            logger.error("[%s] failed", name, exc_info=True)
        finally:
            scraper.close()

    return all_jobs


def run_boss_browser() -> list[JobPosting]:
    """Try Boss直聘 with Playwright."""
    logger.info("=== Running boss (Browser) ===")
    jobs = []

    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        stealth = Stealth()
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        stealth.apply_stealth_sync(page)

        for kw in ["AI测试", "大模型", "Agent"]:
            for city_code, city_name in [("101010100", "北京"), ("101020100", "上海"), ("101280100", "深圳")]:
                try:
                    import urllib.parse
                    url = f"https://www.zhipin.com/web/geek/job?query={urllib.parse.quote(kw)}&city={city_code}"
                    logger.info("[boss] %s @ %s", kw, city_name)
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(4000)

                    verify_el = page.query_selector("[class*='verify'], [class*='captcha']")
                    if verify_el:
                        logger.warning("[boss] verification/captcha detected, skipping")
                        break

                    cards = page.query_selector_all(
                        ".job-card-wrapper, .search-job-result .job-list li, "
                        "[class*='job-card'], [class*='jobCard']"
                    )
                    logger.info("[boss] found %d cards", len(cards))

                    for card in cards:
                        try:
                            title_el = card.query_selector(".job-name, [class*='job-title'], h3")
                            company_el = card.query_selector(".company-name, [class*='company']")
                            salary_el = card.query_selector(".salary, [class*='salary']")
                            area_el = card.query_selector(".job-area, [class*='location']")

                            title = title_el.inner_text().strip() if title_el else ""
                            company = company_el.inner_text().strip() if company_el else ""
                            salary = salary_el.inner_text().strip() if salary_el else ""
                            area = area_el.inner_text().strip() if area_el else city_name

                            if not title or len(title) < 2:
                                continue

                            href = card.query_selector("a")
                            link = href.get_attribute("href") if href else ""
                            full_link = f"https://www.zhipin.com{link}" if link and not link.startswith("http") else link

                            import re
                            jid_match = re.search(r'/job_detail/([^.]+)', link or "")
                            job_id = jid_match.group(1) if jid_match else f"boss-{title[:15]}"

                            job = JobPosting(
                                job_id=job_id,
                                platform="boss",
                                title=title,
                                company=company,
                                location=area,
                                salary=salary,
                                url=full_link,
                            )
                            jobs.append(job)
                        except Exception:
                            continue

                    time.sleep(random.uniform(3.0, 6.0))
                except Exception:
                    logger.warning("[boss] %s @ %s failed", kw, city_name, exc_info=True)

        context.close()
        browser.close()
        pw.stop()
    except Exception:
        logger.error("[boss] browser failed", exc_info=True)

    logger.info("[boss] total: %d jobs", len(jobs))
    return jobs


def process_and_save(all_jobs: list[JobPosting]) -> list[JobPosting]:
    """Normalize, dedup, classify, and save."""
    categories = CONFIG["categories"]

    processed = normalize_jobs(all_jobs, categories)
    processed = deduplicate(processed)

    logger.info("After dedup: %d jobs", len(processed))

    cats = Counter(j.category for j in processed)
    logger.info("Category distribution:")
    for c, n in cats.most_common():
        logger.info("  %s: %d", c, n)

    platforms = Counter(j.platform for j in processed)
    logger.info("Platform distribution:")
    for p, n in platforms.most_common():
        logger.info("  %s: %d", p, n)

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    save_jobs_to_json(processed, str(data_dir / "jobs.json"))

    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    save_jobs_to_json(processed, str(daily_dir / f"{today}.json"))

    generate_readme(processed, "README.md")
    logger.info("Saved %d jobs and generated README.md", len(processed))

    return processed


def main():
    logger.info("Starting full scrape at %s", datetime.now().isoformat())

    all_jobs = []

    api_jobs = run_api_scrapers()
    all_jobs.extend(api_jobs)
    logger.info("API scrapers done: %d jobs", len(api_jobs))

    browser_jobs = run_browser_scrapers()
    all_jobs.extend(browser_jobs)
    logger.info("Browser scrapers (bytedance/alibaba) done: %d jobs", len(browser_jobs))

    zhilian_jobs = run_zhilian_scraper()
    all_jobs.extend(zhilian_jobs)
    logger.info("Zhilian done: %d jobs", len(zhilian_jobs))

    boss_jobs = run_boss_browser()
    all_jobs.extend(boss_jobs)
    logger.info("Boss done: %d jobs", len(boss_jobs))

    logger.info("=" * 60)
    logger.info("TOTAL RAW JOBS: %d", len(all_jobs))
    logger.info("=" * 60)

    processed = process_and_save(all_jobs)

    logger.info("=" * 60)
    logger.info("FINAL RESULT: %d processed jobs", len(processed))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
