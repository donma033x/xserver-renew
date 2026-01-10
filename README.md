# XServer VPS 免费VPS自动续期脚本

## 功能

1. 自动登录 XServer 账户
2. 检查免费VPS到期时间
3. 在到期前1天自动续期
4. 支持 Cloudflare Turnstile 验证
5. 支持多账号
6. Telegram 通知

## 使用方法

### 1. 安装依赖

```bash
cd ~/xserver-vps-renew
python3 -m venv venv
source venv/bin/activate
pip install playwright requests
playwright install chromium
```

### 2. 配置

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

配置说明：
- `ACCOUNTS`: 账号配置，格式 `邮箱:密码`，多账号用逗号分隔
- `TELEGRAM_BOT_TOKEN`: Telegram Bot Token (可选)
- `TELEGRAM_CHAT_ID`: Telegram Chat ID (可选)

### 3. 手动运行

```bash
cd ~/xserver-vps-renew
source venv/bin/activate
xvfb-run python3 renew.py
```

### 4. 定时任务

已配置 systemd timer，每天早上9点运行：

```bash
# 查看状态
systemctl status xserver-vps-renew.timer

# 手动触发
sudo systemctl start xserver-vps-renew.service

# 查看日志
journalctl -u xserver-vps-renew.service -f
```

## 注意事项

1. XServer 免费VPS 只能在到期前1天进行续期
2. 建议设置 Telegram 通知以便了解续期状态
3. 脚本会保存会话，下次运行无需重新登录

## 文件说明

- `renew.py` - 主脚本
- `.env` - 配置文件
- `.env.example` - 配置文件示例
- `sessions/` - 会话保存目录
- `xserver-vps-renew.service` - systemd 服务文件
- `xserver-vps-renew.timer` - systemd 定时器文件
