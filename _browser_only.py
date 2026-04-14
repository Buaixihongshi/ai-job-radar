"""Run Playwright-only scrapers and merge with existing API results."""
import json
import logging
import sys
import time
import random
import re
import urllib.parse
from collections import Counter
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("_browser_log.txt", encoding="utf-8"),
    ],
)
logger = logging.getLogger("browser-scrape")

from src.models import JobPosting, load_jobs_from_json, save_jobs_to_json
from src.pipeline.normalizer import normalize_jobs
from src.pipeline.dedup import deduplicate
from src.report import generate_readme
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

KEYWORDS = ["AI测试", "大模型", "Agent", "LLM", "AIGC", "测试开发"]
CITIES = ["北京", "上海", "深圳"]
CATEGORIES = {
    "product": {"name": "产品类", "keywords": ["产品经理", "策略产品"]},
    "test": {"name": "测试类", "keywords": ["测试", "QA", "评测"]},
    "agent": {"name": "Agent类", "keywords": ["Agent", "LLM"]},
    "dev": {"name": "开发类", "keywords": ["开发", "工具"]},
}


def launch_browser():
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
    return pw, browser, context, page


def scrape_bytedance(page) -> list[JobPosting]:
    logger.info("=== ByteDance ===")
    jobs = []
    for kw in KEYWORDS:
        url = f"https://jobs.bytedance.com/experienced/position?keyword={urllib.parse.quote(kw)}&limit=30&offset=0"
        try:
            logger.info("[bytedance] %s", kw)
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            for _ in range(5):
                page.evaluate("window.scrollBy(0, 500)")
                page.wait_for_timeout(500)

            cards = page.query_selector_all("a[href*='/position/']")
            if not cards:
                cards = page.query_selector_all("[class*='position'], [class*='job']")

            logger.info("[bytedance] %s -> %d elements", kw, len(cards))

            for card in cards:
                try:
                    text = card.inner_text().strip()
                    href = card.get_attribute("href") or ""
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    if not lines:
                        continue
                    title = lines[0]
                    if len(title) < 4 or len(title) > 60:
                        continue
                    jid = re.search(r'/position/(\d+)', href)
                    job_id = jid.group(1) if jid else f"bd-{title[:15]}"
                    full_url = f"https://jobs.bytedance.com{href}" if href and not href.startswith("http") else href

                    dept = lines[1] if len(lines) > 1 else ""
                    location = lines[2] if len(lines) > 2 else ""

                    job = JobPosting(
                        job_id=job_id,
                        platform="bytedance",
                        title=title,
                        company="字节跳动",
                        department=dept,
                        location=location,
                        url=full_url,
                    )
                    jobs.append(job)
                except Exception:
                    continue

            time.sleep(random.uniform(2.0, 4.0))
        except Exception:
            logger.warning("[bytedance] %s failed", kw, exc_info=True)

    logger.info("[bytedance] total: %d", len(jobs))
    return jobs


def scrape_alibaba(page) -> list[JobPosting]:
    logger.info("=== Alibaba ===")
    jobs = []
    for kw in KEYWORDS:
        url = f"https://talent.alibaba.com/off-campus/position-list?keyword={urllib.parse.quote(kw)}"
        try:
            logger.info("[alibaba] %s", kw)
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(4000)

            for _ in range(5):
                page.evaluate("window.scrollBy(0, 500)")
                page.wait_for_timeout(500)

            cards = page.query_selector_all("a[href*='positionId']")
            if not cards:
                cards = page.query_selector_all("[class*='position'], [class*='job'], [class*='Position']")

            logger.info("[alibaba] %s -> %d elements", kw, len(cards))

            for card in cards:
                try:
                    text = card.inner_text().strip()
                    href = card.get_attribute("href") or ""
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    if not lines:
                        continue
                    title = lines[0]
                    if len(title) < 4 or len(title) > 60:
                        continue

                    jid = re.search(r'positionId=(\w+)', href)
                    job_id = jid.group(1) if jid else f"ali-{title[:15]}"
                    full_url = f"https://talent.alibaba.com{href}" if href and not href.startswith("http") else href

                    dept = ""
                    location = ""
                    for line in lines[1:]:
                        if any(c in line for c in ["北京", "上海", "杭州", "深圳", "广州"]):
                            location = line
                        elif not dept:
                            dept = line

                    job = JobPosting(
                        job_id=job_id,
                        platform="alibaba",
                        title=title,
                        company="阿里巴巴",
                        department=dept,
                        location=location,
                        url=full_url,
                    )
                    jobs.append(job)
                except Exception:
                    continue

            time.sleep(random.uniform(2.0, 4.0))
        except Exception:
            logger.warning("[alibaba] %s failed", kw, exc_info=True)

    logger.info("[alibaba] total: %d", len(jobs))
    return jobs


