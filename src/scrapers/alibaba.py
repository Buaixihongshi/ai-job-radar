from __future__ import annotations

import logging
import re

from src.models import JobPosting
from src.scrapers.browser_base import BrowserScraper

logger = logging.getLogger(__name__)

SEARCH_URL = "https://talent.alibaba.com/off-campus/position-list?keyword={keyword}"


class AlibabaScraper(BrowserScraper):
    """Alibaba career site - requires Playwright due to anti-bot."""

    @property
    def platform_name(self) -> str:
        return "alibaba"

    def _fetch_jobs_browser(self, page, keyword: str, city: str) -> list[JobPosting]:
        url = SEARCH_URL.format(keyword=keyword)
        jobs: list[JobPosting] = []

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
        except Exception:
            logger.warning("[alibaba] page load failed for %s", keyword)
            return jobs

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(800)

        cards = page.query_selector_all(
            "[class*='position-item'], [class*='PositionItem'], "
            "[class*='job-card'], [class*='JobCard'], "
            "div[class*='list-item']"
        )

        if not cards:
            cards = page.query_selector_all("a[href*='position-detail']")

        for card in cards:
            try:
                title_el = card.query_selector(
                    "[class*='title'], [class*='name'], h3, h4"
                )
                dept_el = card.query_selector(
                    "[class*='department'], [class*='team'], [class*='bu-name']"
                )
                loc_el = card.query_selector(
                    "[class*='city'], [class*='location']"
                )
                exp_el = card.query_selector(
                    "[class*='experience'], [class*='require']"
                )

                title = title_el.inner_text().strip() if title_el else card.inner_text().strip().split("\n")[0]
                dept = dept_el.inner_text().strip() if dept_el else ""
                location = loc_el.inner_text().strip() if loc_el else ""
                experience = exp_el.inner_text().strip() if exp_el else ""

                if not title or len(title) < 2:
                    continue

                href = card.get_attribute("href") or ""
                if not href:
                    link_el = card.query_selector("a[href*='position']")
                    href = link_el.get_attribute("href") if link_el else ""

                if city and city not in location and location:
                    continue

                jid_match = re.search(r'positionId=(\w+)', href)
                job_id = jid_match.group(1) if jid_match else title[:20]
                full_url = f"https://talent.alibaba.com{href}" if href and not href.startswith("http") else href

                job = JobPosting(
                    job_id=job_id,
                    platform="alibaba",
                    title=title,
                    company="阿里巴巴",
                    department=dept,
                    location=location,
                    experience=experience,
                    url=full_url,
                )
                jobs.append(job)
            except Exception:
                continue

        return jobs
