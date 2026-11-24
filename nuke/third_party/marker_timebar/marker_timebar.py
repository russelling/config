"""
marker_timebar.py  ·  Nuke 14.1 / Python 3.9 / PySide2
═══════════════════════════════════════════════════════════════════
Semi-transparent timebar overlay docked inside Nuke's viewer canvas,
with named colour-coded markers stored in the .nk script.

HOW IT WORKS
  The overlay is parented directly to Nuke's ViewerGL widget so it
  sits *inside* the viewer as a child widget.  WA_TranslucentBackground
  lets the image show through the semi-opaque bar.  An event-filter on
  the viewer parent keeps the overlay glued to the bottom edge.

USAGE
  Script Editor (one-shot):
      import marker_timebar
      marker_timebar.show()

  Permanent — add to  ~/.nuke/menu.py:
      import marker_timebar
      marker_timebar.register()      # Alt+M opens/refreshes overlay

  The small ⊞ button on the overlay opens the full marker list panel.
  Call  marker_timebar.show_panel()  to open it directly.

SAFETY
  Markers are stored as JSON inside a hidden NoOp node in the .nk.
  If someone opens the script without this tool loaded the NoOp is
  completely harmless — just an invisible node sitting off-screen.
═══════════════════════════════════════════════════════════════════
"""

import json
import sys
from typing import Dict, List, Optional

import nuke
import nukescripts

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtCore import Qt
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt


# ─── Configuration ────────────────────────────────────────────────────────────
PANEL_ID     = "com.tools.MarkerTimebar"
STORAGE_KNOB = "mt_marker_data"
POLL_MS      = 50
OVERLAY_H    = 70    # pixel height of the docked strip (taller for label zone)

MARKER_COLORS = [
    ("Red",    "#E05252"),
    ("Orange", "#E08840"),
    ("Yellow", "#E0C030"),
    ("Lime",   "#70C840"),
    ("Green",  "#40B878"),
    ("Cyan",   "#40C8C8"),
    ("Blue",   "#4880E0"),
    ("Purple", "#9050E0"),
    ("Pink",   "#E050A0"),
    ("White",  "#D8D8D8"),
]

# Paint geometry (px)
_PAD        = 10
_LBL_H      = 18   # height of the label zone above the bar
_BAR_Y      = 30   # top of the filled bar (pushed down to make room for labels)
_BAR_H      = 14
_MRK_BELOW  = 3
_MRK_SIZE   = 5


# ─── Module-level shared state ────────────────────────────────────────────────
_active_markers = []   # type: List[Dict]
_overlay        = None  # type: Optional[TimebarOverlay]
_panel          = None  # type: Optional[MarkerTimebarPanel]
_sync_timer     = None  # type: Optional[QtCore.QTimer]


# ─── Persistence helpers ──────────────────────────────────────────────────────
def _ensure_storage_knob():
    """Add the marker data knob to the Root node if not already present."""
    root = nuke.root()
    if STORAGE_KNOB not in root.knobs():
        root.addKnob(nuke.Tab_Knob("mt_tab", "Marker Timebar"))
        knob = nuke.String_Knob(STORAGE_KNOB, "marker JSON", "[]")
        knob.setFlag(nuke.INVISIBLE)
        root.addKnob(knob)
    return root


def load_markers():
    # type: () -> List[Dict]
    root = nuke.root()
    if STORAGE_KNOB not in root.knobs():
        return []
    try:
        return json.loads(root[STORAGE_KNOB].getValue()) or []
    except Exception:
        return []


def save_markers(markers):
    # type: (List[Dict]) -> None
    _ensure_storage_knob()[STORAGE_KNOB].setValue(json.dumps(markers))


def _commit(markers):
    # type: (List[Dict]) -> None
    """Persist markers and push to every live widget."""
    global _active_markers
    _active_markers = markers
    save_markers(markers)
    _push_to_widgets()


def _push_to_widgets():
    global _overlay, _panel
    if _overlay is not None:
        try:
            _overlay.set_markers(_active_markers)
        except RuntimeError:
            _overlay = None
    if _panel is not None:
        try:
            _panel.set_markers(_active_markers)
        except RuntimeError:
            _panel = None


