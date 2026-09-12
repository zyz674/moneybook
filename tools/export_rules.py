# -*- coding: utf-8 -*-
"""把内置分类规则导出成 web/rules.json，给纯前端（GitHub Pages）版本使用。

用法：python tools/export_rules.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from moneybook import categorize  # noqa: E402

DEFAULTS = {
    "field": "any", "direction": "", "source": "", "method": "", "min_cents": None, "max_cents": None,
    "time_from": "", "time_to": "", "action": "categorize", "tags": "", "note": "",
    "enabled": 1, "builtin": 1, "hits": 0,
}


def build():
    rules = []
    rid = 0
    for kw, cat, sub, direction, prio in categorize.DEFAULT_RULES:
        rid += 1
        rule = dict(DEFAULTS)
        rule.update({"id": rid, "keyword": kw, "category": cat, "sub_category": sub, "priority": prio})
        if direction:
            rule["direction"] = direction
        rules.append(rule)
    for item in categorize.DEFAULT_CONDITION_RULES:
        rid += 1
        rule = dict(DEFAULTS)
        rule.update(item)
        rule["id"] = rid
        rule.setdefault("keyword", "")
        rule.setdefault("category", "")
        rule.setdefault("sub_category", "")
        rule.setdefault("priority", 100)
        rules.append(rule)
    rules.sort(key=lambda r: (r["priority"], -len(r.get("keyword") or "")))
    return rules


def main():
    rules = build()
    out_dir = os.path.join(ROOT, "web")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "rules.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": categorize.RULES_VERSION, "rules": rules}, f, ensure_ascii=False, separators=(",", ":"))
    print("已导出 %d 条内置规则 -> %s（%.1f KB）" % (len(rules), path, os.path.getsize(path) / 1024.0))


if __name__ == "__main__":
    main()
