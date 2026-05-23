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
            _client.sock.sendall(b"")  # cheap liveness check
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
    """Send a command to the QGIS plugin and return the JSON response as a string."""
    client = get_client()
    result = client.send_command(command_type, params)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def ping() -> str:
    """Check the connection to the QGIS Salah MCP plugin."""
    return _run("ping")


@mcp.tool()
def get_qgis_info() -> str:
    """Get the running QGIS version and plugin information."""
    return _run("get_qgis_info")


@mcp.tool()
def load_layer(path: str, name: str = None) -> str:
    """Load a vector layer from a file path into the current QGIS project.

    Args:
        path: Absolute path to the vector file (e.g. .shp, .gpkg, .geojson).
        name: Display name for the layer. Defaults to the filename.
    """
    return _run("load_layer", path=path, name=name)


@mcp.tool()
def zoom_to_layer(layer_name: str) -> str:
    """Zoom the QGIS map canvas to the full extent of a layer.

    Args:
        layer_name: Exact name of the layer as it appears in the QGIS Layers panel.
    """
    return _run("zoom_to_layer", layer_name=layer_name)


@mcp.tool()
def get_layer_summary(layer_name: str) -> str:
    """Return a summary of a layer: feature count, field names, geometry type, and CRS.

    Args:
        layer_name: Exact name of the layer as it appears in the QGIS Layers panel.
    """
    return _run("get_layer_summary", layer_name=layer_name)


@mcp.tool()
def apply_categorized_symbology(layer_name: str, field_name: str) -> str:
    """Apply categorized symbology to a layer based on the unique values of a field.

    Args:
        layer_name: Exact name of the layer.
        field_name: Name of the attribute field to categorize by.
    """
    return _run("apply_categorized_symbology", layer_name=layer_name, field_name=field_name)


@mcp.tool()
def spatial_join(
    target_layer: str,
    join_layer: str,
    output_name: str = "Joined_Output",
) -> str:
    """Perform a spatial join between two layers (intersection predicate, one-to-one).
    The result is added as a new memory layer.

    Args:
        target_layer: Name of the base layer to join attributes into.
        join_layer: Name of the layer whose attributes are joined.
        output_name: Name for the resulting memory layer.
    """
    return _run("spatial_join", target_layer=target_layer, join_layer=join_layer, output_name=output_name)


@mcp.tool()
def run_buffer_analysis(
    layer_name: str,
    distance: float,
    output_name: str = "Buffer_Result",
) -> str:
    """Generate a buffer polygon around each feature of a layer.
    The result is added as a new memory layer.

    Args:
        layer_name: Name of the input layer.
        distance: Buffer distance in the layer's CRS units.
        output_name: Name for the resulting memory layer.
    """
    return _run("run_buffer_analysis", layer_name=layer_name, distance=distance, output_name=output_name)


@mcp.tool()
def repair_layer_geometries(layer_name: str) -> str:
    """Fix invalid geometries in a layer using QGIS native:fixgeometries.
    The repaired layer is added as a new memory layer named Fixed_Geometries.

    Args:
        layer_name: Name of the layer with geometries to repair.
    """
    return _run("repair_layer_geometries", layer_name=layer_name)


@mcp.tool()
def calculate_field_expression(layer_name: str, field: str, expression: str) -> str:
    """Calculate and update field values using a QGIS expression. Modifies the layer in place.

    Args:
        layer_name: Name of the layer to update.
        field: Name of the field to calculate (must already exist).
        expression: QGIS expression string (e.g. '$area', '$length', '"pop" / "area"').
    """
    return _run("calculate_field_expression", layer_name=layer_name, field=field, expression=expression)


@mcp.tool()
def export_to_pdf(output_path: str) -> str:
    """Export the current QGIS map canvas view to a PDF file.

    Args:
        output_path: Absolute path for the output PDF file.
    """
    return _run("export_to_pdf", output_path=output_path)


@mcp.tool()
def execute_code(code: str) -> str:
    """Execute arbitrary PyQGIS code inside QGIS. Returns captured stdout and stderr.

    Args:
        code: Valid Python/PyQGIS code string. `iface` and `QgsProject` are pre-imported.
    """
    return _run("execute_code", code=code)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
