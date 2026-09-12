# -*- coding: utf-8 -*-
"""生成安卓 App 的启动图标（复用 make_icons 的 PNG 生成器）。

用法：python tools/make_android_icons.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_icons  # noqa: E402

RES = os.path.join(ROOT, "android", "app", "src", "main", "res")
SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def main():
    for dpi, size in SIZES.items():
        folder = os.path.join(RES, "mipmap-" + dpi)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "ic_launcher.png")
        with open(path, "wb") as f:
            f.write(make_icons.render(size))
        print("  %-10s %dpx" % ("mipmap-" + dpi, size))
    print("安卓图标已生成到 android/app/src/main/res/mipmap-*")


if __name__ == "__main__":
    main()
