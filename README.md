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
4. 支持 Cloudflare Turnstile 验证
5. 支持多账号
6. Telegram 通知

## 系统要求

- Linux (Ubuntu/Debian)
- Python 3.10+
- uv (推荐) 或 pip

## 安装

### 1. 安装系统依赖

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y xvfb

# xvfb 用于在无显示器的服务器上运行浏览器
```

### 2. 安装 uv (如果未安装)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. 克隆项目并安装依赖

```bash
git clone https://github.com/donma033x/xserver-vps-renew.git
cd xserver-vps-renew

# 使用 uv 安装依赖
uv sync

# 安装 Playwright 浏览器
uv run playwright install chromium
```

### 4. 配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
nano .env  # 或使用其他编辑器
```

配置说明：
- `ACCOUNTS`: 账号配置，格式 `邮箱:密码`，多账号用逗号分隔
- `TELEGRAM_BOT_TOKEN`: Telegram Bot Token (可选)
- `TELEGRAM_CHAT_ID`: Telegram Chat ID (可选)

## 使用方法

### 手动运行

```bash
cd xserver-vps-renew
xvfb-run uv run python renew.py
```

### 设置定时任务 (systemd)

```bash
# 复制服务文件
sudo cp xserver-vps-renew.service /etc/systemd/system/
sudo cp xserver-vps-renew.timer /etc/systemd/system/

# 重新加载 systemd
sudo systemctl daemon-reload

# 启用并启动定时器
sudo systemctl enable xserver-vps-renew.timer
sudo systemctl start xserver-vps-renew.timer

# 查看状态
systemctl status xserver-vps-renew.timer
```

注意: 使用前需要修改 `xserver-vps-renew.service` 中的路径为你的实际路径。

### 常用命令

```bash
# 手动触发
sudo systemctl start xserver-vps-renew.service

# 查看日志
journalctl -u xserver-vps-renew.service -f

# 查看下次执行时间
systemctl list-timers xserver-vps-renew.timer
```

## 注意事项

1. **XServer 免费VPS 只能在到期前1天进行续期**
2. 建议设置 Telegram 通知以便了解续期状态
3. 脚本会保存会话，下次运行无需重新登录

## 文件说明

- `renew.py` - 主脚本
- `pyproject.toml` - 项目配置和依赖
- `.env.example` - 配置文件示例
- `sessions/` - 会话保存目录
- `xserver-vps-renew.service` - systemd 服务文件
- `xserver-vps-renew.timer` - systemd 定时器文件

## 许可证

MIT
