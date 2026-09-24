# -*- coding: utf-8 -*-
"""
多周期共振信号引擎
设计原则（用户选定：宁缺勿滥）
  - 单周期信号不独立成信号，必须多周期共振
  - 加权打分：1min=1, 5min=2, 15min=3, 30min=2
  - 至少 2 个周期同向才输出信号
  - 高分阈值才算"强信号"，避免噪音
"""
import indicators as ind_mod
import patterns as pat_mod

# 周期权重
PERIOD_WEIGHT = {"1": 1, "5": 2, "15": 3, "30": 2, "60": 1}
PERIOD_LABEL = {"1": "1分钟", "5": "5分钟", "15": "15分钟", "30": "30分钟", "60": "60分钟"}

# 评分阈值（加权总分）—— 宁缺勿滥，强信号门槛提高
TH_STRONG_BUY = 9
TH_WEAK_BUY = 4
TH_WEAK_SELL = -4
TH_STRONG_SELL = -9

# 方向分歧惩罚：当存在明显反向周期时，削弱总分
# 解决"多周期互相打架却被加总成强信号"的漏洞
DIVERGENCE_PENALTY = 0.45


def _net_dir(scores, thresh=2):
    """一组分数合成方向：+1 多 / -1 空 / 0 中性"""
    s = sum(scores)
    if s >= thresh:
        return 1
    if s <= -thresh:
        return -1
    return 0


def escalation_signal(per_scores, weight=None, periods=None):
    """
    递进升级提示（用户核心需求）
    --------------------------------------------------
    看涨链路：
        1分钟 + 5分钟 均看涨          -> L1 初步看涨
        叠加 15分钟 也看涨            -> L2 强提示
        叠加 30分钟 也看涨            -> L3 最强提示
    看跌链路完全对称。

    判定"某周期看涨"：该周期原始分 >= 2（明确偏多）

    参数：
      per_scores: 单合约用 build_signals 产出的 periods（含 label/raw_score）；
                  价差用 analyze_spread 产出的 periods（同样含 raw_score）
      weight    : 该套周期的权重表。价差的 30 分钟权重是 3（看结构），
                  与单合约的 2 不同，由调用方传入以避免在这里写死。
                  仅用于下面判级别的细节；不传则退回 1/2/3/2。
      periods   : 参与递进的周期元组。价差页面上没有 30 分钟图，
                  但仍需要 30 分钟参与"最强提示"的判定，
                  所以默认仍是四周期；传三周期则最高只到 L2。

    返回 dict:
        {
          "side": "long"/"short"/None,
          "level": 0/1/2/3,
          "stages": {周期:bool},      # 各周期是否已确认
          "text": 提示文字,
          "progress": [ ... ]         # 递进步骤，供前端展示
        }
    """
    periods = tuple(periods or ("1", "5", "15", "30"))

    def bull(tf):
        v = per_scores.get(tf)
        return bool(v and v["raw_score"] >= 2)

    def bear(tf):
        v = per_scores.get(tf)
        return bool(v and v["raw_score"] <= -2)

    # ---- 看涨链路 ----
    b1, b5, b15, b30 = bull("1"), bull("5"), bull("15"), bull("30")
    s1, s5, s15, s30 = bear("1"), bear("5"), bear("15"), bear("30")

    long_stages = {"1": b1, "5": b5, "15": b15, "30": b30}
    short_stages = {"1": s1, "5": s5, "15": s15, "30": s30}

    def _eval(stages, side):
        """按递进规则算等级"""
        base = stages["1"] and stages["5"]
        if not base:
            return 0
        if not stages["15"]:
            return 1              # 1+5 确认，等 15 分钟
        if not stages["30"]:
            return 2              # 15 也确认 -> 强提示
        return 3                  # 30 也确认 -> 最强提示

    long_lv = _eval(long_stages, "long")
    short_lv = _eval(short_stages, "short")

    # 双向都有时，取级别更高的；同级则看哪个基础更扎实（15/30 得分绝对值）
    side, level = None, 0
    if long_lv > short_lv:
        side, level = "long", long_lv
    elif short_lv > long_lv:
        side, level = "short", short_lv
    elif long_lv > 0 and short_lv > 0:
        # 完全对称的对立，视为拉锯，不作为递进信号
        side, level = None, 0

    # 双向都成立时属于严重分歧：递进提示的前提是"共振"，不是"打架"
    if (b1 and b5) and (s1 and s5):
        return {
            "side": None, "level": 0,
            "stages": {"long": long_stages, "short": short_stages},
            "text": "1分钟与5分钟多空信号互相矛盾，视为拉锯，不构成递进提示",
            "progress": [],
        }

    # 短周期与长周期反向（如 1/5 看涨但 15/30 看跌）时，
    # 即使短周期已确认，也不应给出看涨递进提示 —— 那是逆长周期方向。
    if side == "long" and (s15 or s30):
        return {
            "side": None, "level": 0,
            "stages": {"long": long_stages, "short": short_stages},
            "text": "1分钟与5分钟看涨，但15/30分钟方向相反（短多长空），逆势不做递进提示",
            "progress": [],
        }
    if side == "short" and (b15 or b30):
        return {
            "side": None, "level": 0,
            "stages": {"long": long_stages, "short": short_stages},
            "text": "1分钟与5分钟看跌，但15/30分钟方向相反（短空长多），逆势不做递进提示",
            "progress": [],
        }

    if not side:
        # 检查是否处于"链条中途断掉"的状态，给出等待提示
        wait = _wait_hint(long_stages, short_stages)
        return {
            "side": None, "level": 0,
            "stages": {"long": long_stages, "short": short_stages},
            "text": wait, "progress": [],
        }

    stages = long_stages if side == "long" else short_stages
    word = "看涨" if side == "long" else "看跌"

    # 组装递进步骤
    progress = []
    for tf in periods:
        progress.append({"tf": f"{tf}分钟", "done": stages.get(tf, False)})

    if level == 1:
        text = (f"【初步{word}】1分钟与5分钟已同步{word}，"
                f"等待15分钟线确认")
    elif level == 2:
        text = (f"【强{word}提示】1分钟 + 5分钟 + 15分钟 三周期同步{word}，"
                f"共振成立。若30分钟线随后也{word}，将升级为最强提示")
    else:
        text = (f"【最强{word}提示】1分钟 + 5分钟 + 15分钟 + 30分钟 "
                f"四周期全线{word}，共振强度最高")

    return {
        "side": side, "level": level,
        "stages": {"long": long_stages, "short": short_stages},
        "text": text,
        "progress": progress,
    }