# ─── Viewer finder ────────────────────────────────────────────────────────────
def _find_viewer():
    # type: () -> Optional[QtWidgets.QWidget]
    """
    Walk Qt's widget tree to find Nuke's viewer canvas.

    Nuke 14.x on Windows exposes its viewer as a plain QGLWidget.
    There are typically two QGLWidgets visible — the viewer and the node
    graph.  We want the one that is taller (the viewer canvas), not the
    wider-but-shallower node graph surface.

    Tier priority (first tier with any hit wins):
      1. Class name contains 'ViewerGL'    - older/Linux Nuke builds
      2. Class name contains 'Viewer'      - any other Viewer-named class
      3. QGLWidget / QOpenGLWidget, tallest candidate excluding the DAG
         (the DAG GL surface tends to be squarer; the viewer is taller
          relative to its width when the timeline is docked below it)
      4. Broad fallback - largest non-chrome child widget
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None

    tiers = [[], [], [], []]  # type: List[List[QtWidgets.QWidget]]

    for w in app.allWidgets():
        if not w.isVisible():
            continue
        ww, wh = w.width(), w.height()
        if ww < 100 or wh < 100:
            continue
        cn = type(w).__name__

        if "ViewerGL" in cn:
            tiers[0].append(w)
        elif "Viewer" in cn:
            tiers[1].append(w)
        elif cn in ("QGLWidget", "QOpenGLWidget") and ww > 300 and wh > 200:
            tiers[2].append(w)
        elif (ww > 400 and wh > 300
              and not isinstance(w, (QtWidgets.QDialog,
                                     QtWidgets.QMainWindow,
                                     QtWidgets.QMenuBar,
                                     QtWidgets.QToolBar,
                                     QtWidgets.QStatusBar,
                                     QtWidgets.QScrollBar,
                                     QtWidgets.QAbstractItemView,
                                     QtWidgets.QHeaderView))
              and w.parent() is not None):
            tiers[3].append(w)

    # Tier 0/1: take the largest
    for tier in tiers[:2]:
        if tier:
            return max(tier, key=lambda w: w.width() * w.height())

    # Tier 2: QGLWidgets — pick the one with the greatest height.
    # The viewer is taller relative to its area than the DAG surface.
    if tiers[2]:
        return max(tiers[2], key=lambda w: w.height())

    # Tier 3: broad fallback
    if tiers[3]:
        return max(tiers[3], key=lambda w: w.width() * w.height())

    return None


def debug_widgets():
    # type: () -> None
    """
    Print all visible widgets to the Script Editor.
    Run this if show() can't find the viewer, then share the output.

        import marker_timebar
        marker_timebar.debug_widgets()
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        print("No QApplication found.")
        return
    hits = []
    for w in app.allWidgets():
        if w.isVisible() and w.width() > 100 and w.height() > 100:
            hits.append((type(w).__name__, w.width(), w.height()))
    hits.sort(key=lambda x: -(x[1] * x[2]))
    print("=" * 60)
    print("Visible widgets > 100x100 (largest first):")
    print("=" * 60)
    for name, ww, hh in hits[:40]:
        tag = "  <-- viewer candidate" if "viewer" in name.lower() else ""
        print("  {:<50} {}x{}{}".format(name, ww, hh, tag))
    print("=" * 60)
    found = _find_viewer()
    if found:
        print("_find_viewer() would pick: {} ({}x{})".format(
            type(found).__name__, found.width(), found.height()))
    else:
        print("_find_viewer() returned None - share the list above for a fix.")


# ─── Add / Edit dialog ────────────────────────────────────────────────────────
class MarkerDialog(QtWidgets.QDialog):
    def __init__(self, frame=1, label="", color="#E05252", parent=None):
        # parent=None makes this a true top-level dialog so it can receive
        # focus independently of the GL viewer hierarchy.  WindowStaysOnTopHint
        # keeps it above Nuke's main window.
        super(MarkerDialog, self).__init__(None)
        self.setWindowTitle("Marker")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setFixedWidth(300)

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(8)

        self._frame_spin = QtWidgets.QSpinBox()
        self._frame_spin.setRange(-9999999, 9999999)
        self._frame_spin.setValue(frame)
        layout.addRow("Frame:", self._frame_spin)

        self._label_edit = QtWidgets.QLineEdit(label)
        self._label_edit.setPlaceholderText("optional label...")
        layout.addRow("Label:", self._label_edit)

        self._color_combo = QtWidgets.QComboBox()
        for cname, hex_c in MARKER_COLORS:
            pix = QtGui.QPixmap(14, 14)
            pix.fill(QtGui.QColor(hex_c))
            self._color_combo.addItem(QtGui.QIcon(pix), cname, hex_c)
            if hex_c.lower() == color.lower():
                self._color_combo.setCurrentIndex(self._color_combo.count() - 1)
        layout.addRow("Colour:", self._color_combo)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)
        self._label_edit.setFocus()

    def get_marker(self):
        # type: () -> Dict
        return {
            "frame": self._frame_spin.value(),
            "label": self._label_edit.text().strip(),
            "color": self._color_combo.currentData(),
        }


