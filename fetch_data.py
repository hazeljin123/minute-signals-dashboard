# -*- coding: utf-8 -*-
"""
分钟线数据抓取层
数据源：新浪期货公开接口（免费、无需鉴权）

为什么用 curl 而不是 Python urllib：
在 Windows 上实测 Python 直连外网常被拒绝（WinError 10061），
但 curl 可以正常出网。因此本模块统一通过 subprocess 调 curl。
"""
import json
import re
import subprocess
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# 东八区（北京时间）。行情是国内的，所有对外展示的时间戳都必须是北京时间。
# 本机运行时时区本来就是东八区，这里显式指定是为了云端环境 ——
# GitHub Actions 的 runner 一律是 UTC，若直接用 datetime.now()，
# 生成时间会比实际早/晚 8 小时，看板上"数据时间"看着就完全不对。
CN_TZ = timezone(timedelta(hours=8))


def now_cn():
    """返回北京时间的 datetime（带时区）。"""
    return datetime.now(CN_TZ)

# 关注的品种（用户指定 7 个）
WATCH_LIST = {
    "P0": "棕榈油",
    "M0": "豆粕",
    "RM0": "菜粕",
    "Y0": "豆油",
    "OI0": "菜油",
    "JD0": "鸡蛋",
    "LH0": "生猪",
}

# 价差分析表（跨品种价差，主力连续对主力连续）
# ---------------------------------------------------------------
# 为什么去掉跨期月差：
#   月差的组合太多，穷举不完 —— 每品种有 1-5、1-9、5-9、9-1 ……
#   随交割月推进还要整体换月，维护成本高且永远补不全。
#   跨品种价差则是有限集合：4 个品种两两组合共 C(4,2)=6 个，
#   而且全部用主力连续（Y0、P0 …），不存在换月问题，
#   一次配好就长期有效。
#
# 用户指定的组合（4 个，本文实际启用）：
#   豆油-菜油、豆粕-菜粕、豆油-棕榈、菜油-棕榈
# 另外 2 个组合（豆油-豆粕、菜油-菜粕）跨了油脂/粕类，
# 相关性低、价差噪音大，先不做；如需启用只要往下面加一行。
#
# front/back 的先后顺序有含义：价差 = front - back。
# 油脂之间、粕类之间按「主流报价习惯」定方向，方便横向比较。
INTERPRICE_LIST = [
    {"label": "豆油-菜油价差",   "front": "Y0",  "back": "OI0", "kind": "inter"},
    {"label": "豆粕-菜粕价差",   "front": "M0",  "back": "RM0", "kind": "inter"},
    {"label": "豆油-棕榈价差",   "front": "Y0",  "back": "P0",  "kind": "inter"},
    {"label": "菜油-棕榈价差",   "front": "OI0", "back": "P0",  "kind": "inter"},
]

# 备用组合：相关性较低，默认不抓（启用只需把 kind 之外的项挪进上面列表）
INTERPRICE_OPTIONAL = [
    {"label": "豆油-豆粕价差",   "front": "Y0",  "back": "M0",  "kind": "inter"},
    {"label": "菜油-菜粕价差",   "front": "OI0", "back": "RM0", "kind": "inter"},
]

# 价差需要抓取的周期
INTERPRICE_FETCH_PERIODS = ("1", "5", "15", "30")

# 周期 -> 秒数
PERIODS = {"1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600}

MINLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{sym}_{ptype}_/InnerFuturesNewService.getFewMinLine"
    "?symbol={sym}&type={ptype}"
)
QUOTE_URL = "https://hq.sinajs.cn/list=nf_{sym}"
DAILY_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{sym}_/InnerFuturesNewService.getDailyKLine?symbol={sym}"
)


