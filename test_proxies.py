#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SOCKS5 Proxy Tester (for GitHub Actions)
带速度测试，但输出文件不含注释
"""

import os
import sys
import time
import requests
import socks
import socket
import concurrent.futures
from datetime import datetime

MAX_PROXIES = 100
TEST_URL = "http://example.com/"
TEST_TIMEOUT = 10
MAX_WORKERS = 30
URL_FILE = "url.txt"
OUTPUT_FILE = "working_proxies.txt"
OUTPUT_FILE_FAST = "working_proxies_fast.txt"
STATS_FILE = "working_proxies_stats.txt"
MAX_LATENCY = 3.0
MIN_SPEED = 100
IN_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"


def log(msg):
    print(msg, flush=True)


def safe_write(filename, content=""):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)


def read_urls(file):
    if not os.path.exists(file):
        safe_write(file, """# 示例 url.txt
https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt
""")
        log(f"⚠️ 未找到 {file}，已创建示例文件。请添加你的代理源。")
        return []
    with open(file, "r", encoding="utf-8") as f:
        urls = [x.strip() for x in f if x.strip() and not x.startswith("#")]
    log(f"📋 读取到 {len(urls)} 个代理源")
    return urls


def fetch_proxies(url, limit):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, timeout=15, headers=headers)
        res.raise_for_status()
        lines = [x.strip() for x in res.text.splitlines() if ":" in x]
        log(f"✅ {url} → {len(lines)} 个代理")
        return lines[:limit]
    except Exception as e:
        log(f"❌ 获取失败: {url} ({e})")
        return []


def test_proxy(proxy):
    start_time = time.time()
    proxy_clean = proxy.replace("socks5://", "")
    try:
        parts = proxy_clean.split(":")
        host, port = parts[-2], int(parts[-1])

        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, host, port)
        s.settimeout(TEST_TIMEOUT)
        s.connect(("1.1.1.1", 80))
        s.close()

        proxies = {"http": f"socks5://{proxy_clean}", "https": f"socks5://{proxy_clean}"}
        t1 = time.time()
        resp = requests.get(TEST_URL, proxies=proxies, timeout=TEST_TIMEOUT)
        size = len(resp.content)
        latency = t1 - start_time
        speed = (size / 1024) / latency if latency > 0 else 0
        return proxy_clean, latency, speed
    except Exception:
        return None


def main():
    start = time.time()
    log("=" * 70)
    log("🚀 SOCKS5 Proxy Tester (带速度测试, 输出纯地址)")
    log("=" * 70)

    urls = read_urls(URL_FILE)
    if not urls:
        safe_write(OUTPUT_FILE)
        safe_write(OUTPUT_FILE_FAST)
        safe_write(STATS_FILE, "No URLs found.\n")
        return

    all_proxies = []
    for url in urls:
        all_proxies.extend(fetch_proxies(url, MAX_PROXIES - len(all_proxies)))
        if len(all_proxies) >= MAX_PROXIES:
            break
        time.sleep(0.5)

    if not all_proxies:
        log("❌ 未获取到代理")
        safe_write(OUTPUT_FILE)
        safe_write(OUTPUT_FILE_FAST)
        safe_write(STATS_FILE, "No proxies fetched.\n")
        return

    log(f"🧪 开始测试 {len(all_proxies)} 个代理...")
    working = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(test_proxy, p): p for p in all_proxies}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            result = fut.result()
            if result:
                working.append(result)
            if i % 20 == 0 or IN_GITHUB:
                log(f"进度: {i}/{len(all_proxies)} ✅={len(working)}")

    working_sorted = sorted(working, key=lambda x: (-x[2], x[1]))
    proxies_only = [w[0] for w in working_sorted]
    safe_write(OUTPUT_FILE, "\n".join(proxies_only))

    fast = [p for p, latency, speed in working_sorted if latency <= MAX_LATENCY and speed >= MIN_SPEED]
    safe_write(OUTPUT_FILE_FAST, "\n".join(fast))

    lines = [
        f"{p} | 延迟: {latency:.2f}s | 速度: {speed:.1f}KB/s"
        for p, latency, speed in working_sorted
    ]
    stats = (
        f"测试时间: {datetime.now()}\n"
        f"总代理数: {len(all_proxies)}\n"
        f"可用代理: {len(working)}\n"
        f"快速代理: {len(fast)} (延迟≤{MAX_LATENCY}s 且速度≥{MIN_SPEED}KB/s)\n"
        f"耗时: {time.time() - start:.1f}s\n\n详细列表:\n" + "\n".join(lines)
    )
    safe_write(STATS_FILE, stats)
    log(f"✅ 可用: {len(working)} 个, 快速: {len(fast)} 个")
    log("🎉 测试完成！")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ 脚本异常: {e}")
        for f in [OUTPUT_FILE, OUTPUT_FILE_FAST, STATS_FILE]:
            safe_write(f)
        sys.exit(0)
