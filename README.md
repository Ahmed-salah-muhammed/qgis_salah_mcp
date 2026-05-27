# QGIS Salah MCP

**Control QGIS with natural language through Claude AI.**

QGIS Salah MCP is a bridge between [Claude AI](https://claude.ai) and [QGIS](https://qgis.org) using the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). Instead of clicking through QGIS menus, you simply describe what you want to Claude — and Claude calls the right GIS operations for you.

---

## What Does It Do?

This project connects two things:

| Part | What it is | Where it runs |
|---|---|---|
| **QGIS Plugin** | A TCP socket server that executes GIS commands | Inside QGIS |
| **MCP Server** | A FastMCP server that Claude talks to | As a background process |

When you ask Claude *"load my shapefile and buffer it by 500 meters"*, Claude:
1. Calls `load_layer` → your shapefile appears in QGIS
2. Calls `run_buffer_analysis` with distance=500 → a buffer layer appears in QGIS

All in one sentence, no clicking required.

---

## Architecture

```
You (natural language)
        │
        ▼
  Claude Desktop
        │  stdio — Claude spawns this process via uvx
        ▼
 MCP Server (server.py)          ← plain Python, no QGIS needed
        │  TCP socket :8765
        ▼
 QGIS Plugin (socket server)     ← runs inside QGIS
        │
        ▼
 QGIS 3.34+ / 4.x               ← executes real PyQGIS operations
```

**Protocol:** Every message is length-prefixed — a 4-byte big-endian integer followed by a UTF-8 JSON body. This is the same protocol used by [jjsantos01/qgis_mcp](https://github.com/jjsantos01/qgis_mcp).

---

## Installation

### 1 — Install the QGIS Plugin

**Option A: From the QGIS Plugin Repository** *(once approved)*
- QGIS → Plugins → Manage and Install Plugins → Search **"QGIS Salah MCP"** → Install

**Option B: Manual install from this repo**
```bash
# Linux
cp -r qgis_salah_mcp_plugin \
  ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/

# Windows  %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\
# macOS    ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/
```
Then in QGIS: **Plugins → Manage and Install Plugins → Installed → enable "QGIS Salah MCP"**

### 2 — Start the Socket Server

A dock widget appears on the right side of QGIS.
Click **Start Server**. The default port is **8765**.

You should see in the QGIS log panel (View → Panels → Log Messages):
```
QgisSalahMCP: Server started on localhost:8765
```

### 3 — Configure Claude Desktop

Edit `claude_desktop_config.json`:
- Linux: `~/.config/Claude/claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

**Option A — local venv (recommended for local development)**

First create the venv and install the bridge server:
```bash
cd /path/to/this/repo
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "qgis_salah": {
      "command": "/path/to/this/repo/.venv/bin/qgis-salah-mcp",
      "args": []
    }
  }
}
```

**Option B — uvx (no manual install needed)**
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

> **Important:** Always point `command` at the installed `qgis-salah-mcp` entry-point (inside the venv or via `uvx`). Do **not** pass `server.py` directly to Python — the file uses package-relative imports that only work when the package is properly installed.

Restart Claude Desktop. The QGIS tools will appear in Claude's tool panel.

---

## Available Tools (33)

### Connection & Info

| Tool | What it does |
|---|---|
| `ping` | Check if the QGIS plugin is reachable |
| `get_qgis_info` | Get QGIS version and plugin details |

**Example:**
> *"Are you connected to QGIS?"*
> Claude calls `ping` → returns `"pong"` → confirms connection is live.

---

### Project Management

| Tool | What it does |
|---|---|
| `get_project_info` | Get the current project title, file path, CRS, and layer count |
| `load_project` | Open a `.qgs` or `.qgz` project file |
| `save_project` | Save the current project (optionally to a new path) |

**Example:**
> *"Open the project at /home/user/maps/cairo.qgz"*
> Claude calls `load_project(path="/home/user/maps/cairo.qgz")`

---

### Layer Management

| Tool | Parameters | What it does |
|---|---|---|
| `get_layers` | — | List all loaded layers with name, type, CRS, visibility |
| `load_layer` | `path`, `name?` | Load a vector file (.shp, .gpkg, .geojson, …) |
| `load_raster_layer` | `path`, `name?` | Load a raster file (.tif, .img, .asc, …) |
| `remove_layer` | `layer_name` | Remove a layer from the project |
| `rename_layer` | `layer_name`, `new_name` | Rename a layer |
| `set_layer_visibility` | `layer_name`, `visible` | Show or hide a layer |
| `zoom_to_layer` | `layer_name` | Zoom the map canvas to a layer's extent |
| `get_layer_summary` | `layer_name` | Get feature count, field names, geometry type, CRS |

**Example:**
> *"Load the file /data/egypt_roads.shp and zoom to it"*
> Claude calls `load_layer` then `zoom_to_layer`.

---

### Features & Attributes

| Tool | Parameters | What it does |
|---|---|---|
| `get_layer_features` | `layer_name`, `limit?` | Return feature attribute values (default: first 10) |
| `select_by_expression` | `layer_name`, `expression` | Select features matching a QGIS expression |
| `add_field` | `layer_name`, `field_name`, `field_type?` | Add a new attribute field (string/int/double/date) |
| `calculate_field_expression` | `layer_name`, `field`, `expression` | Update a field's values using a QGIS expression (in place) |
| `field_statistics` | `layer_name`, `field_name` | Get count, sum, mean, median, std dev, min, max |

**Example:**
> *"Show me the first 5 features of the population layer"*
> Claude calls `get_layer_features(layer_name="population", limit=5)`.

> *"Select all cities where population > 1,000,000"*
> Claude calls `select_by_expression(expression='"population" > 1000000')`.

---

### Symbology

| Tool | Parameters | What it does |
|---|---|---|
| `apply_categorized_symbology` | `layer_name`, `field_name` | Style each unique field value with a distinct colour |
| `apply_graduated_symbology` | `layer_name`, `field_name`, `classes?`, `color_ramp?` | Choropleth map with equal-interval classification |
| `set_layer_opacity` | `layer_name`, `opacity` | Set transparency (0.0 = invisible, 1.0 = fully visible) |

**Example:**
> *"Colour the governorates layer by 'region' field"*
> Claude calls `apply_categorized_symbology(layer_name="governorates", field_name="region")`.

> *"Make a population density choropleth with 7 classes using a red-to-blue ramp"*
> Claude calls `apply_graduated_symbology(layer_name="...", field_name="density", classes=7, color_ramp="RdBu")`.

---

### Spatial Analysis

| Tool | Parameters | What it does |
|---|---|---|
| `run_buffer_analysis` | `layer_name`, `distance`, `output_name?` | Buffer features by a distance (in the layer's CRS units) |
| `clip_layer` | `layer_name`, `mask_layer`, `output_name?` | Clip a layer to the boundary of another |
| `spatial_join` | `target_layer`, `join_layer`, `output_name?` | Join attributes by spatial intersection (one-to-one) |
| `dissolve_layer` | `layer_name`, `field?`, `output_name?` | Merge features, optionally grouped by a field |
| `merge_layers` | `layer_names`, `output_name?` | Combine multiple layers of the same geometry type |
| `reproject_layer` | `layer_name`, `target_crs`, `output_name?` | Reproject to a different CRS (e.g. `EPSG:4326`) |
| `repair_layer_geometries` | `layer_name` | Fix invalid geometries |
| `extract_by_expression` | `layer_name`, `expression`, `output_name?` | Extract matching features into a new layer |

**Example:**
> *"Buffer the hospitals layer by 2 kilometres"*
> Claude calls `run_buffer_analysis(layer_name="hospitals", distance=2000)`.

> *"Clip the roads layer to the Cairo boundary"*
> Claude calls `clip_layer(layer_name="roads", mask_layer="cairo_boundary")`.

> *"Reproject the layer to WGS84"*
> Claude calls `reproject_layer(layer_name="...", target_crs="EPSG:4326")`.

---

### Export

| Tool | Parameters | What it does |
|---|---|---|
| `save_layer_to_file` | `layer_name`, `output_path` | Export layer to file (format from extension: .shp, .gpkg, .geojson …) |
| `export_map_to_image` | `output_path` | Export the current map view to PNG/JPG |
| `export_to_pdf` | `output_path` | Export the current map view to PDF |

**Example:**
> *"Save the buffer result as a GeoPackage at /output/buffer.gpkg"*
> Claude calls `save_layer_to_file(layer_name="Buffer_Result", output_path="/output/buffer.gpkg")`.

---

### Code Execution

| Tool | Parameters | What it does |
|---|---|---|
| `execute_code` | `code` | Run arbitrary PyQGIS code inside QGIS. `iface` and `QgsProject` are pre-imported |

**Example:**
> *"Print the names of all loaded layers"*
> Claude calls:
> ```python
> execute_code(code="""
> for name, layer in QgsProject.instance().mapLayers().items():
>     print(layer.name())
> """)
> ```

This tool is the escape hatch — anything not covered by the other tools can be done with raw PyQGIS code.

---

## Example Workflow

Here is a full GIS analysis done entirely through Claude:

> **You:** "Load /data/cairo_districts.gpkg, show me a summary, then make a choropleth by population density with 5 classes, buffer the high-density districts by 500m, and export the map as a PDF to /output/cairo_analysis.pdf"

Claude will automatically chain:
1. `load_layer` → loads the file
2. `get_layer_summary` → reports geometry type, fields, CRS
3. `apply_graduated_symbology` → colours the map by population density
4. `select_by_expression` → selects high-density districts
5. `extract_by_expression` → extracts them to a new layer
6. `run_buffer_analysis` → creates 500m buffers
7. `export_to_pdf` → saves the final map

---

## Adding New Tools

1. Add a `_cmd_<name>(self, **kwargs)` method in `qgis_salah_mcp_plugin/qgis_salah_mcp_plugin.py`
2. Add a matching `@mcp.tool()` function in `src/qgis_salah_mcp/server.py` that calls `_run("<name>", ...)`

That's all. The socket server auto-routes any command whose name matches a `_cmd_*` method.

---

## Requirements

- QGIS 3.34 or later (or QGIS 4.x)
- Python 3.10+
- `uv` package manager → [install](https://docs.astral.sh/uv/getting-started/installation/)
- Claude Desktop with an Anthropic account

---

## Troubleshooting

### Bridge server crashes immediately — `TypeError: unexpected keyword argument 'description'`

**Cause:** `FastMCP.__init__()` in `mcp` ≥ 1.3 uses `instructions=`, not `description=`.

**Fix:** In `src/qgis_salah_mcp/server.py`, the `FastMCP(...)` call must use `instructions=`:

```python
# Wrong
mcp = FastMCP("QGIS_Salah_MCP", description="...", lifespan=lifespan)

