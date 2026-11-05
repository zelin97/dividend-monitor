#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股股息率自动监测 + 微信提醒（零成本云端定时版）

运行流程：
  1. 判断今天是否 A 股交易日（休市直接退出，不推送）
  2. 拉取全市场最新价（一次请求覆盖所有自选股，带备用数据源与重试）
  3. 计算股息率 = 近 12 个月每股现金分红(税前) ÷ 最新价 × 100%
     - 分红优先用 config.json 里的手工值
     - 未填则自动从 AKShare 分红明细估算，结果缓存 30 天
  4. 与设定上/下限比较，按「穿越阈值才提醒 + 重复提醒间隔」去重
  5. 有触发时合并成一条消息推送到微信
  6. 写回 state.json（去重状态、分红缓存）与 docs/latest.json（网页看板数据）

用法：
  python monitor.py --dry-run    # 本地试跑：只计算并打印，不推送、不写文件
  python monitor.py --force      # 忽略交易日判断强制执行（用于测试）
  python monitor.py              # 正常执行（GitHub Actions 调用）

环境变量（在 GitHub 仓库 Secrets 中配置，不要写进代码）：
  PUSHPLUS_TOKEN   PushPlus 的 token（channel = pushplus 时必填）
  SERVERCHAN_KEY   Server酱的 SendKey（channel = serverchan 时必填）
  WECOM_WEBHOOK    企业微信群机器人 Webhook（channel = wecom 时必填）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
BOARD_PATH = ROOT / "docs" / "latest.json"
BJ_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 基础工具

def log(msg: str) -> None:
    print(f"[{datetime.now(BJ_TZ):%H:%M:%S}] {msg}", flush=True)


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"读取 {path.name} 失败，改用默认值：{e}")
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def norm_code(v) -> str:
    """统一成 6 位数字代码，兼容 sh600519 / 600519.0 等写法。"""
    return "".join(ch for ch in str(v) if ch.isdigit()).zfill(6)[-6:]


def to_float(v):
    try:
        f = float(v)
        return f if f == f and f > 0 else None  # 排除 NaN 与非正数
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 数据获取

def is_trading_day(ak, today: datetime) -> bool:
    """优先查 AKShare 交易日历；接口异常时退化为工作日判断。"""
    try:
        df = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        days = {str(x)[:10] for x in df[col].tolist()}
        return today.strftime("%Y-%m-%d") in days
    except Exception as e:
        log(f"交易日历获取失败（{e}），退化为按周一至周五判断")
        return today.weekday() < 5


def _parse_spot(df, wanted: set[str]) -> dict[str, float]:
    """从行情表里提取 {6位代码: 最新价}，兼容不同版本的列名。"""
    code_col = next(
        (c for c in df.columns if str(c).strip() in ("代码", "股票代码", "code", "symbol")),
        None,
    )
    price_col = next(
        (c for c in df.columns if str(c).strip() in ("最新价", "现价", "收盘价", "最新价(元)", "price")),
        None,
    )
    if code_col is None or price_col is None:
        raise RuntimeError(f"行情表列名无法识别：{list(df.columns)[:15]}")
    out = {}
    for _, row in df.iterrows():
        code = norm_code(row[code_col])
        if code in wanted:
            p = to_float(row[price_col])
            if p:
                out[code] = p
    return out


def fetch_prices(ak, codes: list[str]) -> dict[str, float]:
    """一次拉全市场行情；主源东方财富，失败自动切新浪，共 4 次尝试。"""
    wanted = set(codes)
    sources = [
        ("东方财富", lambda: ak.stock_zh_a_spot_em()),
        ("新浪财经", lambda: ak.stock_zh_a_spot()),
    ]
    last_err = None
    for attempt in range(4):
        name, fn = sources[attempt % len(sources)]
        try:
            prices = _parse_spot(fn(), wanted)
            if prices:
                log(f"行情获取成功（{name}）：{len(prices)}/{len(wanted)} 只")
                return prices
            raise RuntimeError("返回数据为空或未匹配到自选股代码")
        except Exception as e:
            last_err = e
            log(f"第 {attempt + 1} 次获取行情失败（{name}）：{e}")
            time.sleep(3)
    raise RuntimeError(f"行情获取最终失败，请检查网络或升级 akshare：{last_err}")


