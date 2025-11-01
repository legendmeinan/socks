#!/usr/bin/env python3
"""
SOCKS5 代理分片测试脚本
支持两个阶段：
1. availability - 测试代理可用性（从 API 获取并分片）
2. speed - 测试代理速度（从输入文件读取）
"""

import argparse
import requests
import socks
import socket
import time
import concurrent.futures
from typing import List, Dict, Optional
import re
import sys

# 配置
MAX_PROXIES = 1000
TEST_TARGETS = [
    ("www.google.com", 80),
    ("www.cloudflare.com", 80),
    ("1.1.1.1", 80)
]
TEST_TIMEOUT = 10
MAX_WORKERS = 20
URL_FILE = "url.txt"
RETRY_FAILED = 1
MIN_SUCCESS_RATE = 0.5

# 速度测试配置
MAX_LATENCY = 3.0
MIN_SPEED = 50
SPEED_TEST_SIZE = 1024 * 1000


class ShardProxyTester:
    """分片代理测试器"""
    
    def __init__(self, shard_id: int, total_shards: int):
        self.shard_id = shard_id
        self.total_shards = total_shards
        self.stats = {
            'fetched': 0,
            'tested': 0,
            'working': 0,
            'fast': 0
        }
    
    def read_api_urls(self, filename: str) -> List[str]:
        """读取 API URL 列表"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f 
                       if line.strip() and not line.startswith('#')
                       and (line.startswith('http://') or line.startswith('https://'))]
                return urls
        except FileNotFoundError:
            print(f"❌ 文件 {filename} 不存在")
            return []
    
    def fetch_all_proxies(self, urls: List[str]) -> List[str]:
        """从所有 URL 获取代理"""
        all_proxies = []
        
        for url in urls:
            try:
                print(f"🔍 获取: {url}")
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(url, timeout=30, headers=headers)
                response.raise_for_status()
                
                proxies = [line.strip() for line in response.text.split('\n')
                          if line.strip() and not line.startswith('#')]
                all_proxies.extend(proxies)
                print(f"   ✅ 获取 {len(proxies)} 个")
                
            except Exception as e:
                print(f"   ❌ 失败: {e}")
        
        # 去重并限制数量
        unique = list(set(all_proxies))[:MAX_PROXIES]
        print(f"\n📊 总计: {len(all_proxies)} → 去重: {len(unique)}")
        return unique
    
    def split_for_shard(self, items: List[str]) -> List[str]:
        """将列表分配到当前分片"""
        total = len(items)
        shard_size = (total + self.total_shards - 1) // self.total_shards
        start = (self.shard_id - 1) * shard_size
        end = min(start + shard_size, total)
        
        shard_items = items[start:end]
        print(f"🔢 分片 {self.shard_id}/{self.total_shards}: {len(shard_items)} 项 (索引 {start}-{end-1})")
        return shard_items
    
    def parse_proxy(self, proxy_str: str) -> Optional[Dict]:
        """解析代理字符串"""
        proxy_str = proxy_str.strip()
        
        if proxy_str.startswith('socks5://'):
            proxy_str = proxy_str[9:]
        elif proxy_str.startswith('socks4://'):
            proxy_str = proxy_str[9:]
        elif '://' in proxy_str:
            return None
        
        # 带认证: user:pass@host:port
        auth_match = re.match(r'^([^:@]+):([^@]+)@([^:]+):(\d+)$', proxy_str)
        if auth_match:
            return {
                'username': auth_match.group(1),
                'password': auth_match.group(2),
                'host': auth_match.group(3),
                'port': int(auth_match.group(4))
            }
        
        # 简单: host:port
        simple_match = re.match(r'^([^:]+):(\d+)$', proxy_str)
        if simple_match:
            return {
                'username': None,
                'password': None,
                'host': simple_match.group(1),
                'port': int(simple_match.group(2))
            }
        
        return None
    
    def test_proxy_availability(self, proxy_str: str) -> bool:
        """测试代理可用性"""
        proxy_info = self.parse_proxy(proxy_str)
        if not proxy_info:
            return False
        
        success_count = 0
        for target in TEST_TARGETS:
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
                
                host, port = target
                s.connect((host, port))
                request = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
                s.sendall(request)
                response = s.recv(100)
                s.close()
                
                if b"HTTP" in response or b"html" in response.lower():
                    success_count += 1
            except:
                pass
        
        return (success_count / len(TEST_TARGETS)) >= MIN_SUCCESS_RATE
    
    def test_proxy_speed(self, proxy_str: str) -> Optional[Dict]:
        """测试代理速度"""
        proxy_info = self.parse_proxy(proxy_str)
        if not proxy_info:
            return None
        
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
            
            # 测试延迟
            latency_start = time.time()
            s.connect(("www.google.com", 80))
            latency = time.time() - latency_start
            
            # 测试速度
            request = b"GET /robots.txt HTTP/1.1\r\nHost: www.google.com\r\nConnection: close\r\n\r\n"
            s.sendall(request)
            
            download_start = time.time()
            total_bytes = 0
            
            while total_bytes < SPEED_TEST_SIZE:
                chunk = s.recv(4096)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if time.time() - download_start > TEST_TIMEOUT:
                    break
            
            download_time = time.time() - download_start
            s.close()
            
            speed = (total_bytes / 1024) / download_time if download_time > 0 else 0
            
            return {
                'latency': latency,
                'speed': speed,
                'bytes': total_bytes
            }
        except:
            return None
    
    def test_availability_batch(self, proxies: List[str]) -> List[str]:
        """批量测试可用性"""
        working = []
        total = len(proxies)
        
        print(f"\n🧪 测试可用性 (分片 {self.shard_id}/{self.total_shards})")
        print(f"⚙️  并发={MAX_WORKERS}, 超时={TEST_TIMEOUT}s")
        print("=" * 60)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_proxy = {
                executor.submit(self.test_proxy_availability, p): p 
                for p in proxies
            }
            
            completed = 0
            for future in concurrent.futures.as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                completed += 1
                
                try:
                    if future.result():
                        working.append(proxy)
                    
                    if completed % 10 == 0 or completed == total:
                        progress = (completed / total) * 100
                        print(f"[{completed}/{total}] {progress:.1f}% | 可用: {len(working)}", end='\r')
                except:
                    pass
        
        print(f"\n{'=' * 60}")
        print(f"✅ 完成: {len(working)}/{total} 可用\n")
        
        self.stats['tested'] = total
        self.stats['working'] = len(working)
        
        return working
    
    def test_speed_batch(self, proxies: List[str]) -> List[str]:
        """批量测试速度"""
        fast = []
        total = len(proxies)
        
        print(f"\n🚀 测试速度 (分片 {self.shard_id}/{self.total_shards})")
        print(f"⚙️  最大延迟={MAX_LATENCY}s, 最小速度={MIN_SPEED}KB/s")
        print("=" * 60)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_proxy = {
                executor.submit(self.test_proxy_speed, p): p 
                for p in proxies
            }
            
            completed = 0
            for future in concurrent.futures.as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                completed += 1
                
                try:
                    result = future.result()
                    if result:
                        if result['latency'] <= MAX_LATENCY and result['speed'] >= MIN_SPEED:
                            fast.append(proxy)
                    
                    if completed % 10 == 0 or completed == total:
                        progress = (completed / total) * 100
                        print(f"[{completed}/{total}] {progress:.1f}% | 快速: {len(fast)}", end='\r')
                except:
                    pass
        
        print(f"\n{'=' * 60}")
        print(f"✅ 完成: {len(fast)}/{total} 快速\n")
        
        self.stats['fast'] = len(fast)
        
        return fast
    
    def run_availability_stage(self, output_file: str):
        """运行可用性测试阶段"""
        print(f"🎯 阶段: 可用性测试")
        print(f"📍 分片: {self.shard_id}/{self.total_shards}\n")
        
        # 读取并获取所有代理
        urls = self.read_api_urls(URL_FILE)
        if not urls:
            print("❌ 没有 API URLs")
            sys.exit(1)
        
        all_proxies = self.fetch_all_proxies(urls)
        if not all_proxies:
            print("❌ 没有获取到代理")
            sys.exit(1)
        
        # 分配到当前分片
        shard_proxies = self.split_for_shard(all_proxies)
        
        # 测试可用性
        working = self.test_availability_batch(shard_proxies)
        
        # 保存结果
        if working:
            with open(output_file, 'w') as f:
                for proxy in sorted(working):
                    f.write(proxy + '\n')
            print(f"💾 已保存: {output_file} ({len(working)} 个)")
        else:
            print("⚠️  没有可用代理")
    
    def run_speed_stage(self, input_file: str, output_file: str):
        """运行速度测试阶段"""
        print(f"🎯 阶段: 速度测试")
        print(f"📍 输入: {input_file}\n")
        
        # 读取输入文件
        try:
            with open(input_file, 'r') as f:
                proxies = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            print(f"❌ 文件不存在: {input_file}")
            sys.exit(1)
        
        if not proxies:
            print("❌ 输入文件为空")
            sys.exit(1)
        
        print(f"📊 读取 {len(proxies)} 个代理")
        
        # 测试速度
        fast = self.test_speed_batch(proxies)
        
        # 保存结果
        if fast:
            with open(output_file, 'w') as f:
                for proxy in sorted(fast):
                    f.write(proxy + '\n')
            print(f"💾 已保存: {output_file} ({len(fast)} 个)")
        else:
            print("⚠️  没有快速代理")


def main():
    parser = argparse.ArgumentParser(description='SOCKS5 代理分片测试')
    parser.add_argument('--stage', choices=['availability', 'speed'], required=True,
                       help='测试阶段')
    parser.add_argument('--shard', type=int, help='当前分片编号 (1-based)')
    parser.add_argument('--total-shards', type=int, help='总分片数')
    parser.add_argument('--input', help='输入文件 (speed 阶段)')
    parser.add_argument('--output', help='输出文件')
    
    args = parser.parse_args()
    
    if args.stage == 'availability':
        if not args.shard or not args.total_shards:
            print("❌ availability 阶段需要 --shard 和 --total-shards")
            sys.exit(1)
        
        output = args.output or f"working_proxies_shard{args.shard}.txt"
        tester = ShardProxyTester(args.shard, args.total_shards)
        tester.run_availability_stage(output)
        
    elif args.stage == 'speed':
        if not args.input:
            print("❌ speed 阶段需要 --input")
            sys.exit(1)
        
        output = args.output or "fast_proxies.txt"
        tester = ShardProxyTester(1, 1)  # 速度测试不需要分片编号
        tester.run_speed_stage(args.input, output)


if __name__ == "__main__":
    main()
