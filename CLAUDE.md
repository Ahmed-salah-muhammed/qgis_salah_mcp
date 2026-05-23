# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a single-file MCP (Model Context Protocol) server that exposes QGIS GIS operations as tools consumable by Claude or other MCP clients. The server is named `QGIS_SAlah_Toolbox` and is implemented using `FastMCP`.

## Running the Server

The server **must** run inside a QGIS Python environment because it depends on `qgis.core`, `qgis.analysis`, and `qgis.utils.iface` — which are only available when QGIS is loaded. Two options:

1. **From the QGIS Python Console** — paste or `exec(open('qgis_mcp_salah.py').read())`
2. **As a standalone script with QGIS Python** — using `python-qgis` or a QGIS-bundled interpreter

```bash
source .venv/bin/activate
python qgis_mcp_salah.py
```

The `mcp.run()` call at the bottom starts the MCP server over **stdio** transport.

## Claude Desktop Integration

To register this server in Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "qgis": {
      "command": "/path/to/python-qgis",
      "args": ["/run/media/vhmed/ITI/MCP/qgis_mcp_salah.py"]
    }
  }
}
```

Replace `/path/to/python-qgis` with the QGIS-bundled Python interpreter path.

## Architecture

**Single entry point:** `qgis_mcp_salah.py`

- **`FastMCP` instance** (`mcp`) — created at module level; every `@mcp.tool()` decorator registers a tool.
- **`setup_qgis_processing()`** — called at import time; initializes QGIS Processing framework and native algorithms. If it fails (e.g. outside QGIS), the error is logged but execution continues.
- **`get_layer_by_name(name)`** — all tools that accept a `layer_name` resolve the layer through this helper via `QgsProject.instance().mapLayersByName()`.
- **`validate_path(path)`** — used by export tools to `os.makedirs` the output directory if missing.

### GUI-dependent tools

These tools call `iface` and **require a running QGIS GUI session** (not headless):

- `zoom_to_layer` — calls `iface.setActiveLayer()` / `iface.zoomToActiveLayer()`
- `apply_categorized_symbology` — calls `iface.layerTreeView().refreshLayerSymbology()`
- `export_to_pdf` — reads `iface.mapCanvas().extent()`

### Non-obvious tool behaviors

- **`run_buffer_analysis`** passes `layer_name` (the raw string) directly as `'INPUT'` to `processing.run()`, unlike `spatial_join` which resolves the layer object first. Both work, but this inconsistency means `run_buffer_analysis` bypasses the `get_layer_by_name` helper.
- **`calculate_field_expression`** uses `'OUTPUT': 'inplace'` — it modifies the existing layer in place rather than creating a new memory layer, unlike all other processing tools.
- **Spatial analysis tools** (`spatial_join`, `run_buffer_analysis`, `repair_layer_geometries`, `calculate_field_expression`) defer `import processing` to inside the function body so the module-level import doesn't fail before `setup_qgis_processing()` runs.

## Tool Categories

| Category | Tools |
|---|---|
| Layer Management | `load_layer`, `zoom_to_layer`, `get_layer_summary` |
| Symbology | `apply_categorized_symbology` |
| Spatial Analysis | `spatial_join`, `run_buffer_analysis`, `repair_layer_geometries` |
| Data Processing | `calculate_field_expression` |
| Export | `export_to_pdf` |

## Adding New Tools

Decorate a function with `@mcp.tool()`. If it uses Processing algorithms, defer `import processing` to inside the function body (not at the top of the file).

## Key Dependencies

- `mcp` 1.27.1 (`FastMCP` from `mcp.server.fastmcp`)
- `pydantic` 2.x (used internally by MCP)
- `uvicorn` / `starlette` (MCP transport layer)
- QGIS Python bindings (external — provided by QGIS installation, not in `.venv`)

Python version: **3.14** (`.venv/pyvenv.cfg`)
