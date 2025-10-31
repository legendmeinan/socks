#!/usr/bin/env python3
"""
代理速度测试脚本
读取 merged_proxies.txt 中的可用代理，测试速度并分类输出
"""

import socks
import socket
import time
import concurrent.futures
from typing import List, Dict, Optional
import re
import sys

# 配置
INPUT_FILE = "merged_proxies.txt"  # 输入文件（可用代理列表）
OUTPUT_FILE = "working_proxies.txt"  # 输出：所有测试成功的代理
OUTPUT_FILE_FAST = "working_proxies_fast.txt"  # 输出：快速代理
OUTPUT_FILE_STATS = "working_proxies_stats.txt"  # 输出：统计信息

MAX_WORKERS = 20  # 并发测试线程数
TEST_TIMEOUT = 10  # 超时时间（秒）
SPEED_TEST_SIZE = 1024 * 50  # 下载 50KB 数据用于速度测试
MAX_LATENCY = 3.0  # 最大延迟（秒）
MIN_SPEED = 10.0  # 最小速度（KB/s）


def parse_proxy(proxy_str: str) -> Optional[Dict]:
    """解析代理字符串"""
    proxy_str = proxy_str.strip()
    
    # 移除协议前缀
    if proxy_str.startswith('socks5://'):
        proxy_str = proxy_str[9:]
    elif proxy_str.startswith('socks4://'):
        proxy_str = proxy_str[9:]
    elif '://' in proxy_str:
        return None
    
    # 解析格式: [username:password@]host:port
    auth_match = re.match(r'^([^:@]+):([^@]+)@([^:]+):(\d+)$', proxy_str)
    if auth_match:
        return {
            'username': auth_match.group(1),
            'password': auth_match.group(2),
            'host': auth_match.group(3),
            'port': int(auth_match.group(4))
        }
    
    # 解析格式: host:port
    simple_match = re.match(r'^([^:]+):(\d+)$', proxy_str)
    if simple_match:
        return {
            'username': None,
            'password': None,
            'host': simple_match.group(1),
            'port': int(simple_match.group(2))
        }
    
    return None


def test_proxy_speed(proxy_str: str) -> Optional[Dict[str, float]]:
    """测试代理速度"""
    proxy_info = parse_proxy(proxy_str)
    if not proxy_info:
        return None
    
    try:
        # 创建 socket
        s = socks.socksocket()
        s.set_proxy(
            proxy_type=socks.SOCKS5,
            addr=proxy_info['host'],
            port=proxy_info['port'],
            username=proxy_info['username'],
            password=proxy_info['password']
        )
        s.settimeout(TEST_TIMEOUT)
        
        # 测试延迟
        latency_start = time.time()
        s.connect(("www.google.com", 80))
        latency = time.time() - latency_start
        
        # 测试下载速度
        request = f"GET /robots.txt HTTP/1.1\r\nHost: www.google.com\r\nConnection: close\r\n\r\n".encode()
        s.sendall(request)
        
        # 下载数据并计时
        download_start = time.time()
        total_bytes = 0
        
        while total_bytes < SPEED_TEST_SIZE:
            chunk = s.recv(4096)
            if not chunk:
                break
            total_bytes += len(chunk)
            
            # 超时检查
            if time.time() - download_start > TEST_TIMEOUT:
                break
        
        download_time = time.time() - download_start
        s.close()
        
        # 计算速度 (KB/s)
        if download_time > 0 and total_bytes > 0:
            speed = (total_bytes / 1024) / download_time
        else:
            speed = 0
        
        return {
            'latency': latency,
            'speed': speed,
            'bytes': total_bytes
        }
        
    except Exception as e:
        return None


def load_proxies(filename: str) -> List[str]:
    """加载代理列表"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            proxies = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return proxies
    except FileNotFoundError:
        print(f"❌ 文件未找到: {filename}")
        return []


def format_time(seconds: float) -> str:
    """格式化时间"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}分钟"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}小时"


