# -*- coding: utf-8 -*-
"""python -m moneybook 入口。"""
import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main() or 0)
