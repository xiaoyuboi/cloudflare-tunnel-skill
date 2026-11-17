# Cloudflare Tunnel Skill

[中文](#中文) | [English](README_EN.md)

---

## 中文

一个给 Agent 使用的 Cloudflare Tunnel workflow skill。它可以把本地 HTTP/HTTPS 服务暴露到公网，支持两种模式：

- **Quick Tunnel**：临时 `https://*.trycloudflare.com` 预览链接，适合 demo、课堂、客户临时预览。
- **Named Tunnel**：固定域名映射，例如 `app.example.com -> http://localhost:3000`，适合 webhook、长期预览、团队测试入口。

## 适合场景

- 把 `localhost:3000` 临时发给别人看
- 给本地 API 创建临时公网 URL
- 用固定域名访问本地开发服务
- 给 GitHub、Slack、Stripe、n8n 等 webhook 配一个公网入口
- 让 Agent 自动检查本地服务、启动 tunnel、验证公网 URL

## 不适合场景

- 没有认证保护的敏感后台
- 数据库、Redis、Docker API 等裸服务
- 大文件下载站或公开网盘
- 不愿意接受本地电脑断网/睡眠导致服务中断的生产服务

## 安装

把整个仓库放进 skill 目录即可，目录名用 `cloudflare-tunnel`。

**Claude Code（全局，所有项目可用）**

有 git：

```bash
git clone https://github.com/xiaoyuboi/cloudflare-tunnel-skill ~/.claude/skills/cloudflare-tunnel
```

没有 git（比如新 Mac 上 `git` 报 `xcode-select` 错误），用 curl：

```bash
mkdir -p ~/.claude/skills/cloudflare-tunnel && curl -fsSL https://github.com/xiaoyuboi/cloudflare-tunnel-skill/archive/refs/heads/main.tar.gz | tar -xz --strip-components=1 -C ~/.claude/skills/cloudflare-tunnel
```

**其他位置**：把上面命令里的 `~/.claude/skills` 换掉。

| 用途 | 目录 |
|---|---|
| Claude Code 仅当前项目 | `.claude/skills/cloudflare-tunnel` |
| Codex | `~/.codex/skills/cloudflare-tunnel` |

**更新**：git 安装的执行 `git -C ~/.claude/skills/cloudflare-tunnel pull`；curl 安装的重新执行一遍安装命令。

装好后重开一个会话，直接对 Agent 说「把我本地 3000 端口的网页生成一个公网链接」就行。

## 系统要求

- **cloudflared**：必需。没装的话 Agent 会按 [references/troubleshooting.md](references/troubleshooting.md) 里的命令安装，有 Homebrew 和免 Homebrew 两种方式。
- **Python 3**：可选。有的话用 helper 脚本，体验更好；没有的话 Agent 会改用纯 `cloudflared` 命令，功能一样。
- macOS / Linux 经过完整测试；Windows 为尽力支持，建议优先使用 WSL。

国内网络说明：`trycloudflare.com` 的链接国内可以直接打开；从 GitHub 下载 cloudflared 可能很慢或中断，重复执行同一条下载命令会断点续传。

## Quick Tunnel 临时链接

以下命令里的 `scripts/` 指 skill 安装目录下的 `scripts/`。

```bash
python3 scripts/tunnel_helper.py quick --url http://localhost:3000
```

验证公网链接（会自动重试，等 DNS 生效）：

```bash
python3 scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com
```

查看状态和停止：

```bash
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py stop --port 3000
```

可以同时开多条隧道，每个本地地址一条。状态和日志统一放在 `~/.cloudflare-tunnel-skill/`，不会往你的项目里写文件，在任何目录都能查状态和停止。

不用脚本的手动命令：

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:3000
```

## Named Tunnel 固定域名

前提：

- 已安装 `cloudflared`
- 有 Cloudflare 账号
- 域名 DNS 已托管到 Cloudflare

基础流程：

```bash
cloudflared tunnel login
cloudflared tunnel create my-app
cloudflared tunnel route dns my-app app.example.com
python3 scripts/tunnel_helper.py named-run --name my-app --hostname app.example.com --url http://localhost:3000
```

`named-run` 会在后台启动隧道。停止：

```bash
python3 scripts/tunnel_helper.py stop --name my-app
```

登录时要选对域名所在的站点，其他注意事项见 [references/named-tunnel.md](references/named-tunnel.md)。

## 本地测试

端到端 Quick Tunnel 测试（起一个临时网页，走完 启动 → 验证 → 停止）：

```bash
python3 scripts/test_quick_tunnel.py
```

脚本基础检查：

```bash
python3 -m py_compile scripts/tunnel_helper.py scripts/test_quick_tunnel.py
python3 scripts/tunnel_helper.py check
```

## 安全提醒

Tunnel 会把本地服务暴露到公网。发布前先确认没有公开：

- Token、Cookie、API key
- 未加认证的后台
- 内网系统
- 数据库或调试控制台
- 可修改数据的无认证 API

敏感服务请先加应用登录或 Cloudflare Access。