def _wait_hint(long_stages, short_stages):
    """链路中途断掉时的等待提示"""
    for stages, side in ((long_stages, "看涨"), (short_stages, "看跌")):
        if stages["1"] and stages["5"]:
            if not stages["15"]:
                return f"1分钟与5分钟已{side}，但15分钟线尚未确认，暂不发强提示"
        if stages["1"] and not stages["5"]:
            return f"仅1分钟线{side}，需5分钟线同步确认"
        if stages["5"] and not stages["1"]:
            return f"仅5分钟线{side}，需1分钟线同步确认"
    return "各周期未形成一致的递进信号"


def score_single_period(bars, ptype):
    """
    对单周期打分，返回 dict:
      {score, reasons:[str], indicators:{...}, last_close, atr}
    """
    if not bars or len(bars) < 30:
        return None

    ind = ind_mod.compute_all(bars)
    i = len(bars) - 1
    reasons = []
    score = 0

    c = ind["closes"][i]
    ma5, ma10, ma20, ma60 = ind["ma5"][i], ind["ma10"][i], ind["ma20"][i], ind["ma60"][i]
    dif, dea, hist = ind["dif"], ind["dea"], ind["hist"]
    rsi_v = ind["rsi"][i]
    bup, blo = ind["boll_up"][i], ind["boll_lo"][i]

    # ---- 1. 均线系统 ----
    if ma5 and ma10:
        if ind_mod.cross_up(ind["ma5"], ind["ma10"], i):
            score += 2
            reasons.append("MA5 上穿 MA10（金叉）")
        elif ind_mod.cross_down(ind["ma5"], ind["ma10"], i):
            score -= 2
            reasons.append("MA5 下穿 MA10（死叉）")
    if ma5 and ma10 and ma20 and ma60:
        if c > ma5 > ma10 > ma20 > ma60:
            score += 2
            reasons.append("均线多头排列")
        elif c < ma5 < ma10 < ma20 < ma60:
            score -= 2
            reasons.append("均线空头排列")

    # ---- 2. MACD ----
    if ind_mod.cross_up(dif, dea, i):
        score += 2
        reasons.append("MACD 金叉")
    elif ind_mod.cross_down(dif, dea, i):
        score -= 2
        reasons.append("MACD 死叉")
    # 柱状体方向变化
    if i >= 2 and hist[i] is not None and hist[i - 1] is not None:
        if hist[i] > 0 and hist[i] > hist[i - 1] and hist[i - 1] <= hist[i - 2]:
            score += 1
            reasons.append("MACD 红柱放大")
        elif hist[i] < 0 and hist[i] < hist[i - 1] and hist[i - 1] >= hist[i - 2]:
            score -= 1
            reasons.append("MACD 绿柱放大")
    # MACD 背离（近 40 根比价格极值与 MACD 极值）
    dv = _macd_divergence(ind, i, 40)
    if dv == 1:
        score += 2
        reasons.append("MACD 底背离（价新低 DIF 未新低）")
    elif dv == -1:
        score -= 2
        reasons.append("MACD 顶背离（价新高 DIF 未新高）")

    # ---- 3. RSI ----
    if rsi_v is not None:
        if rsi_v <= 25:
            score += 2
            reasons.append(f"RSI {rsi_v:.0f} 超卖")
        elif rsi_v <= 35:
            score += 1
            reasons.append(f"RSI {rsi_v:.0f} 偏低")
        elif rsi_v >= 75:
            score -= 2
            reasons.append(f"RSI {rsi_v:.0f} 超买")
        elif rsi_v >= 65:
            score -= 1
            reasons.append(f"RSI {rsi_v:.0f} 偏高")

    # ---- 4. 布林带 ----
    if bup and blo:
        if c <= blo:
            score += 2
            reasons.append("触及布林下轨")
        elif c >= bup:
            score -= 2
            reasons.append("触及布林上轨")

    # ---- 5. 形态识别 ----
    pats = pat_mod.detect_all(bars, ind, i)
    for d, txt, s in pats:
        score += d * s
        reasons.append(txt)

    return {
        "score": score,
        "reasons": reasons,
        "last_close": c,
        "atr": ind["atr"][i],
        "rsi": rsi_v,
        "ma20": ma20,
        "boll_up": bup,
        "boll_lo": blo,
        "bars": bars,
        "ind": ind,
        "patterns": pats,
    }


