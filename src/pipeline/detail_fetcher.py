"""Detail page fetching layer.

Architecture:
  list scrape (batch, fast) → filter_strict → detail fetch (targeted, slower)

Only jobs that pass the strict filter are worth fetching full JD text for,
avoiding wasted requests on irrelevant postings.

Usage
-----
    from src.pipeline.detail_fetcher import enrich_with_details
    filtered = filter_strict(raw_jobs)
    enriched = enrich_with_details(filtered, max_workers=3)

Each platform can register a custom detail-fetch function.  If no function is
registered the job is returned as-is (already has enough data from list page).
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.models import JobPosting

logger = logging.getLogger(__name__)

# ── Platform-specific detail fetchers ────────────────────────────────────────
# Signature: (job: JobPosting) -> str | None
#   Return full JD text, or None if fetch failed / not needed.
DetailFetcher = Callable[[JobPosting], "str | None"]

_REGISTRY: dict[str, DetailFetcher] = {}


def register_detail_fetcher(platform: str, fn: DetailFetcher) -> None:
    """Register a detail-fetch function for a platform."""
    _REGISTRY[platform] = fn


def _fetch_tencent_detail(job: JobPosting) -> str | None:
    """Fetch Tencent job detail via their public API."""
    try:
        import urllib.request
        import json
        job_id = job.job_id
        url = (
            f"https://careers.tencent.com/tencentcareer/api/post/ByPostId"
            f"?postId={job_id}&language=zh-cn"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        post = data.get("Data", {})
        parts = [
            post.get("Responsibility", ""),
            post.get("Requirement", ""),
        ]
        return "\n".join(p for p in parts if p).strip() or None
    except Exception as exc:
        logger.debug("[tencent] detail fetch failed for %s: %s", job.job_id, exc)
        return None


def _fetch_baidu_detail(job: JobPosting) -> str | None:
    """Fetch Baidu job detail via their public API."""
    try:
        import urllib.request
        import json
        url = (
            f"https://talent.baidu.com/httprequest/getData/getPositionDetail"
            f"?recruitType=SOCIAL&id={job.job_id}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        item = data.get("data", {})
        parts = [
            item.get("workContent", ""),
            item.get("workRequire", ""),
        ]
        return "\n".join(p for p in parts if p).strip() or None
    except Exception as exc:
        logger.debug("[baidu] detail fetch failed for %s: %s", job.job_id, exc)
        return None


# Register built-in fetchers
register_detail_fetcher("tencent", _fetch_tencent_detail)
register_detail_fetcher("baidu", _fetch_baidu_detail)


# ── Public API ────────────────────────────────────────────────────────────────

def _enrich_one(job: JobPosting, min_desc_len: int = 50) -> JobPosting:
    """Fetch detail for a single job if needed."""
    existing_len = len((job.description or "") + (job.requirements or ""))
    if existing_len >= min_desc_len:
        return job  # already has sufficient content

    fetcher = _REGISTRY.get(job.platform)
    if not fetcher:
        return job

    try:
        text = fetcher(job)
        if text:
            job.description = text
            logger.debug("[%s] detail enriched: %s", job.platform, job.title[:40])
    except Exception:
        logger.debug("[%s] detail fetch error for %s", job.platform, job.job_id, exc_info=True)
    return job


def enrich_with_details(
    jobs: list[JobPosting],
    max_workers: int = 4,
    delay_between: float = 0.5,
    min_desc_len: int = 50,
) -> list[JobPosting]:
    """Enrich jobs that have insufficient descriptions with full JD from detail pages.

    Args:
        jobs:          Jobs to enrich (should already be filtered).
        max_workers:   Thread pool size for concurrent fetches.
        delay_between: Sleep between fetches to avoid rate-limiting.
        min_desc_len:  Skip jobs that already have >= this many chars.

    Returns the same list (mutated in-place).
    """
    needs_fetch = [j for j in jobs if len((j.description or "") + (j.requirements or "")) < min_desc_len]
    if not needs_fetch:
        logger.info("Detail enrichment: all %d jobs already have sufficient descriptions", len(jobs))
        return jobs

    logger.info("Detail enrichment: fetching for %d/%d jobs", len(needs_fetch), len(jobs))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_enrich_one, job, min_desc_len): job for job in needs_fetch}
        done = 0
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
            done += 1
            if delay_between and done < len(needs_fetch):
                time.sleep(delay_between)

    logger.info("Detail enrichment complete")
    return jobs
