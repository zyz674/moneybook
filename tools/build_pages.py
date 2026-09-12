# -*- coding: utf-8 -*-
"""把界面代码 + 纯前端后端组装成可直接静态托管的 web/ 目录（GitHub Pages 用）。

用法：python tools/build_pages.py
"""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
UI = os.path.join(ROOT, "src", "moneybook", "webui")
LOCAL = os.path.join(UI, "local")
OUT = os.path.join(ROOT, "web")

COPY = ["index.html", "style.css", "app.js", "manifest.webmanifest", "sw.js",
        "icon-180.png", "icon-192.png", "icon-512.png", "favicon.png"]
COPY_LOCAL = ["local-core.js", "local-api.js"]


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit("构建失败：找不到需要替换的内容（%s）" % label)
    return text.replace(old, new, 1)


def main():
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from moneybook import categorize

    os.makedirs(OUT, exist_ok=True)
    for name in COPY:
        shutil.copy2(os.path.join(UI, name), os.path.join(OUT, name))
    for name in COPY_LOCAL:
        shutil.copy2(os.path.join(LOCAL, name), os.path.join(OUT, name))

    # 规则库
    sys.path.insert(0, HERE)
    import export_rules
    rules = export_rules.build()
    with open(os.path.join(OUT, "rules.json"), "w", encoding="utf-8") as f:
        json.dump({"version": categorize.RULES_VERSION, "rules": rules}, f,
                  ensure_ascii=False, separators=(",", ":"))

    # index.html：绝对路径改成相对（项目页在 /<repo>/ 下），并注入本地后端
    path = os.path.join(OUT, "index.html")
    html = open(path, encoding="utf-8").read()
    for attr in ('href="', 'src="'):
        html = html.replace(attr + "/", attr)
    html = replace_once(html, '<script src="app.js"></script>',
                        '<script src="local-core.js"></script>\n<script src="local-api.js"></script>\n'
                        '<script src="app.js"></script>', "注入本地脚本")
    open(path, "w", encoding="utf-8").write(html)

    # app.js：Service Worker 用相对路径
    path = os.path.join(OUT, "app.js")
    js = open(path, encoding="utf-8").read()
    js = js.replace('register("/sw.js")', 'register("sw.js")')
    open(path, "w", encoding="utf-8").write(js)

    # sw.js：缓存列表改成相对路径
    path = os.path.join(OUT, "sw.js")
    sw = open(path, encoding="utf-8").read()
    sw = replace_once(sw, 'var SHELL = ["/", "/index.html", "/style.css", "/app.js", "/manifest.webmanifest", "/icon-192.png"];',
                      'var SHELL = ["./", "./index.html", "./style.css", "./app.js", "./local-core.js", '
                      '"./local-api.js", "./manifest.webmanifest", "./icon-192.png"];', "sw 缓存列表")
    open(path, "w", encoding="utf-8").write(sw)

    # manifest：相对路径
    path = os.path.join(OUT, "manifest.webmanifest")
    mf = json.load(open(path, encoding="utf-8"))
    mf["start_url"] = "./"
    mf["icons"] = [dict(i, src=i["src"].lstrip("/")) for i in mf.get("icons", [])]
    json.dump(mf, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 同步一份到安卓工程（WebView 直接读 assets）
    android_assets = os.path.join(ROOT, "android", "app", "src", "main", "assets", "www")
    if "--no-android" not in sys.argv:
        if os.path.isdir(android_assets):
            shutil.rmtree(android_assets)
        shutil.copytree(OUT, android_assets)
        print("已同步到安卓工程：android/app/src/main/assets/www")

    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print("web/ 构建完成：%d 个文件，%.0f KB" % (len(os.listdir(OUT)), total / 1024.0))
    for name in sorted(os.listdir(OUT)):
        print("   %-24s %6.1f KB" % (name, os.path.getsize(os.path.join(OUT, name)) / 1024.0))


if __name__ == "__main__":
    main()
