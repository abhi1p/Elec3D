# -*- coding: utf-8 -*-
"""UI palette and Qt stylesheet.

The colours are the ``:root`` / ``body.light`` custom properties of the
reference HTML viewer, so the FreeCAD panel and the web app look like the same
program.  Layer colours themselves live in :mod:`elec3d.layers`; this module
only covers the chrome around the canvas.
"""

DARK = {
    "bg": "#0E1014", "canvas": "#101216", "panel_line": "#23262E",
    "panel": "#14171E", "panel_header": "#1A1F29", "panel_border": "#242935",
    "text": "#D7DAE0", "text_bright": "#E9ECF2", "text_dim": "#8A93A3",
    "text_faint": "#6F7683", "label": "#8B94A5", "value": "#C9D1DE",
    "ctrl_bg": "#1A1E26", "ctrl_border": "#323847", "ctrl_text": "#C9D1DE",
    "ctrl_hover": "#232936", "seg_active_bg": "#2C3444", "seg_active_text": "#FFFFFF",
    "row_hover": "#1A1F2A", "row_sel": "#232A38",
    "status_bg": "#0E1014", "grid": "#1C1F26", "grid_major": "#262B35",
    "crosshair": "#E6ECF7", "crosshair_halo": "#080A0D", "axis": "#3A4354",
    "ok": "#7FB98A", "warn": "#C9A227", "danger": "#E08A8A",
    "tag_border": "#3A4150", "tag_text": "#9AA3B2", "accent": "#5C7CFA",
    "selection": "#5C7CFA", "hover": "#E9ECF2",
}

LIGHT = {
    "bg": "#EDEFF2", "canvas": "#FAFAFB", "panel_line": "#D3D8E0",
    "panel": "#FFFFFF", "panel_header": "#EEF1F5", "panel_border": "#D7DCE4",
    "text": "#2B303A", "text_bright": "#12151B", "text_dim": "#59616E",
    "text_faint": "#7A8390", "label": "#5F6874", "value": "#22262E",
    "ctrl_bg": "#FFFFFF", "ctrl_border": "#C6CCD6", "ctrl_text": "#22262E",
    "ctrl_hover": "#E8ECF2", "seg_active_bg": "#D4DEF2", "seg_active_text": "#12151B",
    "row_hover": "#EBEFF5", "row_sel": "#D9E3F7",
    "status_bg": "#FAFAFB", "grid": "#E3E6EC", "grid_major": "#D5DAE3",
    "crosshair": "#394150", "crosshair_halo": "#FFFFFF", "axis": "#9AA3B2",
    "ok": "#2E7D46", "warn": "#966A05", "danger": "#C0392B",
    "tag_border": "#C6CCD6", "tag_text": "#59616E", "accent": "#3457D5",
    "selection": "#3457D5", "hover": "#12151B",
}

MONO = '"Cascadia Code", "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace'


def application_theme(default="light"):
    """Return ``dark`` or ``light`` from FreeCAD/Qt's current palette.

    The hosted viewer deliberately follows the application theme instead of
    keeping an independent add-on preference.  Looking at the window colour is
    reliable for both FreeCAD's light/dark styles and standalone Qt previews.
    """
    try:
        from .qt import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app is None:
            return default

        # Prefer FreeCAD's main-window palette when available because FreeCAD
        # themes can be supplied by a stylesheet rather than only by the base
        # QApplication palette.
        palette_obj = None
        try:
            import FreeCADGui as Gui
            main = Gui.getMainWindow()
            if main is not None:
                palette_obj = main.palette()
        except Exception:
            pass
        if palette_obj is None:
            palette_obj = app.palette()

        colour = palette_obj.window().color()
        return "dark" if colour.lightness() < 128 else "light"
    except Exception:
        return default


def palette(theme="dark"):
    return LIGHT if theme == "light" else DARK


