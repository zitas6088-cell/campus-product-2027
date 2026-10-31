"""Strict campus eligibility and product priority, independent of match scores."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "campus_product.json"


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def identity(job):
    """Prefer an employer's stable id over a title/city dedup key."""
    extra = job.get("extra") or {}
    return (job.get("company_name", "") + ":" + str(job.get("identifier") or extra.get("identifier"))) \
        if (job.get("identifier") or extra.get("identifier")) else str(job.get("official_url") or job.get("dedup_key"))


def graduation_evidence(job, year):
    extra = job.get("extra") or {}
    if str(extra.get("graduation_year", "")) == str(year):
        return f"官网毕业年份字段：{year}"
    # Only title, requirements and named batch metadata count; timestamps and URLs do not.
    text = " ".join(str(x or "") for x in (
        job.get("title"), job.get("jd_text"), extra.get("batch_text")))
    pattern = rf"(?:{year}|{str(year)[-2:]})\s*届|{year}\s*(?:年\s*)?(?:校招|校园招聘|毕业生)|(?:校招|校园招聘)\s*{year}|{year}\s*(?:campus|graduate)"
    found = re.search(pattern, text, re.I)
    if found:
        return "官网届别文字：" + found.group(0)
    english = re.search(rf'\b(?:graduating|graduation|graduates?)\b[^.\n]{{0,80}}\b{year}\b', text, re.I)
    if english:
        return "官网毕业资格文字：" + english.group(0)
    # A published graduation window is evidence; publication and application dates are not.
    date_part = r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?"
    for match in re.finditer(date_part + r"\s*(?:至|到|~|～|—|–|-)\s*" + date_part, text):
        context = text[max(0, match.start() - 30):match.end() + 35]
        if "毕业" not in context:
            continue
        try:
            a = dt.date(*map(int, match.groups()[:3]))
            b = dt.date(*map(int, match.groups()[3:]))
        except ValueError:
            continue
        if a <= dt.date(year, 6, 30) <= b:
            return "官网毕业时间范围：" + match.group(0)
    return ""


def classify(job, cfg=None, today=None):
    cfg = cfg or load_config()
    today = today or dt.date.today()
    if job.get("gone") or job.get("company_name") not in cfg["companies"]:
        return "excluded", "非监控公司或已下线"
    if job.get("source_id") not in cfg["source_ids"]:
        return "excluded", "非已配置官网信源"
    title = str(job.get("title", "")).lower()
    cities = cfg.get("cities") or []
    if cities and not any(c in str(job.get("location", "")) for c in cities):
        return "excluded", "不在指定城市"
    if job.get("deadline") and str(job["deadline"])[:10] < today.isoformat():
        return "excluded", "已超过官网截止时间"
    extra = job.get("extra") or {}
    labels = title + " " + str(extra.get("batch_text", "")).lower()
    internship_labels = re.sub(r'可提前实习|可以实习|接受实习|可实习', '', labels)
    if (not cfg.get("include_internships") and
        ("实习" in internship_labels or "intern" in internship_labels or "INTERN" in str(job.get("employment_type", "")))):
        return "internship", "实习单列，不发正式校招提醒"
    if re.search(r"社招|社会招聘|资深|总监|senior|principal|head of", labels):
        return "excluded", "社招或资深职位"
    evidence = graduation_evidence(job, cfg["graduation_year"])
    if not evidence:
        # Only a separately verified, unexpired official recruitment batch can fill a missing year.
        verified = cfg.get("verified_source_cycles", {}).get(job["source_id"], {})
        if (verified.get("year") == cfg["graduation_year"] and verified.get("evidence_url")
                and verified.get("valid_until", "") >= today.isoformat()):
            evidence = "已核实官网批次：" + verified["evidence_url"]
    if not evidence:
        return "pending", "官网信息未明确适用于2027届，待确认"
    if not job.get("official_url"):
        return "pending", "缺少官网入口，待确认"
    return "confirmed", evidence


def is_product(job, cfg=None):
    """Highlight titles about product work; incidental JD mentions do not count."""
    cfg = cfg or load_config()
    title = str(job.get("title", "")).lower()
    return "产品" in title or bool(re.search(r"\bproduct\b", title)) or any(k.lower() in title for k in cfg["product_roles"])


def legacy_product_match(job, cfg):
    """The previous role scope, used only for the one-time ByteDance expansion."""
    title = str(job.get("title", "")).lower()
    return (any(k.lower() in title for k in cfg["product_roles"])
            and not any(k.lower() in title for k in cfg["excluded_titles"]))


def select_pending(jobs, state, cfg=None):
    cfg = cfg or load_config()
    sent = state.get("delivered", {})
    return [j for j in jobs if classify(j, cfg)[0] == "confirmed" and identity(j) not in sent
            and (not cfg.get('notify_product_only', True) or is_product(j, cfg))]
