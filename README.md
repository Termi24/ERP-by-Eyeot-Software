# eyeot-mcp

[![PyPI version](https://img.shields.io/pypi/v/eyeot-mcp.svg)](https://pypi.org/project/eyeot-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/eyeot-mcp.svg)](https://pypi.org/project/eyeot-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Model_Context_Protocol-2024--11--05-blueviolet)](https://modelcontextprotocol.io)

> Official stdio ↔ HTTP bridge for the **eyeot ERP** Model Context Protocol server.
> Plug Claude Desktop, Cursor, or any MCP-compatible agent into a remote eyeot
> deployment with a single command.

**ERP by Eyeot Software** — [erp.eyeot.fr](https://erp.eyeot.fr)

---

## What this does

The eyeot ERP exposes ~440 business actions (CRM, sales, stock, maintenance,
HR, finance, IT service management…) as MCP tools over HTTPS. Most local
agents (Claude Desktop, Cursor) only speak MCP over **stdio**. This package
is the missing piece :

```
Claude Desktop  ←─ JSON-RPC stdio ─→  eyeot-mcp  ←─ HTTPS POST ─→  /api/v1/mcp
```

All authentication, RBAC, audit logging, license guard, idempotency and
tenant isolation happens **server-side**. The bridge only forwards messages.

## Install

```bash
pip install eyeot-mcp
```

Python 3.10+. **No dependencies** — uses only the standard library, so it
installs instantly and works inside restricted sandboxes (Claude Desktop's
embedded Python, locked-down CI runners, etc.).

## Quick start — OAuth (recommended for humans)

```bash
eyeot-mcp login
```

The CLI prints a short code (e.g. `ABCD-WXYZ`) and opens your browser. Log
in to your eyeot ERP account, approve the consent, done. Credentials are
saved to `~/.eyeot-mcp/config.json` (mode 600).

Then add the bridge to your Claude Desktop config (`~/Library/Application
Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "eyeot": {
      "command": "eyeot-mcp"
    }
  }
}
```

Restart Claude Desktop. The eyeot ERP tools are now available in your
conversations — try **"list my last 5 invoices"** or **"create a quote for
ACME for 10 units of PROD-001"**.

## Quick start — API key (for service agents)

If your admin gave you a service API key (`eyk_xxx_xxx`), skip the OAuth flow :

```json
{
  "mcpServers": {
    "eyeot": {
      "command": "eyeot-mcp",
      "args": ["--token", "eyk_xxx_xxx"]
    }
  }
}
```

Or via environment variable (avoid storing keys in config files) :

```json
{
  "mcpServers": {
    "eyeot": {
      "command": "eyeot-mcp",
      "env": { "EYEOT_TOKEN": "eyk_xxx_xxx" }
    }
  }
}
```

## Self-hosted deployment

Override the base URL for any private eyeot deployment :

```bash
eyeot-mcp --base-url https://erp.example.com login
```

```json
{
  "mcpServers": {
    "eyeot": {
      "command": "eyeot-mcp",
      "args": ["--base-url", "https://erp.example.com"]
    }
  }
}
```

## Cursor / other MCP clients

Any client that supports stdio MCP servers works the same way. Cursor :

```json
// .cursor/mcp.json
{
  "mcpServers": {
    "eyeot": {
      "command": "eyeot-mcp"
    }
  }
}
```

## Authentication modes

| Token format | Issued to | Lifetime | Typical use |
|---|---|---|---|
| `eyk_<prefix>_<secret>` | Service account (org-wide) | Until revoked | CI/CD agents, batch jobs, server-to-server |
| `eya_<base64>` access + `eyr_*` refresh | Human user (OAuth 2.1) | 1 h / 30 d | Claude Desktop, Cursor, personal agents |

Both flow through the same `Authorization: Bearer <token>` header
server-side. The bridge does not inspect them.

## Commands

```bash
eyeot-mcp                     # start the stdio bridge using saved credentials
eyeot-mcp login               # OAuth Device Authorization Grant (opens browser)
eyeot-mcp logout              # revoke server-side and remove local credentials
eyeot-mcp --token eyk_...     # one-shot mode with an explicit API key
eyeot-mcp --base-url URL ...  # target a self-hosted eyeot deployment
```

## Security model

- **No business logic in the bridge.** The CLI is ~270 lines of standard
  library Python. Auth, RBAC, audit, license enforcement, multi-tenant
  isolation : all server-side on the eyeot ERP.
- **Credentials are stored at `~/.eyeot-mcp/config.json`** with file mode
  `0600` (POSIX). On Windows the file is in your home directory but file
  ACLs apply.
- **OAuth 2.1 with PKCE S256** for public clients. Refresh-token rotation
  with replay detection (a stolen-and-reused refresh token kills the whole
  family).
- **License guard read-only grace** : if your subscription lapses, `GET`
  tools still work (so the agent can keep you informed) but `POST` returns
  `402 Payment Required` with an `activate_url`.
- **Idempotency** : critical write operations support an `Idempotency-Key`
  header server-side. The bridge forwards it transparently when present
  in the JSON-RPC payload.

## How it works

1. Claude Desktop launches `eyeot-mcp` as a child process and exchanges
   JSON-RPC 2.0 messages over its stdin/stdout pipes.
2. For each line received on stdin, the bridge POSTs the JSON to
   `${base_url}/api/v1/mcp` with `Authorization: Bearer <token>`.
3. The HTTP response body is written back verbatim to stdout, framed as
   line-delimited JSON.
4. The server speaks MCP `2024-11-05` and auto-generates ~440 tools from
   the OpenAPI spec — `initialize`, `tools/list`, `tools/call` all work
   exactly as MCP clients expect.

## Documentation

- **AI Agent Integration Guide** : [erp.eyeot.fr/mcp](https://erp.eyeot.fr/mcp)
- **Full API reference** : [erp.eyeot.fr/api/docs](https://erp.eyeot.fr/api/docs)
- **OpenAPI spec** : [erp.eyeot.fr/api/v1/openapi.json](https://erp.eyeot.fr/api/v1/openapi.json)
- **MCP manifest** : [erp.eyeot.fr/api/v1/mcp/manifest](https://erp.eyeot.fr/api/v1/mcp/manifest)

## Versioning

- This package : [Semantic Versioning](https://semver.org). Major bumps
  may change CLI flags or the on-disk config schema.
- MCP protocol : `2024-11-05` (negotiated server-side).
- ERP API : `/api/v1` (stable). Breaking changes ship as `/api/v2`.

## License

MIT — see [LICENSE](LICENSE).

The eyeot ERP backend is a separate, proprietary product of Eyeot Software.
This bridge is open-source so anyone can audit it, fork it, package it for
their distro, or use it as a reference for building their own MCP clients.

## About Eyeot Software

**ERP by Eyeot Software** is a multi-tenant, AI-native ERP for SMBs covering
CRM, sales, stock, maintenance, IoT, projects, IT service management, HR,
finance, document management, RGPD compliance and cross-module BI. Built to
be operated by AI agents from day one : every action you can do in the UI
you can do via this MCP bridge.

- Production : [erp.eyeot.fr](https://erp.eyeot.fr)
- Contact : [contact@eyeot.fr](mailto:contact@eyeot.fr)
