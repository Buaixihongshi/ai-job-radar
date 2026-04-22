"""Scraper for zhaopin.jd.com (京东招聘).

Uses Playwright: navigate to social recruitment page, use search input,
intercept API responses for structured data, DOM fallback.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.models import JobPosting

logger = logging.getLogger(__name__)

KEYWORDS = ["测试", "AI", "Agent", "评测", "大模型", "质量", "LLM"]
BASE_URL = "https://zhaopin.jd.com/web/job/job_info_list/3"


def scrape_jd() -> list[JobPosting]:
    from src.scrapers.browser_base import playwright_page

    all_items: dict[str, dict] = {}

    with playwright_page() as page:

        captured: list[dict] = []

        def on_resp(response):
            url = response.url
            if response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct and any(s in url for s in ("job", "position", "search", "list")):
                    try:
                        data = response.json()
                        _extract_items(data, captured)
                    except Exception:
                        pass

        page.on("response", on_resp)

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(4000)
        except Exception:
            logger.warning("[jd] initial page load failed, trying alternative URL")
            try:
                page.goto("https://zhaopin.jd.com/", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(4000)
                social_link = page.query_selector("a[href*='job_info_list/3'], a[href*='social']")
                if social_link:
                    social_link.click()
                    page.wait_for_timeout(3000)
            except Exception:
                logger.warning("[jd] alternative URL also failed")

        for item in captured:
            pid = str(item.get("id", item.get("jobId", item.get("positionId", ""))))
            if pid and pid not in all_items:
                all_items[pid] = item
        captured.clear()

        for kw in KEYWORDS:
            try:
                search_input = page.query_selector(
                    "input[placeholder*='搜索'], input[placeholder*='职位'], "
                    "input[type='search'], input[class*='search'], input[class*='Search']"
                )
                if search_input:
                    search_input.click()
                    search_input.fill("")
                    search_input.fill(kw)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(3000)
                else:
                    page.goto(f"{BASE_URL}?keyword={kw}", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)

                for _ in range(3):
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(800)

                for item in captured:
                    pid = str(item.get("id", item.get("jobId", item.get("positionId", ""))))
                    if pid and pid not in all_items:
                        all_items[pid] = item
                captured.clear()

            except Exception:
                logger.warning("[jd] search failed for %s", kw)

            _scrape_dom(page, all_items)
            logger.info("[jd] keyword=%s cumulative=%d", kw, len(all_items))
            time.sleep(random.uniform(1.0, 2.0))

        page.remove_listener("response", on_resp)

        jobs: list[JobPosting] = []
        for pid, item in all_items.items():
            loc = item.get("workAddress", item.get("city", item.get("location", "")))
            if isinstance(loc, list):
                loc = ", ".join(str(v) for v in loc if v)

            jobs.append(JobPosting(
                job_id=pid, platform="jd", company="京东",
                title=item.get("name", item.get("jobName", item.get("positionName", ""))),
                department=item.get("department", item.get("deptName", "")),
                location=str(loc),
                experience=item.get("workYear", item.get("experience", "")),
                education=item.get("education", item.get("degree", "")),
                description=item.get("description", item.get("jobDesc", "")),
                requirements=item.get("requirement", item.get("jobReq", "")),
                url=f"https://zhaopin.jd.com/web/job/job_info/{pid}",
                publish_date=item.get("publishDate", item.get("updateTime", "")),
            ))

        logger.info("[jd] total: %d", len(jobs))

    return jobs


def _extract_items(data, out: list[dict]) -> None:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                has_title = any(k in item for k in ("name", "jobName", "positionName"))
                has_id = any(k in item for k in ("id", "jobId", "positionId"))
                if has_title and has_id:
                    out.append(item)
                else:
                    _extract_items(item, out)
        return
    if not isinstance(data, dict):
        return
    for key in ("data", "list", "result", "records", "items", "jobs", "content"):
        val = data.get(key)
        if val is not None:
            _extract_items(val, out)


def _scrape_dom(page, all_items: dict) -> None:
    try:
        cards = page.query_selector_all(
            "a[href*='job_info'], [class*='job-card'], [class*='job-item'], "
            "[class*='position-item'], [class*='list-item'], [class*='Job']"
        )
        for card in cards:
            try:
                text = card.inner_text().strip()
                href = card.get_attribute("href") or ""
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if not lines or len(lines[0]) < 4 or len(lines[0]) > 80:
                    continue
                jid_match = re.search(r'job_info/(\w+)', href) or re.search(r'/(\d+)', href)
                pid = jid_match.group(1) if jid_match else None
                if not pid or pid in all_items:
                    continue
                all_items[pid] = {
                    "id": pid, "name": lines[0],
                    "department": lines[1] if len(lines) > 1 else "",
                    "city": lines[2] if len(lines) > 2 else "",
                }
            except Exception:
                continue
    except Exception:
        pass
