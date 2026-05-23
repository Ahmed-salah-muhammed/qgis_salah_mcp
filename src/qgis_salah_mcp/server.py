"""
QGIS Salah MCP Server
FastMCP server that connects Claude to QGIS via the QGIS Salah MCP plugin TCP socket.
"""

import json
import logging
import socket
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("QgisSalahMCP")


# ---------------------------------------------------------------------------
# Socket client
# ---------------------------------------------------------------------------

class QgisSalahClient:
    """TCP client that talks to the QGIS Salah MCP plugin socket server."""

    def __init__(self, host: str = "localhost", port: int = 9876):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None

    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.sock = None
            return False

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_command(self, command_type: str, params: dict | None = None) -> dict:
        if not self.sock:
            raise ConnectionError("Not connected to QGIS plugin")
        payload = json.dumps({"type": command_type, "params": params or {}}).encode("utf-8")
        self.sock.sendall(payload)
        data = b""
        while True:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("Connection closed by QGIS plugin")
            data += chunk
            try:
                return json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_client: QgisSalahClient | None = None


def get_client() -> QgisSalahClient:
    global _client
    if _client is not None:
        try:
            _client.sock.sendall(b"")
            return _client
        except Exception:
            _client.disconnect()
            _client = None

    _client = QgisSalahClient()
    if not _client.connect():
        _client = None
        raise ConnectionError(
            "Cannot reach QGIS. Open QGIS, enable the 'QGIS Salah MCP' plugin, "
            "and click 'Start Server' in the dock widget."
        )
    logger.info("Connected to QGIS plugin on port 9876")
    return _client


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    logger.info("QGIS Salah MCP server starting")
    try:
        get_client()
        logger.info("QGIS connection verified on startup")
    except Exception as e:
        logger.warning(f"QGIS not reachable on startup: {e}")
    try:
        yield {}
    finally:
        global _client
        if _client:
            _client.disconnect()
            _client = None
        logger.info("QGIS Salah MCP server stopped")


mcp = FastMCP(
    "QGIS_Salah_MCP",
    description="Control QGIS GIS operations through Claude via the Model Context Protocol",
    lifespan=lifespan,
)


def _run(command_type: str, **params) -> str:
    client = get_client()
    result = client.send_command(command_type, params)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tools — Connection
# ---------------------------------------------------------------------------

@mcp.tool()
def ping() -> str:
    """Check the connection to the QGIS Salah MCP plugin."""
    return _run("ping")


@mcp.tool()
def get_qgis_info() -> str:
    """Get the running QGIS version and plugin information."""
    return _run("get_qgis_info")


# ---------------------------------------------------------------------------
# Tools — Project
# ---------------------------------------------------------------------------

@mcp.tool()
def get_project_info() -> str:
    """Get current QGIS project metadata: title, file path, CRS, and layer count."""
    return _run("get_project_info")


@mcp.tool()
def load_project(path: str) -> str:
    """Open a QGIS project file (.qgs or .qgz). Replaces the current project.

    Args:
        path: Absolute path to the .qgs or .qgz project file.
    """
    return _run("load_project", path=path)


@mcp.tool()
def save_project(path: str = None) -> str:
    """Save the current QGIS project.

    Args:
        path: Save to this path. If omitted, saves to the current project path.
    """
    return _run("save_project", path=path)


# ---------------------------------------------------------------------------
# Tools — Layer management
# ---------------------------------------------------------------------------

@mcp.tool()
def get_layers() -> str:
    """List all layers currently loaded in the QGIS project with their name, type, CRS, and visibility."""
    return _run("get_layers")


@mcp.tool()
def load_layer(path: str, name: str = None) -> str:
    """Load a vector layer from a file into the current QGIS project.

    Args:
        path: Absolute path to the vector file (.shp, .gpkg, .geojson, etc.).
        name: Display name for the layer. Defaults to the filename without extension.
    """
    return _run("load_layer", path=path, name=name)


@mcp.tool()
def load_raster_layer(path: str, name: str = None) -> str:
    """Load a raster layer from a file into the current QGIS project.

    Args:
        path: Absolute path to the raster file (.tif, .img, .asc, etc.).
        name: Display name for the layer. Defaults to the filename without extension.
    """
    return _run("load_raster_layer", path=path, name=name)


@mcp.tool()
def remove_layer(layer_name: str) -> str:
    """Remove a layer from the current QGIS project.

    Args:
        layer_name: Exact name of the layer to remove.
    """
    return _run("remove_layer", layer_name=layer_name)


@mcp.tool()
def rename_layer(layer_name: str, new_name: str) -> str:
    """Rename a layer in the QGIS project.

    Args:
        layer_name: Current name of the layer.
        new_name: New display name for the layer.
    """
    return _run("rename_layer", layer_name=layer_name, new_name=new_name)