def scrape_zhilian(page) -> list[JobPosting]:
    logger.info("=== Zhilian ===")
    jobs = []
    for kw in KEYWORDS:
        for city in CITIES:
            try:
                url = f"https://sou.zhaopin.com/?kw={urllib.parse.quote(kw)}&city={urllib.parse.quote(city)}"
                logger.info("[zhilian] %s @ %s", kw, city)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)

                for _ in range(3):
                    page.evaluate("window.scrollBy(0, 500)")
                    page.wait_for_timeout(600)

                cards = page.query_selector_all(
                    ".joblist-box__item, [class*='job-card'], [class*='JobCard'], "
                    "[class*='joblist'], a[href*='jobs.zhaopin.com']"
                )
                logger.info("[zhilian] %s @ %s -> %d elements", kw, city, len(cards))

                for card in cards:
                    try:
                        text = card.inner_text().strip()
                        href = card.get_attribute("href") or ""
                        if not href:
                            link = card.query_selector("a[href*='jobs.zhaopin.com']")
                            href = link.get_attribute("href") if link else ""

                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                        if not lines:
                            continue

                        title = lines[0]
                        if len(title) < 4 or len(title) > 80:
                            continue

                        jid = re.search(r'/(\w+)\.htm', href)
                        job_id = jid.group(1) if jid else f"zl-{title[:15]}"

                        company = ""
                        salary = ""
                        for line in lines[1:]:
                            if re.search(r'\d+[kK万]', line) or "-" in line and any(c.isdigit() for c in line):
                                salary = line
                            elif not company and len(line) > 2:
                                company = line

                        job = JobPosting(
                            job_id=job_id,
                            platform="zhilian",
                            title=title,
                            company=company,
                            location=city,
                            salary=salary,
                            url=href if href.startswith("http") else "",
                        )
                        jobs.append(job)
                    except Exception:
                        continue

                time.sleep(random.uniform(2.0, 4.0))
            except Exception:
                logger.warning("[zhilian] %s @ %s failed", kw, city, exc_info=True)

    logger.info("[zhilian] total: %d", len(jobs))
    return jobs


def scrape_boss(page) -> list[JobPosting]:
    logger.info("=== Boss ===")
    jobs = []
    city_codes = [("101010100", "北京"), ("101020100", "上海"), ("101280600", "深圳")]

    for kw in ["AI测试", "大模型", "Agent"]:
        for city_code, city_name in city_codes:
            try:
                url = f"https://www.zhipin.com/web/geek/job?query={urllib.parse.quote(kw)}&city={city_code}"
                logger.info("[boss] %s @ %s", kw, city_name)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(4000)

                captcha = page.query_selector("[class*='verify'], [class*='captcha'], .dialog-confirm")
                if captcha:
                    logger.warning("[boss] captcha detected, skipping")
                    continue

                cards = page.query_selector_all(
                    ".job-card-wrapper, [class*='job-card'], "
                    ".search-job-result li, [class*='jobCard']"
                )
                logger.info("[boss] %s @ %s -> %d cards", kw, city_name, len(cards))

                for card in cards:
                    try:
                        text = card.inner_text().strip()
                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                        if not lines:
                            continue

                        title = lines[0]
                        if len(title) < 4 or len(title) > 60:
                            continue

                        link_el = card.query_selector("a")
                        href = link_el.get_attribute("href") if link_el else ""
                        full_url = f"https://www.zhipin.com{href}" if href and not href.startswith("http") else href

                        jid = re.search(r'/job_detail/([^./?]+)', href or "")
                        job_id = jid.group(1) if jid else f"boss-{title[:15]}"

                        company = ""
                        salary = ""
                        for line in lines[1:]:
                            if re.search(r'\d+[kK万]', line) or ("·" in line and any(c.isdigit() for c in line)):
                                salary = line
                            elif not company and len(line) > 2 and not line.startswith("距离"):
                                company = line

                        job = JobPosting(
                            job_id=job_id,
                            platform="boss",
                            title=title,
                            company=company,
                            location=city_name,
                            salary=salary,
                            url=full_url,
                        )
                        jobs.append(job)
                    except Exception:
                        continue

                time.sleep(random.uniform(3.0, 6.0))
            except Exception:
                logger.warning("[boss] %s @ %s failed", kw, city_name, exc_info=True)

    logger.info("[boss] total: %d", len(jobs))
    return jobs


def main():
    logger.info("Starting browser-only scrape")

    pw, browser, context, page = launch_browser()
    all_browser_jobs = []

    try:
        bd_jobs = scrape_bytedance(page)
        all_browser_jobs.extend(bd_jobs)

        ali_jobs = scrape_alibaba(page)
        all_browser_jobs.extend(ali_jobs)

        zl_jobs = scrape_zhilian(page)
        all_browser_jobs.extend(zl_jobs)

        boss_jobs = scrape_boss(page)
        all_browser_jobs.extend(boss_jobs)
    finally:
        context.close()
        browser.close()
        pw.stop()

    logger.info("Browser scrape total: %d raw jobs", len(all_browser_jobs))

    existing = load_jobs_from_json("data/jobs.json")
    logger.info("Existing API jobs: %d", len(existing))

    all_jobs = existing + all_browser_jobs
    processed = normalize_jobs(all_jobs, CATEGORIES)
    processed = deduplicate(processed)

    cats = Counter(j.category for j in processed)
    logger.info("Category distribution:")
    for c, n in cats.most_common():
        logger.info("  %s: %d", c, n)

    platforms = Counter(j.platform for j in processed)
    logger.info("Platform distribution:")
    for p, n in platforms.most_common():
        logger.info("  %s: %d", p, n)

    save_jobs_to_json(processed, "data/jobs.json")

    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = Path("data/daily")
    daily_dir.mkdir(parents=True, exist_ok=True)
    save_jobs_to_json(processed, str(daily_dir / f"{today}.json"))

    generate_readme(processed, "README.md")
    logger.info("FINAL: %d jobs saved, README updated", len(processed))


if __name__ == "__main__":
    main()
