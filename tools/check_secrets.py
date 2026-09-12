# -*- coding: utf-8 -*-
"""提交前自查：扫描仓库里有没有密钥、隐私信息、不该提交的文件。

用法：python tools/check_secrets.py
退出码 0 表示没发现问题，1 表示有需要处理的命中。
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKIP_DIRS = {".git", "__pycache__", "tmp", "data", ".dsh", "node_modules", ".venv", "venv"}

PATTERNS = [
    ("疑似 API Key / Token",
     re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}"
                r"|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})")),
    ("疑似推送密钥",
     re.compile(r"(SCT[0-9]{3,}t?[A-Za-z0-9]{10,}|sctapi\.ftqq\.com/[A-Za-z0-9]{10,}"
                r"|api\.day\.app/[A-Za-z0-9]{12,})")),
    ("疑似赋值式密钥",
     re.compile(r"(?i)(api[_-]?key|secret|passwd|password|access[_-]?token|private[_-]?key)"
                r"\s*[:=]\s*[\x22\x27][^\x22\x27]{12,}[\x22\x27]")),
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Bearer / Bot Token",
     re.compile(r"(?i)(Bearer\s+[A-Za-z0-9._-]{20,}|bot[0-9]{8,}:[A-Za-z0-9_-]{30,})")),
    ("内网 IPv4（文档里请用示例地址）",
     re.compile(r"\b(?:192\.168|10|172\.(?:1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("邮箱地址", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("本机绝对路径", re.compile(r"[A-Za-z]:\\{1,2}(?:Users|deepseek harness|python|Git)\b")),
    ("11 位手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
]

# 允许出现的示例值 / 占位符
ALLOW = [
    r"192\.168\.1\.100", r"138\*\*\*\*8888",
    r"example\.com", r"api\.day\.app/你的KEY", r"sctapi\.ftqq\.com/%s\.send",
    r"你的口令", r"你的KEY", r"你的token", r"你的SendKey", r"你的chatid",
]

MUST_IGNORE = ["config.json", "data/moneybook.db", "data/server.log"]


def allowed(text):
    return any(re.search(a, text) for a in ALLOW)


def main():
    findings = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            scanned += 1
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for label, pat in PATTERNS:
                for m in pat.finditer(text):
                    line_no = text[:m.start()].count("\n") + 1
                    line = text.split("\n")[line_no - 1].strip()[:100]
                    if allowed(m.group(0)) or allowed(line):
                        continue
                    findings.append((rel, line_no, label, line))

    print("扫描 %d 个文件" % scanned)
    for rel, line_no, label, line in findings:
        print("  [%s] %s:%d\n      %s" % (label, rel, line_no, line))
    if not findings:
        print("没有发现密钥或隐私信息")

    print("\n隐私文件检查：")
    for rel in MUST_IGNORE:
        ignored = os.path.exists(os.path.join(ROOT, ".gitignore")) and rel.split("/")[0] in \
            open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
        print("  %s %s" % ("OK  " if ignored else "!!  ", rel))

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
