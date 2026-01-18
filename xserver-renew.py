#!/usr/bin/env python3
"""
XServer VPS 续期 - 完整反自动化版本
包含：CDP点击、Cloudflare处理、反检测参数、随机延迟、人类行为模拟、stealth注入、webdriver隐藏
"""

import asyncio, json, re, random, requests, aiohttp
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

def load_env():
    env_file = Path(__file__).parent / '.env'
    env_vars = {}
    if not env_file.exists(): exit(1)
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env_vars[k.strip()] = v.strip()
    return env_vars

ENV = load_env()
ACCOUNTS_STR = ENV.get('ACCOUNTS', '')
TELEGRAM_BOT_TOKEN = ENV.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = ENV.get('TELEGRAM_CHAT_ID', '')
CAPTCHA_API_URL = ENV.get('CAPTCHA_API_URL', 'https://captcha-120546510085.asia-northeast1.run.app')

LOGIN_URL = "https://secure.xserver.ne.jp/xapanel/login/xserver/?request_page=xvps%2Findex"
VPS_INDEX_URL = "https://secure.xserver.ne.jp/xapanel/xvps/index"
SESSION_DIR = Path(__file__).parent / "sessions"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# 完整的 Stealth JS 脚本
STEALTH_JS = """
// 隐藏 webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 伪造 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
        {name: 'Native Client', filename: 'internal-nacl-plugin'}
    ]
});

// 伪造 languages
Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'ja', 'en-US', 'en']});

// 伪造 permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' 
        ? Promise.resolve({state: Notification.permission}) 
        : originalQuery(parameters)
);

// 伪造 chrome
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};

// 隐藏自动化痕迹
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

// 伪造 WebGL
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.apply(this, arguments);
};
"""

def parse_accounts(s):
    accounts = []
    for item in (s or '').split(','):
        if ':' in item.strip():
            e, p = item.strip().split(':', 1)
            accounts.append({'email': e.strip(), 'password': p.strip()})
    return accounts

def get_session_file(email):
    SESSION_DIR.mkdir(exist_ok=True)
    return SESSION_DIR / f"{email.replace('@', '_at_').replace('.', '_')}.json"

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token, self.chat_id = token, chat_id
        self.enabled = bool(token and chat_id)
    def send(self, msg):
        if not self.enabled: return
        try: requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass
    def send_photo(self, path, caption=""):
        if not self.enabled: return
        try:
            with open(path, 'rb') as f:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption}, files={"photo": f}, timeout=30)
        except: pass

class Logger:
    @staticmethod
    def log(step, msg, status="INFO"):
        symbols = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗", "WAIT": "⏳"}
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{step}] {symbols.get(status, '•')} {msg}", flush=True)


# ==================== 反自动化核心函数 ====================

async def random_delay(min_ms=100, max_ms=500):
    """随机延迟"""
    await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)


async def human_mouse_move(cdp, from_x, from_y, to_x, to_y, steps=None):
    """人类鼠标移动轨迹模拟"""
    if steps is None:
        steps = random.randint(10, 25)
    
    for i in range(steps):
        # 添加随机偏移模拟人类不精确的移动
        progress = (i + 1) / steps
        # 使用缓动函数
        eased = progress * (2 - progress)  # ease-out
        
        x = from_x + (to_x - from_x) * eased + random.randint(-2, 2)
        y = from_y + (to_y - from_y) * eased + random.randint(-2, 2)
        
        await cdp.send('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': int(x), 'y': int(y)})
        await asyncio.sleep(random.uniform(0.01, 0.03))


async def cdp_click(cdp, x, y, move_first=True):
    """CDP 模拟真实点击 - 带鼠标移动"""
    if move_first:
        # 从随机位置移动到目标
        start_x = random.randint(100, 400)
        start_y = random.randint(100, 300)
        await human_mouse_move(cdp, start_x, start_y, x, y)
    
    await random_delay(50, 150)
    await cdp.send('Input.dispatchMouseEvent', {
        'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
    })
    await random_delay(30, 80)
    await cdp.send('Input.dispatchMouseEvent', {
        'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1
    })


