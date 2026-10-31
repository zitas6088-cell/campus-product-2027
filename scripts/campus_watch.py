#!/usr/bin/env python3
"""Notify only confirmed new 2027 product-related jobs from the watched companies."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from job_radar import sync
from job_radar.campus_product import classify, identity, is_product, legacy_product_match, load_config, select_pending

DATA = ROOT / "data"
STATE = DATA / "campus_notify_state.json"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def deliver(text):
    """Credentials stay in environment; verify application-level success too."""
    channel = os.getenv("NOTIFY_CHANNEL", "feishu").strip()
    if channel == "feishu":
        url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        body = {"msg_type": "text", "content": {"text": text}}
        secret = os.getenv("FEISHU_SIGN_SECRET", "").strip()
        if secret:
            timestamp = str(int(time.time()))
            key = (timestamp + "\n" + secret).encode("utf-8")
            body.update(timestamp=timestamp,
                sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii"))
    elif channel == "wecom":
        url = os.getenv("WECOM_WEBHOOK_URL", "").strip()
        body = {"msgtype": "markdown", "markdown": {"content": text}}
    elif channel == "pushplus":
        token = os.getenv("PUSHPLUS_TOKEN", "").strip()
        url = "https://www.pushplus.plus/send" if token else ""
        body = {"token": token, "title": "2027届校招新增提醒", "content": text, "template": "txt"}
    else:
        raise RuntimeError("不支持的通知渠道")
    if not url:
        raise RuntimeError("通知凭证尚未配置；本轮未标记任何岗位为已发送")
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, data = response.status, json.loads(response.read().decode("utf-8"))
    except Exception as e:
        # urllib exception text can contain a credential-bearing URL.
        raise RuntimeError("通知请求失败：" + type(e).__name__) from None
    code = data.get("StatusCode", data.get("code", data.get("errcode")))
    success_code = 200 if channel == "pushplus" else 0
    if channel == "feishu" and str(code) == "19021":
        raise RuntimeError("飞书签名校验失败：请在仓库 Actions Secrets 配置正确的 FEISHU_SIGN_SECRET。未标记已发送，下一轮会重试。")
    if status != 200 or str(code) != str(success_code):
        raise RuntimeError(f"通知服务未确认成功（HTTP {status}，业务码 {code}）")


def line(job):
    prefix = "⭐ 产品重点｜" if is_product(job) else ""
    return f"{prefix}{job['company_name']}｜{job['title']}\n地点：{job.get('location') or '官网未注明'}\n{classify(job)[1]}\n{job['official_url']}"


def expansion_baseline(jobs, state, healthy, cfg, now):
    """Baseline newly fetched ByteDance roles once; keep old product retries intact."""
    expansion = cfg.get("scope_expansion", {})
    if not expansion:
        return
    completed = state.setdefault("scope_baselines", {}).setdefault(expansion["key"], [])
    targets = (set(expansion["source_ids"]) & healthy) - set(completed)
    for job in jobs:
        if job["source_id"] in targets and not legacy_product_match(job, cfg):
            state["delivered"].setdefault(identity(job), {"baseline_at": now, "reason": expansion["key"]})
    completed.extend(sorted(targets))


def notification_batches(jobs):
    # Defence in depth: callers cannot accidentally send unrelated jobs.
    selected = [job for job in jobs if is_product(job)]
    for start in range(0, len(selected), 3):
        yield "⭐ 2027届产品相关岗位新增", selected[start:start + 3]


def report(jobs, health):
    groups = {"confirmed": [], "pending": [], "internship": []}
    for job in jobs:
        kind, evidence = classify(job)
        if kind in groups:
            groups[kind].append(job)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    products = [j for j in groups["confirmed"] if is_product(j)]
    others = [j for j in groups["confirmed"] if not is_product(j)]
    lines = [f"# 2027届校招监控｜{now}（北京时间）", "",
             "仅新增且已确认适用2027届的产品相关正式校招岗位进入飞书提醒。非产品、实习、社招及届别未确认岗位不推送。", ""]
    for rows, title in [(products, "⭐ 产品重点"), (others, "其他已确认2027届岗位"),
                        (groups["pending"], "届别待确认（不推送）"), (groups["internship"], "实习参考（不推送）")]:
        lines.extend([f"## {title} · {len(rows)} 条", ""])
        for job in rows:
            lines.extend(["- " + line(job).replace("\n", "\n  "), ""])
    lines += ["## 信源状态", ""]
    for src in health.get("sources", []):
        if src.get("not_run"):
            continue
        status = src.get("last_error") or src.get("alert") or f"抓取 {src.get('last_count', 0)} 条"
        lines.append(f"- {src['source_id']}：{status}")
    cfg = load_config()
    watched = [s for s in health.get('sources', []) if s.get('source_id') in cfg['source_ids'] and not s.get('not_run')]
    failed = [s for s in watched if s.get('last_error') or s.get('alert') or s.get('skipped')]
    lines += ['', f"监控清单 {len(cfg['companies'])} 家；本轮尝试 {len(watched)} 个信源；正常 {len(watched)-len(failed)}，异常/待适配 {len(failed)}。", '',
              '异常不等于没有新增。首次成功读取的新公司仅建立现有岗位基线，后续出现的新职位再提醒。']
    save(DATA / 'company_monitor_coverage.json', {'generated_at': now,
         'configured_companies': len(cfg['companies']), 'attempted_sources': len(watched),
         'healthy_sources': len(watched)-len(failed), 'unhealthy_sources': failed})
    (DATA / "campus_product.md").write_text("\n".join(lines), encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            # GitHub caps a step summary at 1 MiB; the downloadable report stays complete.
            summary_text = "\n".join(lines)
            if len(summary_text.encode("utf-8")) > 900000:
                summary_text = (f"# 2027届校招监控｜{now}\n\n"
                    f"产品重点 {len(products)} 条；其他确认岗位 {len(others)} 条；"
                    f"届别待确认 {len(groups['pending'])} 条。\n\n完整清单见本次运行附件。\n")
            f.write(summary_text)
    save(DATA / "campus_product.json", {"generated_at": now,
        "confirmed": groups["confirmed"], "pending": groups["pending"], "internships": groups["internship"]})
    return groups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-notification", action="store_true")
    args = parser.parse_args()
    if args.test_notification:
        deliver("2027届产品岗雷达连接测试成功。仅提醒表格公司新增的产品相关正式校招岗位；每天北京时间09:00和15:00检查。")
        print("通知渠道测试成功。")
        return
    cfg = load_config()
    if not args.no_sync:
        # Scope-aware merge keeps a failed source's last successful data.
        health = sync.run(only_source_ids=set(cfg["source_ids"]), preserve_unselected=True)
    else:
        health = read(DATA / "health_report.json", {})
    jobs = read(DATA / "jobs.json", [])
    groups = report(jobs, health)
    state = read(STATE, {"version": 1, "sources_initialized": [], "delivered": {}})
    healthy = {s["source_id"] for s in health.get("sources", [])
               if s["source_id"] in cfg["source_ids"] and not s.get("not_run")
               and not s.get("last_error") and not s.get("alert") and not s.get('skipped')
               and s.get("last_count") is not None and s.get("last_count", -1) >= 0}
    if not healthy:
        raise RuntimeError("没有任何官网信源成功返回岗位，本轮不初始化、不发送、不修改已推状态")
    first_sources = healthy - set(state["sources_initialized"])
    existing = [j for j in jobs if j["source_id"] in first_sources]
    pending_state = json.loads(json.dumps(state))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for job in existing:
        pending_state["delivered"][identity(job)] = {"baseline_at": now}
    pending_state["sources_initialized"] = sorted(set(state["sources_initialized"]) | healthy)
    expansion_baseline(jobs, pending_state, healthy, cfg, now)
    pending = select_pending(jobs, pending_state, cfg)
    pending = [j for j in pending if j["source_id"] in healthy]
    if args.dry_run:
        print(f"仅预览：确认校招岗 {len(groups['confirmed'])}，产品重点 {sum(is_product(j) for j in groups['confirmed'])}，待确认 {len(groups['pending'])}，首次建基线 {len(first_sources)} 个信源，待推 {len(pending)} 条。未发送、未写推送状态。")
        return
    # First successful snapshot of each source establishes a baseline, never floods old listings.
    save(STATE, pending_state)
    if not pending:
        print(f"无符合条件的未推新增；本轮初始化 {len(first_sources)} 个信源。")
        return
    # Each confirmed batch is marked only after the provider acknowledges success.
    for heading, batch in notification_batches(pending):
        text = heading + "\n\n" + "\n\n".join(line(j) for j in batch)
        deliver(text)
        for job in batch:
            pending_state["delivered"][identity(job)] = {"sent_at": now, "title": job["title"]}
        save(STATE, pending_state)
    print(f"已推送 {len(pending)} 条新增校招岗，其中产品重点 {sum(is_product(j) for j in pending)} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
