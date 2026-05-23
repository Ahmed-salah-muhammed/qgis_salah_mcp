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

from qgis.PyQt.QtCore import Qt, QTimer, QVariant
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from qgis.analysis import QgsNativeAlgorithms
from qgis.core import (
    QgsApplication, QgsCategorizedSymbolRenderer, QgsField,
    QgsGraduatedSymbolRenderer, QgsLayoutExporter, QgsLayoutItemMap,
    QgsPrintLayout, QgsProject, QgsRasterLayer, QgsRendererCategory,
    QgsStyle, QgsSymbol, QgsVectorLayer,
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

        try:
            r, _, _ = select.select([self._client_sock], [], [], 0)
            if not r:
                return
            chunk = self._client_sock.recv(65536)
            if not chunk:
                self._close_client()
                return
            self._buf += chunk
            try:
                command = json.loads(self._buf.decode("utf-8"))
                self._buf = b""
                response = self._dispatch(command)
                self._client_sock.sendall(json.dumps(response).encode("utf-8"))
            except json.JSONDecodeError:
                pass
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
        # Connection
        "ping", "get_qgis_info",
        # Project
        "get_project_info", "load_project", "save_project",
        # Layer management
        "get_layers", "load_layer", "load_raster_layer",
        "remove_layer", "rename_layer", "set_layer_visibility",
        "zoom_to_layer", "get_layer_summary",
        # Features / attributes
        "get_layer_features", "select_by_expression",
        "add_field", "field_statistics",
        # Symbology
        "apply_categorized_symbology", "apply_graduated_symbology",
        "set_layer_opacity",
        # Spatial analysis
        "spatial_join", "run_buffer_analysis", "clip_layer",
        "dissolve_layer", "merge_layers", "reproject_layer",
        "repair_layer_geometries", "extract_by_expression",
        # Data processing
        "calculate_field_expression",
        # Export
        "save_layer_to_file", "export_map_to_image", "export_to_pdf",
        # Code execution
        "execute_code",
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
    # Helper
    # ------------------------------------------------------------------

    def _layer(self, name: str):
        layers = QgsProject.instance().mapLayersByName(name)
        if not layers:
            raise ValueError(f"Layer '{name}' not found in project")
        return layers[0]

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _cmd_ping(self, p):
        return "pong"

    def _cmd_get_qgis_info(self, p):
        from qgis.core import Qgis
        return {"qgis_version": Qgis.QGIS_VERSION, "plugin": "QGIS Salah MCP v1.0"}

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def _cmd_get_project_info(self, p):
        proj = QgsProject.instance()
        return {
            "title": proj.title(),
            "path": proj.fileName(),
            "crs": proj.crs().authid(),
            "layer_count": len(proj.mapLayers()),
        }

    def _cmd_load_project(self, p):
        path = p["path"]
        if not os.path.exists(path):
            raise FileNotFoundError(f"Project file not found: {path}")
        QgsProject.instance().read(path)
        return f"Project loaded: {path}"

    def _cmd_save_project(self, p):
        path = p.get("path")
        if path:
            QgsProject.instance().write(path)
            return f"Project saved to: {path}"
        QgsProject.instance().write()
        return "Project saved"

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------

    def _cmd_get_layers(self, p):
        root = QgsProject.instance().layerTreeRoot()
        type_map = {0: "Vector", 1: "Raster", 3: "Plugin", 4: "Mesh", 6: "Annotation"}
        result = []
        for layer_id, layer in QgsProject.instance().mapLayers().items():
            node = root.findLayer(layer_id)
            result.append({
                "name": layer.name(),
                "id": layer_id,
                "type": type_map.get(int(layer.type()), "Unknown"),
                "visible": node.isVisible() if node else False,
                "crs": layer.crs().authid(),
            })
        return result

    def _cmd_load_layer(self, p):
        path = p["path"]
        name = p.get("name") or os.path.splitext(os.path.basename(path))[0]
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            raise ValueError("Layer is invalid — check file format or data")
        QgsProject.instance().addMapLayer(layer)
        return f"Vector layer loaded: {name}"

    def _cmd_load_raster_layer(self, p):
        path = p["path"]
        name = p.get("name") or os.path.splitext(os.path.basename(path))[0]
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        layer = QgsRasterLayer(path, name)
        if not layer.isValid():
            raise ValueError("Invalid raster layer — check file format")
        QgsProject.instance().addMapLayer(layer)
        return f"Raster layer loaded: {name}"

    def _cmd_remove_layer(self, p):
        layer = self._layer(p["layer_name"])
        QgsProject.instance().removeMapLayer(layer.id())
        return f"Layer removed: {p['layer_name']}"

    def _cmd_rename_layer(self, p):
        layer = self._layer(p["layer_name"])
        layer.setName(p["new_name"])
        return f"Layer renamed to: {p['new_name']}"

    def _cmd_set_layer_visibility(self, p):
        layer = self._layer(p["layer_name"])
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if not node:
            raise ValueError(f"Layer tree node not found for '{p['layer_name']}'")
        node.setItemVisibilityChecked(bool(p["visible"]))
        return f"Layer '{p['layer_name']}' visibility set to {p['visible']}"

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

    # ------------------------------------------------------------------
    # Features / attributes
    # ------------------------------------------------------------------

    def _cmd_get_layer_features(self, p):
        layer = self._layer(p["layer_name"])
        limit = int(p.get("limit", 10))
        features = []
        for i, feat in enumerate(layer.getFeatures()):
            if i >= limit:
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

    def _cmd_select_by_expression(self, p):
        layer = self._layer(p["layer_name"])
        layer.selectByExpression(p["expression"])
        return f"Selected {layer.selectedFeatureCount()} features in '{p['layer_name']}'"

    def _cmd_add_field(self, p):
        layer = self._layer(p["layer_name"])
        type_map = {
            "string": QVariant.String,
            "int": QVariant.Int,
            "double": QVariant.Double,
            "date": QVariant.Date,
        }
        field_type = type_map.get(p.get("field_type", "string").lower(), QVariant.String)
        layer.startEditing()
        layer.addAttribute(QgsField(p["field_name"], field_type))
        layer.commitChanges()
        return f"Field '{p['field_name']}' ({p.get('field_type', 'string')}) added to '{p['layer_name']}'"

    def _cmd_field_statistics(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        result = processing.run("native:basicstatisticsforfields", {
            "INPUT": layer,
            "FIELD_NAME": p["field_name"],
        })
        keys = ["COUNT", "SUM", "MEAN", "MEDIAN", "STD_DEV", "MIN", "MAX", "RANGE", "MINORITY", "MAJORITY"]
        return {k: result[k] for k in keys if k in result}

    # ------------------------------------------------------------------
    # Symbology
    # ------------------------------------------------------------------

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

    def _cmd_apply_graduated_symbology(self, p):
        layer = self._layer(p["layer_name"])
        field_name = p["field_name"]
        classes = int(p.get("classes", 5))
        ramp_name = p.get("color_ramp", "Spectral")
        ramp = QgsStyle.defaultStyle().colorRamp(ramp_name)
        renderer = QgsGraduatedSymbolRenderer(field_name)
        renderer.updateColorRamp(ramp)
        renderer.updateClasses(layer, 0, classes)  # 0 = EqualInterval
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(layer.id())
        return f"Graduated symbology applied to '{p['layer_name']}' on '{field_name}' ({classes} classes, {ramp_name})"

    def _cmd_set_layer_opacity(self, p):
        layer = self._layer(p["layer_name"])
        opacity = float(p["opacity"])
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Opacity must be between 0.0 (transparent) and 1.0 (opaque)")
        layer.setOpacity(opacity)
        layer.triggerRepaint()
        return f"Layer '{p['layer_name']}' opacity set to {opacity}"

    # ------------------------------------------------------------------
    # Spatial analysis
    # ------------------------------------------------------------------

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

    def _cmd_clip_layer(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        mask = self._layer(p["mask_layer"])
        out = p.get("output_name", "Clipped_Output")
        result = processing.run("native:clip", {
            "INPUT": layer,
            "OVERLAY": mask,
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Clip complete. Layer added: {out}"

    def _cmd_dissolve_layer(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        field = p.get("field")
        out = p.get("output_name", "Dissolved_Output")
        result = processing.run("native:dissolve", {
            "INPUT": layer,
            "FIELD": [field] if field else [],
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Dissolve complete. Layer added: {out}"

    def _cmd_merge_layers(self, p):
        import processing
        layers = [self._layer(name) for name in p["layer_names"]]
        out = p.get("output_name", "Merged_Output")
        result = processing.run("native:mergevectorlayers", {
            "LAYERS": layers,
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Merge complete. Layer added: {out}"

    def _cmd_reproject_layer(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        out = p.get("output_name", layer.name() + "_reprojected")
        result = processing.run("native:reprojectlayer", {
            "INPUT": layer,
            "TARGET_CRS": p["target_crs"],
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Reprojected to {p['target_crs']}. Layer added: {out}"

    def _cmd_repair_layer_geometries(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        result = processing.run("native:fixgeometries", {
            "INPUT": layer,
            "OUTPUT": "memory:Fixed_Geometries",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return "Geometry repair complete. Layer added: Fixed_Geometries"

    def _cmd_extract_by_expression(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        out = p.get("output_name", "Extracted_Output")
        result = processing.run("native:extractbyexpression", {
            "INPUT": layer,
            "EXPRESSION": p["expression"],
            "OUTPUT": f"memory:{out}",
        })
        QgsProject.instance().addMapLayer(result["OUTPUT"])
        return f"Extracted features to: {out}"

    # ------------------------------------------------------------------
    # Data processing
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _cmd_save_layer_to_file(self, p):
        import processing
        layer = self._layer(p["layer_name"])
        output_path = p["output_path"]
        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        processing.run("native:savefeatures", {
            "INPUT": layer,
            "OUTPUT": output_path,
        })
        return f"Layer saved to: {output_path}"

    def _cmd_export_map_to_image(self, p):
        output_path = p["output_path"]
        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        iface.mapCanvas().saveAsImage(output_path)
        return f"Map exported to: {output_path}"

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

    # ------------------------------------------------------------------
    # Code execution
    # ------------------------------------------------------------------

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
