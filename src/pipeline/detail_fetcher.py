"""Detail page fetching layer.

Architecture
------------
list scrape (batch, fast) → filter_strict → detail fetch (targeted, slower)

Only jobs that pass the strict filter are worth fetching full JD text for,
avoiding wasted requests on irrelevant postings.

Two fetcher types
-----------------
* HTTP fetchers  (_REGISTRY)   – per-job, run in a thread pool.
                                  Suitable for public JSON APIs (Tencent, Baidu).
* Playwright batch fetchers    – per-platform, share ONE browser session.
  (_BATCH_REGISTRY)              Suitable for SPA sites (ByteDance, Didi, XHS).

Usage
-----
    from src.pipeline.detail_fetcher import enrich_with_details
    filtered = filter_strict(raw_jobs)
    enriched = enrich_with_details(filtered)   # mutates in-place
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.models import JobPosting

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────────
HttpFetcher = Callable[[JobPosting], "str | None"]
BatchFetcher = Callable[["list[JobPosting]"], None]

_REGISTRY:       dict[str, HttpFetcher]  = {}
_BATCH_REGISTRY: dict[str, BatchFetcher] = {}


def register_detail_fetcher(platform: str, fn: HttpFetcher) -> None:
    """Register an HTTP-based single-job detail fetcher."""
    _REGISTRY[platform] = fn


def register_batch_fetcher(platform: str, fn: BatchFetcher) -> None:
    """Register a Playwright-based batch fetcher for a platform."""
    _BATCH_REGISTRY[platform] = fn


# ── HTTP fetchers (Tencent & Baidu) ──────────────────────────────────────────

def _fetch_tencent_detail(job: JobPosting) -> str | None:
    import urllib.request
    try:
        url = (
            f"https://careers.tencent.com/tencentcareer/api/post/ByPostId"
            f"?postId={job.job_id}&language=zh-cn"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        post = data.get("Data", {})
        parts = [post.get("Responsibility", ""), post.get("Requirement", "")]
        return "\n".join(p for p in parts if p).strip() or None
    except Exception as exc:
        logger.debug("[tencent] detail fail %s: %s", job.job_id, exc)
        return None


def _fetch_baidu_detail(job: JobPosting) -> str | None:
    import urllib.request
    try:
        url = (
            f"https://talent.baidu.com/httprequest/getData/getPositionDetail"
            f"?recruitType=SOCIAL&id={job.job_id}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        item = data.get("data", {})
        parts = [item.get("workContent", ""), item.get("workRequire", "")]
        return "\n".join(p for p in parts if p).strip() or None
    except Exception as exc:
        logger.debug("[baidu] detail fail %s: %s", job.job_id, exc)
        return None


register_detail_fetcher("tencent", _fetch_tencent_detail)
register_detail_fetcher("baidu",   _fetch_baidu_detail)


# ── Playwright batch fetcher helper ──────────────────────────────────────────

def _playwright_batch_fetch(
    jobs: list[JobPosting],
    platform: str,
    extract_js: str,
    url_fn: Callable[[JobPosting], str],
    delay: float = 1.5,
    timeout: int = 15000,
) -> None:
    """Visit each job's detail page in a single shared browser, extract JD text.

    Mutates jobs in-place.  Jobs that already have descriptions are skipped.
    """
    pending = [j for j in jobs if len((j.description or "") + (j.requirements or "")) < 50]
    if not pending:
        return

    logger.info("[%s] Playwright detail fetch for %d jobs", platform, len(pending))
    from src.scrapers.browser_base import playwright_page

    with playwright_page() as page:
        for job in pending:
            try:
                url = url_fn(job)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(int(delay * 1000))
                raw = page.evaluate(extract_js)
                text = str(raw).strip() if raw else ""
                if len(text) > 30:
                    job.description = text[:3000]
                    logger.debug("[%s] enriched: %s", platform, job.title[:50])
            except Exception:
                logger.debug("[%s] detail skip %s", platform, job.job_id, exc_info=True)
            time.sleep(0.5)


# ── ByteDance ─────────────────────────────────────────────────────────────────

_JS_BYTEDANCE = """
(function() {
  var selectors = [
    '[class*="JobDetailBody"]',
    '[class*="detail-body"]',
    '[class*="position-detail"]',
    '[class*="job-content"]',
    '[class*="position-content"]',
    'article'
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = document.querySelector(selectors[i]);
    if (el && el.innerText && el.innerText.trim().length > 100) {
      return el.innerText.trim().substring(0, 3000);
    }
  }
  return '';
})()
"""


def _batch_bytedance(jobs: list[JobPosting]) -> None:
    _playwright_batch_fetch(
        jobs,
        platform="bytedance",
        extract_js=_JS_BYTEDANCE,
        url_fn=lambda j: j.url or f"https://jobs.bytedance.com/experienced/position/{j.job_id}/detail",
        delay=2.0,
    )


register_batch_fetcher("bytedance", _batch_bytedance)


# ── Didi ─────────────────────────────────────────────────────────────────────

_JS_DIDI = """
(function() {
  var selectors = [
    '.job-detail', '.jd-content', '.position-detail',
    '[class*="job-desc"]', '[class*="detail-content"]',
    '[class*="position-info"]', 'article', '.content-wrap'
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = document.querySelector(selectors[i]);
    if (el && el.innerText && el.innerText.trim().length > 100) {
      return el.innerText.trim().substring(0, 3000);
    }
  }
  return document.body.innerText.trim().substring(0, 2000);
})()
"""


def _batch_didi(jobs: list[JobPosting]) -> None:
    _playwright_batch_fetch(
        jobs,
        platform="didi",
        extract_js=_JS_DIDI,
        url_fn=lambda j: j.url or f"https://talent.didiglobal.com/social/p/{j.job_id}",
        delay=2.0,
    )


register_batch_fetcher("didi", _batch_didi)


# ── Xiaohongshu ───────────────────────────────────────────────────────────────
# XHS detail page exposes data via a JSON API intercepted as a response,
# but the DOM also has structured blocks we can extract directly.

_JS_XHS = """
(function() {
  var selectors = [
    '[class*="description"]', '[class*="position-info"]',
    '[class*="job-detail"]', '[class*="detail-content"]',
    '.job-desc', '.position-desc'
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = document.querySelector(selectors[i]);
    if (el && el.innerText && el.innerText.trim().length > 50) {
      return el.innerText.trim().substring(0, 3000);
    }
  }
  return '';
})()
"""


def _batch_xiaohongshu(jobs: list[JobPosting]) -> None:
    _playwright_batch_fetch(
        jobs,
        platform="xiaohongshu",
        extract_js=_JS_XHS,
        url_fn=lambda j: j.url or f"https://job.xiaohongshu.com/social/position/{j.job_id}",
        delay=2.0,
    )


register_batch_fetcher("xiaohongshu", _batch_xiaohongshu)


# ── 京东 ──────────────────────────────────────────────────────────────────────

_JS_JD = """
(function() {
  var selectors = [
    '[class*="job-desc"]', '[class*="position-desc"]',
    '[class*="jd-content"]', '[class*="detail-info"]',
    '.job-detail', 'article', '[class*="content-main"]'
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = document.querySelector(selectors[i]);
    if (el && el.innerText && el.innerText.trim().length > 100) {
      return el.innerText.trim().substring(0, 3000);
    }
  }
  return '';
})()
"""


def _batch_jd(jobs: list[JobPosting]) -> None:
    _playwright_batch_fetch(
        jobs,
        platform="jd",
        extract_js=_JS_JD,
        url_fn=lambda j: j.url or f"https://zhaopin.jd.com/web/job/job_info/{j.job_id}",
        delay=2.0,
    )


register_batch_fetcher("jd", _batch_jd)


# ── 华为 ──────────────────────────────────────────────────────────────────────

_JS_HUAWEI = """
(function() {
  var selectors = [
    '[class*="job-detail"]', '[class*="position-info"]',
    '[class*="detail-content"]', '[class*="jd-desc"]',
    '.recruit-detail', 'article', '[class*="content-area"]'
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = document.querySelector(selectors[i]);
    if (el && el.innerText && el.innerText.trim().length > 100) {
      return el.innerText.trim().substring(0, 3000);
    }
  }
  return '';
})()
"""


def _batch_huawei(jobs: list[JobPosting]) -> None:
    _playwright_batch_fetch(
        jobs,
        platform="huawei",
        extract_js=_JS_HUAWEI,
        url_fn=lambda j: j.url or (
            f"https://career.huawei.com/reccampportal/portal5/"
            f"social-recruitment-detail.html?jobId={j.job_id}"
        ),
        delay=2.5,
    )


register_batch_fetcher("huawei", _batch_huawei)


# ── Public API ────────────────────────────────────────────────────────────────

def enrich_with_details(
    jobs: list[JobPosting],
    http_max_workers: int = 4,
    min_desc_len: int = 50,
) -> list[JobPosting]:
    """Enrich jobs with missing descriptions using platform-specific detail fetchers.

    Processing strategy:
    - Batch-registry platforms (Playwright): one browser per platform, sequential.
    - HTTP-registry platforms: thread pool, parallel.
    - Platforms with no registered fetcher: skipped.

    Returns the same list (mutated in-place).
    """
    needs_fetch = [
        j for j in jobs
        if len((j.description or "") + (j.requirements or "")) < min_desc_len
        and (j.platform in _REGISTRY or j.platform in _BATCH_REGISTRY)
    ]
    if not needs_fetch:
        logger.info("Detail enrichment: all jobs have sufficient descriptions or no fetcher")
        return jobs

    logger.info(
        "Detail enrichment: %d/%d jobs need enrichment across platforms: %s",
        len(needs_fetch), len(jobs),
        list({j.platform for j in needs_fetch}),
    )

    # ── Playwright batch fetchers (per platform, serial) ──────────────────────
    by_platform: dict[str, list[JobPosting]] = defaultdict(list)
    for job in needs_fetch:
        if job.platform in _BATCH_REGISTRY:
            by_platform[job.platform].append(job)

    for platform, platform_jobs in by_platform.items():
        try:
            _BATCH_REGISTRY[platform](platform_jobs)
        except Exception:
            logger.warning("[%s] batch detail fetch failed", platform, exc_info=True)

    # ── HTTP fetchers (thread pool) ────────────────────────────────────────────
    http_jobs = [j for j in needs_fetch if j.platform in _REGISTRY]
    if http_jobs:
        def _enrich_one(job: JobPosting) -> None:
            try:
                text = _REGISTRY[job.platform](job)
                if text:
                    job.description = text
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=http_max_workers) as pool:
            futures = [pool.submit(_enrich_one, j) for j in http_jobs]
            for f in as_completed(futures):
                f.result(timeout=30)

    enriched = sum(
        1 for j in needs_fetch
        if len((j.description or "") + (j.requirements or "")) >= min_desc_len
    )
    logger.info("Detail enrichment complete: %d/%d enriched", enriched, len(needs_fetch))
    return jobs
