---
name: cloudflare-tunnel
description: "Expose a local HTTP/HTTPS service (localhost) to the public internet with Cloudflare Tunnel: temporary trycloudflare.com Quick Tunnel links for demos and previews, or named tunnels on a fixed custom domain for webhooks and long-lived access. Use when the user wants to share localhost, needs a public URL or webhook endpoint for a local app, or says things like 内网穿透、把本地网页/本地服务发给别人看、生成临时公网链接、用自己的域名访问本地服务."
license: MIT
metadata:
  tags: [cloudflare, tunnel, cloudflared, localhost, public-url, preview, custom-domain, 内网穿透]
---

# Cloudflare Tunnel Agent Workflow

Help the user expose a local service through Cloudflare Tunnel. Choose the safest working mode:

- **Quick mode**: temporary `https://*.trycloudflare.com` URL. Use for demos, previews, classroom sharing, and short-lived testing.
- **Named mode**: fixed hostname such as `app.example.com`. Use when the user owns a Cloudflare-managed domain and needs a stable public URL.

Before running commands, identify:

1. Local service URL, usually `http://localhost:<port>` or `https://localhost:<port>`.
2. Exposure mode: quick or named.
3. Whether the service contains admin panels, tokens, private data, internal systems, or unauthenticated write APIs.

If the service is sensitive, pause and warn the user before exposing it. See `references/security.md`.

## Paths and State

`scripts/...` and `references/...` in this document are relative to the directory that contains this `SKILL.md`, not the user's project. Call the helper with its full path, for example:

```bash
python3 ~/.claude/skills/cloudflare-tunnel/scripts/tunnel_helper.py quick --url http://localhost:3000
```

The helper keeps its state and logs in `~/.cloudflare-tunnel-skill/` (override with `CLOUDFLARE_TUNNEL_STATE_DIR`). `status` and `stop` work from any directory, nothing is written into the user's project, and several tunnels can run at the same time, one per local address.

## Mode Selection

Use **quick mode** when:

- The user says temporary, demo, preview, share localhost, quick public link, or no domain.
- The URL may change after restart.
- The user does not need Cloudflare login.

Use **named mode** when:

- The user asks for a fixed domain, stable public URL, webhook endpoint, or long-lived mapping.
- The user has a Cloudflare account and a domain whose DNS is managed by Cloudflare.
- The hostname should survive process restarts.

If unclear, default to quick mode for non-sensitive demos and ask only when exposing a sensitive service or creating a fixed hostname.

## Required Checks

Run all three before creating a tunnel.

1. `cloudflared`:

   ```bash
   cloudflared --version
   ```

   If it is missing, install it with the commands in `references/troubleshooting.md` (Homebrew, or a direct download that needs neither Homebrew nor admin rights).

2. Python, by actually running it:

   ```bash
   python3 --version
   ```

   On a Mac without Xcode Command Line Tools, `python3` and `git` exist only as stubs that print an `xcode-select` error. Do not install developer tools just for this skill; skip the helper and use **Without Python** below.

3. The local service:

   ```bash
   curl -I http://localhost:<PORT>
   ```

   Any HTTP response below 500 (`200`, `301`, `302`, `401`, `403`, `404`) shows the service is reachable. Investigate connection failures before starting a tunnel.

## Quick Mode

Read `references/quick-tunnel.md` when the user wants a temporary public URL.

Start:

```bash
python3 scripts/tunnel_helper.py quick --url http://localhost:<PORT>
```

The helper starts `cloudflared` in the background, waits for the `trycloudflare.com` URL and a registered edge connection, and prints JSON with `public_url` and a ready-made `stop_command`.

Verify (retries by itself for up to 90 seconds while DNS propagates):

```bash
python3 scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --contains "<text from the page>"
```

List and stop:

```bash
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py stop --port <PORT>
```

Helper behavior worth knowing:

- Running `quick` again for the same local URL reuses the running tunnel (`"reused": true`). A different local URL gets its own tunnel; the existing ones keep running.
- `stop` without arguments stops the only running tunnel. With several running it refuses and lists them; pass `--port`, `--url`, `--name`, or `--all`.
- Transient `api.trycloudflare.com` failures are retried up to 3 times automatically.
- When the system resolver cannot resolve a fresh hostname (common right after creation and behind fake-IP proxy DNS), `verify` falls back to DNS-over-HTTPS and reports it in `dns` and `hint`. The tunnel works; tell the user that browsers on this network may need a minute or two.
- The log line `ERR Configuration file ... was empty` is expected. The empty config is deliberate so that an existing `~/.cloudflared/config.yml` cannot interfere.

### Without Python

Run `cloudflared` directly in the background:

```bash
mkdir -p ~/.cloudflare-tunnel-skill && cd ~/.cloudflare-tunnel-skill
: > empty.yml
nohup cloudflared --config empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT> > manual.log 2>&1 &
echo $! > manual.pid
```

Poll `manual.log` (it takes about 10 seconds) until it contains both a `https://*.trycloudflare.com` URL and a `Registered tunnel connection` line:

```bash
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' manual.log | grep -v '//api\.' | head -1
grep -c 'Registered tunnel connection' manual.log
```

Verify. A fresh hostname often does not resolve through the system DNS for the first minute or two, so resolve it through DNS-over-HTTPS (`https://1.1.1.1/dns-query` also works outside mainland China):

```bash
curl --noproxy '*' --doh-url https://dns.alidns.com/dns-query -sS -L --max-time 20 https://xxxx.trycloudflare.com
```

If that returns the page, the tunnel works; tell the user that browsers on this network may need a minute or two before the link opens.

Stop:

```bash
kill "$(cat ~/.cloudflare-tunnel-skill/manual.pid)"
```

## Named Mode

Read `references/named-tunnel.md` when the user wants a fixed domain.

One-time setup. `login` opens a browser and the user must pick the zone that contains the hostname:

```bash
cloudflared tunnel login
cloudflared tunnel create <TUNNEL_NAME>
cloudflared tunnel route dns <TUNNEL_NAME> <HOSTNAME>
```

Start in the background (writes the config, starts `cloudflared`, waits for a registered connection):

```bash
python3 scripts/tunnel_helper.py named-run \
  --name <TUNNEL_NAME> \
  --hostname <HOSTNAME> \
  --url http://localhost:<PORT>
```

Later, `named-run --name <TUNNEL_NAME>` restarts it from the saved config. Verify and stop:

```bash
python3 scripts/tunnel_helper.py verify --url https://<HOSTNAME>
python3 scripts/tunnel_helper.py stop --name <TUNNEL_NAME>
```

`named-config` with the same arguments only writes the config and prints a foreground command, for users who want to run `cloudflared` themselves.

Do not paste, print, commit, or store Cloudflare tokens outside the user's local Cloudflare config.

## Output

After success, return:

- Public URL
- Local URL
- Mode used
- How to stop the tunnel (the `stop_command` from the helper output)
- A short temporary/stability warning for quick mode

Example:

```text
Public URL: https://xxxx.trycloudflare.com
Local URL: http://localhost:3000
Mode: quick
Stop: python3 ~/.claude/skills/cloudflare-tunnel/scripts/tunnel_helper.py stop --url http://localhost:3000

This is a temporary Quick Tunnel URL. It stops working if cloudflared exits, the computer sleeps, or the network disconnects.
```

## Troubleshooting

Use `references/troubleshooting.md` for:

- installing `cloudflared`, including without Homebrew and on slow GitHub connections
- no public URL in logs
- 404 from the tunnel
- 502 / Bad Gateway, 530
- self-signed local HTTPS
- phone cannot open the link
- named tunnel DNS or login failures
