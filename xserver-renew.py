#!/usr/bin/env python3
"""
XServer VPS 免费VPS自动续期脚本 - 青龙版

cron: 0 10 * * *
new Env('xserver-renew')

环境变量:
    ACCOUNTS_XSERVER: 账号密码，格式 email:password，多个用 & 分隔
    CAPTCHA_API_URL: OCR API 地址 (日文验证码识别)
    YESCAPTCHA_KEY: YesCaptcha API Key (解决 Turnstile，必需)
    TELEGRAM_BOT_TOKEN: Telegram机器人Token (可选)
    TELEGRAM_CHAT_ID: Telegram聊天ID (可选)

重要: XServer 的 Turnstile 在 xvfb 虚拟显示器环境无法自动通过，
      必须配置 YESCAPTCHA_KEY 使用打码平台解决。
"""

import os
import asyncio
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

# ==================== 配置 ====================
ACCOUNTS_STR = os.environ.get('ACCOUNTS_XSERVER', '')
TG_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_USER_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
CAPTCHA_API_URL = os.environ.get('CAPTCHA_API_URL', 'https://captcha-120546510085.asia-northeast1.run.app')
YESCAPTCHA_KEY = os.environ.get('YESCAPTCHA_KEY', '')
TURNSTILE_SITEKEY = '0x4AAAAAABlb1fIlWBrSDU3B'

LOGIN_URL = "https://secure.xserver.ne.jp/xapanel/login/xserver/"
VPS_INDEX_URL = "https://secure.xserver.ne.jp/xapanel/xvps/index"
SESSION_DIR = Path(__file__).parent / "sessions"

# ==================== 工具函数 ====================
class Logger:
    @staticmethod
    def log(tag, msg, icon="ℹ"):
        icons = {"OK": "✓", "WARN": "⚠", "WAIT": "⏳", "INFO": "ℹ"}
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {icons.get(icon, icon)} {msg}")

def parse_accounts(s):
    accounts = []
    for item in (s or '').split('&'):
        item = item.strip()
        if ':' in item:
            email, password = item.split(':', 1)
            accounts.append({'email': email.strip(), 'password': password.strip()})
    return accounts

async def cdp_click(cdp, x, y):
    """CDP 模拟点击"""
    await cdp.send('Input.dispatchMouseEvent', {
        'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
    })
    await asyncio.sleep(0.05)
    await cdp.send('Input.dispatchMouseEvent', {
        'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
    })

def ocr_captcha(img_src):
    """调用 OCR API 识别日文验证码"""
    try:
        r = requests.post(CAPTCHA_API_URL, data=img_src, headers={'Content-Type': 'text/plain'}, timeout=30)
        result = r.text.strip()
        Logger.log("OCR", f"识别结果: {result}", "OK")
        return result
    except Exception as e:
        Logger.log("OCR", f"失败: {e}", "WARN")
        return None

def solve_turnstile_yescaptcha(url):
    """使用 YesCaptcha 解决 Turnstile"""
    if not YESCAPTCHA_KEY:
        Logger.log("Turnstile", "未配置 YESCAPTCHA_KEY，无法解决", "WARN")
        return None
    
    Logger.log("Turnstile", "使用 YesCaptcha 解决...", "WAIT")
    try:
        # 创建任务
        r = requests.post("https://api.yescaptcha.com/createTask", json={
            "clientKey": YESCAPTCHA_KEY,
            "task": {
                "type": "TurnstileTaskProxyless",
                "websiteURL": url,
                "websiteKey": TURNSTILE_SITEKEY
            }
        }, timeout=30)
        data = r.json()
        if data.get('errorId'):
            Logger.log("Turnstile", f"创建任务失败: {data.get('errorDescription')}", "WARN")
            return None
        task_id = data.get('taskId')
        Logger.log("Turnstile", f"任务 ID: {task_id}", "INFO")
        
        # 轮询结果
        for i in range(60):
            time.sleep(3)
            r = requests.post("https://api.yescaptcha.com/getTaskResult", json={
                "clientKey": YESCAPTCHA_KEY,
                "taskId": task_id
            }, timeout=30)
            data = r.json()
            if data.get('status') == 'ready':
                token = data['solution']['token']
                Logger.log("Turnstile", f"成功! token 长度: {len(token)}", "OK")
                return token
            if data.get('errorId'):
                Logger.log("Turnstile", f"错误: {data.get('errorDescription')}", "WARN")
                return None
            if i % 5 == 0:
                Logger.log("Turnstile", f"等待中... ({i*3}s)", "WAIT")
        
        Logger.log("Turnstile", "YesCaptcha 超时", "WARN")
        return None
    except Exception as e:
        Logger.log("Turnstile", f"YesCaptcha 错误: {e}", "WARN")
        return None

