# -*- coding: utf-8 -*-
"""通知 / 短信流水解析。

数据来源（详见 docs/数据来源.md）：
  1) Android 通知监听 App：把「支付宝」「微信支付」「银行」的通知原文 POST 过来
  2) iOS 快捷指令 + 截图 OCR：把识别到的文字 POST 过来
  3) 银行短信转发（Android 短信监听 / 短信转发器）

本模块只做「文本 -> 流水」的无状态解析，时间与去重交给存储层。
"""
import re

from . import util

# ---------- 关键词 ----------
OUT_WORDS = ("支出", "消费", "付款成功", "支付成功", "已支付", "已付款", "扣款", "扣费", "已扣",
             "支付了", "支付", "扫码付", "付款", "消费支出", "代扣", "缴费成功", "支出人民币")
APP_NAME_WORDS = ("微信支付", "微信", "支付宝", "云闪付", "数字人民币")
IN_WORDS = ("收入", "收款成功", "已收款", "收款到账", "到账", "入账", "退款", "已退款", "退回",
            "收到", "转入", "红包到", "收到红包", "收款", "入账人民币",
            "工资", "发工资", "奖金", "报销", "进账", "到帐")

# 花出去但属于「自己账户之间搬钱」的：转账、还款、理财买入、提现等
NEUTRAL_OUT_WORDS = ("转账", "还款", "信用卡还款", "花呗还款", "借呗还款", "余额宝", "零钱通",
                     "理财", "基金", "定期", "提现", "零钱充值", "充值到零钱", "亲情卡",
                     "亲密付", "备用金", "自动充值", "转到银行卡")
# 收进来但同样只是自己账户搬钱的：提现到账、理财赎回等
SELF_TRANSFER_IN_WORDS = ("提现", "零钱提现", "余额宝", "零钱通", "理财", "基金", "定期", "赎回",
                          "充值到零钱")
NOISE_WORDS = ("验证码", "登录", "优惠券", "活动", "邀请", "更新", "广告", "推荐", "签到", "积分",
               "账单已出", "还款日", "额度提升", "安全提醒", "点击查看", "下载")

AMOUNT_PATTERNS = [
    r"[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    r"(?:金额|人民币|消费|支付|付款|收款|到账|收入|支出|扣款|扣费|退款|共计|合计)\s*[:：]?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元",
    r"([0-9][0-9,]*\.[0-9]{2})\s*元",
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元",
]

COUNTERPARTY_PATTERNS = [
    r"(?:向|给)\s*([^\s,，。;；:：]{2,24}?)\s*(?:支付|付款|转账|付款成功)",
    r"在\s*([^\s,，。;；:：]{2,24}?)\s*(?:消费|支付|购买|付款)",
    r"(?:商户|商家|收款方|付款方|交易对方|对方)\s*[:：]?\s*([^\s,，。;；]{2,24})",
    r"微信支付\s*[-—·]\s*([^\s\-—·]{2,24})",
    r"支付宝\s*[-—]\s*([^\s]{2,24})",
    r"【([^】]{2,24})】",
]

BANK_KEYS = ("银行", "储蓄卡", "信用卡", "借记卡", "尾号", "卡号", "账户", "余额", "人民币")
BANK_NAMES = ("工商", "建设", "农业", "中国银行", "招商", "交通银行", "邮储", "邮政", "民生",
              "光大", "中信", "浦发", "兴业", "平安银行", "华夏", "广发", "北京银行", "农商")


def detect_source(text, package=""):
    pkg = (package or "").lower()
    if "alipay" in pkg or "eg.android" in pkg:
        return "alipay"
    if "tencent.mm" in pkg or "wechat" in pkg or "micromsg" in pkg:
        return "wechat"
    if "支付宝" in text:
        return "alipay"
    if "微信" in text or "零钱" in text:
        return "wechat"
    if any(k in text for k in BANK_KEYS) and any(n in text for n in BANK_NAMES):
        return "bank"
    if any(k in text for k in BANK_KEYS):
        return "bank"
    return "other"


def extract_amount_cents(text):
    for pat in AMOUNT_PATTERNS:
        for m in re.finditer(pat, text):
            cents = util.parse_cents(m.group(1))
            if cents:
                return abs(cents)
    return None


