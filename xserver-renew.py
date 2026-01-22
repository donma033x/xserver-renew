#!/usr/bin/env python3
"""
XServer VPS 免费VPS自动续期脚本 - 青龙版

cron: 0 10 * * *
new Env('xserver-renew')

环境变量:
    XSERVER_ACCOUNT: 账号密码，格式 email:password，多个用 & 分隔
    CAPTCHA_API_URL: OCR API 地址 (日文验证码识别)
    YESCAPTCHA_KEY: YesCaptcha API Key (解决 Turnstile，必需)
    TELEGRAM_BOT_TOKEN: Telegram机器人Token (可选)
    TELEGRAM_CHAT_ID: Telegram聊天ID (可选)

重要: XServer 的 Turnstile 在 xvfb 虚拟显示器环境无法自动通过，
      必须配置 YESCAPTCHA_KEY 使用打码平台解决。
"""

import os
import re
import asyncio
import json
import time
import requests
import base64
from pathlib import Path
from datetime import datetime, date
from playwright.async_api import async_playwright

# ==================== 配置 ====================
ACCOUNTS_STR = os.environ.get('XSERVER_ACCOUNT', '')
TG_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_USER_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
CAPTCHA_API_URL = os.environ.get('CAPTCHA_API_URL', 'https://captcha-120546510085.asia-northeast1.run.app')
YESCAPTCHA_KEY = os.environ.get('YESCAPTCHA_KEY', '')
TURNSTILE_SITEKEY = '0x4AAAAAABlb1fIlWBrSDU3B'

LOGIN_URL = "https://secure.xserver.ne.jp/xapanel/login/xserver/"
VPS_INDEX_URL = "https://secure.xserver.ne.jp/xapanel/xvps/index"
SESSION_DIR = Path(__file__).parent / "sessions"
DEBUG_DIR = Path(__file__).parent / "debug"

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

def parse_jp_date(s: str):
    """解析日文日期格式为 date 对象"""
    if not s:
        return None
    s = s.strip()
    m = re.search(r'(\d{4})\s*[年/\-]\s*(\d{1,2})\s*[月/\-]\s*(\d{1,2})\s*日?', s)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    try:
        return date(y, mo, d)
    except:
        return None

async def extract_expire_text(page):
    """从页面提取到期时间文本"""
    js = r"""
    () => {
      const keywords = ["有効期限", "期限"];
      const textOf = (el) => (el && el.innerText ? el.innerText.trim() : "");
      // 1) 尝试表格行
      const rows = Array.from(document.querySelectorAll("tr"));
      for (const tr of rows) {
        const t = textOf(tr);
        if (keywords.some(k => t.includes(k))) {
          const td = tr.querySelector("td");
          if (td) return textOf(td);
        }
      }
      // 2) 尝试 dt/dd
      const dts = Array.from(document.querySelectorAll("dt"));
      for (const dt of dts) {
        if (keywords.some(k => textOf(dt).includes(k))) {
          const dd = dt.nextElementSibling;
          if (dd) return textOf(dd);
        }
      }
      // 3) 退回全文
      return document.body.innerText || "";
    }
    """
    return await page.evaluate(js)

