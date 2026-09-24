# -*- coding: utf-8 -*-
"""
看板服务：常驻抓取 + 本地 HTTP 服务
这是实现「10 秒真刷新」的推荐方式。

为什么必须走 HTTP：
浏览器对 file:// 协议下相同路径的文件会强制走缓存，加时间戳参数也无效。
用本地 HTTP 服务并返回 no-cache 响应头，才能真正做到每次刷新都读到最新数据。

用法：
    python serve.py                # 默认端口 8765，每 10 秒刷新数据
    python serve.py --port 9000    # 指定端口
    python serve.py --interval 5   # 每 5 秒刷新数据
然后浏览器打开 http://127.0.0.1:8765
"""
import argparse
import http.server
import os
import socketserver
import sys
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Windows 下 stdout 重定向到文件时是块缓冲，会导致日志长时间不显示。
# 这里强制改用行缓冲，保证用户能实时看到刷新进度。
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """禁用缓存的静态文件服务"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # 静默，避免刷屏


def do_refresh(verbose=True):
    """执行一次完整刷新：抓取 -> 算信号 -> 写 dashboard.html"""
    import json
    import fetch_data
    import signal_engine
    import build_dashboard

    t0 = time.time()
    md = fetch_data.fetch_all(verbose=verbose)
    sigs = signal_engine.build_signals(md)
    payload = build_dashboard.build_payload(md, sigs)

    with open(os.path.join(BASE_DIR, "template.html"), "r", encoding="utf-8") as f:
        tpl = f.read()
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("/*__DATA__*/null", data_json)
    # 原子写入，避免浏览器读到写了一半的文件
    import build_dashboard
    build_dashboard.write_atomic(os.path.join(BASE_DIR, "dashboard.html"), html)
    build_dashboard.write_atomic(os.path.join(BASE_DIR, "dashboard.data.json"), data_json)

    if verbose:
        strong = [f"{s['name']}{s['verdict']}" for s in payload["pages"]
                  if abs(s["level"]) == 2]
        tag = "、".join(strong) if strong else "无强信号"
        n_sym = sum(1 for s in payload["pages"] if s["kind"] == "symbol")
        n_ip = sum(1 for s in payload["pages"] if s["kind"] == "interprice")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 已刷新 "
              f"({time.time()-t0:.1f}s) | {n_sym}品种+{n_ip}价差 | {tag}")
    return payload


# 非交易日的刷新间隔（秒）。休市时行情不动，没必要每 10 秒抓一次 ——
# 既省网络开销，也避免日志里刷满"已刷新但数据没变"。
IDLE_INTERVAL = 600


def _should_run_now():
    """判断当前是否处于交易时段，返回 (bool, reason)。"""
    try:
        from trading_calendar import session_trading_check
        from datetime import datetime as _dt
        return session_trading_check(_dt.now())
    except Exception as e:
        # 判断模块出问题时不影响刷新，宁可多跑也别停摆
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
            # 非交易时段：只提示一次，然后按 IDLE_INTERVAL 慢速轮询，
            # 这样假期结束、下一个交易日一开盘就能自动恢复实时刷新。
            if not idle_logged:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易时段"
                      f"（{reason}），刷新降频至 {IDLE_INTERVAL // 60} 分钟一次")
                idle_logged = True
            time.sleep(IDLE_INTERVAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=int, default=10,
                    help="数据刷新间隔（秒）")
    args = ap.parse_args()

    # 先同步跑一次，保证打开页面就有数据
    print("首次抓取数据...")
    do_refresh(verbose=False)
    print("首次抓取完成。")

    th = threading.Thread(target=refresh_loop, args=(args.interval,), daemon=True)
    th.start()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), NoCacheHandler) as httpd:
        print("=" * 52)
        print("  分钟线买卖点信号看板")
        print("=" * 52)
        print(f"  访问地址：http://127.0.0.1:{args.port}")
        print(f"  刷新间隔：{args.interval} 秒")
        print("  按 Ctrl+C 停止")
        print("=" * 52)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")


if __name__ == "__main__":
    main()
