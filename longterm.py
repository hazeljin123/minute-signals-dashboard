# -*- coding: utf-8 -*-
"""
长周期（日线 / 周线 / 月线）技术状态分析
输出自然语言描述 + 结构化状态，用于看板顶部展示。
"""
import indicators as I


def _trend_desc(closes, ma5, ma20, ma60, i):
    """判断趋势方向"""
    c = closes[i]
    parts = []
    if ma5[i] and ma20[i]:
        if c > ma5[i] > ma20[i]:
            parts.append("短期多头")
        elif c < ma5[i] < ma20[i]:
            parts.append("短期空头")
    if ma20[i] and ma60[i]:
        if ma20[i] > ma60[i]:
            parts.append("中期偏多")
        else:
            parts.append("中期偏空")
    # 综合
    score = 0
    if ma5[i] and ma20[i]:
        score += 1 if ma5[i] > ma20[i] else -1
    if ma20[i] and ma60[i]:
        score += 1 if ma20[i] > ma60[i] else -1
    if score >= 2:
        return "上行趋势", parts
    if score <= -2:
        return "下行趋势", parts
    return "震荡整理", parts


def _position_desc(highs, lows, closes, i, lookback=60):
    """在近期区间中的位置"""
    if i < lookback:
        lookback = i
    if lookback < 5:
        return "数据不足", None
    hi = max(highs[i - lookback + 1:i + 1])
    lo = min(lows[i - lookback + 1:i + 1])
    c = closes[i]
    if hi == lo:
        return "区间极窄", 50
    pos = (c - lo) / (hi - lo) * 100
    if pos >= 80:
        return f"处于近{lookback}周期高位（{pos:.0f}%）", pos
    if pos <= 20:
        return f"处于近{lookback}周期低位（{pos:.0f}%）", pos
    return f"处于近{lookback}周期中枢（{pos:.0f}%）", pos


def analyze_timeframe(bars, label, min_bars=35):
    """
    分析单个长周期（日/周/月），返回描述性 dict。

    min_bars: 最少根数。默认 35 是为了让 MA20/MACD 有足够历史。
              但**新上市合约**的月线可能只有个位数根
              （如棕榈油 2705 于 2026-05 上市，到 9 月只有 5 根月线），
              此时调用方可下调门槛，否则该周期会被整块判为"无数据"。
              指标函数对数据不足会返回 None，不会崩，所以低门槛是安全的。
    """
    if not bars or len(bars) < min_bars:
        return None

    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    i = len(bars) - 1

    ma5 = I.sma(closes, 5)
    ma20 = I.sma(closes, 20)
    ma60 = I.sma(closes, 60)
    dif, dea, hist = I.macd(closes)
    rsi_v = I.rsi(closes, 14)[i]

    trend, tp = _trend_desc(closes, ma5, ma20, ma60, i)
    pos_txt, pos_val = _position_desc(highs, lows, closes, i)

    # MACD 状态
    macd_txt = "MACD 数据不足"
    if hist[i] is not None:
        if I.cross_up(dif, dea, i):
            macd_txt = "MACD 刚金叉"
        elif I.cross_down(dif, dea, i):
            macd_txt = "MACD 刚死叉"
        elif hist[i] > 0:
            growing = i >= 1 and hist[i - 1] is not None and hist[i] > hist[i - 1]
            macd_txt = "MACD 红柱，动能" + ("增强" if growing else "减弱")
        else:
            growing = i >= 1 and hist[i - 1] is not None and hist[i] < hist[i - 1]
            macd_txt = "MACD 绿柱，抛压" + ("加重" if growing else "减轻")

    # RSI 描述
    if rsi_v is None:
        rsi_txt = "RSI 数据不足"
    elif rsi_v >= 70:
        rsi_txt = f"RSI {rsi_v:.0f}（超买区）"
    elif rsi_v <= 30:
        rsi_txt = f"RSI {rsi_v:.0f}（超卖区）"
    elif rsi_v >= 55:
        rsi_txt = f"RSI {rsi_v:.0f}（偏强）"
    elif rsi_v <= 45:
        rsi_txt = f"RSI {rsi_v:.0f}（偏弱）"
    else:
        rsi_txt = f"RSI {rsi_v:.0f}（中性）"

    # 本周期涨跌
    chg = None
    if len(bars) >= 2:
        chg = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] else None

    # 方向倾向
    bias = 0
    if trend == "上行趋势":
        bias += 1
    elif trend == "下行趋势":
        bias -= 1
    if hist[i] is not None:
        bias += 1 if hist[i] > 0 else -1
    if rsi_v is not None:
        if rsi_v >= 55:
            bias += 1
        elif rsi_v <= 45:
            bias -= 1

    if bias >= 2:
        stance = "偏多"
    elif bias <= -2:
        stance = "偏空"
    else:
        stance = "中性"

    # 组装描述文本
    desc = f"{trend}，{pos_txt}；{macd_txt}，{rsi_txt}"

    return {
        "label": label,
        "date": bars[i]["t"],
        "close": closes[i],
        "trend": trend,
        "trend_parts": tp,
        "position": pos_txt,
        "position_val": pos_val,
        "macd": macd_txt,
        "rsi": rsi_txt,
        "rsi_val": rsi_v,
        "stance": stance,
        "bias": bias,
        "chg": chg,
        "desc": desc,
    }


