"""QGIS 3.x / 4.x enum compatibility shim.

QGIS 4.x moves most enums into the Qgis namespace with fully-qualified forms.
This module resolves the correct value at import time so the plugin stays clean.
Strategy: try the new form first, fall back to the old one.
"""

from qgis.core import (
    Qgis,
    QgsLayoutExporter,
    QgsMapLayer,
    QgsProcessingParameterDefinition,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QIODevice

# ── Layer types ───────────────────────────────────────────────────────
try:
    LAYER_VECTOR = Qgis.LayerType.Vector
except AttributeError:
    LAYER_VECTOR = QgsMapLayer.VectorLayer

try:
    LAYER_RASTER = Qgis.LayerType.Raster
except AttributeError:
    LAYER_RASTER = QgsMapLayer.RasterLayer

# ── Message levels ────────────────────────────────────────────────────
try:
    MSG_INFO = Qgis.MessageLevel.Info
except AttributeError:
    MSG_INFO = Qgis.Info

try:
    MSG_WARNING = Qgis.MessageLevel.Warning
except AttributeError:
    MSG_WARNING = Qgis.Warning

try:
    MSG_CRITICAL = Qgis.MessageLevel.Critical
except AttributeError:
    MSG_CRITICAL = Qgis.Critical

# ── Geometry types ────────────────────────────────────────────────────
try:
    GEOM_POLYGON = Qgis.GeometryType.Polygon
except AttributeError:
    GEOM_POLYGON = QgsWkbTypes.PolygonGeometry

try:
    GEOM_LINE = Qgis.GeometryType.Line
except AttributeError:
    GEOM_LINE = QgsWkbTypes.LineGeometry

# ── Layout export result ──────────────────────────────────────────────
try:
    LAYOUT_SUCCESS = Qgis.LayoutResult.Success
except AttributeError:
    LAYOUT_SUCCESS = QgsLayoutExporter.Success

# ── Processing parameter flags ────────────────────────────────────────
try:
    PROCESSING_OPTIONAL = Qgis.ProcessingParameterFlag.Optional
except AttributeError:
    PROCESSING_OPTIONAL = QgsProcessingParameterDefinition.FlagOptional

# ── Qt IO enum ────────────────────────────────────────────────────────
try:
    IODEVICE_WRITEONLY = QIODevice.OpenModeFlag.WriteOnly
except AttributeError:
    IODEVICE_WRITEONLY = QIODevice.WriteOnly
