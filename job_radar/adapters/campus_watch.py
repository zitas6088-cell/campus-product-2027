"""Official campus APIs, with explicit paging and API error checks.

Endpoint references: GeoSyntax/xiaozhaogw, ruyi1/campus-radar and
HA7CH/job-pro. Only RawJob values leave this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import time

from . import register
from .http import post_json
from ..models import RawJob


def date(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e12 else value,
                                      timezone.utc).strftime("%Y-%m-%d")
    return str(value or "")[:10]


def pages(url, body_for_page, extract, referer, headers=None, start=1, status_success=(0, 200)):
    """Use returned totals, never assume the requested page size was honored."""
    rows, seen, received = [], set(), 0
    for page in range(start, start + 60):
        d = post_json(url, body_for_page(page), headers={"Referer": referer, **(headers or {})})
        if not isinstance(d, dict) or d.get("success") is False:
            raise RuntimeError("招聘接口返回失败状态")
        if "status" in d and d["status"] is not None and str(d["status"]) not in {str(x) for x in status_success}:
            raise RuntimeError("招聘接口 status 非成功")
        if "code" in d and d["code"] not in (0, "0", 200, "200", None):
            raise RuntimeError("招聘接口 code 非成功")
        if "statusCode" in d and d["statusCode"] != 200:
            raise RuntimeError("招聘接口 statusCode 非成功")
        batch, total = extract(d)
        if not isinstance(batch, list):
            raise RuntimeError("岗位列表格式改变")
        if not batch:
            if total and received < int(total):
                raise RuntimeError("岗位列表提前结束，拒绝使用不完整快照")
            return rows
        new = 0
        for row in batch:
            key = str(row.get("positionId") or row.get("reqId") or row.get("postId")
                      or row.get("jobUnionId") or row.get("id") or "")
            if not key:
                raise RuntimeError("岗位缺少编号")
            if key not in seen:
                seen.add(key)
                rows.append(row)
                new += 1
        if not new:
            raise RuntimeError("招聘接口分页重复，拒绝使用不完整快照")
        received += len(batch)
        if total is not None and received >= int(total):
            return rows
        time.sleep(0.2)
    raise RuntimeError("招聘接口分页超过安全上限")


def pair(d, container, list_key, total_key):
    c = d.get(container)
    if not isinstance(c, dict) or list_key not in c:
        raise RuntimeError("招聘接口结构改变")
    total = c.get(total_key)
    if total is None and isinstance(c.get("page"), dict):
        total = c["page"].get("totalCount")
    return ([] if c[list_key] is None else c[list_key]), total


@register("watch_jd")
def jd(endpoint):
    rows = pages("https://campus.jd.com/api/wx/position/page?type=present",
        lambda p: {"pageSize": 100, "pageIndex": p, "parameter": {
            "positionName": "", "planIdList": [], "jobDirectionCodeList": [],
            "workCityCodeList": [], "positionDeptList": []}},
        lambda d: pair(d, "body", "items", "totalNumber"), "https://campus.jd.com/", start=0)
    return [RawJob(company_name="京东", title=r.get("positionName", ""),
        location=r.get("workCity") or "、".join(dict.fromkeys(
            x.get("workCity", "") for x in r.get("requirementVoList", []) if x.get("workCity"))),
        publish_time=date(r.get("publishTime")),
        official_url=f"https://campus.jd.com/#/jobs?reqId={r['reqId']}",
        jd_text="\n".join(str(r.get(k) or "") for k in ("workContent", "qualification")),
        raw={"identifier": str(r["reqId"]), "campus": True,
             "batch_text": str(r.get("planName") or ""), "category": r.get("jobDirection", "")})
        for r in rows]


@register("watch_xhs")
def xhs(endpoint):
    rows = pages("https://job.xiaohongshu.com/websiterecruit/position/pageQueryPosition",
        lambda p: {"recruitType": "campus", "positionName": "", "pageNum": p,
                   "pageSize": 100, "campusRecruitTypes": ["term_regular"], "workplaces": []},
        lambda d: pair(d, "data", "list", "total"), "https://job.xiaohongshu.com/campus/position")
    return [RawJob(company_name="小红书", title=r.get("positionName", ""),
        location=r.get("workplace") or "", publish_time=date(r.get("publishTime")),
        official_url=f"https://job.xiaohongshu.com/campus/position?positionId={r['positionId']}",
        jd_text="\n".join(str(r.get(k) or "") for k in ("duty", "qualification")),
        raw={"identifier": str(r["positionId"]), "campus": True,
             "batch_text": str(r.get("jobProjectName") or ""),
             "category": r.get("subDirectionName") or r.get("directionName") or ""})
        for r in rows]


@register("watch_pdd")
def pdd(endpoint):
    rows = pages("https://careers.pddglobalhr.com/api/careers/api/recruit/position/list",
        lambda p: {"page": p, "pageSize": 100, "t": None},
        lambda d: pair(d, "result", "list", "total"),
        "https://careers.pddglobalhr.com/campus/grad")
    return [RawJob(company_name="拼多多", title=r.get("name", ""),
        location=str(r.get("workLocationName") or r.get("workLocation") or ""),
        publish_time=date(r.get("releaseTime")),
        official_url=f"https://careers.pddglobalhr.com/campus/grad/position/detail?id={r['id']}",
        jd_text="\n".join(str(r.get(k) or "") for k in ("jobDuty", "jobRequirement", "qualification")),
        raw={"identifier": str(r["id"]), "campus": True,
             "graduation_year": str(r.get("graduationYear") or ""),
             "batch_text": str(r.get("recruitTypeName") or ""), "category": r.get("jobName", "")})
        for r in rows]


@register("watch_byte")
def byte(endpoint):
    rows = pages("https://jobs.bytedance.com/api/v1/search/job/posts",
        lambda p: {"keyword": "产品", "limit": 100, "offset": (p - 1) * 100,
            "portal_type": 3, "portal_entrance": 1, "language": "zh",
            "recruitment_id_list": ["201"], "job_category_id_list": ["6704215864629004552"],
            "location_code_list": [], "subject_id_list": []},
        lambda d: pair(d, "data", "job_post_list", "count"),
        "https://jobs.bytedance.com/campus/position",
        headers={"portal-channel": "campus", "portal-platform": "pc", "website-path": "campus"})
    out = []
    for r in rows:
        recruit = r.get("recruit_type") or {}
        parent = recruit.get("parent") or {}
        if str(recruit.get("id")) != "201" or "社招" in str(parent.get("name", "")):
            raise RuntimeError("字节接口返回非校招类型，拒绝混入社招")
        out.append(RawJob(company_name="字节跳动", title=r.get("title", ""),
            location="、".join(x.get("name", "") for x in r.get("city_list") or [])
                or (r.get("city_info") or {}).get("name", ""),
            publish_time=date(r.get("publish_time")),
            official_url=f"https://jobs.bytedance.com/campus/position/{r['id']}/detail",
            jd_text="\n".join(str(r.get(k) or "") for k in ("description", "requirement")),
            raw={"identifier": str(r["id"]), "campus": True,
                 "batch_text": json.dumps(r.get("job_subject") or {}, ensure_ascii=False),
                 "category": (r.get("job_category") or {}).get("name", "")}))
    return out


@register("watch_tencent")
def tencent(endpoint):
    rows = pages("https://join.qq.com/api/v1/position/searchPosition",
        lambda p: {"keyword": "", "pageIndex": p, "pageSize": 100, "bgIds": [],
            "productIds": [], "categoryIds": [], "workLocationIds": [], "projectType": ""},
        lambda d: pair(d, "data", "positionList", "count"), "https://join.qq.com/")
    return [RawJob(company_name="腾讯", title=r.get("positionTitle", ""),
        location=r.get("workCities") or "",
        official_url=f"https://careers.tencent.com/jobdesc.html?postId={r['postId']}",
        jd_text="\n".join(str(r.get(k) or "") for k in ("positionTitle", "projectName", "recruitLabelName")),
        raw={"identifier": str(r["postId"]), "campus": True,
             "batch_text": str(r.get("projectName") or ""),
             "employmentType": "INTERN" if "实习" in str(r.get("recruitLabelName", "")) else ""})
        for r in rows]


@register("watch_meituan")
def meituan(endpoint):
    rows = pages("https://zhaopin.meituan.com/api/official/job/getJobList",
        lambda p: {"page": {"pageNo": p, "pageSize": 100}, "jobShareType": "1",
            "keywords": "", "cityIdList": [], "jobTypeList": ["1"]},
        lambda d: pair(d, "data", "list", "total"), "https://campus.meituan.com/", status_success=(1,))
    return [RawJob(company_name="美团", title=r.get("name", ""),
        location="、".join(c.get("name", "") for c in r.get("cityList") or []),
        publish_time=date(r.get("refreshTime")), deadline=date(r.get("expiredTime")),
        official_url=f"https://zhaopin.meituan.com/jobdetail?jobUnionId={r['jobUnionId']}",
        jd_text="\n".join(str(r.get(k) or "") for k in ("jobDuty", "jobRequirement", "desc")),
        raw={"identifier": str(r["jobUnionId"]), "campus": True,
             "batch_text": str(r.get("projectName") or ""),
             "category": r.get("jobFamily") or ""})
        for r in rows]