# ─── Painted timebar widget ───────────────────────────────────────────────────
class TimebarWidget(QtWidgets.QWidget):

    seek_to_frame         = QtCore.Signal(int)
    request_add_marker    = QtCore.Signal(int)
    request_edit_marker   = QtCore.Signal(int)
    request_delete_marker = QtCore.Signal(int)

    def __init__(self, parent=None):
        super(TimebarWidget, self).__init__(parent)
        h = _BAR_Y + _BAR_H + _MRK_BELOW + _MRK_SIZE * 2 + 14
        self.setMinimumHeight(h)
        self.setMaximumHeight(h + 4)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)

        self._first   = 1
        self._last    = 100
        self._current = 1
        self._markers      = []   # type: List[Dict]
        self._dragging     = False
        self._hover_frame  = None
        self._hover_marker = None
        self._bg_alpha     = 172
        self._thin         = False
        self._font = QtGui.QFont("Consolas", 7)
        self._apply_height()

    def _apply_height(self):
        if self._thin:
            # Thin: bar only, no label zone. ~22 px total.
            h = 4 + 10 + 3 + 5 + 6   # top_pad + bar + below + tri + bottom
        else:
            h = _BAR_Y + _BAR_H + _MRK_BELOW + _MRK_SIZE * 2 + 14
        self.setMinimumHeight(h)
        self.setMaximumHeight(h + 2)

    # ── Public setters ────────────────────────────────────────────────────────
    def set_thin(self, thin):
        # type: (bool) -> None
        self._thin = thin
        self._apply_height()
        self.update()
    def set_bg_alpha(self, alpha):
        # type: (int) -> None
        self._bg_alpha = max(0, min(255, alpha))
        self.update()
    def set_range(self, first, last):
        if first != self._first or last != self._last:
            self._first, self._last = first, last
            self.update()

    def set_current_frame(self, frame):
        if frame != self._current:
            self._current = frame
            self.update()

    def set_markers(self, markers):
        self._markers = markers
        self.update()

    # ── Coordinate helpers ────────────────────────────────────────────────────
    def _f2x(self, frame):
        span = max(1, self._last - self._first)
        return _PAD + (frame - self._first) / float(span) * (self.width() - 2 * _PAD)

    def _x2f(self, x):
        span = max(1, self._last - self._first)
        raw  = self._first + (x - _PAD) / float(self.width() - 2 * _PAD) * span
        return int(round(raw))

    def _clamp(self, frame):
        return max(self._first, min(self._last, frame))

    def _marker_at_x(self, x, tol=6):
        # type: (float, int) -> Optional[int]
        for i, m in enumerate(self._markers):
            if abs(self._f2x(m["frame"]) - x) <= tol:
                return i
        return None

    # ── Paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p  = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w  = self.width()
        fm = p.fontMetrics()

        p.fillRect(0, 0, w, self.height(), QtGui.QColor(12, 12, 12, self._bg_alpha))

        if self._thin:
            self._paint_thin(p, w, fm)
        else:
            self._paint_full(p, w, fm)

        p.end()

    # ── Thin layout (bar only, no label zone) ─────────────────────────────────
    def _paint_thin(self, p, w, fm):
        # Compact constants — local so they don't fight the module-level ones
        bar_y = 4
        bar_h = 10
        mrk_below = 2
        mrk_size  = 4

        bar_rect = QtCore.QRectF(_PAD, bar_y, w - 2 * _PAD, bar_h)
        grad = QtGui.QLinearGradient(0, bar_y, 0, bar_y + bar_h)
        grad.setColorAt(0, QtGui.QColor(55, 55, 55, 210))
        grad.setColorAt(1, QtGui.QColor(35, 35, 35, 210))
        p.fillRect(bar_rect, grad)
        p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90, 160), 1))
        p.drawRect(bar_rect)

        # Ticks (no labels in thin mode — too cramped)
        span = max(1, self._last - self._first)
        step = 1
        for s in (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000, 5000):
            if (w - 2 * _PAD) / float(span) * s >= 60:
                step = s
                break
        p.setFont(self._font)
        start_f = (self._first // step) * step
        if start_f < self._first:
            start_f += step
        f = start_f
        while f <= self._last:
            x = int(self._f2x(f))
            p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90, 150), 1))
            p.drawLine(x, bar_y + 1, x, bar_y + 3)
            f += step

        # Markers
        for i, m in enumerate(self._markers):
            xi    = int(self._f2x(m["frame"]))
            hover = (i == self._hover_marker)
            alpha = 220 if hover else 140
            c     = QtGui.QColor(220, 220, 220, alpha)
            p.setPen(QtGui.QPen(c, 2 if hover else 1))
            p.drawLine(xi, bar_y, xi, bar_y + bar_h)
            apex_y = bar_y + bar_h + mrk_below
            th = mrk_size + (1 if hover else 0)
            tri = QtGui.QPolygon([
                QtCore.QPoint(xi,      apex_y),
                QtCore.QPoint(xi - th, apex_y + th),
                QtCore.QPoint(xi + th, apex_y + th),
            ])
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawPolygon(tri)

        # Hover ghost
        if self._hover_frame is not None and not self._dragging:
            hx = int(self._f2x(self._hover_frame))
            p.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150, 100), 1, Qt.DashLine))
            p.drawLine(hx, bar_y, hx, bar_y + bar_h)

        # Playhead
        cx = int(self._f2x(self._current))
        p.setPen(QtGui.QPen(QtGui.QColor("#F0C030"), 2))
        p.drawLine(cx, bar_y - 2, cx, bar_y + bar_h + 2)

    # ── Full layout (label zone + bar) ────────────────────────────────────────
    def _paint_full(self, p, w, fm):
        lbl_font = QtGui.QFont("Consolas", 7)
        p.setFont(lbl_font)
        lfm     = p.fontMetrics()
        pill_h  = 13
        last_rx = -999

        for m in sorted(self._markers, key=lambda x: x["frame"]):
            mx    = int(self._f2x(m["frame"]))
            col   = QtGui.QColor(m.get("color", "#E05252"))
            label = m.get("label", "")

            if label:
                pill_w = lfm.horizontalAdvance(label) + 10
                pill_x = max(_PAD, mx - pill_w // 2)
                if pill_x < last_rx + 3:
                    pill_x = last_rx + 3
                pill_x = min(pill_x, w - _PAD - pill_w)
                pill_y = (_BAR_Y - pill_h) // 2

                bg = QtGui.QColor(col.red(), col.green(), col.blue(), 200)
                p.setBrush(bg)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, 3, 3)

                lum = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
                p.setPen(QtGui.QColor(0, 0, 0, 230) if lum > 140
                         else QtGui.QColor(255, 255, 255, 230))
                p.drawText(pill_x + 5, pill_y + pill_h - 3, label)
                last_rx = pill_x + pill_w
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(180, 180, 180, 160), 1))
                p.drawLine(mx, 4, mx, _BAR_Y - 2)

        bar_rect = QtCore.QRectF(_PAD, _BAR_Y, w - 2 * _PAD, _BAR_H)
        grad = QtGui.QLinearGradient(0, _BAR_Y, 0, _BAR_Y + _BAR_H)
        grad.setColorAt(0, QtGui.QColor(55, 55, 55, 210))
        grad.setColorAt(1, QtGui.QColor(35, 35, 35, 210))
        p.fillRect(bar_rect, grad)
        p.setBrush(Qt.NoBrush)          # pill loop may have left a colour brush — clear it
        p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90, 160), 1))
        p.drawRect(bar_rect)

        p.setFont(self._font)
        self._paint_ticks(p, w, fm)

        for i, m in enumerate(self._markers):
            self._paint_marker(p, self._f2x(m["frame"]),
                               QtGui.QColor(m.get("color", "#E05252")),
                               i == self._hover_marker)

        if self._hover_frame is not None and not self._dragging:
            hx = int(self._f2x(self._hover_frame))
            p.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150, 120), 1, Qt.DashLine))
            p.drawLine(hx, _BAR_Y, hx, _BAR_Y + _BAR_H)

        cx = int(self._f2x(self._current))
        p.setPen(QtGui.QPen(QtGui.QColor("#F0C030"), 2))
        p.drawLine(cx, _BAR_Y - 4, cx, _BAR_Y + _BAR_H + 4)
        cap = QtGui.QPolygon([
            QtCore.QPoint(cx,     _BAR_Y - 3),
            QtCore.QPoint(cx - 5, _BAR_Y - 11),
            QtCore.QPoint(cx + 5, _BAR_Y - 11),
        ])
        p.setBrush(QtGui.QColor("#F0C030"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(cap)

    def _paint_ticks(self, p, w, fm):
        span = max(1, self._last - self._first)
        step = 1
        for s in (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000, 5000):
            if (w - 2 * _PAD) / float(span) * s >= 50:
                step = s
                break
        start_f = (self._first // step) * step
        if start_f < self._first:
            start_f += step
        f = start_f
        while f <= self._last:
            x   = int(self._f2x(f))
            lbl = str(f)
            lw  = fm.horizontalAdvance(lbl)
            p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90, 150), 1))
            p.drawLine(x, _BAR_Y + 1, x, _BAR_Y + 4)
            tx = max(_PAD, min(w - _PAD - lw, x - lw // 2))
            p.setPen(QtGui.QColor(160, 160, 160, 210))
            p.drawText(tx, _BAR_Y - 2, lbl)
            f += step

    def _paint_marker(self, p, x, color, hover):
        # Line and triangle use the marker's own colour.
        xi    = int(x)
        alpha = 230 if hover else 180
        c     = QtGui.QColor(color.red(), color.green(), color.blue(), alpha)
        p.setPen(QtGui.QPen(c, 2 if hover else 1))
        p.drawLine(xi, _BAR_Y, xi, _BAR_Y + _BAR_H)
        apex_y = _BAR_Y + _BAR_H + _MRK_BELOW
        tri_h  = _MRK_SIZE + (2 if hover else 0)
        tri = QtGui.QPolygon([
            QtCore.QPoint(xi,         apex_y),
            QtCore.QPoint(xi - tri_h, apex_y + tri_h),
            QtCore.QPoint(xi + tri_h, apex_y + tri_h),
        ])
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(tri)

    # ── Mouse events ──────────────────────────────────────────────────────────
    # All handlers call event.accept() to stop events falling through to the
    # underlying QGLWidget (which would trigger Nuke's own timeline popup).
    def mousePressEvent(self, event):
        event.accept()
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.seek_to_frame.emit(self._clamp(self._x2f(event.x())))
        elif event.button() == Qt.RightButton:
            idx = self._marker_at_x(event.x())
            if idx is not None:
                self._show_marker_menu(idx, event.globalPos())
            else:
                self._show_empty_menu(
                    self._clamp(self._x2f(event.x())), event.globalPos()
                )

    def mouseMoveEvent(self, event):
        event.accept()
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.seek_to_frame.emit(self._clamp(self._x2f(event.x())))
        else:
            self._hover_frame  = self._clamp(self._x2f(event.x()))
            self._hover_marker = self._marker_at_x(event.x())
            if self._hover_marker is not None:
                m   = self._markers[self._hover_marker]
                tip = "Frame {}".format(m["frame"])
                if m.get("label"):
                    tip += "  -  " + m["label"]
                self.setToolTip(tip)
            else:
                self.setToolTip("Frame {}".format(self._hover_frame))
        self.update()

    def mouseReleaseEvent(self, event):
        event.accept()
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event):
        event.accept()
        if event.button() == Qt.LeftButton:
            if self._marker_at_x(event.x()) is None:
                self.request_add_marker.emit(self._clamp(self._x2f(event.x())))

    def wheelEvent(self, event):
        event.accept()   # swallow scroll too — stops viewer zoom firing

    def leaveEvent(self, _event):
        self._hover_frame  = None
        self._hover_marker = None
        self.update()

    def _show_empty_menu(self, frame, pos):
        # Immediately fire add-marker — no intermediate context menu step.
        self.request_add_marker.emit(frame)

    def _show_marker_menu(self, idx, pos):
        m    = self._markers[idx]
        menu = QtWidgets.QMenu(self)
        hdr  = menu.addAction(
            "Frame {}".format(m["frame"]) +
            ("  -  " + m["label"] if m.get("label") else "")
        )
        hdr.setEnabled(False)
        menu.addSeparator()
        go = menu.addAction("Go to Frame")
        ed = menu.addAction("Edit Marker...")
        dl = menu.addAction("Delete Marker")
        r  = menu.exec_(pos)
        if r == go:
            self.seek_to_frame.emit(m["frame"])
        elif r == ed:
            self.request_edit_marker.emit(idx)
        elif r == dl:
            self.request_delete_marker.emit(idx)


# ─── Viewer overlay ───────────────────────────────────────────────────────────
class TimebarOverlay(QtWidgets.QWidget):
    """
    Semi-transparent timebar strip parented to Nuke's QGLWidget viewer.

    Key design decisions
    --------------------
    - NO full-viewer child widget: covering QGLWidget with another widget
      kills its GL render pipeline on Windows. Labels live inside this strip.
    - eventFilter intercepts mouse events on the QGLWidget itself when they
      land inside our strip rect, returning True to swallow them before Nuke
      sees them. This is the only reliable way to stop Nuke's timeline popup
      on Windows QGLWidget.
    - Container-level mouse handlers accept all events as a second line of
      defence for events that do reach the overlay widget.
    - MarkerDialog always opened with parent=None + WindowStaysOnTopHint so
      it's a true top-level outside the GL widget hierarchy.
    """

    _OPACITY_LEVELS = [172, 80, 240]
    _OPACITY_TIPS   = [
        "Opacity: semi (click for ghost)",
        "Opacity: ghost (click for solid)",
        "Opacity: solid (click for semi)",
    ]

    _BTN_STYLE = (
        "QPushButton {"
        "  background: rgba(28,28,28,210);"
        "  color: #777;"
        "  border: 1px solid rgba(80,80,80,110);"
        "  border-radius: 3px;"
        "  font-size: 10px;"
        "  padding: 0px 2px;"
        "}"
        "QPushButton:hover { color: #ddd; background: rgba(55,55,55,230); }"
    )

    def __init__(self, viewer):
        # type: (QtWidgets.QWidget) -> None
        super(TimebarOverlay, self).__init__(viewer)
        self._opacity_idx = 0

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

        # Single horizontal row: timebar expands, buttons fixed on the right.
        # All geometry is owned by Qt's layout engine — no manual .move() needed,
        # so minimize/restore can never produce ghost artifacts.
        hbox = QtWidgets.QHBoxLayout(self)
        hbox.setContentsMargins(4, 3, 4, 3)
        hbox.setSpacing(3)

        self._bar = TimebarWidget()
        self._bar.seek_to_frame.connect(lambda f: nuke.frame(f))
        self._bar.request_add_marker.connect(self._on_add)
        self._bar.request_edit_marker.connect(self._on_edit)
        self._bar.request_delete_marker.connect(self._on_delete)
        hbox.addWidget(self._bar, stretch=1)

        def _make_btn(label, tip, slot):
            b = QtWidgets.QPushButton(label, self)
            b.setFixedSize(22, 18)
            b.setToolTip(tip)
            b.setStyleSheet(self._BTN_STYLE)
            b.setCursor(Qt.ArrowCursor)
            b.clicked.connect(slot)
            return b

        self._btn_prev    = _make_btn("<<", "Previous marker",             self._goto_prev)
        self._btn_next    = _make_btn(">>", "Next marker",                 self._goto_next)
        self._btn_add     = _make_btn("+",  "Add marker at current frame", self._add_at_current)
        self._btn_thin    = _make_btn("v",  "Collapse bar height",         self._toggle_thin)
        self._btn_opacity = _make_btn("T",  self._OPACITY_TIPS[0],         self._cycle_opacity)
        self._btn_panel   = _make_btn("=",  "Open full marker list",       show_panel)
        self._btn_close   = _make_btn("x",  "Close overlay",               self.close)

        for btn in (self._btn_prev, self._btn_next, self._btn_add,
                    self._btn_thin, self._btn_opacity,
                    self._btn_panel, self._btn_close):
            hbox.addWidget(btn, stretch=0)

        viewer.installEventFilter(self)
        self.show()
        self.raise_()

    # ── Thin toggle ───────────────────────────────────────────────────────────
    def _toggle_thin(self):
        thin = not self._bar._thin
        self._bar.set_thin(thin)
        self._btn_thin.setText("^" if thin else "v")
        self._btn_thin.setToolTip("Expand bar height" if thin else "Collapse bar height")
        self._reposition()

    # ── Add marker at current frame ───────────────────────────────────────────
    def _add_at_current(self):
        self._on_add(int(nuke.frame()))

    # ── Opacity cycle ─────────────────────────────────────────────────────────
    def _cycle_opacity(self):
        self._opacity_idx = (self._opacity_idx + 1) % len(self._OPACITY_LEVELS)
        self._btn_opacity.setToolTip(self._OPACITY_TIPS[self._opacity_idx])
        self._bar.set_bg_alpha(self._OPACITY_LEVELS[self._opacity_idx])

    # ── Data interface ────────────────────────────────────────────────────────
    @property
    def bar(self):
        return self._bar

    def set_markers(self, markers):
        self._bar.set_markers(markers)

    def set_current_frame(self, frame):
        self._bar.set_current_frame(frame)

    def set_range(self, first, last):
        self._bar.set_range(first, last)

    # ── Event filter on parent QGLWidget ──────────────────────────────────────
    # On Windows, QGLWidget events bypass normal child-widget routing.
    # We intercept them here at the source and swallow anything inside our rect.
    _SWALLOW_TYPES = {
        QtCore.QEvent.MouseButtonPress,
        QtCore.QEvent.MouseButtonRelease,
        QtCore.QEvent.MouseButtonDblClick,
        QtCore.QEvent.Wheel,
    }

    def eventFilter(self, obj, event):
        t = event.type()
        if t in (QtCore.QEvent.Resize,
                 QtCore.QEvent.Show,
                 QtCore.QEvent.WindowActivate,
                 QtCore.QEvent.WindowStateChange):
            QtCore.QTimer.singleShot(0, self._reposition)
            return False
        if t in self._SWALLOW_TYPES:
            try:
                if self.geometry().contains(event.pos()):
                    return True
            except Exception:
                pass
        return False

    def _reposition(self):
        """Snap overlay to viewer bottom edge. Layout handles all child positions."""
        try:
            p = self.parent()
            if p is None:
                return
            pw, ph = p.width(), p.height()
            oh = self._bar.minimumHeight() + 6
            self.setGeometry(0, ph - oh, pw, oh)
        except Exception:
            pass

    # ── Container-level mouse handlers (second line of defence) ──────────────
    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
        p.end()

    # ── Marker CRUD ───────────────────────────────────────────────────────────
    def _on_add(self, frame):
        dlg = MarkerDialog(frame=frame)
        dlg.setWindowTitle("Add Marker")
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            m       = dlg.get_marker()
            markers = [x for x in _active_markers if x["frame"] != m["frame"]]
            markers.append(m)
            markers.sort(key=lambda x: x["frame"])
            _commit(markers)

    def _on_edit(self, idx):
        if idx < 0 or idx >= len(_active_markers):
            return
        m   = _active_markers[idx]
        dlg = MarkerDialog(frame=m["frame"], label=m.get("label", ""),
                           color=m.get("color", "#E05252"))
        dlg.setWindowTitle("Edit Marker")
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            nm      = dlg.get_marker()
            markers = [x for x in _active_markers
                       if x is not m and x["frame"] != nm["frame"]]
            markers.append(nm)
            markers.sort(key=lambda x: x["frame"])
            _commit(markers)

    def _on_delete(self, idx):
        if 0 <= idx < len(_active_markers):
            markers = list(_active_markers)
            markers.pop(idx)
            _commit(markers)

    def _goto_prev(self):
        cf   = int(nuke.frame())
        prev = [m for m in _active_markers if m["frame"] < cf]
        if prev:
            nuke.frame(prev[-1]["frame"])

    def _goto_next(self):
        cf  = int(nuke.frame())
        nxt = [m for m in _active_markers if m["frame"] > cf]
        if nxt:
            nuke.frame(nxt[0]["frame"])


# ─── Full floating marker panel ───────────────────────────────────────────────
class MarkerTimebarPanel(QtWidgets.QWidget):
    """
    Full floating panel: toolbar, timebar bar and sortable marker table.
    Opens from the + button on the overlay, or via show_panel().
    Has its own poll timer so it works standalone if no overlay is live.
    """

    def __init__(self, parent=None):
        super(MarkerTimebarPanel, self).__init__(parent)
        self.setWindowTitle("Marker Timebar")
        self.setMinimumWidth(540)
        self.setMinimumHeight(210)
        self._own_markers = list(_active_markers)
        self._last_range  = (None, None)
        self._last_cf     = None
        self._build_ui()
        self._rebuild_table()
        self._bar.set_markers(self._own_markers)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Toolbar
        tb = QtWidgets.QHBoxLayout()
        tb.setSpacing(4)
        self._lbl_frame = QtWidgets.QLabel("Frame: -")
        self._lbl_frame.setStyleSheet(
            "color:#F0C030; font-family:Consolas; font-size:11px; font-weight:bold;"
        )
        tb.addWidget(self._lbl_frame)
        self._lbl_range = QtWidgets.QLabel("")
        self._lbl_range.setStyleSheet(
            "color:#666; font-family:Consolas; font-size:10px;"
        )
        tb.addWidget(self._lbl_range)
        tb.addStretch()

        def _btn(text, tip, slot):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.setFixedHeight(22)
            b.clicked.connect(slot)
            return b

        tb.addWidget(_btn("<<", "Previous marker", self._goto_prev))
        tb.addWidget(_btn(">>", "Next marker",     self._goto_next))
        tb.addSpacing(6)
        tb.addWidget(_btn("+ Marker", "Add at current frame", self._add_at_current))
        tb.addWidget(_btn("Clear All", "Delete all markers",  self._clear_all))
        root.addLayout(tb)

        # Timebar bar
        self._bar = TimebarWidget(self)
        self._bar.seek_to_frame.connect(lambda f: nuke.frame(f))
        self._bar.request_add_marker.connect(self._add_at_frame)
        self._bar.request_edit_marker.connect(self._edit_marker)
        self._bar.request_delete_marker.connect(self._delete_marker)
        root.addWidget(self._bar)

        # Marker table
        self._table = QtWidgets.QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Frame", "Label", "Colour"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setMaximumHeight(140)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        self._table.doubleClicked.connect(self._on_table_dbl)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_ctx)
        root.addWidget(self._table)

    # ── Data interface (called by _push_to_widgets) ───────────────────────────
    def set_markers(self, markers):
        self._own_markers = list(markers)
        self._bar.set_markers(self._own_markers)
        self._rebuild_table()

    # ── Poll ─────────────────────────────────────────────────────────────────
    def _poll(self):
        try:
            cf = int(nuke.frame())
            if cf != self._last_cf:
                self._last_cf = cf
                self._bar.set_current_frame(cf)
                self._lbl_frame.setText("Frame: {}".format(cf))
                self._highlight_row(cf)
            ff = int(nuke.root().firstFrame())
            lf = int(nuke.root().lastFrame())
            if (ff, lf) != self._last_range:
                self._last_range = (ff, lf)
                self._bar.set_range(ff, lf)
                self._lbl_range.setText("[{} - {}]".format(ff, lf))
        except Exception:
            pass

    # ── CRUD ─────────────────────────────────────────────────────────────────
    def _add_at_current(self):
        self._add_at_frame(int(nuke.frame()))

    def _add_at_frame(self, frame):
        dlg = MarkerDialog(frame=frame, parent=self)
        dlg.setWindowTitle("Add Marker")
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            m       = dlg.get_marker()
            markers = [x for x in self._own_markers if x["frame"] != m["frame"]]
            markers.append(m)
            markers.sort(key=lambda x: x["frame"])
            _commit(markers)

    def _edit_marker(self, idx):
        if idx < 0 or idx >= len(self._own_markers):
            return
        m   = self._own_markers[idx]
        dlg = MarkerDialog(
            frame=m["frame"], label=m.get("label", ""),
            color=m.get("color", "#E05252"), parent=self,
        )
        dlg.setWindowTitle("Edit Marker")
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            nm      = dlg.get_marker()
            markers = [x for x in self._own_markers if x is not m and x["frame"] != nm["frame"]]
            markers.append(nm)
            markers.sort(key=lambda x: x["frame"])
            _commit(markers)

    def _delete_marker(self, idx):
        if 0 <= idx < len(self._own_markers):
            markers = list(self._own_markers)
            markers.pop(idx)
            _commit(markers)

    def _clear_all(self):
        if not self._own_markers:
            return
        r = QtWidgets.QMessageBox.question(
            self, "Clear All Markers",
            "Remove all markers from this script?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if r == QtWidgets.QMessageBox.Yes:
            _commit([])

    def _goto_prev(self):
        cf   = int(nuke.frame())
        prev = [m for m in self._own_markers if m["frame"] < cf]
        if prev:
            nuke.frame(prev[-1]["frame"])

    def _goto_next(self):
        cf  = int(nuke.frame())
        nxt = [m for m in self._own_markers if m["frame"] > cf]
        if nxt:
            nuke.frame(nxt[0]["frame"])

    # ── Table helpers ─────────────────────────────────────────────────────────
    def _rebuild_table(self):
        self._table.setRowCount(0)
        for m in self._own_markers:
            row = self._table.rowCount()
            self._table.insertRow(row)
            fi = QtWidgets.QTableWidgetItem(str(m["frame"]))
            fi.setTextAlignment(Qt.AlignCenter)
            li = QtWidgets.QTableWidgetItem(m.get("label", ""))
            ci = QtWidgets.QTableWidgetItem()
            ci.setTextAlignment(Qt.AlignCenter)
            pix = QtGui.QPixmap(14, 14)
            pix.fill(QtGui.QColor(m.get("color", "#E05252")))
            ci.setIcon(QtGui.QIcon(pix))
            self._table.setItem(row, 0, fi)
            self._table.setItem(row, 1, li)
            self._table.setItem(row, 2, ci)

    def _highlight_row(self, frame):
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and int(item.text()) == frame:
                self._table.selectRow(row)
                return
        self._table.clearSelection()

    def _on_table_dbl(self, index):
        row = index.row()
        if 0 <= row < len(self._own_markers):
            nuke.frame(self._own_markers[row]["frame"])

    def _on_table_ctx(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._own_markers):
            return
        menu = QtWidgets.QMenu(self)
        go   = menu.addAction("Go to Frame")
        ed   = menu.addAction("Edit...")
        dl   = menu.addAction("Delete")
        r    = menu.exec_(self._table.viewport().mapToGlobal(pos))
        if r == go:
            nuke.frame(self._own_markers[row]["frame"])
        elif r == ed:
            self._edit_marker(row)
        elif r == dl:
            self._delete_marker(row)

    def closeEvent(self, event):
        self._timer.stop()
        super(MarkerTimebarPanel, self).closeEvent(event)


# ─── Module-level sync timer (drives the overlay) ─────────────────────────────
def _start_sync_timer():
    global _sync_timer
    if _sync_timer is not None:
        try:
            if _sync_timer.isActive():
                return
        except RuntimeError:
            pass
    _sync_timer = QtCore.QTimer()
    _sync_timer.setInterval(POLL_MS)
    _sync_timer.timeout.connect(_poll_nuke)
    _sync_timer.start()


def _poll_nuke():
    global _overlay, _sync_timer
    try:
        cf = int(nuke.frame())
        ff = int(nuke.root().firstFrame())
        lf = int(nuke.root().lastFrame())
    except Exception:
        return

    if _overlay is not None:
        try:
            _overlay.set_current_frame(cf)
            _overlay.set_range(ff, lf)
            # Keep overlay on top — Nuke's GL repaints can re-bury child widgets
            _overlay.raise_()
        except RuntimeError:
            _overlay = None

    if _overlay is None and _panel is None:
        try:
            _sync_timer.stop()
        except Exception:
            pass


# ─── App-level event filter — kills Nuke's native timeline popup ──────────────
class _OverlayGuard(QtCore.QObject):
    """
    Installed on QApplication to kill Nuke's native timeline popup.
    Only swallows events whose TARGET is the viewer QGLWidget itself —
    never events aimed at our own overlay or its children.
    """

    def __init__(self, viewer):
        # type: (QtWidgets.QWidget) -> None
        super(_OverlayGuard, self).__init__()
        self._viewer = viewer
        self._types  = {
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonRelease,
        }

    def eventFilter(self, obj, event):
        # Only act on the raw viewer widget — let everything else through
        if obj is not self._viewer:
            return False
        if event.type() not in self._types:
            return False
        if _overlay is None:
            return False
        try:
            overlay_screen = QtCore.QRect(
                _overlay.mapToGlobal(QtCore.QPoint(0, 0)),
                _overlay.size(),
            )
            if hasattr(event, "globalPos") and overlay_screen.contains(event.globalPos()):
                return True   # swallow — Nuke never sees this click
        except Exception:
            pass
        return False


_app_guard = None   # type: Optional[_OverlayGuard]


def _install_app_guard(viewer):
    global _app_guard
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    # Remove old guard if viewer changed
    if _app_guard is not None:
        try:
            app.removeEventFilter(_app_guard)
        except Exception:
            pass
    _app_guard = _OverlayGuard(viewer)
    app.installEventFilter(_app_guard)


# ─── Public API ───────────────────────────────────────────────────────────────
def show():
    """
    Attach the semi-transparent overlay to Nuke's viewer.
    Falls back to show_panel() if no viewer widget can be found.
    Safe to call multiple times — detaches and re-attaches cleanly.
    """
    global _overlay, _active_markers

    _active_markers = load_markers()

    # Tear down any existing overlay
    if _overlay is not None:
        try:
            _overlay.close()
            _overlay.deleteLater()
        except RuntimeError:
            pass
        _overlay = None

    viewer = _find_viewer()
    if viewer is None:
        nuke.message(
            "Marker Timebar: could not find a Viewer pane.\n\n"
            "Open a Viewer node in a pane and run\n"
            "   marker_timebar.show()\n"
            "again, or use  marker_timebar.show_panel()  for the floating panel."
        )
        show_panel()
        return

    _install_app_guard(viewer)   # install once per viewer — stays active for the session

    _overlay = TimebarOverlay(viewer)
    _overlay.set_markers(_active_markers)

    try:
        _overlay.set_current_frame(int(nuke.frame()))
        _overlay.set_range(
            int(nuke.root().firstFrame()),
            int(nuke.root().lastFrame()),
        )
    except Exception:
        pass

    _start_sync_timer()


def show_panel():
    """Open the full floating marker-list panel."""
    global _panel
    if _panel is not None:
        try:
            _panel.show()
            _panel.raise_()
            _panel.activateWindow()
            return
        except RuntimeError:
            _panel = None

    _panel = MarkerTimebarPanel()
    _panel.setWindowFlags(
        Qt.Window
        | Qt.WindowStaysOnTopHint
        | Qt.WindowCloseButtonHint
        | Qt.WindowMinimizeButtonHint
    )
    _panel.resize(920, 260)
    _panel.show()
    _start_sync_timer()


def register():
    """
    Wire the tool into Nuke.  Call once from  ~/.nuke/menu.py :

        import marker_timebar
        marker_timebar.register()

    Adds:
      Alt+M        -> show()        (attach overlay to viewer)
      Alt+Shift+M  -> show_panel()  (open the full floating panel)

    Also registers script-load / script-save callbacks so markers persist.
    """
    m = nuke.menu("Nuke").addMenu("Custom Tools")
    m.addCommand("Marker Timebar",       "marker_timebar.show()",       "alt+m")
    m.addCommand("Marker Timebar Panel", "marker_timebar.show_panel()", "alt+shift+m")

    def _on_load():
        global _active_markers
        _active_markers = load_markers()
        _push_to_widgets()

    nuke.addOnScriptLoad(_on_load,                              nodeClass="Root")
    nuke.addOnScriptSave(lambda: save_markers(_active_markers), nodeClass="Root")
