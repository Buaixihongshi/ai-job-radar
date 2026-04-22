"""Scraper for career.huawei.com (华为招聘).

Uses Playwright with API interception for the social recruitment portal.
Huawei's career site renders via template engine with async data fetching.
"""
from __future__ import annotations

import logging
import re

from src.models import JobPosting

logger = logging.getLogger(__name__)

KEYWORDS = ["测试", "AI", "Agent", "评测", "大模型", "质量", "AIGC", "LLM", "算法测试"]

BASE_URL = "https://career.huawei.com/reccampportal/portal5/social-recruitment.html"


def scrape_huawei() -> list[JobPosting]:
    from src.scrapers.browser_base import playwright_page

    all_items: dict[str, dict] = {}

    with playwright_page() as page:

        for kw in KEYWORDS:
            captured: list[dict] = []

            def on_resp(response):
                url = response.url
                if response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct and any(s in url for s in ("position", "job", "search", "query", "recruit", "social")):
                        try:
                            data = response.json()
                            _extract_items(data, captured)
                        except Exception:
                            pass

            page.on("response", on_resp)
            try:
                page.goto(
                    f"{BASE_URL}?keyword={kw}",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                page.wait_for_timeout(4000)

                # Try typing in search if there's a search box
                search_input = page.query_selector(
                    "input[placeholder*='搜索'], input[placeholder*='职位'], "
                    "input[type='search'], input[class*='search']"
                )
                if search_input:
                    search_input.fill(kw)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(3000)

                for _ in range(5):
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(800)
            except Exception:
                logger.warning("[huawei] page load failed for %s", kw)

            page.remove_listener("response", on_resp)

            new_count = 0
            for item in captured:
                pid = str(item.get("id", item.get("jobId", item.get("positionId", ""))))
                if pid and pid not in all_items:
                    all_items[pid] = item
                    new_count += 1

            logger.info("[huawei] keyword=%s captured=%d new=%d cumulative=%d",
                        kw, len(captured), new_count, len(all_items))

            if not captured:
                _scrape_dom(page, kw, all_items)

        jobs: list[JobPosting] = []
        for pid, item in all_items.items():
            loc = item.get("workLocation", item.get("city", item.get("location", "")))
            if isinstance(loc, list):
                loc = ", ".join(str(v) for v in loc if v)

            jobs.append(JobPosting(
                job_id=pid,
                platform="huawei",
                company="华为",
                title=item.get("jobname", item.get("name", item.get("jobName", ""))),
                department=item.get("department", item.get("deptName", item.get("orgName", ""))),
                location=str(loc),
                experience=item.get("workYear", item.get("experience", "")),
                education=item.get("education", item.get("degree", "")),
                description=item.get("description", item.get("responsibility", "")),
                requirements=item.get("requirement", item.get("qualification", "")),
                url=f"https://career.huawei.com/reccampportal/portal5/social-recruitment-detail.html?jobId={pid}",
            ))

        logger.info("[huawei] total: %d", len(jobs))

    return jobs


def _extract_items(data, out: list[dict]) -> None:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                has_title = any(k in item for k in ("name", "jobName", "jobname", "positionName"))
                has_id = any(k in item for k in ("id", "jobId", "positionId"))
                if has_title and has_id:
                    out.append(item)
                else:
                    _extract_items(item, out)
        return
    if not isinstance(data, dict):
        return
    for key in ("data", "list", "result", "records", "items", "content", "jobList",
                "positions", "rows", "pageVO"):
        val = data.get(key)
        if val is not None:
            _extract_items(val, out)


def _scrape_dom(page, keyword: str, all_items: dict) -> None:
    try:
        cards = page.query_selector_all(
            "a[href*='social-recruitment-detail'], a[href*='jobId'], "
            "[class*='job-card'], [class*='job-item'], [class*='position-item'], "
            "tr[class*='item'], li[class*='item']"
        )
        new_count = 0
        for card in cards:
            try:
                text = card.inner_text().strip()
                href = card.get_attribute("href") or ""
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if not lines or len(lines[0]) < 4:
                    continue

                jid_match = re.search(r'jobId=(\w+)', href)
                pid = jid_match.group(1) if jid_match else f"hw-{lines[0][:15]}"

                if pid not in all_items:
                    all_items[pid] = {
                        "id": pid,
                        "jobname": lines[0],
                        "department": lines[1] if len(lines) > 1 else "",
                        "city": lines[2] if len(lines) > 2 else "",
                    }
                    new_count += 1
            except Exception:
                continue
        logger.info("[huawei] DOM fallback for '%s': %d new items", keyword, new_count)
    except Exception:
        pass
