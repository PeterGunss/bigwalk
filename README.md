# bigwalk
bigwalk

## Zotero MCP

This project is configured to use [zotero-mcp](https://github.com/54yyyu/zotero-mcp), a Model Context
Protocol server that lets Claude search, read, and manage your Zotero library. The server is declared
in [`.mcp.json`](.mcp.json), so Claude Code picks it up automatically when you open this project.

### Prerequisites

1. Python 3.10+, then install the server:
   ```bash
   uv tool install zotero-mcp-server     # or: pip install zotero-mcp-server
   ```
2. In Zotero 7+, go to **Settings → Advanced** and enable *"Allow other applications on this
   computer to communicate with Zotero"*.
3. Keep the Zotero desktop app running while you use the MCP tools (`.mcp.json` here uses local
   mode, `ZOTERO_LOCAL=true`, which reads straight from your local `zotero.sqlite`).
4. Optional, for writes (adding/editing items) on Zotero 10+, run once:
   ```bash
   zotero-mcp authorize-local
   ```
   and choose **Always Allow**. On older Zotero versions, writes go through the web API instead
   (see below).

### Using the web API instead (e.g. remote/headless environments)

If Zotero desktop isn't running on the same machine as Claude Code, use the web API instead of
local mode. Generate an API key at
[zotero.org/settings/security](https://www.zotero.org/settings/security#applications) and export
it in your shell before launching Claude Code:

```bash
export ZOTERO_API_KEY=your_api_key
export ZOTERO_LIBRARY_ID=your_numeric_user_id
```

Then update the `zotero` entry in `.mcp.json` to reference them instead of `ZOTERO_LOCAL`:

```json
{
  "mcpServers": {
    "zotero": {
      "command": "zotero-mcp",
      "env": {
        "ZOTERO_API_KEY": "${ZOTERO_API_KEY}",
        "ZOTERO_LIBRARY_ID": "${ZOTERO_LIBRARY_ID}"
      }
    }
  }
}
```

Full configuration reference:
[docs/configuration.md](https://github.com/54yyyu/zotero-mcp/blob/main/docs/configuration.md).
