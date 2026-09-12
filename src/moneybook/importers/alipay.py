# -*- coding: utf-8 -*-
"""支付宝账单解析（支持新版/旧版导出 CSV 与「开具交易流水证明」文件）。"""
from . import base

SOURCE = "alipay"
LABEL = "支付宝"
SIGNATURES = ("支付宝", "alipay", "交易订单号", "商家订单号", "余额宝", "花呗")


def looks_like(text, filename=""):
    name = (filename or "").lower()
    if "alipay" in name or "支付宝" in name:
        return True
    head = text[:2000]
    return ("支付宝" in head) or ("交易订单号" in text[:5000] and "收/付款方式" in text[:5000])


def parse(text, filename=""):
    rows = base.split_rows(text)
    records, mapping = base.parse_table(rows, SOURCE)
    meta = base.head_info(text)
    meta["mapping"] = {k: v for k, v in mapping.items()}
    return records, meta
