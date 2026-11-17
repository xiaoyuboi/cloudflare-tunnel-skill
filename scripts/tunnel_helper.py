#!/usr/bin/env python3
"""Helper for Cloudflare Tunnel agent workflows.

Starts cloudflared in the background, waits until the tunnel is registered,
and records every tunnel in a per-user state directory
(~/.cloudflare-tunnel-skill by default, override with
CLOUDFLARE_TUNNEL_STATE_DIR). status and stop therefore work from any working
directory, and several tunnels can run side by side.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


DEFAULT_TIMEOUT = 120
START_ATTEMPTS = 3
VERIFY_INTERVAL = 5
URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
API_HOST_URL = "https://api.trycloudflare.com"
REGISTERED_MARKER = "Registered tunnel connection"
# Tried in order when the system resolver cannot resolve a hostname. AliDNS is
# first because 1.1.1.1 is unreachable from many networks in mainland China.
DOH_URLS = ("https://dns.alidns.com/dns-query", "https://1.1.1.1/dns-query")
IS_WINDOWS = os.name == "nt"
SCRIPT_PATH = Path(__file__).resolve()

# cloudflared log fragments that indicate a transient edge/API failure worth retrying.
TRANSIENT_MARKERS = (
    "failed to request quick Tunnel",
    "context deadline exceeded",
    "Client.Timeout exceeded",
)


# ---------------------------------------------------------------- output


def print_json(data: dict[str, object]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def fail(message: str, **extra: object) -> int:
    print_json({"ok": False, "error": message, **extra})
    return 1


def quote_command(parts: list[str]) -> str:
    if IS_WINDOWS:
        return subprocess.list2cmdline(parts)
    return " ".join(shlex.quote(part) for part in parts)


def helper_command(*args: str) -> str:
    python = next((name for name in ("python3", "python") if shutil.which(name)), sys.executable)
    return quote_command([python, str(SCRIPT_PATH), *args])


def log_tail(content: str, lines: int = 6) -> str:
    kept = [line.strip() for line in content.splitlines() if line.strip()]
    return " | ".join(kept[-lines:])


# ----------------------------------------------------------------- state


def state_dir() -> Path:
    override = os.environ.get("CLOUDFLARE_TUNNEL_STATE_DIR")
    if override:
        return Path(override).expanduser().absolute()
    return Path.home() / ".cloudflare-tunnel-skill"


def state_path(key: str) -> Path:
    return state_dir() / f"{key}.json"


def log_path(key: str) -> Path:
    return state_dir() / f"{key}.log"


def load_state(key: str) -> dict | None:
    try:
        data = json.loads(state_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("key") == key else None


def save_state(state: dict) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    state_path(state["key"]).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def drop_state(key: str) -> None:
    try:
        state_path(key).unlink()
    except OSError:
        pass


def all_states() -> list[dict]:
    states = []
    for path in sorted(state_dir().glob("*.json")):
        state = load_state(path.stem)
        if state:
            states.append(state)
    return states


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def quick_key(local_url: str) -> str:
    return "quick-" + (slug(urlsplit(local_url).netloc) or "local")


def named_key(name: str) -> str:
    key = slug(name)
    if not key:
        raise RuntimeError("invalid tunnel name")
    return "named-" + key


def url_port(url: str | None) -> int | None:
    if not url:
        return None
    parts = urlsplit(url)
    try:
        if parts.port:
            return parts.port
    except ValueError:
        return None
    return {"http": 80, "https": 443}.get(parts.scheme)


def public_view(state: dict) -> dict[str, object]:
    return {
        "key": state.get("key"),
        "mode": state.get("mode"),
        "pid": state.get("pid"),
        "local_url": state.get("local_url"),
        "public_url": state.get("public_url"),
        "log_file": state.get("log_file"),
    }


def stop_hint(state: dict) -> str:
    if state.get("mode") == "named":
        return helper_command("stop", "--name", str(state.get("name")))
    return helper_command("stop", "--url", str(state.get("local_url")))


# ------------------------------------------------------------- processes


def process_command(pid: int) -> str | None:
    """Return the command line of pid, or None when no such process exists."""
    if IS_WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        for line in result.stdout.splitlines():
            fields = [field.strip('"') for field in line.split('","')]
            if len(fields) > 1 and fields[1] == str(pid):
                return fields[0]
        return None
    proc_file = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_file.exists():
            return proc_file.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip() or None
    except OSError:
        pass
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "command="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip() or None


def is_tunnel_alive(state: dict) -> bool:
    """True only when the recorded pid is still the cloudflared this helper started.

    A pid alone is not proof: after a reboot or a crash the number can belong
    to an unrelated process, which must never be reused or killed.
    """
    pid = state.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    command = process_command(pid)
    if not command or "cloudflared" not in command.lower():
        return False
    marker = state.get("marker")
    if marker and not IS_WINDOWS and str(marker) not in command:
        return False
    return True


def send_signal(pid: int, force: bool = False) -> None:
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def stop_pid(pid: int, proc: subprocess.Popen | None = None) -> None:
    def gone() -> bool:
        if proc is not None:
            return proc.poll() is not None
        return process_command(pid) is None

    send_signal(pid)
    deadline = time.time() + 8
    while time.time() < deadline:
        if gone():
            return
        time.sleep(0.2)
    send_signal(pid, force=True)
    if proc is not None:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def popen_kwargs() -> dict[str, object]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def find_cloudflared() -> str:
    override = os.environ.get("CLOUDFLARED_BIN")
    if override:
        if Path(override).expanduser().is_file():
            return str(Path(override).expanduser())
        raise RuntimeError(f"CLOUDFLARED_BIN points to a missing file: {override}")
    found = shutil.which("cloudflared")
    if found:
        return found
    # Agent shells often miss Homebrew or per-user bin directories in PATH.
    name = "cloudflared.exe" if IS_WINDOWS else "cloudflared"
    candidates = [
        Path.home() / ".local" / "bin" / name,
        Path.home() / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    ]
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "cloudflared" / name)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError("cloudflared not found. Install it first; see references/troubleshooting.md.")


# ------------------------------------------------------------------ http


def normalize_url(args: argparse.Namespace) -> str:
    url = (getattr(args, "url", None) or "").strip()
    if url:
        if "://" not in url:
            url = "http://" + url
        parts = urlsplit(url)
        if not parts.netloc:
            raise RuntimeError(f"invalid local URL: {url}")
        return url[:-1] if parts.path == "/" and not parts.query else url
    if getattr(args, "port", None):
        return f"http://localhost:{args.port}"
    raise RuntimeError("provide --url or --port")


def run_curl(url: str, timeout: int, extra_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["curl", "--noproxy", "*", "-sS", "--max-time", str(timeout), *extra_args, "-w", "\n%{http_code}", url],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def fetch(url: str, timeout: int = 15, follow: bool = True, insecure: bool = False) -> tuple[int | None, str, str]:
    """Fetch url bypassing proxies. Returns (status, body, dns).

    dns is "system", or the DNS-over-HTTPS endpoint that had to be used because
    the system resolver could not resolve the hostname yet.
    """
    if shutil.which("curl"):
        base: tuple[str, ...] = (("-L",) if follow else ()) + (("-k",) if insecure else ())
        result = run_curl(url, timeout, base)
        dns = "system"
        if result.returncode == 6:
            # DNS resolution failed. Fake-IP proxy resolvers and slow trycloudflare
            # propagation both break the system resolver, so retry via DNS-over-HTTPS.
            for doh in DOH_URLS:
                retry = run_curl(url, timeout, base + ("--connect-timeout", "8", "--doh-url", doh))
                if retry.returncode == 0:
                    result, dns = retry, doh
                    break
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"curl exited {result.returncode}")
        body, _, status_text = result.stdout.rpartition("\n")
        try:
            status: int | None = int(status_text)
        except ValueError:
            status = None
        return status, body, dns

    handlers: list[object] = [ProxyHandler({})]
    if insecure:
        handlers.append(HTTPSHandler(context=ssl._create_unverified_context()))  # noqa: SLF001
    opener = build_opener(*handlers)
    req = Request(url, headers={"User-Agent": "cloudflare-tunnel-skill/3.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), "system"
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), "system"
    except (URLError, OSError) as exc:
        raise RuntimeError(str(getattr(exc, "reason", exc))) from exc


def verify_local(url: str, insecure: bool = False) -> None:
    try:
        status, _, _ = fetch(url, timeout=10, follow=False, insecure=insecure)
    except RuntimeError as exc:
        raise RuntimeError(f"local service {url} is not reachable: {exc}") from exc
    if status is not None and status >= 500:
        raise RuntimeError(f"local service {url} returned HTTP {status}; fix the app before exposing it")


# ---------------------------------------------------------------- launch


def find_public_url(content: str) -> str | None:
    for match in URL_PATTERN.finditer(content):
        if match.group(0) != API_HOST_URL:
            return match.group(0)
    return None


def launch(cmd: list[str], state: dict, timeout: int, want_public_url: bool) -> tuple[bool, str | None, str | None, bool]:
    """Start cloudflared once and wait until it is usable.

    Returns (ok, public_url, error, transient). On failure the process is
    stopped and its state entry removed; the log file is kept for diagnosis.
    """
    key = state["key"]
    log_file = log_path(key)
    state_dir().mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **popen_kwargs(),
        )
    state["pid"] = proc.pid
    save_state(state)

    public_url = None
    content = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        if proc.poll() is not None:
            drop_state(key)
            transient = any(marker in content for marker in TRANSIENT_MARKERS)
            return False, None, f"cloudflared exited with code {proc.returncode}: {log_tail(content)}", transient
        if want_public_url and not public_url:
            public_url = find_public_url(content)
        if REGISTERED_MARKER in content and (public_url or not want_public_url):
            return True, public_url, None, False
        time.sleep(0.5)

    stop_pid(proc.pid, proc)
    drop_state(key)
    if want_public_url and not public_url:
        return False, None, f"no trycloudflare.com URL within {timeout}s: {log_tail(content)}", False
    return False, None, f"cloudflared did not register an edge connection within {timeout}s: {log_tail(content)}", True


def start_with_retries(cmd: list[str], state: dict, timeout: int, want_public_url: bool) -> tuple[bool, str | None, str, int]:
    error = "tunnel failed to start"
    for attempt in range(1, START_ATTEMPTS + 1):
        ok, public_url, launch_error, transient = launch(cmd, state, timeout, want_public_url)
        if ok:
            return True, public_url, "", attempt
        error = launch_error or error
        if not transient:
            break
        time.sleep(2)
    return False, None, error, attempt


def reuse_or_clear(key: str) -> dict | None:
    """Return a healthy running tunnel for key, or clear whatever is left of it."""
    existing = load_state(key)
    if not existing:
        return None
    if is_tunnel_alive(existing):
        if existing.get("public_url"):
            return existing
        stop_pid(existing["pid"])
    drop_state(key)
    return None


# -------------------------------------------------------------- commands


def cmd_check(_: argparse.Namespace) -> int:
    try:
        path = find_cloudflared()
        result = subprocess.run([path, "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        print_json(
            {
                "ok": True,
                "cloudflared": path,
                "version": result.stdout.strip(),
                "python": sys.version.split()[0],
                "state_dir": str(state_dir()),
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_quick(args: argparse.Namespace) -> int:
    try:
        local_url = normalize_url(args)
        cloudflared = find_cloudflared()
        verify_local(local_url, insecure=args.no_tls_verify)

        key = quick_key(local_url)
        existing = reuse_or_clear(key)
        if existing and existing.get("local_url") == local_url:
            print_json({"ok": True, "reused": True, **public_view(existing), "stop_command": stop_hint(existing)})
            return 0
        if existing:
            stop_pid(existing["pid"])
            drop_state(key)

        state_dir().mkdir(parents=True, exist_ok=True)
        empty_config = state_dir() / "empty.yml"
        empty_config.write_text("", encoding="utf-8")
        cmd = [cloudflared, "--config", str(empty_config), "tunnel", "--no-autoupdate", "--protocol", args.protocol]
        if args.no_tls_verify:
            cmd.append("--no-tls-verify")
        cmd.extend(["--url", local_url])

        state = {
            "key": key,
            "mode": "quick",
            "local_url": local_url,
            "public_url": None,
            "marker": local_url,
            "log_file": str(log_path(key)),
        }
        ok, public_url, error, attempt = start_with_retries(cmd, state, args.timeout, want_public_url=True)
        if not ok:
            return fail(error, log_file=str(log_path(key)))
        state["public_url"] = public_url
        save_state(state)
        print_json({"ok": True, "attempt": attempt, **public_view(state), "stop_command": stop_hint(state)})
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_status(_: argparse.Namespace) -> int:
    tunnels = []
    for state in all_states():
        entry = public_view(state)
        entry["running"] = is_tunnel_alive(state)
        if not entry["running"]:
            entry["note"] = "process is gone, so this public URL no longer works; entry removed"
            drop_state(state["key"])
        tunnels.append(entry)
    print_json(
        {
            "ok": True,
            "running": any(entry["running"] for entry in tunnels),
            "state_dir": str(state_dir()),
            "tunnels": tunnels,
        }
    )
    return 0


def select_states(states: list[dict], args: argparse.Namespace) -> list[dict] | None:
    """Return the tunnels matching the selector flags, or None when no selector was given."""
    if args.name:
        key = named_key(args.name)
        return [state for state in states if state.get("key") == key]
    if args.url:
        wanted = args.url.strip().rstrip("/")
        return [
            state
            for state in states
            if wanted in {str(state.get("local_url") or "").rstrip("/"), str(state.get("public_url") or "").rstrip("/")}
        ]
    if args.port:
        return [state for state in states if url_port(state.get("local_url")) == args.port]
    return None


def cmd_stop(args: argparse.Namespace) -> int:
    try:
        states = all_states()
        selected = select_states(states, args)
        if selected is None:
            live = [state for state in states if is_tunnel_alive(state)]
            if len(live) > 1 and not args.all:
                return fail(
                    "several tunnels are running; pass --url, --port, --name, or --all",
                    tunnels=[public_view(state) for state in live],
                )
            selected = states
        stopped, already_gone = [], []
        for state in selected:
            if is_tunnel_alive(state):
                stop_pid(state["pid"])
                stopped.append(public_view(state))
            else:
                already_gone.append(public_view(state))
            drop_state(state["key"])
        result: dict[str, object] = {"ok": True, "stopped": stopped}
        if already_gone:
            result["already_gone"] = already_gone
        if not stopped:
            result["message"] = "no running tunnel matched"
        print_json(result)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_verify(args: argparse.Namespace) -> int:
    deadline = time.time() + max(args.wait, 0)
    attempts = 0
    outcome: dict[str, object] = {}
    while True:
        attempts += 1
        retryable = True
        try:
            status, body, dns = fetch(args.url, timeout=args.timeout)
            contains = (args.contains in body) if args.contains else None
            reachable = status is not None and 200 <= status < 500
            outcome = {"status": status, "contains": contains, "dns": dns}
            if dns != "system":
                outcome["hint"] = (
                    "the system DNS has not picked up this hostname yet (resolved through DNS-over-HTTPS); "
                    "browsers on this network may need a minute or two"
                )
            if reachable and contains is not False:
                print_json({"ok": True, "url": args.url, "attempts": attempts, **outcome})
                return 0
            if reachable:
                outcome["error"] = "response does not contain the expected text"
                retryable = False
            else:
                outcome["error"] = f"HTTP {status}"
        except Exception as exc:  # noqa: BLE001 - CLI diagnostics
            outcome = {"error": str(exc)}
        if not retryable or time.time() + VERIFY_INTERVAL > deadline:
            break
        time.sleep(VERIFY_INTERVAL)
    print_json({"ok": False, "url": args.url, "attempts": attempts, **outcome})
    return 1


def named_config_path(name: str) -> Path:
    return state_dir() / f"{named_key(name)}.yml"


def write_named_config(args: argparse.Namespace) -> tuple[Path, str]:
    local_url = normalize_url(args)
    hostname = (args.hostname or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9*]([A-Za-z0-9.*-]*[A-Za-z0-9])?", hostname) or "." not in hostname:
        raise RuntimeError("provide a valid --hostname such as app.example.com (no scheme, no path)")
    config_path = named_config_path(args.name)
    lines = [f"tunnel: {json.dumps(args.name)}"]
    if args.credentials_file:
        lines.append(f"credentials-file: {json.dumps(str(Path(args.credentials_file).expanduser()))}")
    lines.extend(
        [
            "",
            "ingress:",
            f"  - hostname: {json.dumps(hostname)}",
            f"    service: {local_url}",
            "  - service: http_status:404",
            "",
        ]
    )
    state_dir().mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path, local_url


def named_run_command(config_path: Path, name: str, protocol: str = "http2") -> list[str]:
    return ["cloudflared", "--config", str(config_path), "tunnel", "--no-autoupdate", "--protocol", protocol, "run", name]


def cmd_named_config(args: argparse.Namespace) -> int:
    try:
        config_path, local_url = write_named_config(args)
        print_json(
            {
                "ok": True,
                "mode": "named",
                "config": str(config_path),
                "hostname": args.hostname,
                "local_url": local_url,
                "start_command": helper_command("named-run", "--name", args.name),
                "foreground_command": quote_command(named_run_command(config_path, args.name)),
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def read_named_config(config_path: Path) -> tuple[str | None, str | None]:
    text = config_path.read_text(encoding="utf-8")
    hostname = re.search(r"^\s*-\s*hostname:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
    service = re.search(r"^\s*service:\s*(\S+)\s*$", text, re.MULTILINE)
    return (hostname.group(1) if hostname else None, service.group(1) if service else None)


def cmd_named_run(args: argparse.Namespace) -> int:
    try:
        cloudflared = find_cloudflared()
        key = named_key(args.name)
        if args.hostname:
            config_path, _ = write_named_config(args)
        else:
            config_path = named_config_path(args.name)
            if not config_path.exists():
                raise RuntimeError(f"no config for tunnel {args.name!r}; pass --hostname and --url, or run named-config first")
        hostname, local_url = read_named_config(config_path)
        if local_url and local_url.startswith(("http://", "https://")):
            verify_local(local_url, insecure=True)

        existing = reuse_or_clear(key)
        if existing:
            if not args.hostname:
                print_json({"ok": True, "reused": True, **public_view(existing), "stop_command": stop_hint(existing)})
                return 0
            # The config was just rewritten; restart so it takes effect.
            stop_pid(existing["pid"])
            drop_state(key)

        cmd = [cloudflared, *named_run_command(config_path, args.name, args.protocol)[1:]]
        state = {
            "key": key,
            "mode": "named",
            "name": args.name,
            "local_url": local_url,
            "public_url": f"https://{hostname}" if hostname else None,
            "marker": str(config_path),
            "config": str(config_path),
            "log_file": str(log_path(key)),
        }
        ok, _, error, attempt = start_with_retries(cmd, state, args.timeout, want_public_url=False)
        if not ok:
            return fail(error, log_file=str(log_path(key)))
        print_json({"ok": True, "attempt": attempt, **public_view(state), "stop_command": stop_hint(state)})
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


# ------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloudflare Tunnel skill helper")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check cloudflared and show the state directory")
    check.set_defaults(func=cmd_check)

    quick = sub.add_parser("quick", help="start a quick tunnel in the background")
    quick.add_argument("--url", help="local URL, for example http://localhost:3000")
    quick.add_argument("--port", type=int, help="local HTTP port")
    quick.add_argument("--protocol", default="http2", choices=["http2", "quic"], help="cloudflared protocol")
    quick.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    quick.add_argument("--no-tls-verify", action="store_true", help="allow self-signed local HTTPS")
    quick.set_defaults(func=cmd_quick)

    status = sub.add_parser("status", help="list tunnels started by this helper")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="stop tunnels started by this helper")
    stop.add_argument("--url", help="local or public URL of the tunnel to stop")
    stop.add_argument("--port", type=int, help="local port of the tunnel to stop")
    stop.add_argument("--name", help="named tunnel to stop")
    stop.add_argument("--all", action="store_true", help="stop every tunnel when several are running")
    stop.set_defaults(func=cmd_stop)

    verify = sub.add_parser("verify", help="verify a public URL, retrying while DNS propagates")
    verify.add_argument("--url", required=True)
    verify.add_argument("--contains", help="optional response substring to require")
    verify.add_argument("--timeout", type=int, default=20, help="seconds per request")
    verify.add_argument("--wait", type=int, default=90, help="keep retrying for this many seconds (0 = try once)")
    verify.set_defaults(func=cmd_verify)

    def add_named_arguments(target: argparse.ArgumentParser, required: bool) -> None:
        target.add_argument("--name", required=True, help="tunnel name")
        target.add_argument("--hostname", required=required, help="public hostname, for example app.example.com")
        target.add_argument("--url", help="local URL")
        target.add_argument("--port", type=int, help="local HTTP port")
        target.add_argument("--credentials-file", help="optional Cloudflare tunnel credentials JSON")

    named = sub.add_parser("named-config", help="write a named tunnel config")
    add_named_arguments(named, required=True)
    named.set_defaults(func=cmd_named_config)

    named_run = sub.add_parser("named-run", help="run a named tunnel in the background")
    add_named_arguments(named_run, required=False)
    named_run.add_argument("--protocol", default="http2", choices=["http2", "quic", "auto"], help="cloudflared protocol")
    named_run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    named_run.set_defaults(func=cmd_named_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
