/* 记账本 · 纯前端版：本地存储 + 报表 + API 路由
 * 它把 app.js 发出的 /api/... 请求全部在浏览器内实现，数据存 IndexedDB。
 */
(function (global) {
  "use strict";
  var MB = global.MB;
  var U = MB.util;
  var nativeFetch = global.fetch.bind(global);
  global.MONEYBOOK_LOCAL = true;

  var DB_NAME = "moneybook-local", DB_VER = 1;
  var STATE = { tx: [], rules: [], pending: [], kv: {}, ready: false };
  MB.dirty = { rules: false };
  var db = null;

  /* ---------------- IndexedDB ---------------- */

  function openDB() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VER);
      req.onupgradeneeded = function () {
        var d = req.result;
        if (!d.objectStoreNames.contains("tx")) { d.createObjectStore("tx", { keyPath: "id" }); }
        if (!d.objectStoreNames.contains("rules")) { d.createObjectStore("rules", { keyPath: "id" }); }
        if (!d.objectStoreNames.contains("pending")) { d.createObjectStore("pending", { keyPath: "id" }); }
        if (!d.objectStoreNames.contains("kv")) { d.createObjectStore("kv", { keyPath: "k" }); }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function store(name, mode) { return db.transaction(name, mode || "readonly").objectStore(name); }

  function reqToPromise(req) {
    return new Promise(function (resolve, reject) {
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function loadAll(name) {
    return reqToPromise(store(name).getAll());
  }

  function put(name, obj) { return reqToPromise(store(name, "readwrite").put(obj)); }
  function del(name, id) { return reqToPromise(store(name, "readwrite").delete(id)); }
  function clear(name) { return reqToPromise(store(name, "readwrite").clear()); }
  function kvSet(k, v) { STATE.kv[k] = v; return put("kv", { k: k, v: v }); }
  function kvGet(k, dflt) { return STATE.kv[k] === undefined ? dflt : STATE.kv[k]; }

  function nextId(name) {
    var key = "seq_" + name;
    var v = (kvGet(key, 0) || 0) + 1;
    kvSet(key, v);
    return v;
  }

  var DEFAULT_SETTINGS = {
    sync: { interval_hours: 3 },
    reports: { daily_times: ["00:00", "12:00"], daily_mode: "auto", push_daily: true },
    budget: { monthly: 0, categories: {} },
    alerts: { large_amount: 0, budget_percent: 80 },
    notify: { channels: [] },
    categorize: { auto: true, learn_from_manual: true }
  };

  function settings() {
    var s = kvGet("settings", null);
    if (!s) { return JSON.parse(JSON.stringify(DEFAULT_SETTINGS)); }
    return s;
  }
  function mergeSettings(patch) {
    var s = settings();
    Object.keys(patch || {}).forEach(function (k) {
      if (typeof patch[k] === "object" && patch[k] !== null && !Array.isArray(patch[k]) && typeof s[k] === "object") {
        Object.keys(patch[k]).forEach(function (kk) { s[k][kk] = patch[k][kk]; });
      } else { s[k] = patch[k]; }
    });
    kvSet("settings", s);
    return s;
  }

  var readyPromise = null;
  function ready() {
    if (!readyPromise) {
      readyPromise = openDB().then(function (d) {
        db = d;
        return Promise.all([loadAll("tx"), loadAll("rules"), loadAll("pending"), loadAll("kv")]);
      }).then(function (res) {
        STATE.tx = res[0] || [];
        STATE.rules = res[1] || [];
        STATE.pending = res[2] || [];
        (res[3] || []).forEach(function (row) { STATE.kv[row.k] = row.v; });
        if (!STATE.rules.length) { return seedRules(); }
      }).then(function () {
        return handleQuickHash();
      }).then(function () {
        STATE.ready = true;
        return STATE;
      });
    }
    return readyPromise;
  }

  /** 支持用链接直接记一笔：https://.../#q=12.5 肯德基
   *  这样 iPhone 快捷指令只要「打开 URL」一个动作就能记账。
   *  必须在脚本加载时就把参数抓下来——界面启动时会把 hash 重置成 #home。 */
  var QUICK_RAW = (function () {
    var m = String(location.hash || "").match(/^#(?:q|quick)=(.+)$/);
    if (!m) { return null; }
    var raw = m[1];
    try { raw = decodeURIComponent(raw.replace(/\+/g, " ")); } catch (e) { /* 已经是明文 */ }
    try { window.history.replaceState(null, "", location.pathname + location.search + "#home"); } catch (e) { /* ignore */ }
    return raw;
  })();

  /** 安卓 App 里有没有原生桥（有的话就能弹系统通知） */
  function nativeNotify(title, body) {
    try {
      if (global.MoneybookNative && global.MoneybookNative.notify) {
        global.MoneybookNative.notify(title, body);
        return true;
      }
    } catch (e) { /* 不在 App 里 */ }
    return false;
  }

  function extractAmountGuess(text) {
    var m = String(text || "").match(/[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/);
    if (!m) { m = String(text || "").match(/([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元/); }
    return m ? Math.abs(U.parseCents(m[1]) || 0) : null;
  }

  function addPending(raw, source, pkg, reason, amount) {
    var sig = U.hash(U.normText(String(raw).slice(0, 160)));
    for (var i = 0; i < STATE.pending.length; i++) {
      var p = STATE.pending[i];
      if (p.sig === sig) {
        p.attempts = (p.attempts || 1) + 1;
        p.status = "open";
        p.reason = reason;
        put("pending", p);
        return p.id;
      }
    }
    var row = {
      id: nextId("pending"), sig: sig, raw: String(raw).slice(0, 800), source: source || "",
      package: pkg || "", device: "android-app", ts: U.nowStr(), reason: reason,
      amount_cents: amount, status: "open", tx_id: null, attempts: 1,
      created_at: U.nowStr(), updated_at: U.nowStr()
    };
    STATE.pending.push(row);
    put("pending", row);
    return row.id;
  }

  /** 安卓 App 把通知队列喂进来：解析 -> 分类 -> 入库，认不出来的进「待录入」 */
  global.MB.ingestQueue = function (items) {
    if (!items || !items.length) { return 0; }
    var rules = activeRules();
    var saved = [];
    items.forEach(function (it) {
      var text = String((it && it.text) || "");
      var pkg = String((it && it.pkg) || "");
      var ts = (it && it.ts) ? U.fmtTs(new Date(Number(it.ts))) : U.nowStr();
      var parsed = MB.parseNotification(text, { package: pkg, ts: ts });
      if (!parsed.record) {
        addPending(text, "", pkg, parsed.reason, extractAmountGuess(text));
        return;
      }
      var rec = parsed.record;
      rec.origin = "notify";
      if (rec.direction !== "neutral" && MB.rules.shouldNeutralize(rec)) { rec.direction = "neutral"; }
      var rr = MB.rules.applyRules(rules, rec);
      if (rr.action === "ignore") { return; }
      if (!rec.category) {
        var cs = MB.rules.classify(rules, rec);
        rec.category = cs[0]; rec.sub_category = cs[1];
      }
      if (addTx(rec) === "new") { saved.push(rec); }
    });
    persistRulesIfDirty();
    if (saved.length) {
      var lines = saved.slice(0, 4).map(function (r) {
        return (r.direction === "in" ? "+" : "−") + "¥" + U.fmtCents(r.amount_cents) + " " +
               (r.counterparty || r.item || r.category || "");
      });
      var mon = monthStatus(U.monthOf(U.nowStr()));
      nativeNotify("已记 " + saved.length + " 笔",
        lines.join("\n") + "\n本月支出 ¥" + U.fmtCents(mon.out_cents));
    }
    return saved.length;
  };

  function quickMessage(msg) {
    try { global.dispatchEvent(new CustomEvent("mb-quick", { detail: msg })); } catch (e) { /* 老浏览器 */ }
    global.__MB_QUICK_RESULT = msg;
  }

  function handleQuickHash() {
    var raw = QUICK_RAW;
    QUICK_RAW = null;
    if (!raw) { return Promise.resolve(); }
    var res = MB.parseNotification(raw, { loose: true });
    if (!res.record) {
      quickMessage("⚠️ 没识别出交易：" + res.reason + "（试试「12.5 肯德基」）");
      return Promise.resolve();
    }
    var rec = res.record;
    var rr = MB.rules.applyRules(activeRules(), rec);
    if (rr.action === "ignore") {
      quickMessage("这条被规则忽略，没有入账");
      return Promise.resolve();
    }
    if (!rec.category) {
      var cs = MB.rules.classify(activeRules(), rec);
      rec.category = cs[0]; rec.sub_category = cs[1];
    }
    var status = addTx(rec);
    persistRulesIfDirty();
    quickMessage((status === "dup" || status === "merged" ? "🔁 这笔已经记过了：" : "✅ 已记账 ") +
      "¥" + U.fmtCents(rec.amount_cents) + " · " + rec.category +
      (rec.counterparty ? " · " + rec.counterparty : ""));
    return Promise.resolve();
  }

  function seedRules() {
    return nativeFetch("rules.json").then(function (r) { return r.json(); }).then(function (data) {
      var list = (data.rules || []).map(function (r, i) {
        var copy = JSON.parse(JSON.stringify(r));
        copy.id = i + 1;
        return copy;
      });
      STATE.rules = list;
      kvSet("seq_rules", list.length);
      kvSet("rules_version", data.version || 1);
      return Promise.all(list.map(function (r) { return put("rules", r); }));
    }).catch(function () { /* 拿不到 rules.json 也能用，只是没有内置规则 */ });
  }

  function activeRules() {
    return STATE.rules.filter(function (r) { return r.enabled !== 0; })
      .sort(function (a, b) {
        if (a.priority !== b.priority) { return a.priority - b.priority; }
        return String(b.keyword || "").length - String(a.keyword || "").length;
      });
  }

  /* ---------------- 写入流水（含跨渠道去重） ---------------- */

  var ENRICH_FIELDS = ["ext_id", "counterparty", "item", "method", "status", "account"];

  function sameMerchant(a, b) {
    var na = U.normText(a.counterparty || ""), nb = U.normText(b.counterparty || "");
    if (!na || !nb) { return true; }
    return na.indexOf(nb) >= 0 || nb.indexOf(na) >= 0;
  }

  function dedupeKey(rec) {
    if (rec.ext_id) { return "id:" + U.hash(rec.source, rec.ext_id); }
    return "fp:" + U.hash(rec.source, String(rec.ts).slice(0, 16), rec.amount_cents, rec.direction,
                          U.normText(rec.counterparty || ""));
  }

  function addTx(record) {
    var rec = JSON.parse(JSON.stringify(record));
    rec.ts = rec.ts || U.nowStr();
    rec.day = U.dayOf(rec.ts);
    rec.month = U.monthOf(rec.ts);
    rec.amount_cents = Math.abs(parseInt(rec.amount_cents || 0, 10));
    rec.direction = rec.direction || "out";
    rec.dedupe_key = rec.dedupe_key || dedupeKey(rec);
    ["source", "origin", "ext_id", "counterparty", "item", "method", "status", "category",
     "sub_category", "tags", "note", "account", "raw"].forEach(function (f) { if (rec[f] === undefined) { rec[f] = ""; } });
    rec.category = rec.category || "未分类";

    var existing = null;
    for (var i = 0; i < STATE.tx.length; i++) {
      if (STATE.tx[i].dedupe_key === rec.dedupe_key) { existing = STATE.tx[i]; break; }
    }
    if (existing) {
      var changed = false;
      ENRICH_FIELDS.forEach(function (f) {
        var ov = existing[f] || "", nv = rec[f] || "";
        if (nv && nv !== ov && (!ov || String(nv).length > String(ov).length)) { existing[f] = nv; changed = true; }
      });
      if (changed) { put("tx", existing); }
      return changed ? "merged" : "dup";
    }
    // 模糊去重：同来源 + 同金额 + 同方向 + 20 分钟内 + 商户名相近
    var base = new Date(rec.ts.replace(" ", "T"));
    var near = null, bestGap = null;
    STATE.tx.forEach(function (t) {
      if (t.source !== rec.source || t.amount_cents !== rec.amount_cents || t.direction !== rec.direction) { return; }
      if (U.dayOf(t.ts) !== rec.day) { return; }
      var gap = Math.abs(new Date(t.ts.replace(" ", "T")) - base) / 60000;
      if (gap > 20) { return; }
      if (!sameMerchant(t, rec)) { return; }
      if (bestGap === null || gap < bestGap) { bestGap = gap; near = t; }
    });
    if (near) {
      var changed2 = false;
      ENRICH_FIELDS.forEach(function (f) {
        var ov = near[f] || "", nv = rec[f] || "";
        if (nv && nv !== ov && (!ov || String(nv).length > String(ov).length)) { near[f] = nv; changed2 = true; }
      });
      if (changed2) { put("tx", near); }
      return changed2 ? "merged" : "dup";
    }
    rec.id = nextId("tx");
    rec.created_at = rec.updated_at = U.nowStr();
    STATE.tx.push(rec);
    put("tx", rec);
    return "new";
  }

  /* ---------------- 报表 ---------------- */

  function inWindow(t, start, end, inclusive) {
    if (inclusive === false) { return t.ts > start && t.ts <= end; }
    return t.ts >= start && t.ts <= end;
  }

  function windowTotals(start, end, inclusive) {
    var out = { in_cents: 0, out_cents: 0, neutral_cents: 0, cnt: 0, in_cnt: 0, out_cnt: 0, uncat_cnt: 0 };
    STATE.tx.forEach(function (t) {
      if (!inWindow(t, start, end, inclusive)) { return; }
      out.cnt++;
      if (t.direction === "in") { out.in_cents += t.amount_cents; out.in_cnt++; }
      else if (t.direction === "out") { out.out_cents += t.amount_cents; out.out_cnt++; }
      else { out.neutral_cents += t.amount_cents; }
      if (!t.category || t.category === "未分类") { out.uncat_cnt++; }
    });
    out.net = out.in_cents - out.out_cents;
    return out;
  }

  function groupBy(field, start, end, direction, limit, inclusive) {
    var map = {};
    STATE.tx.forEach(function (t) {
      if (!inWindow(t, start, end, inclusive)) { return; }
      if (direction && t.direction !== direction) { return; }
      if (!direction && t.direction === "neutral") { return; }
      var k = t[field] || "未填写";
      if (!map[k]) { map[k] = { key: k, total: 0, count: 0 }; }
      map[k].total += t.amount_cents;
      map[k].count++;
    });
    return Object.keys(map).map(function (k) { return map[k]; })
      .sort(function (a, b) { return b.total - a.total; }).slice(0, limit || 20);
  }

  function topTx(start, end, direction, limit, inclusive) {
    return STATE.tx.filter(function (t) { return inWindow(t, start, end, inclusive) && t.direction === direction; })
      .sort(function (a, b) { return b.amount_cents - a.amount_cents; }).slice(0, limit || 3);
  }

  function summary(start, end, inclusive) {
    var data = windowTotals(start, end, inclusive);
    data.start = start; data.end = end;
    data.by_category = groupBy("category", start, end, "out", 20, inclusive);
    data.by_income_category = groupBy("category", start, end, "in", 10, inclusive);
    data.by_source = groupBy("source", start, end, null, 10, inclusive);
    data.by_source_out = groupBy("source", start, end, "out", 10, inclusive);
    data.top_out = topTx(start, end, "out", 3, inclusive);
    data.top_in = topTx(start, end, "in", 3, inclusive);
    var span = new Date(end.replace(" ", "T")) - new Date(start.replace(" ", "T"));
    var prevStart = U.fmtTs(new Date(new Date(start.replace(" ", "T")) - span));
    var prev = windowTotals(prevStart, start, inclusive);
    data.prev = prev;
    data.out_diff_pct = prev.out_cents > 0
      ? Math.round((data.out_cents - prev.out_cents) * 1000 / prev.out_cents) / 10 : null;
    return data;
  }

  function monthStatus(month) {
    var md = U.monthDays(month);
    var w = windowTotals(md[0] + " 00:00:00", md[1] + " 23:59:59", true);
    var budget = (settings().budget || {}).monthly || 0;
    var cats = {};
    groupBy("category", md[0] + " 00:00:00", md[1] + " 23:59:59", "out", 50, true)
      .forEach(function (c) { cats[c.key] = c.total; });
    var catStatus = [];
    Object.keys((settings().budget || {}).categories || {}).forEach(function (name) {
      var limit = U.parseCents(settings().budget.categories[name]) || 0;
      if (limit <= 0) { return; }
      var spent = cats[name] || 0;
      catStatus.push({ category: name, budget: limit, spent: spent,
                       percent: Math.round(spent * 1000 / limit) / 10 });
    });
    catStatus.sort(function (a, b) { return b.percent - a.percent; });
    var now = new Date();
    return {
      month: month, out_cents: w.out_cents, in_cents: w.in_cents, net: w.net, count: w.cnt,
      out_cnt: w.out_cnt, in_cnt: w.in_cnt, budget: budget,
      budget_percent: budget ? Math.round(w.out_cents * 1000 / budget) / 10 : 0,
      budget_left: budget ? budget - w.out_cents : 0,
      category_budget: catStatus,
      days_in_month: parseInt(md[1].slice(8, 10), 10),
      day_of_month: (month === U.monthOf(U.nowStr())) ? now.getDate() : parseInt(md[1].slice(8, 10), 10)
    };
  }

  function recurring() {
    var groups = {};
    STATE.tx.forEach(function (t) {
      if (t.direction !== "out" || !t.counterparty || !t.amount_cents) { return; }
      var k = t.counterparty + "|" + t.amount_cents;
      (groups[k] = groups[k] || []).push(t);
    });
    var found = [];
    Object.keys(groups).forEach(function (k) {
      var list = groups[k].slice().sort(function (a, b) { return a.day < b.day ? -1 : 1; });
      var months = [];
      list.forEach(function (t) { if (months.indexOf(t.month) < 0) { months.push(t.month); } });
      months.sort();
      var best = 1, run = 1;
      for (var i = 1; i < months.length; i++) {
        var pm = parseInt(months[i - 1].slice(0, 4), 10) * 12 + parseInt(months[i - 1].slice(5, 7), 10);
        var cm = parseInt(months[i].slice(0, 4), 10) * 12 + parseInt(months[i].slice(5, 7), 10);
        run = (cm - pm === 1) ? run + 1 : 1;
        if (run > best) { best = run; }
      }
      if (best < 2) { return; }
      var last = list[list.length - 1];
      var y = parseInt(last.day.slice(0, 4), 10), m = parseInt(last.day.slice(5, 7), 10) + 1;
      if (m > 12) { m = 1; y++; }
      found.push({
        counterparty: last.counterparty, amount_cents: last.amount_cents,
        count: list.length, months: months.length, consecutive_months: best,
        day_of_month: parseInt(last.day.slice(8, 10), 10), last_day: last.day,
        next_day: y + "-" + U.pad2(m) + "-" + last.day.slice(8, 10),
        monthly_cents: last.amount_cents
      });
    });
    return found.sort(function (a, b) { return b.amount_cents - a.amount_cents; });
  }

  function merchants(month) {
    var map = {};
    STATE.tx.forEach(function (t) {
      if (t.direction !== "out" || !t.counterparty || t.month !== month) { return; }
      if (!map[t.counterparty]) { map[t.counterparty] = { name: t.counterparty, cnt: 0, total: 0, last_day: "", category: "" }; }
      var g = map[t.counterparty];
      g.cnt++; g.total += t.amount_cents;
      if (t.day > g.last_day) { g.last_day = t.day; }
      g.category = t.category || g.category;
    });
    return Object.keys(map).map(function (k) { return map[k]; })
      .sort(function (a, b) { return b.total - a.total; });
  }

  function dailyReport(mode, day) {
    mode = mode || (settings().reports || {}).daily_mode || "auto";
    var now = new Date();
    var nowS = U.fmtTs(now), today = U.dayOf(nowS);
    var start, end, inclusive = true, label;
    if (mode === "auto") { mode = now.getHours() < 3 ? "yesterday" : "since_last"; }
    if (mode === "today") {
      var d = day || today;
      start = d + " 00:00:00";
      end = (d === today) ? nowS : d + " 23:59:59";
      label = (d === today) ? d + " 今日（截至 " + end.slice(11, 16) + "）" : d + " 全天";
    } else if (mode === "yesterday") {
      var y = day || U.todayStr(-1);
      start = y + " 00:00:00"; end = y + " 23:59:59"; label = y + " 全天";
    } else {
      var last = kvGet("last_report_ts", null);
      if (last) { start = last; inclusive = false; }
      else { start = U.fmtTs(new Date(now.getTime() - 24 * 3600 * 1000)); }
      end = nowS;
      label = start.slice(5, 16) + " ~ " + end.slice(5, 16);
    }
    var data = summary(start, end, inclusive);
    data.label = label;
    data.month = monthStatus(U.monthOf(end));
    data.day = U.dayOf(end);
    data.mode = mode;
    data.weekday = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][new Date(end.slice(0, 10) + "T00:00:00").getDay()];
    return data;
  }

  var SOURCE_LABEL = { alipay: "支付宝", wechat: "微信", bank: "银行", manual: "手工", other: "其他" };
  function bar(pct, width) {
    width = width || 10;
    var filled = Math.round(Math.max(0, Math.min(100, pct)) / 100 * width);
    return new Array(filled + 1).join("█") + new Array(width - filled + 1).join("░");
  }
  function fmt(c) { return U.fmtCents(c); }

  function renderReport(data) {
    var L = [];
    L.push("📊 记账本 · " + (data.label || "") + (data.weekday ? " " + data.weekday : ""));
    L.push(new Array(17).join("─"));
    L.push("💸 支出 ¥" + fmt(data.out_cents) + "（" + data.out_cnt + " 笔）");
    L.push("💰 收入 ¥" + fmt(data.in_cents) + "（" + data.in_cnt + " 笔）");
    L.push("📉 结余 " + (data.net < 0 ? "-" : "+") + "¥" + fmt(Math.abs(data.net)));
    if (data.neutral_cents) { L.push("🔄 转账/还款等不计收支 ¥" + fmt(data.neutral_cents)); }
    if (data.out_diff_pct !== null && data.out_diff_pct !== undefined) {
      var d = data.out_diff_pct;
      L.push("📈 较上一周期 " + (d > 0 ? "↑" : d < 0 ? "↓" : "→") + Math.abs(d).toFixed(1) + "%");
    }
    if (data.by_category && data.by_category.length) {
      L.push(new Array(17).join("─"));
      L.push("分类占比");
      data.by_category.slice(0, 6).forEach(function (c) {
        var pct = data.out_cents ? c.total * 100 / data.out_cents : 0;
        L.push("  " + (c.key + "      ").slice(0, 6) + " ¥" + (fmt(c.total) + "         ").slice(0, 9) +
               " " + bar(pct) + " " + Math.round(pct) + "%");
      });
    }
    var m = data.month || {};
    if (m.recurring_count) {
      L.push("🔁 固定支出 " + m.recurring_count + " 项，每月约 ¥" + fmt(m.recurring_cents));
    }
    if (m.budget) {
      L.push(new Array(17).join("─"));
      L.push("📅 本月累计支出 ¥" + fmt(m.out_cents) + "｜预算 ¥" + fmt(m.budget) +
             "（" + m.budget_percent.toFixed(1) + "%）" + bar(m.budget_percent));
    } else if (m.out_cents) {
      L.push("📅 本月累计支出 ¥" + fmt(m.out_cents));
    }
    if (data.uncat_cnt) { L.push("❓ " + data.uncat_cnt + " 笔未分类"); }
    if (!data.cnt) { L.push("（本区间没有新流水）"); }
    return L.join("\n");
  }

  /* ---------------- 推送（浏览器直连） ---------------- */

  function pushOne(ch, title, body) {
    var t = (ch.type || "").toLowerCase();
    if (t === "console") { console.log(title + "\n" + body); return Promise.resolve({ ok: true, resp: "输出到控制台" }); }
    if (t === "bark") {
      var url = (ch.url || "https://api.day.app").replace(/\/+$/, "") + (ch.key ? "/" + ch.key : "");
      return nativeFetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, body: body, group: ch.group || "记账本", isArchive: 1 })
      }).then(function (r) { return { ok: r.ok, resp: "HTTP " + r.status }; })
        .catch(function (e) { return { ok: false, resp: "浏览器直连被拦截（CORS）：" + e.message }; });
    }
    if (t === "ntfy") {
      var server = (ch.server || "https://ntfy.sh").replace(/\/+$/, "");
      return nativeFetch(server + "/" + ch.topic, {
        method: "POST", body: body, headers: { Title: encodeURIComponent(title) }
      }).then(function (r) { return { ok: r.ok, resp: "HTTP " + r.status }; })
        .catch(function (e) { return { ok: false, resp: "连不上：" + e.message }; });
    }
    if (t === "webhook" || t === "custom") {
      return nativeFetch(ch.url, {
        method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, ch.headers || {}),
        body: JSON.stringify({ title: title, body: body, text: title + "\n" + body, app: "moneybook" })
      }).then(function (r) { return { ok: r.ok, resp: "HTTP " + r.status }; })
        .catch(function (e) { return { ok: false, resp: "连不上：" + e.message }; });
    }
    if (t === "telegram") {
      return nativeFetch("https://api.telegram.org/bot" + ch.token + "/sendMessage", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: ch.chat_id, text: title + "\n\n" + body })
      }).then(function (r) { return { ok: r.ok, resp: "HTTP " + r.status }; })
        .catch(function (e) { return { ok: false, resp: "连不上：" + e.message }; });
    }
    return Promise.resolve({ ok: false, resp: "纯前端版暂不支持 " + t + " 通道（浏览器直连会被 CORS 拦截），建议用服务端版本" });
  }

  function pushAll(title, body) {
    var channels = (settings().notify || {}).channels || [];
    if (!channels.length) { return Promise.resolve([{ type: "none", ok: false, resp: "没有配置推送通道" }]); }
    return Promise.all(channels.filter(function (c) { return c && c.enabled !== false; })
      .map(function (c) { return pushOne(c, title, body); }))
      .then(function (results) {
        STATE.kv.pushes = [{ at: U.nowStr(), title: title, results: results }]
          .concat(kvGet("pushes", [])).slice(0, 20);
        put("kv", { k: "pushes", v: STATE.kv.pushes });
        return results;
      });
  }

  /* ---------------- 查询辅助 ---------------- */

  function queryTx(q) {
    var list = STATE.tx.slice();
    if (q.month) { list = list.filter(function (t) { return t.month === q.month; }); }
    if (q.from) { list = list.filter(function (t) { return t.day >= q.from; }); }
    if (q.to) { list = list.filter(function (t) { return t.day <= q.to; }); }
    if (q.source) { list = list.filter(function (t) { return t.source === q.source; }); }
    if (q.category) { list = list.filter(function (t) { return t.category === q.category; }); }
    if (q.direction) { list = list.filter(function (t) { return t.direction === q.direction; }); }
    if (q.uncategorized === "1") {
      list = list.filter(function (t) { return !t.category || t.category === "未分类"; });
    }
    if (q.q) {
      var kw = String(q.q).toLowerCase();
      list = list.filter(function (t) {
        return [t.counterparty, t.item, t.note, t.raw, t.category].some(function (f) {
          return String(f || "").toLowerCase().indexOf(kw) >= 0;
        });
      });
    }
    list.sort(function (a, b) { return a.ts === b.ts ? b.id - a.id : (a.ts < b.ts ? 1 : -1); });
    if (q.order === "asc") { list.reverse(); }
    return list;
  }

  function ingest(records, source, origin) {
    var rules = activeRules(), auto = (settings().categorize || {}).auto !== false;
    var stats = { source: source, total: records.length, new: 0, dup: 0, merged: 0, neutral: 0, ignored: 0 };
    records.forEach(function (raw) {
      var rec = JSON.parse(JSON.stringify(raw));
      rec.origin = rec.origin || origin || "import";
      if (rec.direction !== "neutral" && MB.rules.shouldNeutralize(rec)) { rec.direction = "neutral"; }
      if (auto) {
        var res = MB.rules.applyRules(rules, rec);
        if (res.action === "ignore") { stats.ignored++; return; }
        if (!rec.category) {
          var cs = MB.rules.classify(rules, rec);
          rec.category = cs[0]; rec.sub_category = cs[1];
        }
      } else { rec.category = rec.category || "未分类"; }
      if (rec.direction === "neutral") { stats.neutral++; }
      var status = addTx(rec);
      stats[status] = (stats[status] || 0) + 1;
    });
    persistRulesIfDirty();
    return stats;
  }

  function persistRulesIfDirty() {
    if (!MB.dirty.rules) { return; }
    MB.dirty.rules = false;
    STATE.rules.forEach(function (r) { put("rules", r); });
  }

  /* ---------------- 路由 ---------------- */

  function json(obj) {
    return Promise.resolve(new Response(JSON.stringify(obj), {
      status: 200, headers: { "Content-Type": "application/json; charset=utf-8" }
    }));
  }
  function text(body, ctype) {
    return Promise.resolve(new Response(body, {
      status: 200, headers: { "Content-Type": (ctype || "text/plain") + "; charset=utf-8" }
    }));
  }
  function fail(msg, code) {
    return Promise.resolve(new Response(JSON.stringify({ ok: false, error: msg }), {
      status: code || 400, headers: { "Content-Type": "application/json; charset=utf-8" }
    }));
  }

  function handle(path, method, q, body, rawBody, headers) {
    var cfg = settings();

    if (path === "/api/ping") {
      return json({ ok: true, app: "moneybook", version: "1.0.0-local", time: U.nowStr(),
                    auth_required: false, local: true });
    }
    if (path === "/api/server-info") {
      var base = location.origin + location.pathname.replace(/[^/]*$/, "");
      return json({ ok: true, ips: [], port: location.port || "", urls: [base], token: "",
                    webui_dir: "浏览器本地存储（IndexedDB），数据不会离开这台设备" });
    }
    if (path === "/api/overview" && method === "GET") {
      var day = q.day || U.todayStr();
      var month = q.month || U.monthOf(U.nowStr());
      var md = U.monthDays(month);
      var daily = {};
      STATE.tx.forEach(function (t) {
        if (t.month !== month || t.direction === "neutral") { return; }
        daily[t.day] = daily[t.day] || { out: 0, in: 0 };
        daily[t.day][t.direction] += t.amount_cents;
      });
      var days = U.dateRange(md[0], md[1]);
      if (month === U.monthOf(U.nowStr())) { days = days.filter(function (d) { return d <= day; }); }
      return json({
        ok: true,
        series: days.map(function (d) { return { day: d, out: (daily[d] || {}).out || 0, in: (daily[d] || {}).in || 0 }; }),
        today: summary(day + " 00:00:00", day + " 23:59:59", true),
        month: monthStatus(month),
        scheduler: { running: true, busy: false, interval_hours: null, last_sync: kvGet("last_import_at", null),
                     next_sync: null, next_daily_report: nextDailySlot(), daily_times: (cfg.reports || {}).daily_times || [] },
        counts: { total: STATE.tx.length,
                  uncategorized: STATE.tx.filter(function (t) { return !t.category || t.category === "未分类"; }).length,
                  pending: STATE.pending.filter(function (p) { return p.status === "open"; }).length },
        last_sync: kvGet("last_import_at", null),
        last_report_text: kvGet("last_report_text", null),
        server_time: U.nowStr(), local: true
      });
    }
    if (path === "/api/summary" && method === "GET") {
      var start = q.start || U.todayStr() + " 00:00:00", end = q.end || U.todayStr() + " 23:59:59";
      if (q.day) { start = q.day + " 00:00:00"; end = q.day + " 23:59:59"; }
      if (q.month) { var m2 = U.monthDays(q.month); start = m2[0] + " 00:00:00"; end = m2[1] + " 23:59:59"; }
      return json({ ok: true, summary: summary(start, end, true) });
    }
    if (path === "/api/tx" && method === "GET") {
      if (q.id) {
        var one = STATE.tx.filter(function (t) { return String(t.id) === String(q.id); })[0];
        return json({ ok: !!one, item: one || null });
      }
      var all = queryTx(q);
      var limit = parseInt(q.limit || "200", 10), offset = parseInt(q.offset || "0", 10);
      return json({ ok: true, total: all.length, items: all.slice(offset, offset + limit) });
    }
    if (path.indexOf("/api/tx/") === 0 && method === "GET") {
      var id = parseInt(path.split("/")[3], 10);
      var row = STATE.tx.filter(function (t) { return t.id === id; })[0];
      return json({ ok: !!row, item: row || null });
    }
    if (path === "/api/stats" && method === "GET") {
      var sm = q.month || U.monthOf(U.nowStr());
      var smd = U.monthDays(sm);
      var seriesMap = {};
      STATE.tx.forEach(function (t) {
        if (t.month !== sm || t.direction === "neutral") { return; }
        seriesMap[t.day] = seriesMap[t.day] || { out: 0, in: 0 };
        seriesMap[t.day][t.direction] += t.amount_cents;
      });
      var cats = {}, incs = {}, methods = {};
      STATE.tx.forEach(function (t) {
        if (t.month !== sm) { return; }
        if (t.direction === "out") {
          cats[t.category || "未分类"] = (cats[t.category || "未分类"] || 0) + t.amount_cents;
          var mk = t.method || "未知";
          methods[mk] = (methods[mk] || 0) + t.amount_cents;
        } else if (t.direction === "in") {
          incs[t.category || "未分类"] = (incs[t.category || "未分类"] || 0) + t.amount_cents;
        }
      });
      var toArr = function (obj) {
        return Object.keys(obj).map(function (k) { return { category: k, total: obj[k] }; })
          .sort(function (a, b) { return b.total - a.total; });
      };
      return json({
        ok: true, month: sm,
        daily: U.dateRange(smd[0], smd[1]).map(function (d) {
          return { day: d, out: (seriesMap[d] || {}).out || 0, in: (seriesMap[d] || {}).in || 0 };
        }),
        categories: toArr(cats), income_categories: toArr(incs),
        methods: toArr(methods).map(function (x) { return { k: x.category, total: x.total }; }),
        month_status: monthStatus(sm)
      });
    }
    if ((path === "/api/report/preview" || path === "/api/report/daily") && method === "GET") {
      var rep = dailyReport(q.mode, q.day);
      return text(renderReport(rep));
    }
    if (path === "/api/settings" && method === "GET") {
      return json({ ok: true, settings: cfg, token_set: false,
                    paths: { db: "浏览器本地存储（IndexedDB）", inbox: "（纯前端版没有投递目录，用「导入账单」上传）" },
                    channels_supported: ["console", "bark", "ntfy", "telegram", "webhook"] });
    }
    if (path === "/api/rules" && method === "GET") {
      return json({ ok: true, items: STATE.rules.slice().sort(function (a, b) { return a.id - b.id; }),
                    total: STATE.rules.length,
                    custom: STATE.rules.filter(function (r) { return !r.builtin; }).length,
                    fields: { any: "商户或商品", peer: "交易对方", item: "商品说明", category: "交易分类", method: "支付方式" },
                    actions: { categorize: "归到某个分类", neutral: "标记为不计收支", ignore: "直接忽略不入账", tag: "只打标签" } });
    }
    if (path === "/api/pending" && method === "GET") {
      var open = STATE.pending.filter(function (p) { return p.status === "open"; });
      return json({ ok: true, items: open, total: open.length,
                    hint: "这些是没认出来的通知。补上金额和商户就能入账，顺手还能生成一条规则。" });
    }
    if (path === "/api/subscriptions" && method === "GET") {
      var subs = recurring();
      var total = subs.reduce(function (s, x) { return s + x.amount_cents; }, 0);
      return json({ ok: true, items: subs, monthly_total: total, yearly_total: total * 12 });
    }
    if (path === "/api/merchants" && method === "GET") {
      var mm = q.month || U.monthOf(U.nowStr());
      return json({ ok: true, month: mm, items: merchants(mm) });
    }
    if (path === "/api/imports" && method === "GET") {
      return json({ ok: true, items: kvGet("imports", []) });
    }
    if (path === "/api/jobs" && method === "GET") {
      return json({ ok: true, items: kvGet("jobs", []), scheduler: { running: true, daily_times: (cfg.reports || {}).daily_times } });
    }
    if (path === "/api/pushes" && method === "GET") {
      var raw = kvGet("pushes", []) || [];
      var flat = [];
      raw.forEach(function (p) {
        p.results.forEach(function (r) {
          flat.push({ channel: r.type, title: p.title, ok: r.ok ? 1 : 0, resp: r.resp, created_at: p.at });
        });
      });
      return json({ ok: true, items: flat.slice(0, 20) });
    }

    /* ---------- 写操作 ---------- */

    if (path === "/api/import" && method === "POST") {
      var name = "upload.csv";
      try { name = decodeURIComponent((headers && headers["X-Filename"]) || "upload.csv"); } catch (e) { /* ignore */ }

      function logImport(fname, source, stats) {
        var imports = kvGet("imports", []);
        imports.unshift({ filename: fname, source: source, rows_new: stats["new"],
                          rows_dup: (stats.dup || 0) + (stats.merged || 0), imported_at: U.nowStr() });
        kvSet("imports", imports.slice(0, 30));
        kvSet("last_import_at", U.nowStr());
      }

      function parseOne(txt, fname) {
        var parsed = MB.parseStatement(txt, fname);
        var stats = ingest(parsed.records, parsed.source, "import");
        stats.filename = fname;
        stats.label = SOURCE_LABEL[parsed.source] || parsed.source;
        stats.meta = parsed.meta || {};
        return stats;
      }

      function handleBuffer(buf) {
        var bytes = new Uint8Array(buf);
        if (MB.isZip(bytes)) {                       // 支付宝/微信导出的都是 zip
          return MB.readZip(bytes).then(function (res) {
            if (!res.files.length) {
              return fail("这个 zip 里没有 CSV 账单。" +
                (res.skipped.length ? "跳过了：" + res.skipped.join("、") +
                 "。带密码的 zip 请先在 iPhone「文件」App 里长按解压（会提示输密码），再选解压出来的 CSV。" : ""));
            }
            var sum = { source: "", total: 0, "new": 0, dup: 0, merged: 0, neutral: 0, ignored: 0, files: [] };
            res.files.forEach(function (f2) {
              var st = parseOne(f2.text, f2.name);
              ["total", "new", "dup", "merged", "neutral", "ignored"].forEach(function (k) { sum[k] += st[k] || 0; });
              if (!sum.source) { sum.source = st.source; }
              sum.files.push({ name: f2.name, total: st.total, isnew: st["new"] });
            });
            sum.label = SOURCE_LABEL[sum.source] || sum.source;
            sum.from_zip = true;
            sum.skipped = res.skipped;
            logImport(name, sum.source, sum);
            return json({ ok: true, stats: sum });
          }).catch(function (e) { return fail("解压失败：" + e.message); });
        }
        var stats = parseOne(U.decodeBytes(bytes), name);
        logImport(name, stats.source, stats);
        return json({ ok: true, stats: stats });
      }

      if (rawBody && rawBody.arrayBuffer) {
        return rawBody.arrayBuffer().then(handleBuffer);
      }
      var textBody = body && (body.text || body._raw);
      if (textBody) { return handleBuffer(new TextEncoder().encode(textBody).buffer); }
      return fail("没有收到文件内容");
    }

    if (path === "/api/tx" && method === "POST") {
      var cents = U.parseCents(body.amount);
      if (cents === null) { return fail("金额格式不正确"); }
      var rec = {
        source: "manual", origin: "manual", ext_id: "", ts: U.parseDateTimeLoose(body.ts) || U.nowStr(),
        amount_cents: Math.abs(cents), direction: body.direction || (cents < 0 ? "in" : "out"),
        counterparty: body.counterparty || "", item: body.item || "", method: "", status: "",
        category: body.category && body.category !== "未分类" ? body.category : "",
        sub_category: body.sub_category || "", note: body.note || "", raw: "[手工记账]"
      };
      if (rec.direction !== "neutral" && MB.rules.shouldNeutralize(rec)) { rec.direction = "neutral"; }
      if (!rec.category) {
        var r2 = MB.rules.applyRules(activeRules(), rec);
        if (r2.action === "ignore") { return json({ ok: false, error: "这条被规则标记为「忽略」了，没有入账" }); }
        if (!rec.category) { var cs2 = MB.rules.classify(activeRules(), rec); rec.category = cs2[0]; rec.sub_category = cs2[1]; }
      }
      var st = addTx(rec);
      persistRulesIfDirty();
      return json({ ok: true, status: st, id: st === "new" ? STATE.tx[STATE.tx.length - 1].id : null });
    }

    if (path.indexOf("/api/tx/") === 0 && (method === "POST" || method === "PATCH")) {
      var tid = parseInt(path.split("/")[3], 10);
      var t = STATE.tx.filter(function (x) { return x.id === tid; })[0];
      if (!t) { return fail("找不到这笔流水", 404); }
      var learned = null;
      if (body.amount !== undefined) { var c3 = U.parseCents(body.amount); if (c3 !== null) { t.amount_cents = Math.abs(c3); } }
      ["category", "sub_category", "note", "direction", "counterparty", "item", "tags"].forEach(function (f) {
        if (body[f] !== undefined) { t[f] = body[f]; }
      });
      t.updated_at = U.nowStr();
      put("tx", t);
      if (body.category && (settings().categorize || {}).learn_from_manual !== false && t.counterparty) {
        addRule({ keyword: t.counterparty, category: body.category, sub_category: body.sub_category || "",
                  field: "peer", priority: 5, note: "从手工改分类学习" });
        learned = t.counterparty;
      }
      persistRulesIfDirty();
      return json({ ok: true, learned_rule: learned, item: t });
    }

    if (path.indexOf("/api/tx/") === 0 && method === "DELETE") {
      var did = parseInt(path.split("/")[3], 10);
      STATE.tx = STATE.tx.filter(function (x) { return x.id !== did; });
      del("tx", did);
      return json({ ok: true });
    }

    if (path === "/api/rules" && method === "POST") {
      var action = body.action || "categorize";
      if (action === "categorize" && !(body.category || "").trim()) { return fail("归类规则要选一个分类"); }
      if (action === "tag" && !(body.tags || "").trim()) { return fail("打标签规则要填标签名"); }
      var rule = addRule({
        keyword: (body.keyword || "").trim(), field: body.field || "any", action: action,
        category: body.category || "", sub_category: body.sub_category || "", tags: body.tags || "",
        min_cents: body.min_amount === "" || body.min_amount === undefined ? null : U.parseCents(body.min_amount),
        max_cents: body.max_amount === "" || body.max_amount === undefined ? null : U.parseCents(body.max_amount),
        time_from: body.time_from || "", time_to: body.time_to || "", direction: body.direction || "",
        source: body.source || "", method: body.method || "", note: body.note || "",
        priority: parseInt(body.priority || 50, 10), full_match: body.full_match ? 1 : 0
      });
      return json({ ok: true, id: rule.id, items: STATE.rules });
    }

    if (path === "/api/rules/test" && method === "POST") {
      var sample = {
        source: body.source || "wechat", ts: body.ts || U.nowStr(),
        amount_cents: Math.abs(U.parseCents(body.amount) || 0), direction: body.direction || "out",
        counterparty: body.counterparty || "", item: body.item || "", category_raw: body.category_raw || "",
        method: body.method || "", raw: body.raw || body.text || ""
      };
      var res = MB.rules.evaluate(activeRules(), sample);
      return json({ ok: true, matched: !!(res.action || res.tags.length),
                    action: res.action || (res.tags.length ? "tag" : ""),
                    category: res.category, sub_category: res.sub_category, tags: res.tags,
                    rule: res.rule ? { id: res.rule.id, keyword: res.rule.keyword, field: res.rule.field,
                                       category: res.rule.category, sub_category: res.rule.sub_category,
                                       priority: res.rule.priority, note: res.rule.note } : null });
    }

    if (path.indexOf("/api/rules/") === 0 && method === "DELETE") {
      var rid = parseInt(path.split("/")[3], 10);
      STATE.rules = STATE.rules.filter(function (r) { return r.id !== rid; });
      del("rules", rid);
      return json({ ok: true });
    }

    if (path.indexOf("/api/pending/") === 0 && method === "POST") {
      var pid = parseInt(path.split("/")[3], 10);
      var p = STATE.pending.filter(function (x) { return x.id === pid; })[0];
      if (!p) { return fail("这条待录入记录不在了", 404); }
      if (body.action === "ignore") {
        p.status = "ignored"; put("pending", p);
        return json({ ok: true, status: "ignored" });
      }
      var cents4 = body.amount !== undefined && body.amount !== "" ? U.parseCents(body.amount) : p.amount_cents;
      if (!cents4) { return fail("请先填金额"); }
      var parsed2 = MB.parseNotification(p.raw || "", { loose: true, source: p.source || undefined, ts: p.ts });
      var rec4 = {
        source: p.source || (parsed2.record ? parsed2.record.source : "other"), origin: "manual", ext_id: "",
        ts: U.parseDateTimeLoose(body.ts) || p.ts || U.nowStr(), amount_cents: Math.abs(cents4),
        direction: body.direction || "out", counterparty: (body.counterparty || "").trim(),
        item: (body.item || "").trim(), category: (body.category || "").trim(),
        sub_category: body.sub_category || "", method: "", status: "",
        raw: "[待录入补记] " + String(p.raw || "").slice(0, 300)
      };
      if (!rec4.counterparty && parsed2.record) { rec4.counterparty = parsed2.record.counterparty || ""; }
      if (!rec4.category) { var cs4 = MB.rules.classify(activeRules(), rec4); rec4.category = cs4[0]; rec4.sub_category = cs4[1]; }
      var st4 = addTx(rec4);
      p.status = "done"; put("pending", p);
      var learned4 = null;
      if (rec4.category && rec4.counterparty && (settings().categorize || {}).learn_from_manual !== false) {
        addRule({ keyword: rec4.counterparty, category: rec4.category, sub_category: rec4.sub_category,
                  field: "peer", priority: 5, note: "从待录入补录学习" });
        learned4 = rec4.counterparty;
      }
      persistRulesIfDirty();
      var newTx = STATE.tx[STATE.tx.length - 1];
      return json({ ok: true, status: st4, id: newTx.id, learned_rule: learned4, item: newTx });
    }
    if (path.indexOf("/api/pending/") === 0 && method === "DELETE") {
      var pid2 = parseInt(path.split("/")[3], 10);
      STATE.pending = STATE.pending.filter(function (x) { return x.id !== pid2; });
      del("pending", pid2);
      return json({ ok: true });
    }

    if (path === "/api/merchants/recategorize" && method === "POST") {
      var name2 = (body.counterparty || "").trim();
      if (!name2 || !body.category) { return fail("商户和分类都要填"); }
      var n = 0;
      STATE.tx.forEach(function (t) {
        if (t.counterparty === name2) {
          t.category = body.category; t.sub_category = body.sub_category || ""; put("tx", t); n++;
        }
      });
      addRule({ keyword: name2, category: body.category, sub_category: body.sub_category || "",
                field: "peer", priority: 5, note: "从商户批量修改学习" });
      persistRulesIfDirty();
      return json({ ok: true, updated: n, category: body.category });
    }

    if (path === "/api/recategorize" && method === "POST") {
      var updated = 0, rules2 = activeRules();
      STATE.tx.forEach(function (t) {
        if (t.category && t.category !== "未分类") { return; }
        var cs5 = MB.rules.classify(rules2, t);
        if (cs5[0] && cs5[0] !== "未分类") {
          t.category = cs5[0]; t.sub_category = cs5[1]; put("tx", t); updated++;
        }
      });
      persistRulesIfDirty();
      return json({ ok: true, updated: updated });
    }

    if (path === "/api/notify/test" && method === "POST") {
      if (global.Notification && Notification.permission === "default") { Notification.requestPermission(); }
      return pushAll("记账本推送测试", "如果你收到这条消息，说明推送通道配置成功。\n" + U.nowStr())
        .then(function (results) { return json({ ok: results.some(function (r) { return r.ok; }), results: results }); });
    }

    if (path === "/api/settings" && method === "POST") {
      mergeSettings(body);
      return json({ ok: true, settings: settings() });
    }

    if (path === "/api/sync" && method === "POST") {
      kvSet("last_import_at", U.nowStr());
      return json({ ok: true, local: true,
                    sync: { ok: true, new: 0, detail: "纯前端版没有后台服务：请用「设置 → 导入账单」上传导出的 CSV" } });
    }

    if (path === "/api/report/push" && method === "POST") {
      var rep2 = dailyReport(body.mode);
      var bodyText = renderReport(rep2);
      kvSet("last_report_ts", rep2.end);
      kvSet("last_report_text", bodyText);
      return pushAll("账单 " + rep2.label + " · 支出¥" + fmt(rep2.out_cents), bodyText).then(function (results) {
        return json({ ok: true, text: bodyText, title: "账单", results: results });
      });
    }

    if (path === "/quick") {
      var res2 = MB.parseNotification(q.text || "", { loose: true });
      if (!res2.record) { return text("⚠️ 没识别出交易：" + res2.reason); }
      var rec5 = res2.record;
      var rr = MB.rules.applyRules(activeRules(), rec5);
      if (rr.action === "ignore") { return text("这条被规则忽略"); }
      if (!rec5.category) { var cs6 = MB.rules.classify(activeRules(), rec5); rec5.category = cs6[0]; }
      addTx(rec5);
      persistRulesIfDirty();
      return text("✅ 已记账 ¥" + fmt(rec5.amount_cents) + " · " + rec5.category + " · " + (rec5.counterparty || ""));
    }

    return fail("纯前端版没有这个接口：" + path, 404);
  }

  function addRule(data) {
    var rule = {
      id: nextId("rules"), keyword: data.keyword || "", field: data.field || "any",
      category: data.category || "", sub_category: data.sub_category || "",
      direction: data.direction || "", source: data.source || "", method: data.method || "",
      min_cents: data.min_cents === undefined ? null : data.min_cents,
      max_cents: data.max_cents === undefined ? null : data.max_cents,
      time_from: data.time_from || "", time_to: data.time_to || "",
      action: data.action || "categorize", tags: data.tags || "", note: data.note || "",
      full_match: data.full_match || 0, priority: data.priority || 100, enabled: 1, builtin: 0, hits: 0
    };
    // 同样的条件只留一条
    STATE.rules = STATE.rules.filter(function (r) {
      return !(r.keyword === rule.keyword && r.field === rule.field && r.category === rule.category &&
               r.sub_category === rule.sub_category && r.direction === rule.direction &&
               r.min_cents === rule.min_cents && r.max_cents === rule.max_cents &&
               r.time_from === rule.time_from && r.action === rule.action);
    });
    STATE.rules.push(rule);
    put("rules", rule);
    return rule;
  }

  function nextDailySlot() {
    var times = (settings().reports || {}).daily_times || ["12:00"];
    var now = new Date(), best = null;
    times.forEach(function (t) {
      var parts = String(t).split(":"), d = new Date(now.getFullYear(), now.getMonth(), now.getDate(),
                                                     parseInt(parts[0], 10) || 0, parseInt(parts[1], 10) || 0);
      if (d <= now) { d.setDate(d.getDate() + 1); }
      if (!best || d < best) { best = d; }
    });
    return best ? U.fmtTs(best) : null;
  }

  /* ---------------- 到点出账单（页面开着时） ---------------- */

  function checkDaily() {
    var times = (settings().reports || {}).daily_times || [];
    var now = new Date(), hhmm = U.pad2(now.getHours()) + ":" + U.pad2(now.getMinutes());
    var today = U.dayOf(U.fmtTs(now));
    times.forEach(function (slot) {
      if (hhmm < slot) { return; }
      if (kvGet("daily_done_" + slot.replace(":", "") + "_" + today, false)) { return; }
      kvSet("daily_done_" + slot.replace(":", "") + "_" + today, true);
      var rep = dailyReport("auto");
      var bodyText = renderReport(rep);
      kvSet("last_report_ts", rep.end);
      kvSet("last_report_text", bodyText);
      var title = "账单 · 支出 ¥" + fmt(rep.out_cents);
      pushAll(title, bodyText);
      nativeNotify(title, bodyText);
      if (global.Notification && Notification.permission === "granted" && !global.MoneybookNative) {
        try { new Notification(title, { body: bodyText.slice(0, 120) }); } catch (e) { /* ignore */ }
      }
    });
  }

  /* ---------------- 安装 fetch 拦截 ---------------- */

  global.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path, query = {};
    try {
      var u = new URL(url, location.href);
      path = u.pathname.replace(/\/+$/, "") || "/";
      u.searchParams.forEach(function (v, k) { query[k] = v; });
    } catch (e) { return nativeFetch(input, init); }
    if (path.indexOf("/api/") !== 0 && path !== "/quick") { return nativeFetch(input, init); }
    var method = ((init && init.method) || "GET").toUpperCase();
    var headers = (init && init.headers) || {};
    var body = null;
    if (init && init.body && typeof init.body === "string") {
      try { body = JSON.parse(init.body); } catch (e) { body = { _raw: init.body }; }
    }
    return ready().then(function () {
      return handle(path, method, query, body, init && init.body, headers);
    });
  };

  /* 导出 CSV：纯前端版直接生成文件下载 */
  var nativeOpen = global.open;
  global.open = function (url) {
    if (String(url).indexOf("/api/export.csv") === 0) {
      ready().then(function () {
        var label = { in: "收入", out: "支出", neutral: "不计收支" };
        var lines = ["时间,来源,方向,金额,分类,子分类,交易对方,商品说明,支付方式,状态,备注"];
        queryTx({ order: "asc", limit: "100000" }).forEach(function (t) {
          lines.push([t.ts, SOURCE_LABEL[t.source] || t.source, label[t.direction] || t.direction,
                      U.fmtCents(t.amount_cents), t.category, t.sub_category, t.counterparty,
                      t.item, t.method, t.status, t.note]
            .map(function (v) { return '"' + String(v === null || v === undefined ? "" : v).replace(/"/g, "'") + '"'; })
            .join(","));
        });
        var blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "moneybook.csv";
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 3000);
      });
      return null;
    }
    return nativeOpen.apply(global, arguments);
  };

  // 后台模式（安卓 App 的常驻服务用 ?bg=1 打开）才负责定时账单，
  // 这样 App 界面和后台服务不会各弹一次
  var IS_BG = /[?&]bg=1/.test(location.search);
  global.MB.checkDaily = checkDaily;
  global.MB.isBackground = IS_BG;
  if (IS_BG) {
    setInterval(checkDaily, 60000);
    ready().then(function () { setTimeout(checkDaily, 3000); });
  }
  global.MB.quick = function (text) {
    return ready().then(function () {
      location.hash = "q=" + encodeURIComponent(text);
      return handleQuickHash();
    });
  };
})(window);