def fetch_dividend_per_share(ak, code: str, today: datetime):
    """
    估算近 12 个月「已实施」的每股现金分红（元，税前）。

    两个接口返回的都是「从上市至今的全部分红」（工行有 23 条、2007 年起），
    且两个接口的日期排序方向相反，所以必须：
      1. 只保留「实施」状态（排除预案/股东大会通过但未派发的）
      2. 按除权除息日降序排列，只取最近 365 天
      3. 无论日期能否解析，都硬性最多取 4 次（A 股一年最多 4 次分红）
    「派息 / 派息比例」列单位是 元/10 股，需除以 10 换成 元/股。

    返回 (数值, None) 或 (None, 错误说明)。
    """
    import pandas as pd

    sources = (
        ("东财", lambda: ak.stock_history_dividend_detail(symbol=code, indicator="分红"),
         ("派息",), ("除权除息日", "公告日期")),
        ("巨潮", lambda: ak.stock_dividend_cninfo(symbol=code),
         ("派息比例", "派息"), ("除权日", "派息日", "实施方案公告日期")),
    )
    cutoff_dt = today - timedelta(days=365)  # 用 Python datetime，避开 pandas dtype 精度比较
    last_err = ""

    for label, fn, cash_hints, date_hints in sources:
        try:
            df = fn()
            if df is None or df.empty:
                continue

            cash_col = next((c for c in df.columns if any(h in str(c) for h in cash_hints)), None)
            date_col = next((c for c in df.columns if any(h in str(c) for h in date_hints)), None)
            if cash_col is None:
                continue

            # 只统计已实施的现金分红，未派发的预案不计入 TTM
            if "进度" in df.columns:
                df = df[df["进度"].astype(str).str.contains("实施", na=False)]
            if df.empty:
                continue

            cash_vals = [to_float(v) for v in df[cash_col]]
            date_vals = []
            if date_col:
                for x in df[date_col]:
                    try:
                        ts = pd.to_datetime(x)
                        date_vals.append(None if pd.isna(ts)
                                         else ts.to_pydatetime().replace(tzinfo=None))
                    except Exception:
                        date_vals.append(None)
            else:
                date_vals = [None] * len(df)

            records = [(d, c) for d, c in zip(date_vals, cash_vals) if c]
            dated = sorted((r for r in records if r[0] is not None),
                           key=lambda r: r[0], reverse=True)   # 最近的分红排最前
            undated = [r for r in records if r[0] is None]
            # 365 天内 + 最多 4 次，双重保险，杜绝把上市以来全部分红累加
            selected = [c for d, c in (dated + undated)
                        if d is None or d >= cutoff_dt][:4]

            vals = [c / 10.0 for c in selected]  # 元/10股 → 元/股
            if not vals:
                last_err = f"{label}：近 12 个月无现金分红记录"
                continue
            value = round(sum(vals), 4)
            if not (0.001 <= value <= 30):
                last_err = f"{label}：分红数值异常（{value} 元/股）"
                continue  # 明显不合理，换下一个数据源再试
            return value, None
        except Exception as e:
            last_err = f"{label}：{type(e).__name__} {e}"
            continue

    return None, f"分红数据未能解析（{last_err}），请手工填写 dividend"


def resolve_dividends(ak, cfg: dict, state: dict, today: datetime):
    """逐只确定每股分红：手工值 > 30 天内缓存 > 自动拉取。"""
    today_str = today.strftime("%Y-%m-%d")
    cache = state.setdefault("dividend_cache", {})
    result, notes = {}, []
    refresh_all = today_str.endswith("-01")  # 每月 1 号强制刷新一次

    for s in cfg.get("stocks", []):
        code = norm_code(s.get("code"))
        name = s.get("name") or code
        manual = to_float(s.get("dividend"))
        if manual:
            result[code] = (manual, "手工")
            continue

        c = cache.get(code)
        if c and not refresh_all:
            try:
                age = (today - datetime.strptime(c["date"], "%Y-%m-%d")).days
            except Exception:
                age = 999
            if age < 30:
                result[code] = (c["value"], "缓存")
                continue

        val, err = fetch_dividend_per_share(ak, code, today)
        if val:
            cache[code] = {"value": val, "date": today_str}
            result[code] = (val, "自动")
            log(f"{name} 自动获取分红：{val} 元/股")
        elif c:
            result[code] = (c["value"], "缓存(刷新失败)")
            notes.append(f"{name} 分红刷新失败，沿用缓存（{err}）")
        else:
            result[code] = (None, "缺少分红数据")
            notes.append(f"{name} 缺少分红数据（{err}），请在 config.json 里手工填写 dividend")

    return result, notes


