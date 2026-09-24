# -*- coding: utf-8 -*-
"""
看板生成器：抓取 -> 计算信号 -> 生成自包含 dashboard.html
直接运行即可刷新看板。
"""
import json
import os
import sys
from datetime import datetime

import fetch_data
import signal_engine
import longterm
import interprice as interprice_mod
from signal_engine import escalation_signal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(BASE_DIR, "dashboard.html")
OUT_DATA = os.path.join(BASE_DIR, "dashboard.data.json")   # 供前端热更新
TEMPLATE = os.path.join(BASE_DIR, "template.html")

# 看板展示所需的最大 K 线根数
# 六个周期同屏（1/5/15 分钟 + 日/周/月线），每图较窄，根数不宜过多，
# 否则 K 线糊成一片。日线及以上本身根数不多，但历史太长的图表会看不出近端细节，
# 所以也统一截断。
#
# 注意：30 分钟已按用户要求从所有页面撤下（六图 = 1/5/15 分钟 + 日/周/月）。
# 30 分钟的数据仍然照抓不误 —— 它是最长周期的分钟线，价差的结构判断
# 与单合约的多周期共振评分都要用到，只是不再单独占一张图。
MAX_BARS = {"1": 160, "5": 160, "15": 140, "daily": 120, "weekly": 90, "monthly": 60}

# 页面上呈现的六个图表周期（顺序即前端展示顺序）
CHART_TFS = ("1", "5", "15", "daily", "weekly", "monthly")

# 参与的短周期（不含 30）
SHORT_TFS = ("1", "5", "15")

# 长周期图表用哪种时间格式（日线及以上 → 只显示日期）
LONG_TF = ("daily", "weekly", "monthly")


def _short_t(t, tf):
    """
    压缩时间字段。
    分钟线：'2026-09-24 13:45:00' -> '09-24 13:45'
    长周期：'2026-09-24'          -> '26-09-24'
    不能一律切 [5:16]，否则日线会被切得乱七八糟。
    """
    s = str(t or "")
    if tf in LONG_TF:
        # 日线原始格式 YYYY-MM-DD
        return s[2:] if len(s) >= 10 else s
    return s[5:16] if len(s) >= 16 else s


def slim_bars(bars, n, tf="1"):
    """截取最近 n 根，只保留前端需要的字段，压缩体积
    bars 可能已带 sig 字段（买卖点标记）
    """
    b = bars[-n:] if len(bars) > n else bars
    out = []
    for x in b:
        item = {"t": _short_t(x["t"], tf), "o": x["o"], "h": x["h"], "l": x["l"],
                "c": x["c"], "v": x.get("v") or 0}
        if x.get("sig"):
            item["sig"] = x["sig"]
        out.append(item)
    return out


def _chart_bundle(bars, n, tf, want_macd=False, min_bars=40):
    """
    把一段 K 线做成前端画图需要的包（截断 + 指标 + 买卖点标注）。

    抽出成独立函数是为了让「品种页」与「价差页」共用同一套图表口径 ——
    两边都是六个周期，差别只在数据来源，画法必须一致，
    否则同一个看板上两种 K 线会有一点点不一样，看着很别扭。

    want_macd: 品种页保留 MACD 序列（副图），价差页只画均线与 RSI（格子更窄）。
    min_bars : 最少根数，低于此值不画这张图。
               默认 40 保证均线/MACD 有意义；但新上市合约的月线
               可能只有个位数，此时调用方可传更小的值（如 4）。
    """
    if not bars or len(bars) < min_bars:
        return None
    ind = interprice_mod.I.compute_all(bars) if not want_macd else None
    if ind is None:
        import indicators as _I
        ind = _I.compute_all(bars)
    marked = signal_engine.mark_signal_points(bars, ind, lookback=n)
    out = {
        "bars": slim_bars(marked, n, tf),
        "ind": {
            "ma5": [round(x, 2) if x is not None else None for x in ind["ma5"][-n:]],
            "ma20": [round(x, 2) if x is not None else None for x in ind["ma20"][-n:]],
            "rsi": [round(x, 1) if x is not None else None for x in ind["rsi"][-n:]],
        },
    }
    if want_macd:
        out["ind"].update({
            "dif": [round(x, 3) if x is not None else None for x in ind["dif"][-n:]],
            "dea": [round(x, 3) if x is not None else None for x in ind["dea"][-n:]],
            "hist": [round(x, 3) if x is not None else None for x in ind["hist"][-n:]],
        })
    return out


def build_symbol_page(market_data, sym, sig):
    """
    构造单个「品种页」（kind=symbol）。

    图表六个：1/5/15 分钟 + 日/周/月线（30 分钟已撤下）。
    长周期用 longterm 模块分析真实的日/周/月线。
    """
    entry = market_data["symbols"][sym]

    bars = {}
    for tf in CHART_TFS:
        if tf in LONG_TF:
            src = entry.get(tf) or []
            n = MAX_BARS[tf]
        else:
            src = entry["periods"].get(tf)
            n = MAX_BARS[tf]
        bundle = _chart_bundle(src, n, tf, want_macd=(tf in SHORT_TFS))
        if bundle:
            bars[tf] = bundle

    q = entry.get("quote") or {}
    return {
        "kind": "symbol",
        "code": sym,
        "name": sig.get("name", sym),
        "verdict": sig["verdict"],
        "level": sig["level"],
        "total_score": round(sig["total_score"], 1),
        "raw_total_score": round(sig.get("raw_total_score", 0), 1),
        "divergence_corrected": sig.get("divergence_corrected", False),
        "pos_periods": sig["pos_periods"],
        "neg_periods": sig["neg_periods"],
        "current_price": sig.get("current_price") or q.get("last"),
        "stop_loss": sig.get("stop_loss"),
        "target": sig.get("target"),
        "risk_reward": sig.get("risk_reward"),
        "main_tf": sig.get("main_tf"),
        "quote": q,
        "periods": sig["periods"],
        "bars": bars,
        "escalation": sig.get("escalation"),
        "longterm": longterm.long_term_analysis(
            entry.get("daily"), entry.get("weekly"), entry.get("monthly")
        ),
    }


