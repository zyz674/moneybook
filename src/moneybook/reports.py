# -*- coding: utf-8 -*-
"""报表聚合：区间汇总 / 每日账单 / 月度统计 / 预算状态 / 推送文案。"""
from datetime import datetime, timedelta

from . import util
from .importers import SOURCE_LABEL


def _range(start_ts, end_ts, inclusive=True):
    """返回 (时间条件, 参数)。inclusive=False 用左开区间，供「上次推送之后」使用。"""
    if inclusive:
        return ["ts >= ?", "ts <= ?"], [start_ts, end_ts]
    return ["ts > ?", "ts <= ?"], [start_ts, end_ts]


def now_hour(cfg=None):
    """当前小时（0-23），按东八区。"""
    return util.now().hour


def _window(conn, start_ts, end_ts, direction=None, inclusive=True):
    where, params = _range(start_ts, end_ts, inclusive)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    sql = "SELECT %s FROM tx WHERE %s" % (
        "COALESCE(SUM(CASE WHEN direction='in' THEN amount_cents ELSE 0 END),0) AS in_cents,"
        "COALESCE(SUM(CASE WHEN direction='out' THEN amount_cents ELSE 0 END),0) AS out_cents,"
        "COALESCE(SUM(CASE WHEN direction='neutral' THEN amount_cents ELSE 0 END),0) AS neutral_cents,"
        "COUNT(*) AS cnt,"
        "COALESCE(SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END),0) AS in_cnt,"
        "COALESCE(SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END),0) AS out_cnt,"
        "COALESCE(SUM(CASE WHEN category='未分类' OR category='' THEN 1 ELSE 0 END),0) AS uncat_cnt",
        " AND ".join(where))
    row = conn.execute(sql, params).fetchone()
    out = dict(row)
    out["net"] = out["in_cents"] - out["out_cents"]
    return out


def _group(conn, field, start_ts, end_ts, direction="out", limit=10, inclusive=True):
    cond, params = _range(start_ts, end_ts, inclusive)
    sql = ("SELECT COALESCE(NULLIF(%s,''),'未填写') AS k, COALESCE(SUM(amount_cents),0) AS total,"
           " COUNT(*) AS cnt FROM tx WHERE %s" % (field, " AND ".join(cond)))
    if direction:
        sql += " AND direction = ?"
        params.append(direction)
    sql += " GROUP BY k ORDER BY total DESC LIMIT ?"
    params.append(limit)
    return [{"key": r["k"], "total": r["total"], "count": r["cnt"]}
            for r in conn.execute(sql, params).fetchall()]


def _top_tx(conn, start_ts, end_ts, direction="out", limit=3, inclusive=True):
    cond, params = _range(start_ts, end_ts, inclusive)
    sql = ("SELECT id, ts, amount_cents, direction, counterparty, item, category, source FROM tx"
           " WHERE %s" % " AND ".join(cond))
    if direction:
        sql += " AND direction = ?"
        params.append(direction)
    sql += " ORDER BY amount_cents DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def summary(store, start_ts, end_ts, compare=True, inclusive=True):
    """任意区间的完整汇总。"""
    conn = store.conn
    data = _window(conn, start_ts, end_ts, inclusive=inclusive)
    data["start"] = start_ts
    data["end"] = end_ts
    data["by_category"] = _group(conn, "category", start_ts, end_ts, "out", 20, inclusive)
    data["by_income_category"] = _group(conn, "category", start_ts, end_ts, "in", 10, inclusive)
    data["by_source"] = _group(conn, "source", start_ts, end_ts, None, 10, inclusive)
    data["by_source_out"] = _group(conn, "source", start_ts, end_ts, "out", 10, inclusive)
    data["top_out"] = _top_tx(conn, start_ts, end_ts, "out", 3, inclusive)
    data["top_in"] = _top_tx(conn, start_ts, end_ts, "in", 3, inclusive)
    if compare:
        fmt = "%Y-%m-%d %H:%M:%S"
        a = datetime.strptime(start_ts, fmt)
        b = datetime.strptime(end_ts, fmt)
        span = b - a
        prev = _window(conn, (a - span).strftime(fmt), start_ts, inclusive=inclusive)
        data["prev"] = prev
        if prev["out_cents"] > 0:
            data["out_diff_pct"] = round((data["out_cents"] - prev["out_cents"]) * 100.0 / prev["out_cents"], 1)
        else:
            data["out_diff_pct"] = None
    # 近 7 天日均支出（含当天区间所在日期）
    day = util.day_of(end_ts)
    week_ago = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS s FROM tx WHERE direction='out' AND day >= ? AND day <= ?",
        (week_ago, day)).fetchone()
    data["week_avg_out"] = int(row["s"] / 7.0)
    data["week_total_out"] = row["s"]
    return data


