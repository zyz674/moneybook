# -*- coding: utf-8 -*-
"""HTTP 服务：REST API + 手机端 Web UI（PWA）。

只用标准库，方便直接丢到电脑 / 手机 Termux / 服务器上运行。
"""
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import categorize, config as cfgmod, importers, jobs, notify, parsers, reports, util
from .db import Store
from .scheduler import Scheduler

WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")


class App(object):
    """应用上下文：配置 + 存储 + 调度器。"""

    def __init__(self, cfg=None):
        self.cfg = cfg or cfgmod.load_config()
        root = cfgmod.project_root()
        self.cfg["_root"] = root
        self.cfg["_db_path"] = cfgmod.resolve(self.cfg, "db_path", "data/moneybook.db")
        self.cfg["_inbox_dir"] = cfgmod.resolve(self.cfg, "inbox_dir", "data/inbox")
        self.cfg["_archive_dir"] = cfgmod.resolve(self.cfg, "archive_dir", "data/inbox/done")
        os.makedirs(self.cfg["_inbox_dir"], exist_ok=True)
        os.makedirs(self.cfg["_archive_dir"], exist_ok=True)
        self.store = Store(self.cfg["_db_path"])
        self.store.init_schema()
        seeded = categorize.seed_default_rules(self.store)
        if seeded:
            print("[moneybook] 已写入 %d 条默认分类规则" % seeded)
        self.scheduler = Scheduler(self.store, self.cfg, log=lambda m: print(m, flush=True))
        self.started_at = util.now_str()

    @property
    def token(self):
        return (self.cfg.get("access_token") or "").strip()


def _ip_rank(ip):
    """越小越可能是真实的局域网地址（用于手机访问）。"""
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if re.match(r"^172.(1[6-9]|2[0-9]|3[01]).", ip):
        return 2
    if ip.startswith("127."):
        return 90
    if ip.startswith("169.254."):
        return 95
    if ip.startswith("198.18.") or ip.startswith("198.19."):   # 常见虚拟/测速网卡
        return 96
    return 50


def _enumerate_ipv4():
    """尽量把所有网卡的 IPv4 都列出来（含被虚拟网卡抢走默认路由的情况）。"""
    found = []
    if os.name == "nt":
        try:
            out = subprocess.run(["ipconfig"], capture_output=True, timeout=10).stdout
            found += [m.decode() for m in re.findall(rb"IPv4[^\r\n]*?([0-9]{1,3}(?:\.[0-9]{1,3}){3})", out)]
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        for cmd in (["hostname", "-I"], ["ip", "-4", "-o", "addr"], ["ifconfig"]):
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=6).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.SubprocessError):
                continue
            ips = re.findall(r"(d{1,3}(?:.d{1,3}){3})", out)
            if ips:
                found += ips
                break
    return found


def lan_ips():
    """列出可供手机访问的局域网 IPv4 地址，按「像不像真内网」排序。"""
    found = list(_enumerate_ipv4())
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # 不会真的发包，只是让系统选出出口网卡
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    uniq = []
    for ip in found:
        if ip and ip not in uniq and not ip.startswith("127."):
            uniq.append(ip)
    return sorted(uniq, key=_ip_rank)


