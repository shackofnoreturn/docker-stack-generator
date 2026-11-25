"""
Stack Library widget — browse, inspect, load, duplicate, and delete
saved stack definitions so they can be tweaked and re-generated anytime.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from docker_stack_generator.config import settings
from docker_stack_generator.generator import slugify
from docker_stack_generator.stack_library import SavedStack, stack_library
from docker_stack_generator.widgets import HSeparator


def _fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return "Never"
    # Trim timezone/seconds noise for readability: 2026-11-25T15:42:16+01:00 -> 2026-11-25 15:42
    return ts[:16].replace("T", " ")


class DuplicateStackDialog(QDialog):
    """Prompt for a new name and environment when duplicating a saved stack."""

    def __init__(self, source: SavedStack, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate Stack")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_input = QLineEdit(f"{source.config.display_name}_copy")
        form.addRow("New stack name:", self._name_input)

        self._env_input = QLineEdit(source.config.environment)
        form.addRow("Environment:", self._env_input)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self):
        if not slugify(self._name_input.text().strip()):
            QMessageBox.warning(self, "Validation", "Please enter a valid stack name.")
            return
        if not self._env_input.text().strip():
            QMessageBox.warning(self, "Validation", "Please enter an environment.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self._name_input.text().strip(), self._env_input.text().strip()


class StackLibraryWidget(QWidget):
    """
    Tab widget displaying all saved stack definitions.
    Emits `load_requested(SavedStack)` when the user wants to edit/re-generate a stack.
    """

    load_requested = pyqtSignal(object)  # SavedStack

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[SavedStack] = []
        self._current: Optional[SavedStack] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setStyleSheet("background:#2d333b; color:white;")
        hb = QHBoxLayout(header)
        hb.setContentsMargins(12, 6, 12, 6)

        title = QLabel("📚  Stack Library")
        title.setStyleSheet("font-weight:bold; font-size:14px; color:white;")
        hb.addWidget(title)

        hb.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size:11px; color:#aaa;")
        hb.addWidget(self._count_label)

        refresh_btn = QPushButton("⟳  Refresh")
        refresh_btn.setStyleSheet(
            "color:white; background:#3d4451; border:none; padding:3px 8px; border-radius:3px;"
        )
        refresh_btn.clicked.connect(self.refresh)
        hb.addWidget(refresh_btn)

        root.addWidget(header)

        # Splitter: left list | right details
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ---- Left: filter + saved stacks list -----------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        lv.setSpacing(6)

        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("Filter stacks by name, environment, or service…")
        self._filter_input.setClearButtonEnabled(True)
        self._filter_input.textChanged.connect(self._apply_filter)
        lv.addWidget(self._filter_input)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(2)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(lambda _: self._load_selected())
        lv.addWidget(self._list, 1)

        splitter.addWidget(left)

        # ---- Right: stack details + actions -------------------------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 8, 8, 8)
        rv.setSpacing(8)

        self._detail_title = QLabel("<b>Select a saved stack</b>")
        self._detail_title.setStyleSheet("font-size:15px;")
        rv.addWidget(self._detail_title)

        rv.addWidget(HSeparator())

        info_group = QGroupBox("Stack Details")
        ig = QFormLayout(info_group)
        ig.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._lbl_env = QLabel("—")
        self._lbl_path = QLabel("—")
        self._lbl_path.setWordWrap(True)
        self._lbl_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lbl_networks = QLabel("—")
        self._lbl_updated = QLabel("—")
        self._lbl_generated = QLabel("—")

        ig.addRow("Environment:", self._lbl_env)
        ig.addRow("Target path:", self._lbl_path)
        ig.addRow("Networks:", self._lbl_networks)
        ig.addRow("Last saved:", self._lbl_updated)
        ig.addRow("Last generated:", self._lbl_generated)
        rv.addWidget(info_group)

        svc_group = QGroupBox("Services")
        sg = QVBoxLayout(svc_group)
        self._svc_table = QTableWidget(0, 5)
        self._svc_table.setHorizontalHeaderLabels(
            ["Service", "Image", "Port", "Restart", "Resources"]
        )
        self._svc_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._svc_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._svc_table.setAlternatingRowColors(True)
        self._svc_table.verticalHeader().setVisible(False)
        hdr = self._svc_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        sg.addWidget(self._svc_table)
        rv.addWidget(svc_group, 1)

        # Action bar
        actions = QHBoxLayout()

        self._load_btn = QPushButton("✎  Load into Generator")
        self._load_btn.setMinimumHeight(36)
        self._load_btn.setEnabled(False)
        self._load_btn.setStyleSheet(
            "QPushButton:enabled { background:#2a7ae2; color:white; font-weight:bold; border-radius:4px; padding:4px 14px; }"
            "QPushButton:hover:enabled { background:#1a5fc5; }"
            "QPushButton:disabled { background:#ccc; color:#666; }"
        )
        self._load_btn.clicked.connect(self._load_selected)
        actions.addWidget(self._load_btn)

        self._dup_btn = QPushButton("📋  Duplicate…")
        self._dup_btn.setEnabled(False)
        self._dup_btn.clicked.connect(self._duplicate_selected)
        actions.addWidget(self._dup_btn)

        self._open_btn = QPushButton("📁  Open Folder")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_folder)
        actions.addWidget(self._open_btn)

        actions.addStretch()

        self._del_btn = QPushButton("🗑  Delete")
        self._del_btn.setEnabled(False)
        self._del_btn.setStyleSheet("QPushButton:enabled { color:#c0392b; }")
        self._del_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self._del_btn)

        rv.addLayout(actions)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    # ------------------------------------------------------------------
    # Visibility / refresh
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        selected_key = self._current.key if self._current else None
        self._entries = stack_library.list_stacks()
        self._count_label.setText(f"{len(self._entries)} saved stack(s)")
        self._apply_filter(self._filter_input.text(), restore_key=selected_key)

    def _apply_filter(self, text: str = "", restore_key: Optional[str] = None):
        q = text.strip().lower()
        if restore_key is None and self._current is not None:
            restore_key = self._current.key

        self._list.blockSignals(True)
        self._list.clear()

        item_to_select: Optional[QListWidgetItem] = None

        for entry in self._entries:
            cfg = entry.config
            if q:
                haystack = " ".join(
                    [
                        cfg.name,
                        cfg.display_name,
                        cfg.environment,
                        *(s.name for s in cfg.services),
                        *(s.image for s in cfg.services),
                    ]
                ).lower()
                if q not in haystack:
                    continue

            svc_count = len(cfg.services)
            svc_word = "service" if svc_count == 1 else "services"
            label = (
                f"{cfg.display_name}  [{cfg.environment}]\n"
                f"  {svc_count} {svc_word}  |  Saved: {_fmt_ts(entry.updated_at)}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._list.addItem(item)
            if restore_key and entry.key == restore_key:
                item_to_select = item

        self._list.blockSignals(False)

        if item_to_select is not None:
            self._list.setCurrentItem(item_to_select)
            self._show_entry(item_to_select.data(Qt.ItemDataRole.UserRole))
        elif self._list.count() > 0:
            self._list.setCurrentRow(0)
            self._show_entry(self._list.item(0).data(Qt.ItemDataRole.UserRole))
        else:
            self._clear_details()

    # ------------------------------------------------------------------
    # Selection & details
    # ------------------------------------------------------------------

    def _on_selection_changed(self, current: Optional[QListWidgetItem], _prev):
        if current is None:
            self._clear_details()
            return
        entry: Optional[SavedStack] = current.data(Qt.ItemDataRole.UserRole)
        if entry:
            self._show_entry(entry)
        else:
            self._clear_details()

    def _show_entry(self, entry: SavedStack):
        self._current = entry
        cfg = entry.config
        self._detail_title.setText(f"<b>{cfg.display_name}</b>  <span style='color:#888;'>({cfg.name})</span>")
        self._lbl_env.setText(cfg.environment)

        effective_root = cfg.stacks_root or settings.stacks_root
        if effective_root:
            target = str(Path(effective_root) / cfg.environment / cfg.name)
            exists_note = " (exists on disk)" if Path(target).exists() else " (not yet generated)"
            self._lbl_path.setText(f"{target}<span style='color:#888;'>{exists_note}</span>")
            self._open_btn.setEnabled(Path(target).exists())
        else:
            self._lbl_path.setText("<i>No stacks root configured</i>")
            self._open_btn.setEnabled(False)

        self._lbl_networks.setText(", ".join(f"{k} ({v})" for k, v in cfg.network_vars.items()))
        self._lbl_updated.setText(_fmt_ts(entry.updated_at))
        self._lbl_generated.setText(_fmt_ts(entry.last_generated_at))

        self._svc_table.setRowCount(len(cfg.services))
        for row, svc in enumerate(cfg.services):
            self._svc_table.setItem(row, 0, QTableWidgetItem(svc.display_name))
            self._svc_table.setItem(row, 1, QTableWidgetItem(svc.image))
            self._svc_table.setItem(row, 2, QTableWidgetItem(svc.port))
            self._svc_table.setItem(row, 3, QTableWidgetItem(svc.restart_profile))
            self._svc_table.setItem(row, 4, QTableWidgetItem(svc.resource_profile))

        self._load_btn.setEnabled(True)
        self._dup_btn.setEnabled(True)
        self._del_btn.setEnabled(True)

    def _clear_details(self):
        self._current = None
        self._detail_title.setText("<b>Select a saved stack</b>")
        self._lbl_env.setText("—")
        self._lbl_path.setText("—")
        self._lbl_networks.setText("—")
        self._lbl_updated.setText("—")
        self._lbl_generated.setText("—")
        self._svc_table.setRowCount(0)
        self._load_btn.setEnabled(False)
        self._dup_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._del_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _load_selected(self):
        if not self._current:
            return
        self.load_requested.emit(self._current)

    def _duplicate_selected(self):
        if not self._current:
            return
        dlg = DuplicateStackDialog(self._current, self)
        if dlg.exec():
            new_name, new_env = dlg.values()
            dup = stack_library.duplicate_stack(self._current.key, new_name, new_env)
            if dup:
                self._current = dup
                self.refresh()

    def _open_folder(self):
        if not self._current:
            return
        cfg = self._current.config
        effective_root = cfg.stacks_root or settings.stacks_root
        if not effective_root:
            return
        target = Path(effective_root) / cfg.environment / cfg.name
        if target.exists():
            os.system(f"xdg-open '{target}'")
        else:
            QMessageBox.warning(self, "Not found", f"Directory not found:\n{target}")

    def _delete_selected(self):
        if not self._current:
            return
        cfg = self._current.config
        reply = QMessageBox.question(
            self,
            "Delete from Stack Library",
            f"Remove <b>{cfg.display_name}</b> [{cfg.environment}] from the Stack Library?<br><br>"
            "<i>Generated files on disk will not be deleted.</i>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        stack_library.delete_stack(self._current.key)
        self._current = None
        self.refresh()

    def _on_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        load_act = menu.addAction("✎  Load into Generator")
        load_act.triggered.connect(self._load_selected)
        dup_act = menu.addAction("📋  Duplicate…")
        dup_act.triggered.connect(self._duplicate_selected)
        open_act = menu.addAction("📁  Open Folder")
        open_act.triggered.connect(self._open_folder)
        menu.addSeparator()
        del_act = menu.addAction("🗑  Delete from Library")
        del_act.triggered.connect(self._delete_selected)
        menu.exec(self._list.viewport().mapToGlobal(pos))
