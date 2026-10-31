#!/usr/bin/env python3
"""Sync official campus feeds and deliver only confirmed new product opportunities."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from job_radar import sync
from job_radar.campus_product import classify, identity, load_config, select_pending

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
    elif channel == "wecom":
        url = os.getenv("WECOM_WEBHOOK_URL", "").strip()
        body = {"msgtype": "markdown", "markdown": {"content": text}}
    elif channel == "pushplus":
        token = os.getenv("PUSHPLUS_TOKEN", "").strip()
        url = "https://www.pushplus.plus/send" if token else ""
        body = {"token": token, "title": "2027届产品岗新增提醒", "content": text, "template": "txt"}
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
    if status != 200 or str(code) != str(success_code):
        raise RuntimeError(f"通知服务未确认成功（HTTP {status}，业务码 {code}）")


def line(job):
    return f"{job['company_name']}｜{job['title']}\n地点：{job.get('location') or '官网未注明'}\n{classify(job)[1]}\n{job['official_url']}"


def report(jobs, health):
    groups = {"confirmed": [], "pending": [], "internship": []}
    for job in jobs:
        kind, evidence = classify(job)
        if kind in groups:
            groups[kind].append(job)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    lines = [f"# 2027届产品岗监控｜{now}（北京时间）", "",
             "仅已核实届别的正式产品岗进入新增提醒。未确认届别与实习单列。", ""]
    for key, title in [("confirmed", "已确认适用2027届"), ("pending", "届别待确认（不推送）"), ("internship", "实习参考（不推送）")]:
        lines.extend([f"## {title} · {len(groups[key])} 条", ""])
        for job in groups[key]:
            lines.extend(["- " + line(job).replace("\n", "\n  "), ""])
    lines += ["## 信源状态", ""]
    for src in health.get("sources", []):
        if src.get("not_run"):
            continue
        status = src.get("last_error") or src.get("alert") or f"抓取 {src.get('last_count', 0)} 条"
        lines.append(f"- {src['source_id']}：{status}")
    (DATA / "campus_product.md").write_text("\n".join(lines), encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
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
        deliver("2027届产品岗雷达连接测试成功。正式任务仅在发现符合条件的新增岗位时提醒。")
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
               and not s.get("last_error") and not s.get("alert") and s.get("last_count", 0) > 0}
    if not healthy:
        raise RuntimeError("没有任何官网信源成功返回岗位，本轮不初始化、不发送、不修改已推状态")
    first_sources = healthy - set(state["sources_initialized"])
    existing = [j for j in jobs if j["source_id"] in first_sources]
    pending_state = json.loads(json.dumps(state))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for job in existing:
        pending_state["delivered"][identity(job)] = {"baseline_at": now}
    pending_state["sources_initialized"] = sorted(set(state["sources_initialized"]) | healthy)
    pending = select_pending(jobs, pending_state, cfg)
    pending = [j for j in pending if j["source_id"] in healthy]
    if args.dry_run:
        print(f"仅预览：确认产品岗 {len(groups['confirmed'])}，待确认 {len(groups['pending'])}，首次建基线 {len(first_sources)} 个信源，待推 {len(pending)} 条。未发送、未写推送状态。")
        return
    # First successful snapshot of each source establishes a baseline, never floods old listings.
    save(STATE, pending_state)
    if not pending:
        print(f"无符合条件的未推新增；本轮初始化 {len(first_sources)} 个信源。")
        return
    # Each confirmed batch is marked only after the provider acknowledges success.
    for start in range(0, len(pending), 3):
        batch = pending[start:start + 3]
        text = "# 2027届产品岗新增\n\n" + "\n\n".join(line(j) for j in batch)
        deliver(text)
        for job in batch:
            pending_state["delivered"][identity(job)] = {"sent_at": now, "title": job["title"]}
        save(STATE, pending_state)
    print(f"已推送 {len(pending)} 条新增产品岗。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
