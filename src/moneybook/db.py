# -*- coding: utf-8 -*-
"""SQLite 存储层：建表、写入（含跨渠道去重）、查询、设置与日志。"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta  # noqa: F401

from . import util

SCHEMA = """
CREATE TABLE IF NOT EXISTS tx (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL DEFAULT 'other',
    origin        TEXT NOT NULL DEFAULT 'import',
    ext_id        TEXT DEFAULT '',
    ts            TEXT NOT NULL,
    day           TEXT NOT NULL,
    month         TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL DEFAULT 0,
    direction     TEXT NOT NULL DEFAULT 'out',
    counterparty  TEXT DEFAULT '',
    item          TEXT DEFAULT '',
    method        TEXT DEFAULT '',
    status        TEXT DEFAULT '',
    category      TEXT DEFAULT '未分类',
    sub_category  TEXT DEFAULT '',
    tags          TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    account       TEXT DEFAULT '',
    raw           TEXT DEFAULT '',
    dedupe_key    TEXT NOT NULL UNIQUE,
    fuzzy_key     TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_day       ON tx(day);
CREATE INDEX IF NOT EXISTS idx_tx_month     ON tx(month);
CREATE INDEX IF NOT EXISTS idx_tx_source    ON tx(source);
CREATE INDEX IF NOT EXISTS idx_tx_category  ON tx(category);
CREATE INDEX IF NOT EXISTS idx_tx_fuzzy     ON tx(fuzzy_key);
CREATE INDEX IF NOT EXISTS idx_tx_direction ON tx(direction);

-- 分类规则：条件（关键词/金额区间/时间区间/支付方式）+ 动作（归类/不计收支/忽略/打标签）
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sig         TEXT DEFAULT '',            -- 全部条件的指纹，用于去重  （老库由 _migrate 补列）
    keyword     TEXT DEFAULT '',            -- 可写多个，用逗号分隔，命中其一即可
    field       TEXT DEFAULT 'any',         -- any | peer | item | category | method
    category    TEXT DEFAULT '',
    sub_category TEXT DEFAULT '',
    direction   TEXT DEFAULT '',            -- '' | in | out | neutral
    source      TEXT DEFAULT '',            -- '' | alipay | wechat | bank | manual
    method      TEXT DEFAULT '',            -- 支付方式包含此串
    min_cents   INTEGER,                    -- 金额下限（分，含）
    max_cents   INTEGER,                    -- 金额上限（分，含）
    time_from   TEXT DEFAULT '',            -- '11:00'
    time_to     TEXT DEFAULT '',            -- '14:00'（支持跨零点）
    action      TEXT DEFAULT 'categorize',  -- categorize | neutral | ignore | tag
    tags        TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    full_match  INTEGER DEFAULT 0,
    priority    INTEGER DEFAULT 100,        -- 越小越先匹配
    enabled     INTEGER DEFAULT 1,
    builtin     INTEGER DEFAULT 0,
    hits        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);
