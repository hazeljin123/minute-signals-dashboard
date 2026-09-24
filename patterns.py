# -*- coding: utf-8 -*-
"""
量价与形态识别
所有函数返回 (direction, text, strength)
  direction: +1 看多 / -1 看空 / 0 中性
  strength : 0~3 强度
"""


def _body(b):
    return abs(b["c"] - b["o"])


def _range(b):
    return max(b["h"] - b["l"], 1e-9)


def _upper_shadow(b):
    return b["h"] - max(b["o"], b["c"])


def _lower_shadow(b):
    return min(b["o"], b["c"]) - b["l"]


def detect_pin_bar(bars, i):
    """
    单根 Pin Bar（长影线反转形态）
    下影线 > 实体 *2 且 > 全幅 60% → 锤子线，看多
    上影线 > 实体 *2 且 > 全幅 60% → 射击之星，看空
    """
    if i < 1:
        return 0, "", 0
    b = bars[i]
    rng = _range(b)
    body = _body(b)
    if body <= 0:
        body = rng * 0.01  # 处理一字板
    us, ls = _upper_shadow(b), _lower_shadow(b)

    if ls > body * 2 and ls > rng * 0.6:
        return 1, "长下影线（锤子线），下方承接强", 2
    if us > body * 2 and us > rng * 0.6:
        return -1, "长上影线（射击之星），上方抛压重", 2
    return 0, "", 0


def detect_engulfing(bars, i):
    """吞没形态"""
    if i < 1:
        return 0, "", 0
    p, c = bars[i - 1], bars[i]
    pb, cb = _body(p), _body(c)
    if pb <= 0:
        return 0, "", 0
    # 阳包阴
    if p["c"] < p["o"] and c["c"] > c["o"] and c["c"] > p["o"] and c["o"] < p["c"] and cb > pb:
        return 1, "阳包阴吞没形态", 2
    # 阴包阳
    if p["c"] > p["o"] and c["c"] < c["o"] and c["o"] > p["c"] and c["c"] < p["o"] and cb > pb:
        return -1, "阴包阳吞没形态", 2
    return 0, "", 0


def detect_breakout(bars, i, lookback=20):
    """
    突破前高 / 跌破前低（带量能确认）
    """
    if i < lookback + 1:
        return 0, "", 0
    c = bars[i]["c"]
    ref_high = max(b["h"] for b in bars[i - lookback:i])
    ref_low = min(b["l"] for b in bars[i - lookback:i])
    vols = [b["v"] for b in bars[max(0, i - 20):i]]
    avg_v = sum(vols) / len(vols) if vols else 0
    vol_ratio = bars[i]["v"] / avg_v if avg_v > 0 else 1.0

    if c > ref_high:
        s = 3 if vol_ratio >= 1.5 else 2
        tag = "放量" if vol_ratio >= 1.5 else "缩量"
        return 1, f"{tag}突破前{lookback}根高点（量比{vol_ratio:.1f}）", s
    if c < ref_low:
        s = 3 if vol_ratio >= 1.5 else 2
        tag = "放量" if vol_ratio >= 1.5 else "缩量"
        return -1, f"{tag}跌破前{lookback}根低点（量比{vol_ratio:.1f}）", s
    return 0, "", 0


def detect_double(bars, i, span=60, tol=0.004):
    """
    双底（W底）/ 双顶（M头）近似识别
    在最近 span 根里找两个相近的极值点，中间有明确反弹/回落
    """
    if i < span:
        return 0, "", 0
    seg = bars[i - span:i + 1]
    n = len(seg)
    # 找局部低点
    lows_idx = [k for k in range(2, n - 2)
                if seg[k]["l"] <= seg[k - 1]["l"] and seg[k]["l"] <= seg[k + 1]["l"]
                and seg[k]["l"] <= seg[k - 2]["l"] and seg[k]["l"] <= seg[k + 2]["l"]]
    highs_idx = [k for k in range(2, n - 2)
                 if seg[k]["h"] >= seg[k - 1]["h"] and seg[k]["h"] >= seg[k + 1]["h"]
                 and seg[k]["h"] >= seg[k - 2]["h"] and seg[k]["h"] >= seg[k + 2]["h"]]

    # 双底：两个相近低点，中间高点明显
    if len(lows_idx) >= 2:
        for a in range(len(lows_idx) - 1):
            for b in range(a + 1, len(lows_idx)):
                l1, l2 = lows_idx[a], lows_idx[b]
                if l2 - l1 < 5:
                    continue
                p1, p2 = seg[l1]["l"], seg[l2]["l"]
                if abs(p1 - p2) / max(p1, 1e-9) < tol:
                    mid_high = max(x["h"] for x in seg[l1:l2 + 1])
                    if mid_high > max(p1, p2) * (1 + tol * 2):
                        if seg[-1]["c"] > p2:
                            return 1, f"双底（W底）形态，颈线 {mid_high:.1f}", 3

    # 双顶
    if len(highs_idx) >= 2:
        for a in range(len(highs_idx) - 1):
            for b in range(a + 1, len(highs_idx)):
                h1, h2 = highs_idx[a], highs_idx[b]
                if h2 - h1 < 5:
                    continue
                p1, p2 = seg[h1]["h"], seg[h2]["h"]
                if abs(p1 - p2) / max(p1, 1e-9) < tol:
                    mid_low = min(x["l"] for x in seg[h1:h2 + 1])
                    if mid_low < min(p1, p2) * (1 - tol * 2):
                        if seg[-1]["c"] < p2:
                            return -1, f"双顶（M头）形态，颈线 {mid_low:.1f}", 3
    return 0, "", 0


def detect_volume_price(bars, ind, i):
    """
    量价配合关系
    价涨量增=健康；价涨量缩=背离预警；价跌量增=恐慌；价跌量缩=缩量回调
    """
    if i < 6 or ind is None:
        return 0, "", 0
    vm = ind["vol_ma5"][i - 1]
    if not vm:
        return 0, "", 0
    vr = bars[i]["v"] / vm if vm > 0 else 1.0
    up = bars[i]["c"] > bars[i - 1]["c"]

    if up and vr >= 1.5:
        return 1, f"价涨量增，量比{vr:.1f}，上攻有力", 1
    if up and vr <= 0.7:
        return -1, f"价涨量缩，量比{vr:.1f}，动能不足", 1
    if not up and vr <= 0.7:
        return 1, f"缩量回调，量比{vr:.1f}，抛压有限", 1
    if not up and vr >= 1.5:
        return -1, f"价跌量增，量比{vr:.1f}，抛压明显", 1
    return 0, "", 0


def detect_all(bars, ind, i=None):
    """汇总所有形态信号，返回 list[(dir, text, strength)]"""
    if i is None:
        i = len(bars) - 1
    res = []
    for fn in (detect_pin_bar, detect_engulfing):
        r = fn(bars, i)
        if r[0] != 0:
            res.append(r)
    r = detect_breakout(bars, i)
    if r[0] != 0:
        res.append(r)
    r = detect_double(bars, i)
    if r[0] != 0:
        res.append(r)
    r = detect_volume_price(bars, ind, i)
    if r[0] != 0:
        res.append(r)
    return res
