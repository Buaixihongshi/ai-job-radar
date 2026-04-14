"""Taotian (淘天集团) scraper - Playwright intercepts API + category filter + pagination.

淘天集团 includes: 淘宝天猫, 闲鱼, 1688, 淘宝闪购, 阿里妈妈, etc.
API: talent.taotian.com/position/search (CSRF protected, need session)
"""
from __future__ import annotations

import json
import logging
import re
import time

from src.models import JobPosting
from src.scrapers.browser_base import BrowserScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://talent.taotian.com/off-campus/position-list?lang=zh"
DETAIL_URL_TEMPLATE = "https://talent.taotian.com/off-campus/position-detail?positionId={pid}"

SEARCH_URLS = [
    BASE_URL + "&keyword={keyword}",
    BASE_URL + "&keyword={keyword}&category=130",
]


class TaotianScraper(BrowserScraper):
    """淘天集团 (Taotian / Alibaba China E-commerce)."""

    @property
    def platform_name(self) -> str:
        return "taotian"

    def scrape(self) -> list[JobPosting]:
        """Override to use single browser session with multiple searches."""
        page = self._launch()
        all_jobs: list[JobPosting] = []

        keywords = self.config.get("keywords", [])
        seen_ids = set()

        for keyword in keywords:
            url = BASE_URL + f"&keyword={keyword}"
            jobs = self._scrape_pages(page, url, keyword, seen_ids)
            all_jobs.extend(jobs)

        logger.info("[taotian] total raw results: %d", len(all_jobs))
        return all_jobs

    def _scrape_pages(self, page, url: str, keyword: str, seen_ids: set) -> list[JobPosting]:
        jobs = []
        api_data = []

        def on_response(response):
            if "position/search" in response.url and response.status == 200:
                try:
                    data = response.json()
                    if data.get("success"):
                        items = data.get("content", {}).get("datas", [])
                        api_data.extend(items)
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
        except Exception:
            logger.warning("[taotian] page load failed for %s", keyword)
            page.remove_listener("response", on_response)
            return jobs

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(500)

        # Paginate - click through pages
        for page_num in range(2, 6):
            try:
                next_btns = page.query_selector_all(
                    "button.next:not([disabled]), "
                    "li.next:not(.disabled) a, "
                    "[class*='next-btn']:not([disabled]), "
                    "[class*='pagination'] [class*='next']:not([class*='disabled'])"
                )
                if next_btns:
                    next_btns[0].click()
                    page.wait_for_timeout(3000)
                    page.evaluate("window.scrollBy(0, 300)")
                    page.wait_for_timeout(500)
                else:
                    break
            except Exception:
                break

        page.remove_listener("response", on_response)

        for item in api_data:
            pid = str(item.get("id", ""))
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)

            locations = item.get("workLocations", [])
            loc_str = ", ".join(locations) if locations else ""

            job = JobPosting(
                job_id=pid,
                platform="taotian",
                title=item.get("name", ""),
                company="淘天集团",
                department=item.get("departmentName", ""),
                location=loc_str,
                experience=item.get("workYear", ""),
                education=item.get("degree", ""),
                description=item.get("description", ""),
                requirements=item.get("requirement", ""),
                url=DETAIL_URL_TEMPLATE.format(pid=pid),
                publish_date=str(item.get("publishTime", "")),
            )
            jobs.append(job)

        logger.info("[taotian] '%s': intercepted %d API items, %d new unique", keyword, len(api_data), len(jobs))

        # Fetch details for jobs missing description (limited to save time)
        need_detail = [j for j in jobs if not j.description][:15]
        if need_detail:
            self._enrich_details(page, need_detail)

        return jobs

    def _fetch_jobs_browser(self, page, keyword: str, city: str) -> list[JobPosting]:
        """Not used - scrape() is overridden."""
        return []

    def _enrich_details(self, page, jobs: list[JobPosting]) -> None:
        """Visit detail pages to get full descriptions."""
        for job in jobs:
            detail_data = {}

            def on_detail(response):
                if "position/detail" in response.url and response.status == 200:
                    try:
                        data = response.json()
                        if data.get("success"):
                            detail_data["content"] = data.get("content", {})
                    except Exception:
                        pass

            page.on("response", on_detail)
            try:
                page.goto(job.url, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            page.remove_listener("response", on_detail)

            if "content" in detail_data:
                d = detail_data["content"]
                job.description = d.get("description", "") or job.description
                job.requirements = d.get("requirement", "") or job.requirements
                job.department = d.get("departmentName", "") or job.department
                job.education = d.get("degree", "") or job.education
                job.experience = d.get("workYear", "") or job.experience

        enriched = sum(1 for j in jobs if j.description)
        logger.info("[taotian] enriched %d/%d details", enriched, len(jobs))
