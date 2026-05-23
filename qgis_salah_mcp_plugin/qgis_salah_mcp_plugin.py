"""
QGIS Salah MCP Plugin
TCP socket server running inside QGIS that handles MCP commands from Claude.
"""

import io
import json
import os
import select
import socket
import sys
import traceback

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from qgis.analysis import QgsNativeAlgorithms
from qgis.core import (
    QgsApplication, QgsCategorizedSymbolRenderer, QgsLayoutExporter,
    QgsLayoutItemMap, QgsPrintLayout, QgsProject, QgsRendererCategory,
    QgsSymbol, QgsVectorLayer,
)
from qgis.utils import iface


def classFactory(iface):
    return QgisSalahMCPPlugin(iface)


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

class QgisSalahMCPServer:
    """Non-blocking TCP socket server polled by a QTimer every 100 ms."""

    def __init__(self, host: str = "localhost", port: int = 9876):
        self.host = host
        self.port = port
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._buf = b""
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._init_processing()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_processing(self):
        try:
            from processing.core.Processing import Processing
            Processing.initialize()
            QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
        except Exception as e:
            print(f"[QgisSalahMCP] Processing init skipped: {e}")

    def start(self, port: int | None = None):
        if port:
            self.port = port
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.setblocking(False)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(1)
        self._timer.start(100)
        print(f"[QgisSalahMCP] Listening on {self.host}:{self.port}")

    def stop(self):
        self._timer.stop()
        self._close_client()
        if self._server_sock:
            self._server_sock.close()
            self._server_sock = None
        print("[QgisSalahMCP] Server stopped")

    @property
    def is_running(self) -> bool:
        return self._server_sock is not None

    # ------------------------------------------------------------------
    # I/O loop
    # ------------------------------------------------------------------

    def _poll(self):
        # Accept a new client if none connected
        if self._server_sock and self._client_sock is None:
            try:
                r, _, _ = select.select([self._server_sock], [], [], 0)
                if r:
                    self._client_sock, addr = self._server_sock.accept()
                    self._client_sock.setblocking(False)
                    print(f"[QgisSalahMCP] Client connected from {addr}")
            except Exception as e:
                print(f"[QgisSalahMCP] Accept error: {e}")
                return

        if self._client_sock is None:
            return

        # Read available data
        try:
            r, _, _ = select.select([self._client_sock], [], [], 0)
            if not r:
                return
            chunk = self._client_sock.recv(65536)
            if not chunk:
                self._close_client()
                return
            self._buf += chunk
            # Attempt to decode a complete JSON object
            try:
                command = json.loads(self._buf.decode("utf-8"))
                self._buf = b""
                response = self._dispatch(command)
                self._client_sock.sendall(json.dumps(response).encode("utf-8"))
            except json.JSONDecodeError:
                pass  # Wait for more data
        except (ConnectionResetError, BrokenPipeError, OSError):
            self._close_client()
        except Exception as e:
            print(f"[QgisSalahMCP] Poll error: {e}")
            self._close_client()

    def _close_client(self):
        if self._client_sock:
            try:
                self._client_sock.close()
            except Exception:
                pass
            self._client_sock = None
        self._buf = b""

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    _HANDLERS = {
        "ping", "get_qgis_info",
        "load_layer", "zoom_to_layer", "get_layer_summary",
        "apply_categorized_symbology",
        "spatial_join", "run_buffer_analysis",
        "repair_layer_geometries", "calculate_field_expression",
        "export_to_pdf", "execute_code",
    }

    def _dispatch(self, command: dict) -> dict:
        cmd_type = command.get("type", "")
        params = command.get("params", {})
        if cmd_type not in self._HANDLERS:
            return {"status": "error", "message": f"Unknown command: {cmd_type}"}
        handler = getattr(self, f"_cmd_{cmd_type}")
        try:
            return {"status": "success", "result": handler(params)}
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc(),
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _layer(self, name: str):
        layers = QgsProject.instance().mapLayersByName(name)
        if not layers:
            raise ValueError(f"Layer '{name}' not found in project")
        return layers[0]

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_ping(self, p):
        return "pong"

    def _cmd_get_qgis_info(self, p):
        from qgis.core import Qgis
        return {"qgis_version": Qgis.QGIS_VERSION, "plugin": "QGIS Salah MCP v1.0"}

    def _cmd_load_layer(self, p):
        path = p["path"]
        name = p.get("name") or os.path.splitext(os.path.basename(path))[0]
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            raise ValueError("Layer is invalid — check file format or data")
        QgsProject.instance().addMapLayer(layer)
        return f"Layer loaded: {name}"

    def _cmd_zoom_to_layer(self, p):
        layer = self._layer(p["layer_name"])
        iface.setActiveLayer(layer)
        iface.zoomToActiveLayer()
        return f"Zoomed to: {p['layer_name']}"

    def _cmd_get_layer_summary(self, p):
        layer = self._layer(p["layer_name"])
        geom_map = {0: "Point", 1: "Line", 2: "Polygon", 3: "Unknown"}
        return {
            "name": layer.name(),
            "feature_count": layer.featureCount(),
            "fields": [f.name() for f in layer.fields()],
            "geometry_type": geom_map.get(layer.geometryType(), "Unknown"),
            "crs": layer.crs().authid(),
        }

    def _cmd_apply_categorized_symbology(self, p):
        layer = self._layer(p["layer_name"])
        field_name = p["field_name"]
        idx = layer.fields().indexFromName(field_name)
        if idx == -1:
            raise ValueError(f"Field '{field_name}' not found in layer")
        categories = [
            QgsRendererCategory(str(v), QgsSymbol.defaultSymbol(layer.geometryType()), str(v))
            for v in layer.uniqueValues(idx)
        ]
        layer.setRenderer(QgsCategorizedSymbolRenderer(field_name, categories))
        layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(layer.id())
        return f"Categorized symbology applied to '{p['layer_name']}' on field '{field_name}'"

    def _cmd_spatial_join(self, p):
        import processing
        t_lyr = self._layer(p["target_layer"])
        j_lyr = self._layer(p["join_layer"])
        out = p.get("output_name", "Joined_Output")
        result = processing.run("native:joinattributesbylocation", {
            "INPUT": t_lyr, "JOIN": j_lyr,
            "PREDICATE": [0], "METHOD": 0,
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Spatial join complete. Layer added: {out}"

    def _cmd_run_buffer_analysis(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        out = p.get("output_name", "Buffer_Result")
        result = processing.run("native:buffer", {
            "INPUT": layer,
            "DISTANCE": float(p["distance"]),
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Buffer complete. Layer added: {out}"

    def _cmd_repair_layer_geometries(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        result = processing.run("native:fixgeometries", {
            "INPUT": layer,
            "OUTPUT": "memory:Fixed_Geometries",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return "Geometry repair complete. Layer added: Fixed_Geometries"

    def _cmd_calculate_field_expression(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        processing.run("native:fieldcalculator", {
            "INPUT": layer,
            "FIELD_NAME": p["field"],
            "FIELD_TYPE": 0,
            "EXPRESSION": p["expression"],
            "OUTPUT": "inplace",
        })
        return f"Field '{p['field']}' updated with: {p['expression']}"

    def _cmd_export_to_pdf(self, p):
        output_path = p["output_path"]
        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        layout = QgsPrintLayout(QgsProject.instance())
        layout.initializeDefaults()
        map_item = QgsLayoutItemMap(layout)
        map_item.setRect(20, 20, 200, 200)
        map_item.setExtent(iface.mapCanvas().extent())
        layout.addLayoutItem(map_item)
        QgsLayoutExporter(layout).exportToPdf(
            output_path, QgsLayoutExporter.PdfExportSettings()
        )
        return f"Exported to: {output_path}"

    def _cmd_execute_code(self, p):
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = captured_out, captured_err
        try:
            exec(p["code"], {"iface": iface, "QgsProject": QgsProject, "__builtins__": __builtins__})
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return {
            "stdout": captured_out.getvalue(),
            "stderr": captured_err.getvalue(),
        }


# ---------------------------------------------------------------------------
# Dock widget UI
# ---------------------------------------------------------------------------

class QgisSalahDockWidget(QDockWidget):
    def __init__(self, server: QgisSalahMCPServer, parent=None):
        super().__init__("QGIS Salah MCP", parent)
        self.server = server
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._build_ui()

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)

        self.status_label = QLabel("Status: Stopped")

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:"))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(9876)
        port_row.addWidget(self.port_spin)
        port_row.addStretch()

        self.toggle_btn = QPushButton("Start Server")
        self.toggle_btn.clicked.connect(self._toggle)

        layout.addWidget(self.status_label)
        layout.addLayout(port_row)
        layout.addWidget(self.toggle_btn)
        layout.addStretch()
        self.setWidget(root)

    def _toggle(self):
        if not self.server.is_running:
            try:
                self.server.start(port=self.port_spin.value())
                self.status_label.setText(f"Status: Running on port {self.port_spin.value()}")
                self.toggle_btn.setText("Stop Server")
                self.port_spin.setEnabled(False)
            except Exception as e:
                self.status_label.setText(f"Error: {e}")
        else:
            self.server.stop()
            self.status_label.setText("Status: Stopped")
            self.toggle_btn.setText("Start Server")
            self.port_spin.setEnabled(True)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

class QgisSalahMCPPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.server = QgisSalahMCPServer()
        self.dock: QgisSalahDockWidget | None = None
        self.action: QAction | None = None

    def initGui(self):
        self.action = QAction("QGIS Salah MCP", self.iface.mainWindow())
        self.action.triggered.connect(self._show_dock)
        self.iface.addPluginToMenu("QGIS Salah MCP", self.action)

        self.dock = QgisSalahDockWidget(self.server, self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)

    def unload(self):
        self.server.stop()
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action:
            self.iface.removePluginMenu("QGIS Salah MCP", self.action)
            self.action = None

    def _show_dock(self):
        if self.dock:
            self.dock.show()
            self.dock.raise_()
