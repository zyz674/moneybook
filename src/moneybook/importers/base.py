# -*- coding: utf-8 -*-
"""账单文件通用解析：编码探测、表头识别、字段映射、方向判定。"""
import csv
import io
import json
import os
import re

from .. import util

ENC_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16")

# 字段 -> 可能的表头写法（按优先级）
HEADER_ALIASES = {
    "ts": ["交易时间", "付款时间", "交易创建时间", "创建时间", "交易日期", "记账日期", "时间", "日期"],
    "category_raw": ["交易分类", "交易类型", "类型", "分类", "交易场景"],
    "counterparty": ["交易对方", "对方", "商户名称", "商户", "交易对象", "收款方", "付款方", "对方名称"],
    "item": ["商品说明", "商品名称", "商品", "交易说明", "摘要", "说明", "用途"],
    "direction": ["收/支", "收支", "资金方向", "借贷标志", "收付标志"],
    "amount": ["金额(元)", "金额（元）", "金额", "交易金额", "发生额", "发生金额", "支出金额"],
    "method": ["收/付款方式", "收付款方式", "支付方式", "付款方式", "交易方式"],
    "status": ["交易状态", "当前状态", "状态", "交易结果"],
    "ext_id": ["交易订单号", "交易单号", "交易号", "订单号", "流水号"],
    "merchant_id": ["商家订单号", "商户单号", "商户订单号"],
    "note": ["备注"],
    "account": ["对方账号", "账号", "对方账户"],
}

TS_FALLBACKS = ["付款时间", "交易创建时间", "创建时间", "最近修改时间"]


def decode_bytes(raw):
    """多编码尝试解码，按中文特征词打分选最优。"""
    best, best_score = None, -10 ** 9
    for enc in ENC_CANDIDATES:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        score = 0
        for kw in ("交易", "金额", "收/支", "时间", "备注", "收入", "支出"):
            score += text.count(kw)
        score -= text.count("\ufffd") * 20
        if "交易时间" in text or "交易单号" in text or "交易订单号" in text:
            score += 50
        if score > best_score:
            best, best_score = text, score
    if best is None:
        best = raw.decode("utf-8", "replace")
    return best


def read_text(path_or_bytes):
    if isinstance(path_or_bytes, bytes):
        return decode_bytes(path_or_bytes)
    with open(path_or_bytes, "rb") as f:
        return decode_bytes(f.read())


