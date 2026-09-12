# -*- coding: utf-8 -*-
"""导入调度：识别来源、解析、分类、去重入库。"""
import hashlib
import io
import os
import zipfile

from .. import categorize, util
from . import alipay, bank, base, wechat

PARSERS = [alipay, wechat, bank]
SOURCE_LABEL = {"alipay": "支付宝", "wechat": "微信", "bank": "银行", "manual": "手工", "other": "其他"}


def detect_source(text, filename=""):
    for parser in PARSERS:
        try:
            if parser.looks_like(text, filename):
                return parser
        except Exception:
            continue
    return bank


def ingest_records(store, cfg, records, source, origin="import", log=None):
    """写入一批流水，自动分类 + 去重。返回统计字典。"""
    stats = {"source": source, "total": len(records), "new": 0, "dup": 0, "merged": 0,
             "neutral": 0, "ignored": 0}
    auto_cat = bool(cfg.get("categorize", {}).get("auto", True))
    rules = store.list_rules(enabled_only=True) if auto_cat else []
    for rec in records:
        rec = dict(rec)
        rec.setdefault("origin", origin)
        if rec.get("direction") != "neutral" and categorize.should_neutralize(rec):
            rec["direction"] = "neutral"
            rec["neutral_reason"] = "internal_transfer"
        if auto_cat:
            res = categorize.apply_rules(store, rec, rules)
            if res["action"] == "ignore":          # 规则里写了「忽略」，不入账
                stats["ignored"] += 1
                continue
            if not rec.get("category"):
                cat, sub = categorize.classify(store, rec, rules)
                rec["category"], rec["sub_category"] = cat, sub
        else:
            rec.setdefault("category", categorize.UNCATEGORIZED)
        if rec.get("direction") == "neutral":
            stats["neutral"] += 1
        status, tx_id = store.add_tx(rec)
        stats[status] = stats.get(status, 0) + 1
        if log and stats["total"] >= 50 and stats["new"] % 200 == 0:
            log("已写入 %d/%d 条" % (stats["new"], stats["total"]))
    return stats


def import_text(store, cfg, text, filename="", source=None, origin="import", force=False, log=None):
    """导入一段账单文本。返回统计字典。"""
    parser = None
    if source:
        for p in PARSERS:
            if p.SOURCE == source:
                parser = p
                break
    if parser is None:
        parser = detect_source(text, filename)
    file_sha1 = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
    seen = store.import_seen(file_sha1)
    if seen and not force:
        return {"source": parser.SOURCE, "total": 0, "new": 0, "dup": 0, "merged": 0,
                "neutral": 0, "skipped": True, "reason": "该文件已导入过", "file_sha1": file_sha1}
    records, meta = parser.parse(text, filename)
    stats = ingest_records(store, cfg, records, parser.SOURCE, origin=origin, log=log)
    stats["meta"] = meta
    stats["file_sha1"] = file_sha1
    stats["filename"] = filename
    stats["label"] = SOURCE_LABEL.get(parser.SOURCE, parser.SOURCE)
    store.log_import(filename, parser.SOURCE, file_sha1, stats["total"],
                     stats.get("new", 0), stats.get("dup", 0) + stats.get("merged", 0),
                     note=str(meta.get("account", ""))[:60])
    return stats


def import_file(store, cfg, path, source=None, origin="import", force=False, log=None):
    """导入一个文件；支持 .csv/.txt/.zip（支付宝/微信导出的压缩包）。"""
    name = os.path.basename(path)
    lower = name.lower()
    if lower.endswith(".zip"):
        out = {"files": [], "new": 0, "dup": 0, "merged": 0, "total": 0, "source": "zip"}
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                inner = info.filename
                if not inner.lower().endswith((".csv", ".txt")):
                    continue
                raw = zf.read(info)
                text = base.decode_bytes(raw)
                st = import_text(store, cfg, text, inner, source=source, origin=origin, force=force, log=log)
                out["files"].append(st)
                for k in ("new", "dup", "merged", "total"):
                    out[k] += st.get(k, 0)
                out["source"] = st.get("source", out["source"])
        return out
    with open(path, "rb") as f:
        raw = f.read()
    text = base.decode_bytes(raw)
    return import_text(store, cfg, text, name, source=source, origin=origin, force=force, log=log)


def import_directory(store, cfg, directory, archive_dir=None, source=None, log=None):
    """扫描目录里所有账单文件并导入，可选移动到 archive 目录避免重复扫描。"""
    result = {"scanned": 0, "imported": 0, "files": []}
    if not directory or not os.path.isdir(directory):
        return result
    exts = (".csv", ".txt", ".zip")
    names = sorted(n for n in os.listdir(directory) if n.lower().endswith(exts))
    for name in names:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        result["scanned"] += 1
        try:
            st = import_file(store, cfg, path, source=source, log=log)
        except Exception as exc:      # 单个文件失败不影响其它文件
            st = {"filename": name, "error": "%s: %s" % (type(exc).__name__, exc), "new": 0, "total": 0}
        st["filename"] = name
        result["files"].append(st)
        result["imported"] += st.get("new", 0)
        if archive_dir and st.get("new", 0) >= 0 and not st.get("error"):
            try:
                os.makedirs(archive_dir, exist_ok=True)
                stamp = util.now().strftime("%Y%m%d%H%M%S")
                os.replace(path, os.path.join(archive_dir, "%s_%s" % (stamp, name)))
            except OSError:
                pass
    return result
