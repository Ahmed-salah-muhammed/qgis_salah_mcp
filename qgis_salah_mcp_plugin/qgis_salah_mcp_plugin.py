"""
QGIS Salah MCP Plugin
TCP socket server running inside QGIS, using length-prefixed JSON framing.
Protocol: 4-byte big-endian message length header + UTF-8 JSON body.
"""

import contextlib
import io
import json
import os
import socket
import struct
import sys
import traceback

from qgis.PyQt.QtCore import Qt, QObject, QTimer, QVariant
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from qgis.analysis import QgsNativeAlgorithms
from qgis.core import (
    Qgis, QgsApplication, QgsCategorizedSymbolRenderer, QgsField,
    QgsGraduatedSymbolRenderer, QgsLayoutExporter, QgsLayoutItemMap,
    QgsMessageLog, QgsPrintLayout, QgsProject, QgsRasterLayer,
    QgsRendererCategory, QgsStyle, QgsSymbol, QgsVectorLayer,
)
from qgis.utils import iface

from .compat import (
    LAYER_RASTER, LAYER_VECTOR,
    MSG_CRITICAL, MSG_INFO, MSG_WARNING,
)

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8765
_RECV_CHUNK = 65536
_MAX_MSG_SIZE = 10 * 1024 * 1024          # 10 MB
_HEADER = struct.Struct(">I")              # 4-byte big-endian uint
_LOG_TAG = "QgisSalahMCP"


def classFactory(iface):
    return QgisSalahMCPPlugin(iface)


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

