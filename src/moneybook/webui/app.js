/* 记账本 · 手机端界面
   结构与文案原则：这是一张账页，不是卡片堆。金额用窄体数字，分类用印章，
   朱砂只表示支出与印章，青瓷只表示收入；界面文案说清楚「发生了什么、怎么办」。 */
(function () {
  "use strict";

  var LOCAL = !!window.MONEYBOOK_LOCAL;     // 纯前端版（GitHub Pages）：没有服务端
  var TOKEN_KEY = "mb_token";
  var CATS = ["餐饮", "交通", "购物", "日用百货", "居住", "通讯", "娱乐", "医疗健康",
              "教育学习", "人情往来", "金融", "收入", "其他", "未分类"];
  var SOURCE = { alipay: "支付宝", wechat: "微信", bank: "银行", manual: "手工", other: "其他" };
  var WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  var state = { view: "home", month: "", dir: "", uncat: false, src: "", q: "", days: [] };

  function $(s) { return document.querySelector(s); }
  function $$(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function curMonth() { var d = new Date(); return d.getFullYear() + "-" + pad2(d.getMonth() + 1); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  }
  function money(cents, sign) {
    var n = Number(cents || 0), s = n < 0 ? "-" : (sign ? "+" : "");
    n = Math.abs(n);
    return s + Math.floor(n / 100).toLocaleString("zh-CN") + "." + pad2(n % 100);
  }
  function totalHTML(cents) {
    var n = Math.abs(Number(cents || 0));
    return '<span class="cur">¥</span>' + Math.floor(n / 100).toLocaleString("zh-CN") +
           '<span class="dec">.' + pad2(n % 100) + "</span>";
  }
  function dayNum(day) { return String(day || "").slice(8, 10).replace(/^0/, ""); }
  function weekShort(day) {
    var p = String(day || "").split("-");
    if (p.length < 3) { return ""; }
    return WEEK[new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])).getDay()].slice(1);
  }
  function dayLabel(day) {
    var p = String(day || "").split("-");
    if (p.length < 3) { return day; }
    var w = WEEK[new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])).getDay()];
    return Number(p[1]) + "月" + Number(p[2]) + "日 " + w;
  }
  function sealText(category) {
    var c = String(category || "").trim();
    if (!c || c === "未分类") { return "待分"; }
    return c.slice(0, 2);
  }
  function monthCN(month) {
    var p = String(month || "").split("-");
    if (p.length < 2) { return month; }
    var n = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"][Number(p[1])] || p[1];
    return (p[0] === String(new Date().getFullYear()) ? "" : p[0] + "年") + n + "月";
  }
  function toast(msg, ms) {
    var t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.classList.remove("show"); }, ms || 2400);
  }
  function say(msg) {
    var el = $("#topSub");
    if (!msg) { el.classList.add("hidden"); return; }
    el.textContent = msg;
    el.classList.remove("hidden");
  }

  /* ---------------- 网络 ---------------- */
  function token() { return window.localStorage.getItem(TOKEN_KEY) || ""; }
  function setToken(t) {
    if (t) { window.localStorage.setItem(TOKEN_KEY, t); } else { window.localStorage.removeItem(TOKEN_KEY); }
  }
  function api(path, opt) {
    opt = opt || {};
    var url = path + (path.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(token());
    var init = { method: opt.method || (opt.json ? "POST" : "GET"), headers: {} };
    if (token()) { init.headers["X-Token"] = token(); }
    if (opt.json) { init.headers["Content-Type"] = "application/json; charset=utf-8"; init.body = JSON.stringify(opt.json); }
    return fetch(url, init).then(function (r) {
      if (r.status === 401) { askToken(); throw new Error("需要访问口令"); }
      var ct = r.headers.get("content-type") || "";
      if (ct.indexOf("json") >= 0) { return r.json(); }
      return r.text().then(function (t) { return { ok: true, text: t }; });
    });
  }

  /* ---------------- 弹层 ---------------- */
  function openSheet(html) {
    $("#sheetBody").innerHTML = html;
    $("#mask").classList.remove("hidden");
    requestAnimationFrame(function () { $("#mask").classList.add("show"); $("#sheet").classList.add("show"); });
  }
  function closeSheet() {
    $("#mask").classList.remove("show");
    $("#sheet").classList.remove("show");
    setTimeout(function () { $("#mask").classList.add("hidden"); }, 220);
  }
  function askToken() {
    openSheet('<h3>需要访问口令</h3>' +
      '<p class="sheet-note">服务器开了访问保护。口令在启动窗口里，或 config.json 的 access_token 字段。</p>' +
      '<label class="field"><span>访问口令</span><input type="password" id="tkInput" value="' + esc(token()) + '"></label>' +
      '<div class="btn-row"><button class="btn" id="tkSave">保存并重新连接</button></div>');
    $("#tkSave").onclick = function () { setToken($("#tkInput").value.trim()); closeSheet(); boot(); };
  }

  /* ---------------- 流水行 ---------------- */
  function txRow(t) {
    var dir = t.direction;
    var cls = dir === "in" ? "in" : (dir === "neutral" ? "flat" : "out");
    var seal = dir === "in" ? "seal celadon" : (dir === "neutral" ? "seal flat" : "seal");
    var name = t.counterparty || t.item || t.category || "未命名";
    var sub = [];
    if (t.note) { sub.push(t.note); }
    if (t.item && t.item !== name) { sub.push(t.item); }
    sub.push((SOURCE[t.source] || t.source) + " " + String(t.ts).slice(11, 16));
    return '<button class="row" data-id="' + t.id + '">' +
      '<span class="when"><b>' + dayNum(t.day) + "</b><i>" + weekShort(t.day) + "</i></span>" +
      '<span class="who"><b>' + esc(name) + "</b><span>" + esc(sub.join("　")) + "</span></span>" +
      '<span class="' + seal + '">' + esc(sealText(t.category)) + "</span>" +
      '<span class="amt num ' + cls + '">' + money(t.amount_cents) + "</span></button>";
  }
  function emptyBox(title, how) {
    return '<p class="empty"><b>' + title + "</b><br>" + how + "</p>";
  }
  function renderRows(items, group) {
    if (!items || !items.length) {
      return emptyBox("这段时间没有流水。", LOCAL
        ? "点右上角「导入账单」，选支付宝或微信导出的 CSV（在「我的 → 数据导入」里也能上传）。" +
          "也可以点右下角「＋」自己记一笔。"
        : "把支付宝或微信导出的账单放进 <code>data/inbox</code>，" +
          "或在设置里直接上传，点「同步流水」就会自动导入。也可以点右下角「＋」自己记一笔。");
    }
    if (!group) { return '<div class="rows">' + items.map(txRow).join("") + "</div>"; }
    var out = [], lastDay = null, dayOut = 0, buf = [];
    function flush() {
      if (!buf.length) { return; }
      out.push('<div class="dayrule"><span class="d">' + esc(dayLabel(lastDay)) + "</span>" +
        '<span class="sum">支出 ' + money(dayOut) + "</span></div>");
      out.push('<div class="rows">' + buf.join("") + "</div>");
      buf = []; dayOut = 0;
    }
    items.forEach(function (t) {
      if (t.day !== lastDay) { flush(); lastDay = t.day; }
      if (t.direction === "out") { dayOut += t.amount_cents; }
      buf.push(txRow(t));
    });
    flush();
    return out.join("");
  }

  /* ---------------- 走势（每日支出竖笔 + 累计墨线） ---------------- */
  function drawTrend(svg, days, opts) {
    opts = opts || {};
    var W = 300, H = opts.height || 58, base = H - 4, top = 6;
    var n = Math.max(1, days.length);
    var maxDay = 1, cum = [], run = 0;
    days.forEach(function (d) { maxDay = Math.max(maxDay, d.out); run += d.out; cum.push(run); });
    var maxCum = Math.max(1, run);
    var slot = W / n;
    var bw = Math.max(0.8, Math.min(3.2, slot * 0.4));
    var parts = [];
    days.forEach(function (d, i) {
      if (d.out <= 0) { return; }
      var h = Math.max(1.2, d.out / maxDay * (base - top));
      parts.push('<rect x="' + (i * slot + (slot - bw) / 2).toFixed(2) + '" y="' + (base - h).toFixed(2) +
        '" width="' + bw.toFixed(2) + '" height="' + h.toFixed(2) + '" fill="var(--cinnabar)" opacity="' +
        (opts.strokeOpacity || 0.42) + '"/>');
    });
    var pts = [], lastX = 0, lastY = base;
    cum.forEach(function (v, i) {
      var x = (i + 0.5) * slot, y = base - (v / maxCum) * (base - top);
      pts.push(x.toFixed(2) + "," + y.toFixed(2));
      lastX = x; lastY = y;
    });
    if (opts.line !== false) {
      parts.push('<polyline points="' + pts.join(" ") + '" fill="none" stroke="var(--ink)" stroke-width="1.4" ' +
        'stroke-linejoin="round" stroke-linecap="round"/>');
      parts.push('<circle cx="' + lastX.toFixed(2) + '" cy="' + lastY.toFixed(2) + '" r="2.2" fill="var(--ink)"/>');
    }
    parts.push('<line x1="0" y1="' + base + '" x2="' + W + '" y2="' + base + '" stroke="var(--rule)" stroke-width="1"/>');
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.innerHTML = parts.join("");
  }

  /* ---------------- 总账 ---------------- */
  function loadHome() {
    return api("/api/overview").then(function (d) {
      if (!d.ok) { return; }
      say("");
      var m = d.month, t = d.today;
      $("#homeCaption").textContent = monthCN(m.month) + "合计支出";
      $("#homeCaptionNote").textContent = "今日支出 " + money(t.out_cents);
      $("#homeTotal").innerHTML = totalHTML(m.out_cents);
      var left = (m.days_in_month | 0) - (m.day_of_month | 0) + 1;
      $("#homeSub").textContent = "日均支出 " + money(Math.round(m.out_cents / Math.max(1, m.day_of_month))) +
        "　" + monthCN(m.month) + "还剩 " + left + " 天";
      $("#homeOut").textContent = money(m.out_cents);
      $("#homeIn").textContent = money(m.in_cents);
      $("#homeNet").textContent = (m.net >= 0 ? "+" : "−") + money(Math.abs(m.net));
      $("#homeNet").className = "num " + (m.net >= 0 ? "in" : "out");
      $("#homeOutCnt").textContent = m.count + " 笔";
      $("#homeInCnt").textContent = m.in_cnt + " 笔";
      $("#homeNetNote").textContent = "";

      var bl = $("#budgetLine");
      if (m.budget) {
        var pc = m.budget_percent;
        bl.className = "budget-line" + (pc >= 100 ? " over" : (pc >= 80 ? " warn" : ""));
        $("#budgetBar").style.width = Math.min(100, pc) + "%";
        $("#budgetLabel").textContent = pc >= 100 ? "已超预算" : "预算剩余";
        $("#budgetText").textContent = (pc >= 100 ? "超 " + money(Math.abs(m.budget_left)) : money(m.budget_left)) +
          "　" + pc.toFixed(0) + "%";
      } else {
        bl.className = "budget-line";
        $("#budgetBar").style.width = "0%";
        $("#budgetLabel").textContent = "预算";
        $("#budgetText").textContent = "在设置里填一个月度预算";
      }

      var sch = d.scheduler || {};
      $("#syncLine").textContent = LOCAL
        ? ("上次导入 " + (d.last_sync ? d.last_sync.slice(5, 16) : "还没导入过") +
           "　数据存在这台设备的浏览器里，下次账单 " + String(sch.next_daily_report || "-").slice(5) + "。")
        : ("上次同步 " + (d.last_sync ? d.last_sync.slice(5, 16) : "还没同步过") +
           "，每 " + sch.interval_hours + " 小时自动同步一次（下次 " + String(sch.next_sync || "-").slice(5) +
           "），下次账单 " + String(sch.next_daily_report || "-").slice(5) + "。");

      var pending = d.counts.pending || 0;
      $("#pendingLine").classList.toggle("hidden", !pending);
      if (pending) {
        $("#pendingText").textContent = "有 " + pending + " 条通知没认出金额或商户。" +
          "点「去补录」补上就能入账，顺手还会记住这个商户。";
        loadPending(false);
      }

      var uncat = d.counts.uncategorized || 0;
      $("#uncatLine").classList.toggle("hidden", !uncat);
      $("#uncatText").textContent = "有 " + uncat + " 笔还没归类。点「去处理」逐笔指定，" +
        "指定过的商户下次会自动归到同一类。";

      if (d.series && d.series.length) { drawTrend($("#homeSpark"), d.series, { height: 58 }); }
      if (d.last_report_text) { $("#previewText").textContent = d.last_report_text; }
      if (d.counts.total === 0) {
        $("#previewText").textContent = "还没有数据。先从「设置 → 导入账单」上传一份支付宝或微信导出的 CSV。";
      }
    }).catch(function (e) {
      say("连不上服务器");
      $("#syncLine").textContent = "连不上服务器：" + e.message + "。确认手机和电脑在同一个 WiFi，且服务窗口还开着。";
    }).then(loadRecent);
  }

  function loadRecent() {
    return api("/api/tx?limit=8").then(function (d) {
      if (!d.ok) { return; }
      $("#homeTxList").innerHTML = renderRows(d.items, false);
    });
  }

  /* ---------------- 流水 ---------------- */
  function loadList() {
    var q = ["limit=400", "month=" + (state.month || curMonth())];
    if (state.dir) { q.push("direction=" + state.dir); }
    if (state.src) { q.push("source=" + state.src); }
    if (state.uncat) { q.push("uncategorized=1"); }
    if (state.q) { q.push("q=" + encodeURIComponent(state.q)); }
    return api("/api/tx?" + q.join("&")).then(function (d) {
      if (!d.ok) { return; }
      var out = 0, inc = 0, n = d.items.length;
      d.items.forEach(function (t) {
        if (t.direction === "out") { out += t.amount_cents; }
        if (t.direction === "in") { inc += t.amount_cents; }
      });
      $("#listSummary").textContent = n ? ("这 " + n + " 笔里，支出 " + money(out) + "，收入 " + money(inc)) : "";
      $("#listTx").innerHTML = renderRows(d.items, true);
    });
  }

  /* ---------------- 统计 ---------------- */
  function loadStats() {
    return api("/api/stats?month=" + (state.month || curMonth())).then(function (d) {
      if (!d.ok) { return; }
      state.days = d.daily || [];
      drawTrend($("#statsChart"), state.days, { height: 160, strokeOpacity: 0.5 });
      var n = state.days.length || 1;
      $("#statsAxis").innerHTML = "<span>1 日</span><span>" + Math.round(n / 2) + " 日</span><span>" + n + " 日</span>";
      var ms = d.month_status || {};
      $("#statsCaption").textContent = monthCN(d.month) + "共支出 " + money(ms.out_cents);
      $("#statsCats").innerHTML = rankList(d.categories, ms.out_cents, "cinnabar",
        "这个月还没有支出。");
      $("#statsIncome").innerHTML = rankList(d.income_categories, ms.in_cents, "celadon",
        "这个月还没有收入。");
      $("#statsMethods").innerHTML = rankList((d.methods || []).map(function (m) {
        return { category: m.k, total: m.total, cnt: 0 };
      }), ms.out_cents, "ink", "还没有支付方式记录。");
    }).then(loadSubs).then(loadMerchants);
  }

  /* 固定支出：同商户同金额连续几个月都出现，多半是自动续费 */
  function loadSubs() {
    return api("/api/subscriptions").then(function (d) {
      if (!d.ok) { return; }
      var items = d.items || [];
      $("#subsCaption").textContent = items.length
        ? ("每月约 " + money(d.monthly_total) + "，一年约 " + money(d.yearly_total)) : "";
      $("#statsSubs").innerHTML = items.length ? items.map(function (s) {
        return '<div class="sub-item"><span class="nm">' + esc(s.counterparty) +
          "<small>已扣 " + s.consecutive_months + " 个月　每月 " + s.day_of_month + " 日前后　下次约 " + s.next_day.slice(5) +
          "</small></span><span class='amt'>" + money(s.monthly_cents) + "/月</span></div>";
      }).join("") : '<p class="mini" style="padding:12px 0">还没发现按月重复的支出。会员续费、房租、话费这类连续扣几个月后，这里会自动列出来。</p>';
    });
  }

  /* 常去的商户：点一下可以批量改分类 */
  function loadMerchants() {
    return api("/api/merchants?month=" + (state.month || curMonth())).then(function (d) {
      if (!d.ok) { return; }
      state.merchants = d.items || [];
      $("#statsMerchants").innerHTML = state.merchants.length ? state.merchants.map(function (m, i) {
        return '<button class="sub-item" data-mi="' + i + '" style="width:100%;background:none;border-left:0;border-right:0;border-top:0;text-align:left;font:inherit;color:inherit">' +
          '<span class="nm">' + esc(m.name) + "<small>" + m.cnt + " 笔　最近 " + esc(m.last_day) + "　" + esc(m.category || "未分类") +
          "</small></span><span class='amt'>" + money(m.total) + "</span></button>";
      }).join("") : '<p class="mini" style="padding:12px 0">这个月还没有带商户名的支出。</p>';
    });
  }

  function openMerchantSheet(index) {
    var m = (state.merchants || [])[index];
    if (!m) { return; }
    openSheet('<h3>' + esc(m.name) + "</h3>" +
      '<p class="sheet-note">这个月 ' + m.cnt + " 笔，共 " + money(m.total) + "。选一个分类，会一次性改掉它的全部流水，并且以后自动归类。</p>" +
      '<label class="field"><span>改成一类</span>' + pickHTML("mhCats", m.category || "未分类") + "</label>" +
      '<div class="btn-row"><button class="btn" id="mhSave">批量修改</button></div>');
    var cat = m.category || "未分类";
    bindPick("#mhCats", function (c) { cat = c; });
    $("#mhSave").onclick = function () {
      api("/api/merchants/recategorize", { json: { counterparty: m.name, category: cat } }).then(function (r) {
        if (!r.ok) { toast(r.error || "没改成"); return; }
        toast("改了 " + r.updated + " 笔，以后「" + m.name + "」自动归到 " + cat);
        closeSheet(); loadStats(); loadHome();
      });
    };
  }
  function rankList(items, total, color, emptyText) {
    if (!items || !items.length) { return '<p class="mini" style="padding:12px 0">' + emptyText + "</p>"; }
    var max = 1;
    items.forEach(function (c) { max = Math.max(max, c.total); });
    var colorMap = { cinnabar: "var(--cinnabar)", celadon: "var(--celadon)", ink: "var(--ink-2)" };
    return items.map(function (c) {
      var pct = total ? (c.total * 100 / total) : 0;
      return '<div class="rank"><div class="top"><span class="nm">' + esc(c.category || "未分类") + "</span>" +
        '<span class="vl num">' + money(c.total) + "</span>" +
        '<span class="pc num">' + pct.toFixed(pct >= 10 ? 0 : 1) + "%</span></div>" +
        '<div class="rule"><i style="width:' + Math.max(1, c.total / max * 100).toFixed(1) + "%;background:" +
        (colorMap[color] || colorMap.ink) + '"></i></div></div>';
    }).join("");
  }

  /* ---------------- 设置 ---------------- */
  function loadMe() {
    return api("/api/settings").then(function (d) {
      if (!d.ok) { return; }
      var s = d.settings;
      $("#setInbox").value = d.paths.inbox;
      $("#setInterval").value = (s.sync || {}).interval_hours || 3;
      $("#setDailyTimes").value = ((s.reports || {}).daily_times || []).join(",");
      $("#setDailyMode").value = (s.reports || {}).daily_mode || "auto";
      $("#setBudget").value = ((s.budget || {}).monthly || 0);
      $("#setLarge").value = ((s.alerts || {}).large_amount || 0);
      $("#setChannels").value = JSON.stringify(((s.notify || {}).channels) || [], null, 2);
      $("#serverInfo").textContent = LOCAL
        ? ("数据保存在 " + d.paths.db + "，不会上传到任何服务器。换设备或清浏览器数据前，记得先「导出 CSV」备份。")
        : ("数据库 " + d.paths.db + "。访问口令" +
           (d.token_set ? "已开启。" : "没开——同一个 WiFi 下的其他人都能打开，建议在 config.json 里设一个 access_token。"));
    }).then(function () {
      return Promise.all([api("/api/imports"), api("/api/jobs"), api("/api/pushes")]);
    }).then(function (res) {
      $("#logImports").innerHTML = logList(res[0].items.slice(0, 8), function (r) {
        return "<b>" + esc(r.filename) + "</b>　" + esc(SOURCE[r.source] || r.source) + "　新增 " + r.rows_new +
          " 条，去重 " + r.rows_dup + " 条<br><span class='t'>" + esc(r.imported_at) + "</span>";
      }, "还没有导入过账单。");
      $("#logJobs").innerHTML = logList(res[1].items.slice(0, 10), function (r) {
        return "<b>" + esc(r.name) + "</b>　" + (r.ok ? "成功" : "失败") + "　" + esc(r.detail || "") +
          "<br><span class='t'>" + esc(r.finished_at) + "</span>";
      }, "还没有任务记录。");
      $("#logPushes").innerHTML = logList(res[2].items.slice(0, 8), function (r) {
        return "<b>" + esc(r.channel) + "</b>　" + (r.ok ? "成功" : "失败") + "　" +
          esc(String(r.title || "").slice(0, 24)) + "<br><span class='t'>" + esc(String(r.resp || "").slice(0, 80)) + "</span>";
      }, "还没有推送记录。");
    });
  }
  function logList(items, render, emptyText) {
    if (!items || !items.length) { return '<p class="mini" style="padding:10px 0">' + emptyText + "</p>"; }
    return items.map(function (r) { return '<div class="log-item">' + render(r) + "</div>"; }).join("");
  }

  function importFile(file) {
    $("#importResult").textContent = "正在上传 " + file.name + "…";
    fetch("/api/import?token=" + encodeURIComponent(token()), {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
      body: file
    }).then(function (r) { return r.json(); }).then(function (r) {
      if (!r.ok) { $("#importResult").textContent = "导入失败：" + (r.error || "服务器没有说明原因"); return; }
      var s = r.stats || {};
      if (s.skipped) {
        $("#importResult").textContent = "这个文件导过一次了，跳过。";
      } else if (s.error) {
        $("#importResult").textContent = "导入失败：" + s.error;
      } else if (!s.total) {
        $("#importResult").textContent = "没读出流水。确认文件是支付宝或微信「用于个人对账」导出的 CSV，不是截图或 PDF。";
      } else {
        $("#importResult").textContent = "导入完成：" + (s.label || s.source) + " 共 " + s.total + " 条，" +
          "新增 " + s.new + " 条，去重 " + ((s.dup || 0) + (s.merged || 0)) + " 条。";
        toast("新增 " + s.new + " 条流水");
      }
      loadHome(); loadList(); loadMe();
    }).catch(function (e) { $("#importResult").textContent = "导入失败：" + e.message; });
  }

  /* ---------------- 记账 / 编辑 ---------------- */
  function pickHTML(id, selected) {
    return '<div class="pick" id="' + id + '">' + CATS.map(function (c) {
      return '<button type="button" data-cat="' + c + '"' + (c === selected ? ' class="on"' : "") + ">" + c + "</button>";
    }).join("") + "</div>";
  }
  function bindPick(sel, cb) {
    $$(sel + " button").forEach(function (b) {
      b.onclick = function () {
        $$(sel + " button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        if (cb) { cb(b.dataset.cat); }
      };
    });
  }
  function nowLocal() {
    var d = new Date();
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + "T" +
           pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function dirPickHTML(id, current) {
    return '<div class="dir-pick chips" id="' + id + '">' +
      [["out", "支出"], ["in", "收入"], ["neutral", "不计收支"]].map(function (p) {
        return '<button type="button" class="chip' + (current === p[0] ? " on" : "") + '" data-dir="' + p[0] + '">' +
          p[1] + "</button>";
      }).join("") + "</div>";
  }
  function bindDir(sel, cb) {
    $$(sel + " button").forEach(function (b) {
      b.onclick = function () {
        $$(sel + " button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        if (cb) { cb(b.dataset.dir); }
      };
    });
  }

  function openAddSheet() {
    openSheet('<h3>记一笔</h3><p class="sheet-note">填金额就行，其余可以留空；商户名填过的，下次会自动归到同一类。</p>' +
      '<div class="amount-field"><p class="cap">金额（元）</p>' +
      '<input type="number" id="addAmount" inputmode="decimal" step="0.01" placeholder="0.00"></div>' +
      dirPickHTML("addDir", "out") +
      '<label class="field"><span>商户 / 对方</span><input type="text" id="addCp" placeholder="例如：肯德基"></label>' +
      '<label class="field"><span>分类</span>' + pickHTML("addCats", "未分类") + "</label>" +
      '<label class="field"><span>时间</span><input type="datetime-local" id="addTs" value="' + nowLocal() + '"></label>' +
      '<label class="field"><span>备注</span><input type="text" id="addNote"></label>' +
      '<div class="btn-row"><button class="btn" id="addSave">保存这一笔</button></div>');
    var dir = "out", cat = "未分类";
    bindDir("#addDir", function (d) { dir = d; });
    bindPick("#addCats", function (c) { cat = c; });
    $("#addAmount").focus();
    $("#addSave").onclick = function () {
      var amount = $("#addAmount").value.trim();
      if (!amount) { toast("先填金额"); return; }
      api("/api/tx", { json: {
        amount: amount, direction: dir, counterparty: $("#addCp").value.trim(), category: cat,
        ts: $("#addTs").value.replace("T", " "), note: $("#addNote").value.trim()
      } }).then(function (r) {
        if (!r.ok) { toast(r.error || "没保存成功"); return; }
        toast(r.status === "merged" ? "和已有的一笔合并了" : "记好了");
        closeSheet(); loadHome(); loadList();
      }).catch(function (e) { toast(e.message); });
    };
  }

  function openEditSheet(id) {
    api("/api/tx/" + id).then(function (d) {
      if (!d.ok) { toast("这笔流水找不到了"); return; }
      var t = d.item;
      openSheet('<h3>改这一笔</h3>' +
        '<p class="sheet-note">' + esc(t.ts) + "　" + esc(SOURCE[t.source] || t.source) +
          (t.origin === "notify" ? "　由通知自动抓取" : "") + "</p>" +
        '<div class="amount-field"><p class="cap">金额（元）</p><input type="number" id="edAmount" step="0.01" value="' +
          (t.amount_cents / 100).toFixed(2) + '"></div>' +
        dirPickHTML("edDir", t.direction) +
        '<label class="field"><span>商户 / 对方</span><input type="text" id="edCp" value="' + esc(t.counterparty) + '"></label>' +
        '<label class="field"><span>分类</span>' + pickHTML("edCats", t.category) + "</label>" +
        '<label class="field"><span>备注</span><input type="text" id="edNote" value="' + esc(t.note) + '"></label>' +
        '<div class="btn-row"><button class="btn danger" id="edDel">删除</button>' +
        '<button class="btn" id="edSave">保存修改</button></div>' +
        (t.raw ? '<div class="sec-row"><h2 class="sec">原始记录</h2></div><pre class="text-out">' +
          esc(String(t.raw).slice(0, 400)) + "</pre>" : ""));
      var cat = t.category;
      bindPick("#edCats", function (c) { cat = c; });
      $("#edSave").onclick = function () {
        api("/api/tx/" + id, { json: {
          amount: $("#edAmount").value, direction: $("#edDir .chip.on").dataset.dir,
          counterparty: $("#edCp").value.trim(), category: cat, note: $("#edNote").value.trim()
        } }).then(function (r) {
          if (!r.ok) { toast(r.error || "没保存成功"); return; }
          toast(r.learned_rule ? "改好了，以后「" + r.learned_rule + "」自动归到这一类" : "改好了");
          closeSheet(); loadHome(); loadList();
        });
      };
      $("#edDel").onclick = function () {
        fetch("/api/tx/" + id + "?token=" + encodeURIComponent(token()), { method: "DELETE", headers: { "X-Token": token() } })
          .then(function () { toast("删掉了"); closeSheet(); loadHome(); loadList(); });
      };
    });
  }

  /* ---------------- 待录入（没认出来的通知） ---------------- */
  function toLocalInput(ts) {
    var s = String(ts || "").replace("T", " ");
    return s.length >= 16 ? s.slice(0, 16).replace(" ", "T") : nowLocal();
  }
  function loadPending(showSheet) {
    return api("/api/pending?limit=50").then(function (d) {
      if (!d.ok) { return; }
      state.pending = d.items || [];
      state.pendingTotal = d.total || 0;
      $("#pendingLine").classList.toggle("hidden", !state.pendingTotal);
      if (state.pendingTotal) {
        $("#pendingText").textContent = "有 " + state.pendingTotal + " 条通知没认出金额或商户。" +
          "点「去补录」补上就能入账，顺手还会记住这个商户。";
      }
      if (showSheet) { openPendingSheet(); }
    });
  }
  function openPendingSheet() {
    var items = state.pending || [];
    openSheet("<h3>待录入</h3><p class='sheet-note'>这些通知没读出交易信息，所以先放在这里。" +
      "点一条补上金额和商户，就正式入账。</p>" +
      (items.length ? items.map(function (p) {
        return '<button class="sub-item" data-pid="' + p.id + '" style="width:100%;background:none;border-left:0;border-right:0;border-top:0;text-align:left;font:inherit;color:inherit">' +
          '<span class="nm">' + esc(String(p.raw || "").slice(0, 40)) + "<small>" + esc(p.ts) + "　" + esc(p.reason || "") +
          (p.attempts > 1 ? "　出现过 " + p.attempts + " 次" : "") + "</small></span>" +
          '<span class="mini">补录</span></button>';
      }).join("") : '<p class="mini" style="padding:12px 0">没有待录入的记录。</p>'));
    $("#sheetBody").onclick = function (e) {
      var b = e.target.closest ? e.target.closest(".sub-item") : null;
      if (b) { openPendingResolve(b.dataset.pid); }
    };
  }
  function openPendingResolve(pid) {
    var p = (state.pending || []).filter(function (x) { return String(x.id) === String(pid); })[0];
    if (!p) { return; }
    openSheet("<h3>补录这一笔</h3><p class='sheet-note'>通知原文</p>" +
      '<div class="quote">' + esc(p.raw) + "</div>" +
      '<div class="amount-field"><p class="cap">金额（元）</p><input type="number" id="pdAmount" inputmode="decimal" ' +
      'step="0.01" placeholder="0.00" value="' + (p.amount_cents ? (p.amount_cents / 100).toFixed(2) : "") + '"></div>' +
      dirPickHTML("pdDir", "out") +
      '<label class="field"><span>商户 / 对方</span><input type="text" id="pdCp" placeholder="例如：肯德基"></label>' +
      '<label class="field"><span>分类</span>' + pickHTML("pdCats", "未分类") + "</label>" +
      '<label class="field"><span>时间</span><input type="datetime-local" id="pdTs" value="' + toLocalInput(p.ts) + '"></label>' +
      '<div class="btn-row"><button class="btn danger" id="pdIgnore">忽略</button>' +
      '<button class="btn" id="pdSave">入账并记住商户</button></div>');
    var dir = "out", cat = "未分类";
    bindDir("#pdDir", function (x) { dir = x; });
    bindPick("#pdCats", function (c) { cat = c; });
    $("#pdSave").onclick = function () {
      api("/api/pending/" + pid, { json: {
        amount: $("#pdAmount").value, direction: dir, counterparty: $("#pdCp").value.trim(),
        category: cat, ts: $("#pdTs").value.replace("T", " ")
      } }).then(function (r) {
        if (!r.ok) { toast(r.error || "没存上"); return; }
        toast(r.learned_rule ? ("入账了，以后「" + r.learned_rule + "」自动归类") : "入账了");
        closeSheet(); loadHome(); loadPending(false); loadList();
      });
    };
    $("#pdIgnore").onclick = function () {
      api("/api/pending/" + pid, { json: { action: "ignore" } }).then(function () {
        toast("已忽略这条"); closeSheet(); loadHome(); loadPending(false);
      });
    };
  }

  /* ---------------- 分类规则 ---------------- */
  var FIELD_LABEL = { any: "商户或商品", peer: "交易对方", item: "商品说明", category: "交易分类", method: "支付方式" };
  var DIR_LABEL = { in: "收入", out: "支出", neutral: "不计收支" };
  function ruleCondition(r) {
    var parts = [];
    if (r.keyword) {
      parts.push((r.field && r.field !== "any" ? (FIELD_LABEL[r.field] || "") + "含" : "含") + "「" + r.keyword + "」");
    }
    if (r.min_cents !== null || r.max_cents !== null) {
      parts.push("金额 " + (r.min_cents !== null ? money(r.min_cents) : "0") + "–" +
        (r.max_cents !== null ? money(r.max_cents) : "不限"));
    }
    if (r.time_from) { parts.push(r.time_from + "–" + (r.time_to || "24:00")); }
    if (r.direction) { parts.push(DIR_LABEL[r.direction] || r.direction); }
    if (r.source) { parts.push(SOURCE[r.source] || r.source); }
    if (r.method) { parts.push("用" + r.method); }
    return parts.length ? parts.join(" 且 ") : "（无条件的规则不会生效）";
  }
  function ruleResult(r) {
    if (r.action === "ignore") { return "忽略，不入账"; }
    if (r.action === "neutral") { return "标记为不计收支" + (r.category ? "（" + r.category + "）" : ""); }
    if (r.action === "tag") { return "打标签「" + (r.tags || "") + "」"; }
    return (r.category || "未分类") + (r.sub_category ? " / " + r.sub_category : "");
  }
  function loadRules() {
    return api("/api/rules").then(function (d) {
      if (!d.ok) { return; }
      state.rules = d.items || [];
      $("#rulesCaption").textContent = "共 " + d.total + " 条，其中你自己加的 " + d.custom +
        " 条。按优先级从小到大匹配，先命中的一条生效；打标签的规则可以叠加。";
      renderRules();
    });
  }
  function renderRules() {
    var scope = state.ruleScope || "custom";
    var q = (state.ruleQuery || "").trim();
    var items = (state.rules || []).filter(function (r) {
      if (scope === "custom" && r.builtin) { return false; }
      if (scope === "builtin" && !r.builtin) { return false; }
      if (q && String((r.keyword || "") + (r.category || "") + (r.sub_category || "") + (r.note || "")).indexOf(q) < 0) {
        return false;
      }
      return true;
    });
    var shown = items.slice(0, 60);
    $("#rulesList").innerHTML = shown.length ? shown.map(function (r) {
      return '<div class="rule" data-id="' + r.id + '"><div class="body">' +
        '<div class="cond">' + esc(ruleCondition(r)) + (r.builtin ? ' <span class="tag">内置</span>' : "") + "</div>" +
        '<div class="res">→ ' + esc(ruleResult(r)) + (r.note ? "　" + esc(r.note) : "") + "</div></div>" +
        '<div class="hits">' + (r.hits ? "命中 " + r.hits : "") + "</div>" +
        '<button class="del" data-del="' + r.id + '">删</button></div>';
    }).join("") + (items.length > shown.length
      ? '<p class="mini" style="padding:10px 0">还有 ' + (items.length - shown.length) + " 条没显示，用搜索缩小范围。</p>" : "")
      : '<p class="mini" style="padding:12px 0">' +
        (scope === "custom" ? "你还没有加过规则。点「新增」试试，比如：对方含「滴滴」→ 交通 / 打车。" : "没有匹配的规则。") + "</p>";
  }
  function openRuleSheet() {
    openSheet("<h3>新增分类规则</h3><p class='sheet-note'>填写想匹配的条件和想要的结果。" +
      "规则按优先级匹配，数字越小越先判断（具体商户建议 5–20，兜底规则建议 90 以上）。</p>" +
      '<label class="field"><span>关键词<em>（多个用逗号分隔，命中其一即可）</em></span>' +
      '<input type="text" id="rlKeyword" placeholder="例如：滴滴,花小猪"></label>' +
      '<label class="field"><span>匹配范围</span><select id="rlField">' +
      Object.keys(FIELD_LABEL).map(function (k) {
        return '<option value="' + k + '">' + FIELD_LABEL[k] + "</option>";
      }).join("") + "</select></label>" +
      '<label class="field"><span>要做的事</span><select id="rlAction">' +
      '<option value="categorize">归到某个分类</option><option value="neutral">标记为不计收支</option>' +
      '<option value="tag">只打标签（不影响分类）</option><option value="ignore">忽略，不入账</option></select></label>' +
      '<label class="field"><span>分类</span>' + pickHTML("rlCats", "未分类") + "</label>" +
      '<label class="field"><span>子分类<em>（可留空）</em></span><input type="text" id="rlSub" placeholder="例如：打车"></label>' +
      '<label class="field"><span>标签<em>（打标签规则必填）</em></span><input type="text" id="rlTags" placeholder="例如：小额"></label>' +
      '<label class="field"><span>金额区间（元）<em>（可留空）</em></span>' +
      '<div class="row"><input type="number" id="rlMin" step="0.01" placeholder="最小"><input type="number" id="rlMax" step="0.01" placeholder="最大"></div></label>' +
      '<label class="field"><span>时间段<em>（可留空，支持跨零点如 21:30–05:00）</em></span>' +
      '<div class="row"><input type="text" id="rlFrom" placeholder="11:00" inputmode="numeric"><input type="text" id="rlTo" placeholder="14:00" inputmode="numeric"></div></label>' +
      '<label class="field"><span>方向</span><select id="rlDir"><option value="">不限</option>' +
      '<option value="out">只看支出</option><option value="in">只看收入</option><option value="neutral">只看不计收支</option></select></label>' +
      '<label class="field"><span>优先级</span><input type="number" id="rlPriority" value="10" min="1" max="999"></label>' +
      '<label class="field"><span>备注<em>（写给自己看）</em></span><input type="text" id="rlNote"></label>' +
      '<div class="btn-row"><button class="btn" id="rlSave">保存规则</button></div>');
    var cat = "未分类";
    bindPick("#rlCats", function (c) { cat = c; });
    $("#rlSave").onclick = function () {
      api("/api/rules", { json: {
        keyword: $("#rlKeyword").value.trim(), field: $("#rlField").value, action: $("#rlAction").value,
        category: $("#rlAction").value === "tag" || $("#rlAction").value === "ignore" ? "" : cat,
        sub_category: $("#rlSub").value.trim(), tags: $("#rlTags").value.trim(),
        min_amount: $("#rlMin").value, max_amount: $("#rlMax").value,
        time_from: $("#rlFrom").value.trim(), time_to: $("#rlTo").value.trim(),
        direction: $("#rlDir").value, priority: $("#rlPriority").value, note: $("#rlNote").value.trim()
      } }).then(function (r) {
        if (!r.ok) { toast(r.error || "没保存成功"); return; }
        toast("规则加好了"); closeSheet(); loadRules(); loadHome();
      });
    };
  }
  function openRuleTestSheet() {
    openSheet("<h3>试算规则</h3><p class='sheet-note'>随便写一条流水的样子，看看会命中哪条规则、最终归到什么分类。" +
      "不含条件的字段可以留空。</p>" +
      '<label class="field"><span>商户 / 对方</span><input type="text" id="tsCp" placeholder="例如：滴滴出行"></label>' +
      '<label class="field"><span>商品说明</span><input type="text" id="tsItem" placeholder="例如：快车-上班"></label>' +
      '<label class="field"><span>金额（元）</span><input type="number" id="tsAmount" step="0.01" value="23.00"></label>' +
      '<label class="field"><span>时间</span><input type="datetime-local" id="tsTs" value="' + nowLocal() + '"></label>' +
      '<label class="field"><span>来源</span><select id="tsSource"><option value="wechat">微信</option>' +
      '<option value="alipay">支付宝</option><option value="bank">银行</option><option value="manual">手工</option></select></label>' +
      '<div class="btn-row"><button class="btn" id="tsRun">试算</button></div><div id="tsOut"></div>');
    $("#tsRun").onclick = function () {
      api("/api/rules/test", { json: {
        counterparty: $("#tsCp").value.trim(), item: $("#tsItem").value.trim(),
        amount: $("#tsAmount").value, ts: $("#tsTs").value.replace("T", " "), source: $("#tsSource").value
      } }).then(function (r) {
        if (!r.ok) { $("#tsOut").innerHTML = '<p class="mini">算不了：' + esc(r.error || "") + "</p>"; return; }
        var rule = r.rule || {};
        $("#tsOut").innerHTML = '<div class="sec-row"><h2 class="sec">结果</h2></div>' +
          '<div class="quote">分类：' + esc(r.category || "未分类") + (r.sub_category ? " / " + esc(r.sub_category) : "") +
          (r.tags && r.tags.length ? "　标签：" + esc(r.tags.join("、")) : "") +
          "<br>动作：" + esc(r.action || "无") +
          (rule.id ? "<br>命中的规则：" + esc(ruleCondition(rule)) + "（优先级 " + rule.priority + "）" : "<br>没有规则命中，落到默认分类") +
          "</div>";
      });
    };
  }

  /* ---------------- 视图切换 ---------------- */
  var VIEWS = ["home", "list", "stats", "me"];
  function switchView(v) {
    if (VIEWS.indexOf(v) < 0) { v = "home"; }
    state.view = v;
    $$(".view").forEach(function (s) { s.classList.toggle("active", s.id === "view-" + v); });
    $$("#tabbar button").forEach(function (b) { b.classList.toggle("on", b.dataset.view === v); });
    $("#fab").style.display = v === "me" ? "none" : "block";
    try { window.history.replaceState(null, "", "#" + v); } catch (e) { /* 老浏览器忽略 */ }
    if (v === "home") { loadHome(); }
    if (v === "list") { loadList(); }
    if (v === "stats") { loadStats(); }
    if (v === "me") { loadMe(); loadRules(); }
    window.scrollTo(0, 0);
  }

  function bind() {
    $$("#tabbar button").forEach(function (b) { b.onclick = function () { switchView(b.dataset.view); }; });
    $("#fab").onclick = openAddSheet;
    $("#mask").onclick = closeSheet;

    if (LOCAL) {
      $("#btnSync").textContent = "导入账单";
      $("#btnSync").onclick = function () { $("#fileInput").click(); };
      document.documentElement.classList.add("local");
    }
    $("#btnSync").onclick = LOCAL ? $("#btnSync").onclick : function () {
      var btn = this;
      btn.textContent = "同步中…";
      api("/api/sync", { json: {} }).then(function (r) {
        btn.textContent = "同步流水";
        var s = r.sync || {};
        toast(s.ok ? ("同步完成，新增 " + (s.new || 0) + " 条") : "同步失败，具体原因在「设置 → 最近任务」里");
        if (state.view === "home") { loadHome(); } else { loadList(); }
      }).catch(function () { btn.textContent = "同步流水"; });
    };
    $("#btnAllTx").onclick = function () { switchView("list"); };
    $("#btnUncat").onclick = function () {
      state.uncat = true; state.dir = ""; state.src = "";
      $$("#listChips .chip").forEach(function (c) { c.classList.toggle("on", c.dataset.un === "1"); });
      switchView("list");
    };
    $("#btnPreview").onclick = function () {
      $("#previewText").textContent = "生成中…";
      api("/api/report/preview?mode=auto").then(function (r) {
        $("#previewText").textContent = r.text;
      }).catch(function (e) { $("#previewText").textContent = "生成失败：" + e.message; });
    };
    $("#btnPush").onclick = function () {
      var btn = this;
      btn.textContent = "推送中…";
      api("/api/report/push", { json: {} }).then(function (r) {
        btn.textContent = "推送";
        $("#previewText").textContent = r.text || "";
        var okc = (r.results || []).filter(function (x) { return x.ok; }).length;
        toast(okc ? ("已经发到 " + okc + " 个通道") : "没有通道发送成功。在「设置 → 推送通道」里检查配置，再点「发送测试」。");
      }).catch(function () { btn.textContent = "推送"; });
    };

    $("#listMonth").onchange = function () { state.month = this.value; loadList(); };
    $("#btnThisMonth").onclick = function () { state.month = curMonth(); $("#listMonth").value = state.month; loadList(); };
    $("#listSearch").oninput = function () {
      var self = this;
      clearTimeout(this._t);
      this._t = setTimeout(function () { state.q = self.value.trim(); loadList(); }, 260);
    };
    $("#listChips").onclick = function (e) {
      var chip = e.target.closest ? e.target.closest(".chip") : null;
      if (!chip) { return; }
      $$("#listChips .chip").forEach(function (x) { x.classList.remove("on"); });
      chip.classList.add("on");
      state.dir = chip.dataset.dir || "";
      state.uncat = chip.dataset.un === "1";
      state.src = chip.dataset.src || "";
      loadList();
    };
    function rowClick(e) {
      var row = e.target.closest ? e.target.closest(".row") : null;
      if (row) { openEditSheet(row.dataset.id); }
    }
    $("#listTx").onclick = rowClick;
    $("#homeTxList").onclick = rowClick;

    $("#statsMerchants").onclick = function (e) {
      var b = e.target.closest ? e.target.closest(".sub-item") : null;
      if (b) { openMerchantSheet(Number(b.dataset.mi)); }
    };
    $("#statsMonth").onchange = function () { state.month = this.value; loadStats(); };
    $("#btnStatsRefresh").onclick = function () { loadStats(); };

    $("#btnPending").onclick = function () { loadPending(true); };
    $("#btnRuleAdd").onclick = openRuleSheet;
    $("#btnRuleTest").onclick = openRuleTestSheet;
    $("#ruleChips").onclick = function (e) {
      var chip = e.target.closest ? e.target.closest(".chip") : null;
      if (!chip) { return; }
      $$("#ruleChips .chip").forEach(function (x) { x.classList.remove("on"); });
      chip.classList.add("on");
      state.ruleScope = chip.dataset.scope;
      renderRules();
    };
    $("#ruleSearch").oninput = function () {
      var self = this;
      clearTimeout(this._t);
      this._t = setTimeout(function () { state.ruleQuery = self.value; renderRules(); }, 200);
    };
    $("#rulesList").onclick = function (e) {
      var btn = e.target.closest ? e.target.closest("[data-del]") : null;
      if (!btn) { return; }
      fetch("/api/rules/" + btn.dataset.del + "?token=" + encodeURIComponent(token()), { method: "DELETE" })
        .then(function () { toast("规则删了"); loadRules(); loadHome(); });
    };
    $("#fileInput").onchange = function () { if (this.files[0]) { importFile(this.files[0]); } };
    $("#btnSaveSettings").onclick = function () {
      var times = $("#setDailyTimes").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      api("/api/settings", { json: {
        sync: { interval_hours: Number($("#setInterval").value) || 3 },
        reports: { daily_times: times.length ? times : ["00:00", "12:00"], daily_mode: $("#setDailyMode").value },
        budget: { monthly: Number($("#setBudget").value) || 0 },
        alerts: { large_amount: Number($("#setLarge").value) || 0 }
      } }).then(function (r) { toast(r.ok ? "设置保存了，下一轮同步就按新的来" : "没保存成功"); loadHome(); });
    };
    $("#btnSaveChannels").onclick = function () {
      var arr;
      try { arr = JSON.parse($("#setChannels").value || "[]"); }
      catch (e) { toast("JSON 写错了：" + e.message); return; }
      api("/api/settings", { json: { notify: { channels: arr } } }).then(function (r) {
        toast(r.ok ? "推送通道保存了，点「发送测试」验证一下" : "没保存成功");
      });
    };
    $("#btnTestPush").onclick = function () {
      $("#pushResult").textContent = "正在发送…";
      api("/api/notify/test", { json: {} }).then(function (r) {
        var okc = (r.results || []).filter(function (x) { return x.ok; }).length;
        $("#pushResult").textContent = okc
          ? ("发送成功 " + okc + " 个通道：" + (r.results || []).filter(function (x) { return x.ok; })
              .map(function (x) { return x.type; }).join("、") + "。去手机上看看收到没有。")
          : ("没有发送成功。" + (r.results || []).map(function (x) {
              return x.type + " 返回 " + String(x.resp || "").slice(0, 60);
            }).join("；"));
      });
    };
    $("#btnSyncNow").onclick = function () {
      toast("正在同步…");
      api("/api/sync", { json: {} }).then(function (r) {
        toast("新增 " + ((r.sync || {}).new || 0) + " 条流水");
        loadMe();
      });
    };
    $("#btnRecat").onclick = function () {
      api("/api/recategorize", { json: {} }).then(function (r) {
        toast(r.updated ? ("重新归类了 " + r.updated + " 条") : "没有需要改的，分类都对");
        loadHome();
      });
    };
    $("#btnExport").onclick = function () { window.open("/api/export.csv?token=" + encodeURIComponent(token()), "_blank"); };
    $("#btnReload").onclick = function () { loadMe(); loadHome(); toast("刷新了"); };
  }

  function boot() {
    api("/api/server-info").then(function (info) {
      if (info && info.token && !token()) { setToken(info.token); }
    }).catch(function () {});
    api("/api/ping").then(function (d) {
      if (d.auth_required && !token()) { askToken(); }
    }).catch(function () { say("连不上服务器"); });
    $("#listMonth").value = state.month || curMonth();
    $("#statsMonth").value = state.month || curMonth();
    var fromHash = (window.location.hash || "").replace("#", "");
    switchView(VIEWS.indexOf(fromHash) >= 0 ? fromHash : "home");
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* 局域网 HTTP 下不可用 */ });
  }
  var started = false;
  function start() {
    if (started) { return; }
    started = true;
    bind();
    boot();
    // 浏览器前进/后退、书签、手改地址栏里的 #视图 都要能切过去
    window.addEventListener("hashchange", function () {
      var v = (window.location.hash || "").replace("#", "");
      if (VIEWS.indexOf(v) >= 0 && v !== state.view) { switchView(v); }
    });
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", start); } else { start(); }
})();