def summarize(daily, weekly, monthly):
    """
    综合日/周/月三个周期，给出总结。
    返回 (summary_text, overall_stance, conflicts)
    """
    items = [x for x in (daily, weekly, monthly) if x]
    if not items:
        return "长周期数据不足，无法给出总结。", "未知", [], 0

    biases = {x["label"]: x["bias"] for x in items}
    d = biases.get("日线", 0)
    w = biases.get("周线", 0)
    m = biases.get("月线", 0)

    conflicts = []
    if d > 0 > w:
        conflicts.append("日线转强但周线仍弱")
    if d < 0 < w:
        conflicts.append("日线转弱但周线仍强")
    if w > 0 > m:
        conflicts.append("周线转强但月线仍弱")
    if w < 0 < m:
        conflicts.append("周线转弱但月线仍强")

    total = d * 2 + w * 3 + m * 2   # 周线权重最高

    if total >= 6:
        stance = "长周期共振偏多"
    elif total >= 3:
        stance = "长周期偏多"
    elif total <= -6:
        stance = "长周期共振偏空"
    elif total <= -3:
        stance = "长周期偏空"
    else:
        stance = "长周期多空交织，方向不明"

    # 组合总结文字
    def _s(x):
        return x["stance"] if x else "无数据"

    segs = [f"月线{_s(monthly)}", f"周线{_s(weekly)}", f"日线{_s(daily)}"]

    # 若三个周期都是中性，措辞不要显示为"共振偏多"之类
    if all((x is None or x["stance"] == "中性") for x in (daily, weekly, monthly)):
        txt = "长周期方向不明（月、周、日线均中性）"
        if conflicts:
            txt += f"。注意：{'；'.join(conflicts)}"
        return txt, "长周期方向不明", conflicts, total

    txt = f"{stance}（{'、'.join(segs)}）"
    if conflicts:
        txt += f"。注意：{'；'.join(conflicts)}"
    return txt, stance, conflicts, total


def long_term_analysis(daily, weekly, monthly, min_bars=35):
    """
    入口：返回包含三个周期分析与总结的完整结构。

    min_bars: 各周期的最少根数门槛，透传给 analyze_timeframe。
              新上市合约（周/月线根数少）可传更小的值。
    """
    d = analyze_timeframe(daily, "日线", min_bars)
    w = analyze_timeframe(weekly, "周线", min_bars)
    m = analyze_timeframe(monthly, "月线", min_bars)
    summary_txt, stance, conflicts, total = summarize(d, w, m)
    # 归一化多空强度：-1 ~ +1，供前端配色
    strength = max(-1.0, min(1.0, total / 10.0))
    return {
        "daily": d,
        "weekly": w,
        "monthly": m,
        "summary": summary_txt,
        "stancesummary": stance,
        "conflicts": conflicts,
        "score": total,
        "strength": round(strength, 2),
    }
