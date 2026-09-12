# -*- coding: utf-8 -*-
"""生成 App 图标（纯标准库手写 PNG，不依赖 Pillow）。

用法：python tools/make_icons.py
"""
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "src", "moneybook", "webui")

BG = (29, 42, 53)         # 靛墨
BG2 = (38, 52, 63)
FG = (252, 251, 246)      # 纸色
RULE = (176, 58, 46)      # 朱砂：底部那道合计线

# 「¥」字形点阵（1 = 上色）
GLYPH = [
    "1.......1",
    ".1.....1.",
    "..1...1..",
    "...1.1...",
    "....1....",
    "..11111..",
    "....1....",
    "..11111..",
    "....1....",
    "....1....",
    "....1....",
]


def png_bytes(width, height, pixels):
    """pixels: 每行 RGBA 字节串列表。"""
    raw = b"".join(b"\x00" + row for row in pixels)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render(size, radius_ratio=0.22):
    rows = []
    pad = int(size * 0.17)
    gw = len(GLYPH[0])
    gh = len(GLYPH)
    cell = min((size - pad * 2) // gw, (size - pad * 2) // gh)
    off_x = (size - cell * gw) // 2
    off_y = (size - cell * gh) // 2
    corner = size * radius_ratio
    for y in range(size):
        row = bytearray()
        for x in range(size):
            # 圆角矩形遮罩
            cx = min(x, size - 1 - x)
            cy = min(y, size - 1 - y)
            inside = True
            if cx < corner and cy < corner:
                dx = corner - cx
                dy = corner - cy
                inside = (dx * dx + dy * dy) <= corner * corner
            if not inside:
                row += bytes((0, 0, 0, 0))
                continue
            t = (x + y) / (2.0 * size)
            r = int(BG[0] * (1 - t) + BG2[0] * t)
            g = int(BG[1] * (1 - t) + BG2[1] * t)
            b = int(BG[2] * (1 - t) + BG2[2] * t)
            # 字形
            gx = (x - off_x) // cell
            gy = (y - off_y) // cell
            if 0 <= gx < gw and 0 <= gy < gh and GLYPH[gy][gx] == "1":
                r, g, b = FG
            # 底部一道朱砂横线（账本的合计线）
            bar_w = int(size * 0.34)
            bar_h = max(2, int(size * 0.032))
            bar_x = (size - bar_w) // 2
            bar_y = size - pad - bar_h
            if bar_x <= x < bar_x + bar_w and bar_y <= y < bar_y + bar_h:
                r, g, b = RULE
            row += bytes((r, g, b, 255))
        rows.append(bytes(row))
    return png_bytes(size, size, rows)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "icon-180.png"), (32, "favicon.png")):
        data = render(size)
        with open(os.path.join(OUT_DIR, name), "wb") as f:
            f.write(data)
        print("wrote %s (%d bytes)" % (name, len(data)))


if __name__ == "__main__":
    main()