# ---------------------------------------------------------------- 计算与去重

def evaluate(cfg: dict, prices: dict, dividends: dict, state: dict, today: datetime):
    today_str = today.strftime("%Y-%m-%d")
    prev_map = state.get("stocks", {})
    default_high = cfg.get("default_high")
    default_low = cfg.get("default_low")
    repeat_days = int(cfg.get("repeat_days", 7))
    rows, alerts, new_map = [], [], {}

    for s in cfg.get("stocks", []):
        code = norm_code(s.get("code"))
        name = s.get("name") or code
        div, div_src = dividends.get(code, (None, "缺少分红数据"))
        high = s.get("high", default_high)
        low = s.get("low", default_low)
        prev = prev_map.get(code, {})
        prev_zone = prev.get("zone", "normal")
        last_alert = prev.get("last_alert", "")

        row = {
            "code": code,
            "name": name,
            "dividend": div,
            "dividend_source": div_src,
            "high": high,
            "low": low,
            "price": prices.get(code),
            "yield": None,
            "zone": "normal",
            "status": "正常",
        }
        price = prices.get(code)

        if not div or not price:
            row["status"] = "缺少分红数据" if not div else "未取到行情"
            rows.append(row)
            new_map[code] = prev
            continue

        y = div / price * 100
        # 异常拦截：口径或单位出错时宁可跳过，也不发错误提醒
        if y > 25 or y < 0.05:
            row["status"] = f"数值异常({y:.2f}%)，请核对分红"
            rows.append(row)
            new_map[code] = prev
            log(f"{name} 股息率异常 {y:.2f}%，跳过（多半是分红单位不对）")
            continue

        zone = "normal"
        if high is not None and y >= float(high):
            zone = "high"
        elif low is not None and y <= float(low):
            zone = "low"

        row["yield"] = round(y, 2)
        row["price"] = round(price, 3)
        row["zone"] = zone
        row["status"] = {"high": "已达上限", "low": "已破下限", "normal": "正常"}[zone]

        should, reason = False, ""
        if zone != "normal":
            if zone != prev_zone:
                should, reason = True, "首次达到提醒线"
            else:
                try:
                    gap = (today - datetime.strptime(last_alert, "%Y-%m-%d")).days
                except Exception:
                    gap = repeat_days + 1
                if gap >= repeat_days:
                    should, reason = True, "持续处于提醒区间（定期复报）"

        if should:
            if zone == "high":
                text = f"股息率 **{y:.2f}%** ≥ 设定上限 **{high}%**，可关注"
            else:
                text = f"股息率 **{y:.2f}%** ≤ 设定下限 **{low}%**，可关注"
            alerts.append({
                "name": name, "code": code, "yield": y, "price": price,
                "zone": zone, "reason": reason, "text": text,
                "dividend": div, "dividend_source": div_src,
                "high": high, "low": low,
            })
            last_alert = today_str

        rows.append(row)
        new_map[code] = {"zone": zone, "last_alert": last_alert}

    return rows, alerts, new_map, today_str


# ---------------------------------------------------------------- 推送

