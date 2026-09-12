# -*- coding: utf-8 -*-
"""通用工具：金额、时间、文本规范化与去重指纹。

金额一律使用「整数分」存储，避免浮点误差。
"""
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))          # 默认东八区
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_AMOUNT_CLEAN = str.maketrans({
    "￥": "", "¥": "", "元": "", ",": "", "，": "", " ": "",
    "＋": "+", "－": "-", "—": "-", "−": "-", "\u00a0": "",
})


def now():
    """当前北京时间（带时区）。"""
    return datetime.now(CST)


def now_str():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today_str(offset_days=0):
    return (now() + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def parse_cents(value):
    """把 '¥1,234.56' / '12.50元' / '12.5' / -1250 解析成整数分；失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        return int(round(value * 100))
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("人民币", "").replace("RMB", "").replace("rmb", "")
    s = s.translate(_AMOUNT_CLEAN)
    neg = False
    if s.startswith("-"):
        neg = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    m = re.match(r"^\d+(?:\.\d+)?$", s)
    if not m:
        m = re.search(r"\d+(?:\.\d+)?", s)
        if not m:
            return None
        s = m.group(0)
    cents = int(round(float(s) * 100))
    return -cents if neg else cents


def fmt_cents(cents, symbol=False):
    """整数分 -> '12.50' 或 '¥12.50'。"""
    if cents is None:
        return "-"
    sign = "-" if cents < 0 else ""
    body = "{:,}".format(abs(int(cents)) // 100) + "." + "{:02d}".format(abs(int(cents)) % 100)
    return (sign + ("¥" if symbol else "") + body)


def norm_text(text):
    """文本规范化：全角转半角、去空白标点、统一大小写。用于去重与规则匹配。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[·•,，。.、;；:：!！?？\"'“”‘’()（）\[\]【】<>《》_\-—/\\|]+", "", s)
    return s.lower()


def normalize_text_keep_amount(text):
    """通知/短信文本规范化：全角转半角（１２．５０ -> 12.50、￥ -> ¥），保留可读内容。"""
    s = unicodedata.normalize("NFKC", str(text or ""))
    return s.replace("\u3000", " ").replace("\xa0", " ").strip()


def sha1(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p if p is not None else "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def dedupe_key(source, ts, cents, direction, counterparty="", ext_id=None):
    """生成唯一键。有官方单号用单号；否则用「来源+分钟+金额+方向+对手方」指纹。"""
    if ext_id:
        return "id:" + sha1(source, ext_id)[:40]
    return "fp:" + sha1(source, str(ts)[:16], cents, direction, norm_text(counterparty))[:40]


def fuzzy_key(source, day, cents, direction):
    """粗指纹：同来源 + 同一天 + 同金额 + 同方向。用于跨渠道模糊去重。"""
    return sha1(source, day, cents, direction)[:32]


def parse_datetime_loose(text, default_year=None):
    """尽力解析各种中文/数字时间格式，返回 'YYYY-MM-DD HH:MM:SS'；失败返回 None。"""
    if not text:
        return None
    if isinstance(text, datetime):
        return text.strftime("%Y-%m-%d %H:%M:%S")
    s = unicodedata.normalize("NFKC", str(text)).strip()
    s = s.replace("年", "-").replace("月", "-").replace("日", " ").replace("时", ":").replace("分", ":").replace("秒", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip("-: ")
    fmts = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
        "%Y%m%d%H%M%S", "%Y%m%d",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
        except ValueError:
            continue
        if dt.year == 1900:
            dt = dt.replace(year=default_year or now().year)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    # 只有「月-日 [时:分[:秒]]」的写法（通知/短信里常见）
    s_short = s.replace("/", "-")
    m = re.match(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", s_short)
    if m:
        y = default_year or now().year
        mo, d = int(m.group(1)), int(m.group(2))
        hh, mi, ss = int(m.group(3) or 0), int(m.group(4) or 0), int(m.group(5) or 0)
        try:
            return datetime(y, mo, d, hh, mi, ss).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None


def day_of(ts):
    return str(ts)[:10]


def month_of(ts):
    return str(ts)[:7]


def weekday_cn(day):
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return ""
    return WEEKDAY_CN[dt.weekday()]


def date_range(start_day, end_day):
    """包含首尾的日期列表。"""
    a = datetime.strptime(start_day, "%Y-%m-%d")
    b = datetime.strptime(end_day, "%Y-%m-%d")
    out = []
    while a <= b:
        out.append(a.strftime("%Y-%m-%d"))
        a += timedelta(days=1)
    return out


def month_days(month):
    """'2026-02' -> ('2026-02-01', '2026-02-28')"""
    y, m = int(month[:4]), int(month[5:7])
    first = datetime(y, m, 1)
    nxt = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
    last = nxt - timedelta(days=1)
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


def get(cfg, path, default=None):
    """按 'reports.daily_times' 这样的点号路径读取嵌套配置。"""
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def mask_account(text, keep=4):
    """账号/卡号脱敏：保留后 keep 位。"""
    s = str(text or "")
    if len(s) <= keep:
        return s
    return "*" * (len(s) - keep) + s[-keep:]
