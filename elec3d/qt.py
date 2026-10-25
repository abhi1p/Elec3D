# -*- coding: utf-8 -*-
"""Qt import shim.

FreeCAD exposes Qt through its own ``PySide`` compatibility package (Qt6 in
FreeCAD 1.1).  Outside FreeCAD -- for example when previewing the viewer with
``tools/preview_ui.py`` -- plain PySide6 or PySide2 is used instead.  Importing
Qt through this module keeps the rest of the code free of that noise.
"""

QT_BINDING = None

try:                                     # FreeCAD's shim
    from PySide import QtCore, QtGui, QtWidgets
    QT_BINDING = "PySide (FreeCAD)"
except ImportError:                      # pragma: no cover - standalone use
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        QT_BINDING = "PySide6"
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        QT_BINDING = "PySide2"

Qt = QtCore.Qt
Signal = QtCore.Signal
Slot = QtCore.Slot

__all__ = ["QtCore", "QtGui", "QtWidgets", "Qt", "Signal", "Slot",
           "QT_BINDING", "exec_dialog", "enum",
           "NoWheelComboBox", "NoWheelSpinBox", "NoWheelDoubleSpinBox"]



class NoWheelComboBox(QtWidgets.QComboBox):
    """Combo box whose current value is never changed by the mouse wheel."""

    def wheelEvent(self, event):
        # Ignored wheel events propagate to the containing scroll area.
        event.ignore()


class NoWheelSpinBox(QtWidgets.QSpinBox):
    """Integer spin box whose value is never changed by the mouse wheel."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """Float spin box whose value is never changed by the mouse wheel."""

    def wheelEvent(self, event):
        event.ignore()


def exec_dialog(dialog):
    """``exec()`` on Qt6, ``exec_()`` on the older bindings."""
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return runner()


def enum(path, *names):
    """Look up an enum value that moved between Qt5 and Qt6.

    Qt6 scopes enums (``Qt.PenStyle.SolidLine``) while Qt5 also exposes them
    flat (``Qt.SolidLine``); PySide6 accepts both, PySide2 only the flat form.
    ``enum(Qt, "PenStyle", "SolidLine")`` works everywhere.
    """
    obj = path
    for name in names:
        nxt = getattr(obj, name, None)
        if nxt is None:
            return getattr(path, names[-1])
        obj = nxt
    return obj