async def debug_click(page, cdp, x, y, name="click"):
    """点击并在页面上显示红点标记，然后截图 - 只用 CDP"""
    # 在页面上画红点
    await page.evaluate(f"""(pos) => {{
        const dot = document.createElement('div');
        dot.style.cssText = 'position:fixed;left:' + (pos.x-10) + 'px;top:' + (pos.y-10) + 'px;width:20px;height:20px;background:red;border-radius:50%;z-index:99999;pointer-events:none;';
        dot.id = 'debug-dot';
        document.body.appendChild(dot);
    }}""", {'x': x, 'y': y})
    
    await asyncio.sleep(0.1)
    
    # 截图
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    fn = f"{datetime.now().strftime('%H%M%S')}_debug_{name}_{x}_{y}.png"
    await page.screenshot(path=str(SCREENSHOT_DIR / fn))
    Logger.log("调试", f"点击位置截图: {fn}", "OK")
    
    # 移除红点
    await page.evaluate("document.getElementById('debug-dot')?.remove()")
    
    # 只用 CDP 点击
    await cdp_click(cdp, x, y, move_first=False)


async def human_type(page, selector, text):
    """人类打字模拟 - 带随机延迟和偶尔的停顿"""
    await page.click(selector)
    await random_delay(200, 400)
    
    for i, char in enumerate(text):
        await page.type(selector, char, delay=random.randint(50, 150))
        # 偶尔停顿一下，模拟人类思考
        if random.random() < 0.1:
            await random_delay(200, 500)


async def simulate_human_behavior(page):
    """模拟人类行为 - 随机滚动和鼠标移动"""
    try:
        # 随机鼠标移动
        await page.mouse.move(random.randint(100, 500), random.randint(100, 300), steps=random.randint(5, 15))
        await random_delay(300, 600)
        
        # 随机滚动
        scroll_amount = random.randint(100, 300)
        await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        await random_delay(400, 800)
        
        # 再滚回来一点
        await page.evaluate(f"window.scrollBy(0, -{scroll_amount // 2})")
        await random_delay(200, 400)
        
        # 再次随机移动鼠标
        await page.mouse.move(random.randint(200, 600), random.randint(150, 400), steps=random.randint(8, 20))
    except:
        pass


async def handle_cloudflare(page, cdp, max_attempts=30):
    """处理 Cloudflare 挑战页面"""
    Logger.log("CF", "检查 Cloudflare...", "WAIT")
    
    for i in range(max_attempts):
        title = await page.title()
        if 'Just a moment' not in title and 'Checking' not in title:
            Logger.log("CF", "Cloudflare 验证通过", "OK")
            return True
        
        # 使用 CDP 点击挑战
        await cdp_click(cdp, 210, 290)
        await asyncio.sleep(2)
        
        if i > 0 and i % 10 == 0:
            Logger.log("CF", f"等待 Cloudflare... ({i}/{max_attempts})", "WAIT")
    
    Logger.log("CF", "Cloudflare 验证超时", "ERROR")
    return False


async def handle_turnstile(page, cdp, max_wait=60):
    """处理 Turnstile 验证 - 多种方法尝试"""
    Logger.log("Turnstile", "等待验证...", "WAIT")
    
    # 先模拟人类行为
    await simulate_human_behavior(page)
    
    # 查找 Turnstile
    turnstile = await page.evaluate('''() => {
        const container = document.querySelector('.cf-turnstile');
        if (container) {
            const iframe = container.querySelector('iframe');
            if (iframe) {
                const r = iframe.getBoundingClientRect();
                return {x: r.x, y: r.y, w: r.width, h: r.height, type: 'iframe'};
            }
            const r = container.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height, type: 'container'};
        }
        return null;
    }''')
    
    if not turnstile:
        Logger.log("Turnstile", "未找到 Turnstile 元素", "INFO")
        return True
    
    # 尝试直接操作 iframe
    frames = page.frames
    for frame in frames:
        frame_url = frame.url or ''
        if 'challenges.cloudflare.com' in frame_url or 'turnstile' in frame_url:
            Logger.log("Turnstile", f"找到 Cloudflare iframe: {frame_url[:50]}", "INFO")
            try:
                # 方法1: 尝试点击 iframe 内的复选框
                checkbox = await frame.query_selector('input[type="checkbox"]')
                if checkbox:
                    await checkbox.click()
                    Logger.log("Turnstile", "方法1: 点击了 checkbox", "OK")
                    await asyncio.sleep(1)
                    continue
                    
                # 方法2: 尝试点击任何可点击元素
                clickable = await frame.query_selector('[role="checkbox"], .checkbox, label')
                if clickable:
                    await clickable.click()
                    Logger.log("Turnstile", "方法2: 点击了 clickable 元素", "OK")
                    await asyncio.sleep(1)
                    continue
                    
                # 方法3: 在 iframe 内执行 JS 点击
                await frame.evaluate("""() => {
                    const cb = document.querySelector('input[type="checkbox"]');
                    if (cb) { cb.click(); return 'clicked checkbox'; }
                    const label = document.querySelector('label');
                    if (label) { label.click(); return 'clicked label'; }
                    return 'nothing found';
                }""")
                Logger.log("Turnstile", "方法3: 执行了 JS 点击", "OK")
            except Exception as e:
                Logger.log("Turnstile", f"iframe 操作失败: {e}", "WARN")
    
    # 计算点击位置
    if turnstile.get('type') == 'iframe':
        x = int(turnstile['x'] + 25)
        y = int(turnstile['y'] + turnstile['h'] / 2)
    else:
        x = int(turnstile["x"] + 380)
        y = int(turnstile['y'] + 32)
    
    Logger.log("Turnstile", f"点击位置 ({x}, {y})", "INFO")
    
    # 多种点击方式
    await debug_click(page, cdp, x, y, 'turnstile')
    
    # 等待 token
    for i in range(max_wait):
        await asyncio.sleep(1)
        response = await page.evaluate('() => document.querySelector("input[name=cf-turnstile-response]")?.value || ""')
        if len(response) > 10:
            Logger.log("Turnstile", "验证完成!", "OK")
            return True
        
        if i > 0 and i % 10 == 0:
            Logger.log("Turnstile", f"等待中... ({i}/{max_wait}秒)", "WAIT")
            # 模拟一些人类行为
            await page.mouse.move(random.randint(300, 600), random.randint(200, 400), steps=10)
            await random_delay(300, 600)
            # 两种方式都尝试
            await cdp_click(cdp, x, y, move_first=False)
            await asyncio.sleep(0.3)
            await page.mouse.click(x, y)
    
    Logger.log("Turnstile", "验证超时", "WARN")
    return False


