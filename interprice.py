# -*- coding: utf-8 -*-
"""
价差技术分析（跨期月差 + 跨品种价差）
==================================================
用户需求：除了单合约的分钟线买卖点，还要看「价差」的技术形态。

两类价差共用同一套算法，区别只在于两条腿是什么：

  A. 跨期月差（同一品种、不同月份）
     例：豆粕 1-5 月差 = M2701 - M2705
  B. 跨品种价差（不同品种、同一主力连续）
     例：豆油-棕榈价差 = Y0 - P0
        豆粕-菜粕价差 = M0 - RM0
        豆油-棕榈价差、菜油-棕榈价差

为什么价差值得单独看：
  单合约价格会被整个商品板块的系统性涨跌带着走；
  价差是两条曲线的差，系统性因素被抵消掉，
  剩下的基本是「两个标的谁强谁弱」的相对结构。
  所以价差的趋势往往比单边价格更干净，也更适合做套利决策。

跨期 vs 跨品种的一个实务差异：
  跨期价差的两个月份属于同一品种，供需节奏高度相关，价差通常波动较小；
  跨品种价差涉及两个独立品种，会真实反映替代关系（如豆油/棕榈的
  油脂间替代、豆粕/菜粕的蛋白粕替代），波动幅度通常更大。
  算法上用「波动单位」归一化后，两者可以用同一套阈值，不需要分开调参。

数据对齐的关键点（实测结论）：
  新浪的分钟线接口对每个周期都返回固定根数（约 1023 根），
  也就是说周期越长，覆盖的历史越久：
      1 分钟 -> 约 4 个交易日
      30 分钟 -> 约 4 个月
  15/30 分钟两个标的的时间戳实测 100% 对齐，可以直接逐根相减。
  1 分钟因为取数时刻不同会有几根错位，所以用「按时间戳取交集」的方式对齐，
  这样任何情况下都不会错配。

价差序列的 OHLC 怎么取：
  同一根 K 线上 价差 = 前腿 - 后腿，
  o/h/l/c 分别用两个标的对应字段相减即可。
  注意 h（最高）应该是「前腿最高 - 后腿最低」才对应真实可达的最大价差，
  但这样会让 K 线的振幅被放大、影线虚长。
  这里统一用「同名字段相减」，与文华的价差图口径一致，
  也更适合做技术分析（均线/MACD 不会被影线干扰）。
"""
import indicators as I
import patterns as P

# 需要展示的周期（与单合约看板保持一致）
SPREAD_PERIODS = ("1", "5", "15", "30")

# 各周期的展示根数
SPREAD_MAX_BARS = {"1": 200, "5": 200, "15": 160, "30": 140}

# 周期权重：价差看的是结构，长周期更能反映结构变化，所以 30 分钟给高权重
SPREAD_WEIGHT = {"1": 1, "5": 2, "15": 3, "30": 3}


# ----------------------------------------------------------------------
# 一、价差序列构造
# ----------------------------------------------------------------------

def align_pair(front_bars, back_bars):
    """
    把前腿与后腿的分钟线按时间戳对齐，逐根相减得到价差序列。
    用 dict 做 O(n) 对齐，只保留两边都有的时间戳，天然容错。
    返回 list[dict]，结构与普通 K 线一致（含 v 成交量，取两腿之和）。

    命名用 front/back 而不是 near/far：跨期时是「近月/远月」，
    跨品种时是「A 品种/B 品种」，后者没有远近之分，用中性词更准确。
    """
    if not front_bars or not back_bars:
        return []
    back_map = {b["t"]: b for b in back_bars}
    out = []
    for nb in front_bars:
        fb = back_map.get(nb["t"])
        if not fb:
            continue
        try:
            out.append({
                "t": nb["t"],
                "o": nb["o"] - fb["o"],
                "h": nb["h"] - fb["h"],
                "l": nb["l"] - fb["l"],
                "c": nb["c"] - fb["c"],
                "v": (nb.get("v") or 0) + (fb.get("v") or 0),
            })
        except (TypeError, KeyError):
            continue
    # 防御：万一有 h<l 的异常（数据源错位），修正一下保证 K 线合法
    for b in out:
        if b["h"] < b["l"]:
            b["h"], b["l"] = b["l"], b["h"]
        b["h"] = max(b["h"], b["o"], b["c"])
        b["l"] = min(b["l"], b["o"], b["c"])
    return out


