# QGIS Salah MCP

Control QGIS with Claude through the Model Context Protocol. Built on the same architecture as [jjsantos01/qgis_mcp](https://github.com/jjsantos01/qgis_mcp).

## Architecture

```
Claude Desktop
     │  stdio
     ▼
src/qgis_salah_mcp/server.py   ← FastMCP server (plain Python, no QGIS needed)
     │  TCP :9876
     ▼
qgis_salah_mcp_plugin/         ← QGIS plugin (socket server running inside QGIS)
     │
     ▼
QGIS (executes PyQGIS commands)
```

## Installation

### 1. Install the QGIS Plugin

Copy the plugin folder to your QGIS plugins directory:

```bash
# Linux
cp -r qgis_salah_mcp_plugin ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/

# Windows
# Copy to: %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\

# macOS
# Copy to: ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/
```

Then in QGIS: **Plugins → Manage and Install Plugins → Installed → enable "QGIS Salah MCP"**

### 2. Start the Socket Server in QGIS

A dock widget appears on the right side of QGIS. Click **Start Server** (default port: 9876).

### 3. Configure Claude Desktop

Edit `claude_desktop_config.json`:

- **Linux:** `~/.config/Claude/claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "qgis_salah": {
      "command": "uvx",
      "args": ["--from", "/path/to/this/repo", "qgis-salah-mcp"]
    }
  }
}
```

Or if you prefer `uv`:

```json
{
  "mcpServers": {
    "qgis_salah": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/this/repo", "qgis-salah-mcp"]
    }
  }
}
```

Restart Claude Desktop. You should see the QGIS tools available.

## Available Tools

| Tool | Description |
|---|---|
| `ping` | Check connection to the QGIS plugin |
| `get_qgis_info` | Get QGIS version info |
| `load_layer` | Load a vector file into the project |
| `zoom_to_layer` | Zoom map canvas to a layer's extent |
| `get_layer_summary` | Feature count, fields, geometry type, CRS |
| `apply_categorized_symbology` | Style a layer by unique field values |
| `spatial_join` | Join attributes between two layers by location |
| `run_buffer_analysis` | Buffer features by a given distance |
| `repair_layer_geometries` | Fix invalid geometries |
| `calculate_field_expression` | Update a field using a QGIS expression |
| `export_to_pdf` | Export the current map view to PDF |
| `execute_code` | Run arbitrary PyQGIS code inside QGIS |

## Adding New Tools

1. Add a command handler `_cmd_<name>(self, params)` in `qgis_salah_mcp_plugin.py` and register its name in `_HANDLERS`.
2. Add the matching `@mcp.tool()` function in `src/qgis_salah_mcp/server.py` that calls `_run("<name>", ...)`.

## Requirements

- QGIS 3.0+
- Python 3.10+
- `mcp[cli] >= 1.3.0`
- `uv` package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