def build_message(alerts: list, rows: list, cfg: dict, today_str: str, notes: list) -> str:
    lines = [f"## 股息率提醒 · {today_str}", ""]
    for a in alerts:
        lines += [
            f"### {a['name']}（{a['code']}）",
            a["text"],
            f"- 最新价：¥{a['price']:.2f}",
            f"- 近 12 个月每股分红（税前）：¥{a['dividend']:.4f}（来源：{a['dividend_source']}）",
            f"- 触发原因：{a['reason']}",
            "",
        ]
    lines += [
        "---",
        "### 全部自选股",
        "",
        "| 股票 | 现价 | 每股分红 | 股息率 | 状态 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for r in rows:
        y = f"{r['yield']:.2f}%" if r["yield"] is not None else "—"
        p = f"{r['price']:.2f}" if r["price"] else "—"
        d = f"{r['dividend']:.4f}" if r["dividend"] else "—"
        lines.append(f"| {r['name']} | {p} | {d} | {y} | {r['status']} |")

    lines += [
        "",
        "> 口径：近 12 个月每股现金分红（税前）÷ 最新价。",
        "> 数据仅供个人参考，不构成投资建议；分红方案变更后请核对 config.json。",
    ]
    if notes:
        lines += ["", "**数据提示**：" + "；".join(notes)]
    board = str(cfg.get("notify", {}).get("board_url", "")).strip()
    if board.startswith("http") and "你的用户名" not in board:
        lines += ["", f"[打开网页看板]({board})"]
    return "\n".join(lines)


def send(title: str, content: str, cfg: dict) -> None:
    ch = cfg.get("notify", {}).get("channel", "none")
    if ch == "none":
        log("推送渠道设为 none，跳过发送")
        return

    if ch == "pushplus":
        token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("缺少环境变量 PUSHPLUS_TOKEN")
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content, "template": "markdown"},
            timeout=25,
        )
        ok = r.ok and str(r.json().get("code")) == "200"

    elif ch == "serverchan":
        key = os.environ.get("SERVERCHAN_KEY", "").strip()
        if not key:
            raise RuntimeError("缺少环境变量 SERVERCHAN_KEY")
        r = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=25,
        )
        ok = r.ok and str(r.json().get("code")) == "0"

    elif ch == "wecom":
        hook = os.environ.get("WECOM_WEBHOOK", "").strip()
        if not hook:
            raise RuntimeError("缺少环境变量 WECOM_WEBHOOK")
        r = requests.post(
            hook,
            json={"msgtype": "markdown", "markdown": {"content": content[:3800]}},
            timeout=25,
        )
        ok = r.ok and r.json().get("errcode") == 0
    else:
        raise RuntimeError(f"未知推送渠道：{ch}")

    if not ok:
        raise RuntimeError(f"推送失败（{ch}）：HTTP {r.status_code} {r.text[:300]}")
    log(f"微信推送已发送（{ch}）")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="A股股息率监测提醒")
    ap.add_argument("--dry-run", action="store_true", help="只计算并打印，不推送、不写文件")
    ap.add_argument("--force", action="store_true", help="忽略交易日判断强制执行")
    args = ap.parse_args()

    cfg = read_json(CONFIG_PATH, {})
    stocks = cfg.get("stocks", [])
    if not stocks:
        log("config.json 中没有配置股票，退出")
        return 0

    # 统一使用「无时区的北京时间」，避免与分红日期、last_alert 比较时出现
    # offset-naive / offset-aware 的 TypeError
    today = datetime.now(BJ_TZ).replace(tzinfo=None)
    log(f"开始检查：{today:%Y-%m-%d %H:%M}（北京时间）")

    import akshare as ak

    if not (args.force or args.dry_run) and not is_trading_day(ak, today):
        log("今天非 A 股交易日，跳过本次检查")
        return 0

    codes = [norm_code(s.get("code")) for s in stocks]
    prices = fetch_prices(ak, codes)

    state = read_json(STATE_PATH, {"stocks": {}, "dividend_cache": {}})
    dividends, notes = resolve_dividends(ak, cfg, state, today)
    rows, alerts, new_map, today_str = evaluate(cfg, prices, dividends, state, today)

    log(f"计算完成，{len(alerts)} 只触发提醒：")
    for r in rows:
        y = f"{r['yield']:.2f}%" if r["yield"] is not None else "—"
        log(f"  {r['name']}({r['code']}) 现价={r['price']} 股息率={y} {r['status']}")

    title = f"股息率提醒：{'、'.join(a['name'] for a in alerts[:3])}"
    if len(alerts) > 3:
        title += "等"
    content = build_message(alerts, rows, cfg, today_str, notes)

    if args.dry_run:
        log("【试跑模式】未推送、未写文件。以下为将要发送的内容：")
        print("\n" + "=" * 50 + "\n" + content + "\n" + "=" * 50)
        return 0

    if alerts:
        send(title or "股息率提醒", content, cfg)
    else:
        log("本次无股票触发提醒，不推送（可在网页看板查看当前状态）")

    state["stocks"] = new_map
    write_json(STATE_PATH, state)
    write_json(BOARD_PATH, {
        "updated_at": today.strftime("%Y-%m-%d %H:%M"),
        "market_date": today_str,
        "basis": "近12个月每股现金分红(税前) ÷ 最新价",
        "alerts_today": [a["name"] for a in alerts],
        "stocks": rows,
    })
    log("state.json 与 docs/latest.json 已更新")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"运行失败：{exc}")
        sys.exit(1)