class CaptchaSolver:
    def __init__(self, url): self.url = url
    async def solve(self, img_data_url):
        try:
            Logger.log("OCR", "发送验证码...", "WAIT")
            async with aiohttp.ClientSession() as s:
                async with s.post(self.url, data=img_data_url,
                    headers={'Content-Type': 'text/plain'}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if not r.ok: return ""
                    code = (await r.text()).strip()
                    Logger.log("OCR", f"返回: {code}", "OK")
                    nums = re.findall(r'\d+', code)
                    return nums[0][:6] if nums else code
        except Exception as e:
            Logger.log("OCR", f"失败: {e}", "ERROR")
            return ""


class XServerVPSRenewer:
    def __init__(self, email, password, telegram):
        self.email, self.password = email, password
        self.session_file = get_session_file(email)
        self.telegram = telegram
        self.captcha_solver = CaptchaSolver(CAPTCHA_API_URL)
        self.browser = self.context = self.page = self.cdp = None
        self.screenshot_count = 0
        SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    async def screenshot(self, name, send=False):
        self.screenshot_count += 1
        fn = f"{datetime.now().strftime('%H%M%S')}_{self.screenshot_count}_{name}.png"
        path = SCREENSHOT_DIR / fn
        await self.page.screenshot(path=str(path))
        Logger.log("截图", fn, "OK")
        if send: self.telegram.send_photo(str(path), f"📸 {name}")
        return str(path)
    
    async def setup_browser(self, playwright):
        """设置浏览器 - 完整反检测配置"""
        # 浏览器启动参数 - 反检测
        launch_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-infobars',
            '--window-size=1280,900',
            '--disable-extensions',
            '--disable-plugins-discovery',
            '--disable-background-networking',
        ]
        
        self.browser = await playwright.chromium.launch(
            headless=False,
            args=launch_args
        )
        
        # 上下文配置
        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='ja-JP',
            timezone_id='Asia/Tokyo',
            color_scheme='light',
            java_script_enabled=True,
        )
        
        # 注入 Stealth JS
        await self.context.add_init_script(STEALTH_JS)
        
        self.page = await self.context.new_page()
        
        # 创建 CDP 会话
        self.cdp = await self.context.new_cdp_session(self.page)
        
        # 如果有 playwright-stealth，应用它
        if HAS_STEALTH:
            try:
                stealth = Stealth()
                await stealth.apply_stealth_async(self.page)
                Logger.log("Stealth", "playwright-stealth 已应用", "OK")
            except Exception as e:
                Logger.log("Stealth", f"应用失败: {e}", "WARN")
        
        # 加载会话
        if self.session_file.exists():
            try:
                with open(self.session_file) as f:
                    await self.context.add_cookies(json.load(f))
                Logger.log("会话", "已加载", "OK")
            except: pass
    
    async def solve_captcha(self):
        Logger.log("验证码", "查找...", "WAIT")
        img = await self.page.evaluate("""() => {
            for (const img of document.querySelectorAll('img'))
                if (img.src && img.src.startsWith('data:image')) return img.src;
            return null;
        }""")
        if not img:
            Logger.log("验证码", "未找到", "WARN")
            return False
        code = await self.captcha_solver.solve(img)
        if not code: return False
        
        # 使用人类打字方式填入
        filled = await self.page.evaluate("""(code) => {
            for (const input of document.querySelectorAll('input[type="text"]')) {
                const ph = input.placeholder || '';
                if (ph.includes('上の画像') || ph.includes('数字')) {
                    input.focus();
                    return {found: true, selector: 'input[placeholder*="上の画像"], input[placeholder*="数字"]'};
                }
            }
            return {found: false};
        }""", code)
        
        if filled.get('found'):
            # 清空并用人类方式输入
            input_selector = filled.get('selector', 'input[type="text"]')
            await self.page.fill(input_selector, '')
            await random_delay(100, 200)
            for char in code:
                await self.page.type(input_selector, char, delay=random.randint(80, 150))
            Logger.log("验证码", f"已填入: {code}", "OK")
            return True
        return False
    
    async def login(self):
        Logger.log("登录", "访问...", "WAIT")
        await self.page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
        
        # 处理 Cloudflare 挑战页
        await handle_cloudflare(self.page, self.cdp)
        await asyncio.sleep(2)
        
        if 'xvps/index' in self.page.url and 'login' not in self.page.url:
            Logger.log("登录", "已登录", "OK")
            return True
        
        # 模拟人类行为
        await simulate_human_behavior(self.page)
        
        Logger.log("登录", "填写表单...")
        # 使用人类打字
        await human_type(self.page, '#memberid', self.email)
        await random_delay(500, 1000)
        await human_type(self.page, '#user_password', self.password)
        Logger.log("登录", "已填写", "OK")
        
        # 处理登录页的 Turnstile
        await handle_turnstile(self.page, self.cdp, 30)
        
        await random_delay(500, 1000)
        Logger.log("登录", "点击登录...")
        await self.page.click('input[name="action_user_login"]')
        await asyncio.sleep(5)
        
        # 可能有 Cloudflare 挑战
        await handle_cloudflare(self.page, self.cdp, 10)
        
        if 'login' not in self.page.url.lower() or 'xvps' in self.page.url:
            Logger.log("登录", "成功!", "OK")
            return True
        Logger.log("登录", "失败", "ERROR")
        return False
    
    async def get_vps_list(self):
        await self.page.goto(VPS_INDEX_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        return await self.page.evaluate(r'''() => {
            const r = [];
            for (const a of document.querySelectorAll('a')) {
                const t = a.textContent.trim();
                if (/vps-\d{4}-\d{2}-\d{2}/.test(t)) {
                    const m = a.href.match(/id_vps=(\d+)|id=(\d+)/);
                    if (m) r.push({id: m[1] || m[2], name: t});
                }
            }
            const s = new Set();
            return r.filter(v => { if (s.has(v.id)) return false; s.add(v.id); return true; });
        }''')
    
    async def get_vps_expiry(self, vps_id):
        await self.page.goto(f"https://secure.xserver.ne.jp/xapanel/xvps/server/detail?id={vps_id}", wait_until='domcontentloaded')
        await asyncio.sleep(2)
        return await self.page.evaluate(r'''() => {
            const m = document.body.innerText.match(/利用期限[\s\S]*?(\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})/);
            return m ? m[1] : null;
        }''')
    
    async def renew_vps(self, vps_id, vps_name):
        result = {'id': vps_id, 'name': vps_name, 'success': False, 'message': '', 'old_expiry': None, 'new_expiry': None}
        Logger.log("续期", f"处理: {vps_name}", "WAIT")
        
        result['old_expiry'] = await self.get_vps_expiry(vps_id)
        Logger.log("续期", f"当前到期: {result['old_expiry']}")
        
        await self.page.goto(f"https://secure.xserver.ne.jp/xapanel/xvps/server/freevps/extend/index?id_vps={vps_id}", wait_until='domcontentloaded')
        await asyncio.sleep(3)
        await self.screenshot("01_extend", True)
        
        txt = await self.page.evaluate('() => document.body.innerText')
        if '1日前から' in txt and '以降にお試しください' in txt:
            m = re.search(r'(\d+年\d+月\d+日)以降', txt)
            result['message'] = f"未到续期时间，可续期: {m.group(1) if m else '未知'}"
            Logger.log("续期", result['message'], "INFO")
            return result
        
        # 步骤1: 点击继续按钮
        Logger.log("续期", "步骤1: 点击继续...", "WAIT")
        await simulate_human_behavior(self.page)
        await self.page.evaluate("""() => {
            for (const b of document.querySelectorAll('button, a'))
                if ((b.textContent || '').includes('引き続き無料VPS')) { b.click(); return; }
        }""")
        await asyncio.sleep(3)
        await self.screenshot("02_verify", True)
        
        # 步骤2: 模拟人类行为
        await simulate_human_behavior(self.page)
        
        # 步骤3: 验证码
        Logger.log("续期", "步骤2: 验证码...", "WAIT")
        await self.solve_captcha()
        await self.screenshot("03_captcha")
        
        # 步骤4: Turnstile
        Logger.log("续期", "步骤3: Turnstile...", "WAIT")
        await handle_turnstile(self.page, self.cdp, 60)
        await self.screenshot("04_turnstile", True)
        
        # 检查令牌
        has_token = await self.page.evaluate('() => { const t = document.querySelector("[name=cf-turnstile-response]"); return t && t.value && t.value.length > 0; }')
        Logger.log("续期", f"Turnstile 令牌: {'有' if has_token else '无'}", "OK" if has_token else "WARN")
        
        # 步骤5: 提交
        Logger.log("续期", "步骤4: 提交...", "WAIT")
        await random_delay(500, 1000)
        await self.screenshot("05_submit")
        await self.page.evaluate("""() => {
            const btn = document.querySelector('button[type="submit"], input[type="submit"]');
            if (btn) btn.click();
            else for (const b of document.querySelectorAll('button'))
                if (b.textContent.includes('継続')) { b.click(); break; }
        }""")
        
        Logger.log("续期", "已提交", "OK")
        await asyncio.sleep(5)
        await self.screenshot("06_result", True)
        
        html = await self.page.content()
        if any(e in html for e in ["認証コードが正しくありません", "エラー"]):
            result['message'] = "验证码错误"
            return result
        
        new_expiry = await self.get_vps_expiry(vps_id)
        result['new_expiry'] = new_expiry
        Logger.log("续期", f"续期后: {new_expiry}")
        
        if result['old_expiry'] != new_expiry:
            result['success'] = True
            result['message'] = f"成功! {result['old_expiry']} -> {new_expiry}"
            Logger.log("续期", result['message'], "OK")
        else:
            result['message'] = "到期时间未变"
        return result
    
    async def run(self):
        Logger.log("账号", f"处理: {self.email}", "WAIT")
        results = []
        async with async_playwright() as p:
            await self.setup_browser(p)
            
            try:
                if not await self.login():
                    return [{'success': False, 'message': '登录失败'}]
                vps_list = await self.get_vps_list()
                Logger.log("VPS", f"找到 {len(vps_list)} 个", "OK")
                for vps in vps_list:
                    results.append(await self.renew_vps(vps['id'], vps['name']))
                with open(self.session_file, 'w') as f:
                    json.dump(await self.context.cookies(), f)
            except Exception as e:
                Logger.log("错误", str(e), "ERROR")
                results.append({'success': False, 'message': str(e)})
            await self.browser.close()
        return results


async def main():
    accounts = parse_accounts(ACCOUNTS_STR)
    if not accounts: exit(1)
    telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    
    print(f"\n{'='*50}")
    print("XServer VPS 续期 - 完整反自动化版本")
    print(f"Stealth 库: {'已加载' if HAS_STEALTH else '未安装'}")
    print(f"{'='*50}")
    
    all_results = []
    for acc in accounts:
        r = XServerVPSRenewer(acc['email'], acc['password'], telegram)
        all_results.append({'email': acc['email'], 'results': await r.run()})
    
    success = sum(1 for ar in all_results for r in ar['results'] if r.get('success'))
    total = sum(len(ar['results']) for ar in all_results)
    print(f"\n结果: {success}/{total}")
    
    if telegram.enabled:
        msg = "🖥 <b>XServer VPS 续期</b>\n\n"
        for ar in all_results:
            msg += f"📧 {ar['email']}\n"
            for r in ar['results']:
                s = "✅" if r.get('success') else "❌"
                msg += f"  {s} {r.get('name','?')}\n"
                if r.get('old_expiry'): msg += f"     原: {r['old_expiry']}\n"
                if r.get('new_expiry'): msg += f"     新: {r['new_expiry']}\n"
                if r.get('message'): msg += f"     {r['message']}\n"
        msg += f"\n📊 {success}/{total}"
        telegram.send(msg)


if __name__ == '__main__':
    asyncio.run(main())