def _curl(url, referer="https://finance.sina.com.cn", timeout=20):
    """
    调 curl 取文本，失败返回 None。

    关于 --noproxy '*'（重要）：
        本机装了代理软件，环境变量里带 http_proxy/https_proxy。
        curl 默认会读这些变量，于是「访问 localhost 或内网」也会被推到代理上，
        代理不认就返回 502。加上 --noproxy '*' 强制直连，问题消失。
        GitHub Actions 的 runner 同理 —— 它没有可用代理，
        但若继承了任何 proxy 变量，请求会被送去一个不存在的代理而全挂。
        显式直连对两边都更稳。
    """
    cmd = ["curl", "-s", "--noproxy", "*", "--max-time", str(timeout), url]
    if referer:
        cmd += ["-H", "Referer: " + referer]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        if p.returncode != 0:
            return None
        # 新浪返回 GBK，但行情里中文少，先尝试 utf-8 再退回 gbk
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return p.stdout.decode(enc)
            except UnicodeDecodeError:
                continue
        return p.stdout.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [curl失败] {e}", file=sys.stderr)
        return None


def fetch_minline(symbol, ptype="5", retries=2):
    """
    抓取单品种单周期分钟线。
    返回 list[dict]：{t, o, h, l, c, v, p}

    关于 retries（重要）：
        新浪接口在并发较高时会**间歇性返回空体**（HTTP 正常、内容为空），
        实测 14 线程抓 7 品种×4 周期时，每次总有一两个请求落空，
        且每次落空的品种/周期都不一样（随机）。不重试的话，
        该周期的数据会静默变成空数组，进而让价差配对失败、
        整页图表消失（曾导致云端校验报「M0-RM0 没有任何图表数据」）。
        这里对「空结果」做重试，仍失败才认命返回 []。
    """
    for attempt in range(retries + 1):
        url = MINLINE_URL.format(sym=symbol, ptype=ptype)
        text = _curl(url)
        bars = _parse_minline_text(text)
        if bars:
            return bars
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))   # 退避：0.6s、1.2s
    return []


def _parse_minline_text(text):
    """把分钟线接口的 jsonp 响应解析成 bar 列表。空/异常一律返回 []。"""
    if not text:
        return []
    # 剥离 jsonp 包裹
    m = re.search(r"var\s+_\w+_\((.*)\)\s*;?\s*$", text, re.S)
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw or raw == "null":
        return []
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError:
        return []

    out = []
    for it in arr:
        try:
            out.append({
                "t": it["d"],
                "o": float(it["o"]),
                "h": float(it["h"]),
                "l": float(it["l"]),
                "c": float(it["c"]),
                "v": float(it.get("v") or 0),
                "p": float(it.get("p") or 0),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fetch_quote(symbol, retries=2):
    """抓取实时行情快照，返回 dict 或 None。带重试，见 fetch_minline 的说明。"""
    for attempt in range(retries + 1):
        q = _parse_quote_text(_curl(QUOTE_URL.format(sym=symbol)))
        if q:
            return q
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))
    return None


def _parse_quote_text(text):
    if not text or "=" not in text:
        return None
    m = re.search(r'"(.*)"', text)
    if not m:
        return None
    f = m.group(1).split(",")
    if len(f) < 18:
        return None
    try:
        return {
            "name": f[0],
            "time": f[1],
            "open": float(f[2]),
            "high": float(f[3]),
            "low": float(f[4]),
            "bid": float(f[6]),
            "ask": float(f[7]),
            "last": float(f[8]),
            "settle": float(f[9]) if f[9] else 0.0,
            "volume": float(f[13]) if f[13] else 0.0,
            "oi": float(f[14]) if f[14] else 0.0,
            "date": f[17],
        }
    except (ValueError, IndexError):
        return None


def fetch_daily(symbol, retries=2):
    """
    抓取日线（历史很长，约 4600 根，覆盖近 19 年）。
    周线与月线由日线聚合得到，避免额外接口（且周/月线接口不可用）。
    带重试，见 fetch_minline 的说明。
    """
    for attempt in range(retries + 1):
        bars = _parse_daily_text(_curl(DAILY_URL.format(sym=symbol)))
        if bars:
            return bars
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))
    return []


