# -*- coding: utf-8 -*-
"""微信支付账单解析（微信导出的 CSV 为 UTF-8，金额形如 ¥12.50）。"""
from . import base

SOURCE = "wechat"
LABEL = "微信"
SIGNATURES = ("微信", "wechat", "微信支付", "交易单号", "零钱", "微信昵称")


def looks_like(text, filename=""):
    name = (filename or "").lower()
    if "wechat" in name or "微信" in name:
        return True
    head = text[:3000]
    return ("微信支付账单" in head) or ("微信昵称" in head) or ("交易单号" in text[:5000] and "当前状态" in text[:5000])


def parse(text, filename=""):
    rows = base.split_rows(text)
    records, mapping = base.parse_table(rows, SOURCE)
    meta = base.head_info(text)
    meta["mapping"] = {k: v for k, v in mapping.items()}
    return records, meta