async def handle_turnstile(page, cdp, max_wait=15):
    """处理 Turnstile 验证"""
    Logger.log("Turnstile", "等待验证...", "WAIT")
    
    turnstile = await page.evaluate('''() => {
        const el = document.querySelector('.cf-turnstile');
        if (el) { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y}; }
        return null;
    }''')
    
    if not turnstile:
        Logger.log("Turnstile", "未找到元素", "INFO")
        return True
    
    # 先尝试 CDP 点击
    x = int(turnstile['x'] + 30)
    y = int(turnstile['y'] + 32)
    Logger.log("Turnstile", f"点击 ({x}, {y})", "INFO")
    await cdp_click(cdp, x, y)
    
    # 等待 token
    for i in range(max_wait):
        await asyncio.sleep(1)
        response = await page.evaluate('() => document.querySelector("input[name=cf-turnstile-response]")?.value || ""')
        if len(response) > 10:
            Logger.log("Turnstile", "验证完成", "OK")
            return True
    
    Logger.log("Turnstile", "CDP 点击超时，尝试 YesCaptcha", "INFO")
    
    # CDP 点击失败，使用 YesCaptcha
    token = solve_turnstile_yescaptcha(page.url)
    if token:
        await page.evaluate(f'''() => {{
            const input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input) input.value = "{token}";
        }}''')
        Logger.log("Turnstile", "已注入 YesCaptcha token", "OK")
        return True
    
    Logger.log("Turnstile", "验证失败", "WARN")
    return False

