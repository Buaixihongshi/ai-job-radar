from __future__ import annotations

import json
import logging
import re

from src.models import JobPosting
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

SOCIAL_LIST_URL = "https://talent.baidu.com/jobs/social-list"


class BaiduScraper(BaseScraper):
    """Baidu talent site uses SSR."""

    @property
    def platform_name(self) -> str:
        return "baidu"

    def _fetch_jobs(self, keyword: str, city: str) -> list[JobPosting]:
        params = {"search": keyword}
        resp = self._request_with_retry("GET", SOCIAL_LIST_URL, params=params)
        html = resp.text

        jobs = self._parse_nuxt_data(html, city)
        if jobs:
            return jobs
        return self._parse_html(html, keyword, city)

    def _parse_nuxt_data(self, html: str, city: str) -> list[JobPosting]:
        jobs = []

        m = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                props = data.get("props", {}).get("pageProps", {})
                post_list = props.get("postList", props.get("jobs", []))
                for p in post_list:
                    job = self._dict_to_posting(p)
                    if job and (not city or city in job.location):
                        jobs.append(job)
                return jobs
            except (json.JSONDecodeError, KeyError):
                pass

        m2 = re.search(r'window\.__NUXT__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
        if m2:
            try:
                data = json.loads(m2.group(1))
                return self._walk_nuxt_for_posts(data, city)
            except (json.JSONDecodeError, ValueError):
                pass

        return jobs

    def _walk_nuxt_for_posts(self, data: dict, city: str) -> list[JobPosting]:
        jobs = []
        if isinstance(data, dict):
            for key, val in data.items():
                if key in ("postList", "jobList", "list") and isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            job = self._dict_to_posting(item)
                            if job and (not city or city in job.location):
                                jobs.append(job)
                elif isinstance(val, dict):
                    jobs.extend(self._walk_nuxt_for_posts(val, city))
        return jobs

    def _parse_html(self, html: str, keyword: str, city: str) -> list[JobPosting]:
        jobs = []
        simple_pattern = re.compile(r'([^<（]+)（([A-Z]\d+)）')
        for m in simple_pattern.finditer(html):
            raw_title = m.group(1).strip()
            raw_title = re.sub(r'^(?:span|div|a|li|h\d)>', '', raw_title)
            title = raw_title + f"（{m.group(2)}）"
            job = JobPosting(
                job_id=m.group(2),
                platform="baidu",
                title=title,
                company="百度",
                url=f"https://talent.baidu.com/jobs/social-list?search={keyword}",
            )
            if not city or city in html:
                jobs.append(job)
        return jobs

    def _dict_to_posting(self, d: dict) -> JobPosting | None:
        jid = str(d.get("id", d.get("jobId", d.get("postId", ""))))
        if not jid:
            return None
        return JobPosting(
            job_id=jid,
            platform="baidu",
            title=d.get("name", d.get("title", "")),
            company="百度",
            department=d.get("department", d.get("businessGroup", "")),
            location=d.get("city", d.get("location", "")),
            experience=d.get("workYear", ""),
            education=d.get("education", ""),
            description=d.get("description", d.get("responsibility", "")),
            url=f"https://talent.baidu.com/jobs/social-detail/{jid}",
            publish_date=d.get("publishDate", d.get("updateDate", "")),
        )
