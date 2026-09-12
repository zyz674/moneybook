# -*- coding: utf-8 -*-
"""定时任务实体：同步流水、生成并推送账单。"""
import json
import os
import urllib.request
from datetime import timedelta

from . import categorize, importers, notify, parsers, reports, util


def _log(msg):
    print("[moneybook] " + str(msg), flush=True)


def pull_urls(store, cfg, log=_log):
    """从远端 URL 拉账单文件到 inbox（例如网盘直链、自建同步服务）。"""
    urls = util.get(cfg, "sync.pull_urls", []) or []
    inbox = cfg.get("_inbox_dir")
    out = []
    if not urls or not inbox:
        return out
    os.makedirs(inbox, exist_ok=True)
    for item in urls:
        url = item.get("url") if isinstance(item, dict) else item
        name = (item.get("name") if isinstance(item, dict) else "") or os.path.basename(url.split("?")[0]) or "pull.csv"
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = resp.read()
            path = os.path.join(inbox, name)
            with open(path, "wb") as f:
                f.write(data)
            out.append({"url": url, "file": path, "bytes": len(data)})
            log("已下载 %s（%d 字节）" % (name, len(data)))
        except Exception as exc:
            out.append({"url": url, "error": "%s: %s" % (type(exc).__name__, exc)})
            log("下载失败 %s：%s" % (url, exc))
    return out


def sync_now(store, cfg, reason="manual", log=_log):
    """一次同步：扫描 inbox 导入账单 -> 拉远端文件 -> 补分类 -> 大额提醒。"""
    started = util.now_str()
    stats = {"reason": reason, "started_at": started, "inbox": None, "pulls": [], "auto_categorize": 0,
             "new": 0, "alert": None}
    try:
        if util.get(cfg, "sync.watch_inbox", True):
            stats["inbox"] = importers.import_directory(
                store, cfg, cfg.get("_inbox_dir"), cfg.get("_archive_dir"), log=log)
            stats["new"] += stats["inbox"].get("imported", 0)
        stats["pulls"] = pull_urls(store, cfg, log=log)
        if stats["pulls"]:
            again = importers.import_directory(store, cfg, cfg.get("_inbox_dir"), cfg.get("_archive_dir"), log=log)
            stats["new"] += again.get("imported", 0)
            stats["inbox_after_pull"] = again
        stats["auto_categorize"] = categorize.autocategorize_pending(store)
        last_key = "last_sync_ts"
        store.set_state(last_key, util.now_str())
        store.set_state("last_sync_stats", stats)
        ok = True
        detail = "新增 %d 条，自动分类 %d 条" % (stats["new"], stats["auto_categorize"])
    except Exception as exc:
        ok = False
        detail = "%s: %s" % (type(exc).__name__, exc)
        log("同步失败：" + detail)
    store.log_job("sync", started, ok, detail)
    stats["ok"] = ok
    stats["detail"] = detail
    return stats


def check_large_amount(store, cfg, log=_log):
    """大额支出提醒（可选）。"""
    threshold = util.parse_cents(util.get(cfg, "alerts.large_amount", 0)) or 0
    if threshold <= 0:
        return None
    since = store.get_state("last_alert_ts") or (util.now().strftime("%Y-%m-%d") + " 00:00:00")
    now = util.now_str()
    rows = store.conn.execute(
        "SELECT * FROM tx WHERE direction='out' AND amount_cents >= ? AND ts > ? AND ts <= ? ORDER BY ts",
        (threshold, since, now)).fetchall()
    store.set_state("last_alert_ts", now)
    if not rows:
        return None
    lines = ["💳 大额支出提醒（≥ ¥%s）" % util.fmt_cents(threshold), "─" * 16]
    for r in rows:
        lines.append("· %s %s ¥%s" % (r["ts"][5:16], (r["counterparty"] or r["item"] or r["category"])[:14],
                                      util.fmt_cents(r["amount_cents"])))
    body = "\n".join(lines)
    notify.send(cfg, store, "大额支出提醒", body, kind="alert")
    log("已推送大额提醒 %d 条" % len(rows))
    return body