def send_telegram(msg):
    """发送 Telegram 通知"""
    if TG_BOT_TOKEN and TG_USER_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                data={"chat_id": TG_USER_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
        except:
            pass

# ==================== 主逻辑 ====================
async def renew_account(playwright, email, password):
    """续期单个账号"""
    Logger.log("账号", f"处理: {email}", "WAIT")
    
    browser = None
    result = {"email": email, "success": False, "msg": ""}
    
    try:
        browser = await playwright.chromium.launch(
            headless=False,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', 
                  '--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        
        # 加载会话
        SESSION_DIR.mkdir(exist_ok=True)
        session_file = SESSION_DIR / f"{email}.json"
        if session_file.exists():
            try:
                with open(session_file) as f:
                    await context.add_cookies(json.load(f))
                Logger.log("会话", "已加载", "OK")
            except:
                pass
        
        # 登录
        await page.goto(LOGIN_URL, timeout=60000)
        await asyncio.sleep(3)
        
        if "login" in page.url:
            Logger.log("登录", "填写表单...", "INFO")
            await page.fill('#memberid', email)
            await page.fill('#user_password', password)
            await asyncio.sleep(1)
            await page.click('input[name="action_user_login"]')
            await asyncio.sleep(5)
            
            if "login" in page.url:
                result["msg"] = "登录失败"
                return result
            
            # 保存会话
            cookies = await context.cookies()
            with open(session_file, 'w') as f:
                json.dump(cookies, f)
            Logger.log("登录", "成功", "OK")
        
        # 访问 VPS 列表
        await page.goto(VPS_INDEX_URL, timeout=60000)
        await asyncio.sleep(3)
        
        # 获取 VPS 详情页链接
        detail_href = await page.evaluate("document.querySelector('a[href*=\"server/detail\"]')?.getAttribute('href')")
        if not detail_href:
            result["msg"] = "未找到 VPS"
            return result
        
        # 访问详情页找续期链接
        await page.goto(f"https://secure.xserver.ne.jp{detail_href}", timeout=60000)
        await asyncio.sleep(2)
        
        extend_href = await page.evaluate("document.querySelector('a[href*=\"extend\"]')?.getAttribute('href')")
        if not extend_href:
            result["msg"] = "未找到续期链接（可能还未到续期时间）"
            return result
        
        Logger.log("续期", "找到续期链接", "OK")
        
        # 访问续期页面
        await page.goto(f"https://secure.xserver.ne.jp{extend_href}", timeout=60000)
        await asyncio.sleep(3)
        
        # 点击"继续使用免费VPS"
        btn = await page.query_selector('button:has-text("無料VPS"), a:has-text("無料VPS")')
        if btn:
            await btn.click()
            Logger.log("续期", "点击继续", "OK")
            await asyncio.sleep(3)
        
        # OCR 验证码
        captcha_src = await page.evaluate("document.querySelector('img[src*=\"captcha\"]')?.src")
        if captcha_src:
            captcha_result = ocr_captcha(captcha_src)
            if captcha_result:
                await page.fill('input[placeholder*="入力"]', captcha_result)
                Logger.log("验证码", f"已填入: {captcha_result}", "OK")
        
        # 处理 Turnstile
        turnstile_ok = await handle_turnstile(page, cdp)
        if not turnstile_ok:
            result["msg"] = "Turnstile 验证失败（需配置 YESCAPTCHA_KEY）"
            return result
        
        # 等待按钮可用
        await asyncio.sleep(2)
        
        # 提交
        submit_btn = await page.query_selector('button:has-text("継続"):not([disabled]), input[type="submit"]:not([disabled])')
        if submit_btn:
            await submit_btn.click()
            Logger.log("续期", "已提交", "OK")
        else:
            # 按钮可能还是 disabled，强制点击
            await page.click('button:has-text("継続"), input[type="submit"]', force=True)
            Logger.log("续期", "强制提交", "OK")
        
        await asyncio.sleep(5)
        
        # 检查结果
        page_text = await page.evaluate('() => document.body.innerText')
        if "完了" in page_text or "更新" in page_text or "継続" in page_text:
            result["success"] = True
            result["msg"] = "续期成功"
        else:
            result["msg"] = "续期结果未知"
        
        Logger.log("续期", result["msg"], "OK" if result["success"] else "WARN")
        
    except Exception as e:
        result["msg"] = f"错误: {str(e)[:100]}"
        Logger.log("错误", result["msg"], "WARN")
    finally:
        if browser:
            await browser.close()
    
    return result

async def main():
    print("=" * 50)
    print("XServer VPS 续期脚本")
    print("=" * 50)
    
    accounts = parse_accounts(ACCOUNTS_STR)
    if not accounts:
        print("错误: 未配置 ACCOUNTS_XSERVER 环境变量")
        print("格式: email:password 或 email1:pass1&email2:pass2")
        return
    
    Logger.log("配置", f"共 {len(accounts)} 个账号", "INFO")
    if YESCAPTCHA_KEY:
        Logger.log("配置", "YesCaptcha 已配置", "OK")
    else:
        Logger.log("配置", "警告: 未配置 YESCAPTCHA_KEY，Turnstile 可能失败", "WARN")
    
    results = []
    async with async_playwright() as playwright:
        for acc in accounts:
            result = await renew_account(playwright, acc['email'], acc['password'])
            results.append(result)
            await asyncio.sleep(3)
    
    # 汇总
    success = sum(1 for r in results if r['success'])
    fail = len(results) - success
    
    print("=" * 50)
    Logger.log("汇总", f"成功: {success}, 失败: {fail}", "INFO")
    
    # 发送通知
    msg_lines = ["🖥 XServer VPS 续期", ""]
    for r in results:
        icon = "✅" if r['success'] else "❌"
        msg_lines.append(f"{icon} {r['email']}: {r['msg']}")
    
    msg = "\n".join(msg_lines)
    print(msg)
    send_telegram(msg)

if __name__ == "__main__":
    asyncio.run(main())
