from __future__ import annotations

import logging

from src.models import JobPosting
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://hr.163.com/api/hr163/position/queryPage"


class NeteaseScraper(BaseScraper):
    """网易 - POST API returns structured JSON."""

    @property
    def platform_name(self) -> str:
        return "netease"

    def _fetch_jobs(self, keyword: str, city: str) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        page = 1
        max_pages = 3

        while page <= max_pages:
            payload = {
                "keyword": keyword,
                "currentPage": page,
                "pageSize": 20,
            }
            if city:
                payload["workPlaceName"] = city

            try:
                resp = self._request_with_retry(
                    "POST", API_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Referer": "https://hr.163.com/",
                        "Origin": "https://hr.163.com",
                    },
                )
                data = resp.json()
            except Exception:
                logger.warning("[netease] request failed for %s @ %s page %d", keyword, city, page)
                break

            if data.get("code") != 200:
                logger.info("[netease] code=%s msg=%s", data.get("code"), data.get("msg"))
                break

            records = data.get("data", {}).get("list", [])
            if not records:
                break

            for item in records:
                work_places = item.get("workPlaceNameList", [])
                location = ", ".join(work_places) if work_places else ""

                job = JobPosting(
                    job_id=str(item.get("id", "")),
                    platform="netease",
                    title=item.get("name", ""),
                    company="网易",
                    department=item.get("firstDepName", ""),
                    location=location,
                    experience=item.get("reqWorkYearsName", ""),
                    education=item.get("reqEducationName", ""),
                    description=item.get("description", ""),
                    requirements=item.get("requirement", ""),
                    url=item.get("beeUrl", f"https://hr.163.com/job-detail.html?id={item.get('id', '')}"),
                    publish_date=item.get("updateTime", ""),
                    category=item.get("firstPostTypeName", ""),
                )
                jobs.append(job)

            total = data.get("data", {}).get("total", 0)
            if page * 20 >= total:
                break
            page += 1

        return jobs