class QgisSalahMCPServer(QObject):
    """
    Non-blocking TCP socket server polled by a QTimer every 25 ms.

    Protocol (same as jjsantos01/qgis_mcp):
        Request / Response = [4-byte big-endian length][UTF-8 JSON body]

    Command shape:  {"type": "<name>", "params": {...}}
    Response shape: {"status": "success"|"error", "result": ...}
    """

    MAX_CLIENTS = 10

    def __init__(self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT):
        super().__init__()
        self.host = host
        self.port = port
        self._server_sock: socket.socket | None = None
        self._clients: dict[socket.socket, bytes] = {}   # sock → recv buffer
        self._timer = QTimer()
        self._timer.timeout.connect(self._process)
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
            QgsMessageLog.logMessage(f"Processing init skipped: {e}", _LOG_TAG, MSG_WARNING)

    def start(self, port: int | None = None) -> bool:
        if port:
            self.port = port
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.setblocking(False)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(self.MAX_CLIENTS)
            self._timer.start(25)
            QgsMessageLog.logMessage(
                f"Server started on {self.host}:{self.port}", _LOG_TAG, MSG_INFO
            )
            return True
        except Exception as e:
            QgsMessageLog.logMessage(f"Failed to start: {e}", _LOG_TAG, MSG_CRITICAL)
            self.stop()
            return False

    def stop(self):
        self._timer.stop()
        if self._server_sock:
            with contextlib.suppress(Exception):
                self._server_sock.close()
            self._server_sock = None
        for sock in list(self._clients):
            with contextlib.suppress(Exception):
                sock.close()
        self._clients.clear()
        QgsMessageLog.logMessage("Server stopped", _LOG_TAG, MSG_INFO)

    @property
    def is_running(self) -> bool:
        return self._server_sock is not None

    # ------------------------------------------------------------------
    # I/O loop (called every 25 ms by QTimer)
    # ------------------------------------------------------------------

    def _process(self):
        if not self._server_sock:
            return

        # Accept new clients
        while len(self._clients) < self.MAX_CLIENTS:
            try:
                client_sock, addr = self._server_sock.accept()
                client_sock.setblocking(False)
                self._clients[client_sock] = b""
                QgsMessageLog.logMessage(
                    f"Client connected: {addr} ({len(self._clients)} active)",
                    _LOG_TAG, MSG_INFO,
                )
            except BlockingIOError:
                break
            except Exception as e:
                QgsMessageLog.logMessage(f"Accept error: {e}", _LOG_TAG, MSG_WARNING)
                break

        # Read from each client
        for sock in list(self._clients):
            try:
                data = sock.recv(_RECV_CHUNK)
                if not data:
                    self._drop(sock)
                    continue
                buf = self._clients[sock] + data
                if len(buf) > _MAX_MSG_SIZE:
                    self._drop(sock, "Buffer exceeded 10 MB")
                    continue
                # Extract all complete length-prefixed messages
                while len(buf) >= 4:
                    msg_len = _HEADER.unpack(buf[:4])[0]
                    if msg_len > _MAX_MSG_SIZE:
                        self._drop(sock, f"Message too large: {msg_len} bytes")
                        buf = b""
                        break
                    if len(buf) < 4 + msg_len:
                        break                   # Incomplete — wait for more data
                    msg_bytes = buf[4: 4 + msg_len]
                    buf = buf[4 + msg_len:]
                    try:
                        command = json.loads(msg_bytes.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        self._send(sock, {"status": "error", "message": f"Invalid JSON: {e}"})
                        continue
                    response = self._dispatch(command)
                    self._send(sock, response)
                self._clients[sock] = buf
            except BlockingIOError:
                pass
            except Exception as e:
                self._drop(sock, f"Client error: {e}")

    def _send(self, sock: socket.socket, response: dict):
        """Send a length-prefixed JSON response."""
        body = json.dumps(response).encode("utf-8")
        sock.sendall(_HEADER.pack(len(body)) + body)

    def _drop(self, sock: socket.socket, reason: str = "Client disconnected"):
        with contextlib.suppress(Exception):
            sock.close()
        self._clients.pop(sock, None)
        QgsMessageLog.logMessage(
            f"{reason} ({len(self._clients)} active)", _LOG_TAG, MSG_INFO
        )

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, command: dict) -> dict:
        cmd_type = command.get("type", "")
        params = command.get("params", {})
        handler = getattr(self, f"_cmd_{cmd_type}", None)
        if not handler:
            return {"status": "error", "message": f"Unknown command: {cmd_type}"}
        try:
            QgsMessageLog.logMessage(f"Executing: {cmd_type}", _LOG_TAG, MSG_INFO)
            return {"status": "success", "result": handler(**params)}
        except Exception as e:
            QgsMessageLog.logMessage(f"Error in {cmd_type}: {e}", _LOG_TAG, MSG_CRITICAL)
            return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _layer(self, layer_name: str):
        layers = QgsProject.instance().mapLayersByName(layer_name)
        if not layers:
            raise ValueError(f"Layer '{layer_name}' not found in project")
        return layers[0]

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _cmd_ping(self):
        return "pong"

    def _cmd_get_qgis_info(self):
        return {
            "qgis_version": Qgis.QGIS_VERSION,
            "plugin": "QGIS Salah MCP v1.0",
            "port": self.port,
        }

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def _cmd_get_project_info(self):
        proj = QgsProject.instance()
        return {
            "title": proj.title(),
            "path": proj.fileName(),
            "crs": proj.crs().authid(),
            "layer_count": len(proj.mapLayers()),
        }

    def _cmd_load_project(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Project file not found: {path}")
        QgsProject.instance().read(path)
        return f"Project loaded: {path}"

    def _cmd_save_project(self, path: str = None):
        if path:
            QgsProject.instance().write(path)
            return f"Project saved to: {path}"
        QgsProject.instance().write()
        return "Project saved"

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------

    def _cmd_get_layers(self):
        root = QgsProject.instance().layerTreeRoot()
        type_map = {
            LAYER_VECTOR: "Vector",
            LAYER_RASTER: "Raster",
        }
        result = []
        for layer_id, layer in QgsProject.instance().mapLayers().items():
            node = root.findLayer(layer_id)
            result.append({
                "name": layer.name(),
                "id": layer_id,
                "type": type_map.get(layer.type(), "Other"),
                "visible": node.isVisible() if node else False,
                "crs": layer.crs().authid(),
            })
        return result

    def _cmd_load_layer(self, path: str, name: str = None):
        name = name or os.path.splitext(os.path.basename(path))[0]
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            raise ValueError("Invalid vector layer — check file format")
        QgsProject.instance().addMapLayer(layer)
        return f"Vector layer loaded: {name}"

    def _cmd_load_raster_layer(self, path: str, name: str = None):
        name = name or os.path.splitext(os.path.basename(path))[0]
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        layer = QgsRasterLayer(path, name)
        if not layer.isValid():
            raise ValueError("Invalid raster layer — check file format")
        QgsProject.instance().addMapLayer(layer)
        return f"Raster layer loaded: {name}"

    def _cmd_remove_layer(self, layer_name: str):
        layer = self._layer(layer_name)
        QgsProject.instance().removeMapLayer(layer.id())
        return f"Layer removed: {layer_name}"

    def _cmd_rename_layer(self, layer_name: str, new_name: str):
        self._layer(layer_name).setName(new_name)
        return f"Layer renamed to: {new_name}"

    def _cmd_set_layer_visibility(self, layer_name: str, visible: bool):
        layer = self._layer(layer_name)
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if not node:
            raise ValueError(f"Layer tree node not found for '{layer_name}'")
        node.setItemVisibilityChecked(bool(visible))
        return f"Layer '{layer_name}' visibility set to {visible}"

    def _cmd_zoom_to_layer(self, layer_name: str):
        layer = self._layer(layer_name)
        iface.setActiveLayer(layer)
        iface.zoomToActiveLayer()
        return f"Zoomed to: {layer_name}"

    def _cmd_get_layer_summary(self, layer_name: str):
        layer = self._layer(layer_name)
        geom_map = {0: "Point", 1: "Line", 2: "Polygon", 3: "Unknown"}
        return {
            "name": layer.name(),
            "feature_count": layer.featureCount(),
            "fields": [f.name() for f in layer.fields()],
            "geometry_type": geom_map.get(layer.geometryType(), "Unknown"),
            "crs": layer.crs().authid(),
        }

    # ------------------------------------------------------------------
    # Features / attributes
    # ------------------------------------------------------------------

    def _cmd_get_layer_features(self, layer_name: str, limit: int = 10):
        layer = self._layer(layer_name)
        features = []
        for i, feat in enumerate(layer.getFeatures()):
            if i >= int(limit):
                break
            attrs = {}
            for field in layer.fields():
                val = feat[field.name()]
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                attrs[field.name()] = val
            features.append({"id": feat.id(), "attributes": attrs})
        return {
            "total_count": layer.featureCount(),
            "returned": len(features),
            "features": features,
        }

    def _cmd_select_by_expression(self, layer_name: str, expression: str):
        layer = self._layer(layer_name)
        layer.selectByExpression(expression)
        return f"Selected {layer.selectedFeatureCount()} features in '{layer_name}'"

    def _cmd_add_field(self, layer_name: str, field_name: str, field_type: str = "string"):
        layer = self._layer(layer_name)
        type_map = {
            "string": QVariant.String,
            "int": QVariant.Int,
            "double": QVariant.Double,
            "date": QVariant.Date,
        }
        vtype = type_map.get(field_type.lower(), QVariant.String)
        layer.startEditing()
        layer.addAttribute(QgsField(field_name, vtype))
        layer.commitChanges()
        return f"Field '{field_name}' ({field_type}) added to '{layer_name}'"

    def _cmd_field_statistics(self, layer_name: str, field_name: str):
        import processing
        layer = self._layer(layer_name)
        result = processing.run("native:basicstatisticsforfields", {
            "INPUT": layer,
            "FIELD_NAME": field_name,
        })
        keys = ["COUNT", "SUM", "MEAN", "MEDIAN", "STD_DEV", "MIN", "MAX", "RANGE"]
        return {k: result[k] for k in keys if k in result}

    # ------------------------------------------------------------------
    # Symbology
    # ------------------------------------------------------------------

    def _cmd_apply_categorized_symbology(self, layer_name: str, field_name: str):
        layer = self._layer(layer_name)
        idx = layer.fields().indexFromName(field_name)
        if idx == -1:
            raise ValueError(f"Field '{field_name}' not found")
        categories = [
            QgsRendererCategory(str(v), QgsSymbol.defaultSymbol(layer.geometryType()), str(v))
            for v in layer.uniqueValues(idx)
        ]
        layer.setRenderer(QgsCategorizedSymbolRenderer(field_name, categories))
        layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(layer.id())
        return f"Categorized symbology applied to '{layer_name}' on field '{field_name}'"

    def _cmd_apply_graduated_symbology(
        self, layer_name: str, field_name: str,
        classes: int = 5, color_ramp: str = "Spectral",
    ):
        layer = self._layer(layer_name)
        ramp = QgsStyle.defaultStyle().colorRamp(color_ramp)
        renderer = QgsGraduatedSymbolRenderer(field_name)
        renderer.updateColorRamp(ramp)
        renderer.updateClasses(layer, 0, int(classes))  # 0 = EqualInterval
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(layer.id())
        return f"Graduated symbology applied to '{layer_name}' on '{field_name}' ({classes} classes)"

    def _cmd_set_layer_opacity(self, layer_name: str, opacity: float):
        opacity = float(opacity)
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Opacity must be between 0.0 and 1.0")
        layer = self._layer(layer_name)
        layer.setOpacity(opacity)
        layer.triggerRepaint()
        return f"Opacity set to {opacity} on '{layer_name}'"

    # ------------------------------------------------------------------
    # Spatial analysis
    # ------------------------------------------------------------------

    def _cmd_spatial_join(
        self, target_layer: str, join_layer: str,
        output_name: str = "Joined_Output",
    ):
        import processing
        result = processing.run("native:joinattributesbylocation", {
            "INPUT": self._layer(target_layer),
            "JOIN": self._layer(join_layer),
            "PREDICATE": [0], "METHOD": 0,
            "OUTPUT": f"memory:{output_name}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Spatial join complete. Layer added: {output_name}"

    def _cmd_run_buffer_analysis(
        self, layer_name: str, distance: float,
        output_name: str = "Buffer_Result",
    ):
        import processing
        result = processing.run("native:buffer", {
            "INPUT": self._layer(layer_name),
            "DISTANCE": float(distance),
            "OUTPUT": f"memory:{output_name}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Buffer complete. Layer added: {output_name}"

    def _cmd_clip_layer(
        self, layer_name: str, mask_layer: str,
        output_name: str = "Clipped_Output",
    ):
        import processing
        result = processing.run("native:clip", {
            "INPUT": self._layer(layer_name),
            "OVERLAY": self._layer(mask_layer),
            "OUTPUT": f"memory:{output_name}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Clip complete. Layer added: {output_name}"

    def _cmd_dissolve_layer(
        self, layer_name: str, field: str = None,
        output_name: str = "Dissolved_Output",
    ):
        import processing
        result = processing.run("native:dissolve", {
            "INPUT": self._layer(layer_name),
            "FIELD": [field] if field else [],
            "OUTPUT": f"memory:{output_name}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Dissolve complete. Layer added: {output_name}"

    def _cmd_merge_layers(self, layer_names: list, output_name: str = "Merged_Output"):
        import processing
        result = processing.run("native:mergevectorlayers", {
            "LAYERS": [self._layer(n) for n in layer_names],
            "OUTPUT": f"memory:{output_name}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Merge complete. Layer added: {output_name}"

    def _cmd_reproject_layer(
        self, layer_name: str, target_crs: str, output_name: str = None,
    ):
        import processing
        layer = self._layer(layer_name)
        out = output_name or f"{layer_name}_reprojected"
        result = processing.run("native:reprojectlayer", {
            "INPUT": layer,
            "TARGET_CRS": target_crs,
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Reprojected to {target_crs}. Layer added: {out}"

    def _cmd_repair_layer_geometries(self, layer_name: str):
        import processing
        result = processing.run("native:fixgeometries", {
            "INPUT": self._layer(layer_name),
            "OUTPUT": "memory:Fixed_Geometries",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return "Geometry repair complete. Layer added: Fixed_Geometries"

    def _cmd_extract_by_expression(
        self, layer_name: str, expression: str,
        output_name: str = "Extracted_Output",
    ):
        import processing
        result = processing.run("native:extractbyexpression", {
            "INPUT": self._layer(layer_name),
            "EXPRESSION": expression,
            "OUTPUT": f"memory:{output_name}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Extracted features to: {output_name}"

    # ------------------------------------------------------------------
    # Data processing
    # ------------------------------------------------------------------

    def _cmd_calculate_field_expression(
        self, layer_name: str, field: str, expression: str,
    ):
        import processing
        processing.run("native:fieldcalculator", {
            "INPUT": self._layer(layer_name),
            "FIELD_NAME": field,
            "FIELD_TYPE": 0,
            "EXPRESSION": expression,
            "OUTPUT": "inplace",
        })
        return f"Field '{field}' updated with: {expression}"

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _cmd_save_layer_to_file(self, layer_name: str, output_path: str):
        import processing
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        processing.run("native:savefeatures", {
            "INPUT": self._layer(layer_name),
            "OUTPUT": output_path,
        })
        return f"Layer saved to: {output_path}"

    def _cmd_export_map_to_image(self, output_path: str):
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        iface.mapCanvas().saveAsImage(output_path)
        return f"Map exported to: {output_path}"

    def _cmd_export_to_pdf(self, output_path: str):
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
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

    # ------------------------------------------------------------------
    # Code execution
    # ------------------------------------------------------------------

    def _cmd_execute_code(self, code: str):
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            exec(code, {"iface": iface, "QgsProject": QgsProject, "__builtins__": __builtins__})
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return {"stdout": out.getvalue(), "stderr": err.getvalue()}


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
        self.port_spin.setValue(_DEFAULT_PORT)
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
            ok = self.server.start(port=self.port_spin.value())
            if ok:
                self.status_label.setText(f"Status: Running on port {self.port_spin.value()}")
                self.toggle_btn.setText("Stop Server")
                self.port_spin.setEnabled(False)
            else:
                self.status_label.setText("Status: Failed to start (check QGIS log)")
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
