#!/usr/bin/env python3
"""End-to-end test for the Cloudflare Tunnel skill quick mode.

Starts a temporary local HTTP site and drives tunnel_helper.py exactly like an
agent would: quick -> status -> verify -> stop. State goes to a throwaway
directory, so tunnels you already have running are left alone.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

EXPECTED = "Cloudflare Tunnel Skill Quick Mode Test OK"
HELPER = Path(__file__).resolve().parent / "tunnel_helper.py"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def helper(env: dict[str, str], *args: str) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(HELPER), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )
    print(f"$ tunnel_helper.py {' '.join(args)}\n{result.stdout.rstrip()}")
    try:
        return result.returncode, json.loads(result.stdout)
    except ValueError:
        return result.returncode, {}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cloudflare-tunnel-skill-test-"))
    site = tmp / "site"
    site.mkdir()
    (site / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>Quick Tunnel Test</title><h1>{EXPECTED}</h1>",
        encoding="utf-8",
    )
    env = {**os.environ, "CLOUDFLARE_TUNNEL_STATE_DIR": str(tmp / "state")}

    handler = functools.partial(QuietHandler, directory=str(site))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    local_url = f"http://127.0.0.1:{port}"

    failures: list[str] = []

    def expect(condition: bool, label: str) -> None:
        print(("PASS: " if condition else "FAIL: ") + label)
        if not condition:
            failures.append(label)

    try:
        code, started = helper(env, "quick", "--url", local_url)
        public_url = started.get("public_url")
        expect(code == 0 and bool(public_url), "quick returns a public URL")
        if not public_url:
            return 1

        _, again = helper(env, "quick", "--url", local_url)
        expect(again.get("reused") is True and again.get("public_url") == public_url, "second quick reuses the tunnel")

        _, status = helper(env, "status")
        expect(status.get("running") is True and len(status.get("tunnels", [])) == 1, "status lists the running tunnel")

        code, verified = helper(env, "verify", "--url", public_url, "--contains", EXPECTED, "--wait", "150")
        expect(code == 0 and verified.get("contains") is True, "public URL returns the local page")

        _, stopped = helper(env, "stop", "--port", str(port))
        expect(len(stopped.get("stopped", [])) == 1, "stop ends the tunnel")

        _, status = helper(env, "status")
        expect(status.get("running") is False and not status.get("tunnels"), "status is empty after stop")
    finally:
        helper(env, "stop", "--all")
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nRESULT: " + ("PASS" if not failures else "FAIL - " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