@mcp.tool()
def set_layer_visibility(layer_name: str, visible: bool) -> str:
    """Show or hide a layer in the QGIS map canvas.

    Args:
        layer_name: Exact name of the layer.
        visible: True to show the layer, False to hide it.
    """
    return _run("set_layer_visibility", layer_name=layer_name, visible=visible)


@mcp.tool()
def zoom_to_layer(layer_name: str) -> str:
    """Zoom the QGIS map canvas to the full extent of a layer.

    Args:
        layer_name: Exact name of the layer.
    """
    return _run("zoom_to_layer", layer_name=layer_name)


@mcp.tool()
def get_layer_summary(layer_name: str) -> str:
    """Return a summary of a layer: feature count, field names, geometry type, and CRS.

    Args:
        layer_name: Exact name of the layer.
    """
    return _run("get_layer_summary", layer_name=layer_name)


# ---------------------------------------------------------------------------
# Tools — Features / attributes
# ---------------------------------------------------------------------------

@mcp.tool()
def get_layer_features(layer_name: str, limit: int = 10) -> str:
    """Get features and their attribute values from a vector layer.

    Args:
        layer_name: Exact name of the layer.
        limit: Maximum number of features to return (default 10).
    """
    return _run("get_layer_features", layer_name=layer_name, limit=limit)


@mcp.tool()
def select_by_expression(layer_name: str, expression: str) -> str:
    """Select features in a layer that match a QGIS expression.

    Args:
        layer_name: Exact name of the layer.
        expression: QGIS expression string (e.g. '"population" > 1000').
    """
    return _run("select_by_expression", layer_name=layer_name, expression=expression)


@mcp.tool()
def add_field(layer_name: str, field_name: str, field_type: str = "string") -> str:
    """Add a new attribute field to a vector layer.

    Args:
        layer_name: Exact name of the layer.
        field_name: Name for the new field.
        field_type: Data type — 'string', 'int', 'double', or 'date' (default: 'string').
    """
    return _run("add_field", layer_name=layer_name, field_name=field_name, field_type=field_type)


@mcp.tool()
def field_statistics(layer_name: str, field_name: str) -> str:
    """Calculate statistics (count, sum, mean, median, std dev, min, max) for a numeric field.

    Args:
        layer_name: Exact name of the layer.
        field_name: Name of the numeric field to analyse.
    """
    return _run("field_statistics", layer_name=layer_name, field_name=field_name)


# ---------------------------------------------------------------------------
# Tools — Symbology
# ---------------------------------------------------------------------------

@mcp.tool()
def apply_categorized_symbology(layer_name: str, field_name: str) -> str:
    """Apply categorized symbology to a layer — each unique value in the field gets a distinct colour.

    Args:
        layer_name: Exact name of the layer.
        field_name: Attribute field whose unique values drive the categories.
    """
    return _run("apply_categorized_symbology", layer_name=layer_name, field_name=field_name)


@mcp.tool()
def apply_graduated_symbology(
    layer_name: str,
    field_name: str,
    classes: int = 5,
    color_ramp: str = "Spectral",
) -> str:
    """Apply graduated (choropleth) symbology to a layer using equal-interval classification.

    Args:
        layer_name: Exact name of the layer.
        field_name: Numeric field to classify.
        classes: Number of colour classes (default 5).
        color_ramp: QGIS colour ramp name (default 'Spectral'). Others: 'Blues', 'RdYlGn', etc.
    """
    return _run("apply_graduated_symbology", layer_name=layer_name, field_name=field_name,
                classes=classes, color_ramp=color_ramp)


@mcp.tool()
def set_layer_opacity(layer_name: str, opacity: float) -> str:
    """Set the transparency of a layer.

    Args:
        layer_name: Exact name of the layer.
        opacity: Value between 0.0 (fully transparent) and 1.0 (fully opaque).
    """
    return _run("set_layer_opacity", layer_name=layer_name, opacity=opacity)


# ---------------------------------------------------------------------------
# Tools — Spatial analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def spatial_join(target_layer: str, join_layer: str, output_name: str = "Joined_Output") -> str:
    """Join attributes from one layer to another based on spatial intersection (one-to-one).

    Args:
        target_layer: Base layer that receives the joined attributes.
        join_layer: Layer whose attributes are transferred.
        output_name: Name for the resulting memory layer.
    """
    return _run("spatial_join", target_layer=target_layer, join_layer=join_layer, output_name=output_name)


