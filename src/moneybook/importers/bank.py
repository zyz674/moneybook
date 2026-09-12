# -*- coding: utf-8 -*-
"""银行流水/通用 CSV 解析：兜底用，按表头自动映射。"""
from . import base

SOURCE = "bank"
LABEL = "银行"


def looks_like(text, filename=""):
    name = (filename or "").lower()
    if any(k in name for k in ("bank", "银行", "icbc", "cmb", "ccb", "abc", "boc")):
        return True
    head = text[:2000]
    return ("银行" in head and "交易" in head) or ("卡号" in head)


def parse(text, filename=""):
    rows = base.split_rows(text)
    records, mapping = base.parse_table(rows, SOURCE)
    meta = base.head_info(text)
    meta["mapping"] = {k: v for k, v in mapping.items()}
    return records, meta
