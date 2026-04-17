"""DiDi job scraper using bb-browser (DOM-based).

Navigates talent.didiglobal.com in the user's real Chrome, extracts
all job listings by paginating through the Ant Design pagination,
then filters for AI-related positions in Python.
"""
from __future__ import annotations

import logging
import time

from src.models import JobPosting
from src.scrapers.bb_base import bb_eval, bb_is_available

logger = logging.getLogger(__name__)

DIDI_URL = "https://talent.didiglobal.com/social/list/1"
MAX_PAGES = 86

AI_KEYWORDS = [
    "AI", "大模型", "Agent", "LLM", "AIGC", "算法", "机器学习",
    "测试", "QA", "质量", "评测", "自动化", "NLP", "深度学习",
    "智能", "MLOps",
]

JS_EXTRACT = r"""
(function() {
  var jobs = [];
  var links = document.querySelectorAll('a');
  for (var i = 0; i < links.length; i++) {
    var text = links[i].textContent.trim();
    var m = text.match(/^(.+?)\s*\(([A-Z][A-Za-z0-9]+)\)(.+?)\/(.*?)\/(.*)/);
    if (m) {
      var rest = m[5].trim();
      var cityM = rest.match(/^([\u4e00-\u9fa5]+[\u5e02]?)/);
      var href = links[i].getAttribute('href') || '';
      jobs.push({
        title: m[1].trim(),
        jid: m[2],
        dept: m[3].trim(),
        cat: m[4].trim(),
        city: cityM ? cityM[1] : '',
        href: href
      });
    }
  }
  var activePg = '';
  document.querySelectorAll('.ant-pagination-item-active').forEach(function(el) {
    activePg = el.textContent.trim();
  });
  return JSON.stringify({page: activePg, count: jobs.length, jobs: jobs});
})()
"""

JS_CLICK_PAGE = r"""
(function(target) {
  var items = document.querySelectorAll('.ant-pagination-item');
  for (var i = 0; i < items.length; i++) {
    if (items[i].textContent.trim() === String(target)) {
      var link = items[i].querySelector('a');
      if (link) { link.click(); return 'ok'; }
      items[i].click();
      return 'ok';
    }
  }
  var next = document.querySelector('.ant-pagination-next a, .ant-pagination-next');
  if (next) { next.click(); return 'next'; }
  return 'not_found';
})(%d)
"""

JS_CLICK_NEXT = r"""
(function() {
  var next = document.querySelector('.ant-pagination-next a');
  if (next && !next.closest('li').classList.contains('ant-pagination-disabled')) {
    next.click();
    return 'ok';
  }
  return 'disabled';
})()
"""


def _is_ai_related(title: str, dept: str) -> bool:
    text = (title + " " + dept).upper()
    return any(kw.upper() in text for kw in AI_KEYWORDS)


def _navigate_to_didi() -> bool:
    """Ensure the active tab is on DiDi's job listing page."""
    try:
        url = bb_eval("window.location.href", timeout=5)
        if isinstance(url, str) and "didiglobal" in url:
            return True
        bb_eval(f"window.location.href = '{DIDI_URL}'", timeout=5)
        time.sleep(6)
        url = bb_eval("window.location.href", timeout=5)
        return isinstance(url, str) and "didiglobal" in url
    except Exception as e:
        logger.warning("[didi_bb] navigation failed: %s", e)
        return False


def scrape_didi_bb() -> list[JobPosting]:
    if not bb_is_available():
        logger.warning("[didi_bb] bb-browser not available, skipping")
        return []

    if not _navigate_to_didi():
        logger.error("[didi_bb] cannot navigate to DiDi, skipping")
        return []

    all_jobs: dict[str, JobPosting] = {}
    consecutive_empty = 0

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            try:
                if page <= 5:
                    result = bb_eval(JS_CLICK_PAGE % page, timeout=5)
                else:
                    result = bb_eval(JS_CLICK_NEXT, timeout=5)
                if result == "disabled" or result == "not_found":
                    logger.info("[didi_bb] no more pages at page %d", page)
                    break
            except RuntimeError as e:
                logger.warning("[didi_bb] page click failed: %s", e)
                break
            time.sleep(2)

        try:
            data = bb_eval(JS_EXTRACT, timeout=10)
        except RuntimeError as e:
            logger.warning("[didi_bb] extract failed on page %d: %s", page, e)
            break

        if not isinstance(data, dict):
            logger.warning("[didi_bb] unexpected response on page %d", page)
            break

        items = data.get("jobs", [])
        if not items:
            logger.info("[didi_bb] page %d: no items, stopping", page)
            break

        new_count = 0
        for item in items:
            jid = item.get("jid", "")
            if not jid or jid in all_jobs:
                continue

            title = item.get("title", "")
            dept = item.get("dept", "")
            if not _is_ai_related(title, dept):
                continue

            href = item.get("href", "")
            url = f"https://talent.didiglobal.com{href}" if href.startswith("/") else href

            job = JobPosting(
                job_id=jid,
                platform="didi",
                title=title,
                company="滴滴",
                department=dept,
                location=item.get("city", "").rstrip("市"),
                url=url,
            )
            all_jobs[jid] = job
            new_count += 1

        if new_count == 0:
            consecutive_empty += 1
        else:
            consecutive_empty = 0

        logger.info("[didi_bb] page=%d items=%d ai_new=%d total=%d (empty_streak=%d)",
                    page, len(items), new_count, len(all_jobs), consecutive_empty)

        if consecutive_empty >= 15:
            logger.info("[didi_bb] 15 consecutive pages without AI jobs, stopping early")
            break

    logger.info("[didi_bb] done, total AI-related jobs: %d", len(all_jobs))
    return list(all_jobs.values())
