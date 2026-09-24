# -*- coding: utf-8 -*-
"""
技术指标计算 —— 纯 Python 实现，无第三方依赖
所有函数输入为 float 列表，输出为等长列表（不足处补 None）
"""


def sma(values, n):
    """简单移动平均"""
    out = [None] * len(values)
    if n <= 0:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values, n):
    """指数移动平均"""
    out = [None] * len(values)
    if not values or n <= 0:
        return out
    k = 2.0 / (n + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """
    返回 (dif, dea, hist)
    dif = EMA(fast) - EMA(slow)
    dea = EMA(dif, signal)
    hist = (dif - dea) * 2   —— 与国内软件（文华/通达信）口径一致
    """
    ef = ema(closes, fast)
    es = ema(closes, slow)
    dif = [None if (a is None or b is None) else a - b for a, b in zip(ef, es)]
    valid = [x for x in dif if x is not None]
    dea_valid = ema(valid, signal) if valid else []
    dea = [None] * len(dif)
    j = 0
    for i, x in enumerate(dif):
        if x is not None:
            dea[i] = dea_valid[j]
            j += 1
    hist = [None if (a is None or b is None) else (a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, hist


def rsi(closes, n=14):
    """Wilder RSI"""
    out = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0.0
        l = -d if d < 0 else 0.0
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def boll(closes, n=20, k=2.0):
    """布林带，返回 (mid, upper, lower)"""
    mid = sma(closes, n)
    up = [None] * len(closes)
    lo = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is None:
            continue
        window = closes[i - n + 1:i + 1]
        mean = mid[i]
        var = sum((x - mean) ** 2 for x in window) / n
        sd = var ** 0.5
        up[i] = mean + k * sd
        lo[i] = mean - k * sd
    return mid, up, lo


def atr(highs, lows, closes, n=14):
    """平均真实波幅，用于止损位计算"""
    trs = [None] * len(closes)
    for i in range(len(closes)):
        if i == 0:
            trs[i] = highs[i] - lows[i]
        else:
            trs[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    return sma(trs, n)


def cross_up(a, b, i):
    """a 在 i 处上穿 b"""
    if i < 1 or a[i] is None or b[i] is None or a[i - 1] is None or b[i - 1] is None:
        return False
    return a[i - 1] <= b[i - 1] and a[i] > b[i]


def cross_down(a, b, i):
    """a 在 i 处下穿 b"""
    if i < 1 or a[i] is None or b[i] is None or a[i - 1] is None or b[i - 1] is None:
        return False
    return a[i - 1] >= b[i - 1] and a[i] < b[i]


def compute_all(bars):
    """
    对一组 K 线计算全部指标，返回 dict。
    bars: list[dict] 含 o/h/l/c/v
    """
    if not bars:
        return None
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    vols = [b["v"] for b in bars]

    dif, dea, hist = macd(closes)
    mid, up, lo = boll(closes)
    return {
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "vols": vols,
        "ma5": sma(closes, 5),
        "ma10": sma(closes, 10),
        "ma20": sma(closes, 20),
        "ma60": sma(closes, 60),
        "dif": dif,
        "dea": dea,
        "hist": hist,
        "rsi": rsi(closes, 14),
        "boll_mid": mid,
        "boll_up": up,
        "boll_lo": lo,
        "atr": atr(highs, lows, closes, 14),
        "vol_ma5": sma(vols, 5),
    }