def main():
    print("=" * 70)
    print("🚀 SOCKS5 代理速度测试")
    print("=" * 70)
    
    start_time = time.time()
    
    # 加载代理列表
    print(f"📂 读取代理列表: {INPUT_FILE}")
    proxies = load_proxies(INPUT_FILE)
    
    if not proxies:
        print("❌ 没有找到代理")
        # 创建空文件
        for f in [OUTPUT_FILE, OUTPUT_FILE_FAST, OUTPUT_FILE_STATS]:
            open(f, 'w').close()
        sys.exit(1)
    
    total = len(proxies)
    print(f"✅ 加载了 {total} 个代理")
    
    # 测试速度
    print(f"\n🧪 开始速度测试")
    print(f"⚙️  配置: 并发={MAX_WORKERS}, 超时={TEST_TIMEOUT}s")
    print(f"📊 筛选条件: 延迟≤{MAX_LATENCY}s, 速度≥{MIN_SPEED}KB/s")
    print("=" * 70)
    
    speed_results = {}
    working_proxies = []
    fast_proxies = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_proxy = {
            executor.submit(test_proxy_speed, proxy): proxy 
            for proxy in proxies
        }
        
        completed = 0
        last_update = time.time()
        
        for future in concurrent.futures.as_completed(future_to_proxy):
            proxy = future_to_proxy[future]
            completed += 1
            
            try:
                result = future.result()
                if result:
                    latency = result['latency']
                    speed = result['speed']
                    
                    # 保存所有成功测试的代理
                    working_proxies.append(proxy)
                    speed_results[proxy] = result
                    
                    # 检查是否满足快速代理条件
                    if latency <= MAX_LATENCY and speed >= MIN_SPEED:
                        fast_proxies.append(proxy)
                
                # 每秒更新一次进度
                current_time = time.time()
                if current_time - last_update >= 1 or completed == total:
                    progress = (completed / total) * 100
                    working_count = len(working_proxies)
                    fast_count = len(fast_proxies)
                    print(f"[{completed}/{total}] {progress:.1f}% | 可用: {working_count} | 快速: {fast_count}", end='\r')
                    last_update = current_time
                    
            except Exception as e:
                pass
    
    print(f"\n{'=' * 70}")
    
    # 按延迟排序
    working_proxies.sort(key=lambda p: speed_results.get(p, {}).get('latency', 999))
    fast_proxies.sort(key=lambda p: speed_results.get(p, {}).get('latency', 999))
    
    # 保存所有可用代理
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for proxy in working_proxies:
            f.write(proxy + '\n')
    
    # 保存快速代理（带速度信息）
    with open(OUTPUT_FILE_FAST, 'w', encoding='utf-8') as f:
        f.write("# 快速代理列表 (已按延迟排序)\n")
        f.write(f"# 筛选条件: 延迟≤{MAX_LATENCY}s, 速度≥{MIN_SPEED}KB/s\n")
        f.write("# 格式: 代理地址  # 延迟(s) | 速度(KB/s)\n\n")
        for proxy in fast_proxies:
            result = speed_results.get(proxy, {})
            latency = result.get('latency', 0)
            speed = result.get('speed', 0)
            f.write(f"{proxy}  # {latency:.2f}s | {speed:.1f}KB/s\n")
    
    # 计算统计信息
    elapsed_time = time.time() - start_time
    
    # 保存统计信息
    with open(OUTPUT_FILE_STATS, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write("代理速度测试统计\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"测试代理总数: {total}\n")
        f.write(f"可用代理数: {len(working_proxies)}\n")
        f.write(f"快速代理数: {len(fast_proxies)}\n")
        f.write(f"测试耗时: {format_time(elapsed_time)}\n\n")
        
        if total > 0:
            success_rate = (len(working_proxies) / total) * 100
            f.write(f"成功率: {success_rate:.2f}%\n")
        
        if len(working_proxies) > 0:
            fast_rate = (len(fast_proxies) / len(working_proxies)) * 100
            f.write(f"快速率: {fast_rate:.2f}% (在可用代理中)\n")
        
        f.write(f"\n筛选条件:\n")
        f.write(f"  - 最大延迟: {MAX_LATENCY}s\n")
        f.write(f"  - 最小速度: {MIN_SPEED}KB/s\n")
        
        # 显示速度分布
        if speed_results:
            latencies = [r['latency'] for r in speed_results.values()]
            speeds = [r['speed'] for r in speed_results.values()]
            
            f.write(f"\n速度统计:\n")
            f.write(f"  - 平均延迟: {sum(latencies)/len(latencies):.2f}s\n")
            f.write(f"  - 最小延迟: {min(latencies):.2f}s\n")
            f.write(f"  - 最大延迟: {max(latencies):.2f}s\n")
            f.write(f"  - 平均速度: {sum(speeds)/len(speeds):.1f}KB/s\n")
            f.write(f"  - 最大速度: {max(speeds):.1f}KB/s\n")
        
        f.write("\n" + "=" * 70 + "\n")
    
    # 输出结果
    print("✅ 测试完成!")
    print("=" * 70)
    print(f"⏱️  耗时: {format_time(elapsed_time)}")
    print(f"📊 测试代理: {total}")
    print(f"✅ 可用代理: {len(working_proxies)} ({len(working_proxies)/total*100:.1f}%)")
    print(f"🚀 快速代理: {len(fast_proxies)} ({len(fast_proxies)/len(working_proxies)*100:.1f}% of working)" if working_proxies else "🚀 快速代理: 0")
    print("=" * 70)
    print(f"💾 输出文件:")
    print(f"   - {OUTPUT_FILE} (所有可用代理)")
    print(f"   - {OUTPUT_FILE_FAST} (快速代理)")
    print(f"   - {OUTPUT_FILE_STATS} (统计信息)")
    print()
    
    if fast_proxies:
        print(f"🎯 前 5 个最快的代理:")
        for i, proxy in enumerate(fast_proxies[:5], 1):
            result = speed_results[proxy]
            print(f"   {i}. {proxy}")
            print(f"      延迟: {result['latency']:.2f}s | 速度: {result['speed']:.1f}KB/s")


if __name__ == "__main__":
    main()
