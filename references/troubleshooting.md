# Troubleshooting

## `cloudflared` Missing

Check:

```bash
cloudflared --version
```

macOS with Homebrew:

```bash
brew install cloudflared
```

macOS without Homebrew (no admin rights needed):

```bash
mkdir -p ~/.local/bin
arch=$(uname -m | sed 's/x86_64/amd64/')
curl -fL -C - --retry 5 --retry-delay 3 -o /tmp/cloudflared.tgz "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-${arch}.tgz"
tar -xzf /tmp/cloudflared.tgz -C ~/.local/bin && rm /tmp/cloudflared.tgz
~/.local/bin/cloudflared --version
```

Linux without a package manager:

```bash
mkdir -p ~/.local/bin
arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
curl -fL -C - --retry 5 --retry-delay 3 -o ~/.local/bin/cloudflared "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}"
chmod +x ~/.local/bin/cloudflared
```

Windows:

```powershell
winget install --id Cloudflare.cloudflared
```

Notes:

- The download is about 20 MB from GitHub. On slow or unstable connections (common in mainland China) it can stall; `-C -` resumes, so rerun the same `curl` command until it finishes.
- The helper finds `~/.local/bin/cloudflared` even when that directory is not on `PATH`. For manual commands use the full path or run `export PATH="$HOME/.local/bin:$PATH"`.

## `python3` or `git` Prints an `xcode-select` Error

The Mac has no Xcode Command Line Tools, so both commands are placeholders. The skill does not need them:

- Install the skill with the `curl | tar` command from the README instead of `git clone`.
- Start the tunnel with the **Without Python** commands in `SKILL.md`.

## No `trycloudflare.com` URL Appears

Read the log:

```bash
tail -n 80 ~/.cloudflare-tunnel-skill/quick-*.log
```

`failed to request quick Tunnel` means `api.trycloudflare.com` was unreachable or rate limited; wait a little and start again. The helper already retries three times.

## Public URL Returns 404

An existing Cloudflare config can interfere with Quick Tunnel ingress rules. The helper always passes an empty config for this reason. For manual commands do the same:

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT>
```

A 404 can also simply be the app's own answer for `/`. Test a path that exists.

## Public URL Returns 502 / Bad Gateway

Cloudflare reached the tunnel connector, but the local service did not respond.

Check:

```bash
curl -I http://localhost:<PORT>
```

Fix the local app before debugging Cloudflare.

## Public URL Returns 530

The tunnel is no longer connected: `cloudflared` exited, the computer went to sleep, or the network dropped. Check `status` and start the tunnel again. A Quick Tunnel gets a new URL every time.

## Public URL Does Not Resolve

Right after creation the hostname may not resolve for a minute or two, and fake-IP proxy DNS can hide it for longer. `tunnel_helper.py verify` retries and falls back to DNS-over-HTTPS automatically. If `verify` reports `"ok": true` with a `hint`, the tunnel works and only local DNS is lagging.

## Local HTTPS Uses a Self-Signed Certificate

```bash
python3 scripts/tunnel_helper.py quick --url https://localhost:<PORT> --no-tls-verify
```

## Phone Cannot Open the App

Make sure the phone is opening the public URL:

```text
https://xxxx.trycloudflare.com
```

Not:

```text
http://localhost:<PORT>
```

## Link Dies After a While

The computer went to sleep. Keep it awake while sharing; on macOS:

```bash
caffeinate -i -w "$(pgrep -n cloudflared)"
```

## Named Tunnel DNS Fails

Check:

```bash
cloudflared tunnel list
cloudflared tunnel route dns <TUNNEL_NAME> <HOSTNAME>
```

Also confirm the domain is active in Cloudflare, the hostname is not already occupied by another DNS record, and the hostname belongs to the zone chosen during login. See **Common Pitfalls** in `references/named-tunnel.md`.

## Login Does Not Open Browser

Run:

```bash
cloudflared tunnel login
```

Copy the printed URL into a browser manually, choose the correct Cloudflare zone, then rerun the tunnel commands.
