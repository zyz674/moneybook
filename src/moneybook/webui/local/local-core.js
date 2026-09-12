/* 记账本 · 纯前端版核心逻辑
 * 把服务端的解析、分类、统计全部搬到浏览器里跑，数据存在 IndexedDB，不上传任何地方。
 * 与服务端版本共用同一份界面代码（app.js / style.css / index.html）。
 */
(function (global) {
  "use strict";

  var MB = {};

  /* ---------------- 基础工具（对应服务端 util.py） ---------------- */

  function pad2(n) {
    n = parseInt(n, 10);
    if (isNaN(n)) { n = 0; }
    return (n < 10 ? "0" : "") + n;
  }
  function fmtTs(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " +
           pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
  }
  function nowStr() { return fmtTs(new Date()); }
  function dayOf(ts) { return String(ts || "").slice(0, 10); }
  function monthOf(ts) { return String(ts || "").slice(0, 7); }
  function todayStr(offset) { var d = new Date(); d.setDate(d.getDate() + (offset || 0)); return dayOf(fmtTs(d)); }

  function parseCents(value) {
    if (value === null || value === undefined || value === "") { return null; }
    if (typeof value === "number") { return Math.round(value * 100); }
    var s = String(value).trim().replace(/人民币|RMB|rmb/g, "");
    s = s.replace(/[￥¥元,，\s＋]/g, function (ch) { return ch === "＋" ? "+" : ""; })
         .replace(/[－—−]/g, "-").replace(/\u00a0/g, "");
    var neg = false;
    if (s.charAt(0) === "-") { neg = true; s = s.slice(1); }
    else if (s.charAt(0) === "+") { s = s.slice(1); }
    var m = s.match(/^\d+(?:\.\d+)?$/);
    if (!m) {
      m = s.match(/\d+(?:\.\d+)?/);
      if (!m) { return null; }
      s = m[0];
    }
    var cents = Math.round(parseFloat(s) * 100);
    return neg ? -cents : cents;
  }

  function fmtCents(cents, symbol) {
    var n = Number(cents || 0);
    var sign = n < 0 ? "-" : "";
    n = Math.abs(n);
    var body = Math.floor(n / 100).toLocaleString("zh-CN") + "." + pad2(n % 100);
    return sign + (symbol ? "¥" : "") + body;
  }

  var PUNCT = /[\s\u3000·•,，。.、;；:：!！?？"'“”‘’()（）\[\]【】<>《》_\-—/\\|]+/g;
  function normText(text) {
    if (text === null || text === undefined) { return ""; }
    var s = String(text);
    try { s = s.normalize("NFKC"); } catch (e) { /* 老浏览器忽略 */ }
    return s.replace(PUNCT, "").toLowerCase();
  }

  /* cyrb53：稳定的字符串哈希，够用且同步 */
  function hash() {
    var parts = Array.prototype.slice.call(arguments);
    var str = parts.map(function (p) { return p === null || p === undefined ? "" : String(p); }).join("\u001f");
    var h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (var i = 0; i < str.length; i++) {
      var ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
  }

  function parseDateTimeLoose(text, defaultYear) {
    if (!text) { return null; }
    if (text instanceof Date) { return fmtTs(text); }
    var s = String(text).normalize ? String(text).normalize("NFKC").trim() : String(text).trim();
    s = s.replace(/年|月/g, "-").replace(/日|时/g, " ").replace(/分|秒/g, "").replace(/\s+/g, " ").trim().replace(/[-\s:]+$/, "");
    var m = s.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
    if (m) {
      return m[1] + "-" + pad2(m[2]) + "-" + pad2(m[3]) + " " + pad2(m[4] || 0) + ":" + pad2(m[5] || 0) + ":" + pad2(m[6] || 0);
    }
    m = s.match(/^(\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
    if (m) {
      var y = defaultYear || new Date().getFullYear();
      return y + "-" + pad2(m[1]) + "-" + pad2(m[2]) + " " + pad2(m[3] || 0) + ":" + pad2(m[4] || 0) + ":" + pad2(m[5] || 0);
    }
    m = s.match(/^(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?$/);
    if (m) {
      return m[1] + "-" + m[2] + "-" + m[3] + " " + (m[4] || "00") + ":" + (m[5] || "00") + ":" + (m[6] || "00");
    }
    return null;
  }

  function dateRange(startDay, endDay) {
    var out = [], a = new Date(startDay + "T00:00:00"), b = new Date(endDay + "T00:00:00");
    while (a <= b) { out.push(dayOf(fmtTs(a))); a.setDate(a.getDate() + 1); }
    return out;
  }
  function monthDays(month) {
    var y = parseInt(month.slice(0, 4), 10), m = parseInt(month.slice(5, 7), 10);
    var last = new Date(y, m, 0);
    return [month + "-01", dayOf(fmtTs(last))];
  }

  /* ---------------- CSV / 账单解析（对应 importers/base.py） ---------------- */

  var HEADER_ALIASES = {
    ts: ["交易时间", "付款时间", "交易创建时间", "创建时间", "交易日期", "记账日期", "时间", "日期"],
    category_raw: ["交易分类", "交易类型", "类型", "分类", "交易场景"],
    counterparty: ["交易对方", "对方", "商户名称", "商户", "交易对象", "收款方", "付款方", "对方名称"],
    item: ["商品说明", "商品名称", "商品", "交易说明", "摘要", "说明", "用途"],
    direction: ["收/支", "收支", "资金方向", "借贷标志", "收付标志"],
    amount: ["金额(元)", "金额（元）", "金额", "交易金额", "发生额", "发生金额", "支出金额"],
    method: ["收/付款方式", "收付款方式", "支付方式", "付款方式", "交易方式"],
    status: ["交易状态", "当前状态", "状态", "交易结果"],
    ext_id: ["交易订单号", "交易单号", "交易号", "订单号", "流水号"],
    merchant_id: ["商家订单号", "商户单号", "商户订单号"],
    note: ["备注"],
    account: ["对方账号", "账号", "对方账户"]
  };
  var TS_FALLBACKS = ["付款时间", "交易创建时间", "创建时间", "最近修改时间"];

  function decodeBytes(buf) {
    var bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    var utf8 = new TextDecoder("utf-8").decode(bytes);
    if (utf8.indexOf("\ufffd") < 0) { return utf8; }
    try { return new TextDecoder("gbk").decode(bytes); } catch (e) { return utf8; }
  }

  function parseCSV(text, delim) {
    var rows = [], row = [], field = "", inQuotes = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      if (inQuotes) {
        if (ch === '"') {
          if (text.charAt(i + 1) === '"') { field += '"'; i++; } else { inQuotes = false; }
        } else { field += ch; }
      } else if (ch === '"') { inQuotes = true; }
      else if (ch === delim) { row.push(field.trim()); field = ""; }
      else if (ch === "\n") { row.push(field.trim()); rows.push(row); row = []; field = ""; }
      else if (ch !== "\r") { field += ch; }
    }
    if (field.length || row.length) { row.push(field.trim()); rows.push(row); }
    return rows.filter(function (r) { return r.some(function (c) { return c !== ""; }); });
  }

  function normHeader(cell) { return String(cell || "").replace(/[\s\u3000()（）:：]/g, "").toLowerCase(); }

  function findHeader(rows) {
    for (var idx = 0; idx < Math.min(rows.length, 40); idx++) {
      var row = rows[idx];
      if (!row || row.filter(function (c) { return c; }).length < 3) { continue; }
      var mapping = {}, used = {};
      var normed = row.map(normHeader);
      Object.keys(HEADER_ALIASES).forEach(function (field) {
        var aliases = HEADER_ALIASES[field];
        for (var a = 0; a < aliases.length && mapping[field] === undefined; a++) {
          var na = normHeader(aliases[a]);
          for (var i = 0; i < normed.length; i++) {
            if (used[i] || !normed[i]) { continue; }
            if (normed[i] === na || normed[i].indexOf(na) === 0 || normed[i].indexOf(na) >= 0) {
              mapping[field] = i; used[i] = true; break;
            }
          }
        }
      });
      if (mapping.amount !== undefined && (mapping.ts !== undefined || mapping.counterparty !== undefined)) {
        return { index: idx, mapping: mapping };
      }
    }
    return { index: -1, mapping: {} };
  }

  function cell(row, mapping, field) {
    var i = mapping[field];
    if (i === undefined || i >= row.length) { return ""; }
    var v = row[i];
    if (v === "/" || v === "-" || v === "--" || v === "nan" || v === "None" || v === "null") { return ""; }
    return v || "";
  }

  function guessDirection(text, cents) {
    var t = normText(text);
    if (!t) { return cents !== null && cents !== undefined ? (cents > 0 ? "in" : "out") : "out"; }
    if (t.indexOf("不计收支") >= 0 || t.indexOf("不计入") >= 0) { return "neutral"; }
    if (t.charAt(0) === "支" || t === "out" || t === "借" || t === "d" || t === "debit") { return "out"; }
    if (t.charAt(0) === "收" || t === "in" || t === "贷" || t === "c" || t === "credit") { return "in"; }
    if (t.indexOf("支出") >= 0 || t.indexOf("付款") >= 0 || t.indexOf("消费") >= 0) { return "out"; }
    if (t.indexOf("收入") >= 0 || t.indexOf("收款") >= 0 || t.indexOf("到账") >= 0) { return "in"; }
    return cents !== null && cents !== undefined ? (cents > 0 ? "in" : "out") : "out";
  }

  function normalizeStatus(status, direction) {
    var s = normText(status);
    if (!s) { return direction; }
    var bad = ["已关闭", "交易关闭", "交易失败", "支付失败", "失败", "已撤销", "已取消", "已作废"];
    for (var i = 0; i < bad.length; i++) { if (s.indexOf(bad[i]) >= 0) { return "neutral"; } }
    if (direction === "out") {
      var refund = ["全额退款", "已全额退款", "已退回", "已退还"];
      for (var j = 0; j < refund.length; j++) { if (s.indexOf(refund[j]) >= 0) { return "neutral"; } }
    }
    return direction;
  }

  function looksLikeFooter(row) {
    if (!row || !row.length) { return true; }
    var joined = row.join("");
    if (/^[-=—\s]+$/.test(joined)) { return true; }
    if (row.filter(function (c) { return c; }).length <= 1) { return true; }
    if (row.length <= 2 && /(共\s*\d+\s*笔|以上|合计|小计|说明|提示|注意)/.test(joined)) { return true; }
    return false;
  }

  function detectSource(text, filename) {
    var name = String(filename || "").toLowerCase(), head = text.slice(0, 3000);
    if (name.indexOf("alipay") >= 0 || name.indexOf("支付宝") >= 0) { return "alipay"; }
    if (name.indexOf("wechat") >= 0 || name.indexOf("微信") >= 0) { return "wechat"; }
    if (head.indexOf("支付宝") >= 0 || (text.slice(0, 5000).indexOf("交易订单号") >= 0 && text.slice(0, 5000).indexOf("收/付款方式") >= 0)) { return "alipay"; }
    if (head.indexOf("微信支付账单") >= 0 || head.indexOf("微信昵称") >= 0 ||
        (text.slice(0, 5000).indexOf("交易单号") >= 0 && text.slice(0, 5000).indexOf("当前状态") >= 0)) { return "wechat"; }
    if (head.indexOf("银行") >= 0 || head.indexOf("卡号") >= 0) { return "bank"; }
    return "bank";
  }

  function parseStatement(text, filename) {
    var source = detectSource(text, filename);
    var sample = text.split("\n").slice(0, 40).join("\n");
    var delim = sample.split("\t").length > sample.split(",").length ? "\t" : ",";
    var rows = parseCSV(text.replace(/\r\n/g, "\n").replace(/\r/g, "\n"), delim);
    var hdr = findHeader(rows);
    if (hdr.index < 0) { return { source: source, records: [], mapping: {} }; }
    var records = [];
    for (var i = hdr.index + 1; i < rows.length; i++) {
      var row = rows[i];
      if (looksLikeFooter(row)) { continue; }
      var tsRaw = cell(row, hdr.mapping, "ts");
      if (!tsRaw) {
        for (var f = 0; f < TS_FALLBACKS.length && !tsRaw; f++) { tsRaw = cell(row, hdr.mapping, TS_FALLBACKS[f]); }
      }
      var cents = parseCents(cell(row, hdr.mapping, "amount"));
      if (cents === null) { continue; }
      var ts = parseDateTimeLoose(tsRaw);
      if (!ts) { continue; }
      var direction = normalizeStatus(cell(row, hdr.mapping, "status"),
                                      guessDirection(cell(row, hdr.mapping, "direction"), cents));
      records.push({
        source: source, origin: "import",
        ext_id: cell(row, hdr.mapping, "ext_id") || cell(row, hdr.mapping, "merchant_id"),
        ts: ts, amount_cents: Math.abs(cents), direction: direction,
        counterparty: cell(row, hdr.mapping, "counterparty"), item: cell(row, hdr.mapping, "item"),
        method: cell(row, hdr.mapping, "method"), status: cell(row, hdr.mapping, "status"),
        category_raw: cell(row, hdr.mapping, "category_raw"), account: cell(row, hdr.mapping, "account"),
        note: cell(row, hdr.mapping, "note"), raw: JSON.stringify(row).slice(0, 600)
      });
    }
    var meta = {};
    var head = text.split("\n").slice(0, 15).join("\n");
    var acc = head.match(/(?:账号|微信昵称|支付宝账号|账户)\s*[:：]\s*\[?([^\]\n]+)\]?/);
    if (acc) { meta.account = acc[1].trim(); }
    return { source: source, records: records, mapping: hdr.mapping, meta: meta };
  }

  /* ---------------- 通知 / 短信文本解析（对应 parsers.py） ---------------- */

  var OUT_WORDS = ["支出", "消费", "付款成功", "支付成功", "已支付", "已付款", "扣款", "扣费", "已扣",
                   "支付了", "支付", "扫码付", "付款", "消费支出", "代扣", "缴费成功", "支出人民币"];
  var IN_WORDS = ["收入", "收款成功", "已收款", "收款到账", "到账", "入账", "退款", "已退款", "退回",
                  "收到", "转入", "红包到", "收到红包", "收款", "入账人民币", "工资", "发工资", "奖金",
                  "报销", "进账", "到帐"];
  var NEUTRAL_OUT = ["转账", "还款", "信用卡还款", "花呗还款", "借呗还款", "余额宝", "零钱通", "理财",
                     "基金", "定期", "提现", "零钱充值", "充值到零钱", "亲情卡", "亲密付", "备用金",
                     "自动充值", "转到银行卡"];
  var SELF_TRANSFER_IN = ["提现", "零钱提现", "余额宝", "零钱通", "理财", "基金", "定期", "赎回", "充值到零钱"];
  var NOISE_WORDS = ["验证码", "登录", "优惠券", "活动", "邀请", "更新", "广告", "推荐", "签到", "积分",
                     "账单已出", "还款日", "额度提升", "安全提醒", "点击查看", "下载"];
  var APP_NAMES = ["微信支付", "微信", "支付宝", "云闪付", "数字人民币"];
  var AMOUNT_PATTERNS = [
    /[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/,
    /(?:金额|人民币|消费|支付|付款|收款|到账|收入|支出|扣款|扣费|退款|共计|合计)\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元/,
    /([0-9][0-9,]*\.[0-9]{2})\s*元/,
    /([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元/
  ];
  var CP_PATTERNS = [
    /(?:向|给)\s*([^\s,，。;；:：]{2,24}?)\s*(?:支付|付款|转账|付款成功)/,
    /在\s*([^\s,，。;；:：]{2,24}?)\s*(?:消费|支付|购买|付款)/,
    /(?:商户|商家|收款方|付款方|交易对方|对方)\s*[:：]?\s*([^\s,，。;；]{2,24})/,
    /微信支付\s*[-—·]\s*([^\s\-—·]{2,24})/,
    /支付宝\s*[-—]\s*([^\s]{2,24})/,
    /【([^】]{2,24})】/
  ];
  var BANK_NAMES = ["工商", "建设", "农业", "中国银行", "招商", "交通银行", "邮储", "邮政", "民生",
                    "光大", "中信", "浦发", "兴业", "平安银行", "华夏", "广发", "北京银行", "农商"];

  function detectNotifySource(text, pkg) {
    pkg = String(pkg || "").toLowerCase();
    if (pkg.indexOf("alipay") >= 0 || pkg.indexOf("eg.android") >= 0) { return "alipay"; }
    if (pkg.indexOf("tencent.mm") >= 0 || pkg.indexOf("wechat") >= 0 || pkg.indexOf("micromsg") >= 0) { return "wechat"; }
    if (text.indexOf("支付宝") >= 0) { return "alipay"; }
    if (text.indexOf("微信") >= 0 || text.indexOf("零钱") >= 0) { return "wechat"; }
    if (BANK_NAMES.some(function (n) { return text.indexOf(n) >= 0; })) { return "bank"; }
    if (/银行|储蓄卡|信用卡|借记卡|尾号|卡号|账户|余额|人民币/.test(text)) { return "bank"; }
    return "other";
  }

  function extractAmount(text) {
    for (var i = 0; i < AMOUNT_PATTERNS.length; i++) {
      var m = text.match(AMOUNT_PATTERNS[i]);
      if (m) {
        var cents = parseCents(m[1]);
        if (cents) { return Math.abs(cents); }
      }
    }
    return null;
  }

  function extractDirection(text) {
    var core = text;
    APP_NAMES.forEach(function (a) { core = core.split(a).join(""); });
    var hasOut = OUT_WORDS.some(function (w) { return core.indexOf(w) >= 0; });
    var hasIn = IN_WORDS.some(function (w) { return core.indexOf(w) >= 0; });
    var direction = null;
    if (hasIn && !hasOut) { direction = "in"; }
    else if (hasOut && !hasIn) { direction = "out"; }
    else if (hasIn && hasOut) {
      var firstIn = core.length, firstOut = core.length;
      IN_WORDS.forEach(function (w) { var i = core.indexOf(w); if (i >= 0 && i < firstIn) { firstIn = i; } });
      OUT_WORDS.forEach(function (w) { var i = core.indexOf(w); if (i >= 0 && i < firstOut) { firstOut = i; } });
      direction = firstIn < firstOut ? "in" : "out";
    } else if (/[¥￥]/.test(text)) { direction = "out"; }
    if (direction === "out" && NEUTRAL_OUT.some(function (w) { return text.indexOf(w) >= 0; })) { return "neutral"; }
    if (direction === "in" && SELF_TRANSFER_IN.some(function (w) { return text.indexOf(w) >= 0; })) { return "neutral"; }
    return direction;
  }

  function extractCounterparty(text) {
    for (var i = 0; i < CP_PATTERNS.length; i++) {
      var m = text.match(CP_PATTERNS[i]);
      if (m) {
        var name = m[1].replace(/^[\s　\-—·:：]+|[\s　\-—·:：]+$/g, "");
        if (name && !/^\d+$/.test(name) && name.length >= 2) { return name; }
      }
    }
    return "";
  }

  function guessMerchant(text) {
    var s = text;
    AMOUNT_PATTERNS.forEach(function (p) { s = s.replace(p, " "); });
    s = s.replace(/[0-9]+(?:\.[0-9]{1,2})?/, " ");
    ["微信支付", "支付宝", "已支付", "支付成功", "付款成功", "支付", "付款", "支出", "消费", "扫码",
     "收款", "到账", "收入", "金额", "人民币", "花了", "买了", "元", "块", "钱"].forEach(function (w) {
      s = s.split(w).join(" ");
    });
    s = s.replace(/[^0-9A-Za-z\u4e00-\u9fa5]+/g, " ").trim();
    return (s.length >= 2 && s.length <= 24) ? s : "";
  }

  function parseNotification(text, opts) {
    opts = opts || {};
    if (!text || !String(text).trim()) { return { record: null, reason: "空文本" }; }
    text = String(text).normalize ? String(text).normalize("NFKC") : String(text);
    if (NOISE_WORDS.some(function (w) { return text.indexOf(w) >= 0; })) {
      return { record: null, reason: "非交易通知（含噪音关键词）" };
    }
    var cents = extractAmount(text);
    if (!cents && opts.loose) {
      var m = text.match(/[0-9]+(?:\.[0-9]{1,2})?/);
      if (m) { cents = Math.abs(parseCents(m[0]) || 0); }
    }
    if (!cents) { return { record: null, reason: "未找到金额" }; }
    var direction = extractDirection(text);
    if (!direction) {
      if (!opts.loose) { return { record: null, reason: "无法判断收支方向" }; }
      direction = "out";
    }
    var src = opts.source || detectNotifySource(text, opts.package);
    if (src === "other" && !opts.loose && text.indexOf("微信") < 0 && text.indexOf("支付宝") < 0) {
      return { record: null, reason: "无法识别来源" };
    }
    var cp = extractCounterparty(text);
    if (["支付宝", "微信", "微信支付", "支付宝支付", "云闪付"].indexOf(cp) >= 0) { cp = ""; }
    if (!cp && opts.loose) { cp = guessMerchant(text); }
    var method = "";
    ["零钱", "余额宝", "花呗", "银行卡", "信用卡", "储蓄卡", "云闪付", "数字人民币", "零钱通"].forEach(function (kw) {
      if (!method && text.indexOf(kw) >= 0) { method = kw; }
    });
    var label = { alipay: "支付宝通知", wechat: "微信支付通知", bank: "银行短信" }[src] || "流水通知";
    var acc = text.match(/(?:尾号|卡号|账号|账户)\s*([0-9]{3,6})/);
    return {
      record: {
        source: src, origin: "notify", ext_id: "",
        ts: opts.ts || nowStr(), amount_cents: cents, direction: direction,
        counterparty: cp, item: cp || label, method: method, status: "",
        category_raw: label, account: acc ? acc[1] : "", raw: text.slice(0, 600)
      }, reason: "ok"
    };
  }

  /* ---------------- 规则引擎（对应 categorize.py） ---------------- */

  var NEUTRAL_WORDS = ["转账", "信用卡还款", "还款", "还花呗", "花呗还款", "借呗还款", "备用金",
                       "余额宝", "零钱通", "余利宝", "理财", "基金", "黄金", "定期", "笔笔攒",
                       "提现", "零钱充值", "零钱提现", "充值到零钱", "亲情卡", "亲密付", "自动充值"];
  var FIELD_TO_CTX = { any: null, peer: "peer", item: "item", category: "category_raw", method: "method" };

  function shouldNeutralize(record) {
    var blob = normText((record.category_raw || "") + " " + (record.counterparty || "") + " " + (record.item || ""));
    if (NEUTRAL_WORDS.some(function (w) { return blob.indexOf(normText(w)) >= 0; })) { return true; }
    var cat = normText(record.category_raw || "");
    return ["转账", "信用卡还款", "余额宝", "理财", "零钱提现", "零钱充值"].indexOf(cat) >= 0;
  }

  function inTimeWindow(hhmm, start, end) {
    if (!start || !end || !hhmm) { return true; }
    if (start <= end) { return hhmm >= start && hhmm < end; }
    return hhmm >= start || hhmm < end;
  }

  function ruleContext(record) {
    var ts = String(record.ts || "");
    return {
      peer: normText(record.counterparty), item: normText(record.item),
      category_raw: normText(record.category_raw), method: normText(record.method),
      raw: normText(String(record.raw || "").slice(0, 300)),
      direction: record.direction || "", source: record.source || "",
      cents: Math.abs(parseInt(record.amount_cents || 0, 10)),
      hhmm: ts.length >= 16 ? ts.slice(11, 16) : ""
    };
  }

  function ruleMatches(rule, ctx) {
    if (rule.direction && rule.direction !== ctx.direction) { return false; }
    if (rule.source && rule.source !== ctx.source) { return false; }
    if (rule.min_cents !== null && rule.min_cents !== undefined && ctx.cents < rule.min_cents) { return false; }
    if (rule.max_cents !== null && rule.max_cents !== undefined && ctx.cents > rule.max_cents) { return false; }
    if (rule.method && ctx.method.indexOf(normText(rule.method)) < 0) { return false; }
    if (rule.time_from && !inTimeWindow(ctx.hhmm, rule.time_from, rule.time_to || "23:59")) { return false; }
    var key = FIELD_TO_CTX[rule.field || "any"];
    var hay = key === null
      ? [ctx.peer, ctx.item, ctx.category_raw, ctx.raw].join(" ")
      : (ctx[key] || "");
    var keywords = String(rule.keyword || "").split(/[,，、;；]/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    if (!keywords.length) {
      return !!(rule.min_cents !== null && rule.min_cents !== undefined ||
                rule.max_cents !== null && rule.max_cents !== undefined ||
                rule.method || rule.time_from);
    }
    for (var i = 0; i < keywords.length; i++) {
      var nk = normText(keywords[i]);
      if (!nk) { continue; }
      if (rule.full_match) { if (hay === nk) { return true; } }
      else if (hay.indexOf(nk) >= 0) { return true; }
    }
    return false;
  }

  function evaluate(rules, record) {
    var ctx = ruleContext(record), out = { action: "", category: "", sub_category: "", tags: [],
                                           rule_id: null, rule: null };
    for (var i = 0; i < rules.length; i++) {
      var rule = rules[i];
      if (!ruleMatches(rule, ctx)) { continue; }
      var action = rule.action || "categorize";
      if (action === "tag") {
        String(rule.tags || "").split(/[,，、;；]/).forEach(function (t) {
          t = t.trim();
          if (t && out.tags.indexOf(t) < 0) { out.tags.push(t); }
        });
        continue;
      }
      if (out.action) { continue; }
      out.action = action;
      out.category = rule.category || "";
      out.sub_category = rule.sub_category || "";
      out.rule_id = rule.id;
      out.rule = rule;
      if (action === "ignore") { break; }
    }
    return out;
  }

  function applyRules(rules, record) {
    var res = evaluate(rules, record);
    if (res.action === "ignore") { return res; }
    if (res.action === "neutral") { record.direction = "neutral"; }
    if ((res.action === "categorize" || res.action === "neutral") && res.category) {
      record.category = res.category;
      record.sub_category = res.sub_category;
      if (res.rule_id !== null) { res.rule.hits = (res.rule.hits || 0) + 1; MB.dirty.rules = true; }
    }
    if (res.tags.length) {
      var old = String(record.tags || "").split(",").filter(function (t) { return t; });
      res.tags.forEach(function (t) { if (old.indexOf(t) < 0) { old.push(t); } });
      record.tags = old.join(",");
    }
    return res;
  }

  function classify(rules, record) {
    var res = evaluate(rules, record);
    if ((res.action === "categorize" || res.action === "neutral") && res.category) {
      return [res.category, res.sub_category];
    }
    if (record.direction === "neutral") { return ["金融", "资金流转"]; }
    if (record.direction === "in") { return ["收入", "其他收入"]; }
    return ["未分类", ""];
  }

  MB.util = { pad2: pad2, fmtTs: fmtTs, nowStr: nowStr, todayStr: todayStr, dayOf: dayOf, monthOf: monthOf,
              parseCents: parseCents, fmtCents: fmtCents, normText: normText, hash: hash,
              parseDateTimeLoose: parseDateTimeLoose, dateRange: dateRange, monthDays: monthDays,
              decodeBytes: decodeBytes };
  MB.parseStatement = parseStatement;
  MB.parseNotification = parseNotification;
  MB.rules = { evaluate: evaluate, applyRules: applyRules, classify: classify,
               shouldNeutralize: shouldNeutralize, ruleMatches: ruleMatches,
               ruleContext: ruleContext, inTimeWindow: inTimeWindow };
  global.MB = MB;
})(window);
