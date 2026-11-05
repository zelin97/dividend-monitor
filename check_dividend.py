#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
辅助工具（只在本地手动运行，不参与定时任务）：
查询某只 A 股的历史分红，核对自动抓取的 dividend 是否准确。

用法：
  python tools/check_dividend.py 601398
  python tools/check_dividend.py            # 不带参数则交互输入
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak  # noqa: E402
from monitor import fetch_dividend_per_share, norm_code  # noqa: E402


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else input("请输入 6 位股票代码：")
    code = norm_code(raw)
    print(f"\n正在查询 {code} 的历史分红……\n")

    df = None
    for label, fn in (
        ("东财·历史分红", lambda: ak.stock_history_dividend_detail(symbol=code, indicator="分红")),
        ("巨潮·分红", lambda: ak.stock_dividend_cninfo(symbol=code)),
    ):
        try:
            d = fn()
            print(f"=== {label}（最近 8 条）===")
            print(d.head(8).to_string(index=False))
            print()
            if df is None and d is not None and not d.empty:
                df = d
        except Exception as e:
            print(f"=== {label} 获取失败：{e} ===\n")

    value, err = fetch_dividend_per_share(ak, code, datetime.now())
    if value:
        print(f"程序估算的「近 12 个月每股现金分红」= {value} 元/股（税前）")
        print(f"对应口径：约每 10 股派 {value * 10:.3f} 元")
    else:
        print(f"自动估算失败：{err}")

    print("\n请拿这个数字与 F10 / 年报「分配方案」核对。")
    print("若不一致，把正确数字填进 config.json 对应股票的 dividend 字段，程序会优先使用手工值。")


if __name__ == "__main__":
    main()
