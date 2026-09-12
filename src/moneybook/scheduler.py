# -*- coding: utf-8 -*-
"""轻量调度器：每 N 小时自动同步流水 + 每天定时汇总账单。

不依赖 apscheduler，纯线程 + 状态表，进程重启后不会重复推送。
"""
import threading
import time
from datetime import datetime, timedelta

from . import jobs, util


def _naive(dt):
    """统一成无时区本地时间：库里存的时间字符串都是本地时间。"""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _parse_hhmm(text, default=(12, 0)):
    try:
        hh, mm = str(text).strip().split(":")
        return int(hh), int(mm)
    except (ValueError, AttributeError):
        return default


class Scheduler(object):
    def __init__(self, store, cfg, log=print):
        self.store = store
        self.cfg = cfg
        self.log = log
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_tick = None
        self.next_sync = None
        self.next_daily = None
        self.running = False

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="moneybook-scheduler", daemon=True)
        self._thread.start()
        self.log("[scheduler] 已启动：每 %s 小时同步，每日 %s 出账单"
                 % (util.get(self.cfg, "sync.interval_hours", 3),
                    "/".join(util.get(self.cfg, "reports.daily_times", ["12:00"]))))

    def stop(self):
        self._stop.set()

    # ---------- 时间计算 ----------
    def _hours(self):
        try:
            h = float(util.get(self.cfg, "sync.interval_hours", 3))
        except (TypeError, ValueError):
            h = 3.0
        return max(0.2, h)

    def sync_due(self, now):
        now = _naive(now)
        last = self.store.get_state("last_sync_ts")
        if not last:
            return True
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return True
        return now >= last_dt + timedelta(hours=self._hours())

    def _daily_slots(self):
        slots = util.get(self.cfg, "reports.daily_times", ["12:00"]) or []
        if isinstance(slots, str):
            slots = [slots]
        return [str(s) for s in slots]

    def due_daily_slots(self, now):
        """返回当前应当执行的账单时段列表（错过 6 小时内会补发）。"""
        now = _naive(now)
        out = []
        for slot in self._daily_slots():
            hh, mm = _parse_hhmm(slot)
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now < target:
                continue
            if now > target + timedelta(hours=6):
                continue
            key = "daily_done_%s_%s" % (slot.replace(":", ""), now.strftime("%Y-%m-%d"))
            if self.store.get_state(key):
                continue
            out.append(slot)
        return out

    def due_weekly(self, now):
        now = _naive(now)
        conf = util.get(self.cfg, "reports.weekly", {}) or {}
        if not conf.get("enabled"):
            return False
        try:
            weekday = int(conf.get("weekday", 1))
        except (TypeError, ValueError):
            weekday = 1
        hh, mm = _parse_hhmm(conf.get("time", "09:00"), (9, 0))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < target or now > target + timedelta(hours=6):
            return False
        if now.isoweekday() != weekday:
            return False
        key = "weekly_done_" + now.strftime("%Y-%m-%d")
        return not self.store.get_state(key)

    def due_monthly(self, now):
        now = _naive(now)
        conf = util.get(self.cfg, "reports.monthly", {}) or {}
        if not conf.get("enabled"):
            return False
        try:
            day = int(conf.get("day", 1))
        except (TypeError, ValueError):
            day = 1
        hh, mm = _parse_hhmm(conf.get("time", "09:00"), (9, 0))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now.day != day or now < target or now > target + timedelta(hours=6):
            return False
        key = "monthly_done_" + now.strftime("%Y-%m")
        return not self.store.get_state(key)

    # ---------- 主循环 ----------
    def _loop(self):
        # 启动时先跑一次同步（可配置关闭）
        if util.get(self.cfg, "sync.on_start", True) and self.sync_due(util.now()):
            self._safe(jobs.sync_now, self.store, self.cfg, "startup")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.log("[scheduler] tick 异常：%s: %s" % (type(exc).__name__, exc))
            self._stop.wait(20)

    def _safe(self, fn, *args, **kwargs):
        with self._lock:
            self.running = True
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                self.log("[scheduler] 任务异常：%s: %s" % (type(exc).__name__, exc))
                return {"ok": False, "error": str(exc)}
            finally:
                self.running = False

    def tick(self):
        now = util.now()
        self.last_tick = now.strftime("%Y-%m-%d %H:%M:%S")
        if self.sync_due(now):
            res = self._safe(jobs.sync_now, self.store, self.cfg, "schedule")
            self._safe(jobs.check_large_amount, self.store, self.cfg)
            self.log("[scheduler] 定时同步完成：%s" % (res or {}).get("detail", ""))
        for slot in self.due_daily_slots(now):
            res = self._safe(jobs.push_report, self.store, self.cfg, None, "daily")
            self.store.set_state("daily_done_%s_%s" % (slot.replace(":", ""), now.strftime("%Y-%m-%d")), True)
            self.log("[scheduler] %s 账单已生成并推送" % slot)
        if self.due_weekly(now):
            self._safe(jobs.weekly_report, self.store, self.cfg)
            self.store.set_state("weekly_done_" + now.strftime("%Y-%m-%d"), True)
            self.log("[scheduler] 周报已推送")
        if self.due_monthly(now):
            self._safe(jobs.monthly_report, self.store, self.cfg)
            self.store.set_state("monthly_done_" + now.strftime("%Y-%m"), True)
            self.log("[scheduler] 月报已推送")
        self._refresh_next(now)

    def _refresh_next(self, now):
        now = _naive(now)
        last = self.store.get_state("last_sync_ts")
        try:
            base = datetime.strptime(last, "%Y-%m-%d %H:%M:%S") if last else now
        except ValueError:
            base = now
        self.next_sync = (base + timedelta(hours=self._hours())).strftime("%Y-%m-%d %H:%M")
        upcoming = []
        for slot in self._daily_slots():
            hh, mm = _parse_hhmm(slot)
            t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            upcoming.append(t)
        self.next_daily = min(upcoming).strftime("%Y-%m-%d %H:%M") if upcoming else None

    def status(self):
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "busy": self.running,
            "interval_hours": self._hours(),
            "last_sync": self.store.get_state("last_sync_ts"),
            "next_sync": self.next_sync,
            "next_daily_report": self.next_daily,
            "daily_times": self._daily_slots(),
            "last_tick": self.last_tick,
        }
