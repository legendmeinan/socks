#!/usr/bin/env python3
"""
代理可用性测试脚本 - Worker 版本
用于 GitHub Actions 并发测试，每个 worker 测试一部分代理
"""

import sys
import socks
import socket
import time
import re
from typing import List, Dict, Optional

# 配置
PROXY_FILE = "url.txt"  # 代理来源文件（可以是代理列表或 API URL）
TEST_TARGETS = [
    ("www.google.com", 80),
    ("www.cloudflare.com", 80),
    ("1.1.1.1", 80)
]  # 测试目标
TEST_TIMEOUT = 10  # 超时时间（秒）
MIN_SUCCESS_RATE = 0.5  # 最小成功率（至少一半目标成功）
RETRY_FAILED = 1  # 失败重试次数


def load_proxies_from_file(filename: str, max_proxies: int = None) -> List[str]:
    """
    从文件加载代理列表
    支持：
    1. 直接的代理列表
    2. API URL（需要先下载）
    """
    import requests
    
    proxies = []
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        # 检查是否是 API URL
        for line in lines:
            if line.startswith('http://') or line.startswith('https://'):
                # 从 API 获取代理
                print(f"📡 从 API 获取代理: {line}")
                try:
                    response = requests.get(line, timeout=30)
                    response.raise_for_status()
                    api_proxies = [p.strip() for p in response.text.split('\n') 
                                   if p.strip() and not p.startswith('#')]
                    proxies.extend(api_proxies)
                    print(f"   ✅ 获取到 {len(api_proxies)} 个代理")
                except Exception as e:
                    print(f"   ❌ 获取失败: {e}")
            else:
                # 直接的代理地址
                proxies.append(line)
        
        # 去重
        proxies = list(set(proxies))
        
        # 应用数量限制
        if max_proxies and len(proxies) > max_proxies:
            print(f"⚠️  代理总数 {len(proxies)} 超过限制，仅使用前 {max_proxies} 个")
            proxies = proxies[:max_proxies]
        
        return proxies
        
    except FileNotFoundError:
        print(f"❌ 文件未找到: {filename}")
        return []
    except Exception as e:
        print(f"❌ 加载代理失败: {e}")
        return []


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


def test_proxy_with_target(proxy_info: Dict, target: tuple) -> bool:
    """使用指定目标测试代理"""
    try:
        s = socks.socksocket()
        s.set_proxy(
            proxy_type=socks.SOCKS5,
            addr=proxy_info['host'],
            port=proxy_info['port'],
            username=proxy_info['username'],
            password=proxy_info['password']
        )
        s.settimeout(TEST_TIMEOUT)
        
        # 连接到目标
        host, port = target
        s.connect((host, port))
        
        # 发送简单的 HTTP 请求
        request = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        s.sendall(request)
        
        # 接收响应
        response = s.recv(100)
        s.close()
        
        # 检查响应
        return b"HTTP" in response or b"html" in response.lower()
    except:
        return False


def test_proxy(proxy_str: str) -> bool:
    """测试单个代理是否可用（多目标测试）"""
    proxy_info = parse_proxy(proxy_str)
    if not proxy_info:
        return False
    
    # 测试多个目标
    success_count = 0
    for target in TEST_TARGETS:
        if test_proxy_with_target(proxy_info, target):
            success_count += 1
    
    # 计算成功率
    success_rate = success_count / len(TEST_TARGETS)
    return success_rate >= MIN_SUCCESS_RATE


def test_proxy_with_retry(proxy_str: str) -> bool:
    """带重试的代理测试"""
    for attempt in range(RETRY_FAILED + 1):
        if test_proxy(proxy_str):
            return True
        if attempt < RETRY_FAILED:
            time.sleep(0.5)
    return False


def main():
    if len(sys.argv) < 3:
        print("用法: python test_availability.py <worker_id> <total_workers> [max_proxies]")
        print("示例: python test_availability.py 0 10 200")
        sys.exit(1)
    
    worker_id = int(sys.argv[1])
    total_workers = int(sys.argv[2])
    max_proxies = int(sys.argv[3]) if len(sys.argv) > 3 else None
    
    print("=" * 70)
    print(f"🚀 SOCKS5 代理可用性测试 - Worker {worker_id}/{total_workers-1}")
    print("=" * 70)
    
    if max_proxies:
        print(f"⚙️  最大代理数限制: {max_proxies}")
    
    print(f"🎯 测试目标: {len(TEST_TARGETS)} 个")
    print(f"⏱️  超时时间: {TEST_TIMEOUT}s")
    print(f"🔄 重试次数: {RETRY_FAILED}")
    print(f"📊 最小成功率: {MIN_SUCCESS_RATE*100}%")
    print()
    
    # 加载所有代理
    print(f"📂 加载代理列表: {PROXY_FILE}")
    all_proxies = load_proxies_from_file(PROXY_FILE, max_proxies)
    
    if not all_proxies:
        print("❌ 没有可用的代理")
        # 创建空输出文件
        output_file = f'available_proxies_{worker_id}.txt'
        open(output_file, 'w').close()
        sys.exit(1)
    
    print(f"✅ 加载了 {len(all_proxies)} 个代理")
    
    # 分配任务给当前 worker
    chunk_size = len(all_proxies) // total_workers
    start_idx = worker_id * chunk_size
    
    # 最后一个 worker 处理剩余的所有代理
    if worker_id == total_workers - 1:
        end_idx = len(all_proxies)
    else:
        end_idx = start_idx + chunk_size
    
    my_proxies = all_proxies[start_idx:end_idx]
    
    print(f"\n📦 Worker {worker_id} 任务分配:")
    print(f"   - 索引范围: {start_idx} - {end_idx-1}")
    print(f"   - 代理数量: {len(my_proxies)}")
    print()
    
    # 测试代理
    print("🧪 开始测试...")
    print("-" * 70)
    
    available = []
    start_time = time.time()
    
    for i, proxy in enumerate(my_proxies, 1):
        progress = (i / len(my_proxies)) * 100
        print(f"[{i}/{len(my_proxies)}] {progress:.1f}% | 测试: {proxy[:50]}...", end='')
        
        if test_proxy_with_retry(proxy):
            print(" ✅ 可用")
            available.append(proxy)
        else:
            print(" ❌ 不可用")
    
    elapsed_time = time.time() - start_time
    
    # 保存结果
    output_file = f'available_proxies_{worker_id}.txt'
    with open(output_file, 'w', encoding='utf-8') as f:
        for proxy in available:
            f.write(f"{proxy}\n")
    
    # 输出统计
    print("-" * 70)
    print()
    print("=" * 70)
    print(f"✅ Worker {worker_id} 完成!")
    print("=" * 70)
    print(f"⏱️  耗时: {elapsed_time:.1f}秒")
    print(f"📊 测试代理: {len(my_proxies)}")
    print(f"✅ 可用代理: {len(available)}")
    
    if len(my_proxies) > 0:
        success_rate = (len(available) / len(my_proxies)) * 100
        print(f"📈 成功率: {success_rate:.2f}%")
    
    print(f"💾 输出文件: {output_file}")
    print("=" * 70)
    
    # 显示前几个可用代理
    if available:
        print(f"\n🎯 可用代理示例 (前 5 个):")
        for proxy in available[:5]:
            print(f"   - {proxy}")
    
    print()


if __name__ == "__main__":
    main()
