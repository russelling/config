"""
buf_marker_timebar.py  --  Buffalo VFX adapter for Marker Timebar on Nuke 16+.

STUDIO_MARKER_TIMEBAR_NUKE17_2026_11_17   (v1: floating bar)
STUDIO_MARKER_TIMEBAR_NUKE17_V2_2026_11_17 (v2: survives deleted widgets)

Upstream marker_timebar.py (bratgot/Timebarmarkers @ d3622f3, unmodified, next
to this file) was written for Nuke 14, where the Viewer is a QGLWidget and the
bar can be a child widget on top of it. On Nuke 17 (Mark, 2026-11-17, Windows):

  * Python sees every Nuke widget as plain QWidget, so upstream's class-name
    search finds no Viewer. The real names are in metaObject().className():
        Viewer_Window   title = the Viewer node name ("Viewer1")
          Image_Window  the picture area
            QWindowContainer   a native GPU surface
  * Anything drawn as a child of Image_Window is UNDER that native surface.
  * Nuke rebuilds Viewer widgets (switching Viewers / panels), and PySide can
    hand back a wrapper for one that is already gone: any call on it raises
    "Internal C++ object (PySide6.QtWidgets.QWidget) already deleted" (v1 hit
    this on Alt+M via upstream's fallback finder and its child-widget init).

So on Nuke 16+ this module:
  * only ever touches widgets shiboken reports alive;
  * finds the active Viewer's Image_Window (title == nuke.activeViewer() name,
    else the largest visible one);
  * builds upstream's TimebarOverlay on a placeholder it owns (never parented
    to a Nuke widget), then floats it as a frameless translucent TOOL window
    owned by Nuke's main window, on the bottom edge of the picture area;
  * follows moves / resizes / fullscreen, hides when the Viewer is not
    showing or Nuke is minimised, and re-attaches (at most once a second) when
    the picture area is rebuilt or another Viewer becomes active;
  * never falls back to upstream's finder; if no live picture area exists,
    opens the marker panel (Alt+Shift+M) and says why in the Script Editor.
On older Nukes (no Viewer_Window / Image_Window classes) upstream is unchanged.

install(marker_timebar) must be called before marker_timebar.register().
"""

try:
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtCore import Qt
    import shiboken6 as _shiboken
except ImportError:                      # Nuke 15 and older
    from PySide2 import QtCore, QtWidgets
    from PySide2.QtCore import Qt
    try:
        import shiboken2 as _shiboken
    except ImportError:
        _shiboken = None

VIEWER_CLASS = "Viewer_Window"
IMAGE_CLASS = "Image_Window"
MAIN_CLASSES = ("Foundry::UI::DockMainWindow",)
FOLLOW_MS = 200          # how often the bar re-checks the picture area
REFIND_MS = 1000         # at most this often, look for a new picture area


def alive(widget):
    """True if the C++ widget behind this wrapper still exists."""
    if widget is None:
        return False
    try:
        if _shiboken is not None and not _shiboken.isValid(widget):
            return False
        widget.metaObject()
        return True
    except (RuntimeError, AttributeError):
        return False


def _cls(widget):
    try:
        return widget.metaObject().className() if alive(widget) else ""
    except RuntimeError:
        return ""


def _visible(widget):
    try:
        return alive(widget) and widget.isVisible()
    except RuntimeError:
        return False


def _area(widget):
    try:
        return widget.width() * widget.height()
    except RuntimeError:
        return 0


def _live_widgets():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return []
    return [w for w in app.allWidgets() if alive(w)]


def is_new_viewer_nuke():
    """Nuke 16+ widget layout (Viewer_Window / Image_Window) present?"""
    return any(_cls(w) in (VIEWER_CLASS, IMAGE_CLASS) for w in _live_widgets())


def active_viewer_name():
    try:
        import nuke
        av = nuke.activeViewer()
        return av.node().name() if av else None
    except Exception:
        return None


def find_image_window(viewer_name=None):
    """The live, visible picture area (Image_Window) of the named / active
    Viewer, else of the largest visible Viewer. None if there is none."""
    viewers = [w for w in _live_widgets()
               if _cls(w) == VIEWER_CLASS and _visible(w)]
    if not viewers:
        return None
    named = []
    for w in viewers:
        try:
            if viewer_name and w.windowTitle() == viewer_name:
                named.append(w)
        except RuntimeError:
            pass
    images = []
    for viewer in (named or viewers):
        try:
            children = viewer.findChildren(QtWidgets.QWidget)
        except RuntimeError:
            continue
        images.extend(c for c in children if _cls(c) == IMAGE_CLASS and _visible(c))
    if not images:
        return None
    return max(images, key=_area)


def find_main_window(fallback=None):
    for w in _live_widgets():
        if _cls(w) in MAIN_CLASSES:
            return w
    try:
        if alive(fallback):
            return fallback.window()
    except RuntimeError:
        pass
    return None


