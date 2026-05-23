# QGIS Salah MCP

Control QGIS with Claude through the Model Context Protocol.

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

Restart Claude Desktop. You should see the QGIS tools listed in the tools panel.

## Available Tools (33)

### Connection
| Tool | Description |
|---|---|
| `ping` | Check connection to the QGIS plugin |
| `get_qgis_info` | Get QGIS version and plugin info |

### Project
| Tool | Description |
|---|---|
| `get_project_info` | Get project title, path, CRS, and layer count |
| `load_project` | Open a `.qgs` or `.qgz` project file |
| `save_project` | Save the current project (optionally to a new path) |

### Layer Management
| Tool | Description |
|---|---|
| `get_layers` | List all loaded layers with name, type, CRS, and visibility |
| `load_layer` | Load a vector file into the project (.shp, .gpkg, .geojson, …) |
| `load_raster_layer` | Load a raster file into the project (.tif, .img, .asc, …) |
| `remove_layer` | Remove a layer from the project |
| `rename_layer` | Rename a layer |
| `set_layer_visibility` | Show or hide a layer |
| `zoom_to_layer` | Zoom the map canvas to a layer's extent |
| `get_layer_summary` | Get feature count, fields, geometry type, and CRS |

### Features & Attributes
| Tool | Description |
|---|---|
| `get_layer_features` | Retrieve feature attribute values (with optional limit) |
| `select_by_expression` | Select features matching a QGIS expression |
| `add_field` | Add a new attribute field (string, int, double, date) |
| `calculate_field_expression` | Update a field's values using a QGIS expression (in place) |
| `field_statistics` | Compute count, sum, mean, median, std dev, min, max for a field |

### Symbology
| Tool | Description |
|---|---|
| `apply_categorized_symbology` | Style a layer by unique values in a field |
| `apply_graduated_symbology` | Apply choropleth styling with a colour ramp and equal-interval classes |
| `set_layer_opacity` | Set layer transparency (0.0 – 1.0) |

### Spatial Analysis
| Tool | Description |
|---|---|
| `spatial_join` | Join attributes between two layers by spatial intersection |
| `run_buffer_analysis` | Buffer features by a given distance |
| `clip_layer` | Clip a layer to the boundary of a mask layer |
| `dissolve_layer` | Dissolve features, optionally grouped by a field |
| `merge_layers` | Merge multiple layers of the same geometry type into one |
| `reproject_layer` | Reproject a layer to a different CRS (e.g. EPSG:4326) |
| `repair_layer_geometries` | Fix invalid geometries |
| `extract_by_expression` | Extract features matching an expression into a new layer |

### Export
| Tool | Description |
|---|---|
| `save_layer_to_file` | Export a layer to a file (.shp, .gpkg, .geojson, …) |
| `export_map_to_image` | Export the current map view to an image (PNG, JPG, …) |
| `export_to_pdf` | Export the current map view to a PDF |

### Code Execution
| Tool | Description |
|---|---|
| `execute_code` | Run arbitrary PyQGIS code inside QGIS (`iface` and `QgsProject` are pre-imported) |

## Adding New Tools

1. Add a `_cmd_<name>(self, params)` method in `qgis_salah_mcp_plugin/qgis_salah_mcp_plugin.py` and register its name in `_HANDLERS`.
2. Add a matching `@mcp.tool()` function in `src/qgis_salah_mcp/server.py` that calls `_run("<name>", ...)`.

## Requirements

- QGIS 3.0+
- Python 3.10+
- `mcp[cli] >= 1.3.0`
- `uv` package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
