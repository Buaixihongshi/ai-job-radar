"""Precision filtering for AI testing/evaluation job search.

Target directions (from user's WPS JD reference):
  1. 大模型/AI测试    - 大模型测试/AI测试/算法测试 (title must have AI context)
  2. 测试开发(AI方向)  - 测试开发 with AI/大模型/Agent context in title or description
  3. Agent评测        - 大模型评测/Agent评测/模型评估
  4. AI/Agent产品     - ONLY Agent产品/AIGC产品/评测产品 (tight scope)

Additional constraints:
  - 社招 only, no 校招/实习
  - Education: 本科 or below (exclude 硕士/博士 requirement)
  - Experience: allow up to 3 years requirement (exclude 五年以上/八年以上/5-10年)
"""
from __future__ import annotations

import logging
import re

from src.models import JobPosting

logger = logging.getLogger(__name__)

HIGH_EDU_REQUIRED = re.compile(r"硕士|博士|研究生|Master|PhD", re.IGNORECASE)

HIGH_EXP_FIELD = re.compile(
    r"(五年以上|八年以上|十年以上|5-10年|10年以上)",
)

HIGH_EXP_TEXT = re.compile(
    r"(\d+)\s*年以?上.{0,6}(工作|经验|经历)",
)
# "三年以上" and "3-5年" are OK (require 3 years, user qualifies)

CAMPUS_RECRUIT = re.compile(
    r"(校招|应届|届\+|27届|28届|26届|毕业生|campus)",
    re.IGNORECASE,
)

EXCLUDE_TITLE = re.compile(
    r"(硬件|嵌入式|芯片|射频|FPGA|驱动工程|电气|机械|光学|"
    r"相机测试|传感器测试|基带测试|可靠性测试|认证测试|近距通信|显示测试|"
    r"PQE|结构工艺|质量PQA|制造质量|电子件|"
    r"数据库内核|DBA|存储引擎|SRE|运维工程|"
    r"安全渗透|红队|攻防|漏洞挖掘|安全合规|安全评测|安全产品|"
    r"设计师|美术|编剧|导演|短剧|版权|"
    r"运营(?!.*(?:评测|测试|质量))|销售|市场营销|财务|商务|法务|行政|"
    r"标注主管|数据标注|"
    r"MicroLED|显示驱动|ATE测试|"
    r"实习生|实习(?!.*(?:测试开发|评测))|届\+|27届|28届|"
    r"后台开发工程师|架构师|解决方案)",
    re.IGNORECASE,
)

AI_IN_TITLE = re.compile(
    r"(AI|人工智能|大模型|LLM|Agent|AIGC|算法|多模态|NLP|"
    r"模型评[测估]|智能体?|GPT|RAG|Prompt|"
    r"千问|元宝|混元|文心|通义|copilot|ima|CodeBuddy)",
    re.IGNORECASE,
)

AI_IN_DESC = re.compile(
    r"(大模型|LLM|Agent|AIGC|算法测试|多模态|NLP|"
    r"模型评[测估]|智能体|GPT|RAG|Prompt|"
    r"Benchmark|badcase|模型效果|模型质量|AI质量|"
    r"算法效果|算法质量|评测平台|评测框架)",
    re.IGNORECASE,
)

PURE_GAME_PATTERN = re.compile(
    r"(SLG|MMORPG|单机|FPS|竞速|赛车|格斗|RTS|卡牌|"
    r"引擎测试|引擎开发|遗忘之海|大世界|天美中台|无限大|"
    r"服务器方向|破次元|客户端测试(?!.*AI)|"
    r"游戏研发向|AI生成游戏|AI竞技机器人)",
    re.IGNORECASE,
)

PRODUCT_EXCLUDE = re.compile(
    r"(外贸|社交|音乐|写歌|推荐|分发|投放|广告|OCR|招聘系统|"
    r"地图|云AI-ToB|轻量云|WeGame|"
    r"具身智能|data\s*agent|数据|文档产品|"
    r"平台产品经理|计算平台)",
    re.IGNORECASE,
)


def _title_has_ai(title: str) -> bool:
    return bool(AI_IN_TITLE.search(title))


def _desc_has_ai(job: JobPosting) -> bool:
    text = f"{job.description} {job.requirements}"
    return bool(AI_IN_DESC.search(text))


def _check_eligibility(job: JobPosting) -> str | None:
    """Return rejection reason or None if eligible."""
    title = job.title
    if CAMPUS_RECRUIT.search(title):
        return "campus"

    edu = job.education.strip()
    if edu and HIGH_EDU_REQUIRED.search(edu):
        return "edu_high"

    req_text = job.requirements or ""
    if re.search(r"硕士及以上|硕士以上|研究生及以上|研究生以上|硕士学历", req_text):
        return "edu_high_in_req"

    exp = job.experience.strip()
    if exp and HIGH_EXP_FIELD.search(exp):
        return "exp_high"
    if exp:
        m_exp = re.search(r"(\d+)\s*年", exp)
        if m_exp and int(m_exp.group(1)) > 3:
            return "exp_high"

    m = HIGH_EXP_TEXT.search(req_text)
    if m:
        years = int(m.group(1))
        if years > 3:
            return "exp_high_in_req"

    return None


def classify_strict(job: JobPosting) -> str | None:
    title = job.title.strip()

    if not title or len(title) < 4:
        return None
    if title.startswith(("script>", "window.")):
        return None
    if EXCLUDE_TITLE.search(title):
        return None
    if PURE_GAME_PATTERN.search(title):
        return None

    rejection = _check_eligibility(job)
    if rejection:
        return None

    title_ai = _title_has_ai(title)
    desc_ai = _desc_has_ai(job)
    has_ai = title_ai or desc_ai

    # --- 测试开发(AI方向) ---
    if "测试开发" in title or "自动化测试" in title:
        if title_ai:
            return "测试开发(AI方向)"
        if desc_ai:
            return "测试开发(AI方向)"
        return None

    # --- Agent评测 ---
    if "评测" in title or ("评估" in title and ("模型" in title or "agent" in title.lower())):
        if re.search(r"算法(工程师|研究员)", title) and "评测" not in title:
            return None
        return "Agent评测"

    # --- 大模型/AI测试 ---
    if any(kw in title for kw in ["测试", "质量保障"]) or "QA" in title.upper():
        if "游戏" in title and not title_ai:
            return None
        if title_ai:
            return "大模型/AI测试"
        if desc_ai:
            return "大模型/AI测试"
        return None

    # --- AI/Agent产品 (tight scope) ---
    if "产品" in title:
        title_lower = title.lower()
        is_agent_product = (
            "agent" in title_lower
            or "评测" in title
            or "评估" in title
            or "策略产品" in title and ("元宝" in title or "ima" in title or "CodeBuddy" in title or "WorkBuddy" in title)
            or "AIGC产品" in title
        )
        if is_agent_product and not PRODUCT_EXCLUDE.search(title):
            return "AI/Agent产品"
        return None

    return None


def filter_strict(jobs: list[JobPosting]) -> list[JobPosting]:
    result = []
    for job in jobs:
        cat = classify_strict(job)
        if cat:
            job.category = cat
            result.append(job)

    logger.info(
        "Strict filter: %d/%d jobs passed (%.0f%% removed)",
        len(result), len(jobs),
        (1 - len(result) / max(len(jobs), 1)) * 100,
    )
    return result
