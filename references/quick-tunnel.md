# Quick Tunnel

Quick Tunnel creates a temporary public URL under `trycloudflare.com` for a local HTTP/HTTPS service.

Use it for:

- local app previews
- demos and classroom sharing
- short-lived client review
- testing webhooks when a stable domain is not required

Avoid it for:

- production traffic
- fixed callback URLs
- large file distribution
- services that require guaranteed uptime
- streaming features that depend on Server-Sent Events (SSE)

## Recommended Flow

1. Find the local service URL.
2. Verify it locally.
3. Start the tunnel with HTTP/2 and an empty config.
4. Wait for the public URL and registered edge connection.
5. Verify the public URL.
6. Tell the user it is temporary.

## Helper Command

```bash
python3 scripts/tunnel_helper.py quick --url http://localhost:<PORT>
```

Useful options:

```bash
python3 scripts/tunnel_helper.py quick --port 3000
python3 scripts/tunnel_helper.py quick --url https://localhost:8443 --no-tls-verify
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py stop --port 3000
python3 scripts/tunnel_helper.py stop --all
```

State lives in `~/.cloudflare-tunnel-skill/` (override with `CLOUDFLARE_TUNNEL_STATE_DIR`), so `status` and `stop` work from any directory:

- `quick-<host>-<port>.json`: pid, local URL, public URL of one tunnel
- `quick-<host>-<port>.log`: its `cloudflared` output
- `empty.yml`: the deliberately empty config

Behavior:

- Re-running `quick` with the same local URL reuses the running tunnel. A different local URL starts another tunnel next to it, for example a frontend on 3000 and an API on 8000.
- `stop` without arguments stops the only running tunnel. With several running it lists them and asks for `--port`, `--url` (local or public), or `--all`.
- Before reusing or stopping, the helper checks that the recorded pid is still its own `cloudflared`. A stale pid that now belongs to another program is ignored, never killed.
- `status` drops entries whose process is gone and says so.
- Transient `api.trycloudflare.com` failures are retried up to 3 times automatically.
- `cloudflared` is also found in `~/.local/bin`, `~/bin`, `/opt/homebrew/bin`, and `/usr/local/bin` when it is not on `PATH`. Set `CLOUDFLARED_BIN` to use a specific binary.

## Manual Command

Foreground, for a terminal the user keeps open:

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT>
```

An agent should run it in the background instead; see **Without Python** in `SKILL.md`.

For self-signed local HTTPS add `--no-tls-verify`:

```bash
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --no-tls-verify --url https://localhost:8443
```

`ERR Configuration file ... was empty` in the output is expected and harmless.

## Verification

```bash
python3 scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --contains "<text from the page>"
```

`verify` retries every 5 seconds for up to 90 seconds (`--wait`), because DNS for a fresh subdomain can take a minute or two. If the service returns JSON, verify a known endpoint such as `/health`.

Without the helper:

```bash
curl --noproxy '*' -sS -L --max-time 20 https://xxxx.trycloudflare.com
```

If curl fails with `Could not resolve host` (exit code 6), the system resolver cannot see the fresh subdomain yet. This is common right after tunnel creation and behind fake-IP proxy DNS. Retry with DNS-over-HTTPS:

```bash
curl --noproxy '*' --doh-url https://dns.alidns.com/dns-query -sS -L --max-time 20 https://xxxx.trycloudflare.com
```

Outside mainland China `https://1.1.1.1/dns-query` works as well; inside it usually times out. The helper tries both automatically and reports which one it needed in the `dns` field.

## Notes

Quick Tunnel URLs are ephemeral. The URL changes when the process restarts, and it stops working when the machine sleeps, the network disconnects, or `cloudflared` exits. A stopped tunnel answers with HTTP 530.
