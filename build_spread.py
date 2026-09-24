# -*- coding: utf-8 -*-
"""
P2701-P2705 跨期月差 —— 独立版生成器

生成一个只含「P2701-P2705 月差」单页的看板，规格与主子看板的价差页一致：
    6 张图（1/5/15 分钟 + 日/周/月线）+ 递进提示 + 长周期总结 + 止损参考

与 build_dashboard 的关系：
    复用其 _chart_bundle / slim_bars / write_atomic 等工具，
    但不碰它的 pages 组装逻辑 —— 本文件产出的页面数组只有 1 项。
    这样主看板的 11 页完全不受影响。

产出：
    spread.html          自包含页面（数据内嵌）
    spread.data.json     纯数据（供前端热更新）
"""

import json
import os
import sys
from datetime import datetime

import fetch_data
import interprice as interprice_mod
import longterm
import build_dashboard as bd
from fetch_spread import SPREAD_CONFIG, fetch_all
from signal_engine import escalation_signal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(BASE_DIR, "spread.html")
OUT_DATA = os.path.join(BASE_DIR, "spread.data.json")
TEMPLATE = os.path.join(BASE_DIR, "template_spread.html")

from build_dashboard import MAX_BARS, CHART_TFS, LONG_TF

# 新上市合约的月线数据天然很少，这里放宽门槛。
# P2705 于 2026-05 上市，到 2026-09 只有约 5 根月线、18 根周线。
# 指标函数（sma/macd/rsi）对数据不足会返回 None，不会崩，
# 所以只要能画出 K 线就值得展示 —— 看趋势形态本身也有用。
MIN_LONG_BARS = 4       # 月线最少 4 根（约 4 个月）
MIN_WEEK_BARS = 8       # 周线最少 8 根（约 2 个月）
MIN_CHART_BARS = 4      # 单张图最少 4 根


def build_long_thresholds():
    """给 build_long_periods 用的门槛（比默认 35 宽松）。"""
    return {"min_bars": 20, "min_long": MIN_LONG_BARS}


def build_spread_page(market_data):
    """
    构造月差页面（kind=interprice，与主看板的价差页结构完全一致，
    这样前端模板不用改一行）。
    """
    cfg = market_data["spread"]
    front, back = cfg["front"], cfg["back"]
    fe = market_data["symbols"].get(front) or {}
    be = market_data["symbols"].get(back) or {}

    fper, bper = fe.get("periods") or {}, be.get("periods") or {}
    if not fper or not bper:
        return None

    kind = cfg.get("kind", "calendar")

    # ---- 分钟线价差 ----
    period_bars = {}
    for pt in interprice_mod.SPREAD_PERIODS:
        s = interprice_mod.align_pair(fper.get(pt), bper.get(pt))
        if s and len(s) >= 30:
            period_bars[pt] = s
    if not period_bars:
        return None

    analysis = interprice_mod.analyze_spread(period_bars, cfg["label"], kind)
    if not analysis:
        return None

    # ---- 长周期价差（日/周/月）----
    th = build_long_thresholds()
    long_bars = interprice_mod.build_long_periods(
        fe.get("daily") or [], be.get("daily") or [],
        min_bars=th["min_bars"], min_long=th["min_long"],
    )

    # ---- 六张图 ----
    bars = {}
    for tf in CHART_TFS:
        if tf in LONG_TF:
            src = long_bars.get(tf) or []
        else:
            src = period_bars.get(tf)
        bundle = bd._chart_bundle(src, MAX_BARS[tf], tf, min_bars=MIN_CHART_BARS)
        if bundle:
            bars[tf] = bundle

    # ---- 长周期总结（分析价差本身）----
    # 注意这里传 min_bars=MIN_LONG_BARS：新上市合约的月线只有 5 根，
    # 若用默认门槛 35，周/月线会被判为"无数据"，长周期总结只剩日线，白瞎。
    lt = {}
    if long_bars:
        lt = longterm.long_term_analysis(
            long_bars.get("daily"), long_bars.get("weekly"), long_bars.get("monthly"),
            min_bars=MIN_LONG_BARS,
        )

    # ---- 递进提示 ----
    esc = escalation_signal(analysis.get("periods") or {},
                            interprice_mod.SPREAD_WEIGHT)

    fq = fe.get("quote") or {}
    bq = be.get("quote") or {}

    return {
        "kind": "interprice",
        "code": f"{front}-{back}",
        "name": cfg["label"],
        "label": analysis["label"],
        "front": front,
        "back": back,
        "front_name": fe.get("name", cfg["front_name"]),
        "back_name": be.get("name", cfg["back_name"]),
        "verdict": analysis["verdict"],
        "level": analysis["level"],
        "kind_tag": kind,
        "total_score": analysis["total_score"],
        "pos_periods": analysis["pos_periods"],
        "neg_periods": analysis["neg_periods"],
        "current": analysis["current"],
        "conflict_note": analysis.get("conflict_note"),
        "summary": analysis.get("summary"),
        "structure": analysis.get("structure"),
        "strength": analysis.get("strength"),
        "periods": analysis["periods"],
        "bars": bars,
        "escalation": esc,
        "longterm": lt,
        # 月差专有：两条腿的实时价，方便页面直接显示「9806 − 10271 = −465」
        "legs": {
            "front_price": fq.get("last"),
            "back_price": bq.get("last"),
            "front_change": None,
            "back_change": None,
        },
    }


def build_payload(market_data):
    """构造前端数据包（只有一个页面）。"""
    pages = []
    page = build_spread_page(market_data)
    if page:
        pages.append(page)
    return {
        "generated_at": fetch_data.now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "data_at": market_data.get("fetched_at", ""),
        "single": True,          # 标记：单页模式，前端据此隐藏品种按钮条
        "pages": pages,
    }


def main():
    md = fetch_all()
    payload = build_payload(md)

    if not payload["pages"]:
        print("\n[错误] 未能生成月差页面 —— 两条腿的数据可能不完整。")
        return 1

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()

    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("/*__DATA__*/null", data_json)

    bd.write_atomic(OUT_HTML, html)
    bd.write_atomic(OUT_DATA, data_json)

    p = payload["pages"][0]
    print(f"\n[月差看板已生成] {OUT_HTML}")
    print(f"  {p['code']} {p['name']}: {p['verdict']}")
    tf_list = " ".join(sorted(p["bars"].keys()))
    print(f"  图表周期: {tf_list}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