def push_report(store, cfg, mode=None, kind="daily", day=None, log=_log):
    """生成当前区间账单并推送。"""
    data = reports.daily_report(store, cfg, day=day, mode=mode)
    text = reports.render_report(data, cfg)
    title = "账单 %s · 支出¥%s" % (data["label"], util.fmt_cents(data["out_cents"]))
    results = notify.send(cfg, store, title, text, kind=kind)
    store.set_state("last_report_ts", data["end"])
    store.set_state("last_report_text", text)
    ok = any(r.get("ok") for r in results)
    store.log_job("report_" + kind, util.now_str(), ok, json_safe(results))
    log("账单已推送：%s（%s）" % (title, "成功" if ok else "全部失败"))
    return {"report": data, "text": text, "title": title, "results": results}


def json_safe(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)[:800]
    except Exception:
        return str(obj)[:800]


def weekly_report(store, cfg, log=_log):
    """本周（周一到现在）账单。"""
    now = util.now()
    monday = now - timedelta(days=now.weekday())
    start = monday.strftime("%Y-%m-%d 00:00:00")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    data = reports.period_report(store, cfg, start, end, "本周 %s ~ %s" % (start[5:10], end[5:10]))
    data["weekday"] = util.weekday_cn(util.day_of(end))
    text = reports.render_report(data, cfg)
    title = "周报 · 支出¥%s" % util.fmt_cents(data["out_cents"])
    results = notify.send(cfg, store, title, text, kind="weekly")
    store.log_job("report_weekly", util.now_str(), any(r.get("ok") for r in results), json_safe(results))
    return {"report": data, "text": text, "title": title, "results": results}


def monthly_report(store, cfg, month=None, log=_log):
    """上个月整月账单。"""
    now = util.now()
    if month:
        y, m = int(month[:4]), int(month[5:7])
    else:
        y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    month = "%04d-%02d" % (y, m)
    start, last = util.month_days(month)
    data = reports.period_report(store, cfg, start + " 00:00:00", last + " 23:59:59", "%s 月账单" % month)
    text = reports.render_report(data, cfg)
    title = "月报 %s · 支出¥%s" % (month, util.fmt_cents(data["out_cents"]))
    results = notify.send(cfg, store, title, text, kind="monthly")
    store.log_job("report_monthly", util.now_str(), any(r.get("ok") for r in results), json_safe(results))
    return {"report": data, "text": text, "title": title, "results": results}


def ingest_notification(store, cfg, text, source=None, ts=None, package="", device="", loose=False):
    """接收手机端推送过来的通知/短信/快捷指令文本，解析并入库。"""
    rec, reason = parsers.parse_notification(text, source=source, ts=ts, package=package,
                                             device=device, loose=loose)
    if rec is None:
        # 认不出来不丢掉：进待录入队列，等用户在界面上一句话补上（参考 WhereIsMyMoney）
        pid = store.add_pending(text or "", source=source or "", package=package, device=device,
                                ts=ts or util.now_str(), reason=reason,
                                amount_cents=parsers.extract_amount_cents(text or ""))
        store.log_job("ingest_reject", util.now_str(), True, "%s | %s" % (reason, (text or "")[:60]))
        return {"ok": False, "reason": reason, "saved": False, "pending_id": pid}
    if categorize.should_neutralize(rec) and rec["direction"] != "neutral":
        rec["direction"] = "neutral"
    if cfg.get("categorize", {}).get("auto", True):
        res = categorize.apply_rules(store, rec)
        if res["action"] == "ignore":
            return {"ok": True, "status": "ignored", "saved": False, "record": {"ts": rec["ts"]}}
        if not rec.get("category"):
            c, sub = categorize.classify(store, rec)
            rec["category"], rec["sub_category"] = c, sub
    status, tx_id = store.add_tx(rec)
    if status == "new":
        check_large_amount(store, cfg)
    return {"ok": True, "status": status, "id": tx_id, "record": {
        "ts": rec["ts"], "amount": util.fmt_cents(rec["amount_cents"]), "direction": rec["direction"],
        "counterparty": rec["counterparty"], "category": rec.get("category"), "source": rec["source"]}}
