#!/usr/bin/env python3
"""
SOCKS5 代理测试脚本
从 url.txt 读取 API 链接，测试其中的 SOCKS5 代理是否可用
包含速度测试，过滤慢速代理
"""

import requests
import socks
import socket
import time
import concurrent.futures
from typing import List, Set, Optional, Dict, Tuple
import re
import sys
from datetime import datetime

# 配置
TEST_TARGETS = [
    ("www.google.com", 80),
    ("www.cloudflare.com", 80),
    ("1.1.1.1", 80)
]  # 多个测试目标，增加可靠性
TEST_TIMEOUT = 10  # 每个代理的测试超时时间（秒）
MAX_WORKERS = 20  # 并发测试的最大线程数
URL_FILE = "url.txt"  # API 链接列表文件
OUTPUT_FILE = "working_proxies.txt"  # 输出文件
OUTPUT_FILE_FAST = "working_proxies_fast.txt"  # 快速代理输出文件
RETRY_FAILED = 1  # 失败后重试次数
MIN_SUCCESS_RATE = 0.5  # 最小成功率（至少一半的测试目标成功）

# 速度测试配置
SPEED_TEST_ENABLED = True  # 是否启用速度测试
SPEED_TEST_URL = "http://www.google.com/robots.txt"  # 速度测试 URL（小文件）
SPEED_TEST_SIZE = 1024 * 50  # 下载 50KB 数据用于速度测试
MAX_LATENCY = 3.0  # 最大延迟（秒），超过此值的代理被认为太慢
MIN_SPEED = 2048.0  # 最小速度（KB/s），低于此速度的代理被过滤


class ProxyTester:
    """代理测试器"""
    
    def __init__(self):
        self.total_fetched = 0
        self.total_unique = 0
        self.total_tested = 0
        self.total_working = 0
        self.total_fast = 0
        self.start_time = time.time()
        self.speed_results = {}  # 存储每个代理的速度测试结果
    
    def read_api_urls(self, filename: str) -> List[str]:
        """读取 API URL 列表"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                urls = []
                for line in f:
                    line = line.strip()
                    # 跳过空行和注释
                    if not line or line.startswith('#'):
                        continue
                    # 验证是否为 HTTP/HTTPS URL
                    if line.startswith('http://') or line.startswith('https://'):
                        urls.append(line)
                    else:
                        print(f"⚠️  跳过无效 URL: {line}")
                
                print(f"📋 读取到 {len(urls)} 个 API 链接")
                return urls
        except FileNotFoundError:
            print(f"❌ 文件 {filename} 不存在，创建示例文件...")
            self.create_example_url_file(filename)
            return []
    
    def create_example_url_file(self, filename: str):
        """创建示例 URL 文件"""
        example_content = """# SOCKS5 代理列表 API 链接
# 每行一个 HTTP/HTTPS 链接

