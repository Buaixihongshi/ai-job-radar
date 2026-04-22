"""Scraper for jobs.feishu.cn (飞书招聘) - 支持多家公司.

DOM 提取：从 a[data-id] 卡片中获取标题、城市、描述，
通过点击分页按钮翻页（最多 MAX_PAGES 页）。

FEISHU_COMPANIES 格式：(显示名称, subdomain, 可选路径)
  标准路径为 /index/；如公司使用自定义路径，显式指定第三个参数。
"""
from __future__ import annotations

import json
import logging
import random
import time

from src.models import JobPosting

logger = logging.getLogger(__name__)

# (显示名称, 飞书租户 subdomain, 可选路径前缀)
# 标准路径 = /index/，百川等使用自定义二级路径
FEISHU_COMPANIES: list[tuple[str, str, str]] = [
    # ── 已有 ──────────────────────────────────────────────────────
    ("MiniMax",  "vrfi1sk8a0",  "/index/"),
    ("智谱AI",   "zhipu-ai",    "/index/"),
    # ── 新增 ──────────────────────────────────────────────────────
    ("商汤科技", "sensetime",   "/index/"),
    ("零一万物", "01ai",        "/index/"),
    ("百川智能", "cq6qe6bvfr6", "/baichuanzhaopin/"),
]

MAX_PAGES = 8

JS_EXTRACT = """
(function() {
  var items = document.querySelectorAll('a[data-id]');
  var jobs = [];
  for (var i = 0; i < items.length; i++) {
    var el = items[i];
    var jid = el.getAttribute('data-id');
    var titleEl = el.querySelector('.positionItem-title-text');
    var title = titleEl ? titleEl.textContent.trim() : '';
    var subtitleSpans = el.querySelectorAll('.positionItem-subTitle span');
    var city = subtitleSpans.length > 0 ? subtitleSpans[0].textContent.trim() : '';
    var descEl = el.querySelector('[class*=jobDesc]');
    var desc = descEl ? descEl.textContent.trim() : '';
    var href = el.getAttribute('href');
    if (jid && title) {
      jobs.push({id: jid, title: title, city: city, desc: desc, href: href || ''});
    }
  }
  return JSON.stringify(jobs);
})()
"""

JS_NEXT_PAGE = """
(function() {
  var activeItem = document.querySelector('.atsx-pagination-item-active');
  if (!activeItem) return 'no-pagination';
  var next = activeItem.nextElementSibling;
  while (next) {
    var cls = next.className || '';
    if (cls.includes('atsx-pagination-item') && !cls.includes('jump') && !cls.includes('ellipsis')) {
      next.click();
      return 'clicked:' + next.textContent.trim();
    }
    next = next.nextElementSibling;
  }
  return 'no-next';
})()
"""


def scrape_feishu() -> list[JobPosting]:
    from src.scrapers.browser_base import playwright_page

    all_jobs: list[JobPosting] = []
    seen_ids: set[str] = set()

    with playwright_page() as page:

        for entry in FEISHU_COMPANIES:
            company_name, subdomain, path = entry
            url = f"https://{subdomain}.jobs.feishu.cn{path}"
            logger.info("[feishu] scraping %s (%s)", company_name, url)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_selector("a[data-id]", timeout=12000)
            except Exception:
                logger.warning("[feishu] page load failed for %s", company_name)
                continue

            company_count = 0
            for page_no in range(1, MAX_PAGES + 1):
                page.wait_for_timeout(1200)

                raw = page.evaluate(JS_EXTRACT)
                try:
                    items = json.loads(raw)
                except Exception:
                    items = []

                new_count = 0
                for it in items:
                    jid = str(it.get("id", ""))
                    if not jid or jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    new_count += 1

                    href = it.get("href", "")
                    job_url = (
                        f"https://{subdomain}.jobs.feishu.cn{href}"
                        if href.startswith("/")
                        else f"https://{subdomain}.jobs.feishu.cn{path}position/{jid}/detail"
                    )

                    all_jobs.append(JobPosting(
                        job_id=jid,
                        platform="feishu",
                        company=company_name,
                        title=it.get("title", ""),
                        location=it.get("city", ""),
                        description=it.get("desc", ""),
                        url=job_url,
                    ))

                company_count += new_count
                logger.info("[feishu] %s page=%d items=%d new=%d", company_name, page_no, len(items), new_count)

                if new_count == 0:
                    break

                result = page.evaluate(JS_NEXT_PAGE)
                if result in ("no-next", "no-pagination"):
                    break

                time.sleep(random.uniform(0.8, 1.5))

            logger.info("[feishu] %s done: %d jobs", company_name, company_count)

    logger.info("[feishu] total: %d", len(all_jobs))
    return all_jobs
