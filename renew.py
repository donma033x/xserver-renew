#!/usr/bin/env python3
"""
XServer VPS 免费VPS自动续期脚本

功能:
1. 自动登录 XServer 账户
2. 检查免费VPS到期时间
3. 在到期前1天自动续期
4. 支持 Cloudflare Turnstile 验证
5. Telegram 通知

使用方法:
    xvfb-run python3 renew.py
"""

import asyncio
import json
import re
import requests
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

# ==================== 加载配置 ====================
def load_env():
    """从 .env 文件加载配置"""
    env_file = Path(__file__).parent / '.env'
    env_vars = {}
    
    if not env_file.exists():
        print("错误: 未找到 .env 文件")
        print("请复制 .env.example 为 .env 并填写配置")
        exit(1)
    
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()
    
    return env_vars

ENV = load_env()

# 账号配置 (格式: email:password)
ACCOUNTS_STR = ENV.get('ACCOUNTS', '')

# Telegram 配置
TELEGRAM_BOT_TOKEN = ENV.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = ENV.get('TELEGRAM_CHAT_ID', '')

# XServer 配置
LOGIN_URL = "https://secure.xserver.ne.jp/xapanel/login/xserver/?request_page=xvps%2Findex"
VPS_INDEX_URL = "https://secure.xserver.ne.jp/xapanel/xvps/index"
SESSION_DIR = Path(__file__).parent / "sessions"


def parse_accounts(accounts_str: str) -> list:
    """解析账号配置"""
    accounts = []
    if not accounts_str:
        return accounts
    
    for item in accounts_str.split(','):
        item = item.strip()
        if ':' in item:
            email, password = item.split(':', 1)
            accounts.append({'email': email.strip(), 'password': password.strip()})
    
    return accounts


def get_session_file(email: str) -> Path:
    """获取账号对应的会话文件路径"""
    SESSION_DIR.mkdir(exist_ok=True)
    safe_name = email.replace('@', '_at_').replace('.', '_')
    return SESSION_DIR / f"{safe_name}.json"


class TelegramNotifier:
    """Telegram 通知发送器"""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
    
    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except:
            return False


