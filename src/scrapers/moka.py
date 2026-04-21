"""Scraper for app.mokahr.com (MokaHR 招聘) - 支持多家公司.

DOM 提取：从 [class*=job-description] 元素提取描述，
向上遍历找到包含 a[href*="#/job/"] 的容器获取 ID 和标题。

当前覆盖：DeepSeek（幻方）、Kimi（月之暗面）。
"""
from __future__ import annotations

import json
import logging
import random
import time

from src.models import JobPosting

logger = logging.getLogger(__name__)

# (显示名称, org_slug, org_id)  org_id 为 MokaHR 数字 ID
MOKA_COMPANIES = [
    ("DeepSeek", "high-flyer", "140576"),
    ("Kimi", "moonshot", "148506"),
]

JS_EXTRACT = """
(function() {
  var descEls = document.querySelectorAll('[class*=job-description]');
  var jobs = [];
  for (var i = 0; i < descEls.length; i++) {
    var desc = descEls[i];
    var container = desc.parentElement;
    while (container && container !== document.body) {
      if (container.querySelector('a[href*="#/job/"]')) break;
      container = container.parentElement;
    }
    if (!container || container === document.body) continue;

    var aEl = container.querySelector('a[href*="#/job/"]');
    var href = aEl ? aEl.getAttribute('href') : '';
    var jidMatch = href.match(/#\\/job\\/([^?&]+)/);
    var jid = jidMatch ? jidMatch[1] : '';

    var fullText = container.innerText || '';
    var lines = fullText.split('\\n').filter(function(l) { return l.trim(); });
    var title = lines[0] ? lines[0].trim().substring(0, 80) : '';

    var cityMatch = fullText.match(
      /\\u5317\\u4eac|\\u4e0a\\u6d77|\\u6df1\\u5733|\\u676d\\u5dde|\\u5e7f\\u5dde|\\u6210\\u90fd|\\u6b66\\u6c49|\\u5357\\u4eac|\\u897f\\u5b89|\\u91cd\\u5e86|\\u4e4c\\u5170\\u5bdf\\u5e03/
    );
    var city = cityMatch ? cityMatch[0] : '';

    var descText = desc.innerText.trim();
    if (title && descText) {
      jobs.push({id: jid || title.substring(0, 20), title: title, city: city, desc: descText});
    }
  }
  return JSON.stringify(jobs);
})()
"""


def scrape_moka() -> list[JobPosting]:
    from src.scrapers.browser_base import playwright_page

    all_jobs: list[JobPosting] = []
    seen_ids: set[str] = set()

    with playwright_page() as page:

        for company_name, org_slug, org_id in MOKA_COMPANIES:
            base = f"https://app.mokahr.com/social-recruitment/{org_slug}/{org_id}"
            jobs_url = f"{base}#/jobs"
            logger.info("[moka] scraping %s (%s)", company_name, jobs_url)

            try:
                page.goto(jobs_url, wait_until="domcontentloaded", timeout=25000)
                # Wait for job items to appear
                page.wait_for_selector("[class*=job-description]", timeout=12000)
            except Exception:
                logger.warning("[moka] page load failed for %s", company_name)
                continue

            page.wait_for_timeout(2000)

            raw = page.evaluate(JS_EXTRACT)
            try:
                items = json.loads(raw)
            except Exception:
                items = []

            company_count = 0
            for it in items:
                jid = str(it.get("id", ""))
                if not jid or jid in seen_ids:
                    continue
                seen_ids.add(jid)
                company_count += 1

                job_url = f"{base}#/job/{jid}" if "-" in jid else base

                all_jobs.append(JobPosting(
                    job_id=jid,
                    platform="moka",
                    company=company_name,
                    title=it.get("title", ""),
                    location=it.get("city", ""),
                    description=it.get("desc", ""),
                    url=job_url,
                ))

                logger.info("[moka] %s done: %d jobs", company_name, company_count)
                time.sleep(random.uniform(1.0, 2.0))

    logger.info("[moka] total: %d", len(all_jobs))
    return all_jobs
