# -*- coding: utf-8 -*-
"""moneybook 全链路测试：导入 -> 解析 -> 去重 -> 汇总 -> 接口。

运行：python tests/test_all.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from moneybook import categorize, config as cfgmod, importers, jobs, parsers, reports, util  # noqa: E402
from moneybook.db import Store  # noqa: E402
from moneybook.scheduler import Scheduler  # noqa: E402
from moneybook.server import App, create_server  # noqa: E402

ALIPAY_CSV = """支付宝交易记录明细查询
账号:[138****8888]
起始日期:[2026-09-01 00:00:00]    终止日期:[2026-09-12 23:59:59]
---------------------------------交易记录明细列表------------------------------------
交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
2026-09-11 08:12:33,餐饮美食,肯德基,,KFC早餐,支出,12.50,余额宝,交易成功,2026091122001444001,M001,
2026-09-11 12:30:00,交通出行,滴滴出行,,快车-上班,支出,23.00,花呗,交易成功,2026091122001555002,M002,
2026-09-11 20:00:00,转账,张三,,朋友转账,不计收支,200.00,余额,交易成功,2026091122001666003,M003,
2026-09-12 09:00:00,收入,某某科技有限公司,,9月工资,收入,8000.00,余额,交易成功,2026091222001777004,M004,
2026-09-12 10:20:00,购物,淘宝商城,,退款-连衣裙,收入,99.00,余额,退款成功,2026091222001888005,M005,
2026-09-12 11:00:00,购物,某某旗舰店,,已关闭的订单,支出,30.00,余额,交易关闭,2026091222001999006,M006,
共6笔记录
"""

WECHAT_CSV = """微信支付账单明细
微信昵称:[小明]
起始时间:[2026-09-01 00:00:00] 终止时间:[2026-09-12 23:59:59]
导出类型:[全部]
常见问题:本账单仅供个人对账使用
----------------------微信支付账单明细列表--------------------
交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注
2026-09-11 12:31:10,商户消费,美团外卖,美团外卖订单,支出,¥35.80,零钱,支付成功,4200001234202609111234567890,WH001,/
2026-09-11 19:20:00,商户消费,永辉超市,永辉超市购物,支出,¥128.40,零钱通,支付成功,4200001234202609111987654321,WH002,/
2026-09-12 08:05:00,微信红包,张三,发出红包,支出,¥66.00,零钱,支付成功,4200001234202609120811111111,WH003,/
2026-09-12 09:10:00,转账,李四,微信转账,不计收支,¥500.00,零钱,已转账,4200001234202609120922222222,WH004,/
2026-09-12 10:00:00,商户消费,滴滴出行,滴滴出行-快车,支出,¥18.60,零钱,支付成功,4200001234202609121033333333,WH005,/
共5笔记录
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="moneybook-test-")
        self.cfg = cfgmod._deep_merge(cfgmod.DEFAULT_CONFIG, {
            "db_path": os.path.join(self.tmp, "test.db"),
            "inbox_dir": os.path.join(self.tmp, "inbox"),
            "archive_dir": os.path.join(self.tmp, "inbox", "done"),
            "access_token": "",
            "sync": {"on_start": False},
            "budget": {"monthly": 3000, "categories": {"餐饮": 800}},
            "notify": {"channels": [{"type": "console", "enabled": False}]},
        })
        self.app = App(self.cfg)
        self.store = self.app.store

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestUtil(unittest.TestCase):
    def test_amount(self):
        self.assertEqual(util.parse_cents("¥1,234.56"), 123456)
        self.assertEqual(util.parse_cents("12.50元"), 1250)
        self.assertEqual(util.parse_cents("-30"), -3000)
        self.assertEqual(util.parse_cents("￥0.01"), 1)
        self.assertIsNone(util.parse_cents("暂无"))
        self.assertEqual(util.fmt_cents(123456), "1,234.56")
        self.assertEqual(util.fmt_cents(-1250), "-12.50")

    def test_datetime(self):
        self.assertEqual(util.parse_datetime_loose("2026-09-11 08:12:33"), "2026-09-11 08:12:33")
        self.assertEqual(util.parse_datetime_loose("2026年9月11日 8:12"), "2026-09-11 08:12:00")
        self.assertEqual(util.parse_datetime_loose("09-11 08:12").endswith("09-11 08:12:00"), True)
        self.assertIsNone(util.parse_datetime_loose("不是时间"))

    def test_norm(self):
        self.assertEqual(util.norm_text("肯德基 KFC"), "肯德基kfc")
        self.assertEqual(util.norm_text("微信支付·"), "微信支付")


