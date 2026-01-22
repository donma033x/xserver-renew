# XServer VPS 免费VPS自动续期脚本

自动登录 XServer VPS 并在到期前自动续期免费VPS。

## ⚠️ 免责声明

本项目仅供学习网页自动化技术使用。使用本脚本可能违反相关网站的服务条款，包括但不限于：
- 禁止使用自动化工具访问
- 禁止绕过安全验证措施

**使用本项目的风险由用户自行承担**，包括但不限于账号被封禁、服务被终止等后果。请在使用前仔细阅读相关网站的服务条款。

## 功能

1. 自动登录 XServer 账户
2. 检查免费VPS到期时间
3. 在到期前1天自动续期
4. 支持日文验证码 OCR 识别
5. 支持 Cloudflare Turnstile 验证 (通过 YesCaptcha)
6. 续期前后对比到期时间，确认续期成功
7. 支持多账号
8. Telegram 通知

## 系统要求

- Linux (推荐) / macOS / Windows
- Python 3.10+
- uv (推荐) 或 pip
- 浏览器环境 (非 headless 模式运行更稳定)

## 安装

### 1. 安装 uv (如果未安装)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 克隆项目并安装依赖

```bash
git clone https://github.com/donma033x/xserver-renew.git
cd xserver-renew

# 使用 uv 安装依赖
uv sync

# 安装 Playwright 浏览器
uv run playwright install chromium
```

### 3. 配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
nano .env  # 或使用其他编辑器
```

配置说明：
- `XSERVER_ACCOUNT`: 账号配置，格式 `邮箱:密码`，多账号用 `&` 分隔
- `CAPTCHA_API_URL`: 日文验证码 OCR API 地址
- `YESCAPTCHA_KEY`: YesCaptcha API Key (必需，用于解决 Turnstile)
- `TELEGRAM_BOT_TOKEN`: Telegram Bot Token (可选)
- `TELEGRAM_CHAT_ID`: Telegram Chat ID (可选)

## 使用方法

### 手动运行

```bash
cd xserver-renew
# 加载环境变量并运行
set -a && source .env && set +a && uv run python xserver-renew.py
```

### 设置定时任务 (systemd)

```bash
# 复制服务文件
sudo cp xserver-renew.service /etc/systemd/system/
sudo cp xserver-renew.timer /etc/systemd/system/

# 修改 xserver-renew.service 中的路径为你的实际路径
sudo nano /etc/systemd/system/xserver-renew.service

# 重新加载 systemd
sudo systemctl daemon-reload

# 启用并启动定时器
sudo systemctl enable xserver-renew.timer
sudo systemctl start xserver-renew.timer

# 查看状态
systemctl status xserver-renew.timer
```

### 常用命令

```bash
# 手动触发
sudo systemctl start xserver-renew.service

# 查看日志
journalctl -u xserver-renew.service -f

# 查看下次执行时间
systemctl list-timers xserver-renew.timer
```

## 注意事项

1. **XServer 免费VPS 只能在到期前1天进行续期**
2. **必须配置 YESCAPTCHA_KEY** - Turnstile 验证需要打码平台支持
3. 建议设置 Telegram 通知以便了解续期状态
4. 续期成功会对比原到期时间和新到期时间确认

## 文件说明

- `xserver-renew.py` - 主脚本
- `pyproject.toml` - 项目配置和依赖
- `.env.example` - 配置文件示例
- `sessions/` - 会话保存目录 (自动创建)
- `debug/` - 调试截图目录 (失败时自动创建)
- `xserver-renew.service` - systemd 服务文件
- `xserver-renew.timer` - systemd 定时器文件

## 许可证

MIT
