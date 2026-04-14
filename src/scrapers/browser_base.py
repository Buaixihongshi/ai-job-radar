from __future__ import annotations

import logging
import os
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path

from src.models import JobPosting

logger = logging.getLogger(__name__)

COOKIES_DIR = Path(__file__).parent.parent.parent / "data" / ".cookies"


class BrowserScraper(ABC):
    """Base class for Playwright-based scrapers (Tier 3).

    Launches a headless Chromium browser with stealth patches.
    Subclasses implement _fetch_jobs_browser(page, keyword, city).
    """

    def __init__(self, config: dict):
        self.config = config
        self._pw = None
        self._browser = None
        self._context = None

    def _get_cookie_path(self) -> Path:
        COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        return COOKIES_DIR / f"{self.platform_name}.json"

    def _launch(self):
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        stealth = Stealth()
        self._pw = sync_playwright().start()

        proxy = os.environ.get("PROXY_URL")
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }
        if proxy:
            launch_args["proxy"] = {"server": proxy}

        self._browser = self._pw.chromium.launch(**launch_args)

        cookie_path = self._get_cookie_path()
        context_args = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if cookie_path.exists():
            context_args["storage_state"] = str(cookie_path)

        self._context = self._browser.new_context(**context_args)
        page = self._context.new_page()
        stealth.apply_stealth_sync(page)
        return page

    def _save_cookies(self) -> None:
        if self._context:
            try:
                self._context.storage_state(path=str(self._get_cookie_path()))
            except Exception:
                logger.debug("[%s] failed to save cookies", self.platform_name)

    @property
    @abstractmethod
    def platform_name(self) -> str: ...

    @abstractmethod
    def _fetch_jobs_browser(self, page, keyword: str, city: str) -> list[JobPosting]: ...

    def scrape(self) -> list[JobPosting]:
        all_jobs: list[JobPosting] = []
        keywords = self.config.get("keywords", [])
        cities = self.config.get("cities", [])

        page = None
        try:
            page = self._launch()
        except Exception:
            logger.error("[%s] browser launch failed", self.platform_name, exc_info=True)
            return all_jobs

        try:
            for kw in keywords:
                for city in cities:
                    try:
                        logger.info("[%s] browser search: %s @ %s", self.platform_name, kw, city)
                        jobs = self._fetch_jobs_browser(page, kw, city)
                        all_jobs.extend(jobs)
                        time.sleep(random.uniform(3.0, 6.0))
                    except Exception:
                        logger.warning(
                            "[%s] %s @ %s failed",
                            self.platform_name, kw, city,
                            exc_info=True,
                        )
        finally:
            self._save_cookies()

        logger.info("[%s] total raw results: %d", self.platform_name, len(all_jobs))
        return all_jobs

    def close(self) -> None:
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