def split_rows(text):
    """切分成二维表，自动判断分隔符。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sample = "\n".join(text.split("\n")[:40])
    delim = ","
    if sample.count("\t") > sample.count(","):
        delim = "\t"
    elif sample.count(",") == 0 and sample.count(";") > 3:
        delim = ";"
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    for raw in reader:
        row = [(c or "").strip().strip('"').strip() for c in raw]
        rows.append(row)
    return rows


def _norm_header(cell):
    return re.sub(r"[\s\u3000()（）:：]", "", str(cell or "")).lower()


def find_header(rows):
    """找到表头行下标与字段映射。"""
    for idx, row in enumerate(rows[:40]):
        if not row or len([c for c in row if c]) < 3:
            continue
        mapping, used = {}, set()
        normed = [_norm_header(c) for c in row]
        for field, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                na = _norm_header(alias)
                for i, cell in enumerate(normed):
                    if i in used or not cell:
                        continue
                    if cell == na or cell.startswith(na) or na in cell:
                        mapping[field] = i
                        used.add(i)
                        break
                if field in mapping:
                    break
        if "amount" in mapping and ("ts" in mapping or "counterparty" in mapping):
            return idx, mapping
    return None, {}


def cell(row, mapping, field):
    i = mapping.get(field)
    if i is None or i >= len(row):
        return ""
    val = row[i]
    if val in ("", "/", "-", "--", "nan", "None", "null"):
        return ""
    return val


def guess_direction(text, amount_cents=None, source="other"):
    """把 '支出/收入/不计收支/借/贷' 等统一成 in/out/neutral。"""
    t = util.norm_text(text)
    if not t:
        if amount_cents is not None:
            return "in" if amount_cents > 0 else "out"
        return "out"
    if "不计收支" in t or "不计入" in t or "中性" in t:
        return "neutral"
    if t.startswith("支") or t in ("out", "支出", "付款", "借", "d", "debit"):
        return "out"
    if t.startswith("收") or t in ("in", "收入", "收款", "贷", "c", "credit"):
        return "in"
    if "支出" in t or "付款" in t or "消费" in t:
        return "out"
    if "收入" in t or "收款" in t or "到账" in t:
        return "in"
    if amount_cents is not None:
        return "in" if amount_cents > 0 else "out"
    return "out"


def normalize_status(status, direction):
    """已关闭/失败/被全额退回的交易不计入收支；退款到账仍按收入计。"""
    s = util.norm_text(status)
    if not s:
        return direction
    for bad in ("已关闭", "交易关闭", "交易失败", "支付失败", "失败", "已撤销", "已取消", "已作废"):
        if bad in s:
            return "neutral"
    if direction == "out" and any(bad in s for bad in ("全额退款", "已全额退款", "已退回", "已退还")):
        return "neutral"
    return direction


def row_to_record(row, mapping, source, extra=None):
    """单行 -> 流水字典；无效行返回 None。"""
    ts_raw = cell(row, mapping, "ts")
    if not ts_raw:
        for f in TS_FALLBACKS:
            ts_raw = cell(row, mapping, f)
            if ts_raw:
                break
    amount_raw = cell(row, mapping, "amount")
    cents = util.parse_cents(amount_raw)
    if cents is None:
        return None
    ts = util.parse_datetime_loose(ts_raw)
    if not ts:
        return None

    direction = guess_direction(cell(row, mapping, "direction"), cents, source)
    status = cell(row, mapping, "status")
    direction = normalize_status(status, direction)
    if direction == "in" and cents < 0:
        cents = -cents

    rec = {
        "source": source,
        "origin": "import",
        "ext_id": cell(row, mapping, "ext_id") or cell(row, mapping, "merchant_id"),
        "ts": ts,
        "amount_cents": abs(cents),
        "direction": direction,
        "counterparty": cell(row, mapping, "counterparty"),
        "item": cell(row, mapping, "item"),
        "method": cell(row, mapping, "method"),
        "status": status,
        "category_raw": cell(row, mapping, "category_raw"),
        "account": cell(row, mapping, "account"),
        "note": cell(row, mapping, "note"),
    }
    if extra:
        rec.update(extra)
    rec["raw"] = json.dumps(row, ensure_ascii=False)[:1000]
    return rec


def looks_like_footer(row):
    if not row or not any(row):
        return True
    joined = "".join(row)
    if re.match(r"^[-=—\s]+$", joined):
        return True
    if len(row) <= 2 and re.search(r"(共\s*\d+\s*笔|以上|合计|小计|说明|提示|注意)", joined):
        return True
    if len([c for c in row if c]) <= 1:
        return True
    return False


def head_info(text):
    """抓取账单文件头部的账号/起止时间等元信息。"""
    info = {}
    head = "\n".join(text.replace("\r\n", "\n").split("\n")[:15])
    for key, pat in (
        ("account", r"(?:账号|微信昵称|支付宝账号|账户)\s*[:：]\s*\[?([^\]\n]+)\]?"),
        ("range_start", r"(?:起始日期|起始时间|开始时间)\s*[:：]\s*\[?([^\]\n]+)\]?"),
        ("range_end", r"(?:终止日期|终止时间|结束时间)\s*[:：]\s*\[?([^\]\n]+)\]?"),
        ("export_type", r"(?:导出类型|账单类型)\s*[:：]\s*\[?([^\]\n]+)\]?"),
    ):
        m = re.search(pat, head)
        if m:
            info[key] = m.group(1).strip()
    return info


def parse_table(rows, source):
    """二维表 -> 流水列表。"""
    header_idx, mapping = find_header(rows)
    if header_idx is None:
        return [], {}
    records = []
    for row in rows[header_idx + 1:]:
        if looks_like_footer(row):
            continue
        rec = row_to_record(row, mapping, source)
        if rec:
            records.append(rec)
    return records, mapping
