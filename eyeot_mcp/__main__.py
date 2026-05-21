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

# OAuth access tokens live ~1 h. Refresh this many seconds BEFORE expiry so a
# long-lived bridge process never forwards a stale token to the server.
TOKEN_REFRESH_SKEW_S = 60


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


def _refresh_oauth_token(cfg: dict) -> bool:
    """Exchange the saved refresh_token for a fresh access+refresh pair.

    The server rotates the refresh token on every use (replay detection),
    so we MUST persist the new one — the previous refresh token is dead the
    moment this call succeeds. `cfg` is mutated in place and written back to
    disk on success. Returns True on success, False otherwise (caller falls
    back to the stale token, then a 401 → `eyeot-mcp login` prompt).
    """
    refresh = cfg.get("refresh_token")
    if not refresh:
        return False
    base = cfg.get("base_url", DEFAULT_BASE_URL)
    client_id = cfg.get("client_id", DEFAULT_CLIENT_ID)
    try:
        status, body = _http_post(
            f"{base}/api/v1/oauth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        print(f"eyeot-mcp: token refresh transport error: {e}", file=sys.stderr)
        return False

    if status == 200 and isinstance(body, dict) and body.get("access_token"):
        cfg["access_token"] = body["access_token"]
        # Rotation — the server returns a NEW refresh token; the old one is
        # now revoked. Persist it or the next refresh fails.
        if body.get("refresh_token"):
            cfg["refresh_token"] = body["refresh_token"]
        cfg["expires_at"] = int(time.time() + body.get("expires_in", 3600))
        _save_config(cfg)
        print("eyeot-mcp: OAuth token refreshed.", file=sys.stderr)
        return True

    print(
        f"eyeot-mcp: token refresh failed (HTTP {status}) — "
        "run `eyeot-mcp login` to re-authenticate.",
        file=sys.stderr,
    )
    return False


def cmd_proxy(args: argparse.Namespace) -> int:
    """Run the stdio bridge — JSON-RPC messages on stdin → HTTP → stdout.

    Auth resolution (precedence) :
      1. `--token`          : static API key / token, no refresh
      2. saved OAuth config : access token, refreshed automatically
      3. `EYEOT_TOKEN` env  : static API key / token, no refresh

    On the OAuth path the access token is refreshed automatically — proactively
    just before it expires, and reactively if the server answers 401 — so a
    bridge process started once keeps working for days without the user having
    to re-run `eyeot-mcp login`.
    """
    base = args.base_url
    rpc_url = f"{base}/api/v1/mcp"

    cfg = _load_config()
    oauth_mode = not args.token and bool(cfg.get("access_token"))
    static_token = args.token or (
        None if oauth_mode else os.environ.get("EYEOT_TOKEN")
    )

    if not oauth_mode and not static_token:
        print(
            "ERROR: no credentials. Run `eyeot-mcp login` or pass --token / set EYEOT_TOKEN.",
            file=sys.stderr,
        )
        return 1

    def auth_header() -> str:
        """Current Bearer header — refreshes the OAuth token proactively."""
        if not oauth_mode:
            return f"Bearer {static_token}"
        expires_at = cfg.get("expires_at", 0)
        if expires_at and time.time() >= expires_at - TOKEN_REFRESH_SKEW_S:
            _refresh_oauth_token(cfg)  # best-effort; on failure keep stale token
        return f"Bearer {cfg.get('access_token', '')}"

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
            status, body = _http_post(
                rpc_url, payload, headers={"Authorization": auth_header()}
            )
            # Reactive refresh — token rejected mid-session (expired early or
            # rotated elsewhere). Refresh once and replay the same request.
            if status == 401 and oauth_mode and _refresh_oauth_token(cfg):
                status, body = _http_post(
                    rpc_url,
                    payload,
                    headers={"Authorization": f"Bearer {cfg.get('access_token', '')}"},
                )
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
