# -*- coding: utf-8 -*-
"""配置加载：config.json + 环境变量，支持点号路径读写。"""
import copy
import json
import os

DEFAULT_CONFIG = {
    "timezone": "Asia/Shanghai",
    "host": "0.0.0.0",
    "port": 8787,
    "access_token": "",
    "db_path": "data/moneybook.db",
    "inbox_dir": "data/inbox",
    "archive_dir": "data/inbox/done",
    "sync": {
        "interval_hours": 3,
        "on_start": True,
        "watch_inbox": True,
        "pull_urls": [],
    },
    "reports": {
        "daily_times": ["12:00"],
        "daily_mode": "since_last",
        "push_daily": True,
        "weekly": {"enabled": True, "weekday": 1, "time": "09:00"},
        "monthly": {"enabled": True, "day": 1, "time": "09:00"},
    },
    "budget": {"monthly": 0, "categories": {}},
    "alerts": {"large_amount": 0, "budget_percent": 80},
    "notify": {"channels": []},
    "categorize": {"auto": True, "learn_from_manual": True},
    "privacy": {"store_raw": True, "mask_account": True},
}

_ENV_MAP = {
    "MONEYBOOK_PORT": "port",
    "MONEYBOOK_HOST": "host",
    "MONEYBOOK_TOKEN": "access_token",
    "MONEYBOOK_DB": "db_path",
    "MONEYBOOK_INBOX": "inbox_dir",
}


def project_root():
    """项目根目录（src/moneybook/config.py -> 上溯三级）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get(cfg, path, default=None):
    """按 'reports.daily_times' 取值（实现在 util.get）。"""
    from .util import get as _get
    return _get(cfg, path, default)


def set_path(cfg, path, value):
    parts = path.split(".")
    cur = cfg
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
    return cfg


def config_path():
    return os.environ.get("MONEYBOOK_CONFIG") or os.path.join(project_root(), "config.json")


def load_config(create_if_missing=True):
    path = config_path()
    user = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f) or {}
        except (OSError, ValueError) as exc:
            print("[config] 读取失败，使用默认配置：%s" % exc)
    cfg = _deep_merge(DEFAULT_CONFIG, user)
    for env_key, cfg_key in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val not in (None, ""):
            if cfg_key == "port":
                try:
                    val = int(val)
                except ValueError:
                    continue
            cfg[cfg_key] = val
    if create_if_missing and not os.path.exists(path):
        save_config(cfg, path)
    return cfg


def save_config(cfg, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def resolve(cfg, key, default=""):
    """把配置里的相对路径解析为项目根下的绝对路径。"""
    val = cfg.get(key) or default
    if not val:
        return ""
    if os.path.isabs(val):
        return val
    return os.path.abspath(os.path.join(project_root(), val))
