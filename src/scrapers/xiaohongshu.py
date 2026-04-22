"""Scraper for job.xiaohongshu.com (小红书招聘).

Uses Playwright for DOM extraction + API interception for detail pages.
"""
from __future__ import annotations

import logging
import re

from src.models import JobPosting

logger = logging.getLogger(__name__)

KEYWORDS = ["测试", "AI", "Agent", "评测", "质量", "算法", "大模型", "AIGC", "LLM"]

# Common field names seen in XHS position detail API responses
_DESC_KEYS  = ["positionDesc", "description", "jobDescription", "content", "duty", "responsibility"]
_REQ_KEYS   = ["positionReq", "requirement", "jobRequirement", "qualification", "serviceCondition"]
_EXP_KEYS   = ["workYear", "workExperience", "experience", "experienceRequire"]
_EDU_KEYS   = ["education", "educationRequire", "degree"]


def _pick(data: dict, keys: list[str]) -> str:
    for k in keys:
        v = data.get(k, "")
        if v and isinstance(v, str) and len(v.strip()) > 3:
            return v.strip()
    return ""


def scrape_xiaohongshu() -> list[JobPosting]:
    from src.scrapers.browser_base import playwright_page

    all_jobs: list[JobPosting] = []
    seen_ids: set[str] = set()

    with playwright_page() as page:
        for kw in KEYWORDS:
            try:
                page.goto(
                    f"https://job.xiaohongshu.com/social/position?positionName={kw}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                page.wait_for_timeout(4000)

                for _ in range(5):
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(1000)

                links = page.query_selector_all("a[href*='/social/position/']")
                new_count = 0
                for link in links:
                    href = link.get_attribute("href") or ""
                    m = re.search(r"/social/position/(\d+)", href)
                    if not m:
                        continue
                    pid = m.group(1)
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    text = link.inner_text().strip()
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    if len(lines) >= 2:
                        title = lines[0]
                        dept  = lines[1] if len(lines) > 1 else ""
                        loc   = lines[2] if len(lines) > 2 else ""
                        all_jobs.append(JobPosting(
                            job_id=pid,
                            platform="xiaohongshu",
                            company="小红书",
                            title=title,
                            department=dept,
                            location=loc,
                            url=f"https://job.xiaohongshu.com/social/position/{pid}",
                        ))
                        new_count += 1
                logger.info("[xiaohongshu] keyword=%s new=%d total=%d", kw, new_count, len(all_jobs))
            except Exception:
                logger.warning("[xiaohongshu] keyword=%s failed", kw, exc_info=True)

        # Fetch details for relevant jobs
        relevant_kw = ["测试", "评测", "质量", "QA", "Agent", "AI", "大模型"]
        relevant = [j for j in all_jobs if any(k in j.title for k in relevant_kw)]
        logger.info("[xiaohongshu] fetching details for %d relevant jobs", len(relevant))

        for j in relevant[:30]:
            try:
                detail_data: dict = {}

                def handler(resp, _dd=detail_data):
                    ct = resp.headers.get("content-type", "")
                    if resp.status == 200 and "json" in ct and "position" in resp.url:
                        try:
                            raw = resp.json()
                            # Unwrap common envelope patterns
                            payload = raw
                            for key in ("data", "result", "content"):
                                if isinstance(payload, dict) and key in payload:
                                    payload = payload[key]
                            if isinstance(payload, dict) and len(payload) > 2:
                                _dd.update(payload)
                        except Exception:
                            pass

                page.on("response", handler)
                page.goto(j.url, wait_until="domcontentloaded", timeout=12000)
                page.wait_for_timeout(2500)
                page.remove_listener("response", handler)

                if detail_data:
                    j.description  = _pick(detail_data, _DESC_KEYS)
                    j.requirements = _pick(detail_data, _REQ_KEYS)
                    j.experience   = _pick(detail_data, _EXP_KEYS)
                    j.education    = _pick(detail_data, _EDU_KEYS)
                    logger.debug("[xiaohongshu] detail ok: %s desc_len=%d",
                                 j.title[:40], len(j.description))

                if not j.description:
                    # Fallback: DOM extraction
                    raw_text = page.evaluate("""
                    (function() {
                        var selectors = [
                            '[class*="description"]', '[class*="position-info"]',
                            '[class*="job-detail"]', '.job-desc', '.position-desc',
                            '[class*="detail-content"]'
                        ];
                        for (var i = 0; i < selectors.length; i++) {
                            var el = document.querySelector(selectors[i]);
                            if (el && el.innerText && el.innerText.trim().length > 50) {
                                return el.innerText.trim().substring(0, 3000);
                            }
                        }
                        return '';
                    })()
                    """)
                    if raw_text and len(raw_text.strip()) > 50:
                        j.description = raw_text.strip()

            except Exception:
                logger.debug("[xiaohongshu] detail skip for %s", j.job_id)

    logger.info("[xiaohongshu] total: %d", len(all_jobs))
    return all_jobs
