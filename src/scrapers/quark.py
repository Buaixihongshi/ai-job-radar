"""Scraper for talent.quark.cn (千问/夸克 - Alibaba Group).

Uses Playwright request interception to override search API pagination params,
since the SPA ignores URL-based keyword/page parameters.
"""
from __future__ import annotations

import json
import logging

from src.models import JobPosting

logger = logging.getLogger(__name__)

DEGREE_MAP = {
    "bachelor": "本科",
    "master": "硕士",
    "doctor": "博士",
    "phd": "博士",
    "college": "大专",
    "high_school": "高中",
}

PAGE_SIZE = 50


def _format_experience(exp) -> str:
    if not exp or exp == "None":
        return ""
    if isinstance(exp, str):
        return exp
    if isinstance(exp, dict):
        fr = exp.get("from")
        to = exp.get("to")
        if fr and to:
            return f"{fr}-{to}年"
        if fr:
            return f"{fr}年以上"
    return ""


def _format_degree(deg) -> str:
    if not deg:
        return ""
    if isinstance(deg, str):
        return DEGREE_MAP.get(deg.lower(), deg)
    return str(deg)


def scrape_quark() -> list[JobPosting]:
    from playwright.sync_api import sync_playwright
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
    except ImportError:
        stealth = None

    all_items: dict[str, dict] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        if stealth:
            stealth.apply_stealth_sync(page)

        page.goto(
            "https://talent.quark.cn/off-campus/position-list?lang=zh",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        page.wait_for_timeout(3000)

        target_page = [1]

        def intercept(route, request):
            if request.method == "POST":
                try:
                    body = json.loads(request.post_data)
                    body["pageIndex"] = target_page[0]
                    body["pageSize"] = PAGE_SIZE
                    route.continue_(post_data=json.dumps(body))
                    return
                except Exception:
                    pass
            route.continue_()

        page.route("**/position/search**", intercept)

        current_batch: list[dict] = []
        total_count = 0

        def on_resp(response):
            nonlocal total_count
            if "position/search" in response.url and response.status == 200:
                try:
                    data = response.json()
                    if data.get("success"):
                        content = data.get("content", {})
                        total_count = content.get("totalCount", 0)
                        current_batch.clear()
                        current_batch.extend(content.get("datas", []))
                except Exception:
                    pass

        page.on("response", on_resp)

        for pi in range(1, 30):
            target_page[0] = pi
            current_batch.clear()

            page.goto(
                "https://talent.quark.cn/off-campus/position-list?lang=zh",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            page.wait_for_timeout(2000)

            if not current_batch:
                logger.info("[quark] page %d: empty, done", pi)
                break

            new_count = 0
            for item in current_batch:
                pid = str(item.get("id", ""))
                if pid and pid not in all_items:
                    all_items[pid] = item
                    new_count += 1

            logger.info(
                "[quark] page %d: total=%d, returned=%d, new=%d, cumulative=%d",
                pi, total_count, len(current_batch), new_count, len(all_items),
            )

            if len(current_batch) < PAGE_SIZE:
                break

        page.unroute("**/position/search**")
        page.remove_listener("response", on_resp)

        jobs: list[JobPosting] = []
        for pid, item in all_items.items():
            locations = item.get("workLocations", [])
            if isinstance(locations, list):
                loc = ", ".join(locations)
            else:
                loc = str(locations)

            jobs.append(JobPosting(
                job_id=pid,
                platform="quark",
                title=item.get("name", ""),
                company="阿里巴巴",
                department=item.get("department", "") or item.get("departmentName", ""),
                location=loc,
                experience=_format_experience(item.get("experience")),
                education=_format_degree(item.get("degree")),
                description=item.get("description", ""),
                requirements=item.get("requirement", ""),
                url=f"https://talent.quark.cn/off-campus/position-detail?positionId={pid}",
            ))

        logger.info("[quark] total positions scraped: %d", len(jobs))
        browser.close()

    return jobs
