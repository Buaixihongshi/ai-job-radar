"""ByteDance job scraper using bb-browser.

Two strategies, attempted in order:

1. **API path** — runs ``adapters/bytedance/search.js`` in the active tab via
   ``bb_run_adapter``. The adapter directly hits jobs.bytedance.com's internal
   ``/api/v1/search/position`` endpoint, returning structured fields
   (description, requirements, …) and supporting offset-based pagination.

2. **DOM fallback** — original behaviour: navigate the search results page and
   extract job cards from the rendered DOM. Used when the API path returns no
   data (e.g. internal API changed) so the scraper always degrades gracefully.

Both paths require the user's real Chrome to be open and logged in to
jobs.bytedance.com so that cookies and same-origin policy let us reach the
internal API.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from pathlib import Path

from src.models import JobPosting
from src.scrapers.bb_base import bb_eval, bb_is_available, bb_run_adapter

logger = logging.getLogger(__name__)

BASE_URL = "https://jobs.bytedance.com/experienced/position"
ADAPTER_PATH = Path(__file__).parent.parent.parent / "adapters" / "bytedance" / "search.js"

# DOM strategy
PAGE_SIZE = 10
MAX_PAGES = 8

# API strategy (adapter)
API_PAGE_SIZE = 50
API_MAX_PAGES = 6

KEYWORDS = [
    "大模型测试", "AI测试", "算法测试", "测试开发",
    "Agent产品", "AIGC产品", "AI策略产品",
    "大模型评测", "Agent开发", "AI质量", "智能测试",
]

JS_EXTRACT = r"""
(function() {
  var jobs = [];
  var links = document.querySelectorAll('a[href*="/position/"]');
  for (var i = 0; i < links.length; i++) {
    var a = links[i];
    var href = a.getAttribute('href') || '';
    var m = href.match(/\/experienced\/position\/(\w+)/);
    if (!m) continue;
    var pid = m[1];
    var allSpans = a.querySelectorAll('span');
    var title = '';
    for (var s = 0; s < allSpans.length; s++) {
      var txt = allSpans[s].textContent.trim();
      if (txt.length > 3) { title = txt; break; }
    }
    var full = a.textContent;
    var cm = full.match(/(北京|上海|深圳|杭州|成都|广州|武汉|西安|南京|苏州|天津|重庆)/);
    var city = cm ? cm[1] : '';
    var dm = full.match(/(研发|产品|运营|市场|销售|设计|游戏策划|职能|教研教学)\s*-\s*([\u4e00-\u9fa5A-Za-z]+)/);
    var dept = dm ? dm[0] : '';
    var idm = full.match(/职位\s*ID[：:]\s*(\w+)/);
    var jid = idm ? idm[1] : pid;
    if (title) {
      jobs.push({id: pid, jid: jid, title: title, city: city, dept: dept,
                 url: 'https://jobs.bytedance.com/experienced/position/' + pid + '/detail'});
    }
  }
  return JSON.stringify({count: jobs.length, jobs: jobs});
})()
"""


def _build_url(keyword: str, page: int = 1) -> str:
    params = urllib.parse.urlencode({
        "keywords": keyword,
        "category": "",
        "location": "",
        "project": "",
        "type": "",
        "job_hot_flag": "",
        "current": page,
        "limit": PAGE_SIZE,
    })
    return f"{BASE_URL}?{params}"


def _navigate(url: str, wait: float = 3.0) -> bool:
    """Navigate active tab without opening a new tab."""
    try:
        bb_eval(f"window.location.href = '{url}'", timeout=5)
        time.sleep(wait)
        cur = bb_eval("window.location.href", timeout=5)
        return isinstance(cur, str) and "bytedance" in cur
    except RuntimeError:
        return False


def _extract_page() -> list[dict]:
    """Run JS extraction on the current page (DOM fallback path)."""
    data = bb_eval(JS_EXTRACT, timeout=10)
    if isinstance(data, dict):
        return data.get("jobs", [])
    if isinstance(data, str):
        import json
        try:
            parsed = json.loads(data)
            return parsed.get("jobs", [])
        except Exception:
            pass
    return []


def _has_pagination() -> int:
    """Return total page count detected from pagination widget, or 0."""
    result = bb_eval(
        r"""
        (function() {
          var items = document.querySelectorAll('li[class*="page"], ul li');
          var maxP = 0;
          for (var i = 0; i < items.length; i++) {
            var n = parseInt(items[i].textContent.trim());
            if (!isNaN(n) && n > maxP) maxP = n;
          }
          return maxP;
        })()
        """,
        timeout=5,
    )
    return int(result) if result else 0


def _ensure_on_bytedance() -> bool:
    """Ensure active tab is on jobs.bytedance.com."""
    for attempt in range(3):
        try:
            url = bb_eval("window.location.href", timeout=5)
            if isinstance(url, str) and "bytedance" in url:
                return True
            logger.info("[bytedance_bb] navigating to bytedance (attempt %d)", attempt + 1)
            bb_eval(f"window.location.href = '{BASE_URL}'", timeout=5)
            time.sleep(8)
            url = bb_eval("window.location.href", timeout=5)
            if isinstance(url, str) and "bytedance" in url:
                return True
        except RuntimeError:
            time.sleep(3)
    return False


# ── API path via adapter ─────────────────────────────────────────────────────


def _fetch_via_api(keyword: str, page: int, page_size: int = API_PAGE_SIZE) -> list[dict]:
    """Fetch one page of results by running the JS adapter in the active tab.

    Returns a list of normalized dicts with keys
    ``jobId / title / department / city / description / requirements / url``.
    Empty list on error or when adapter signals failure.
    """
    offset = (page - 1) * page_size
    try:
        result = bb_run_adapter(
            ADAPTER_PATH,
            {"keyword": keyword, "limit": page_size, "offset": offset},
            timeout=20,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        logger.debug("[bytedance_bb] adapter call failed for %s: %s", keyword, exc)
        return []

    if not isinstance(result, dict):
        return []
    if "error" in result:
        logger.debug("[bytedance_bb] adapter reported error: %s", result.get("error"))
        return []

    jobs = result.get("jobs", [])
    return jobs if isinstance(jobs, list) else []


def _store_api_jobs(items: list[dict], all_jobs: dict[str, JobPosting]) -> int:
    """Convert adapter items into JobPosting and merge into *all_jobs*. Returns new count."""
    new_count = 0
    for it in items:
        pid = str(it.get("jobId", ""))
        if not pid or pid in all_jobs:
            continue
        all_jobs[pid] = JobPosting(
            job_id=pid,
            platform="bytedance",
            title=it.get("title", ""),
            company="字节跳动",
            department=it.get("department", ""),
            location=it.get("city", ""),
            description=it.get("description", ""),
            requirements=it.get("requirements", ""),
            url=it.get("url", ""),
        )
        new_count += 1
    return new_count


# ── DOM path (legacy fallback) ───────────────────────────────────────────────


def _scrape_keyword_via_dom(kw: str, all_jobs: dict[str, JobPosting]) -> int:
    """DOM fallback: navigate paginated search and extract from cards. Returns total new."""
    if not _navigate(_build_url(kw, 1), wait=3.0):
        logger.warning("[bytedance_bb][dom] nav failed for keyword=%s", kw)
        return 0

    total_pages = min(_has_pagination() or 1, MAX_PAGES)
    logger.info("[bytedance_bb][dom] kw=%s total_pages=%d (capped %d)", kw, total_pages, MAX_PAGES)

    total_new = 0
    for page in range(1, total_pages + 1):
        if page > 1:
            if not _navigate(_build_url(kw, page), wait=2.0):
                logger.warning("[bytedance_bb][dom] page %d nav failed", page)
                break

        items = _extract_page()
        if not items:
            logger.info("[bytedance_bb][dom] page %d: no items, stopping", page)
            break

        new_count = 0
        for item in items:
            pid = str(item.get("id", ""))
            if not pid or pid in all_jobs:
                continue
            all_jobs[pid] = JobPosting(
                job_id=pid,
                platform="bytedance",
                title=item.get("title", ""),
                company="字节跳动",
                department=item.get("dept", ""),
                location=item.get("city", ""),
                url=item.get("url", ""),
            )
            new_count += 1
        total_new += new_count

        logger.info("[bytedance_bb][dom] kw=%s page=%d fetched=%d new=%d total=%d",
                    kw, page, len(items), new_count, len(all_jobs))
    return total_new


# ── Orchestrator ─────────────────────────────────────────────────────────────


def scrape_bytedance() -> list[JobPosting]:
    if not bb_is_available():
        logger.warning("[bytedance_bb] bb-browser not available, skipping")
        return []

    if not _ensure_on_bytedance():
        logger.error("[bytedance_bb] cannot navigate to ByteDance, skipping")
        return []

    all_jobs: dict[str, JobPosting] = {}
    use_api = ADAPTER_PATH.exists()
    api_first_failed = False

    for kw in KEYWORDS:
        logger.info("[bytedance_bb] keyword=%s", kw)

        if use_api and not api_first_failed:
            page = 1
            api_jobs = _fetch_via_api(kw, page=page)
            if not api_jobs:
                logger.info("[bytedance_bb][api] empty for kw=%s; will retry next kw", kw)
                if not all_jobs:
                    logger.info("[bytedance_bb] API path returned no data on first keyword; "
                                "switching to DOM fallback for the rest")
                    api_first_failed = True
            else:
                while api_jobs and page <= API_MAX_PAGES:
                    new_count = _store_api_jobs(api_jobs, all_jobs)
                    logger.info("[bytedance_bb][api] kw=%s page=%d fetched=%d new=%d total=%d",
                                kw, page, len(api_jobs), new_count, len(all_jobs))
                    if new_count == 0 or len(api_jobs) < API_PAGE_SIZE:
                        break
                    page += 1
                    api_jobs = _fetch_via_api(kw, page=page)
                time.sleep(1)
                continue

        _scrape_keyword_via_dom(kw, all_jobs)
        time.sleep(1)

    logger.info("[bytedance_bb] done, total unique jobs: %d", len(all_jobs))
    return list(all_jobs.values())