https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt
https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt
https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt
https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5
"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(example_content)
        print(f"✅ 已创建示例文件: {filename}")
    
    def fetch_proxies_from_url(self, url: str) -> List[str]:
        """从 URL 获取代理列表"""
        try:
            print(f"🔍 正在获取: {url}")
            
            # 设置请求头，模拟浏览器
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, timeout=30, headers=headers)
            response.raise_for_status()
            
            proxies = []
            for line in response.text.split('\n'):
                line = line.strip()
                # 跳过空行和注释
                if not line or line.startswith('#'):
                    continue
                proxies.append(line)
            
            print(f"   ✅ 获取到 {len(proxies)} 个代理")
            return proxies
        except requests.exceptions.Timeout:
            print(f"   ⏱️  超时: {url}")
            return []
        except requests.exceptions.RequestException as e:
            print(f"   ❌ 获取失败: {e}")
            return []
        except Exception as e:
            print(f"   ❌ 未知错误: {e}")
            return []
    
    def parse_proxy(self, proxy_str: str) -> Optional[Dict]:
        """解析代理字符串，返回 host, port, username, password"""
        proxy_str = proxy_str.strip()
        
        # 移除协议前缀
        if proxy_str.startswith('socks5://'):
            proxy_str = proxy_str[9:]
        elif proxy_str.startswith('socks4://'):
            proxy_str = proxy_str[9:]
        elif '://' in proxy_str:
            # 跳过其他协议
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
    
    def test_proxy_with_target(self, proxy_info: Dict, target: tuple) -> bool:
        """使用指定目标测试代理"""
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
    
    def test_proxy(self, proxy_str: str) -> bool:
        """测试单个代理是否可用（多目标测试）"""
        proxy_info = self.parse_proxy(proxy_str)
        if not proxy_info:
            return False
        
        # 测试多个目标
        success_count = 0
        for target in TEST_TARGETS:
            if self.test_proxy_with_target(proxy_info, target):
                success_count += 1
        
        # 计算成功率
        success_rate = success_count / len(TEST_TARGETS)
        return success_rate >= MIN_SUCCESS_RATE
    
    def test_proxy_speed(self, proxy_str: str) -> Optional[Dict[str, float]]:
        """测试代理速度，返回 {latency, speed} 或 None"""
        proxy_info = self.parse_proxy(proxy_str)
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
    
    def test_proxy_with_retry(self, proxy_str: str) -> bool:
        """带重试的代理测试"""
        for attempt in range(RETRY_FAILED + 1):
            if self.test_proxy(proxy_str):
                return True
            if attempt < RETRY_FAILED:
                time.sleep(0.5)  # 重试前短暂等待
        return False
    
    def test_proxies_batch(self, proxies: List[str]) -> List[str]:
        """批量测试代理"""
        working_proxies = []
        total = len(proxies)
        
        print(f"\n🧪 阶段 1: 测试代理可用性")
        print(f"⚙️  配置: 并发={MAX_WORKERS}, 超时={TEST_TIMEOUT}s, 重试={RETRY_FAILED}次")
        print(f"🎯 测试目标: {len(TEST_TARGETS)} 个 (成功率≥{MIN_SUCCESS_RATE*100}%)")
        print("=" * 70)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # 提交所有测试任务
            future_to_proxy = {
                executor.submit(self.test_proxy_with_retry, proxy): proxy 
                for proxy in proxies
            }
            
            completed = 0
            last_update = time.time()
            
            for future in concurrent.futures.as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                completed += 1
                
                try:
                    if future.result():
                        working_proxies.append(proxy)
                        status = "✅"
                    else:
                        status = "❌"
                    
                    # 每秒最多输出一次进度
                    current_time = time.time()
                    if current_time - last_update >= 1 or completed == total:
                        progress = (completed / total) * 100
                        working_count = len(working_proxies)
                        print(f"[{completed}/{total}] {progress:.1f}% | 可用: {working_count}", end='\r')
                        last_update = current_time
                        
                except Exception as e:
                    pass
        
        print(f"\n{'=' * 70}")
        print(f"✅ 阶段 1 完成: 找到 {len(working_proxies)} 个可用代理\n")
        
        return working_proxies
    
    def test_speed_batch(self, proxies: List[str]) -> List[str]:
        """批量测试代理速度"""
        if not SPEED_TEST_ENABLED or not proxies:
            return proxies
        
        fast_proxies = []
        total = len(proxies)
        
        print(f"🚀 阶段 2: 测试代理速度")
        print(f"⚙️  配置: 最大延迟={MAX_LATENCY}s, 最小速度={MIN_SPEED}KB/s")
        print("=" * 70)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_proxy = {
                executor.submit(self.test_proxy_speed, proxy): proxy 
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
                        
                        # 保存速度测试结果
                        self.speed_results[proxy] = result
                        
                        # 检查是否满足速度要求
                        if latency <= MAX_LATENCY and speed >= MIN_SPEED:
                            fast_proxies.append(proxy)
                            status = f"✅ {latency:.2f}s {speed:.1f}KB/s"
                        else:
                            status = f"🐌 {latency:.2f}s {speed:.1f}KB/s (太慢)"
                    else:
                        status = "❌ 速度测试失败"
                    
                    # 实时显示进度
                    current_time = time.time()
                    if current_time - last_update >= 1 or completed == total:
                        progress = (completed / total) * 100
                        fast_count = len(fast_proxies)
                        print(f"[{completed}/{total}] {progress:.1f}% | 快速: {fast_count}", end='\r')
                        last_update = current_time
                        
                except Exception as e:
                    pass
        
        print(f"\n{'=' * 70}")
        print(f"✅ 阶段 2 完成: 找到 {len(fast_proxies)} 个快速代理\n")
        
        # 按速度排序（延迟从低到高）
        fast_proxies.sort(key=lambda p: self.speed_results.get(p, {}).get('latency', 999))
        
        return fast_proxies
    
    def format_time(self, seconds: float) -> str:
        """格式化时间"""
        if seconds < 60:
            return f"{seconds:.1f}秒"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}分钟"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}小时"
    
    def save_results(self, working_proxies: List[str], fast_proxies: List[str], filename: str):
        """保存结果并生成统计信息"""
        # 保存所有可用代理
        with open(filename, 'w', encoding='utf-8') as f:
            for proxy in sorted(working_proxies):
                f.write(proxy + '\n')
        
        # 保存快速代理（带速度信息）
        if SPEED_TEST_ENABLED and fast_proxies:
            with open(OUTPUT_FILE_FAST, 'w', encoding='utf-8') as f:
                f.write("# 快速代理列表 (已按延迟排序)\n")
                f.write("# 格式: 代理地址 | 延迟(s) | 速度(KB/s)\n\n")
                for proxy in fast_proxies:
                    result = self.speed_results.get(proxy, {})
                    latency = result.get('latency', 0)
                    speed = result.get('speed', 0)
                    f.write(f"{proxy}  # {latency:.2f}s | {speed:.1f}KB/s\n")
        
        # 生成统计文件
        stats_file = filename.replace('.txt', '_stats.txt')
        elapsed_time = time.time() - self.start_time
        
        with open(stats_file, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("SOCKS5 代理测试统计报告\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"耗时: {self.format_time(elapsed_time)}\n\n")
            f.write(f"📥 获取代理总数: {self.total_fetched}\n")
            f.write(f"🔄 去重后数量: {self.total_unique}\n")
            f.write(f"🧪 测试代理数: {self.total_tested}\n")
            f.write(f"✅ 可用代理数: {self.total_working}\n")
            
            if SPEED_TEST_ENABLED:
                f.write(f"🚀 快速代理数: {self.total_fast}\n")
            
            f.write("\n")
            
            if self.total_tested > 0:
                success_rate = (self.total_working / self.total_tested) * 100
                f.write(f"📊 可用率: {success_rate:.2f}%\n")
                
                if SPEED_TEST_ENABLED and self.total_working > 0:
                    fast_rate = (self.total_fast / self.total_working) * 100
                    f.write(f"⚡ 快速率: {fast_rate:.2f}% (在可用代理中)\n")
            
            if SPEED_TEST_ENABLED:
                f.write(f"\n速度测试配置:\n")
                f.write(f"  - 最大延迟: {MAX_LATENCY}s\n")
                f.write(f"  - 最小速度: {MIN_SPEED}KB/s\n")
            
            f.write("\n" + "=" * 70 + "\n")
        
        print(f"📊 统计信息已保存到: {stats_file}")
    
    def run(self):
        """运行主程序"""
        print("=" * 70)
        print("🚀 SOCKS5 代理自动测试工具 (含速度测试)")
        print("=" * 70)
        print(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # 读取 API URL 列表
        api_urls = self.read_api_urls(URL_FILE)
        if not api_urls:
            print("❌ 没有找到有效的 API 链接")
            sys.exit(1)
        
        # 从所有 URL 获取代理
        print("📡 开始获取代理列表...")
        print("-" * 70)
        all_proxies = []
        for url in api_urls:
            proxies = self.fetch_proxies_from_url(url)
            all_proxies.extend(proxies)
            time.sleep(1)  # 避免请求过快
        
        self.total_fetched = len(all_proxies)
        
        if not all_proxies:
            print("\n❌ 没有获取到任何代理")
            with open(OUTPUT_FILE, 'w') as f:
                pass
            sys.exit(1)
        
        # 去重
        unique_proxies = list(set(all_proxies))
        self.total_unique = len(unique_proxies)
        self.total_tested = len(unique_proxies)
        
        print(f"\n📊 统计:")
        print(f"   - 获取总数: {self.total_fetched}")
        print(f"   - 去重后: {self.total_unique}")
        
        # 阶段 1: 测试可用性
        working_proxies = self.test_proxies_batch(unique_proxies)
        self.total_working = len(working_proxies)
        
        # 阶段 2: 测试速度
        fast_proxies = []
        if SPEED_TEST_ENABLED and working_proxies:
            fast_proxies = self.test_speed_batch(working_proxies)
            self.total_fast = len(fast_proxies)
        
        # 计算耗时
        elapsed_time = time.time() - self.start_time
        
        # 输出结果
        print("=" * 70)
        print("✅ 测试完成!")
        print("=" * 70)
        print(f"⏱️  总耗时: {self.format_time(elapsed_time)}")
        print(f"📊 可用代理: {self.total_working}/{self.total_tested} ({(self.total_working/self.total_tested*100) if self.total_tested > 0 else 0:.2f}%)")
        
        if SPEED_TEST_ENABLED:
            print(f"🚀 快速代理: {self.total_fast}/{self.total_working} ({(self.total_fast/self.total_working*100) if self.total_working > 0 else 0:.2f}%)")
        
        print("=" * 70)
        
        # 保存结果
        if working_proxies:
            self.save_results(working_proxies, fast_proxies, OUTPUT_FILE)
            print(f"💾 所有可用代理: {OUTPUT_FILE}")
            if SPEED_TEST_ENABLED and fast_proxies:
                print(f"⚡ 快速代理列表: {OUTPUT_FILE_FAST}")
        else:
            with open(OUTPUT_FILE, 'w') as f:
                pass
            print(f"⚠️  没有可用代理，已创建空文件: {OUTPUT_FILE}")
        
        print("\n🎉 任务完成!")


if __name__ == "__main__":
    tester = ProxyTester()
    tester.run()