def _macd_divergence(ind, i, lookback):
    """简易 MACD 背离检测，返回 1 底背离 / -1 顶背离 / 0 无"""
    if i < lookback:
        return 0
    seg_l = ind["lows"][i - lookback:i + 1]
    seg_h = ind["highs"][i - lookback:i + 1]
    seg_diff = ind["dif"][i - lookback:i + 1]
    if any(x is None for x in seg_diff):
        return 0

    # 后 1/4 区间 vs 前 1/4 区间
    q = max(lookback // 4, 3)
    low_recent = min(seg_l[-q:])
    low_past = min(seg_l[:q])
    dif_recent_low = min(seg_diff[-q:])
    dif_past_low = min(seg_diff[:q])
    if low_recent < low_past and dif_recent_low > dif_past_low:
        return 1

    high_recent = max(seg_h[-q:])
    high_past = max(seg_h[:q])
    dif_recent_high = max(seg_diff[-q:])
    dif_past_high = max(seg_diff[:q])
    if high_recent > high_past and dif_recent_high < dif_past_high:
        return -1
    return 0


def generate_signal(period_bars):
    """
    period_bars: {"1": bars, "5": bars, "15": bars, "30": bars}
    返回完整信号 dict
    """
    per_scores = {}
    total = 0
    weighted_reasons = []

    for pt, bars in period_bars.items():
        r = score_single_period(bars, pt)
        if not r:
            continue
        w = PERIOD_WEIGHT.get(pt, 1)
        per_scores[pt] = {
            "label": PERIOD_LABEL.get(pt, pt + "分钟"),
            "raw_score": r["score"],
            "weighted": r["score"] * w,
            "weight": w,
            "reasons": r["reasons"],
            "last_close": r["last_close"],
            "rsi": r["rsi"],
            "atr": r["atr"],
            "ma20": r["ma20"],
            "boll_up": r["boll_up"],
            "boll_lo": r["boll_lo"],
            "score_detail": r,
        }
        total += r["score"] * w

    # 方向投票（至少 2 个周期同向）
    pos = sum(1 for v in per_scores.values() if v["raw_score"] >= 2)
    neg = sum(1 for v in per_scores.values() if v["raw_score"] <= -2)

    # ---- 分歧惩罚 ----
    # 若同时存在方向明确的看多与看空周期，说明多周期打架，
    # 直接加总会把"互相对冲"误判成强信号。此处按反向合力扣减。
    raw_total = total
    if pos >= 1 and neg >= 1:
        opp_sum = sum(v["weighted"] for v in per_scores.values()
                      if v["raw_score"] <= -2) if total > 0 else \
                  sum(v["weighted"] for v in per_scores.values() if v["raw_score"] >= 2)
        penalty = abs(opp_sum) * DIVERGENCE_PENALTY
        total = total - penalty if total > 0 else total + penalty

    # 一致性要求：主力周期（15/30）不得与结论反向
    main_dir = 0
    for key in ("15", "30"):
        if key in per_scores:
            sc = per_scores[key]["raw_score"]
            if sc >= 2:
                main_dir = 1
                break
            if sc <= -2:
                main_dir = -1
                break

    def _agree(direction):
        return main_dir == 0 or main_dir == direction

    # 短周期与长周期「显著」方向对立 => 视为分歧，不出强信号
    # 判定要点：
    #   1) 短周期（1/5分钟）噪音大，必须累计足够分值才够格否决长周期
    #   2) 轻微反向（如短-2 / 长+3）不算对立，那只是震荡中的正常波动
    short_keys = [k for k in ("1", "5") if k in per_scores]
    long_keys = [k for k in ("15", "30") if k in per_scores]
    short_sum = sum(per_scores[k]["raw_score"] for k in short_keys)
    long_sum = sum(per_scores[k]["raw_score"] for k in long_keys)
    short_dir = _net_dir([short_sum], thresh=4)
    long_dir = _net_dir([long_sum], thresh=3)
    tf_conflict = (short_dir * long_dir == -1)

    if tf_conflict:
        verdict, level = "观望（长短周期方向对立）", 0
    elif total >= TH_STRONG_BUY and pos >= 2 and _agree(1):
        verdict, level = "强烈买点", 2
    elif total >= TH_WEAK_BUY and pos >= 2 and _agree(1):
        verdict, level = "偏多（可轻仓试多）", 1
    elif total <= TH_STRONG_SELL and neg >= 2 and _agree(-1):
        verdict, level = "强烈卖点", -2
    elif total <= TH_WEAK_SELL and neg >= 2 and _agree(-1):
        verdict, level = "偏空（可轻仓试空）", -1
    else:
        verdict, level = "观望（信号未共振）", 0

    # 参考主周期：15分钟优先，其次 5 分钟
    main = per_scores.get("15") or per_scores.get("5") or next(iter(per_scores.values()), None)

    result = {
        "verdict": verdict,
        "level": level,
        "total_score": total,
        "pos_periods": pos,
        "neg_periods": neg,
        "periods": {k: {kk: vv for kk, vv in v.items() if kk != "score_detail"}
                    for k, v in per_scores.items()},
        "stop_loss": None,
        "target": None,
        "main_tf": None,
        "escalation": escalation_signal(per_scores),
    }

    if main:
        c = main["last_close"]
        a = main["atr"] or (c * 0.005)
        result["main_tf"] = main["label"]
        result["current_price"] = c
        # 用主周期近 40 根的前高/前低作为目标参考，与 ATR 倍数取较近的一个，
        # 避免目标位过远导致盈亏比虚高
        bars_main = main["score_detail"]["bars"]
        lb = min(40, len(bars_main))
        seg = bars_main[-lb:]
        prior_high = max(b["h"] for b in seg)
        prior_low = min(b["l"] for b in seg)

        if level > 0:
            stop = c - 1.5 * a
            t_atr = c + 2.5 * a
            t_prior = prior_high if prior_high > c else t_atr
            target = min(t_atr, t_prior)
            if target <= c:
                target = t_atr
            result["stop_loss"] = round(stop, 1)
            result["target"] = round(target, 1)
            result["risk_reward"] = round(
                (target - c) / max(c - stop, 1e-9), 2)
        elif level < 0:
            stop = c + 1.5 * a
            t_atr = c - 2.5 * a
            t_prior = prior_low if prior_low < c else t_atr
            target = max(t_atr, t_prior)
            if target >= c:
                target = t_atr
            result["stop_loss"] = round(stop, 1)
            result["target"] = round(target, 1)
            result["risk_reward"] = round(
                (c - target) / max(stop - c, 1e-9), 2)

    result["raw_total_score"] = raw_total
    result["divergence_corrected"] = abs(raw_total - total) > 0.01
    return result


def mark_signal_points(bars, ind, lookback=120, min_gap=12):
    """
    在历史 K 线上标出买卖点，供看板画箭头。
    设计取舍：图上箭头必须稀疏才有意义。因此：
      - 同一方向信号之间至少间隔 min_gap 根
      - 条件从严：金叉/死叉之外还要求 RSI 与均线位置配合
    返回带 sig 字段的 bars 副本（'B' / 'S' / None）
    """
    if not bars or not ind:
        return bars
    n = len(bars)
    start = max(35, min_gap)
    out = []
    last_b = -10 ** 9
    last_s = -10 ** 9

    for i in range(n):
        b = dict(bars[i])
        b["sig"] = None
        if i >= start:
            rsi_v = ind["rsi"][i]
            ma20 = ind["ma20"][i]
            c = ind["closes"][i]
            gc = ind_mod.cross_up(ind["dif"], ind["dea"], i)
            dc = ind_mod.cross_down(ind["dif"], ind["dea"], i)
            pin = pat_mod.detect_pin_bar(bars, i)
            eng = pat_mod.detect_engulfing(bars, i)

            if rsi_v is not None and ma20:
                buy_ok = (
                    (gc and rsi_v < 45 and c <= ma20 * 1.02)
                    or (pin[0] == 1 and rsi_v < 38)
                    or (eng[0] == 1 and rsi_v < 38)
                )
                sell_ok = (
                    (dc and rsi_v > 55 and c >= ma20 * 0.98)
                    or (pin[0] == -1 and rsi_v > 62)
                    or (eng[0] == -1 and rsi_v > 62)
                )
                if buy_ok and (i - last_b) >= min_gap and (i - last_s) >= min_gap:
                    b["sig"] = "B"
                    last_b = i
                elif sell_ok and (i - last_s) >= min_gap and (i - last_b) >= min_gap:
                    b["sig"] = "S"
                    last_s = i
        out.append(b)
    return out


def build_signals(market_data, periods=("1", "5", "15", "30")):
    """对抓取的全部品种生成信号"""
    out = {}
    for sym, entry in market_data.get("symbols", {}).items():
        pb = {}
        for pt in periods:
            b = entry["periods"].get(pt)
            if b:
                pb[pt] = b
        if not pb:
            continue
        sig = generate_signal(pb)
        sig["name"] = entry.get("name", sym)
        sig["quote"] = entry.get("quote")
        out[sym] = sig
    return out