async def get_expire_date(page):
    """获取到期日期"""
    text = await extract_expire_text(page)
    patterns = [
        r'有効期限[：: ]*\s*([0-9]{4}[年/\-][0-9]{1,2}[月/\-][0-9]{1,2}日?)',
        r'期限[：: ]*\s*([0-9]{4}[年/\-][0-9]{1,2}[月/\-][0-9]{1,2}日?)',
        r'([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)\s*まで',
        r'([0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return parse_jp_date(m.group(1))
    return parse_jp_date(text)

async def save_debug_info(page, email, stage):
    """保存调试信息"""
    DEBUG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    prefix = f"{email}_{stage}_{timestamp}"
    try:
        await page.screenshot(path=DEBUG_DIR / f"{prefix}.png")
        html = await page.content()
        with open(DEBUG_DIR / f"{prefix}.html", 'w', encoding='utf-8') as f:
            f.write(html)
        Logger.log("调试", f"已保存截图和HTML: {prefix}", "INFO")
    except Exception as e:
        Logger.log("调试", f"保存失败: {e}", "WARN")

async def cdp_click(cdp, x, y):
    """CDP 模拟点击"""
    await cdp.send('Input.dispatchMouseEvent', {
        'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
    })
    await asyncio.sleep(0.05)
    await cdp.send('Input.dispatchMouseEvent', {
        'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
    })

async def get_captcha_image_base64(page):
    """获取验证码图片的 base64 内容"""
    js = r"""
    async () => {
        // XServer 的验证码图片是 data:image 格式，带 border 样式
        const selectors = [
            'img[src^="data:image"]',
            'img[style*="border"]',
            'img[src*="captcha"]',
        ];
        
        for (const sel of selectors) {
            const img = document.querySelector(sel);
            if (img && img.src && img.src.startsWith('data:image')) {
                return img.src;
            }
        }
        
        // 尝试找任何 data:image 的图片
        const allImgs = document.querySelectorAll('img');
        for (const img of allImgs) {
            if (img.src && img.src.startsWith('data:image')) {
                return img.src;
            }
        }
        
        return null;
    }
    """
    return await page.evaluate(js)

def ocr_captcha(img_data_url, max_retries=3):
    """调用 OCR API 识别日文验证码"""
    if not img_data_url:
        Logger.log("OCR", "未获取到验证码图片", "WARN")
        return None
    
    try:
        if img_data_url.startswith('data:'):
            base64_data = img_data_url.split(',', 1)[1] if ',' in img_data_url else img_data_url
        else:
            base64_data = img_data_url
        
        for attempt in range(max_retries):
            try:
                Logger.log("OCR", f"尝试识别 (第{attempt+1}次)...", "WAIT")
                r = requests.post(CAPTCHA_API_URL, data=base64_data, headers={'Content-Type': 'text/plain'}, timeout=60)
                result = r.text.strip()
                if result and len(result) >= 4:
                    Logger.log("OCR", f"识别结果: {result}", "OK")
                    return result
                else:
                    Logger.log("OCR", f"识别结果无效: {result}", "WARN")
            except requests.Timeout:
                Logger.log("OCR", f"第{attempt+1}次超时", "WARN")
            except Exception as e:
                Logger.log("OCR", f"第{attempt+1}次错误: {e}", "WARN")
        
        Logger.log("OCR", f"识别失败，已尝试{max_retries}次", "WARN")
        return None
    except Exception as e:
        Logger.log("OCR", f"失败: {e}", "WARN")
        return None

async def fill_captcha(page, captcha_result):
    """填入验证码并校验"""
    if not captcha_result:
        return False
    
    selectors = [
        'input[name*="captcha"]',
        'input[id*="captcha"]',
        'input[placeholder*="数字"]',
        'input[placeholder*="入力"]',
        'input[placeholder*="認証"]',
        'input[type="text"][class*="captcha"]',
        'input[type="text"]',
    ]
    
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                await locator.fill(captcha_result)
                await asyncio.sleep(0.5)
                filled_value = await locator.input_value()
                if filled_value == captcha_result:
                    Logger.log("验证码", f"已填入: {captcha_result} (选择器: {selector})", "OK")
                    return True
                else:
                    Logger.log("验证码", f"填入校验失败: 期望 {captcha_result}, 实际 {filled_value}", "WARN")
        except:
            continue
    
    Logger.log("验证码", "未找到验证码输入框", "WARN")
    return False

def solve_turnstile_yescaptcha(url):
    """使用 YesCaptcha 解决 Turnstile"""
    if not YESCAPTCHA_KEY:
        Logger.log("Turnstile", "未配置 YESCAPTCHA_KEY，无法解决", "WARN")
        return None
    
    Logger.log("Turnstile", "使用 YesCaptcha 解决...", "WAIT")
    try:
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

async def inject_turnstile_token(page, token):
    """注入 Turnstile token 并触发事件和 callback"""
    js = f'''
    () => {{
        let result = {{ found: false, callbackCalled: false }};
        
        // 找到所有 turnstile response 输入框并设置值
        const selectors = [
            'input[name="cf-turnstile-response"]',
            'textarea[name="cf-turnstile-response"]',
            'input[id*="turnstile"][id*="response"]',
        ];
        
        for (const sel of selectors) {{
            const elements = document.querySelectorAll(sel);
            elements.forEach(el => {{
                el.value = "{token}";
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                result.found = true;
            }});
        }}
        
        // 调用 Turnstile callback 函数 - 这是关键！
        // XServer 使用 callbackTurnstile 函数
        if (typeof window.callbackTurnstile === 'function') {{
            try {{
                window.callbackTurnstile("{token}");
                result.callbackCalled = true;
                console.log('callbackTurnstile called successfully');
            }} catch (e) {{
                console.log('callbackTurnstile error:', e);
            }}
        }}
        
        // 也尝试从 data-callback 属性获取回调名
        const turnstileEl = document.querySelector('.cf-turnstile');
        if (turnstileEl) {{
            const callbackName = turnstileEl.getAttribute('data-callback');
            if (callbackName && callbackName !== 'callbackTurnstile' && typeof window[callbackName] === 'function') {{
                try {{
                    window[callbackName]("{token}");
                    result.callbackCalled = true;
                }} catch (e) {{}}
            }}
        }}
        
        // 启用提交按钮
        const submitBtns = document.querySelectorAll('input[type="submit"], button[type="submit"]');
        submitBtns.forEach(btn => {{
            btn.disabled = false;
        }});
        
        // 获取最终 response 长度
        const responseEl = document.querySelector('input[name="cf-turnstile-response"]');
        result.valueLen = responseEl ? responseEl.value.length : 0;
        
        return result;
    }}
    '''
    return await page.evaluate(js)

async def check_turnstile_status(page):
    """检查 Turnstile 状态"""
    js = '''
    () => {
        const hasTurnstile = !!document.querySelector('.cf-turnstile');
        const responseInput = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
        const responseLen = responseInput ? responseInput.value.length : 0;
        
        // 查找提交按钮
        let submitBtn = document.querySelector('input[type="submit"], button[type="submit"]');
        if (!submitBtn) {
            const buttons = Array.from(document.querySelectorAll('button'));
            submitBtn = buttons.find(b => b.innerText.includes('継続') || b.innerText.includes('確認'));
        }
        const submitDisabled = submitBtn ? submitBtn.disabled : null;
        
        return {
            hasTurnstile,
            responseLen,
            submitDisabled
        };
    }
    '''
    return await page.evaluate(js)

async def handle_turnstile(page, cdp, max_wait=30):
    """处理 Turnstile 验证"""
    Logger.log("Turnstile", "检查验证状态...", "WAIT")
    
    status = await check_turnstile_status(page)
    Logger.log("Turnstile", f"状态: 存在={status.get('hasTurnstile')}, response长度={status.get('responseLen')}, 按钮disabled={status.get('submitDisabled')}", "INFO")
    
    if status.get('responseLen', 0) > 10 and not status.get('submitDisabled'):
        Logger.log("Turnstile", "已有有效 token 且按钮可用", "OK")
        return True
    
    if not status.get('hasTurnstile'):
        Logger.log("Turnstile", "未找到 Turnstile 元素", "INFO")
        return True
    
    # 首先尝试等待 Turnstile 自动完成（有时候只需要等待）
    Logger.log("Turnstile", "等待 Turnstile 自动验证...", "WAIT")
    for i in range(10):
        await asyncio.sleep(1)
        status = await check_turnstile_status(page)
        if status.get('responseLen', 0) > 10 and not status.get('submitDisabled'):
            Logger.log("Turnstile", "自动验证完成", "OK")
            return True
    
    # 尝试点击 Turnstile iframe 内的复选框
    turnstile = await page.evaluate('''() => {
        const el = document.querySelector('.cf-turnstile');
        if (el) { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; }
        return null;
    }''')
    
    if turnstile:
        # 点击复选框区域（通常在左侧）
        x = int(turnstile['x'] + 30)
        y = int(turnstile['y'] + turnstile['height'] / 2)
        Logger.log("Turnstile", f"尝试 CDP 点击复选框 ({x}, {y})", "INFO")
        await cdp_click(cdp, x, y)
        
        for i in range(max_wait):
            await asyncio.sleep(1)
            status = await check_turnstile_status(page)
            if status.get('responseLen', 0) > 10 and not status.get('submitDisabled'):
                Logger.log("Turnstile", "CDP 点击成功，验证完成", "OK")
                return True
            if i % 5 == 0:
                Logger.log("Turnstile", f"等待验证中... ({i}s)", "WAIT")
    
    Logger.log("Turnstile", "CDP 点击未生效，使用 YesCaptcha", "INFO")
    
    token = solve_turnstile_yescaptcha(page.url)
    if token:
        result = await inject_turnstile_token(page, token)
        Logger.log("Turnstile", f"Token 注入结果: {result}", "INFO")
        
        if result.get('found'):
            # 等待更长时间让前端处理
            for i in range(10):
                await asyncio.sleep(1)
                status = await check_turnstile_status(page)
                Logger.log("Turnstile", f"注入后状态: response长度={status.get('responseLen')}, 按钮disabled={status.get('submitDisabled')}", "INFO")
                
                if status.get('responseLen', 0) > 10 and not status.get('submitDisabled'):
                    Logger.log("Turnstile", "YesCaptcha token 注入成功，按钮已启用", "OK")
                    return True
            
            # 即使按钮还是 disabled，也尝试继续（可能前端逻辑问题）
            if status.get('responseLen', 0) > 10:
                Logger.log("Turnstile", "Token 已注入，但按钮仍 disabled，强制继续", "WARN")
                return True
            else:
                Logger.log("Turnstile", "Token 注入但未生效", "WARN")
        else:
            Logger.log("Turnstile", "未找到 token 输入框", "WARN")
    
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
    result = {"email": email, "success": False, "msg": "", "old_expire": None, "new_expire": None}
    
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
                await save_debug_info(page, email, "login_failed")
                result["msg"] = "登录失败"
                return result
            
            cookies = await context.cookies()
            with open(session_file, 'w') as f:
                json.dump(cookies, f)
            Logger.log("登录", "成功", "OK")
        
        # 访问 VPS 列表
        await page.goto(VPS_INDEX_URL, timeout=60000)
        await asyncio.sleep(3)
        
        detail_href = await page.evaluate("document.querySelector('a[href*=\"server/detail\"]')?.getAttribute('href')")
        if not detail_href:
            await save_debug_info(page, email, "no_vps")
            result["msg"] = "未找到 VPS"
            return result
        
        # 访问详情页获取原到期时间
        detail_url = f"https://secure.xserver.ne.jp{detail_href}"
        await page.goto(detail_url, timeout=60000)
        await asyncio.sleep(2)
        
        old_expire = await get_expire_date(page)
        if old_expire:
            Logger.log("到期时间", f"原到期时间: {old_expire}", "INFO")
            result["old_expire"] = old_expire
        else:
            Logger.log("到期时间", "无法解析原到期时间", "WARN")
            await save_debug_info(page, email, "no_old_expire")
        
        # 查找续期链接
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
            Logger.log("续期", "点击继续使用免费VPS", "OK")
            await asyncio.sleep(3)
        
        # OCR 验证码 - 使用 base64 图片
        captcha_base64 = await get_captcha_image_base64(page)
        captcha_ok = False
        if captcha_base64:
            Logger.log("验证码", "检测到验证码图片", "INFO")
            captcha_result = ocr_captcha(captcha_base64)
            if captcha_result:
                captcha_ok = await fill_captcha(page, captcha_result)
                if not captcha_ok:
                    await save_debug_info(page, email, "captcha_fill_failed")
                    result["msg"] = "验证码填入失败"
                    return result
            else:
                await save_debug_info(page, email, "captcha_ocr_failed")
                result["msg"] = "验证码识别失败"
                return result
        else:
            Logger.log("验证码", "页面无验证码图片", "INFO")
            captcha_ok = True
        
        # 处理 Turnstile
        turnstile_ok = await handle_turnstile(page, cdp)
        if not turnstile_ok:
            await save_debug_info(page, email, "turnstile_failed")
            result["msg"] = "Turnstile 验证失败"
            return result
        
        await asyncio.sleep(2)
        
        # 保存提交前截图
        await save_debug_info(page, email, "before_submit")
        
        # 查找并点击提交按钮
        submit_selectors = [
            'input[type="submit"]:not([disabled])',
            'button[type="submit"]:not([disabled])',
        ]
        
        submit_clicked = False
        for sel in submit_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    btn_text = await btn.inner_text() if await btn.evaluate('el => el.tagName') == 'BUTTON' else await btn.get_attribute('value')
                    Logger.log("续期", f"找到提交按钮: {btn_text}", "INFO")
                    await btn.click()
                    submit_clicked = True
                    Logger.log("续期", "已点击提交", "OK")
                    break
            except Exception as e:
                Logger.log("续期", f"点击失败 ({sel}): {e}", "WARN")
        
        if not submit_clicked:
            # 尝试强制点击
            try:
                await page.click('input[type="submit"], button[type="submit"]', force=True)
                Logger.log("续期", "强制提交", "OK")
                submit_clicked = True
            except Exception as e:
                Logger.log("续期", f"强制提交失败: {e}", "WARN")
        
        # 等待页面变化
        await asyncio.sleep(3)
        
        # 检查提交后页面
        current_url = page.url
        page_text = await page.evaluate('() => document.body.innerText')
        Logger.log("续期", f"提交后URL: {current_url}", "INFO")
        
        # 保存提交后截图
        await save_debug_info(page, email, "after_submit")
        
        # 检查是否有错误或成功提示
        if "エラー" in page_text or "失敗" in page_text:
            Logger.log("续期", "页面显示错误", "WARN")
        if "完了" in page_text or "更新しました" in page_text:
            Logger.log("续期", "页面显示成功", "OK")
        
        await asyncio.sleep(2)
        
        # 回到详情页获取新到期时间
        await page.goto(detail_url, timeout=60000)
        await asyncio.sleep(3)
        
        new_expire = await get_expire_date(page)
        if new_expire:
            Logger.log("到期时间", f"新到期时间: {new_expire}", "INFO")
            result["new_expire"] = new_expire
        else:
            Logger.log("到期时间", "无法解析新到期时间", "WARN")
            await save_debug_info(page, email, "no_new_expire")
        
        # 对比到期时间判断是否成功
        if old_expire and new_expire:
            if new_expire > old_expire:
                result["success"] = True
                result["msg"] = f"续期成功: {old_expire} → {new_expire}"
                Logger.log("续期", f"✓ 续期成功: {old_expire} → {new_expire}", "OK")
            else:
                result["msg"] = f"续期未生效: {old_expire} == {new_expire}"
                Logger.log("续期", f"✗ 续期未生效: 到期时间未变化 ({old_expire})", "WARN")
                await save_debug_info(page, email, "renew_not_effective")
        elif new_expire:
            result["msg"] = f"续期状态未知 (新到期: {new_expire}, 无法对比)"
            Logger.log("续期", result["msg"], "WARN")
        else:
            result["msg"] = "无法获取到期时间，续期状态未知"
            Logger.log("续期", result["msg"], "WARN")
            await save_debug_info(page, email, "unknown_status")
        
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
        print("错误: 未配置 XSERVER_ACCOUNT 环境变量")
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
    
    success = sum(1 for r in results if r['success'])
    fail = len(results) - success
    
    print("=" * 50)
    Logger.log("汇总", f"成功: {success}, 失败: {fail}", "INFO")
    
    msg_lines = ["🖥 XServer VPS 续期", ""]
    for r in results:
        icon = "✅" if r['success'] else "❌"
        msg_lines.append(f"{icon} {r['email']}: {r['msg']}")
    
    msg = "\n".join(msg_lines)
    print(msg)
    send_telegram(msg)

if __name__ == "__main__":
    asyncio.run(main())