@mcp.tool()
def run_buffer_analysis(layer_name: str, distance: float, output_name: str = "Buffer_Result") -> str:
    """Generate buffer polygons around each feature of a layer.

    Args:
        layer_name: Name of the input layer.
        distance: Buffer distance in the layer's CRS units.
        output_name: Name for the resulting memory layer.
    """
    return _run("run_buffer_analysis", layer_name=layer_name, distance=distance, output_name=output_name)


@mcp.tool()
def clip_layer(layer_name: str, mask_layer: str, output_name: str = "Clipped_Output") -> str:
    """Clip a layer to the boundary of a mask layer.

    Args:
        layer_name: Layer to be clipped.
        mask_layer: Layer used as the clipping boundary.
        output_name: Name for the resulting memory layer.
    """
    return _run("clip_layer", layer_name=layer_name, mask_layer=mask_layer, output_name=output_name)


@mcp.tool()
def dissolve_layer(layer_name: str, field: str = None, output_name: str = "Dissolved_Output") -> str:
    """Dissolve features in a layer, optionally grouping by a field.

    Args:
        layer_name: Name of the input layer.
        field: Field to group by. If omitted, all features are dissolved into one.
        output_name: Name for the resulting memory layer.
    """
    return _run("dissolve_layer", layer_name=layer_name, field=field, output_name=output_name)


@mcp.tool()
def merge_layers(layer_names: list[str], output_name: str = "Merged_Output") -> str:
    """Merge multiple vector layers of the same geometry type into one layer.

    Args:
        layer_names: List of layer names to merge.
        output_name: Name for the resulting memory layer.
    """
    return _run("merge_layers", layer_names=layer_names, output_name=output_name)


@mcp.tool()
def reproject_layer(layer_name: str, target_crs: str, output_name: str = None) -> str:
    """Reproject a layer to a different coordinate reference system.

    Args:
        layer_name: Name of the layer to reproject.
        target_crs: Target CRS as an authority string (e.g. 'EPSG:4326', 'EPSG:3857').
        output_name: Name for the resulting memory layer. Defaults to '<layer>_reprojected'.
    """
    return _run("reproject_layer", layer_name=layer_name, target_crs=target_crs, output_name=output_name)


@mcp.tool()
def repair_layer_geometries(layer_name: str) -> str:
    """Fix invalid geometries in a layer. Result added as a new memory layer 'Fixed_Geometries'.

    Args:
        layer_name: Name of the layer with geometries to repair.
    """
    return _run("repair_layer_geometries", layer_name=layer_name)


@mcp.tool()
def extract_by_expression(layer_name: str, expression: str, output_name: str = "Extracted_Output") -> str:
    """Extract features matching a QGIS expression into a new layer.

    Args:
        layer_name: Name of the source layer.
        expression: QGIS expression to filter features (e.g. '"area" > 500').
        output_name: Name for the resulting memory layer.
    """
    return _run("extract_by_expression", layer_name=layer_name, expression=expression, output_name=output_name)


# ---------------------------------------------------------------------------
# Tools — Data processing
# ---------------------------------------------------------------------------

@mcp.tool()
def calculate_field_expression(layer_name: str, field: str, expression: str) -> str:
    """Calculate and update a field's values using a QGIS expression. Modifies the layer in place.

    Args:
        layer_name: Name of the layer to update.
        field: Name of the field to calculate (must already exist).
        expression: QGIS expression (e.g. '$area', '$length', '"pop" / "area"').
    """
    return _run("calculate_field_expression", layer_name=layer_name, field=field, expression=expression)


# ---------------------------------------------------------------------------
# Tools — Export
# ---------------------------------------------------------------------------

@mcp.tool()
def save_layer_to_file(layer_name: str, output_path: str) -> str:
    """Save a layer to a file. Format is inferred from the extension (.shp, .gpkg, .geojson, etc.).

    Args:
        layer_name: Name of the layer to export.
        output_path: Absolute path for the output file.
    """
    return _run("save_layer_to_file", layer_name=layer_name, output_path=output_path)


@mcp.tool()
def export_map_to_image(output_path: str) -> str:
    """Export the current QGIS map canvas view to an image file (PNG, JPG, etc.).

    Args:
        output_path: Absolute path for the output image file.
    """
    return _run("export_map_to_image", output_path=output_path)


@mcp.tool()
def export_to_pdf(output_path: str) -> str:
    """Export the current QGIS map canvas view to a PDF file.

    Args:
        output_path: Absolute path for the output PDF file.
    """
    return _run("export_to_pdf", output_path=output_path)


# ---------------------------------------------------------------------------
# Tools — Code execution
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_code(code: str) -> str:
    """Execute arbitrary PyQGIS code inside QGIS. Returns captured stdout and stderr.

    Args:
        code: Valid Python/PyQGIS code. `iface` and `QgsProject` are pre-imported.
    """
    return _run("execute_code", code=code)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