# Correct
mcp = FastMCP("QGIS_Salah_MCP", instructions="...", lifespan=lifespan)
```

This was fixed in the current codebase.

---

### Claude Desktop shows "server disconnected" or tools never appear

**Cause:** The config JSON was pointing Python at `server.py` as a plain script:

```json
// Wrong — package imports fail when run as a plain script
"command": "/path/to/.venv/bin/python",
"args": ["/path/to/src/qgis_salah_mcp/server.py"]
```

`server.py` uses `from qgis_salah_mcp.server import main` internally (via the entry-point wrapper), which only resolves when the package is installed — not when the file is executed as a loose script.

**Fix:** Use the installed entry-point:

```json
// Correct
"command": "/path/to/.venv/bin/qgis-salah-mcp",
"args": []
```

---

### Tools connect but every call returns "Cannot reach QGIS"

The bridge server started successfully but the QGIS plugin socket is not running.

1. Open QGIS
2. Enable the **QGIS Salah MCP** plugin (Plugins → Manage and Install Plugins)
3. Click **Start Server** in the dock widget (default port: 8765)
4. Check the QGIS Log Messages panel — you should see `Server started on localhost:8765`

---

## Author

**Ahmed Salah Muhammed**
[github.com/Ahmed-salah-muhammed](https://github.com/Ahmed-salah-muhammed)
