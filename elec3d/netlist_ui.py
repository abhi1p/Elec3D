# -*- coding: utf-8 -*-
"""Four-step KiCad netlist import wizard for the document-free editor."""

import os

from .netlist import parse_netlist_file
from .qt import Qt, QtWidgets


class NetlistImportWizard(QtWidgets.QDialog):
    """Import a KiCad legacy ``.net`` in the same four logical stages as the
    reference browser viewer, adapted to native Qt/FreeCAD file pickers.

    1. Pick and inspect the netlist.
    2. Resolve the referenced footprint geometry.
    3. Optionally choose the KiCad 3D model search folder.
    4. Review the result and finish in the 3D board view.
    """

    def __init__(self, viewer, parent=None):
        super(NetlistImportWizard, self).__init__(parent or viewer)
        self.viewer = viewer
        self.netlist = None
        self.netlist_path = ""
        self.step = 0
        self.setWindowTitle("Import KiCad netlist")
        self.setModal(True)
        self.resize(590, 430)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Import KiCad netlist")
        title.setObjectName("kind")
        head.addWidget(title)
        head.addStretch(1)
        self.step_label = QtWidgets.QLabel()
        self.step_label.setObjectName("faint")
        head.addWidget(self.step_label)
        root.addLayout(head)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 4)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        root.addWidget(self.progress)

        self.pages = QtWidgets.QStackedWidget()
        root.addWidget(self.pages, 1)
        self._build_pages()

        footer = QtWidgets.QHBoxLayout()
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_back = QtWidgets.QPushButton("Back")
        self.btn_next = QtWidgets.QPushButton("Next")
        footer.addWidget(self.btn_cancel)
        footer.addStretch(1)
        footer.addWidget(self.btn_back)
        footer.addWidget(self.btn_next)
        root.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_back.clicked.connect(self._back)
        self.btn_next.clicked.connect(self._next)
        self._show_step(0)

    # -- pages ---------------------------------------------------------
    @staticmethod
    def _page(title, text):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(10)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("kind")
        layout.addWidget(heading)
        description = QtWidgets.QLabel(text)
        description.setWordWrap(True)
        description.setObjectName("faint")
        layout.addWidget(description)
        return page, layout

    def _build_pages(self):
        page, lay = self._page(
            "1. Choose a netlist",
            "Choose the .net file exported by KiCad. Components become footprint "
            "instances and the net connectivity is used for the ratsnest.")
        self.btn_pick_net = QtWidgets.QPushButton("Choose .net file\u2026")
        self.btn_pick_net.clicked.connect(self._pick_netlist)
        lay.addWidget(self.btn_pick_net, 0, Qt.AlignLeft)
        self.net_summary = QtWidgets.QLabel("No netlist selected")
        self.net_summary.setWordWrap(True)
        self.net_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.net_summary)
        lay.addStretch(1)
        self.pages.addWidget(page)

        page, lay = self._page(
            "2. Load footprints",
            "A KiCad netlist names footprints but does not contain their geometry. "
            "Choose a footprint library folder (including .pretty folders), or add "
            "individual .kicad_mod files. Unresolved components remain usable placeholders.")
        actions = QtWidgets.QHBoxLayout()
        self.btn_fp_folder = QtWidgets.QPushButton("Choose footprints folder" + "\u2026")
        self.btn_fp_files = QtWidgets.QPushButton("Add .kicad_mod files" + "\u2026")
        self.btn_fp_folder.clicked.connect(self._pick_footprint_folder)
        self.btn_fp_files.clicked.connect(self._pick_footprint_files)
        actions.addWidget(self.btn_fp_folder)
        actions.addWidget(self.btn_fp_files)
        actions.addStretch(1)
        lay.addLayout(actions)
        self.fp_summary = QtWidgets.QLabel()
        self.fp_summary.setWordWrap(True)
        self.fp_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.fp_summary)
        lay.addStretch(1)
        self.pages.addWidget(page)

        page, lay = self._page(
            "3. Load 3D models",
            "Choose the root of your KiCad 3D model libraries. STEP/WRL models referenced "
            "by each footprint are matched by filename and shown automatically in 3D. "
            "This step is optional; pads and footprint graphics render without models.")
        self.btn_model_folder = QtWidgets.QPushButton("Choose 3D models folder" + "\u2026")
        self.btn_model_folder.clicked.connect(self._pick_model_folder)
        lay.addWidget(self.btn_model_folder, 0, Qt.AlignLeft)
        self.model_summary = QtWidgets.QLabel("No 3D models folder selected")
        self.model_summary.setWordWrap(True)
        self.model_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.model_summary)
        lay.addStretch(1)
        self.pages.addWidget(page)

        page, lay = self._page(
            "4. Ready",
            "Review the imported board. Finish switches the same document-free editor "
            "to the 3D view; press 2 at any time to return to 2D.")
        self.ready_summary = QtWidgets.QLabel()
        self.ready_summary.setWordWrap(True)
        self.ready_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.ready_summary)
        lay.addStretch(1)
        self.pages.addWidget(page)

    # -- file operations ----------------------------------------------
    def _pick_netlist(self):
        start = self.viewer.last_import_directory()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select KiCad netlist", start,
            "KiCad netlist (*.net);;All files (*)")
        if not path:
            return
        try:
            netlist = parse_netlist_file(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "KiCad netlist", "Could not parse %s:\n\n%s" %
                (os.path.basename(path), exc))
            return
        self.netlist = netlist
        self.netlist_path = path
        self.viewer.remember_import_directory(path)
        distinct = netlist.footprint_names()
        lines = [
            "<b>%s</b>" % os.path.basename(path),
            "Components: %d" % len(netlist.components),
            "Nets: %d" % len(netlist.nets),
            "Distinct footprints: %d" % len(distinct),
        ]
        if distinct:
            lines.append("<br>".join(distinct[:18]) +
                         ("<br>\u2026" if len(distinct) > 18 else ""))
        self.net_summary.setText("<br>".join(lines))
        self._update_buttons()

    def _pick_footprint_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select KiCad footprint library folder", self.viewer.last_import_directory())
        if not path:
            return
        self.viewer.remember_import_directory(path)
        self.viewer.resolve_netlist_footprints(folder=path)
        self._refresh_footprint_summary()

    def _pick_footprint_files(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select KiCad footprint files", self.viewer.last_import_directory(),
            "KiCad footprint (*.kicad_mod);;All files (*)")
        if not files:
            return
        self.viewer.remember_import_directory(files[0])
        self.viewer.resolve_netlist_footprints(files=files)
        self._refresh_footprint_summary()

    def _pick_model_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select KiCad 3D models folder", self.viewer.last_import_directory())
        if not path:
            return
        self.viewer.remember_import_directory(path)
        self.viewer.set_models_folder(path)
        self._refresh_model_summary()

    # -- navigation ----------------------------------------------------
    def _show_step(self, step):
        self.step = max(0, min(3, int(step)))
        self.pages.setCurrentIndex(self.step)
        self.step_label.setText("Step %d of 4" % (self.step + 1))
        self.progress.setValue(self.step + 1)
        if self.step == 1:
            self._refresh_footprint_summary()
        elif self.step == 2:
            self._refresh_model_summary()
        elif self.step == 3:
            self._refresh_ready_summary()
        self._update_buttons()

    def _update_buttons(self):
        self.btn_back.setEnabled(self.step > 0)
        if self.step == 0:
            self.btn_next.setText("Next")
            self.btn_next.setEnabled(self.netlist is not None)
        elif self.step == 1:
            self.btn_next.setText("Next" if self.viewer.resolved_netlist_count() else "Skip")
            self.btn_next.setEnabled(True)
        elif self.step == 2:
            self.btn_next.setText("Next" if self.viewer.models_folder else "Skip")
            self.btn_next.setEnabled(True)
        else:
            self.btn_next.setText("Finish")
            self.btn_next.setEnabled(True)

    def _back(self):
        if self.step > 0:
            self._show_step(self.step - 1)

    def _next(self):
        if self.step == 0:
            if self.netlist is None:
                return
            if self.viewer._entries:
                answer = QtWidgets.QMessageBox.question(
                    self, "Import KiCad netlist",
                    "Replace the currently loaded footprints with the %d components from %s?" %
                    (len(self.netlist.components), os.path.basename(self.netlist_path)),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if answer != QtWidgets.QMessageBox.Yes:
                    return
            self.viewer.apply_netlist(self.netlist)
            self._show_step(1)
            return
        if self.step < 3:
            self._show_step(self.step + 1)
            return
        self.accept()
        self.viewer.set_view_mode("3d")

    # -- summaries -----------------------------------------------------
    def _refresh_footprint_summary(self):
        if self.netlist is None:
            self.fp_summary.setText("Choose a netlist first.")
            return
        total = len(self.viewer._entries)
        resolved = self.viewer.resolved_netlist_count()
        pending = self.viewer.pending_netlist_footprints()
        text = "Loaded geometry for <b>%d of %d</b> components." % (resolved, total)
        if pending:
            text += "<br><br>Still using placeholders:<br>" + "<br>".join(pending[:24])
            if len(pending) > 24:
                text += "<br>\u2026"
        self.fp_summary.setText(text)
        self._update_buttons()

    def _refresh_model_summary(self):
        refs, resolved = self.viewer.model_reference_stats()
        if not self.viewer.models_folder:
            self.model_summary.setText(
                "No folder selected. %d footprint%s reference%s a 3D model." %
                (refs, "" if refs == 1 else "s", "s" if refs == 1 else ""))
        else:
            self.model_summary.setText(
                "<b>%s</b><br>Referenced models found by filename: %d of %d" %
                (self.viewer.models_folder, resolved, refs))
        self._update_buttons()

    def _refresh_ready_summary(self):
        total = len(self.viewer._entries)
        resolved = self.viewer.resolved_netlist_count()
        refs, models = self.viewer.model_reference_stats()
        self.ready_summary.setText(
            "Components placed: <b>%d</b><br>"
            "Nets: <b>%d</b><br>"
            "Footprints loaded: <b>%d of %d</b><br>"
            "3D models found: <b>%d of %d</b><br><br>"
            "Finish opens the 3D board view." %
            (total, len(self.netlist.nets) if self.netlist else 0,
             resolved, total, models, refs))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super(NetlistImportWizard, self).keyPressEvent(event)
