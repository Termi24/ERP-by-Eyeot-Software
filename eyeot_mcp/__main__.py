"""eyeot-mcp CLI : stdio ↔ HTTP bridge for Claude Desktop / Cursor.

Subcommands :
- `eyeot-mcp` (no args)               : start the stdio bridge using saved credentials
- `eyeot-mcp --token <eyk>`           : use a pre-issued API key
- `eyeot-mcp login`                   : OAuth Device Authorization Grant flow
- `eyeot-mcp logout`                  : delete saved credentials

Configuration is stored in `$HOME/.eyeot-mcp/config.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

DEFAULT_BASE_URL = "https://erp.eyeot.fr"
CONFIG_DIR = Path.home() / ".eyeot-mcp"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_CLIENT_ID = "eyeot-cli"  # public client registered server-side


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)  # POSIX only — best effort on Windows
    except Exception:
        pass


def _http_post(url: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


def _http_get(url: str, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


def cmd_login(args: argparse.Namespace) -> int:
    """OAuth Device Authorization Grant — opens a browser, polls until consent."""
    base = args.base_url
    client_id = args.client_id

    print("eyeot-mcp login — initiating device authorization flow...", file=sys.stderr)

    status, body = _http_post(
        f"{base}/api/v1/oauth/device_authorization",
        {"client_id": client_id, "scope": args.scope},
    )
    if status != 200:
        print(f"Failed to start device auth: HTTP {status} {body}", file=sys.stderr)
        return 1

    user_code = body["user_code"]
    verification_uri = body["verification_uri"]
    verification_uri_complete = body.get("verification_uri_complete", verification_uri)
    device_code = body["device_code"]
    interval = body.get("interval", 5)
    expires_in = body.get("expires_in", 600)

    print("\n┌──────────────────────────────────────────────────┐", file=sys.stderr)
    print("│  EYEOT ERP — Device authorization                │", file=sys.stderr)
    print("├──────────────────────────────────────────────────┤", file=sys.stderr)
    print(f"│  1. Ouvre  : {verification_uri}", file=sys.stderr)
    print(f"│  2. Code   : {user_code}", file=sys.stderr)
    print(f"│  Direct    : {verification_uri_complete}", file=sys.stderr)
    print("└──────────────────────────────────────────────────┘\n", file=sys.stderr)

    # Try to open the browser automatically
    try:
        import webbrowser
        webbrowser.open(verification_uri_complete)
    except Exception:
        pass

    deadline = time.time() + expires_in
    print(f"Polling every {interval}s (timeout in {expires_in}s)...", file=sys.stderr)

    while time.time() < deadline:
        time.sleep(interval)
        status, body = _http_post(f"{base}/api/v1/oauth/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id,
        })
        if status == 200 and "access_token" in body:
            cfg = _load_config()
            cfg["base_url"] = base
            cfg["client_id"] = client_id
            cfg["access_token"] = body["access_token"]
            cfg["refresh_token"] = body.get("refresh_token")
            cfg["expires_at"] = int(time.time() + body.get("expires_in", 3600))
            _save_config(cfg)
            print("\n✓ eyeot-mcp authenticated. You may now configure Claude Desktop.",
                  file=sys.stderr)
            return 0
        # 422 + oauth_error in errors means pending / denied / expired
        err_block = body.get("errors", {}) if isinstance(body, dict) else {}
        oauth_err = err_block.get("oauth_error") if isinstance(err_block, dict) else None
        if oauth_err == "access_denied":
            print("\n✗ Access denied by user.", file=sys.stderr)
            return 2
        if oauth_err == "expired_token":
            print("\n✗ Code expired. Re-run `eyeot-mcp login`.", file=sys.stderr)
            return 3
        if oauth_err == "authorization_pending":
            continue  # keep polling
        # Other transient errors — retry quietly
        continue

    print("\n✗ Timeout waiting for user authorization.", file=sys.stderr)
    return 4


def cmd_logout(args: argparse.Namespace) -> int:
    if not CONFIG_PATH.exists():
        print("Already logged out.", file=sys.stderr)
        return 0
    cfg = _load_config()
    refresh = cfg.get("refresh_token")
    base = cfg.get("base_url", DEFAULT_BASE_URL)
    if refresh:
        # Best-effort revocation
        _http_post(f"{base}/api/v1/oauth/revoke", {"token": refresh})
    try:
        CONFIG_PATH.unlink()
        print("✓ Logged out.", file=sys.stderr)
    except Exception as e:
        print(f"Failed to remove config: {e}", file=sys.stderr)
        return 1
    return 0


def _resolve_auth_header(args: argparse.Namespace) -> str | None:
    """Return the Authorization header value to use for proxied calls."""
    if args.token:
        return f"Bearer {args.token}"
    cfg = _load_config()
    if cfg.get("access_token"):
        # TODO: refresh if expired — V2 (V1 emits a 401 and the user re-logs)
        return f"Bearer {cfg['access_token']}"
    env_tok = os.environ.get("EYEOT_TOKEN")
    if env_tok:
        return f"Bearer {env_tok}"
    return None


def cmd_proxy(args: argparse.Namespace) -> int:
    """Run the stdio bridge — JSON-RPC messages on stdin → HTTP → stdout."""
    base = args.base_url
    auth_header = _resolve_auth_header(args)
    if auth_header is None:
        print(
            "ERROR: no credentials. Run `eyeot-mcp login` or pass --token / set EYEOT_TOKEN.",
            file=sys.stderr,
        )
        return 1

    rpc_url = f"{base}/api/v1/mcp"
    # Read line-delimited JSON from stdin (the MCP framing for stdio)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        try:
            status, body = _http_post(rpc_url, payload, headers={"Authorization": auth_header})
        except Exception as e:
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {
                    "code": -32603,
                    "message": f"Bridge transport error: {type(e).__name__}: {e}",
                },
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        if status >= 400 and not body:
            body = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {"code": -32000, "message": f"HTTP {status} from server"},
            }

        sys.stdout.write(json.dumps(body) + "\n")
        sys.stdout.flush()

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eyeot-mcp",
        description="stdio <-> HTTP MCP bridge for the eyeot ERP",
    )
    parser.add_argument("--base-url", default=os.environ.get("EYEOT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", help="API key (eyk_...) or OAuth access token (eya_...)")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                        help="OAuth client_id used for `login` (default: eyeot-cli)")
    parser.add_argument("--scope", default="admin",
                        help="OAuth scope requested at login (default: admin)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("login", help="OAuth Device Authorization Grant (browser-based)")
    sub.add_parser("logout", help="Revoke and remove stored credentials")
    # Default action (no subcommand) = stdio proxy

    args = parser.parse_args(argv)

    if args.cmd == "login":
        return cmd_login(args)
    if args.cmd == "logout":
        return cmd_logout(args)
    return cmd_proxy(args)


if __name__ == "__main__":
    sys.exit(main())
