from __future__ import annotations

import logging
import re
import time

from src.models import JobPosting
from src.scrapers.browser_base import BrowserScraper

logger = logging.getLogger(__name__)

SEARCH_URL = "https://jobs.bytedance.com/experienced/position?keyword={keyword}&limit=30&offset=0"


class BytedanceScraper(BrowserScraper):
    """ByteDance career site - SPA, requires Playwright."""

    @property
    def platform_name(self) -> str:
        return "bytedance"

    def _fetch_jobs_browser(self, page, keyword: str, city: str) -> list[JobPosting]:
        url = SEARCH_URL.format(keyword=keyword)
        jobs: list[JobPosting] = []

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
        except Exception:
            logger.warning("[bytedance] page load failed for %s", keyword)
            return jobs

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(800)

        cards = page.query_selector_all(
            "[class*='JobCard'], [class*='job-card'], "
            "[class*='position-item'], [class*='PositionItem'], "
            "li[class*='list-item']"
        )

        if not cards:
            cards = page.query_selector_all("a[href*='/position/']")

        for card in cards:
            try:
                title_el = card.query_selector(
                    "[class*='title'], [class*='name'], h3, h4"
                )
                dept_el = card.query_selector(
                    "[class*='department'], [class*='team'], [class*='category']"
                )
                loc_el = card.query_selector(
                    "[class*='city'], [class*='location'], [class*='address']"
                )

                title = title_el.inner_text().strip() if title_el else card.inner_text().strip().split("\n")[0]
                dept = dept_el.inner_text().strip() if dept_el else ""
                location = loc_el.inner_text().strip() if loc_el else ""

                if not title or len(title) < 2:
                    continue

                href = card.get_attribute("href") or ""
                if not href:
                    link_el = card.query_selector("a[href*='/position/']")
                    href = link_el.get_attribute("href") if link_el else ""

                if city and city not in location and location:
                    continue

                jid_match = re.search(r'/position/(\d+)', href)
                job_id = jid_match.group(1) if jid_match else title[:20]
                full_url = f"https://jobs.bytedance.com{href}" if href and not href.startswith("http") else href

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

        return jobs
