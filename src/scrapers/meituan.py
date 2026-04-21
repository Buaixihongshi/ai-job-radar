"""Scraper for zhaopin.meituan.com (美团招聘).

Uses Playwright: navigate to keyword search URL, extract job cards directly
from DOM (.position_list_item), paginate via URL param pageNo.

Description is embedded in each card's .desc elements, so no detail API needed.
"""
from __future__ import annotations

import logging
import time
import random

from src.models import JobPosting

logger = logging.getLogger(__name__)

KEYWORDS = ["大模型测试", "AI测试", "算法测试", "测试开发", "Agent评测", "大模型评测", "AIGC产品", "Agent产品"]
BASE_URL = "https://zhaopin.meituan.com/web/social"
MAX_PAGES = 5
CITY_PAT = ("北京", "上海", "深圳", "杭州", "广州", "成都", "武汉", "南京", "西安", "重庆")

JS_EXTRACT = """
(function() {
  var items = document.querySelectorAll('.position_list_item[data-jobunionid]');
  var results = [];
  for (var i = 0; i < items.length; i++) {
    var el = items[i];
    var jid = el.getAttribute('data-jobunionid');
    var titleEl = el.querySelector('.postion_name .title') || el.querySelector('.title');
    var title = titleEl ? titleEl.textContent.trim() : '';
    var city = '', dept = '';
    var spans = el.querySelectorAll('.split_line_box_item span');
    for (var s = 0; s < spans.length; s++) {
      var t = spans[s].textContent.trim();
      if (!city && t.match(/\u5317\u4eac|\u4e0a\u6d77|\u6df1\u5733|\u676d\u5dde|\u5e7f\u5dde|\u6210\u90fd|\u6b66\u6c49|\u5357\u4eac|\u897f\u5b89|\u91cd\u5e86/)) city = t;
      if (!dept && t.includes('-') && t.length > 3 && t.length < 30) dept = t;
    }
    var descs = el.querySelectorAll('.desc');
    var desc = '';
    for (var d = 0; d < descs.length; d++) desc += descs[d].textContent.trim() + ' ';
    var pageData = null;
    try { pageData = JSON.parse(el.getAttribute('data-page').replace(/&quot;/g, '"')); } catch(e) {}
    if (jid && title) results.push({
      id: jid, title: title, city: city, dept: dept, desc: desc.trim(),
      totalPages: pageData ? pageData.totalPage : 1
    });
  }
  return JSON.stringify(results);
})()
"""


def scrape_meituan() -> list[JobPosting]:
    from playwright.sync_api import sync_playwright
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
    except ImportError:
        stealth = None

    all_items: dict[str, dict] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        page = context.new_page()
        if stealth:
            stealth.apply_stealth_sync(page)

        for kw in KEYWORDS:
            try:
                url = f"{BASE_URL}?keyword={kw}&pageNo=1"
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_selector(".position_list_item", timeout=10000)
            except Exception:
                logger.warning("[meituan] page load failed for keyword=%s", kw)
                continue

            raw = page.evaluate(JS_EXTRACT)
            try:
                import json
                items = json.loads(raw)
            except Exception:
                items = []

            total_pages = 1
            for it in items:
                total_pages = max(total_pages, it.get("totalPages", 1))
                jid = it["id"]
                if jid not in all_items:
                    all_items[jid] = it

            logger.info("[meituan] keyword=%s page=1 got=%d total_pages=%d cumulative=%d",
                        kw, len(items), total_pages, len(all_items))

            # Paginate up to MAX_PAGES
            for pno in range(2, min(total_pages + 1, MAX_PAGES + 1)):
                try:
                    page.goto(f"{BASE_URL}?keyword={kw}&pageNo={pno}",
                              wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_selector(".position_list_item", timeout=8000)
                except Exception:
                    break

                raw = page.evaluate(JS_EXTRACT)
                try:
                    page_items = json.loads(raw)
                except Exception:
                    page_items = []

                new_count = 0
                for it in page_items:
                    jid = it["id"]
                    if jid not in all_items:
                        all_items[jid] = it
                        new_count += 1

                logger.info("[meituan] keyword=%s page=%d new=%d cumulative=%d",
                            kw, pno, new_count, len(all_items))

                if new_count == 0:
                    break
                time.sleep(random.uniform(1.0, 2.0))

        browser.close()

    jobs: list[JobPosting] = []
    for jid, it in all_items.items():
        desc = it.get("desc", "")
        jobs.append(JobPosting(
            job_id=jid,
            platform="meituan",
            company="美团",
            title=it.get("title", ""),
            department=it.get("dept", ""),
            location=it.get("city", ""),
            description=desc,
            requirements="",
            url=f"https://zhaopin.meituan.com/web/social-recruitment/{jid}",
        ))

    logger.info("[meituan] total: %d", len(jobs))
    return jobs
