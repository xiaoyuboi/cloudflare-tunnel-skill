# Named Tunnel

Named Tunnel maps a fixed hostname such as `app.example.com` to a local service through Cloudflare Tunnel.

Use it when the user needs:

- a stable public URL
- webhook registration
- repeatable client previews
- a custom domain
- a long-lived public mapping

Prerequisites:

- `cloudflared` is installed.
- The user has a Cloudflare account.
- The domain is active in Cloudflare DNS.
- The user can complete browser login or provide an approved tunnel token through Cloudflare's normal mechanisms.

## Locally Managed Flow

Login. This opens a browser; the user picks one zone (domain), and the resulting `~/.cloudflared/cert.pem` is tied to that zone:

```bash
cloudflared tunnel login
```

Create the tunnel:

```bash
cloudflared tunnel create <TUNNEL_NAME>
```

Route DNS:

```bash
cloudflared tunnel route dns <TUNNEL_NAME> <HOSTNAME>
```

Start it in the background. This writes the config, starts `cloudflared`, and returns once an edge connection is registered:

```bash
python3 scripts/tunnel_helper.py named-run \
  --name <TUNNEL_NAME> \
  --hostname <HOSTNAME> \
  --url http://localhost:<PORT>
```

Manage it:

```bash
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py named-run --name <TUNNEL_NAME>   # start again from the saved config
python3 scripts/tunnel_helper.py stop --name <TUNNEL_NAME>
```

The tunnel keeps running after the agent session ends, but not across a reboot. For a tunnel that must come back by itself, install `cloudflared` as a system service with Cloudflare's own instructions instead.

To only write the config and run `cloudflared` yourself in the foreground:

```bash
python3 scripts/tunnel_helper.py named-config \
  --name <TUNNEL_NAME> \
  --hostname <HOSTNAME> \
  --url http://localhost:<PORT>
```

It prints the exact `cloudflared ... run` command to use.

## Config Shape

The helper writes `~/.cloudflare-tunnel-skill/named-<name>.yml`:

```yaml
tunnel: "<TUNNEL_NAME>"

ingress:
  - hostname: "app.example.com"
    service: http://localhost:3000
  - service: http_status:404
```

For many local setups this is enough because `cloudflared` can resolve credentials from the user's existing Cloudflare configuration. If a credentials file is required, pass `--credentials-file`, which adds:

```yaml
credentials-file: "/Users/<user>/.cloudflared/<TUNNEL_ID>.json"
```

## Common Pitfalls

- **Hostname in a different zone than the login.** `route dns` creates the record in the zone chosen during `cloudflared tunnel login`. For a hostname in another zone it can create a wrong record such as `app.other.com.<login-zone>`. Check the record that the command reports; if the zone is wrong, delete `~/.cloudflared/cert.pem`, log in again, and pick the right zone.
- **Hostname already has a DNS record.** `route dns` fails. Confirm with the user that the existing record may be replaced, then add `--overwrite-dns`.
- **`Cannot determine default origin certificate path`.** The user has not run `cloudflared tunnel login` on this machine.

## Token-Based Remotely Managed Tunnels

If the user creates a remotely managed tunnel in the Cloudflare dashboard, they may receive a run command using a token:

```bash
cloudflared tunnel run --token <TOKEN>
```

Do not print, commit, or store this token in repo files. If automation is needed, keep it in an environment variable or a local secret manager.

## Verification

Verify the local service first:

```bash
curl -I http://localhost:<PORT>
```

Then verify the hostname:

```bash
python3 scripts/tunnel_helper.py verify --url https://<HOSTNAME>
```

`verify` keeps retrying for up to 90 seconds, because DNS and edge propagation take time right after routing.