def install(mt):
    """Teach upstream marker_timebar (module `mt`) about Nuke 16+ Viewers."""
    if getattr(mt, "_buf_nuke17_installed", False):
        return
    import nuke

    original_find = mt._find_viewer
    original_show = mt.show
    BaseOverlay = mt.TimebarOverlay

    def _find_viewer():
        if is_new_viewer_nuke():
            return find_image_window(active_viewer_name())   # never upstream's
        return original_find()

    class FloatingOverlay(BaseOverlay):
        """Upstream overlay, floated as a tool window over a native Viewer."""

        def __init__(self, target, main):
            # Upstream's __init__ parents us to its argument and installs an
            # event filter on it. Give it a placeholder we own, never a Nuke
            # widget (a stale Nuke wrapper raised there in v1).
            self._placeholder = QtWidgets.QWidget()
            self._target = target
            self._main = main
            self._last_refind = QtCore.QElapsedTimer()
            self._last_refind.start()
            super(FloatingOverlay, self).__init__(self._placeholder)
            self._placeholder.removeEventFilter(self)
            self.setParent(main, Qt.Tool | Qt.FramelessWindowHint
                           | Qt.NoDropShadowWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_NoSystemBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WA_DeleteOnClose)
            self._watch(target)
            self._watch(main)
            self._follow = QtCore.QTimer(self)
            self._follow.setInterval(FOLLOW_MS)
            self._follow.timeout.connect(self._reposition)
            self._follow.start()
            self._reposition()

        def _watch(self, widget):
            try:
                if alive(widget):
                    widget.installEventFilter(self)
            except RuntimeError:
                pass

        def eventFilter(self, obj, event):
            # Geometry tracking only; clicks land on this window directly.
            try:
                if event.type() in (QtCore.QEvent.Move, QtCore.QEvent.Resize,
                                    QtCore.QEvent.Show, QtCore.QEvent.Hide,
                                    QtCore.QEvent.WindowStateChange):
                    QtCore.QTimer.singleShot(0, self, self._reposition)
            except RuntimeError:
                pass
            return False

        def _retarget(self):
            """The picture area went away or another Viewer is active."""
            if self._last_refind.elapsed() < REFIND_MS:
                return
            self._last_refind.restart()
            fresh = find_image_window(active_viewer_name())
            if fresh is not None and fresh is not self._target:
                self._target = fresh
                self._watch(fresh)

        def _reposition(self):
            try:
                if not alive(self):
                    return
                if not _visible(self._target):
                    self._retarget()
                target = self._target
                main_ok = alive(self._main)
                if (not _visible(target) or not main_ok
                        or self._main.isMinimized()):
                    if self.isVisible():
                        self.hide()
                    return
                # Follow a change of active Viewer.
                name = active_viewer_name()
                if name and target.parentWidget() is not None:
                    owner = target.parentWidget()
                    while owner is not None and _cls(owner) != VIEWER_CLASS:
                        owner = owner.parentWidget()
                    if owner is not None and owner.windowTitle() != name:
                        self._retarget()
                        target = self._target
                        if not _visible(target):
                            return
                height = self._bar.minimumHeight() + 6
                corner = target.mapToGlobal(
                    QtCore.QPoint(0, max(0, target.height() - height)))
                rect = QtCore.QRect(corner, QtCore.QSize(target.width(), height))
                if self.geometry() != rect:
                    self.setGeometry(rect)
                if not self.isVisible():
                    self.show()
            except RuntimeError:
                try:
                    self.hide()
                except RuntimeError:
                    pass

        def closeEvent(self, event):
            try:
                self._follow.stop()
                self._placeholder.deleteLater()
            except RuntimeError:
                pass
            super(FloatingOverlay, self).closeEvent(event)

    def show():
        """Upstream show(), but a floating bar over a Nuke 16+ Viewer.
        (mt.TimebarOverlay is left alone: its __init__ looks itself up by
        name for super(), so it cannot be replaced.)"""
        if not is_new_viewer_nuke():
            return original_show()            # Nuke 15 and older
        image = find_image_window(active_viewer_name())
        main = find_main_window(image)
        if image is None or main is None:
            print("[marker_timebar] no Viewer picture area found - opening "
                  "the marker panel instead. Show a Viewer and press Alt+M again.")
            mt.show_panel()
            return None
        mt._active_markers = mt.load_markers()
        old = mt._overlay
        mt._overlay = None
        if old is not None:
            try:
                if alive(old):
                    old.close()
                    old.deleteLater()
            except RuntimeError:
                pass
        overlay = FloatingOverlay(image, main)
        overlay.set_markers(mt._active_markers)
        try:
            overlay.set_current_frame(int(nuke.frame()))
            overlay.set_range(int(nuke.root().firstFrame()),
                              int(nuke.root().lastFrame()))
        except Exception:
            pass
        mt._overlay = overlay
        mt._start_sync_timer()
        return overlay

    mt._find_viewer = _find_viewer         # also what debug_widgets() reports
    mt.show = show                         # menu command "marker_timebar.show()"
    mt._BufFloatingOverlay = FloatingOverlay
    mt._buf_nuke17_installed = True
    mt._buf_nuke17_version = 2