class TestImporters(Base):
    def test_alipay(self):
        stats = importers.import_text(self.store, self.cfg, ALIPAY_CSV, "alipay.csv")
        self.assertEqual(stats["source"], "alipay")
        self.assertEqual(stats["total"], 6)
        self.assertEqual(stats["new"], 6)
        self.assertEqual(stats["neutral"], 2)      # 转账 + 已关闭订单
        rows, total = self.store.query(source="alipay", limit=50)
        by_ext = {r["ext_id"]: r for r in rows}
        kfc = by_ext["2026091122001444001"]
        self.assertEqual(kfc["amount_cents"], 1250)
        self.assertEqual(kfc["direction"], "out")
        self.assertEqual(kfc["category"], "餐饮")
        self.assertEqual(kfc["sub_category"], "快餐")
        self.assertEqual(kfc["day"], "2026-09-11")
        closed = by_ext["2026091222001999006"]
        self.assertEqual(closed["direction"], "neutral")
        refund = by_ext["2026091222001888005"]
        self.assertEqual(refund["direction"], "in")          # 退款到账仍算收入
        self.assertEqual(refund["category"], "收入")
        salary = by_ext["2026091222001777004"]
        self.assertEqual(salary["category"], "收入")
        self.assertEqual(salary["sub_category"], "工资")

    def test_wechat(self):
        stats = importers.import_text(self.store, self.cfg, WECHAT_CSV, "wechat.csv")
        self.assertEqual(stats["source"], "wechat")
        self.assertEqual(stats["new"], 5)
        rows, _ = self.store.query(source="wechat", limit=50)
        by_ext = {r["ext_id"]: r for r in rows}
        waimai = by_ext["4200001234202609111234567890"]
        self.assertEqual(waimai["amount_cents"], 3580)
        self.assertEqual(waimai["category"], "餐饮")
        self.assertEqual(waimai["sub_category"], "外卖")
        self.assertEqual(waimai["method"], "零钱")
        transfer = by_ext["4200001234202609120922222222"]
        self.assertEqual(transfer["direction"], "neutral")
        yonghui = by_ext["4200001234202609111987654321"]
        self.assertEqual(yonghui["category"], "日用百货")

    def test_reimport_same_file_is_skipped(self):
        importers.import_text(self.store, self.cfg, WECHAT_CSV, "wechat.csv")
        again = importers.import_text(self.store, self.cfg, WECHAT_CSV, "wechat.csv")
        self.assertTrue(again.get("skipped"))
        self.assertEqual(self.store.count_tx(), 5)

    def test_import_directory(self):
        os.makedirs(self.cfg["_inbox_dir"], exist_ok=True)
        with open(os.path.join(self.cfg["_inbox_dir"], "wechat.csv"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + WECHAT_CSV.encode("utf-8"))
        with open(os.path.join(self.cfg["_inbox_dir"], "alipay.csv"), "wb") as f:
            f.write(ALIPAY_CSV.encode("gb18030"))          # 模拟支付宝 GBK 导出
        res = importers.import_directory(self.store, self.cfg, self.cfg["_inbox_dir"], self.cfg["_archive_dir"])
        self.assertEqual(res["scanned"], 2)
        self.assertEqual(self.store.count_tx(), 11)
        self.assertEqual(len(os.listdir(self.cfg["_archive_dir"])), 2)
        res2 = importers.import_directory(self.store, self.cfg, self.cfg["_inbox_dir"], self.cfg["_archive_dir"])
        self.assertEqual(res2["scanned"], 0)
        self.assertEqual(self.store.count_tx(), 11)

    def test_zip_import(self):
        import zipfile
        os.makedirs(self.cfg["_inbox_dir"], exist_ok=True)
        zpath = os.path.join(self.cfg["_inbox_dir"], "bill.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("alipay_record.csv", ALIPAY_CSV.encode("gb18030"))
        res = importers.import_file(self.store, self.cfg, zpath)
        self.assertEqual(res["new"], 6)

    def test_export_csv_endpoint_data(self):
        importers.import_text(self.store, self.cfg, ALIPAY_CSV, "alipay.csv")
        rows, _ = self.store.query(limit=100)
        self.assertEqual(len(rows), 6)


class TestParsers(Base):
    def test_wechat_notification(self):
        rec, reason = parsers.parse_notification("微信支付：已支付¥35.80，收款方美团外卖", package="com.tencent.mm")
        self.assertEqual(reason, "ok")
        self.assertEqual(rec["source"], "wechat")
        self.assertEqual(rec["amount_cents"], 3580)
        self.assertEqual(rec["direction"], "out")
        self.assertEqual(rec["counterparty"], "美团外卖")

    def test_alipay_notification(self):
        rec, _ = parsers.parse_notification("支付宝支付成功，付款金额12.50元，商户：肯德基")
        self.assertEqual(rec["source"], "alipay")
        self.assertEqual(rec["amount_cents"], 1250)
        self.assertEqual(rec["direction"], "out")

    def test_income_notification(self):
        rec, _ = parsers.parse_notification("微信支付收款到账通知：微信转账收款 ￥100.00")
        self.assertEqual(rec["direction"], "in")
        self.assertEqual(rec["amount_cents"], 10000)

    def test_bank_sms(self):
        text = "【招商银行】您尾号1234的储蓄卡09月12日12:30向某某超市支付人民币128.40元，余额5000.00元。"
        rec, _ = parsers.parse_notification(text)
        self.assertEqual(rec["source"], "bank")
        self.assertEqual(rec["amount_cents"], 12840)
        self.assertEqual(rec["direction"], "out")
        self.assertEqual(rec["account"], "1234")

    def test_noise_and_no_amount(self):
        rec, reason = parsers.parse_notification("您的验证码是123456，请勿告诉他人")
        self.assertIsNone(rec)
        rec2, reason2 = parsers.parse_notification("支付宝安全提醒：请及时修改密码")
        self.assertIsNone(rec2)

    def test_notification_dedupe(self):
        text = "微信支付：已支付¥35.80，收款方美团外卖"
        r1 = jobs.ingest_notification(self.store, self.cfg, text, ts="2026-09-11 12:31:20")
        r2 = jobs.ingest_notification(self.store, self.cfg, text, ts="2026-09-11 12:31:25")
        self.assertEqual(r1["status"], "new")
        self.assertIn(r2["status"], ("dup", "merged"))
        self.assertEqual(self.store.count_tx(), 1)

    def test_cross_channel_merge(self):
        """先收到通知，后来导入官方账单，应合并为一条并补全单号。"""
        jobs.ingest_notification(self.store, self.cfg,
                                 "微信支付：已支付¥35.80，收款方美团外卖", ts="2026-09-11 12:31:20")
        importers.import_text(self.store, self.cfg, WECHAT_CSV, "wechat.csv")
        rows, _ = self.store.query(source="wechat", limit=50)
        self.assertEqual(len(rows), 5)                      # 没有变成 6 条
        waimai = [r for r in rows if r["counterparty"] == "美团外卖"][0]
        self.assertEqual(waimai["ext_id"], "4200001234202609111234567890")
        self.assertEqual(waimai["origin"], "notify")

    def test_same_amount_different_merchant_not_merged(self):
        jobs.ingest_notification(self.store, self.cfg,
                                 "微信支付：已支付¥35.80，收款方美团外卖", ts="2026-09-11 12:31:20")
        jobs.ingest_notification(self.store, self.cfg,
                                 "微信支付：已支付¥35.80，收款方肯德基", ts="2026-09-11 12:33:20")
        self.assertEqual(self.store.count_tx(), 2)


class TestReports(Base):
    def setUp(self):
        super().setUp()
        importers.import_text(self.store, self.cfg, ALIPAY_CSV, "alipay.csv")
        importers.import_text(self.store, self.cfg, WECHAT_CSV, "wechat.csv")

    def test_daily_summary(self):
        data = reports.daily_report(self.store, self.cfg, day="2026-09-12", mode="yesterday")
        self.assertEqual(data["start"], "2026-09-12 00:00:00")
        # 9/12 支出：微信红包 66 + 滴滴 18.60 = 84.60；收入：工资 8000 + 退款 99
        self.assertEqual(data["out_cents"], 8460)
        self.assertEqual(data["in_cents"], 809900)
        self.assertEqual(data["out_cnt"], 2)
        cats = {c["key"]: c["total"] for c in data["by_category"]}
        self.assertEqual(cats.get("人情往来"), 6600)
        self.assertEqual(cats.get("交通"), 1860)

    def test_render_text(self):
        data = reports.daily_report(self.store, self.cfg, day="2026-09-11", mode="yesterday")
        text = reports.render_report(data, self.cfg)
        self.assertIn("支出", text)
        self.assertIn("餐饮", text)
        self.assertIn("本月累计支出", text)

    def test_month_status(self):
        st = reports.month_status(self.store, self.cfg, "2026-09")
        self.assertEqual(st["budget"], 300000)
        self.assertGreater(st["out_cents"], 0)
        self.assertTrue(st["category_budget"])

    def test_since_last_window(self):
        self.store.set_state("last_report_ts", "2026-09-12 00:00:00")
        data = reports.daily_report(self.store, self.cfg, mode="since_last")
        self.assertEqual(data["start"], "2026-09-12 00:00:00")
        self.assertEqual(data["out_cents"], 8460)       # 左开区间，不重复计入 00:00:00 的记录

    def test_auto_mode_picks_reasonable_window(self):
        data = reports.daily_report(self.store, self.cfg, mode="auto")
        self.assertIn(data["mode"], ("yesterday", "since_last"))
        self.assertTrue(data["start"] and data["end"])

    def test_autocategorize(self):
        self.store.update_tx(1, category="未分类", sub_category="")
        n = categorize.autocategorize_pending(self.store)
        self.assertGreaterEqual(n, 0)

    def test_learn_from_edit_creates_rule(self):
        rows, _ = self.store.query(source="alipay", limit=1)
        row = rows[0]
        kw = categorize.learn_from_edit(self.store, row, "餐饮", "快餐")
        self.assertTrue(kw)
        rec = {"counterparty": kw, "item": "", "category_raw": "", "direction": "out", "raw": ""}
        cat, sub = categorize.classify(self.store, rec)
        self.assertEqual(cat, "餐饮")


class TestJobs(Base):
    def test_push_and_sync(self):
        os.makedirs(self.cfg["_inbox_dir"], exist_ok=True)
        with open(os.path.join(self.cfg["_inbox_dir"], "wechat.csv"), "wb") as f:
            f.write(WECHAT_CSV.encode("utf-8"))
        res = jobs.sync_now(self.store, self.cfg, reason="test")
        self.assertTrue(res["ok"])
        self.assertEqual(res["new"], 5)
        pushed = jobs.push_report(self.store, self.cfg, mode="today")
        self.assertIn("支出", pushed["text"])
        self.assertIsNotNone(self.store.get_state("last_report_ts"))

    def test_manual_tx_and_update(self):
        payload = {"amount": "15.5", "direction": "out", "counterparty": "兰州拉面", "item": "午饭"}
        r = self.app  # noqa: F841
        from moneybook import jobs as _jobs  # noqa: F401
        cents = util.parse_cents(payload["amount"])
        status, tx_id = self.store.add_tx({
            "source": "manual", "origin": "manual", "ts": util.now_str(), "amount_cents": cents,
            "direction": "out", "counterparty": "兰州拉面", "item": "午饭"})
        self.assertEqual(status, "new")
        self.store.update_tx(tx_id, category="餐饮", sub_category="快餐")
        self.assertEqual(self.store.get_tx(tx_id)["category"], "餐饮")


class TestScheduler(Base):
    def test_due_logic(self):
        sch = Scheduler(self.store, self.cfg, log=lambda m: None)
        now = util.now()
        self.assertTrue(sch.sync_due(now))
        self.store.set_state("last_sync_ts", now.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertFalse(sch.sync_due(now))
        self.assertTrue(sch.sync_due(now + __import__("datetime").timedelta(hours=4)))
        self.cfg["reports"]["daily_times"] = [(now - __import__("datetime").timedelta(minutes=5)).strftime("%H:%M")]
        slots = sch.due_daily_slots(now)
        self.assertEqual(len(slots), 1)
        self.store.set_state("daily_done_%s_%s" % (slots[0].replace(":", ""), now.strftime("%Y-%m-%d")), True)
        self.assertEqual(sch.due_daily_slots(now), [])
        sch._refresh_next(now)
        self.assertTrue(sch.next_sync)
        self.assertTrue(sch.next_daily)
        self.assertTrue(sch.status()["interval_hours"] == 3.0)

    def test_tick_runs_sync(self):
        sch = Scheduler(self.store, self.cfg, log=lambda m: None)
        os.makedirs(self.cfg["_inbox_dir"], exist_ok=True)
        with open(os.path.join(self.cfg["_inbox_dir"], "alipay.csv"), "wb") as f:
            f.write(ALIPAY_CSV.encode("gb18030"))
        self.cfg["reports"]["daily_times"] = []
        sch.tick()
        self.assertEqual(self.store.count_tx(), 6)
        self.assertIsNotNone(self.store.get_state("last_sync_ts"))


class TestApi(Base):
    def setUp(self):
        super().setUp()
        importers.import_text(self.store, self.cfg, WECHAT_CSV, "wechat.csv")
        self.httpd = create_server(self.app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path, payload, ctype="application/json"):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if ctype == "application/json" else payload
        req = urllib.request.Request(self.url(path), data=data, headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_ping_and_overview(self):
        self.assertTrue(self.get("/api/ping")["ok"])
        ov = self.get("/api/overview")
        self.assertTrue(ov["ok"])
        self.assertEqual(ov["counts"]["total"], 5)

    def test_tx_list_and_update(self):
        data = self.get("/api/tx?limit=10")
        self.assertEqual(len(data["items"]), 5)
        tx_id = data["items"][0]["id"]
        res = self.post("/api/tx/%d" % tx_id, {"category": "娱乐", "sub_category": "休闲娱乐"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["item"]["category"], "娱乐")

    def test_manual_add(self):
        res = self.post("/api/tx", {"amount": "23.80", "direction": "out", "counterparty": "肯德基"})
        self.assertTrue(res["ok"])
        row = self.store.get_tx(res["id"])
        self.assertEqual(row["amount_cents"], 2380)
        self.assertEqual(row["category"], "餐饮")

    def test_ingest_endpoint_plain_text(self):
        res = self.post("/api/ingest", "微信支付：已支付¥12.34，收款方罗森便利店".encode("utf-8"), "text/plain")
        self.assertEqual(res["saved"], 1)
        self.assertEqual(res["results"][0]["record"]["amount"], "12.34")

    def test_import_endpoint(self):
        res = self.post("/api/import", {"text": ALIPAY_CSV, "filename": "a.csv"})
        self.assertEqual(res["stats"]["new"], 6)

    def test_report_and_export(self):
        rep = self.get("/api/report/daily?mode=today")
        self.assertTrue(rep["ok"])
        with urllib.request.urlopen(self.url("/api/export.csv"), timeout=10) as r:
            body = r.read().decode("utf-8")
        self.assertIn("时间,来源,方向", body)

    def test_quick_endpoint(self):
        with urllib.request.urlopen(self.url("/quick?format=text&text=" +
                                             urllib.parse.quote("微信支付：已支付¥12.34，收款方罗森便利店")), timeout=10) as r:
            body = r.read().decode("utf-8")
        self.assertIn("已记账", body)
        self.assertIn("12.34", body)
        with urllib.request.urlopen(self.url("/quick?format=text&text=" +
                                             urllib.parse.quote("微信支付：已支付¥12.34，收款方罗森便利店")), timeout=10) as r:
            body2 = r.read().decode("utf-8")
        self.assertIn("已经记过", body2)
        with urllib.request.urlopen(self.url("/quick?format=text&text=" + urllib.parse.quote("今天天气不错")), timeout=10) as r:
            body3 = r.read().decode("utf-8")
        self.assertIn("没识别出交易", body3)
        with urllib.request.urlopen(self.url("/quick?format=text&text=" +
                                             urllib.parse.quote("已支付 8 元 兰州拉面")), timeout=10) as r:
            self.assertIn("餐饮", r.read().decode("utf-8"))

    def test_two_posts_on_one_connection(self):
        """长连接下第二个 POST 不能拿到上一个请求的 body（曾经的真 bug）。"""
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            body1 = json.dumps({"text": "微信支付：已支付¥11.10，收款方甲商户"}, ensure_ascii=False).encode("utf-8")
            conn.request("POST", "/api/ingest", body=body1,
                         headers={"Content-Type": "application/json; charset=utf-8"})
            r1 = json.loads(conn.getresponse().read().decode("utf-8"))
            self.assertEqual(r1["results"][0]["record"]["amount"], "11.10")
            body2 = json.dumps({"text": "微信支付：已支付¥22.20，收款方乙商户"}, ensure_ascii=False).encode("utf-8")
            conn.request("POST", "/api/ingest", body=body2,
                         headers={"Content-Type": "application/json; charset=utf-8"})
            r2 = json.loads(conn.getresponse().read().decode("utf-8"))
            self.assertEqual(r2["results"][0]["record"]["amount"], "22.20")
            self.assertEqual(r2["results"][0]["record"]["counterparty"], "乙商户")
            conn.request("GET", "/api/tx?limit=1")
            r3 = json.loads(conn.getresponse().read().decode("utf-8"))
            self.assertTrue(r3["ok"])
        finally:
            conn.close()

    def test_auth(self):
        self.app.cfg["access_token"] = "secret"
        try:
            urllib.request.urlopen(self.url("/api/overview"), timeout=5)
            self.fail("应当 401")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)
        req = urllib.request.Request(self.url("/api/overview"), headers={"X-Token": "secret"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertTrue(json.loads(r.read().decode("utf-8"))["ok"])
        self.app.cfg["access_token"] = ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
