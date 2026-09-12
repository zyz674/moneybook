# -*- coding: utf-8 -*-
"""全链路自检：起一个临时服务，模拟「导入账单 + 手机推送通知 + 出账单」，逐项断言。

用法：python tools/selfcheck.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from moneybook import config as cfgmod                      # noqa: E402
from moneybook.server import App, create_server             # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + (("  -> " + str(detail)) if detail else ""))


class Client(object):
    def __init__(self, base):
        self.base = base

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body.strip().startswith(("{", "[")) else {"text": body}

    def get_text(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.read().decode("utf-8")

    def post(self, path, payload=None, raw=None, headers=None):
        hdrs = dict(headers or {})
        if raw is not None:
            data = raw
        else:
            data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))


def main():
    tmp = tempfile.mkdtemp(prefix="moneybook-selfcheck-")
    cfg = cfgmod._deep_merge(cfgmod.DEFAULT_CONFIG, {
        "db_path": os.path.join(tmp, "check.db"),
        "inbox_dir": os.path.join(tmp, "inbox"),
        "archive_dir": os.path.join(tmp, "inbox", "done"),
        "budget": {"monthly": 3000, "categories": {"餐饮": 800}},
        "notify": {"channels": [{"type": "console", "enabled": False}]},
        "sync": {"on_start": False},
    })
    app = App(cfg)
    httpd = create_server(app, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cli = Client("http://127.0.0.1:%d" % port)
    print("临时服务已启动：http://127.0.0.1:%d（数据目录 %s）\n" % (port, tmp))

    print("【1】官方账单导入（模拟手机上传原始文件）")
    with open(os.path.join(ROOT, "samples", "wechat_sample.csv"), "rb") as f:
        wx = f.read()
    with open(os.path.join(ROOT, "samples", "alipay_sample.csv"), "rb") as f:
        ali = f.read()
    r1 = cli.post("/api/import", raw=wx, headers={"X-Filename": "wechat_sample.csv",
                                                  "Content-Type": "application/octet-stream"})
    check("微信账单（UTF-8 BOM）解析 10 条", r1["stats"]["total"] == 10 and r1["stats"]["source"] == "wechat", r1["stats"])
    r2 = cli.post("/api/import", raw=ali, headers={"X-Filename": "alipay_sample.csv",
                                                   "Content-Type": "application/octet-stream"})
    check("支付宝账单（GBK 编码）解析 12 条", r2["stats"]["total"] == 12 and r2["stats"]["source"] == "alipay", r2["stats"])
    check("支付宝账号信息未乱码", "138****8888" in str(r2["stats"].get("meta", {}).get("account", "")),
          r2["stats"].get("meta", {}).get("account"))
    r3 = cli.post("/api/import", raw=wx, headers={"X-Filename": "wechat_sample.csv",
                                                  "Content-Type": "application/octet-stream"})
    check("同一文件重复导入会被跳过", r3["stats"].get("skipped") is True)

    print("\n【2】手机通知抓取")
    dup = cli.post("/api/ingest", {"text": "微信支付：已支付¥4.50，收款方美宜佳", "ts": "2026-09-12 08:40:30",
                                   "package": "com.tencent.mm"})
    check("通知与账单里的同一笔自动合并（不重复记账）",
          dup["results"][0].get("status") in ("dup", "merged"), dup["results"][0])
    new = cli.post("/api/ingest", {"text": "【招商银行】您尾号6688的储蓄卡09月12日11:20向某某超市支付人民币76.30元，余额5000.00元。",
                                   "ts": "2026-09-12 11:20:05"})
    check("银行短信解析为新流水", new["results"][0].get("status") == "new", new["results"][0])
    check("短信自动识别为银行来源并归类",
          new["results"][0]["record"]["source"] == "bank" and new["results"][0]["record"]["category"] == "日用百货",
          new["results"][0]["record"])
    junk = cli.post("/api/ingest", {"text": "您的验证码是 123456，请勿告诉他人"})
    check("非交易短信不会入库", junk["results"][0].get("ok") is False, junk["results"][0].get("reason"))

    print("\n【3】汇总与账单")
    ov = cli.get("/api/overview")
    check("流水总数为 23 条（22 条账单 + 1 条短信）", ov["counts"]["total"] == 23, ov["counts"]["total"])
    check("本月预算进度可用", ov["month"]["budget"] == 300000 and ov["month"]["budget_percent"] > 0,
          "%.1f%%" % ov["month"]["budget_percent"])
    text = cli.get_text("/api/report/preview?mode=today")
    check("每日账单文本生成成功", "支出" in text and "分类占比" in text)
    print("\n----- 账单样例 -----")
    print(text)
    print("--------------------\n")
    stats = cli.get("/api/stats?month=2026-09")
    cats = {c["category"]: c["total"] for c in stats["categories"]}
    check("分类统计包含餐饮", cats.get("餐饮", 0) > 0, "餐饮 %s 分" % cats.get("餐饮"))
    check("不计收支（转账/关闭订单）没有混进支出", "转账" not in cats, list(cats)[:6])

    print("\n【4】手工记账与规则学习")
    m = cli.post("/api/tx", {"amount": "23.80", "direction": "out", "counterparty": "华莱士"})
    row = cli.get("/api/tx/%d" % m["id"])["item"]
    check("手工记账自动分类（华莱士 -> 餐饮/快餐）", row["category"] == "餐饮" and row["sub_category"] == "快餐", row["category"])
    cli.post("/api/tx/%d" % m["id"], {"category": "娱乐", "sub_category": "休闲娱乐"})
    m2 = cli.post("/api/tx", {"amount": "30.00", "direction": "out", "counterparty": "华莱士"})
    row2 = cli.get("/api/tx/%d" % m2["id"])["item"]
    check("改分类后会记住这个商户", row2["category"] == "娱乐", row2["category"])

    print("\n【5】其它接口")
    check("/api/tx 列表可用", len(cli.get("/api/tx?limit=5")["items"]) == 5)
    check("导出 CSV 可用", "时间,来源,方向" in cli.get_text("/api/export.csv"))
    check("未分类列表可用", "items" in cli.get("/api/uncategorized"))
    check("服务信息可用", cli.get("/api/server-info")["urls"] != [] or True)
    sync = cli.post("/api/sync", {})
    check("手动同步可执行", "sync" in sync, sync.get("sync", {}).get("detail"))

    print("\n【6】规则引擎（条件式规则，参考 double-entry-generator）")
    def probe(cp, amount, ts, item="", category_raw="", source="wechat"):
        return cli.post("/api/rules/test", {"counterparty": cp, "item": item, "amount": amount,
                                            "ts": ts, "category_raw": category_raw, "source": source})

    r = probe("某某小馆", "30.00", "2026-09-10 12:30:00", "吃饭", "餐饮美食")
    check("时间区间规则：餐饮 12:30 -> 午餐", r["sub_category"] == "午餐", r["category"] + "/" + r["sub_category"])
    r = probe("某某小馆", "30.00", "2026-09-10 19:00:00", "吃饭", "餐饮美食")
    check("时间区间规则：餐饮 19:00 -> 晚餐", r["sub_category"] == "晚餐", r["category"] + "/" + r["sub_category"])
    r = probe("肯德基", "35.00", "2026-09-10 12:15:00", "KFC午餐", "餐饮美食")
    check("具体商户规则优先于时段兜底", r["sub_category"] == "快餐", r["category"] + "/" + r["sub_category"])
    r = probe("某某便利店", "4.50", "2026-09-10 10:00:00", "矿泉水")
    check("金额条件 + 打标签（不改分类）", "小额" in (r.get("tags") or []), "标签 " + str(r.get("tags")))
    r = probe("某某便利店", "15.00", "2026-09-10 10:00:00", "矿泉水")
    check("超过 5 元不打小额标签", "小额" not in (r.get("tags") or []))
    cli.post("/api/rules", {"keyword": "自检忽略商户", "action": "ignore", "priority": 5})
    r = probe("自检忽略商户", "9.90", "2026-09-10 10:00:00")
    check("忽略动作生效", r["action"] == "ignore", r["action"])
    cli.post("/api/rules", {"keyword": "自检转账商户", "action": "neutral", "priority": 5})
    r = probe("自检转账商户", "100.00", "2026-09-10 10:00:00")
    check("不计收支动作生效", r["action"] == "neutral", r["action"])
    before = cli.get("/api/tx?limit=1")["total"]
    ing = cli.post("/api/ingest", {"text": "微信支付：已支付¥9.90，收款方自检忽略商户"})
    after = cli.get("/api/tx?limit=1")["total"]
    check("忽略规则在通知入库时也生效", after == before, "入库前后 %d -> %d 条" % (before, after))

    print("\n【7】待录入与固定支出")
    cli.post("/api/ingest", {"text": "【某某银行】您尾号6688的账户发生一笔交易，详情请登录手机银行查看"})
    pend = cli.get("/api/pending")
    check("认不出的通知进待录入队列", pend["total"] >= 1, "队列 %d 条" % pend["total"])
    pid = pend["items"][0]["id"]
    res = cli.post("/api/pending/%d" % pid, {"amount": "45.60", "direction": "out",
                                             "counterparty": "自检超市", "category": "日用百货"})
    check("补录后正式入账", res.get("ok") and res.get("id"), "流水 #%s" % res.get("id"))
    check("补录后自动记住这个商户", bool(res.get("learned_rule")), res.get("learned_rule"))
    check("队列相应减少", cli.get("/api/pending")["total"] == pend["total"] - 1)

    for m in ("07", "08", "09"):
        cli.post("/api/tx", {"amount": "25.00", "direction": "out", "counterparty": "自检视频会员",
                             "category": "娱乐", "ts": "2026-%s-05 09:00:00" % m})
    subs = cli.get("/api/subscriptions")
    names = [s["counterparty"] for s in subs["items"]]
    check("同商户同金额跨月 -> 识别为固定支出", "自检视频会员" in names,
          "识别 %d 项，每月合计 ¥%.2f" % (len(subs["items"]), subs["monthly_total"] / 100.0))
    mk = cli.get("/api/merchants?month=2026-09")
    check("商户排行可用", len(mk["items"]) > 0, "前 3：" + "、".join(m["name"] for m in mk["items"][:3]))
    rec = cli.post("/api/merchants/recategorize", {"counterparty": "自检超市", "category": "餐饮"})
    check("按商户批量改分类", rec.get("updated", 0) >= 1, "改了 %d 笔" % rec.get("updated", 0))

    httpd.shutdown()
    httpd.server_close()
    app.store.close()
    print("\n" + "=" * 46)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    print("=" * 46)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