def extract_direction(text):
    """判定收支方向。自己账户间的搬钱算「不计收支」，避免虚增消费。"""
    core = text
    for app in APP_NAME_WORDS:            # 「微信支付」里的「支付」不是收支信号
        core = core.replace(app, "")
    out_hit = any(w in core for w in OUT_WORDS)
    in_hit = any(w in core for w in IN_WORDS)
    if in_hit and not out_hit:
        return "in"
    if out_hit and not in_hit:
        direction = "out"
    elif in_hit and out_hit:
        # 例如「付款成功，退款到账」；以出现位置靠前者为准
        first_in = min((core.find(w) for w in IN_WORDS if w in core), default=10 ** 6)
        first_out = min((core.find(w) for w in OUT_WORDS if w in core), default=10 ** 6)
        direction = "in" if first_in < first_out else "out"
    elif re.search(r"[¥￥]", text):
        direction = "out"
    else:
        direction = None
    if direction == "out" and any(w in text for w in NEUTRAL_OUT_WORDS):
        return "neutral"
    if direction == "in" and any(w in text for w in SELF_TRANSFER_IN_WORDS):
        return "neutral"
    return direction


def extract_counterparty(text):
    for pat in COUNTERPARTY_PATTERNS:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip(" 　-—·:：")
            if name and not name.isdigit() and len(name) >= 2:
                return name
    return ""


def extract_account(text):
    m = re.search(r"(?:尾号|卡号|账号|账户)\s*([0-9]{3,6})", text)
    if m:
        return m.group(1)
    return ""


def is_noise(text):
    if any(w in text for w in NOISE_WORDS):
        return True
    return False


def guess_merchant(text, cents=0):
    """宽松模式：把数字和常见词去掉，剩下的当商户名（「8元 兰州拉面」-> 兰州拉面）。"""
    s = text
    for pat in AMOUNT_PATTERNS:
        s = re.sub(pat, " ", s, count=1)
    s = re.sub(r"[0-9]+(?:\.[0-9]{1,2})?", " ", s, count=1)
    for kw in ("微信支付", "支付宝", "已支付", "支付成功", "付款成功", "支付", "付款", "支出", "消费",
               "扫码", "收款", "到账", "收入", "金额", "人民币", "花了", "买了", "元", "块", "钱"):
        s = s.replace(kw, " ")
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", " ", s).strip()
    return s[:24] if 2 <= len(s) <= 24 else ""


def parse_notification(text, source=None, ts=None, package="", device="", loose=False):
    """把一条通知/短信解析成流水字典；不是交易则返回 (None, reason)。

    loose=True 用于「一键快记」：允许没有 App 关键字、自己手打金额（如「12.5 肯德基」）。
    """
    if not text or not str(text).strip():
        return None, "空文本"
    text = util.normalize_text_keep_amount(text)
    if is_noise(text):
        return None, "非交易通知（含噪音关键词）"

    cents = extract_amount_cents(text)
    if not cents and loose:
        m = re.search(r"[0-9]+(?:\.[0-9]{1,2})?", text)
        if m:
            cents = abs(util.parse_cents(m.group(0)) or 0)
    if not cents:
        return None, "未找到金额"
    direction = extract_direction(text)
    if direction is None:
        if not loose:
            return None, "无法判断收支方向"
        direction = "out"

    src = source or detect_source(text, package)
    if src == "other" and not loose and "微信" not in text and "支付宝" not in text:
        return None, "无法识别来源"

    cp = extract_counterparty(text)
    if not cp and loose:
        cp = guess_merchant(text, cents)
    if cp in ("支付宝", "微信", "微信支付", "支付宝支付", "云闪付"):
        cp = ""
    method = ""
    for kw in ("零钱", "余额宝", "花呗", "银行卡", "信用卡", "储蓄卡", "云闪付", "数字人民币", "零钱通"):
        if kw in text:
            method = kw
            break

    label = {"alipay": "支付宝通知", "wechat": "微信支付通知", "bank": "银行短信"}.get(src, "流水通知")
    item = cp or label
    rec = {
        "source": src,
        "origin": "notify",
        "ts": ts or util.now_str(),
        "amount_cents": cents,
        "direction": direction,
        "counterparty": cp,
        "item": item,
        "method": method,
        "status": "",
        "category_raw": label,
        "account": extract_account(text),
        "raw": text[:600],
        "device": device,
        "ext_id": "",
    }
    return rec, "ok"
