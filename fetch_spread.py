# -*- coding: utf-8 -*-
"""
P2701-P2705 跨期月差 —— 独立版数据抓取层

与主子看板的区别：
    主看板抓的是 7 个「主力连续」合约（P0、M0 …），
    本模块抓的是两个「具体月份合约」（P2701、P2705）。

为什么单独写而不复用 fetch_data：
    ① 合约列表不同（具体月份 vs 主力连续）；
    ② 月差只有一条腿的组合，不需要 7 品种 × 4 周期那套并发矩阵；
    ③ 独立成文件，主看板改动风险为零。

复用的部分（从 fetch_data 引入）：
    _curl / fetch_minline / fetch_daily / fetch_quote / merge_today_daily /
    aggregate —— 这些接口对「具体月份合约」同样有效，已实测验证。
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import fetch_data as fd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 月差配置
# ---------------------------------------------------------------------------
# 价差 = front - back，即「近月 - 远月」。
# 棕榈油 2701（2027年1月）比 2705（2027年5月）更近，
# 所以 front=P2701、back=P2705。
#
# 行情含义（跨期月差术语）：
#   价差变大 = 走扩 = 近月相对远月变强
#   价差变小 = 收敛 = 近月相对远月变弱
# 当前实测约 -465（近月贴水远月），属于远月升水结构。
SPREAD_CONFIG = {
    "label": "P2701-P2705月差",
    "front": "P2701",
    "back": "P2705",
    "kind": "calendar",          # 跨期月差
    "front_name": "棕榈油2701",
    "back_name": "棕榈油2705",
}

# 抓取的分钟周期（1/5/15 上图，30 只参与打分与结构判断）
PERIODS = ("1", "5", "15", "30")


def fetch_leg(symbol, verbose=True):
    """
    抓一条腿的全部数据：4 个分钟周期 + 实时行情 + 日线（含当日补齐）。
    """
    result = {
        "symbol": symbol,
        "periods": {},
        "quote": None,
        "daily": [],
    }

    tasks = [("minline", pt) for pt in PERIODS]
    tasks.append(("quote", None))
    tasks.append(("daily", None))

    def _run(task):
        kind, pt = task
        if kind == "minline":
            return task, fd.fetch_minline(symbol, pt)
        if kind == "quote":
            return task, fd.fetch_quote(symbol)
        return task, fd.fetch_daily(symbol)

    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futs = {ex.submit(_run, t): t for t in tasks}
        for fu in as_completed(futs):
            try:
                task, data = fu.result()
            except Exception as e:
                print(f"  [{symbol} 抓取失败] {futs[fu]}: {e}", file=sys.stderr)
                continue
            kind, pt = task
            if kind == "minline":
                result["periods"][pt] = data
            elif kind == "quote":
                result["quote"] = data
            else:
                result["daily"] = data

    # 补当日日K（与主看板同一逻辑：接口只返回已收盘日K，盘中缺当天）
    result["daily"] = fd.merge_today_daily(
        result.get("daily") or [], result.get("quote")
    )

    if verbose:
        q = result.get("quote") or {}
        parts = [f"{pt}m:{len(result['periods'].get(pt) or [])}" for pt in PERIODS]
        print(f"  [抓取] {symbol}: {' '.join(parts)}  "
              f"日线:{len(result['daily'])}  最新 {q.get('last', '-')}")

    return result


def fetch_all(verbose=True):
    """
    抓取月差两条腿的全部数据。

    返回结构刻意与主看板的 market_data 保持一致（symbols 字典），
    这样 build_spread 里可以直接复用 interprice 模块的分析函数。
    """
    t0 = datetime.now()

    front = fetch_leg(SPREAD_CONFIG["front"], verbose)
    back = fetch_leg(SPREAD_CONFIG["back"], verbose)

    result = {
        "fetched_at": fd.now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "spread": SPREAD_CONFIG,
        "symbols": {
            SPREAD_CONFIG["front"]: {
                "name": SPREAD_CONFIG["front_name"],
                "periods": front["periods"],
                "quote": front["quote"],
                "daily": front["daily"],
            },
            SPREAD_CONFIG["back"]: {
                "name": SPREAD_CONFIG["back_name"],
                "periods": back["periods"],
                "quote": back["quote"],
                "daily": back["daily"],
            },
        },
    }

    if verbose:
        elapsed = (datetime.now() - t0).total_seconds()
        f = result["symbols"][SPREAD_CONFIG["front"]]
        b = result["symbols"][SPREAD_CONFIG["back"]]
        fq, bq = f.get("quote") or {}, b.get("quote") or {}
        if fq.get("last") and bq.get("last"):
            print(f"  [月差] {SPREAD_CONFIG['label']}: "
                  f"{fq['last']:.0f} - {bq['last']:.0f} = {fq['last'] - bq['last']:.0f}")
        print(f"  [耗时] {elapsed:.1f} 秒")

    return result


if __name__ == "__main__":
    fetch_all()