def build_interprice_page(market_data, pair):
    """
    构造单个「跨品种价差页」（kind=interprice）。

    与品种页的区别：
      - 标题是「前腿 − 后腿」（如 豆油 − 菜油），代码是 Y0-OI0；
      - 所有 K 线都是「价差序列」而不是价格序列：
          分钟线：两腿分钟线按时间戳对齐后逐根相减
          日线  ：两腿日线对齐相减
          周/月线：由价差日线再聚合（先减后聚，不能反过来）
      - 长周期独立分析「价差自身」的日/周/月线，而不是两条腿各自的走势；
      - 递进提示只看 1/5/15（30 分钟只参与结构判断与总分）。

    数据来源：两条腿都在 WATCH_LIST 里，分钟线与日线 fetch 阶段已抓，
    本函数零额外网络请求。
    """
    fe = (market_data.get("symbols") or {}).get(pair["front"]) or {}
    be = (market_data.get("symbols") or {}).get(pair["back"]) or {}
    fper, bper = fe.get("periods") or {}, be.get("periods") or {}
    if not fper or not bper:
        return None

    kind = pair.get("kind", "inter")

    # ---- 分钟线价差 ----
    period_bars = {}
    for pt in interprice_mod.SPREAD_PERIODS:
        s = interprice_mod.align_pair(fper.get(pt), bper.get(pt))
        if s and len(s) >= 30:
            period_bars[pt] = s
    if not period_bars:
        return None

    analysis = interprice_mod.analyze_spread(period_bars, pair["label"], kind)
    if not analysis:
        return None

    # ---- 长周期价差（日/周/月）----
    long_bars = interprice_mod.build_long_periods(
        fe.get("daily") or [], be.get("daily") or []
    )

    # ---- 六张图 ----
    bars = {}
    for tf in CHART_TFS:
        if tf in LONG_TF:
            src = long_bars.get(tf) or []
        else:
            src = period_bars.get(tf)
        bundle = _chart_bundle(src, MAX_BARS[tf], tf)
        if bundle:
            bars[tf] = bundle

    # ---- 长周期总结（分析价差本身）----
    lt = {}
    if long_bars:
        lt = longterm.long_term_analysis(
            long_bars.get("daily"), long_bars.get("weekly"), long_bars.get("monthly")
        )

    # ---- 递进提示（价差版，只看 1/5/15）----
    esc = escalation_signal(analysis.get("periods") or {}, interprice_mod.SPREAD_WEIGHT)

    return {
        "kind": "interprice",
        "code": f"{pair['front']}-{pair['back']}",
        "name": pair["label"],
        "label": analysis["label"],
        "front": pair["front"],
        "back": pair["back"],
        "front_name": fe.get("name", pair["front"]),
        "back_name": be.get("name", pair["back"]),
        "verdict": analysis["verdict"],
        "level": analysis["level"],
        "kind_tag": "inter",
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
    }


def build_pages(market_data, signals):
    """
    统一页面列表：7 个品种页 + 4 个跨品种价差页。
    价差页接在品种页之后（用户要求「接在最顶上的生猪的后面」）。
    """
    pages = []
    for sym, sig in signals.items():
        page = build_symbol_page(market_data, sym, sig)
        if page:
            pages.append(page)
    for pair in fetch_data.INTERPRICE_LIST:
        page = build_interprice_page(market_data, pair)
        if page:
            pages.append(page)
    return pages


def build_payload(market_data, signals):
    """构造前端数据包（统一 pages 数组，kind 区分品种页/价差页）"""
    return {
        "generated_at": fetch_data.now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "data_at": market_data.get("fetched_at", ""),
        "pages": build_pages(market_data, signals),
    }


def write_atomic(path, content):
    """
    原子写入：先写临时文件再替换目标文件。
    避免看板服务与手动刷新同时运行时，浏览器读到写了一半的 HTML。
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def main():
    symbols = sys.argv[1:] or None
    md = fetch_data.fetch_all(symbols)
    sigs = signal_engine.build_signals(md)
    payload = build_payload(md, sigs)

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()

    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("/*__DATA__*/null", data_json)

    write_atomic(OUT_HTML, html)
    # 同时输出纯 JSON，供页面热更新（避免整页刷新造成闪烁）
    write_atomic(OUT_DATA, data_json)

    print(f"\n[看板已生成] {OUT_HTML}")
    for p in payload["pages"]:
        esc = p.get("escalation") or {}
        lv = esc.get("level", 0)
        tag = f" [递进L{lv}]" if lv else ""
        if p["kind"] == "interprice":
            print(f"  [{p['kind']}] {p['code']} {p['name']}: {p['verdict']}{tag}")
        else:
            print(f"  [{p['kind']}] {p['code']} {p['name']}: {p['verdict']}{tag}")


if __name__ == "__main__":
    main()