def _parse_daily_text(text):
    if not text:
        return []
    m = re.search(r"var\s+_\w+_\((.*)\)\s*;?\s*$", text, re.S)
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw or raw.startswith("{"):  # 错误响应
        return []
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError:
        return []

    out = []
    for it in arr:
        try:
            out.append({
                "t": it["d"],
                "o": float(it["o"]),
                "h": float(it["h"]),
                "l": float(it["l"]),
                "c": float(it["c"]),
                "v": float(it.get("v") or 0),
                "p": float(it.get("p") or 0),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out


def merge_today_daily(daily, quote):
    """
    把「今天的实时行情」补成一根当日日K，追加到日线末尾。

    为什么需要这一步（重要）：
        新浪的 getDailyKLine 接口**只返回已收盘的日K**，盘中不含当天那根。
        实测 2026-09-24 14:17（盘中）拉日线，最后一根还是 09-23。
        结果就是：日/周/月线的图少一根、长周期总结停在昨天，
        当天的大幅波动（如生猪当日跌 135 点）在长周期面板里完全看不到。

    这里用实时快照拼出当日日K：
        开=今开、高=当日最高、低=当日最低、收=最新价、量=当日累计成交量。
        盘中「收」会随行情跳动，收盘后自动定格为结算价附近的收盘价。

    去重规则：
        - 若日线末根日期 == 快照日期 → 用快照覆盖末根（同一天，取更新的值）
        - 若日线末根日期  < 快照日期 → 追加一根
        - 若日线末根日期  > 快照日期 → 不动（快照是旧的，别倒退）

    注意：快照缺字段（停牌/接口异常）时原样返回，不硬造数据。
    """
    if not daily or not quote:
        return daily

    qd = (quote.get("date") or "").strip()
    if not qd or len(qd) < 10:
        return daily
    # 快照日期统一成 YYYY-MM-DD（接口给的可能是 YYYY-MM-DD）
    qd = qd[:10]

    o = quote.get("open") or 0
    h = quote.get("high") or 0
    l = quote.get("low") or 0
    c = quote.get("last") or 0
    v = quote.get("volume") or 0
    # 当天还没成交（如夜盘开盘前）时，OHLC 会是 0，这种情况不补
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return daily

    bar = {
        "t": qd,
        "o": float(o),
        "h": float(h),
        "l": float(l),
        "c": float(c),
        "v": float(v),
        "p": float(quote.get("oi") or 0),
        "provisional": True,   # 标记：这根是盘中拼的，未收盘
    }

    last_t = (daily[-1].get("t") or "")[:10]
    if last_t == qd:
        out = list(daily)
        out[-1] = bar
        return out
    if last_t < qd:
        return list(daily) + [bar]
    return daily


def aggregate(daily, mode):
    """
    把日线聚合为周线或月线。
    mode: 'week' | 'month'
    规则：按自然周（ISO 年-周）或自然月分组，
          开=首日开，高=区间最高，低=区间最低，收=末日收，量=累加
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
        seg = groups[key]
        out.append({
            "t": seg[-1]["t"],          # 以区间最后一天为时间戳
            "o": seg[0]["o"],
            "h": max(x["h"] for x in seg),
            "l": min(x["l"] for x in seg),
            "c": seg[-1]["c"],
            "v": sum(x["v"] for x in seg),
            "p": seg[-1].get("p", 0),
        })
    return out


def fetch_all(symbols=None, periods=("1", "5", "15", "30"), max_workers=14,
              verbose=True):
    """
    并发抓取多品种多周期，返回嵌套 dict 并落盘。
    并发是为了让 7 品种 × 4 周期 = 28 个请求能在 10 秒内跑完。

    注意 max_workers 不要调太高：新浪接口并发一高就开始随机返回空体，
    14 线程已是实测比较稳的上限（配合 fetch_* 内部的重试）。
    """
    symbols = symbols or list(WATCH_LIST.keys())
    os.makedirs(DATA_DIR, exist_ok=True)
    result = {
        "fetched_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "symbols": {},
    }

    for sym in symbols:
        result["symbols"][sym] = {
            "name": WATCH_LIST.get(sym, sym),
            "periods": {},
            "quote": None,
            "daily": [],
            "weekly": [],
            "monthly": [],
        }

    # 构造全部任务：品种×周期 的分钟线 + 品种的实时行情 + 品种的日线
    # 注意：跨品种价差的两个腿都是「主力连续」，而主力连续的各周期分钟线
    # 本来就在下面这批任务里抓了，所以价差不需要额外的网络请求 ——
    # 只是把已抓到的数据两两配对相减而已。这也是换掉月差后的一个额外好处：
    # 请求数从 68 降到 28，全流程快了一倍多。
    tasks = []
    for sym in symbols:
        for pt in periods:
            tasks.append(("minline", sym, pt))
        tasks.append(("quote", sym, None))
        tasks.append(("daily", sym, None))

    def _run(task):
        kind, sym, pt = task[0], task[1], task[2]
        if kind == "minline":
            return task, fetch_minline(sym, pt)
        if kind == "daily":
            return task, fetch_daily(sym)
        if kind == "quote":
            return task, fetch_quote(sym)
        return task, None

    t0 = now_cn()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_run, t): t for t in tasks}
        for fu in as_completed(futs):
            try:
                task, data = fu.result()
            except Exception as e:
                task = futs[fu]
                print(f"  [失败] {task[1]} {task[0]}: {e}", file=sys.stderr)
                continue
            kind, sym, pt = task[0], task[1], task[2]
            if kind == "minline":
                result["symbols"][sym]["periods"][pt] = data
            elif kind == "daily":
                result["symbols"][sym]["daily"] = data
            elif kind == "quote":
                result["symbols"][sym]["quote"] = data

    # 抓取完整性自检：重试后仍为空的任务记下来。
    # 这些空值会直接导致对应图表消失、价差配对失败，
    # 所以必须在日志里显式暴露，而不是静默吞掉。
    gaps = []
    for sym in symbols:
        e = result["symbols"][sym]
        for pt in periods:
            if not (e["periods"].get(pt) or []):
                gaps.append(f"{sym} {pt}m")
        if not (e.get("daily") or []):
            gaps.append(f"{sym} 日线")
        if not e.get("quote"):
            gaps.append(f"{sym} 行情")

    # 补当日日K（detail 见 merge_today_daily 的 docstring）：
    # 必须在聚合周/月线之前做，否则今天这根进不了本周/本月。
    for sym in symbols:
        e = result["symbols"][sym]
        e["daily"] = merge_today_daily(e.get("daily") or [], e.get("quote"))

    # 聚合周线 / 月线
    for sym in symbols:
        dl = result["symbols"][sym]["daily"]
        result["symbols"][sym]["weekly"] = aggregate(dl, "week")
        result["symbols"][sym]["monthly"] = aggregate(dl, "month")

    # 价差的原始数据不再需要单独落盘 —— 它直接取自上面已抓好的主力连续分钟线。
    # build 阶段会用 INTERPRICE_LIST 里的 front/back 去 result["symbols"] 里取。
    elapsed = (now_cn() - t0).total_seconds()

    if verbose:
        for sym in symbols:
            e = result["symbols"][sym]
            q = e.get("quote")
            if q and q.get("name"):
                e["name"] = q["name"].replace("连续", "")
            parts = []
            for pt in periods:
                bars = e["periods"].get(pt) or []
                parts.append(f"{pt}m:{len(bars)}")
            latest = ""
            for pt in periods:
                bars = e["periods"].get(pt)
                if bars:
                    latest = bars[-1]["t"]
                    break
            print(f"[抓取] {sym} {e['name']}: {' '.join(parts)}  最新 {latest}")
        print(f"[耗时] {elapsed:.1f} 秒（并发 {max_workers} 线程）")
        if gaps:
            print(f"[警告] 重试后仍为空：{', '.join(gaps)}")
        else:
            print("[完整性] 全部品种/周期抓取齐全")
    else:
        # 静默模式下仍要修正名称
        for sym in symbols:
            e = result["symbols"][sym]
            q = e.get("quote")
            if q and q.get("name"):
                e["name"] = q["name"].replace("连续", "")
        # 静默模式也要把缺口报到 stderr，不能悄悄放过
        if gaps:
            print(f"  [警告] 重试后仍为空：{', '.join(gaps)}", file=sys.stderr)

    out_path = os.path.join(DATA_DIR, "market_data.json")
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False)
    return result


if __name__ == "__main__":
    symbols = sys.argv[1:] or None
    fetch_all(symbols)