def month_status(store, cfg, month=None):
    """本月累计与预算使用情况。"""
    month = month or util.now().strftime("%Y-%m")
    conn = store.conn
    start = month + "-01 00:00:00"
    end = month + "-31 23:59:59"
    w = _window(conn, start, end)
    budget = cfg.get("budget", {}) or {}
    total_budget = util.parse_cents(budget.get("monthly") or 0) or 0
    cats = budget.get("categories") or {}
    used = {}
    for item in _group(conn, "category", start, end, "out", 50):
        used[item["key"]] = item["total"]
    cat_status = []
    for name, amount in cats.items():
        limit = util.parse_cents(amount) or 0
        if limit <= 0:
            continue
        spent = used.get(name, 0)
        cat_status.append({"category": name, "budget": limit, "spent": spent,
                           "percent": round(spent * 100.0 / limit, 1) if limit else 0})
    cat_status.sort(key=lambda x: x["percent"], reverse=True)
    subs = store.recurring("out")
    sub_total = sum(s["amount_cents"] for s in subs)
    return {
        "recurring": subs[:12],
        "recurring_count": len(subs),
        "recurring_cents": sub_total,
        "month": month,
        "out_cents": w["out_cents"],
        "in_cents": w["in_cents"],
        "net": w["net"],
        "count": w["cnt"],
        "out_cnt": w["out_cnt"],
        "in_cnt": w["in_cnt"],
        "budget": total_budget,
        "budget_percent": round(w["out_cents"] * 100.0 / total_budget, 1) if total_budget else 0,
        "budget_left": total_budget - w["out_cents"] if total_budget else 0,
        "category_budget": cat_status,
        "days_in_month": util.month_days(month)[1][-2:],
        "day_of_month": int(util.now().strftime("%d")),
    }


def period_report(store, cfg, start_ts, end_ts, label="", inclusive=True):
    data = summary(store, start_ts, end_ts, inclusive=inclusive)
    data["label"] = label
    data["month"] = month_status(store, cfg)
    return data


def daily_report(store, cfg, day=None, mode=None):
    """生成一份「每日账单」。mode: today | yesterday | since_last"""
    mode = mode or util.get(cfg, "reports.daily_mode", "auto")
    if mode == "auto":
        # 凌晨那次汇总「刚过去的自然日」，白天/晚上那次汇总「距上次推送以来的流水」
        mode = "yesterday" if now_hour(cfg) < 3 else "since_last"
    fmt = "%Y-%m-%d %H:%M:%S"
    now = util.now()
    inclusive = True
    today = now.strftime("%Y-%m-%d")
    if mode == "today":
        d = day or today
        start = d + " 00:00:00"
        end = (now.strftime(fmt) if d == today else d + " 23:59:59")
        label = "%s 今日（截至 %s）" % (d, end[11:16]) if d == today else "%s 全天" % d
    elif mode == "yesterday":
        d = day or (now - timedelta(days=1)).strftime("%Y-%m-%d")
        start = d + " 00:00:00"
        end = d + " 23:59:59"
        label = "%s 全天" % d
    else:
        last = store.get_state("last_report_ts")
        if last:
            start = last
            inclusive = False
        else:
            start = (now - timedelta(hours=24)).strftime(fmt)
            inclusive = True
        end = now.strftime(fmt)
        label = "%s ~ %s" % (start[5:16], end[5:16])
    data = period_report(store, cfg, start, end, label, inclusive=inclusive)
    data["day"] = util.day_of(end)
    data["mode"] = mode
    data["weekday"] = util.weekday_cn(util.day_of(end))
    return data