-- 没认出来的通知：进待录入队列，等人工补一下（参考 WhereIsMyMoney）
CREATE TABLE IF NOT EXISTS pending (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sig         TEXT NOT NULL UNIQUE,
    raw         TEXT NOT NULL,
    source      TEXT DEFAULT '',
    package     TEXT DEFAULT '',
    device      TEXT DEFAULT '',
    ts          TEXT DEFAULT '',
    reason      TEXT DEFAULT '',
    amount_cents INTEGER,
    status      TEXT DEFAULT 'open',        -- open | done | ignored
    tx_id       INTEGER,
    attempts    INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending(status);

CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    source      TEXT,
    sha1        TEXT,
    rows_total  INTEGER DEFAULT 0,
    rows_new    INTEGER DEFAULT 0,
    rows_dup    INTEGER DEFAULT 0,
    imported_at TEXT,
    note        TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imports_sha ON imports(sha1);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    started_at  TEXT,
    finished_at TEXT,
    ok          INTEGER DEFAULT 1,
    detail      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pushes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT,
    title      TEXT,
    body       TEXT,
    ok         INTEGER DEFAULT 0,
    resp       TEXT DEFAULT '',
    kind       TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pushes_created ON pushes(created_at);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# 索引单独放：老库要先补完列才能建索引，否则一启动就报 no such column: sig
SCHEMA_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_sig ON rules(sig);
CREATE INDEX IF NOT EXISTS idx_rules_priority ON rules(priority);
"""


def rule_sig(rule):
    """规则指纹：所有条件 + 动作的组合，保证同一条件不会重复入库。"""
    parts = [
        str(rule.get("keyword") or ""), str(rule.get("field") or "any"),
        str(rule.get("category") or ""), str(rule.get("sub_category") or ""),
        str(rule.get("direction") or ""), str(rule.get("source") or ""),
        str(rule.get("method") or ""), str(rule.get("min_cents")), str(rule.get("max_cents")),
        str(rule.get("time_from") or ""), str(rule.get("time_to") or ""),
        str(rule.get("action") or "categorize"), str(rule.get("tags") or ""),
        "1" if rule.get("full_match") else "0",
    ]
    return util.sha1(*parts)[:40]


TX_FIELDS = [
    "source", "origin", "ext_id", "ts", "day", "month", "amount_cents", "direction",
    "counterparty", "item", "method", "status", "category", "sub_category", "tags",
    "note", "account", "raw", "dedupe_key", "fuzzy_key",
]

# 允许被后续更完整的数据（如官方账单 CSV）补全的字段
ENRICH_FIELDS = ["ext_id", "counterparty", "item", "method", "status", "account"]


class Store(object):
    """线程安全的 SQLite 封装（每个线程一个连接，WAL 模式）。"""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def init_schema(self):
        with self._write_lock:
            self.conn.executescript(SCHEMA)          # 1. 建表
            self._migrate()                          # 2. 给老库补列、回填指纹
            self.conn.executescript(SCHEMA_INDEXES)  # 3. 最后建索引
            self.conn.commit()

    def _columns(self, table):
        return {r["name"] for r in self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()}

    def _migrate(self):
        """老版本数据库自动升级：补列 + 重建唯一索引 + 回填指纹。"""
        want = {
            "sig": "TEXT DEFAULT ''", "field": "TEXT DEFAULT 'any'", "source": "TEXT DEFAULT ''",
            "method": "TEXT DEFAULT ''", "min_cents": "INTEGER", "max_cents": "INTEGER",
            "time_from": "TEXT DEFAULT ''", "time_to": "TEXT DEFAULT ''",
            "action": "TEXT DEFAULT 'categorize'", "tags": "TEXT DEFAULT ''",
            "note": "TEXT DEFAULT ''", "full_match": "INTEGER DEFAULT 0", "builtin": "INTEGER DEFAULT 0",
        }
        cols = self._columns("rules")
        for name, decl in want.items():
            if name not in cols:
                self.conn.execute("ALTER TABLE rules ADD COLUMN %s %s" % (name, decl))
        self.conn.execute("DROP INDEX IF EXISTS idx_rules_kw")
        stale = self.conn.execute("SELECT * FROM rules WHERE sig IS NULL OR sig=''").fetchall()
        for row in stale:
            self.conn.execute("UPDATE rules SET sig=? WHERE id=?", (rule_sig(dict(row)), row["id"]))
        # 老库里可能有条件完全相同的重复规则，先去重，否则建不了唯一索引
        self.conn.execute("DELETE FROM rules WHERE id NOT IN (SELECT MIN(id) FROM rules GROUP BY sig)")
        # 早期版本可能建过同名的普通索引：同名时 CREATE UNIQUE INDEX IF NOT EXISTS 会直接跳过，
        # 导致唯一约束永远缺失（add_rule 的 ON CONFLICT 会报错）。这里先拆掉重建。
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_rules_sig'").fetchone()
        if row is not None and row["sql"] and "UNIQUE" not in row["sql"].upper():
            self.conn.execute("DROP INDEX idx_rules_sig")

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    # ---------- 设置 / 状态 ----------
    def get_setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return row["value"]

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_state(self, key, default=None):
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return row["value"]

    def set_state(self, key, value):
        self.conn.execute(
            "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    # ---------- 流水写入 ----------
    def _enrich(self, old, new):
        """用更完整的数据补全已有记录，返回是否有变化。"""
        changed = False
        for f in ENRICH_FIELDS:
            ov = (old[f] or "").strip() if isinstance(old[f], str) else old[f]
            nv = (new.get(f) or "").strip() if isinstance(new.get(f), str) else new.get(f)
            if nv and nv != ov and (not ov or len(str(nv)) > len(str(ov))):
                self.conn.execute("UPDATE tx SET %s=?, updated_at=? WHERE id=?" % f, (nv, util.now_str(), old["id"]))
                changed = True
        return changed

    @staticmethod
    def _counterparty(item):
        """兼容 dict / sqlite3.Row 两种记录形态。"""
        if isinstance(item, dict):
            return item.get("counterparty") or ""
        try:
            return item["counterparty"] or ""
        except (TypeError, IndexError, KeyError):
            return ""

    @classmethod
    def _same_merchant(cls, a, b):
        """两条记录是否可能来自同一笔交易：商户名一方包含另一方（或缺失）时认为可能相同。"""
        na = util.norm_text(cls._counterparty(a))
        nb = util.norm_text(cls._counterparty(b))
        if not na or not nb:
            return True
        return na in nb or nb in na

    def add_tx(self, record, fuzzy_minutes=20):
        """写入一条流水。返回 (status, id)，status in {new, dup, merged}。

        去重策略：
          1) 官方单号 / 分钟级指纹完全一致 -> dup
          2) 同来源+同金额+同方向+时间相差 <= fuzzy_minutes -> merged（用更完整的数据补全）
        """
        rec = dict(record)
        rec.setdefault("source", "other")
        rec.setdefault("origin", "import")
        rec.setdefault("ext_id", "")
        rec["ts"] = rec.get("ts") or util.now_str()
        rec["day"] = util.day_of(rec["ts"])
        rec["month"] = util.month_of(rec["ts"])
        rec["amount_cents"] = int(rec.get("amount_cents") or 0)
        rec["direction"] = rec.get("direction") or "out"
        rec["dedupe_key"] = rec.get("dedupe_key") or util.dedupe_key(
            rec["source"], rec["ts"], rec["amount_cents"], rec["direction"],
            rec.get("counterparty", ""), rec.get("ext_id") or None)
        rec["fuzzy_key"] = util.fuzzy_key(rec["source"], rec["day"], rec["amount_cents"], rec["direction"])
        now = util.now_str()
        rec.setdefault("created_at", now)
        rec.setdefault("updated_at", now)
        for f in TX_FIELDS:
            rec.setdefault(f, "")

        with self._write_lock:
            cur = self.conn.execute("SELECT * FROM tx WHERE dedupe_key=?", (rec["dedupe_key"],))
            row = cur.fetchone()
            if row is not None:
                changed = self._enrich(row, rec)
                self.conn.commit()
                return ("merged" if changed else "dup"), row["id"]

            if rec["amount_cents"] > 0 and fuzzy_minutes > 0:
                try:
                    base = datetime.strptime(rec["ts"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    base = None
                if base is not None:
                    lo = (base - timedelta(minutes=fuzzy_minutes)).strftime("%Y-%m-%d %H:%M:%S")
                    hi = (base + timedelta(minutes=fuzzy_minutes)).strftime("%Y-%m-%d %H:%M:%S")
                    near = self.conn.execute(
                        "SELECT * FROM tx WHERE fuzzy_key=? AND ts BETWEEN ? AND ? ORDER BY ABS(julianday(ts)-julianday(?)) LIMIT 1",
                        (rec["fuzzy_key"], lo, hi, rec["ts"]),
                    ).fetchone()
                    if near is not None and self._same_merchant(near, rec):
                        changed = self._enrich(near, rec)
                        self.conn.commit()
                        return ("merged" if changed else "dup"), near["id"]

            fields = TX_FIELDS + ["created_at", "updated_at"]
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            cur = self.conn.execute(
                "INSERT INTO tx (%s) VALUES (%s)" % (cols, marks), [rec[f] for f in fields])
            self.conn.commit()
            return "new", cur.lastrowid

    def update_tx(self, tx_id, **fields):
        allowed = set(TX_FIELDS) | {"note", "category", "sub_category", "tags", "direction", "account", "item", "counterparty"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append("%s=?" % k)
            vals.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        vals.append(util.now_str())
        vals.append(tx_id)
        with self._write_lock:
            self.conn.execute("UPDATE tx SET %s WHERE id=?" % ", ".join(sets), vals)
            self.conn.commit()
        return True

    def delete_tx(self, tx_id):
        with self._write_lock:
            self.conn.execute("DELETE FROM tx WHERE id=?", (tx_id,))
            self.conn.commit()

    def get_tx(self, tx_id):
        row = self.conn.execute("SELECT * FROM tx WHERE id=?", (tx_id,)).fetchone()
        return dict(row) if row else None

    def query(self, day_from=None, day_to=None, source=None, category=None, direction=None,
              keyword=None, limit=200, offset=0, order="desc", uncategorized=False, month=None):
        where, params = [], []
        if day_from:
            where.append("day >= ?")
            params.append(day_from)
        if day_to:
            where.append("day <= ?")
            params.append(day_to)
        if month:
            where.append("month = ?")
            params.append(month)
        if source:
            where.append("source = ?")
            params.append(source)
        if category:
            where.append("category = ?")
            params.append(category)
        if direction:
            where.append("direction = ?")
            params.append(direction)
        if uncategorized:
            where.append("(category = '未分类' OR category = '')")
        if keyword:
            like = "%" + keyword + "%"
            where.append("(counterparty LIKE ? OR item LIKE ? OR note LIKE ? OR raw LIKE ? OR category LIKE ?)")
            params.extend([like] * 5)
        sql = "SELECT * FROM tx"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts %s, id %s LIMIT ? OFFSET ?" % ("DESC" if order == "desc" else "ASC", "DESC" if order == "desc" else "ASC")
        params.extend([int(limit), int(offset)])
        rows = [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        cnt_sql = "SELECT COUNT(*) AS c FROM tx"
        if where:
            cnt_sql += " WHERE " + " AND ".join(where)
        total = self.conn.execute(cnt_sql, params[:-2]).fetchone()["c"]
        return rows, total

    # ---------- 规则 ----------
    def add_rule(self, keyword="", category="", sub_category="", priority=100, direction="",
                 field="any", source="", method="", min_cents=None, max_cents=None,
                 time_from="", time_to="", action="categorize", tags="", note="",
                 full_match=False, builtin=False):
        """新增/更新一条规则（按条件指纹去重）。"""
        data = {
            "keyword": keyword or "", "field": field or "any", "category": category or "",
            "sub_category": sub_category or "", "direction": direction or "", "source": source or "",
            "method": method or "", "min_cents": min_cents, "max_cents": max_cents,
            "time_from": time_from or "", "time_to": time_to or "", "action": action or "categorize",
            "tags": tags or "", "note": note or "", "full_match": 1 if full_match else 0,
            "priority": int(priority or 100), "builtin": 1 if builtin else 0,
        }
        data["sig"] = rule_sig(data)
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO rules(sig,keyword,field,category,sub_category,direction,source,method,"
                "min_cents,max_cents,time_from,time_to,action,tags,note,full_match,priority,enabled,builtin,created_at)"
                " VALUES(:sig,:keyword,:field,:category,:sub_category,:direction,:source,:method,"
                ":min_cents,:max_cents,:time_from,:time_to,:action,:tags,:note,:full_match,:priority,1,:builtin,:created_at)"
                " ON CONFLICT(sig) DO UPDATE SET priority=excluded.priority, enabled=1,"
                " note=excluded.note, action=excluded.action, tags=excluded.tags",
                dict(data, created_at=util.now_str()))
            self.conn.commit()
        return data["sig"]

    def mark_legacy_rules_builtin(self):
        """没有版本号的老库里，规则全部来自内置种子，标记一下，界面才不会把它们当成「我加的」。"""
        with self._write_lock:
            self.conn.execute("UPDATE rules SET builtin=1 WHERE builtin=0")
            self.conn.commit()

    def delete_rules_builtin(self):
        """清掉内置规则（用于规则库升级后重建）。"""
        with self._write_lock:
            self.conn.execute("DELETE FROM rules WHERE builtin=1")
            self.conn.commit()

    def list_rules(self, enabled_only=True, with_conditions=False):
        sql = "SELECT * FROM rules"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY priority ASC, length(keyword) DESC, id DESC"
        rows = [dict(r) for r in self.conn.execute(sql).fetchall()]
        if not with_conditions:
            return rows
        return rows

    # ---------- 待录入 ----------
    def add_pending(self, raw, source="", package="", device="", ts="", reason="", amount_cents=None):
        """把没认出来的通知放进待录入队列；重复的通知只累加次数。"""
        sig = util.sha1(util.norm_text(raw)[:160])[:40]
        now = util.now_str()
        with self._write_lock:
            row = self.conn.execute("SELECT id, status FROM pending WHERE sig=?", (sig,)).fetchone()
            if row is not None:
                self.conn.execute(
                    "UPDATE pending SET attempts=attempts+1, ts=?, reason=?, status='open', updated_at=? WHERE id=?",
                    (ts or now, reason, now, row["id"]))
                self.conn.commit()
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO pending(sig,raw,source,package,device,ts,reason,amount_cents,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?, 'open', ?, ?)",
                (sig, raw[:800], source, package, device, ts or now, reason, amount_cents, now, now))
            self.conn.commit()
            return cur.lastrowid

    def list_pending(self, status="open", limit=100):
        rows = self.conn.execute(
            "SELECT * FROM pending WHERE status=? ORDER BY id DESC LIMIT ?", (status, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def get_pending(self, pid):
        row = self.conn.execute("SELECT * FROM pending WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def close_pending(self, pid, status="done", tx_id=None):
        with self._write_lock:
            self.conn.execute("UPDATE pending SET status=?, tx_id=?, updated_at=? WHERE id=?",
                              (status, tx_id, util.now_str(), pid))
            self.conn.commit()

    def count_pending(self):
        return self.conn.execute("SELECT COUNT(*) AS c FROM pending WHERE status='open'").fetchone()["c"]

    # ---------- 周期性支出（订阅/固定支出） ----------
    def recurring(self, direction="out", months_at_least=2, gap_min=25, gap_max=35):
        """同商户 + 同金额连续几个月都出现 -> 固定支出（自动续费、房租、话费…）。

        用「连续月份数」判定，而不是平均间隔：同一个月多买一次不会破坏判断。
        """
        cands = self.conn.execute(
            "SELECT counterparty, amount_cents, COUNT(*) AS n, COUNT(DISTINCT month) AS m"
            " FROM tx WHERE direction=? AND counterparty<>'' AND amount_cents>0"
            " GROUP BY counterparty, amount_cents HAVING m>=?",
            (direction, int(months_at_least))).fetchall()
        found = []
        for c in cands:
            days = [r["day"] for r in self.conn.execute(
                "SELECT day FROM tx WHERE direction=? AND counterparty=? AND amount_cents=? ORDER BY day",
                (direction, c["counterparty"], c["amount_cents"])).fetchall()]
            dates = []
            for d in days:
                try:
                    dates.append(datetime.strptime(d, "%Y-%m-%d"))
                except ValueError:
                    pass
            if len(dates) < 2:
                continue
            months = sorted(set(d.strftime("%Y-%m") for d in dates))
            # 判定标准：连续月份都出现（同月多买一次不影响判断）
            best = run = 1
            for i in range(1, len(months)):
                prev_y, prev_m = int(months[i - 1][:4]), int(months[i - 1][5:7])
                cur_y, cur_m = int(months[i][:4]), int(months[i][5:7])
                run = run + 1 if (cur_y * 12 + cur_m) - (prev_y * 12 + prev_m) == 1 else 1
                best = max(best, run)
            if best < months_at_least:
                continue
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            gaps = [g for g in gaps if g > 3]           # 同月重复买的不参与间隔统计
            avg = (sum(gaps) / float(len(gaps))) if gaps else 30.0
            last = dates[-1]
            y, m = last.year + (1 if last.month == 12 else 0), 1 if last.month == 12 else last.month + 1
            day = min(last.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                                 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
            found.append({
                "counterparty": c["counterparty"], "amount_cents": c["amount_cents"],
                "count": c["n"], "months": len(months), "consecutive_months": best,
                "avg_gap_days": round(avg, 1), "day_of_month": last.day,
                "last_day": days[-1], "next_day": datetime(y, m, day).strftime("%Y-%m-%d"),
                "monthly_cents": c["amount_cents"],
            })
        found.sort(key=lambda x: -x["amount_cents"])
        return found

    def delete_rule(self, rule_id):
        with self._write_lock:
            self.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
            self.conn.commit()

    def bump_rule(self, rule_id):
        self.conn.execute("UPDATE rules SET hits=hits+1 WHERE id=?", (rule_id,))
        self.conn.commit()

    def count_rules(self):
        return self.conn.execute("SELECT COUNT(*) AS c FROM rules").fetchone()["c"]

    # ---------- 导入 / 任务日志 ----------
    def log_import(self, filename, source, file_sha1, total, new, dup, note=""):
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO imports(filename,source,sha1,rows_total,rows_new,rows_dup,imported_at,note)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(sha1) DO UPDATE SET imported_at=excluded.imported_at, rows_total=excluded.rows_total,"
                " rows_new=excluded.rows_new, rows_dup=excluded.rows_dup, note=excluded.note",
                (filename, source, file_sha1, total, new, dup, util.now_str(), note))
            self.conn.commit()

    def import_seen(self, file_sha1):
        row = self.conn.execute("SELECT id, rows_new FROM imports WHERE sha1=?", (file_sha1,)).fetchone()
        return dict(row) if row else None

    def list_imports(self, limit=50):
        rows = self.conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def log_job(self, name, started_at, ok, detail=""):
        self.conn.execute(
            "INSERT INTO jobs(name,started_at,finished_at,ok,detail) VALUES(?,?,?,?,?)",
            (name, started_at, util.now_str(), 1 if ok else 0, detail))
        self.conn.commit()

    def list_jobs(self, limit=30):
        rows = self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def log_push(self, channel, title, body, ok, resp="", kind=""):
        self.conn.execute(
            "INSERT INTO pushes(channel,title,body,ok,resp,kind,created_at) VALUES(?,?,?,?,?,?,?)",
            (channel, title, body, 1 if ok else 0, str(resp)[:500], kind, util.now_str()))
        self.conn.commit()

    # ---------- 统计 ----------
    def totals(self, day_from=None, day_to=None, month=None, exclude_neutral=True):
        where, params = [], []
        if month:
            where.append("month = ?")
            params.append(month)
        if day_from:
            where.append("day >= ?")
            params.append(day_from)
        if day_to:
            where.append("day <= ?")
            params.append(day_to)
        if exclude_neutral:
            where.append("direction IN ('in','out')")
        sql = "SELECT direction, COALESCE(SUM(amount_cents),0) AS s, COUNT(*) AS c FROM tx"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY direction"
        out = {"in": 0, "out": 0, "neutral": 0, "in_count": 0, "out_count": 0, "neutral_count": 0}
        for r in self.conn.execute(sql, params).fetchall():
            d = r["direction"] if r["direction"] in ("in", "out", "neutral") else "neutral"
            out[d] = r["s"]
            out[d + "_count"] = r["c"]
        out["net"] = out["in"] - out["out"]
        return out

    def group_by(self,field, day_from=None, day_to=None, month=None, direction=None, limit=20):
        assert field in ("category", "sub_category", "source", "counterparty", "day", "month", "method", "origin")
        where, params = [], []
        if month:
            where.append("month = ?")
            params.append(month)
        if day_from:
            where.append("day >= ?")
            params.append(day_from)
        if day_to:
            where.append("day <= ?")
            params.append(day_to)
        if direction:
            where.append("direction = ?")
            params.append(direction)
        else:
            where.append("direction IN ('in','out')")
        sql = ("SELECT %s AS k, COALESCE(SUM(amount_cents),0) AS total, COUNT(*) AS cnt FROM tx" % field)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY k ORDER BY total DESC LIMIT ?"
        params.append(int(limit))
        return [{"key": r["k"] or "未填写", "total": r["total"], "count": r["cnt"]}
                for r in self.conn.execute(sql, params).fetchall()]

    def daily_series(self, day_from, day_to):
        rows = self.conn.execute(
            "SELECT day, direction, COALESCE(SUM(amount_cents),0) AS s FROM tx"
            " WHERE day BETWEEN ? AND ? AND direction IN ('in','out') GROUP BY day, direction",
            (day_from, day_to)).fetchall()
        data = {}
        for r in rows:
            d = data.setdefault(r["day"], {"in": 0, "out": 0})
            d[r["direction"]] = r["s"]
        return data

    def count_tx(self):
        return self.conn.execute("SELECT COUNT(*) AS c FROM tx").fetchone()["c"]

    def last_tx_ts(self):
        row = self.conn.execute("SELECT MAX(ts) AS m FROM tx").fetchone()
        return row["m"] if row else None