def stylesheet(theme="dark"):
    """Qt style sheet for the viewer window."""
    c = palette(theme)
    return """
    QWidget#elec3dViewer, QWidget#elec3dSidebar, QWidget#elec3dHeader {
        background: %(bg)s; color: %(text)s;
        font-family: %(mono)s; font-size: 12px;
    }
    QWidget#elec3dSidebar { border-right: 1px solid %(panel_line)s; }
    QFrame#toolRail {
        background: %(bg)s; color: %(text)s;
        border-left: 1px solid %(panel_line)s; border-right: none;
    }
    QWidget#elec3dHeader { border-bottom: 1px solid %(panel_line)s; }
    QScrollArea, QScrollArea > QWidget > QWidget { background: %(bg)s; }

    QLabel { color: %(text)s; background: transparent; }
    QLabel#sectionLabel {
        font-size: 10px; letter-spacing: 2px; color: %(label)s;
        background: %(panel_header)s; border: 1px solid %(panel_border)s;
        border-radius: 4px; padding: 5px 8px;
    }
    QLabel#title { font-size: 14px; color: %(text_bright)s; }
    QLabel#tag {
        color: %(tag_text)s; border: 1px solid %(tag_border)s;
        border-radius: 3px; padding: 1px 6px; font-size: 10.5px;
    }
    QLabel#dim   { color: %(text_dim)s; }
    QLabel#faint { color: %(text_faint)s; font-size: 11px; }
    QLabel#value { color: %(value)s; }
    QLabel#kind  { color: %(text_bright)s; font-size: 12.5px; }

    QPushButton {
        background: %(ctrl_bg)s; color: %(ctrl_text)s;
        border: 1px solid %(ctrl_border)s; border-radius: 4px;
        padding: 4px 10px; font-size: 11.5px;
    }
    QPushButton:hover   { background: %(ctrl_hover)s; }
    QPushButton:pressed { background: %(seg_active_bg)s; }
    QPushButton:checked {
        background: %(seg_active_bg)s; color: %(seg_active_text)s;
        border-color: %(accent)s;
    }
    QPushButton#fileMenuButton {
        padding-left: 10px; padding-right: 10px;
    }

    /* File popup.  Give menu entries a little more vertical and horizontal
       breathing room than the native FreeCAD menu style so the popup reads as
       a separate command surface rather than another row of header buttons. */
    QMenu {
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(panel_border)s;
        padding: 5px 0; font-family: %(mono)s; font-size: 11.5px;
    }
    QMenu::item {
        padding: 7px 30px 7px 12px; margin: 1px 4px;
        border-radius: 3px;
    }
    QMenu::item:selected {
        background: %(row_sel)s; color: %(text_bright)s;
    }
    QMenu::item:disabled { color: %(text_faint)s; }
    QMenu::separator {
        height: 1px; background: %(panel_line)s; margin: 5px 8px;
    }
    QToolButton#toolRailButton {
        background: transparent; color: %(ctrl_text)s;
        border: 1px solid transparent; border-radius: 3px; padding: 0;
    }
    QToolButton#toolRailButton:hover { background: %(row_hover)s; }
    QToolButton#toolRailButton:checked {
        background: %(seg_active_bg)s; border-color: %(accent)s;
    }
    QPushButton#sectionHeader {
        background: %(panel_header)s; color: %(label)s;
        border: 1px solid %(panel_border)s; border-radius: 4px;
        padding: 5px 8px; text-align: left;
        font-size: 10px; letter-spacing: 2px;
    }
    QPushButton#sectionHeader:hover { background: %(ctrl_hover)s; }
    QPushButton#rowButton {
        background: transparent; border: none; border-radius: 3px;
        padding: 3px 6px; text-align: left; color: %(text)s; font-size: 11.5px;
    }
    QPushButton#rowButton:hover   { background: %(row_hover)s; }
    QPushButton#rowButton:checked { background: %(row_sel)s; color: %(text_bright)s; }

    /* Properties: "Basic Properties" bar and the collapsible "Fields" bar.
       Both reuse the section-header chrome one level in, so the panel reads as
       a hierarchy rather than as a flat run of labels. */
    QLabel#groupTitle {
        background: %(panel_header)s; border: 1px solid %(panel_border)s;
        border-radius: 3px; color: %(label)s;
        padding: 4px 6px; font-size: 10px; letter-spacing: 1px;
    }
    QPushButton#groupHeader {
        background: %(panel_header)s; color: %(label)s;
        border: 1px solid %(panel_border)s; border-radius: 3px;
        padding: 4px 6px; text-align: left;
        font-size: 10px; letter-spacing: 1px;
    }
    QPushButton#groupHeader:hover { color: %(text)s; background: %(ctrl_hover)s; }

    /* Design layers.  A stack row is a whole clickable strip, so the frame --
       not a child label -- carries the hover and active states. */
    QFrame#stackRow {
        border: 1px solid transparent; border-radius: 3px; background: transparent;
    }
    QFrame#stackRow:hover { background: %(row_hover)s; }
    QFrame#stackRow[active="true"] {
        background: %(row_sel)s; border-color: %(accent)s;
    }
    QLabel#stackName  { color: %(text)s; font-size: 11.5px; }
    QLabel#stackCount { color: %(text_faint)s; font-size: 11px; }
    QPushButton#removeButton {
        background: transparent; border: none; color: %(text_faint)s;
        padding: 0 3px; font-size: 13px; min-width: 14px;
    }
    QPushButton#removeButton:hover   { color: %(danger)s; background: transparent; }
    QPushButton#removeButton:pressed { background: transparent; }

    QCheckBox { color: %(text)s; font-size: 11.5px; spacing: 6px; padding: 2px 0; }
    QCheckBox::indicator {
        width: 12px; height: 12px; border-radius: 3px;
        border: 1px solid %(ctrl_border)s; background: %(ctrl_bg)s;
    }
    QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }

    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
        background: %(ctrl_bg)s; color: %(ctrl_text)s;
        border: 1px solid %(ctrl_border)s; border-radius: 4px;
        padding: 2px 6px; font-size: 11.5px; selection-background-color: %(accent)s;
    }
    QComboBox QAbstractItemView {
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(ctrl_border)s; selection-background-color: %(row_sel)s;
    }
    QComboBox::drop-down { border: none; width: 14px; }
    /* Qt draws no arrow once the drop-down is restyled, which left the control
       looking like a read-only text box.  Build a caret out of borders rather
       than shipping an image asset that would need a copy per theme. */
    QComboBox::down-arrow {
        image: none; width: 0; height: 0; margin-right: 4px;
        border-left: 3px solid transparent;
        border-right: 3px solid transparent;
        border-top: 4px solid %(text_dim)s;
    }
    QComboBox::down-arrow:disabled { border-top-color: %(text_faint)s; }

    QFrame#separator { background: %(panel_line)s; max-height: 1px; border: none; }

    /* Board solid card: one compact summary of the imported body plus a
       per-layer table showing where each copper plane meets the solid. */
    QFrame#boardCard {
        background: %(panel)s; border: 1px solid %(panel_border)s; border-radius: 4px;
    }
    QLabel#boardName { color: %(text_bright)s; font-size: 12px; }
    QLabel#boardMeta { color: %(text_dim)s; font-size: 11px; }
    QLabel#layerOk   { color: %(ok)s; font-size: 11px; }
    QLabel#layerWarn { color: %(warn)s; font-size: 11px; }
    QLabel#layerBad  { color: %(danger)s; font-size: 11px; }
    QPushButton#primaryButton {
        background: %(accent)s; color: #FFFFFF; border-color: %(accent)s;
    }
    QPushButton#primaryButton:hover { background: %(selection)s; }
    QPushButton#linkButton {
        background: transparent; border: none; color: %(accent)s;
        padding: 2px 4px; text-align: left;
    }
    QPushButton#linkButton:hover { text-decoration: underline; background: transparent; }
    QPushButton#linkButton:disabled { color: %(text_faint)s; }
    QFrame#boardCard QPushButton::menu-indicator { image: none; width: 0; }
    QWidget#layerBar {
        background: %(status_bg)s;
        border-top: 1px solid %(panel_line)s;
    }
    QScrollArea#layerBarScroll, QScrollArea#layerBarScroll > QWidget > QWidget,
    QWidget#layerBarHost {
        background: %(status_bg)s; border: none;
    }
    QPushButton#layerTabButton {
        background: transparent; color: %(text_dim)s;
        border: 1px solid transparent; border-radius: 2px;
        padding: 2px 8px; min-height: 18px; text-align: left;
    }
    QPushButton#layerTabButton:hover { background: %(row_hover)s; color: %(text)s; }
    QPushButton#layerTabButton:checked {
        background: %(row_sel)s; color: %(text_bright)s;
        border-color: %(accent)s;
    }
    QPushButton#layerTabButton[layerVisible="false"] { color: %(text_faint)s; }
    QWidget#statusBar {
        background: %(status_bg)s; border-top: 1px solid %(panel_line)s;
    }
    QLabel#statusText { color: %(text_dim)s; font-size: 11px; }
    QLabel#hoverText  { color: %(text)s; font-size: 11px; }

    QScrollBar:vertical {
        background: transparent; width: 9px; margin: 0;
    }
    QScrollBar:horizontal {
        background: transparent; height: 7px; margin: 0;
    }
    QScrollBar::handle:horizontal {
        background: %(ctrl_border)s; border-radius: 3px; min-width: 24px;
    }
    QScrollBar::handle:vertical {
        background: %(ctrl_border)s; border-radius: 4px; min-height: 24px;
    }
    QScrollBar::handle:vertical:hover { background: %(text_faint)s; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QToolTip {
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(panel_border)s; padding: 3px;
    }
    """ % dict(c, mono=MONO)
