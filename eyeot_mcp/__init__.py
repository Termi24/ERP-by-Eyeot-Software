"""eyeot-mcp — stdio bridge to the eyeot ERP MCP server.

ERP by Eyeot Software — https://erp.eyeot.fr

Usage in Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`
or Windows equivalent) :

    {
      "mcpServers": {
        "eyeot": {
          "command": "eyeot-mcp",
          "args": ["--token", "eyk_xxx_xxx"]
        }
      }
    }

Or with OAuth (after `eyeot-mcp login`) :

    {
      "mcpServers": {
        "eyeot": {
          "command": "eyeot-mcp"
        }
      }
    }

The bridge reads JSON-RPC 2.0 messages on stdin, forwards them to
`https://erp.eyeot.fr/api/v1/mcp` over HTTP, and writes the responses to
stdout — enabling Claude Desktop to talk to a remote MCP server through
the standard stdio transport.
"""

__version__ = "1.1.0"