class Logger:
    """带时间戳的日志输出"""
    @staticmethod
    def log(step: str, msg: str, status: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        symbols = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗", "WAIT": "⏳"}
        symbol = symbols.get(status, "•")
        print(f"[{timestamp}] [{step}] {symbol} {msg}")


class XServerVPSRenewer:
    """XServer VPS 续期主类"""
    
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session_file = get_session_file(email)
        self.browser = None
        self.context = None
        self.page = None
        self.cdp = None
    
    async def handle_turnstile(self, timeout: int = 30) -> bool:
        """处理 Cloudflare Turnstile 验证"""
        Logger.log("验证", "检查 Turnstile...", "WAIT")
        
        for i in range(timeout):
            # 检查是否有 Turnstile
            turnstile = await self.page.evaluate('''
                () => {
                    const el = document.querySelector('.cf-turnstile, [data-turnstile-widget]');
                    if (el) {
                        const r = el.getBoundingClientRect();
                        return {x: r.x, y: r.y, width: r.width, height: r.height};
                    }
                    return null;
                }
            ''')
            
            if not turnstile:
                # 没有 Turnstile，检查是否已通过
                response = await self.page.evaluate(
                    '() => document.querySelector("input[name=cf-turnstile-response]")?.value || ""'
                )
                if len(response) > 10:
                    Logger.log("验证", "Turnstile 已验证", "OK")
                    return True
                # 可能根本没有 Turnstile
                await asyncio.sleep(1)
                continue
            
            # 点击 Turnstile
            x = int(turnstile['x'] + 30)
            y = int(turnstile['y'] + 32)
            
            if i == 0:
                Logger.log("验证", f"点击 Turnstile ({x}, {y})", "INFO")
            
            await self.cdp.send('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})
            await asyncio.sleep(0.1)
            await self.cdp.send('Input.dispatchMouseEvent', {
                'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
            })
            await asyncio.sleep(0.05)
            await self.cdp.send('Input.dispatchMouseEvent', {
                'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
            })
            
            await asyncio.sleep(1)
            
            # 检查是否已完成
            response = await self.page.evaluate(
                '() => document.querySelector("input[name=cf-turnstile-response]")?.value || ""'
            )
            if len(response) > 10:
                Logger.log("验证", "Turnstile 验证完成", "OK")
                return True
        
        Logger.log("验证", "Turnstile 验证超时", "WARN")
        return False
    
    async def login(self) -> bool:
        """登录 XServer"""
        Logger.log("登录", "访问登录页面...", "WAIT")
        await self.page.goto(LOGIN_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        
        # 检查是否已登录
        if 'xvps/index' in self.page.url and 'login' not in self.page.url:
            Logger.log("登录", "已登录", "OK")
            return True
        
        # 填写登录表单
        Logger.log("登录", "填写登录表单...")
        
        try:
            await self.page.fill('#memberid', self.email)
            Logger.log("登录", f"用户名: {self.email}", "OK")
            await self.page.fill('#user_password', self.password)
            Logger.log("登录", "密码: ********", "OK")
        except Exception as e:
            Logger.log("登录", f"填写表单失败: {e}", "ERROR")
            return False
        
        # 点击登录
        Logger.log("登录", "点击登录按钮...")
        await self.page.click('input[name="action_user_login"]')
        
        # 等待结果
        Logger.log("登录", "等待登录结果...", "WAIT")
        await asyncio.sleep(5)
        
        # 检查结果
        url = self.page.url
        if 'login' not in url.lower() or 'customer' in url or 'xvps' in url:
            Logger.log("登录", "登录成功!", "OK")
            return True
        
        Logger.log("登录", f"登录失败，当前 URL: {url}", "ERROR")
        return False
    
    async def get_vps_list(self) -> list:
        """获取 VPS 列表"""
        Logger.log("VPS", "获取 VPS 列表...", "WAIT")
        
        await self.page.goto(VPS_INDEX_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        
        # 提取 VPS 信息
        vps_list = await self.page.evaluate('''
            () => {
                const results = [];
                const links = document.querySelectorAll('a');
                for (const link of links) {
                    const text = link.textContent.trim();
                    const href = link.href;
                    // 匹配 VPS 名称格式 vps-YYYY-MM-DD-HH-MM-SS
                    if (/vps-\d{4}-\d{2}-\d{2}/.test(text)) {
                        const match = href.match(/id_vps=(\d+)|id=(\d+)/);
                        if (match) {
                            results.push({
                                id: match[1] || match[2],
                                name: text
                            });
                        }
                    }
                }
                // 去重
                const seen = new Set();
                return results.filter(v => {
                    if (seen.has(v.id)) return false;
                    seen.add(v.id);
                    return true;
                });
            }
        ''')
        
        Logger.log("VPS", f"找到 {len(vps_list)} 个 VPS", "OK")
        for vps in vps_list:
            Logger.log("VPS", f"  - {vps['name']} (ID: {vps['id']})")
        
        return vps_list
    
    async def get_vps_expiry(self, vps_id: str) -> str:
        """获取 VPS 到期时间"""
        detail_url = f"https://secure.xserver.ne.jp/xapanel/xvps/server/detail?id={vps_id}"
        await self.page.goto(detail_url, wait_until='domcontentloaded')
        await asyncio.sleep(2)
        
        expiry = await self.page.evaluate('''
            () => {
                const text = document.body.innerText;
                const match = text.match(/利用期限[\s\S]*?(\d{4}年\d{1,2}月\d{1,2}日)/);
                return match ? match[1] : null;
            }
        ''')
        
        return expiry
    
    async def renew_vps(self, vps_id: str, vps_name: str) -> dict:
        """续期 VPS"""
        result = {
            'id': vps_id,
            'name': vps_name,
            'success': False,
            'message': '',
            'expiry': None
        }
        
        Logger.log("续期", f"处理 VPS: {vps_name}", "WAIT")
        
        # 获取到期时间
        expiry = await self.get_vps_expiry(vps_id)
        result['expiry'] = expiry
        Logger.log("续期", f"到期时间: {expiry}")
        
        # 访问续期页面
        extend_url = f"https://secure.xserver.ne.jp/xapanel/xvps/server/freevps/extend/index?id_vps={vps_id}"
        Logger.log("续期", "访问续期页面...", "WAIT")
        await self.page.goto(extend_url, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        
        # 检查页面内容
        page_text = await self.page.evaluate('() => document.body.innerText')
        
        # 检查是否在续期时间窗口外
        if '1日前から' in page_text and '以降にお試しください' in page_text:
            match = re.search(r'(\d+年\d+月\d+日)以降', page_text)
            renew_date = match.group(1) if match else "未知"
            result['message'] = f"未到续期时间，可续期日期: {renew_date}"
            Logger.log("续期", result['message'], "INFO")
            return result
        
        # 处理 Turnstile 验证
        await self.handle_turnstile(15)
        
        # 查找并点击续期按钮
        Logger.log("续期", "查找续期按钮...", "WAIT")
        
        btn_clicked = await self.page.evaluate('''
            () => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.includes('継続') || btn.textContent.includes('引き続き')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        ''')
        
        if not btn_clicked:
            result['message'] = "未找到续期按钮"
            Logger.log("续期", result['message'], "WARN")
            return result
        
        Logger.log("续期", "点击了续期按钮", "OK")
        await asyncio.sleep(5)
        
        # 检查结果
        result_text = await self.page.evaluate('() => document.body.innerText')
        
        if '完了' in result_text or '更新' in result_text or '継続' in result_text:
            # 检查是否还是提示不在时间窗口
            if '1日前から' in result_text and '以降にお試しください' in result_text:
                match = re.search(r'(\d+年\d+月\d+日)以降', result_text)
                renew_date = match.group(1) if match else "未知"
                result['message'] = f"未到续期时间，可续期日期: {renew_date}"
                Logger.log("续期", result['message'], "INFO")
            else:
                result['success'] = True
                result['message'] = "续期成功"
                Logger.log("续期", "续期成功!", "OK")
        else:
            result['message'] = "续期状态未知"
            Logger.log("续期", result['message'], "WARN")
        
        return result
    
    async def save_session(self):
        """保存会话"""
        cookies = await self.context.cookies()
        with open(self.session_file, 'w') as f:
            json.dump(cookies, f, indent=2)
        Logger.log("会话", f"会话已保存到 {self.session_file.name}", "OK")
    
    async def load_session(self) -> bool:
        """加载已保存的会话"""
        if self.session_file.exists():
            try:
                with open(self.session_file) as f:
                    cookies = json.load(f)
                await self.context.add_cookies(cookies)
                Logger.log("会话", "已加载保存的会话", "OK")
                return True
            except Exception as e:
                Logger.log("会话", f"加载会话失败: {e}", "WARN")
        return False
    
    async def run(self) -> list:
        """运行续期流程"""
        print()
        print("-" * 60)
        Logger.log("账号", f"开始处理: {self.email}", "WAIT")
        print("-" * 60)
        
        results = []
        
        async with async_playwright() as p:
            # 启动浏览器
            Logger.log("启动", "启动浏览器...")
            self.browser = await p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 900},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            self.page = await self.context.new_page()
            self.cdp = await self.context.new_cdp_session(self.page)
            Logger.log("启动", "浏览器已启动", "OK")
            
            try:
                # 加载会话
                await self.load_session()
                
                # 登录
                if not await self.login():
                    Logger.log("结果", "登录失败", "ERROR")
                    await self.browser.close()
                    return [{'success': False, 'message': '登录失败'}]
                
                # 获取 VPS 列表
                vps_list = await self.get_vps_list()
                
                if not vps_list:
                    Logger.log("结果", "未找到 VPS", "WARN")
                    await self.browser.close()
                    return [{'success': False, 'message': '未找到 VPS'}]
                
                # 续期每个 VPS
                for vps in vps_list:
                    result = await self.renew_vps(vps['id'], vps['name'])
                    results.append(result)
                
                # 保存会话
                await self.save_session()
                
            except Exception as e:
                Logger.log("错误", f"发生异常: {e}", "ERROR")
                results.append({'success': False, 'message': str(e)})
            
            await self.browser.close()
        
        return results


async def main():
    accounts = parse_accounts(ACCOUNTS_STR)
    if not accounts:
        print("错误: 未配置账号信息")
        print("请在 .env 文件中配置 ACCOUNTS=email:password")
        exit(1)
    
    # 初始化 Telegram 通知
    telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    if telegram.enabled:
        print("✓ Telegram 通知已启用")
    
    print()
    print("=" * 60)
    print("  XServer VPS 免费VPS自动续期脚本")
    print("=" * 60)
    print(f"  账号数量: {len(accounts)}")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    all_results = []
    for i, account in enumerate(accounts, 1):
        print(f"\n[进度] 处理账号 {i}/{len(accounts)}")
        renewer = XServerVPSRenewer(account['email'], account['password'])
        results = await renewer.run()
        all_results.append({
            'email': account['email'],
            'results': results
        })
    
    # 汇总结果
    print()
    print("=" * 60)
    print("  📊 任务汇总")
    print("=" * 60)
    
    success_count = 0
    total_vps = 0
    
    for account_result in all_results:
        email = account_result['email']
        results = account_result['results']
        
        print(f"\n  账号: {email}")
        for r in results:
            total_vps += 1
            status = "✓" if r.get('success') else "✗"
            name = r.get('name', 'Unknown')
            msg = r.get('message', '')
            expiry = r.get('expiry', '')
            
            if r.get('success'):
                success_count += 1
            
            print(f"    {status} {name}")
            if expiry:
                print(f"      到期: {expiry}")
            if msg:
                print(f"      {msg}")
    
    print()
    print("-" * 60)
    print(f"  总计: {success_count}/{total_vps} 成功续期")
    print(f"  完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()
    
    # 发送 Telegram 通知
    if telegram.enabled:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        msg_lines = ["🖥 <b>XServer VPS 续期报告</b>", ""]
        
        for account_result in all_results:
            email = account_result['email']
            results = account_result['results']
            
            msg_lines.append(f"📧 {email}")
            for r in results:
                status = "✅" if r.get('success') else "ℹ️"
                name = r.get('name', 'Unknown')
                msg = r.get('message', '')
                expiry = r.get('expiry', '')
                
                msg_lines.append(f"  {status} {name}")
                if expiry:
                    msg_lines.append(f"     到期: {expiry}")
                if msg:
                    msg_lines.append(f"     {msg}")
        
        msg_lines.append("")
        msg_lines.append(f"📊 结果: {success_count}/{total_vps} 成功")
        msg_lines.append(f"🕒 时间: {now}")
        
        message = "\n".join(msg_lines)
        telegram.send(message)
        print("✓ 已发送 Telegram 通知")
    
    return success_count > 0 or total_vps > 0


if __name__ == '__main__':
    result = asyncio.run(main())
    exit(0 if result else 1)
