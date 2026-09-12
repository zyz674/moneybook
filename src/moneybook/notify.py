# -*- coding: utf-8 -*-
"""推送通道：Bark / Server酱 / PushPlus / ntfy / Telegram / 企业微信 / 钉钉 / 飞书 / 通用 Webhook。

只用标准库 urllib，避免安装依赖。每个通道互不影响，一个失败不影响其它。
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 12
UA = "moneybook/1.0 (+https://localhost)"


def _request(url, data=None, headers=None, method=None):
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            return True, "HTTP %s %s" % (resp.status, body[:300])
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return False, "HTTP %s %s" % (exc.code, body[:300])
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)


def _post_json(url, payload, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json; charset=utf-8"}
    hdrs.update(headers or {})
    return _request(url, data=data, headers=hdrs)


def _post_form(url, payload, headers=None):
    data = urllib.parse.urlencode(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    hdrs.update(headers or {})
    return _request(url, data=data, headers=hdrs)


def _dingtalk_sign(secret):
    ts = str(round(time.time() * 1000))
    string_to_sign = "%s\n%s" % (ts, secret)
    h = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(h).decode("utf-8"))
    return ts, sign


def _send_one(ch, title, body):
    """返回 (ok, resp)。"""
    t = (ch.get("type") or "").lower()
    if t == "console":
        print("\n" + "=" * 30 + "\n" + title + "\n" + body + "\n" + "=" * 30)
        return True, "printed to console"

    if t == "bark":
        url = (ch.get("url") or "https://api.day.app").rstrip("/")
        if ch.get("key"):
            url += "/" + ch["key"]
        payload = {"title": title, "body": body, "group": ch.get("group", "记账本"),
                   "sound": ch.get("sound", "birdsong"), "isArchive": 1}
        if ch.get("level"):
            payload["level"] = ch["level"]
        return _post_json(url + "/push", payload)

    if t in ("serverchan", "server_chan", "sct"):
        key = ch.get("key") or ch.get("sendkey")
        if not key:
            return False, "缺少 key"
        url = ch.get("url") or ("https://sctapi.ftqq.com/%s.send" % key)
        return _post_form(url, {"title": title, "desp": body})

    if t == "pushplus":
        url = ch.get("url") or "http://www.pushplus.plus/send"
        return _post_json(url, {"token": ch.get("token"), "title": title, "content": body,
                                "template": ch.get("template", "txt")})

    if t == "ntfy":
        server = (ch.get("server") or "https://ntfy.sh").rstrip("/")
        topic = ch.get("topic")
        if not topic:
            return False, "缺少 topic"
        headers = {"Title": urllib.parse.quote(title.encode("utf-8")), "Markdown": "no"}
        if ch.get("priority"):
            headers["Priority"] = str(ch["priority"])
        if ch.get("token"):
            headers["Authorization"] = "Bearer " + ch["token"]
        return _request(server + "/" + topic, data=body.encode("utf-8"), headers=headers)

    if t == "telegram":
        token = ch.get("token")
        chat_id = ch.get("chat_id")
        if not token or not chat_id:
            return False, "缺少 token/chat_id"
        return _post_json("https://api.telegram.org/bot%s/sendMessage" % token,
                          {"chat_id": chat_id, "text": title + "\n\n" + body, "disable_web_page_preview": True})

    if t in ("wecom", "qywx", "workwechat"):
        url = ch.get("webhook") or ch.get("url")
        if not url:
            return False, "缺少 webhook"
        return _post_json(url, {"msgtype": "text", "text": {"content": title + "\n" + body}})

    if t == "dingtalk":
        url = ch.get("webhook") or ch.get("url")
        if not url:
            return False, "缺少 webhook"
        if ch.get("secret"):
            ts, sign = _dingtalk_sign(ch["secret"])
            url += ("&" if "?" in url else "?") + "timestamp=%s&sign=%s" % (ts, sign)
        return _post_json(url, {"msgtype": "markdown", "markdown": {"title": title, "text": title + "\n\n" + body}})

    if t == "feishu":
        url = ch.get("webhook") or ch.get("url")
        if not url:
            return False, "缺少 webhook"
        payload = {"msg_type": "text", "content": {"text": title + "\n" + body}}
        if ch.get("secret"):
            ts = str(int(time.time()))
            s = "%s\n%s" % (ts, ch["secret"])
            payload["timestamp"] = ts
            payload["sign"] = base64.b64encode(
                hmac.new(s.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
        return _post_json(url, payload)

    if t in ("webhook", "custom"):
        url = ch.get("url")
        if not url:
            return False, "缺少 url"
        headers = ch.get("headers") or {}
        return _post_json(url, {"title": title, "body": body, "text": title + "\n" + body,
                                "kind": ch.get("kind", ""), "app": "moneybook"}, headers)

    if t == "email":
        return False, "未实现 SMTP 邮件通道"

    return False, "未知通道类型：%s" % t


def send(cfg, store, title, body, kind="notify", only_types=None):
    """向所有启用通道推送。返回结果列表。"""
    channels = (cfg.get("notify") or {}).get("channels") or []
    results = []
    for ch in channels:
        if not isinstance(ch, dict):
            continue
        if ch.get("enabled") is False:
            continue
        if only_types and (ch.get("type") or "").lower() not in only_types:
            continue
        ok, resp = _send_one(ch, title, body)
        results.append({"type": ch.get("type"), "ok": ok, "resp": resp})
        if store is not None:
            try:
                store.log_push(ch.get("type") or "?", title, body, ok, resp, kind)
            except Exception:
                pass
    if not results:
        results.append({"type": "none", "ok": False, "resp": "没有配置任何推送通道"})
    return results