class Handler(BaseHTTPRequestHandler):
    server_version = "moneybook/1.0"
    app = None                    # 由 create_server 注入
    protocol_version = "HTTP/1.1"

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):
        if os.environ.get("MONEYBOOK_QUIET"):
            return
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token, X-Filename")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str), "application/json; charset=utf-8")

    def _error(self, msg, code=400, **extra):
        payload = {"ok": False, "error": msg}
        payload.update(extra)
        self._json(payload, code)

    def handle_one_request(self):
        """HTTP/1.1 长连接下 handler 实例会被复用：每个请求都必须重新读 body，
        否则第二个 POST 会拿到上一个请求的请求体（曾导致补录/试算算的是别的数据）。"""
        self._raw_body = None
        return BaseHTTPRequestHandler.handle_one_request(self)

    def _body_bytes(self):
        cached = getattr(self, "_raw_body", None)
        if cached is not None:
            return cached
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        self._raw_body = self.rfile.read(length) if length > 0 else b""
        return self._raw_body

    def _is_json_request(self):
        return "json" in (self.headers.get("Content-Type") or "").lower()

    def _json_body(self):
        raw = self._body_bytes()
        if not raw:
            return {}
        if not self._is_json_request():
            # 纯文本/二进制上传：原样保留，交给具体接口按需解码
            return {"_raw": raw.decode("utf-8", "replace"), "_bytes": raw}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {"_value": data}
        except (ValueError, UnicodeDecodeError):
            return {"_raw": raw.decode("utf-8", "replace"), "_bytes": raw}

    def _q(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _authorized(self, query):
        token = self.app.token
        if not token:
            return True
        given = (self.headers.get("X-Token") or "").strip()
        if not given:
            given = (query.get("token") or [""])[0].strip()
        if not given:
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                if part.strip().startswith("mb_token="):
                    given = part.strip()[len("mb_token="):]
        return given == token

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = self._q()
        if path == "/quick":
            if not self._authorized(query):
                return self._send(401, "需要访问口令：请在链接后面加上 &token=你的口令", "text/plain; charset=utf-8")
            return self.quick(query)
        if path.startswith("/api/") and path != "/api/ping":
            if not self._authorized(query):
                return self._error("需要访问口令", 401)
        try:
            if path.startswith("/api/"):
                return self.api_get(path, query)
            if path == "/login":
                token = (query.get("token") or [""])[0]
                if token == self.app.token:
                    return self._send(200, "登录成功，可以关闭此页面", "text/plain; charset=utf-8",
                                      extra={"Set-Cookie": "mb_token=%s; Path=/; Max-Age=31536000" % token})
                return self._send(403, "口令不正确", "text/plain; charset=utf-8")
            return self.static(path)
        except Exception as exc:
            return self._error("服务器异常：%s: %s" % (type(exc).__name__, exc), 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        query = self._q()
        if not self._authorized(query):
            return self._error("需要访问口令", 401)
        try:
            return self.api_post(path, query)
        except Exception as exc:
            return self._error("服务器异常：%s: %s" % (type(exc).__name__, exc), 500)

    def do_PATCH(self):
        self.do_POST()

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if not self._authorized(self._q()):
            return self._error("需要访问口令", 401)
        parts = path.split("/")
        if len(parts) == 4 and parts[2] == "tx":
            self.app.store.delete_tx(int(parts[3]))
            return self._json({"ok": True})
        if len(parts) == 4 and parts[2] == "rules":
            self.app.store.delete_rule(int(parts[3]))
            return self._json({"ok": True})
        return self._error("未知接口", 404)

    # ---------- 静态资源 ----------
    def static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEBUI_DIR, rel))
        if not full.startswith(WEBUI_DIR) or not os.path.isfile(full):
            return self._send(404, "404 Not Found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/manifest+json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        return self._send(200, data, ctype)

    # ---------- 一键快记（快捷指令 / 自动化用） ----------
    def quick(self, q):
        """GET /quick?text=微信支付：已支付¥12.50，收款方肯德基&format=text

        手机快捷指令里只要「获取 URL 内容」一个动作就能记账。
        """
        one = lambda k, d=None: (q.get(k) or [d])[0]      # noqa: E731
        text = (one("text") or one("content") or "").strip()
        fmt = (one("format") or "").lower()
        want_text = fmt in ("text", "plain", "txt")
        if not text:
            return self._send(400, "缺少 text 参数，例如 /quick?text=已支付12.5元 肯德基" if want_text
                              else "<h3>缺少 text 参数</h3>", "text/plain; charset=utf-8" if want_text else "text/html; charset=utf-8")
        res = jobs.ingest_notification(self.app.store, self.app.cfg, text, one("source"), one("ts"),
                                       one("package", ""), "quick", loose=True)
        if not res.get("ok"):
            msg = "⚠️ 没识别出交易：" + str(res.get("reason"))
        else:
            rec = res.get("record") or {}
            status = res.get("status")
            head = {"new": "✅ 已记账", "dup": "🔁 这笔已经记过了", "merged": "🔗 已并入已有记录"}.get(status, "✅ 已记账")
            msg = "%s %s %s · %s · %s" % (head, "¥" + str(rec.get("amount")),
                                          {"in": "收入", "out": "支出", "neutral": "不计收支"}.get(rec.get("direction"), ""),
                                          rec.get("category") or "", rec.get("counterparty") or "")
        if want_text:
            return self._send(200, msg, "text/plain; charset=utf-8")
        html = ("<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>记账本</title><link rel=stylesheet href=/style.css></head><body>"
                "<main><div class=card><h2>一键快记</h2><p style='font-size:17px;font-weight:600'>" + msg + "</p>"
                "<p class=mini style='margin-top:10px'>原文：" + urllib.parse.quote(text)[:0] + self._esc(text) + "</p>"
                "<div class=row style='gap:8px;margin-top:14px'><a class=btn style='text-align:center;text-decoration:none' href='/'>回到记账本</a></div>"
                "</div></main></body></html>")
        return self._send(200, html, "text/html; charset=utf-8")

    @staticmethod
    def _esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    # ---------- GET API ----------
    def api_get(self, path, q):
        app = self.app
        store = app.store
        one = lambda k, d=None: (q.get(k) or [d])[0]   # noqa: E731

        if path == "/api/ping":
            return self._json({"ok": True, "app": "moneybook", "version": "1.0.0",
                               "time": util.now_str(), "auth_required": bool(app.token)})

        if path == "/api/overview":
            day = one("day") or util.today_str()
            month = one("month") or util.now().strftime("%Y-%m")
            m_start, m_end = util.month_days(month)
            daily = store.daily_series(m_start, m_end)
            days = util.date_range(m_start, m_end)
            if month == util.now().strftime("%Y-%m"):
                days = [d for d in days if d <= day]      # 当月只画到今天，后面是空白天
            series = [{"day": d, "out": daily.get(d, {}).get("out", 0), "in": daily.get(d, {}).get("in", 0)}
                      for d in days]
            return self._json({
                "ok": True,
                "series": series,
                "today": reports.period_report(store, app.cfg, day + " 00:00:00", day + " 23:59:59", day),
                "month": reports.month_status(store, app.cfg, month),
                "scheduler": app.scheduler.status(),
                "counts": {"total": store.count_tx(), "uncategorized": store.query(uncategorized=True, limit=1)[1],
                           "pending": store.count_pending()},
                "last_sync": store.get_state("last_sync_ts"),
                "last_report_text": store.get_state("last_report_text"),
                "server_time": util.now_str(),
            })

        if path == "/api/summary":
            start, end = one("start"), one("end")
            if one("day"):
                start, end = one("day") + " 00:00:00", one("day") + " 23:59:59"
            if one("month"):
                m = one("month")
                s, e = util.month_days(m)
                start, end = s + " 00:00:00", e + " 23:59:59"
            if not start:
                start = util.today_str() + " 00:00:00"
            if not end:
                end = util.today_str() + " 23:59:59"
            return self._json({"ok": True, "summary": reports.summary(store, start, end)})

        if path == "/api/stats":
            month = one("month") or util.now().strftime("%Y-%m")
            s, e = util.month_days(month)
            conn = store.conn
            series = []
            daily = store.daily_series(s, e)
            for day in util.date_range(s, e):
                d = daily.get(day, {"in": 0, "out": 0})
                series.append({"day": day, "in": d.get("in", 0), "out": d.get("out", 0)})
            cats = conn.execute(
                "SELECT category, COALESCE(SUM(amount_cents),0) AS total, COUNT(*) AS cnt FROM tx"
                " WHERE month=? AND direction='out' GROUP BY category ORDER BY total DESC", (month,)).fetchall()
            inc = conn.execute(
                "SELECT category, COALESCE(SUM(amount_cents),0) AS total FROM tx"
                " WHERE month=? AND direction='in' GROUP BY category ORDER BY total DESC", (month,)).fetchall()
            methods = conn.execute(
                "SELECT COALESCE(NULLIF(method,''),'未知') AS k, COALESCE(SUM(amount_cents),0) AS total FROM tx"
                " WHERE month=? AND direction='out' GROUP BY k ORDER BY total DESC LIMIT 8", (month,)).fetchall()
            return self._json({"ok": True, "month": month, "daily": series,
                               "categories": [dict(r) for r in cats], "income_categories": [dict(r) for r in inc],
                               "methods": [dict(r) for r in methods],
                               "month_status": reports.month_status(store, app.cfg, month)})

        if path == "/api/tx":
            rows, total = store.query(
                day_from=one("from"), day_to=one("to"), source=one("source"), category=one("category"),
                direction=one("direction"), keyword=one("q"), month=one("month"),
                limit=int(one("limit", 200)), offset=int(one("offset", 0)),
                order=one("order", "desc"), uncategorized=(one("uncategorized") == "1"))
            return self._json({"ok": True, "total": total, "items": rows})

        if path.startswith("/api/tx/"):
            row = store.get_tx(int(path.rsplit("/", 1)[1]))
            return self._json({"ok": bool(row), "item": row})

        if path == "/api/report/daily":
            data = reports.daily_report(store, app.cfg, day=one("day"), mode=one("mode"))
            if one("format") == "text":
                return self._send(200, reports.render_report(data, app.cfg), "text/plain; charset=utf-8")
            return self._json({"ok": True, "report": data, "text": reports.render_report(data, app.cfg)})

        if path == "/api/report/preview":
            mode = one("mode", "today")
            data = reports.daily_report(store, app.cfg, day=one("day"), mode=mode)
            return self._send(200, reports.render_report(data, app.cfg), "text/plain; charset=utf-8")

        if path == "/api/settings":
            safe = dict(app.cfg)
            for k in list(safe):
                if k.startswith("_"):
                    safe.pop(k)
            return self._json({"ok": True, "settings": safe, "token_set": bool(app.token),
                               "paths": {"db": app.cfg["_db_path"], "inbox": app.cfg["_inbox_dir"]},
                               "channels_supported": ["bark", "serverchan", "pushplus", "ntfy", "telegram",
                                                      "wecom", "dingtalk", "feishu", "webhook", "console"]})

        if path == "/api/rules":
            items = store.list_rules(enabled_only=False)
            return self._json({"ok": True, "items": items, "total": len(items),
                               "custom": sum(1 for r in items if not r.get("builtin")),
                               "fields": categorize.FIELD_LABEL, "actions": categorize.ACTION_LABEL})

        if path == "/api/pending":
            items = store.list_pending(one("status", "open"), int(one("limit", 100)))
            return self._json({"ok": True, "items": items, "total": store.count_pending(),
                               "hint": "这些是没认出来的通知。补上金额和商户就能入账，顺手还能生成一条规则。"})

        if path == "/api/subscriptions":
            subs = store.recurring("out")
            return self._json({"ok": True, "items": subs,
                               "monthly_total": sum(s["amount_cents"] for s in subs),
                               "yearly_total": sum(s["amount_cents"] for s in subs) * 12})

        if path == "/api/merchants":
            month = one("month") or util.now().strftime("%Y-%m")
            rows = store.conn.execute(
                "SELECT counterparty AS name, COUNT(*) AS cnt, COALESCE(SUM(amount_cents),0) AS total,"
                " MAX(day) AS last_day, MAX(category) AS category FROM tx"
                " WHERE direction='out' AND counterparty<>'' AND month=?"
                " GROUP BY counterparty ORDER BY total DESC LIMIT ?", (month, int(one("limit", 25)))).fetchall()
            return self._json({"ok": True, "month": month, "items": [dict(r) for r in rows]})

        if path == "/api/imports":
            return self._json({"ok": True, "items": store.list_imports(100)})

        if path == "/api/jobs":
            return self._json({"ok": True, "items": store.list_jobs(50), "scheduler": app.scheduler.status()})

        if path == "/api/pushes":
            rows = store.conn.execute("SELECT * FROM pushes ORDER BY id DESC LIMIT 50").fetchall()
            return self._json({"ok": True, "items": [dict(r) for r in rows]})

        if path == "/api/uncategorized":
            rows, total = store.query(uncategorized=True, limit=int(one("limit", 100)))
            return self._json({"ok": True, "total": total, "items": rows})

        if path == "/api/server-info":
            port = app.cfg.get("port")
            return self._json({"ok": True, "ips": lan_ips(), "port": port,
                               "urls": ["http://%s:%s" % (ip, port) for ip in lan_ips()],
                               "token": app.token, "webui_dir": WEBUI_DIR})

        if path == "/api/export.csv":
            rows, _ = store.query(limit=100000, order="asc")
            head = "时间,来源,方向,金额,分类,子分类,交易对方,商品说明,支付方式,状态,备注\n"
            lines = [head]
            label = {"in": "收入", "out": "支出", "neutral": "不计收支"}
            for r in rows:
                lines.append(",".join('"%s"' % str(v).replace('"', "'") for v in (
                    r["ts"], importers.SOURCE_LABEL.get(r["source"], r["source"]), label.get(r["direction"], r["direction"]),
                    util.fmt_cents(r["amount_cents"]), r["category"], r["sub_category"], r["counterparty"],
                    r["item"], r["method"], r["status"], r["note"])))
            return self._send(200, "\ufeff" + "\n".join(lines), "text/csv; charset=utf-8",
                              extra={"Content-Disposition": "attachment; filename=moneybook.csv"})

        return self._error("未知接口：%s" % path, 404)

    # ---------- POST API ----------
    def api_post(self, path, q):
        app = self.app
        store = app.store
        body = self._json_body()

        if path == "/api/ingest":
            raw_text = body.get("_raw") or ""
            texts = body.get("texts") or []
            text = body.get("text") or raw_text
            if text and not texts:
                texts = [text]
            if not texts:
                return self._error("缺少 text 字段")
            results = []
            for item in texts:
                if isinstance(item, dict):
                    results.append(jobs.ingest_notification(
                        store, app.cfg, item.get("text", ""), item.get("source"), item.get("ts"),
                        item.get("package", ""), item.get("device", "")))
                else:
                    results.append(jobs.ingest_notification(
                        store, app.cfg, item, body.get("source"), body.get("ts"),
                        body.get("package", ""), body.get("device", "")))
            saved = sum(1 for r in results if r.get("saved") or r.get("ok"))
            return self._json({"ok": True, "saved": saved, "total": len(results), "results": results})

        if path == "/api/import":
            filename = (body.get("filename") or self.headers.get("X-Filename")
                        or body.get("name") or "upload.csv")
            if isinstance(body.get("text"), str) and body["text"].strip():
                text = body["text"]
            else:
                # 原始字节交给服务端探测编码（支付宝导出多为 GBK，微信为 UTF-8 BOM）
                from .importers import base as ibase
                text = ibase.decode_bytes(self._body_bytes())
            if not text.strip():
                return self._error("文件内容为空")
            stats = importers.import_text(store, app.cfg, text, filename,
                                          source=body.get("source"), force=bool(body.get("force")))
            return self._json({"ok": True, "stats": stats})

        if path == "/api/sync":
            res = jobs.sync_now(store, app.cfg, reason="manual")
            res["alert"] = jobs.check_large_amount(store, app.cfg)
            return self._json({"ok": res.get("ok", False), "sync": res, "scheduler": app.scheduler.status()})

        if path == "/api/report/push":
            kind = body.get("kind") or "daily"
            mode = body.get("mode")
            if kind == "weekly":
                res = jobs.weekly_report(store, app.cfg)
            elif kind == "monthly":
                res = jobs.monthly_report(store, app.cfg, body.get("month"))
            else:
                res = jobs.push_report(store, app.cfg, mode=mode)
            return self._json({"ok": True, "text": res["text"], "title": res["title"], "results": res["results"]})

        if path == "/api/tx":
            cents = util.parse_cents(body.get("amount"))
            if cents is None:
                return self._error("金额格式不正确")
            direction = body.get("direction") or ("in" if cents < 0 else "out")
            ts = util.parse_datetime_loose(body.get("ts")) or util.now_str()
            rec = {
                "source": body.get("source") or "manual",
                "origin": "manual",
                "ts": ts,
                "amount_cents": abs(cents),
                "direction": direction,
                "counterparty": body.get("counterparty", ""),
                "item": body.get("item", ""),
                "category": body.get("category") or categorize.UNCATEGORIZED,
                "sub_category": body.get("sub_category", ""),
                "method": body.get("method", ""),
                "note": body.get("note", ""),
                "raw": "[手工记账]",
            }
            if app.cfg.get("categorize", {}).get("auto", True):
                res = categorize.apply_rules(store, rec)
                if res["action"] == "ignore":
                    return self._json({"ok": False, "error": "这条被规则标记为「忽略」了，没有入账"})
                if not rec.get("category") or rec["category"] == categorize.UNCATEGORIZED:
                    c, sub = categorize.classify(store, rec)
                    rec["category"], rec["sub_category"] = c, sub
            status, tx_id = store.add_tx(rec)
            return self._json({"ok": True, "status": status, "id": tx_id})

        if path.startswith("/api/tx/"):
            tx_id = int(path.rsplit("/", 1)[1])
            old = store.get_tx(tx_id)
            fields = {}
            if "amount" in body:
                cents = util.parse_cents(body["amount"])
                if cents is None:
                    return self._error("金额格式不正确")
                fields["amount_cents"] = abs(cents)
            for key in ("category", "sub_category", "note", "direction", "counterparty", "item", "method", "tags"):
                if key in body:
                    fields[key] = body[key]
            if "direction" in fields:
                fields["direction"] = importers.base.guess_direction(fields["direction"], None)
            if not fields:
                return self._error("没有需要更新的字段")
            store.update_tx(tx_id, **fields)
            learned = None
            if old and "category" in body and app.cfg.get("categorize", {}).get("learn_from_manual", True):
                learned = categorize.learn_from_edit(store, old, body["category"], body.get("sub_category", ""))
            return self._json({"ok": True, "learned_rule": learned, "item": store.get_tx(tx_id)})

        if path == "/api/recategorize":
            n = categorize.autocategorize_pending(store, limit=int(body.get("limit") or 2000))
            return self._json({"ok": True, "updated": n})

        if path == "/api/settings":
            allowed = ("port", "host", "access_token", "sync", "reports", "budget", "alerts", "notify",
                       "categorize", "privacy", "inbox_dir", "archive_dir", "db_path")
            for key, value in (body or {}).items():
                if key not in allowed:
                    continue
                cur = app.cfg.get(key)
                # 深层合并：表单只提交了部分字段时，不覆盖其它已有配置
                if isinstance(cur, dict) and isinstance(value, dict):
                    app.cfg[key] = cfgmod._deep_merge(cur, value)
                else:
                    app.cfg[key] = value
            cfgmod.save_config({k: v for k, v in app.cfg.items() if not k.startswith("_")})
            app.scheduler.cfg = app.cfg
            return self._json({"ok": True, "settings": {k: v for k, v in app.cfg.items() if not k.startswith("_")}})

        if path == "/api/rules":
            action = (body.get("action") or "categorize").strip()
            kw = (body.get("keyword") or "").strip()
            cat = (body.get("category") or "").strip()
            lo = util.parse_cents(body.get("min_amount")) if body.get("min_amount") not in (None, "") else None
            hi = util.parse_cents(body.get("max_amount")) if body.get("max_amount") not in (None, "") else None
            if action == "categorize" and not cat:
                return self._error("归类规则要选一个分类")   # 「不计收支」可以不指定分类
            if action == "ignore" and not (kw or lo is not None or hi is not None):
                return self._error("忽略规则至少要有条件，否则会把所有交易都吞掉")
            if action == "tag" and not (body.get("tags") or "").strip():
                return self._error("打标签规则要填标签名")
            if not kw and lo is None and hi is None and not body.get("time_from") and not body.get("method"):
                return self._error("至少要有一个条件：关键词、金额区间、时间段或支付方式")
            sig = store.add_rule(
                keyword=kw, category=cat, sub_category=body.get("sub_category", ""),
                priority=int(body.get("priority") or 50), direction=body.get("direction", ""),
                field=body.get("field", "any"), source=body.get("source", ""),
                method=body.get("method", ""), min_cents=lo, max_cents=hi,
                time_from=body.get("time_from", ""), time_to=body.get("time_to", ""),
                action=action, tags=body.get("tags", ""), note=body.get("note", ""),
                full_match=bool(body.get("full_match")))
            return self._json({"ok": True, "sig": sig, "items": store.list_rules(enabled_only=False)})

        if path == "/api/rules/test":
            sample = {
                "source": body.get("source") or "wechat", "ts": body.get("ts") or util.now_str(),
                "amount_cents": abs(util.parse_cents(body.get("amount")) or 0),
                "direction": body.get("direction") or "out",
                "counterparty": body.get("counterparty", ""), "item": body.get("item", ""),
                "category_raw": body.get("category_raw", ""), "method": body.get("method", ""),
                "raw": body.get("raw") or body.get("text", ""),
            }
            rules = store.list_rules(enabled_only=True)
            res = categorize.evaluate(store, sample, rules)
            rule = res.get("rule") or {}
            return self._json({"ok": True, "matched": bool(res["action"] or res["tags"]),
                               "action": res["action"] or ("tag" if res["tags"] else ""),
                               "category": res["category"], "sub_category": res["sub_category"],
                               "tags": res["tags"],
                               "rule": {k: rule.get(k) for k in ("id", "keyword", "field", "category",
                                                                 "sub_category", "priority", "note")} if rule else None})

        if path.startswith("/api/pending/"):
            pid = int(path.rsplit("/", 1)[1])
            row = store.get_pending(pid)
            if not row:
                return self._error("这条待录入记录不在了", 404)
            if body.get("action") == "ignore":
                store.close_pending(pid, "ignored")
                return self._json({"ok": True, "status": "ignored"})
            cents = util.parse_cents(body.get("amount")) if body.get("amount") not in (None, "") else None
            if cents is None:
                cents = row.get("amount_cents")
            if not cents:
                return self._error("请先填金额")
            ts = util.parse_datetime_loose(body.get("ts")) or util.parse_datetime_loose(row.get("ts")) or util.now_str()
            rec = {
                "source": body.get("source") or row.get("source") or "other",
                "origin": "manual", "ext_id": "",
                "ts": ts, "amount_cents": abs(cents),
                "direction": body.get("direction") or "out",
                "counterparty": (body.get("counterparty") or "").strip(),
                "item": (body.get("item") or "").strip(),
                "category": (body.get("category") or "").strip(),
                "sub_category": body.get("sub_category", ""),
                "raw": "[待录入补记] " + (row.get("raw") or "")[:300],
            }
            if not rec["counterparty"]:
                guess, _ = parsers.parse_notification(row.get("raw") or "", loose=True)
                if guess:
                    rec["counterparty"] = guess.get("counterparty", "")
                    rec["item"] = rec["item"] or guess.get("item", "")
            if app.cfg.get("categorize", {}).get("auto", True) and not rec["category"]:
                c, sub = categorize.classify(store, rec)
                rec["category"], rec["sub_category"] = c, sub
            status, tx_id = store.add_tx(rec)
            store.close_pending(pid, "done", tx_id)
            learned = None
            if rec["category"] and app.cfg.get("categorize", {}).get("learn_from_manual", True):
                learned = categorize.learn_from_edit(store, rec, rec["category"], rec["sub_category"])
            return self._json({"ok": True, "status": status, "id": tx_id, "learned_rule": learned,
                               "item": store.get_tx(tx_id)})

        if path == "/api/merchants/recategorize":
            name = (body.get("counterparty") or "").strip()
            cat = (body.get("category") or "").strip()
            if not name or not cat:
                return self._error("商户和分类都要填")
            sub = body.get("sub_category", "")
            ids = [r["id"] for r in store.conn.execute(
                "SELECT id FROM tx WHERE counterparty=?", (name,)).fetchall()]
            for tx_id in ids:
                store.update_tx(tx_id, category=cat, sub_category=sub)
            store.add_rule(name, cat, sub, priority=5, field="peer", note="从商户批量修改学习")
            return self._json({"ok": True, "updated": len(ids), "category": cat})

        if path == "/api/notify/test":
            results = notify.send(app.cfg, store, "记账本推送测试",
                                  "如果你收到这条消息，说明推送通道配置成功。\n" + util.now_str(), kind="test")
            return self._json({"ok": any(r.get("ok") for r in results), "results": results})

        if path == "/api/scheduler/run":
            name = body.get("job") or "sync"
            if name == "sync":
                return self._json({"ok": True, "result": jobs.sync_now(store, app.cfg, "api")})
            if name == "daily":
                res = jobs.push_report(store, app.cfg, mode=body.get("mode"))
                return self._json({"ok": True, "text": res["text"], "results": res["results"]})
            return self._error("未知任务")

        return self._error("未知接口：%s" % path, 404)


def create_server(app, host=None, port=None):
    host = host or app.cfg.get("host", "0.0.0.0")
    port = int(port or app.cfg.get("port", 8787))
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    argv = argv or sys.argv[1:]
    cfg = cfgmod.load_config()
    if "--port" in argv:
        cfg["port"] = int(argv[argv.index("--port") + 1])
    if "--host" in argv:
        cfg["host"] = argv[argv.index("--host") + 1]
    if "--token" in argv:
        cfg["access_token"] = argv[argv.index("--token") + 1]
    app = App(cfg)
    httpd = create_server(app, cfg.get("host"), cfg.get("port"))
    if "--no-scheduler" not in argv:
        app.scheduler.start()
    ips = lan_ips()
    print("=" * 56)
    print("  记账本 moneybook 已启动")
    print("  本机：http://127.0.0.1:%s" % cfg["port"])
    for ip in ips:
        print("  手机：http://%s:%s   （手机需与电脑同一 WiFi）" % (ip, cfg["port"]))
    if app.token:
        for ip in ips[:1]:
            print("  手机登录链接：http://%s:%s/login?token=%s" % (ip, cfg["port"], app.token))
    else:
        print("  ⚠ 未设置访问口令：同一 WiFi 下的任何人都能打开记账本。")
        print("    建议在 config.json 里填 access_token，比如：%s" % secrets.token_hex(6))
    print("  数据文件：%s" % cfg["_db_path"])
    print("  账单投递目录：%s" % cfg["_inbox_dir"])
    print("  自动同步：每 %s 小时；每日账单：%s"
          % (util.get(cfg, "sync.interval_hours", 3), "、".join(util.get(cfg, "reports.daily_times", ["12:00"]))))
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[moneybook] 正在退出…")
    finally:
        app.scheduler.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
