# Cloudflare Tunnel Skill

An agent workflow skill for exposing local HTTP/HTTPS services through Cloudflare Tunnel.

It supports two modes:

- **Quick Tunnel**: temporary `https://*.trycloudflare.com` preview URLs for demos and short-lived sharing.
- **Named Tunnel**: fixed custom hostnames such as `app.example.com -> http://localhost:3000`.

## Use Cases

- Share `localhost:3000` with someone else.
- Create a temporary public URL for a local API.
- Map a fixed domain to a local development service.
- Provide webhook endpoints for GitHub, Slack, Stripe, n8n, and similar tools.
- Let an agent check the local service, start a tunnel, extract the public URL, and verify it.

## Avoid For

- Sensitive admin panels without authentication.
- Raw databases, Redis, Docker API, or private network services.
- Public file hosting or large downloads.
- Production services that cannot tolerate local machine sleep, process exits, or network loss.

## Install

Put the whole repository into your skills directory, in a folder named `cloudflare-tunnel`.

**Claude Code (global, available in all projects)**

With git:

```bash
git clone https://github.com/xiaoyuboi/cloudflare-tunnel-skill ~/.claude/skills/cloudflare-tunnel
```

Without git (for example a new Mac where `git` prints an `xcode-select` error), use curl:

```bash
mkdir -p ~/.claude/skills/cloudflare-tunnel && curl -fsSL https://github.com/xiaoyuboi/cloudflare-tunnel-skill/archive/refs/heads/main.tar.gz | tar -xz --strip-components=1 -C ~/.claude/skills/cloudflare-tunnel
```

**Other locations**: replace `~/.claude/skills` in the commands above.

| Use | Directory |
|---|---|
| Claude Code, current project only | `.claude/skills/cloudflare-tunnel` |
| Codex | `~/.codex/skills/cloudflare-tunnel` |

**Update**: `git -C ~/.claude/skills/cloudflare-tunnel pull` for a git install; rerun the install command for a curl install.

Start a new session afterwards and ask the agent, for example, "give me a public link for my local app on port 3000".

## Requirements

- **cloudflared**: required. If it is missing, the agent installs it with the commands in [references/troubleshooting.md](references/troubleshooting.md), with or without Homebrew.
- **Python 3**: optional. The helper script needs it; without it the agent falls back to plain `cloudflared` commands with the same result.
- Fully tested on macOS and Linux. Windows support is best-effort; prefer WSL.

## Quick Tunnel

`scripts/` below means the `scripts/` folder inside the installed skill.

```bash
python3 scripts/tunnel_helper.py quick --url http://localhost:3000
```

Verify the public URL (retries while DNS propagates):

```bash
python3 scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com
```

Status and stop:

```bash
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py stop --port 3000
```

Several tunnels can run at once, one per local address. State and logs live in `~/.cloudflare-tunnel-skill/`, so nothing is written into your project and `status` / `stop` work from any directory.

Manual command without the helper:

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:3000
```

## Named Tunnel

Prerequisites:

- `cloudflared` is installed.
- You have a Cloudflare account.
- The domain is managed by Cloudflare DNS.

Flow:

```bash
cloudflared tunnel login
cloudflared tunnel create my-app
cloudflared tunnel route dns my-app app.example.com
python3 scripts/tunnel_helper.py named-run --name my-app --hostname app.example.com --url http://localhost:3000
```

`named-run` starts the tunnel in the background. Stop it with:

```bash
python3 scripts/tunnel_helper.py stop --name my-app
```

Pick the zone that contains the hostname during login; see [references/named-tunnel.md](references/named-tunnel.md) for other pitfalls.

## Test

End-to-end Quick Tunnel test (serves a temporary page, then start, verify, stop):

```bash
python3 scripts/test_quick_tunnel.py
```

Basic checks:

```bash
python3 -m py_compile scripts/tunnel_helper.py scripts/test_quick_tunnel.py
python3 scripts/tunnel_helper.py check
```

## Security

Cloudflare Tunnel exposes your local service to the public internet. Before sharing a URL, make sure it does not expose tokens, cookies, API keys, private data, internal systems, unauthenticated admin panels, or unsafe write APIs.

Use application authentication or Cloudflare Access for sensitive services.