def compute_spread(label, front_bars, back_bars):
    """
    计算一组价差在各周期上的序列。
    返回 {period: bars} （bars 为价差 K 线，未截断）
    """
    out = {}
    for pt in SPREAD_PERIODS:
        nb = front_bars.get(pt)
        fb = back_bars.get(pt)
        s = align_pair(nb, fb)
        if s and len(s) >= 30:
            out[pt] = s
    return out


# 长周期（日/周/月）—— 价差同样要看大级别结构
LONG_PERIODS = ("daily", "weekly", "monthly")


def aggregate_daily(daily, mode):
    """
    把日线聚合为周线或月线。
    规则与 fetch_data.aggregate 完全一致（按自然周/自然月分组，
    开=首日开、高=最高、低=最低、收=末日收、量累加），
    这里复制一份是因为 fetch_data 的版本带文件路径等副作用，
    而且价差序列是「计算出来的」而不是抓来的，不适合走那条链路。
    mode: 'week' | 'month'
    """
    from datetime import date
    if not daily:
        return []
    groups = {}
    order = []
    for b in daily:
        try:
            y, m, d = (int(x) for x in b["t"].split("-"))
            dt = date(y, m, d)
        except (ValueError, AttributeError):
            continue
        if mode == "week":
            iso = dt.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:
            key = f"{y}-{m:02d}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(b)

    out = []
    for key in order:
        g = groups[key]
        out.append({
            "t": g[-1]["t"],
            "o": g[0]["o"],
            "h": max(x["h"] for x in g),
            "l": min(x["l"] for x in g),
            "c": g[-1]["c"],
            "v": sum((x.get("v") or 0) for x in g),
        })
    return out


def build_long_periods(front_daily, back_daily, min_bars=35, min_long=35):
    """
    用两条腿的日线构造价差的 日线 / 周线 / 月线 序列。

    顺序很重要：先把两个品种的日线对齐相减得到「价差日线」，
    再由价差日线聚合出周线、月线。
    反过来（各自聚合再相减）会错，因为两个品种的周线分组边界
    虽然一致，但某品种停牌/涨跌停导致缺日时分组会错位。

    参数：
        min_bars : 价差日线的最少根数，低于此值认为数据不可用，返回 {}
        min_long : 周线/月线的最少根数。默认 35 是为了保证 MA20/MACD
                   有足够历史；但**新上市的合约**（如棕榈油 2705）
                   天生只有几个月数据，此时应放宽，否则周月线会整块消失。
                   调用方可按需下调，例如月差页传 min_long=8。
    """
    daily = align_pair(front_daily, back_daily)
    if not daily or len(daily) < min_bars:
        return {}
    out = {"daily": daily}
    w = aggregate_daily(daily, "week")
    m = aggregate_daily(daily, "month")
    if len(w) >= min_long:
        out["weekly"] = w
    if len(m) >= min_long:
        out["monthly"] = m
    return out


# ----------------------------------------------------------------------
# 一·半、措辞映射
# ----------------------------------------------------------------------
# 同一套打分逻辑，两类价差要用不同的行话：
#   跨期月差：价差变大 = 走扩（近月强于远月），变小 = 收敛
#   跨品种价差：前腿相对后腿变强 = 走强，变弱 = 走弱
# 用错词会让人读着别扭（"豆油-棕榈走扩"不通顺），所以按 kind 切换。

WORD = {
    "calendar": {   # 跨期月差
        "up": "走扩",
        "down": "收敛",
        "flat": "横盘",
        "strong_up": "强烈走扩",
        "mild_up": "偏走扩",
        "strong_down": "强烈收敛",
        "mild_down": "偏收敛",
        "neutral": "震荡，无明确方向",
    },
    "inter": {      # 跨品种价差
        "up": "走强",
        "down": "走弱",
        "flat": "横盘",
        "strong_up": "显著走强",
        "mild_up": "偏走强",
        "strong_down": "显著走弱",
        "mild_down": "偏走弱",
        "neutral": "震荡，无明确方向",
    },
}


def word(kind, key):
    """取该类价差的措辞，未知 kind 回落到跨品种（更通用的说法）"""
    return WORD.get(kind, WORD["inter"]).get(key, key)


# ----------------------------------------------------------------------
# 二、价差单周期打分
# ----------------------------------------------------------------------

