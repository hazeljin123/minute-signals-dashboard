# -*- coding: utf-8 -*-
"""
P2701-P2705 月差 —— 本机实时版服务

与主看板的 serve.py 是同一个套路：常驻抓取 + 本地 HTTP 服务，
每 N 秒重写 spread.html，做到真·实时刷新。

与 serve.py 的区别：
    - 只抓两条腿（P2701 / P2705），比主看板 7 品种快很多
    - 只产出 spread.html / spread.data.json
    - 默认端口 8766，可以和主看板同时开，互不打架

用法：
    python serve_spread.py                # 默认端口 8766，每 10 秒刷新
    python serve_spread.py --port 9001    # 指定端口
    python serve_spread.py --interval 5   # 每 5 秒刷新
然后浏览器打开 http://127.0.0.1:8766/spread.html
"""
import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """禁用缓存的静态文件服务（file:// 下浏览器会强缓存，必须走 HTTP）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def do_refresh(verbose=True):
    """执行一次完整刷新：抓两条腿 -> 算月差 -> 写 spread.html"""
    import fetch_spread
    import build_spread
    import build_dashboard as bd

    t0 = time.time()
    md = fetch_spread.fetch_all(verbose=verbose)
    payload = build_spread.build_payload(md)

    if not payload["pages"]:
        if verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 月差数据不完整，本次跳过")
        return None

    with open(os.path.join(BASE_DIR, "template_spread.html"), "r", encoding="utf-8") as f:
        tpl = f.read()

    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("/*__DATA__*/null", data_json)

    # 原子写入，避免浏览器读到写了一半的文件
    bd.write_atomic(os.path.join(BASE_DIR, "spread.html"), html)
    bd.write_atomic(os.path.join(BASE_DIR, "spread.data.json"), data_json)

    if verbose:
        p = payload["pages"][0]
        legs = p.get("legs") or {}
        fp, bp = legs.get("front_price"), legs.get("back_price")
        price = f"{fp} - {bp} = {p['current']}" if fp and bp else f"{p['current']}"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 已刷新 "
              f"({time.time()-t0:.1f}s) | {p['code']} {price} | {p['verdict']}")
    return payload


# 非交易日的刷新间隔（秒）——休市时行情不动，没必要空抓
IDLE_INTERVAL = 600


def _should_run_now():
    """判断当前是否处于交易时段，返回 (bool, reason)。"""
    try:
        from trading_calendar import session_trading_check
        from datetime import datetime as _dt
        return session_trading_check(_dt.now())
    except Exception as e:
        return True, f"交易日判断不可用（{e}），按交易时段处理"


def refresh_loop(interval):
    """后台线程：定期刷新。非交易日自动降频。"""
    idle_logged = False
    while True:
        ok, reason = _should_run_now()
        if ok:
            idle_logged = False
            try:
                do_refresh()
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 刷新失败: {e}")
            time.sleep(interval)
        else:
            if not idle_logged:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易时段"
                      f"（{reason}），刷新降频至 {IDLE_INTERVAL // 60} 分钟一次")
                idle_logged = True
            time.sleep(IDLE_INTERVAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766,
                    help="HTTP 端口（默认 8766，避开主看板的 8765）")
    ap.add_argument("--interval", type=int, default=10,
                    help="数据刷新间隔（秒）")
    args = ap.parse_args()

    print("首次抓取数据...")
    do_refresh(verbose=False)
    print("首次抓取完成。")

    th = threading.Thread(target=refresh_loop, args=(args.interval,), daemon=True)
    th.start()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), NoCacheHandler) as httpd:
        print("=" * 52)
        print("  P2701-P2705 月差看板（本机实时版）")
        print("=" * 52)
        print(f"  访问地址：http://127.0.0.1:{args.port}/spread.html")
        print(f"  刷新间隔：{args.interval} 秒")
        print("  按 Ctrl+C 停止")
        print("=" * 52)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")


if __name__ == "__main__":
    main()