def bar(percent, width=10):
    filled = int(round(max(0.0, min(100.0, percent)) / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


def render_report(data, cfg=None, with_details=True):
    """把汇总渲染成适合手机通知阅读的纯文本。"""
    cfg = cfg or {}
    lines = []
    head = data.get("label") or ""
    wk = data.get("weekday") or ""
    lines.append("📊 记账本 · %s%s" % (head, (" " + wk) if wk else ""))
    lines.append("─" * 16)
    out_c = data["out_cents"]
    in_c = data["in_cents"]
    lines.append("💸 支出 ¥%s（%d 笔）" % (util.fmt_cents(out_c), data["out_cnt"]))
    lines.append("💰 收入 ¥%s（%d 笔）" % (util.fmt_cents(in_c), data["in_cnt"]))
    lines.append("📉 结余 %s¥%s" % ("-" if data["net"] < 0 else "+", util.fmt_cents(abs(data["net"]))))
    if data.get("neutral_cents"):
        lines.append("🔄 转账/还款等不计收支 ¥%s" % util.fmt_cents(data["neutral_cents"]))
    diff = data.get("out_diff_pct")
    if diff is not None:
        arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
        lines.append("📈 较上一周期 %s%.1f%%" % (arrow, abs(diff)))

    if with_details and data.get("by_category"):
        lines.append("─" * 16)
        lines.append("分类占比")
        top = data["by_category"][:6]
        for item in top:
            pct = item["total"] * 100.0 / out_c if out_c else 0
            lines.append("  %-6s ¥%-9s %s %.0f%%" % (item["key"][:6], util.fmt_cents(item["total"]), bar(pct), pct))
        if len(data["by_category"]) > 6:
            others = sum(i["total"] for i in data["by_category"][6:])
            lines.append("  其他   ¥%-9s %s %.0f%%" % (util.fmt_cents(others), bar(others * 100.0 / out_c if out_c else 0),
                                                    others * 100.0 / out_c if out_c else 0))

    if with_details and data.get("top_out"):
        lines.append("─" * 16)
        lines.append("最大支出")
        for tx in data["top_out"][:3]:
            name = (tx["counterparty"] or tx["item"] or tx["category"] or "未知")[:14]
            lines.append("  · %s ¥%s" % (name, util.fmt_cents(tx["amount_cents"])))

    if with_details and data.get("by_source_out"):
        parts = ["%s ¥%s" % (SOURCE_LABEL.get(s["key"], s["key"]), util.fmt_cents(s["total"]))
                 for s in data["by_source_out"]]
        if parts:
            lines.append("🏦 " + "｜".join(parts))

    mon = data.get("month") or {}
    if mon.get("recurring_count"):
        lines.append("🔁 固定支出 %d 项，每月约 ¥%s" % (mon["recurring_count"], util.fmt_cents(mon["recurring_cents"])))
    if mon.get("budget"):
        pct = mon["budget_percent"]
        lines.append("─" * 16)
        lines.append("📅 本月累计支出 ¥%s｜预算 ¥%s（%.1f%%）%s"
                     % (util.fmt_cents(mon["out_cents"]), util.fmt_cents(mon["budget"]), pct, bar(pct)))
        if mon.get("budget_left", 0) < 0:
            lines.append("⚠️ 已超预算 ¥%s" % util.fmt_cents(abs(mon["budget_left"])))
        for cb in (mon.get("category_budget") or [])[:3]:
            if cb["percent"] >= 80:
                lines.append("⚠️ %s 已用 %.0f%%（¥%s/¥%s）" % (cb["category"], cb["percent"],
                                                          util.fmt_cents(cb["spent"]), util.fmt_cents(cb["budget"])))
    elif mon.get("out_cents"):
        lines.append("📅 本月累计支出 ¥%s" % util.fmt_cents(mon["out_cents"]))

    if data.get("uncat_cnt"):
        lines.append("❓ %d 笔未分类，打开记账本确认一下" % data["uncat_cnt"])
    if data.get("cnt") == 0:
        lines.append("（本区间没有新流水）")
    return "\n".join(lines)