def _atr_like(bars, i, n=14):
    """
    价差的自适应波动单位（类 ATR），用于把绝对值换算成「多少个波动单位」。

    这里刻意取 n*3 根（默认 42 根）而不是标准 ATR 的 14 根：
    价差序列比单合约更平滑，短窗口会把单位压到接近 0，
    于是任何微小变动都换算成「好几个波动单位」。
    实测豆粕价差在 120 根窗口上，若只取最近 14 根，
    效率被算成 544%（>100%，物理上不可能是「走直线」的结果）；
    取 42 根后回落到 21%，与真实盘面吻合。
    """
    lo = max(0, i - n * 3)
    trs = []
    for k in range(lo + 1, i + 1):
        h, l = bars[k]["h"], bars[k]["l"]
        pc = bars[k - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return sum(trs) / len(trs)


def _recent_extreme(bars, i, lookback):
    """
    取「i 之前」lookback 根的区间高低，返回 (hi, lo, 幅度)。

    注意 i 是参照区间的右端点（含），因此调用时应传 i-1，
    以排除当前这根 K 线 —— 否则当前价永远等于区间端点，
    突破判断会失效（这是实测踩到的坑：菜粕 1 分钟显示
    「贴近区间上沿 100%」却说「未突破」，就是自参照导致的）。
    """
    if i < 0:
        return None, None, 0.0
    lo_idx = max(0, i - lookback + 1)
    if i - lo_idx + 1 < 5:
        return None, None, 0.0
    seg = bars[lo_idx:i + 1]
    hi = max(b["h"] for b in seg)
    lo = min(b["l"] for b in seg)
    return hi, lo, hi - lo


def _disambiguate_verdict(verdict, level, structure, pos_n=0, neg_n=0, kind="inter"):
    """
    消除结论与结构描述的自相矛盾。

    实测案例：菜粕 1-5 月差各周期短期评分偏多（合计 +27），
    但结构函数判定为「收敛」。若直接输出「强烈走扩」+「近期收敛」，
    用户会无所适从。此时降级并说明原因，宁可少说也不说错。

    但要避免过度抑制：如果四个周期全线同向（共振），
    这本身就是很强的信息，即使近期横盘也应保留「偏」这一级，
    只用文字提示「结构尚未确认」，而不是直接归零。

    规则：
      结论多 vs 结构向下  -> 由结构改判「偏收敛/偏走弱」（结构已确认，短周期还没跟上）
      结论空 vs 结构向上  -> 由结构改判「偏走扩/偏走强」（同上）
      结论极端 vs 结构横盘 -> 降到「偏」一级；若四周期共振则保留「偏」并加提示
      结论震荡 vs 结构横盘 -> 维持震荡（真正的无方向）

    关键取舍：结构的权重高于短周期评分。
    价差看的是相对结构，结构一旦确认（效率 + 波动单位双重达标），
    比分钟级评分更能代表「真实发生的事」。短周期评分只是先行/滞后的扰动，
    不应把已经成立的结构抹平成「震荡」。
    """
    if not structure:
        return verdict, level, ""

    # 用 dir_sign 判断方向，不依赖中文字符串比较 ——
    # 这样跨期（走扩/收敛）和跨品种（走强/走弱）能共用同一套逻辑。
    ds = structure.get("dir_sign", 0)

    # level==0 时也要往下走：实测菜油 1-5 月差 total=-5.2 落在震荡区间，
    # 但结构效率 17.6%、9.2 个波动单位，是实打实的走扩。
    # 若在此处直接 return，页面会同时出现「结论:震荡」与「结构:走扩」，
    # 正是要消除的那种自相矛盾。
    if level == 0 and ds == 0:
        return verdict, level, ""

    # 结构本身已有明确方向时，若结论方向与之同向，属于互相印证，不应降级；
    # 若结论反向或不明，则由结构改判「偏」一档。
    # 实测菜油 1-5 月差：结构效率 17.6%、9.2 个波动单位，是真正在走扩；
    # 30 分钟 +3 / 15 分钟 +3 同向，却因为 1m、5m 为负把总分拉到震荡区间。
    # 这种情况应如实升格为「偏走扩」，而不是让「结构:走扩」和「结论:震荡」
    # 同时出现在页面上自相矛盾。
    if ds > 0:
        if level > 0:
            return verdict, level, ""
        return (word(kind, "mild_up"), 1,
                f"结构已确认{word(kind, 'up')}（价差突破区间上沿），"
                f"短周期评分尚未跟上，属结构性{word(kind, 'up')}的早期阶段")
    if ds < 0:
        if level < 0:
            return verdict, level, ""
        return (word(kind, "mild_down"), -1,
                f"结构已确认{word(kind, 'down')}（价差跌破区间下沿），"
                f"短周期评分尚未跟上，属结构性{word(kind, 'down')}的早期阶段")

    # ds == 0 -> 结构横盘
    # 四周期全线共振：保留方向判断，但明确标注结构未确认
    full_resonance = (pos_n >= 4 and level > 0) or (neg_n >= 4 and level < 0)
    # 「强烈」在横盘下站不住，降到「偏」一档；注意必须保留原方向的正负号
    if abs(level) == 2:
        new_lv = 1 if level > 0 else -1
    else:
        new_lv = level
    if new_lv == 0:
        return verdict, level, ""
    new_verdict = word(kind, "mild_up") if new_lv > 0 else word(kind, "mild_down")
    if full_resonance:
        note = ("四周期评分一致，但近期价差整体横盘，"
                "结构尚未确认，属短线共振，建议等突破区间再动")
    elif abs(level) == 2:
        note = ("各周期短期评分同向，但近期价差整体横盘，"
                "尚未形成结构性方向，故由「强烈」下调为「偏」")
    else:
        note = "近期整体横盘，此处偏向仅为短线波动，结构性意义有限"
    return new_verdict, new_lv, note


def _trend_lookback(ptype):
    """
    各周期「近期趋势」的回溯根数（单位：根）。
    设计原则：换算成时间要大致相当，约等于「最近 1~2 个交易日」。
        1 分钟线：1 个交易日约 225 根 -> 取 120 根（约半天多）
        5 分钟线：约 45 根/日      -> 取 48 根（约 1 日）
        15 分钟线：约 15 根/日     -> 取 32 根（约 2 日）
        30 分钟线：约 8 根/日      -> 取 24 根（约 3 日）
    这与单合约看板「多周期共振」的精神一致：短周期看短、长周期看长。
    """
    return {"1": 120, "5": 48, "15": 32, "30": 24}.get(ptype, 40)


def _structure_window(ptype):
    """
    各周期用于「区间突破 / 均线排列 / 形态」判断的回溯窗口（根）。
    同样按「时间等价」设计，避免在 1023 根的长序列上得出几个月前的结论。
    """
    return {"1": 240, "5": 160, "15": 120, "30": 96}.get(ptype, 120)


def score_spread_period(bars, ptype="5"):
    """
    对价差序列单周期打分。

    与单合约打分的核心差异（都是实测踩过的坑）：

      1) 【最关键】必须剔除「陈旧趋势」
         各周期序列都很长（固定 1023 根），覆盖时间差别很大：
         1 分钟线约 4 个交易日，30 分钟线约 4 个月。
         如果直接在整段序列上算均线排列 / 形态，等于在拿 4 个月前的
         结构来给现在的行情下结论。
         实测案例：棕榈油 1-5 月差过去 2.5 个月从 -44 走到 -466，
         但最近一周一直贴在 -460 附近横盘。用全样本会得到「强烈收敛」，
         实际上走扩行情早就走完，当下是横盘。
         解决办法：所有判断（趋势/均线/区间/形态）都锚定在
         「与该周期时间等价」的近期窗口内，见 _trend_lookback /
         _structure_window。同时要求趋势判断中包含「近端仍在推进」的检验。

      2) 阈值按「波动单位」归一化，而不是写死分数
         各价差量纲差异极大（豆粕月差 400、棕榈油月差 -466、菜粕月差 -18，
         豆油-棕榈价差也在不同量级），固定数值判断突破会让小量纲标的
         永远触发、大量纲标的永不触发。

      3) 不做超买超卖的反转判断
         价差可以长期贴着区间上下沿运行，触轨不代表反转。

    分量按重要性排序：近期趋势 > 区间突破 > MACD > 均线 > 形态。
    """
    if not bars or len(bars) < 60:
        return None

    ind = I.compute_all(bars)
    i = len(bars) - 1
    reasons = []
    score = 0

    c = ind["closes"][i]
    dif, dea, hist = ind["dif"], ind["dea"], ind["hist"]
    rsi_v = ind["rsi"][i]

    # 自适应波动单位
    unit = _atr_like(bars, i, 14) or (abs(c) * 0.002) or 1e-9

    # 该周期对应的近期窗口
    win = _structure_window(ptype)
    tlb = min(_trend_lookback(ptype), i)

    # ---- 0. 近期趋势（核心分量）----
    # 用窗口内的「净变动 / 波动单位」衡量趋势，并同时看窗口内是否在推进
    if tlb >= 10:
        ref = bars[i - tlb]["c"]
        move = c - ref
        move_u = move / unit if unit else 0.0
        # 时间等价的窗口下，阈值放宽到 1.5 / 3.0 个波动单位
        if move_u >= 3.0:
            score += 3
            reasons.append(f"价差近{tlb}根走扩 {move:+.0f}（{move_u:.1f}倍波幅），趋势在推进")
        elif move_u >= 1.5:
            score += 2
            reasons.append(f"价差近{tlb}根小幅走扩 {move:+.0f}")
        elif move_u <= -3.0:
            score -= 3
            reasons.append(f"价差近{tlb}根收敛 {move:+.0f}（{move_u:.1f}倍波幅），趋势在推进")
        elif move_u <= -1.5:
            score -= 2
            reasons.append(f"价差近{tlb}根小幅收敛 {move:+.0f}")
        else:
            reasons.append(f"价差近{tlb}根基本走平（{move:+.0f}），无明确近期趋势")

    # ---- 1. 区间突破（在近期窗口内判断，权重最高）----
    hi, lo, rng = _recent_extreme(bars, i - 1, win)
    if hi is not None and rng > 1e-9:
        if c > hi + unit * 0.5:
            score += 3
            reasons.append(f"价差上破近{win}根区间上沿 {hi:.0f}，正向结构强化")
        elif c < lo - unit * 0.5:
            score -= 3
            reasons.append(f"价差下破近{win}根区间下沿 {lo:.0f}，反向结构强化")
        else:
            pos = (c - lo) / rng * 100
            if pos >= 85:
                reasons.append(f"价差贴近近{win}根区间上沿（{pos:.0f}%），结构偏紧但未突破")
            elif pos <= 15:
                reasons.append(f"价差贴近近{win}根区间下沿（{pos:.0f}%），结构偏松但未破位")

    # ---- 2. MACD ----
    if I.cross_up(dif, dea, i):
        score += 2
        reasons.append("价差 MACD 金叉")
    elif I.cross_down(dif, dea, i):
        score -= 2
        reasons.append("价差 MACD 死叉")
    if i >= 2 and None not in (hist[i], hist[i - 1], hist[i - 2]):
        if hist[i] > 0 and hist[i] > hist[i - 1] and hist[i - 1] <= hist[i - 2]:
            score += 1
            reasons.append("价差红柱放大，走扩动能增强")
        elif hist[i] < 0 and hist[i] < hist[i - 1] and hist[i - 1] >= hist[i - 2]:
            score -= 1
            reasons.append("价差绿柱放大，收敛动能增强")

    # ---- 3. 均线排列（在近期窗口内检验，且要求与最近方向一致）----
    seg = bars[-win:] if len(bars) > win else bars
    sc = [b["c"] for b in seg]
    s_ma5 = I.sma(sc, 5)
    s_ma20 = I.sma(sc, 20)
    s_ma60 = I.sma(sc, 60)
    k = len(seg) - 1
    last_move = (sc[-1] - sc[max(0, k - max(k // 3, 5))]) if k >= 5 else 0.0
    near_up = last_move > 0
    if s_ma5[k] and s_ma20[k] and s_ma60[k]:
        if s_ma5[k] > s_ma20[k] > s_ma60[k] and near_up:
            score += 2
            reasons.append(f"价差近{win}根内均线多头排列且近期向上")
        elif s_ma5[k] < s_ma20[k] < s_ma60[k] and not near_up:
            score -= 2
            reasons.append(f"价差近{win}根内均线空头排列且近期向下")
        elif sc[k] > s_ma5[k] > s_ma20[k] and near_up:
            score += 1
            reasons.append(f"价差近{win}根内站上 MA5/MA20")
        elif sc[k] < s_ma5[k] < s_ma20[k] and not near_up:
            score -= 1
            reasons.append(f"价差近{win}根内跌破 MA5/MA20")

    # ---- 4. RSI（只做极端提示，不作为反转依据）----
    if rsi_v is not None:
        if rsi_v >= 80:
            reasons.append(f"价差 RSI {rsi_v:.0f} 高位（走扩已较充分）")
        elif rsi_v <= 20:
            reasons.append(f"价差 RSI {rsi_v:.0f} 低位（收敛已较充分）")

    # ---- 5. 形态（在近期窗口内检测）----
    pin = P.detect_pin_bar(seg, k)
    if pin[0]:
        score += pin[0] * pin[2]
        reasons.append("价差" + pin[1])
    eng = P.detect_engulfing(seg, k)
    if eng[0]:
        score += eng[0] * eng[2]
        reasons.append("价差" + eng[1])
    if len(seg) >= 60:
        dbl = P.detect_double(seg, k, span=min(60, k))
        if dbl[0]:
            score += dbl[0] * dbl[2]
            reasons.append("价差" + dbl[1])

    return {
        "score": score,
        "reasons": reasons,
        "last_close": c,
        "rsi": rsi_v,
        "unit": unit,
        "trend_lb": tlb,
        "trend_move": (c - bars[i - tlb]["c"]) if tlb >= 10 else 0.0,
        "window": win,
        "atr": ind["atr"][i],
        "ind": ind,
        "bars": bars,
    }


def detect_structure(bars, lookback=120, kind="inter"):
    """
    识别价差的结构形态。

    为什么不能用「净变动 / 区间幅度」判断方向：
      实测发现这个指标很不稳定。豆粕 30 分钟在 60 根上算出 -6%（横盘），
      在 120 根上却是 +41%（走扩），换一个窗口结论就翻。
      原因是区间幅度会被单根毛刺拉大，分母不稳。

    改进做法：改用「路径效率」= 净变动 / 累计路径长度
      走直线时效率接近 100%，反复震荡时效率趋近 0。
      实测区分度很好：
          纯噪音  -> 效率 1~5%
          真趋势  -> 效率 13~21%
      这个指标对毛刺不敏感（毛刺会同时增加分子和分母），
      而且天然回答「这个方向是真的在推进，还是来回磨」。

    同时叠加「净变动是否达到波动单位量级」作为二次确认，
    避免在小幅波动里也报出趋势。

    kind 只影响输出的方向用词（跨期=走扩/收敛，跨品种=走强/走弱），
    不影响任何数值判断。
    """
    if not bars or len(bars) < 20:
        return None
    n = len(bars)
    lb = min(lookback, n)
    seg = bars[-lb:]
    closes = [b["c"] for b in seg]
    nn = len(closes)
    cur = closes[-1]
    hi = max(b["h"] for b in seg)
    lo = min(b["l"] for b in seg)
    rng = max(hi - lo, 1e-9)

    # 区间位置
    pos = (cur - lo) / rng * 100 if hi > lo else 50.0

    # 路径效率
    net = closes[-1] - closes[0]
    path = sum(abs(closes[k] - closes[k - 1]) for k in range(1, nn))
    eff = abs(net) / path * 100 if path > 1e-9 else 0.0

    # 二次确认：净变动相对波动单位的量级
    unit = _atr_like(seg, nn - 1, 14) or (rng * 0.1) or 1e-9
    move_u = abs(net) / unit

    # 效率的理论上限是 100%：路径长度不可能小于净位移。
    # 若算出 >100%，说明 unit 被短窗口压得过小（价差序列过于平滑时会发生），
    # 此时 move_u 不可信，改用「净变动 / 区间幅度」这个有界比值兜底。
    if eff > 100.0:
        move_u = abs(net) / rng * 10.0
        eff = min(eff, 100.0)

    # 判定：效率够高 且 净变动够大，才算有方向
    # 方向用词按 kind 切换（走扩/收敛 vs 走强/走弱）；
    # 另外单独保留 dir_sign，供下游做逻辑判断，避免依赖中文字符串比较。
    if eff >= 12 and move_u >= 4:
        direction = word(kind, "up") if net > 0 else word(kind, "down")
        dir_sign = 1 if net > 0 else -1
    elif eff >= 7 and move_u >= 8:
        # 效率中等但净变动很大（时间换空间），也算方向
        direction = word(kind, "up") if net > 0 else word(kind, "down")
        dir_sign = 1 if net > 0 else -1
    else:
        direction = word(kind, "flat")
        dir_sign = 0

    # 波动率变化：最近 1/3 根 vs 前 1/3 根的平均绝对变动
    third = max(nn // 3, 5)
    def _avg_abs_move(xs):
        if len(xs) < 2:
            return 0.0
        return sum(abs(xs[k] - xs[k - 1]) for k in range(1, len(xs))) / (len(xs) - 1)
    recent_vol = _avg_abs_move(closes[-third:])
    early_vol = _avg_abs_move(closes[:third])
    vol_ratio = recent_vol / early_vol if early_vol > 1e-9 else 1.0

    if vol_ratio >= 1.4:
        vol_txt = "波动放大"
    elif vol_ratio <= 0.7:
        vol_txt = "波动收敛"
    else:
        vol_txt = "波动平稳"

    return {
        "cur": cur,
        "hi": hi,
        "lo": lo,
        "pos": pos,
        "net": net,
        "path": path,
        "eff": eff,
        "move_u": move_u,
        "direction": direction,
        "dir_sign": dir_sign,
        "vol_ratio": vol_ratio,
        "vol_txt": vol_txt,
        "lookback": lb,
    }


# ----------------------------------------------------------------------
# 三、多周期合成
# ----------------------------------------------------------------------

def _verdict_from_score(total, pos_n, neg_n, kind="inter"):
    """
    价差的结论刻度。
    经过饱和压缩后，总分上限约 ±42，实际有效区间大多落在 ±25 内。
    取 18 / 8 作为强弱两档门槛，并要求足够多的周期同向，
    以配合「宁缺勿滥」的整体基调。
    """
    if total >= 18 and pos_n >= 3:
        return word(kind, "strong_up"), 2
    if total >= 8 and pos_n >= 2:
        return word(kind, "mild_up"), 1
    if total <= -18 and neg_n >= 3:
        return word(kind, "strong_down"), -2
    if total <= -8 and neg_n >= 2:
        return word(kind, "mild_down"), -1
    return word(kind, "neutral"), 0


def _trend_ma(series, n):
    return I.sma(series, n)


def _describe_extremes(bars, lookback=120):
    """
    取当前价差在近期区间中的相对位置。
    窗口长度与 detect_structure 保持一致，避免两个数字互相打架。
    """
    if not bars or len(bars) < 20:
        return ""
    lb = min(lookback, len(bars))
    seg = bars[-lb:]
    hi = max(b["h"] for b in seg)
    lo = min(b["l"] for b in seg)
    cur = bars[-1]["c"]
    if hi == lo:
        return ""
    pos = (cur - lo) / (hi - lo) * 100
    if pos >= 80:
        return f"位于近{lb}根区间上沿（{pos:.0f}%）"
    if pos <= 20:
        return f"位于近{lb}根区间下沿（{pos:.0f}%）"
    return f"位于近{lb}根区间中枢（{pos:.0f}%）"


def analyze_spread(spread_period_bars, label="价差", kind="inter"):
    """
    输入 {period: 价差K线}，输出完整价差分析结构。
    kind: "calendar"（跨期月差）或 "inter"（跨品种价差），只影响用词。
    返回 None 表示数据不足。
    """
    per = {}
    total = 0.0
    pos_n = neg_n = 0

    for pt in SPREAD_PERIODS:
        bars = spread_period_bars.get(pt)
        r = score_spread_period(bars, ptype=pt)
        if not r:
            continue
        w = SPREAD_WEIGHT.get(pt, 1)
        per[pt] = {
            "label": f"{pt}分钟",
            "raw_score": r["score"],
            "weighted": r["score"] * w,
            "weight": w,
            "reasons": r["reasons"],
            "last_close": r["last_close"],
            "rsi": r["rsi"],
        }
        total += r["score"] * w

    # 多空周期数按「每个周期的原始分方向」统计。
    # 不能用 |score| >= 2 的门槛：实测豆粕价差 1m=+6 5m=+5 15m=+1 30m=+3，
    # 四周期全为多头（pos_n 应为 4），但 15m 的 +1 过不了 >=2 的门槛，
    # 会被算成 pos_n=3，与页面展示的分周期颜色自相矛盾。
    # 门槛交给 _verdict_from_score 的总分阈值去把关即可。
    pos_n = sum(1 for v in per.values() if v["raw_score"] > 0)
    neg_n = sum(1 for v in per.values() if v["raw_score"] < 0)

    if not per:
        return None

    # 与单合约一致的分歧惩罚：多周期互相打架时削弱总分
    if pos_n >= 1 and neg_n >= 1:
        opp = sum(v["weighted"] for v in per.values()
                  if (v["raw_score"] <= -2) if total > 0) if total > 0 else \
              sum(v["weighted"] for v in per.values() if v["raw_score"] >= 2)
        penalty = abs(opp) * 0.45
        total = total - penalty if total > 0 else total + penalty

    # ---- 分数饱和处理 ----
    # 单周期分值上限约 ±12，四周期加权后理论上可到 ±100。
    # 但分值堆到很高时，边际信息量并没有同比增加
    # （-40 与 -74 都只是"很空"，差异不具备决策意义）。
    # 这里做一次压缩，让映射到结论与配色时不会把量纲差异放大成假信号。
    if total > 25:
        total = 25 + (total - 25) * 0.35
    elif total < -25:
        total = -25 + (total + 25) * 0.35

    verdict, level = _verdict_from_score(total, pos_n, neg_n, kind)

    # 结构判断用 30 分钟（历史最久）优先，其次 15 分钟。
    # 窗口取该周期「时间等价」的长度：30 分钟线约 8 根/日，120 根约 3 周，
    # 这个尺度既能反映结构性变化，又不会被几个月前的老趋势主导。
    st_pt = "30" if spread_period_bars.get("30") else \
            "15" if spread_period_bars.get("15") else "5"
    st_bars = spread_period_bars.get(st_pt)
    structure = detect_structure(st_bars, _structure_window(st_pt), kind) if st_bars else None

    # 结论若与近期结构自相矛盾，降级并给出说明
    verdict, level, conflict_note = _disambiguate_verdict(
        verdict, level, structure, pos_n, neg_n, kind)

    # 当前价差（优先取 1 分钟最新，代表最新时刻）
    cur_val = None
    for pt in ("1", "5", "15", "30"):
        b = spread_period_bars.get(pt)
        if b:
            cur_val = b[-1]["c"]
            break

    # 自然语言总结
    segs = []
    if cur_val is not None:
        segs.append(f"当前 {cur_val:+.0f}")
    if structure:
        segs.append(f"近期{structure['direction']}")
        segs.append(structure["vol_txt"])
        pos_txt = _describe_extremes(st_bars, _structure_window(st_pt))
        if pos_txt:
            segs.append(pos_txt)
    summary = f"{label}：" + "，".join(segs) if segs else f"{label}数据不足"
    if conflict_note:
        summary += f"。{conflict_note}"

    # 给前端做多空配色的归一化强度。
    # 注意：disambiguate 可能依据结构把 level 改判（如菜油由震荡升为偏走扩），
    # 此时 total 仍停在震荡区间，若直接用 total/25 会得到与 verdict 相反的配色，
    # 页面上就会出现「文字说走扩、颜色却是绿的」。因此以 level 为准做兜底。
    strength = max(-1.0, min(1.0, total / 25.0))
    if level > 0:
        strength = max(strength, 0.3 * level)
    elif level < 0:
        strength = min(strength, 0.3 * level)
    else:
        strength = 0.0

    return {
        "label": label,
        "kind": kind,
        "current": cur_val,
        "verdict": verdict,
        "level": level,
        "conflict_note": conflict_note,
        "total_score": round(total, 1),
        "pos_periods": pos_n,
        "neg_periods": neg_n,
        "periods": per,
        "structure": structure,
        "summary": summary,
        "strength": round(strength, 2),
    }


# ----------------------------------------------------------------------
# 四、从原始行情构建价差
# ----------------------------------------------------------------------

def build_spread(raw_pair_data, label, kind="inter"):
    """
    raw_pair_data: {period: (front_bars, back_bars)}
    kind: "calendar"（跨期月差）/ "inter"（跨品种价差）
    返回 (analysis, period_bars)
    """
    period_bars = {}
    for pt, (nb, fb) in raw_pair_data.items():
        s = align_pair(nb, fb)
        if s and len(s) >= 30:
            period_bars[pt] = s
    if not period_bars:
        return None, {}
    return analyze_spread(period_bars, label, kind), period_bars
