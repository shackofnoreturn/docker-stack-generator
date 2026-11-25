"""
Template Manager widget — browse, edit, create, rename and delete
the shared Jinja2 snippets that live under  stacks/templates/.

Layout
──────
┌─────────────────┬────────────────────────────────────────────────────┐
│  Category tree  │  Editor pane                                       │
│                 │  ┌─ path breadcrumb ─────────────────────────────┐ │
│  common/        │  │ stacks/templates/policies/medium.yml.j2        │ │
│    logging      │  └────────────────────────────────────────────────┘ │
│  healthchecks/  │  ┌─ editor ──────────────────────────────────────┐ │
│    http         │  │  <syntax-highlighted YAML/Jinja2>              │ │
│  labels/        │  │                                                │ │
│    homepage     │  └────────────────────────────────────────────────┘ │
│    traefik      │  [ Save ]  [ Discard ]  [ Delete ]                  │
│  …              │                                                     │
│                 │                                                     │
│ [+ Category]    │                                                     │
│ [+ Template]    │                                                     │
└─────────────────┴────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from docker_stack_generator.config import settings
from docker_stack_generator.highlighter import Jinja2YamlHighlighter
from docker_stack_generator.widgets import HSeparator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "policies":     "Restart, update & rollback policies applied to service deploy blocks.",
    "resources":    "CPU / memory limit and reservation profiles.",
    "common":       "Shared snippets included by all services (e.g. logging driver).",
    "labels":       "Docker label snippets — Traefik routing, Homepage dashboard entries, etc.",
    "healthchecks": "Healthcheck definitions for services.",
    "mounts":       "Volume / bind-mount snippet helpers.",
    "placement":    "Docker Swarm placement constraint snippets.",
}


def _templates_dir() -> Optional[Path]:
    root = settings.stacks_root
    if not root:
        return None
    return Path(root) / "templates"


def _category_icon(name: str) -> str:
    icons = {
        "policies":     "🔁",
        "resources":    "📊",
        "common":       "🔧",
        "labels":       "🏷",
        "healthchecks": "❤",
        "mounts":       "💾",
        "placement":    "📌",
    }
    return icons.get(name, "📁")


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class TemplateManagerWidget(QWidget):
    """
    Full template manager embedded as a tab in the main window.
    Re-scans the templates directory whenever it becomes visible.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: Optional[Path] = None
        self._dirty = False
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setStyleSheet("background:#2d333b; color:white;")
        hb = QHBoxLayout(header)
        hb.setContentsMargins(12, 6, 12, 6)

        title = QLabel("📂  Template Library")
        title.setStyleSheet("font-weight:bold; font-size:14px; color:white;")
        hb.addWidget(title)

        hb.addStretch()

        self._dir_label = QLabel("")
        self._dir_label.setStyleSheet("font-size:11px; color:#aaa;")
        hb.addWidget(self._dir_label)

        refresh_btn = QPushButton("⟳  Refresh")
        refresh_btn.setStyleSheet("color:white; background:#3d4451; border:none; padding:3px 8px; border-radius:3px;")
        refresh_btn.clicked.connect(self.refresh)
        hb.addWidget(refresh_btn)

        root.addWidget(header)

        # Splitter: tree | editor
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ---- Left: category/file tree ------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 6, 4, 6)
        lv.setSpacing(4)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_context_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        lv.addWidget(self._tree, 1)

        lv.addWidget(HSeparator())

        btn_row = QHBoxLayout()
        add_cat_btn = QPushButton("＋ Category")
        add_cat_btn.setToolTip("Create a new template category (sub-folder)")
        add_cat_btn.clicked.connect(self._add_category)
        btn_row.addWidget(add_cat_btn)

        add_tpl_btn = QPushButton("＋ Template")
        add_tpl_btn.setToolTip("Create a new template file in the selected category")
        add_tpl_btn.clicked.connect(self._add_template)
        btn_row.addWidget(add_tpl_btn)
        lv.addLayout(btn_row)

        splitter.addWidget(left)

        # ---- Right: editor pane ------------------------------------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 6, 6, 6)
        rv.setSpacing(6)

        # Breadcrumb + category description
        self._breadcrumb = QLabel("")
        self._breadcrumb.setStyleSheet("font-size:12px; color:#555;")
        self._breadcrumb.setWordWrap(True)
        rv.addWidget(self._breadcrumb)

        self._category_desc = QLabel("")
        self._category_desc.setStyleSheet("font-size:11px; color:#888; font-style:italic;")
        self._category_desc.setWordWrap(True)
        rv.addWidget(self._category_desc)

        rv.addWidget(HSeparator())

        # Editor
        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText(
            "Select a template from the tree to edit it,\n"
            "or click '+ Template' to create a new one."
        )
        font = QFont("Monospace", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)
        self._editor.setStyleSheet(
            "QPlainTextEdit { background: #282c34; color: #abb2bf; "
            "border: 1px solid #3e4451; border-radius: 4px; padding: 6px; }"
        )
        self._editor.textChanged.connect(self._on_text_changed)
        self._highlighter = Jinja2YamlHighlighter(self._editor.document())
        rv.addWidget(self._editor, 1)

        # Action buttons
        btn_row2 = QHBoxLayout()

        self._save_btn = QPushButton("💾  Save")
        self._save_btn.setEnabled(False)
        self._save_btn.setStyleSheet(
            "QPushButton:enabled { background:#2a7ae2; color:white; border-radius:4px; padding:4px 12px; }"
            "QPushButton:disabled { background:#ccc; }"
        )
        self._save_btn.clicked.connect(self._save_current)
        btn_row2.addWidget(self._save_btn)

        self._discard_btn = QPushButton("✕  Discard")
        self._discard_btn.setEnabled(False)
        self._discard_btn.clicked.connect(self._discard_changes)
        btn_row2.addWidget(self._discard_btn)

        btn_row2.addStretch()

        self._rename_btn = QPushButton("✎  Rename")
        self._rename_btn.setEnabled(False)
        self._rename_btn.clicked.connect(self._rename_current)
        btn_row2.addWidget(self._rename_btn)

        self._delete_btn = QPushButton("🗑  Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.setStyleSheet(
            "QPushButton:enabled { color:#c0392b; }"
        )
        self._delete_btn.clicked.connect(self._delete_current)
        btn_row2.addWidget(self._delete_btn)

        rv.addLayout(btn_row2)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # Empty-state placeholder (shown when no stacks root configured)
        self._placeholder = QLabel(
            "⚙  Configure a <b>Stacks Root</b> directory in Settings first.\n"
            "Templates live at  <code>&lt;stacks root&gt;/templates/</code>"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#888; font-size:13px; padding:40px;")
        self._placeholder.setTextFormat(Qt.TextFormat.RichText)
        self._placeholder.setWordWrap(True)
        root.addWidget(self._placeholder)

        self._splitter = splitter

    # ------------------------------------------------------------------
    # Visibility / refresh
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        tdir = _templates_dir()
        if not tdir:
            self._placeholder.setVisible(True)
            self._splitter.setVisible(False)
            self._dir_label.setText("No stacks root configured")
            return

        self._placeholder.setVisible(False)
        self._splitter.setVisible(True)
        self._dir_label.setText(str(tdir))

        # Ensure base dirs exist
        tdir.mkdir(parents=True, exist_ok=True)

        self._populate_tree(tdir)

    def _populate_tree(self, tdir: Path):
        # Preserve current selection path string for restoration
        current_path_str = str(self._current_path) if self._current_path else None

        self._tree.blockSignals(True)
        self._tree.clear()

        # Sort categories: known order first, then alphabetical
        known_order = ["common", "policies", "resources", "labels", "healthchecks", "mounts", "placement"]
        cats = sorted(
            [d for d in tdir.iterdir() if d.is_dir()],
            key=lambda d: (known_order.index(d.name) if d.name in known_order else 999, d.name),
        )

        item_to_restore: Optional[QTreeWidgetItem] = None

        for cat_dir in cats:
            cat_item = QTreeWidgetItem(self._tree)
            cat_item.setText(0, f"  {_category_icon(cat_dir.name)}  {cat_dir.name}")
            cat_item.setData(0, Qt.ItemDataRole.UserRole, cat_dir)
            cat_item.setExpanded(True)
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)

            for tpl_file in sorted(cat_dir.iterdir()):
                if not tpl_file.is_file():
                    continue
                tpl_item = QTreeWidgetItem(cat_item)
                stem = tpl_file.stem  # remove .j2
                if stem.endswith(".yml"):
                    stem = stem[:-4]  # remove .yml too → "medium", "http" etc.
                tpl_item.setText(0, f"    {stem}")
                tpl_item.setData(0, Qt.ItemDataRole.UserRole, tpl_file)
                if current_path_str and str(tpl_file) == current_path_str:
                    item_to_restore = tpl_item

        self._tree.blockSignals(False)

        if item_to_restore:
            self._tree.setCurrentItem(item_to_restore)
        else:
            # Clear editor only if no current selection
            if not self._current_path:
                self._clear_editor()

    # ------------------------------------------------------------------
    # Tree selection
    # ------------------------------------------------------------------

    def _on_tree_selection(self, current: Optional[QTreeWidgetItem], _previous):
        if current is None:
            return
        path: Optional[Path] = current.data(0, Qt.ItemDataRole.UserRole)
        if path is None or path.is_dir():
            return
        if self._dirty and not self._confirm_discard():
            return
        self._load_file(path)

    def _load_file(self, path: Path):
        self._current_path = path
        self._dirty = False

        rel = self._relative_display(path)
        self._breadcrumb.setText(f"<b>templates/{rel}</b>")

        # Category description
        cat = path.parent.name
        self._category_desc.setText(CATEGORY_DESCRIPTIONS.get(cat, ""))

        # Load content
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            content = f"# Error reading file: {exc}"

        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.blockSignals(False)

        self._set_dirty(False)
        self._rename_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)

    def _relative_display(self, path: Path) -> str:
        tdir = _templates_dir()
        if tdir:
            try:
                return str(path.relative_to(tdir))
            except ValueError:
                pass
        return path.name

    def _clear_editor(self):
        self._current_path = None
        self._dirty = False
        self._editor.blockSignals(True)
        self._editor.clear()
        self._editor.blockSignals(False)
        self._breadcrumb.setText("")
        self._category_desc.setText("")
        self._set_dirty(False)
        self._rename_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Editor state
    # ------------------------------------------------------------------

    def _on_text_changed(self):
        if self._current_path is not None:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        self._save_btn.setEnabled(dirty)
        self._discard_btn.setEnabled(dirty)

    def _confirm_discard(self) -> bool:
        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            "You have unsaved changes. Discard them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # Save / discard
    # ------------------------------------------------------------------

    def _save_current(self):
        if not self._current_path:
            return
        content = self._editor.toPlainText()
        try:
            self._current_path.write_text(content, encoding="utf-8")
            self._set_dirty(False)
        except OSError as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save file:\n{exc}")

    def _discard_changes(self):
        if self._current_path:
            self._load_file(self._current_path)

    # ------------------------------------------------------------------
    # Add category
    # ------------------------------------------------------------------

    def _add_category(self):
        tdir = _templates_dir()
        if not tdir:
            QMessageBox.warning(self, "No stacks root", "Configure stacks root in Settings first.")
            return
        name, ok = QInputDialog.getText(
            self,
            "New Category",
            "Category name (will become a sub-folder under templates/):",
        )
        name = name.strip().lower().replace(" ", "-")
        if not ok or not name:
            return
        new_dir = tdir / name
        if new_dir.exists():
            QMessageBox.warning(self, "Exists", f"Category '{name}' already exists.")
            return
        new_dir.mkdir(parents=True)
        self.refresh()

    # ------------------------------------------------------------------
    # Add template
    # ------------------------------------------------------------------

    def _add_template(self):
        tdir = _templates_dir()
        if not tdir:
            QMessageBox.warning(self, "No stacks root", "Configure stacks root in Settings first.")
            return

        # Determine target category from selection
        current = self._tree.currentItem()
        cat_dir: Optional[Path] = None
        if current:
            data: Optional[Path] = current.data(0, Qt.ItemDataRole.UserRole)
            if data:
                cat_dir = data if data.is_dir() else data.parent

        dlg = NewTemplateDialog(tdir, default_category=cat_dir.name if cat_dir else "", parent=self)
        if dlg.exec():
            new_path = dlg.created_path()
            self.refresh()
            # Select the new file
            self._select_path(new_path)

    def _select_path(self, path: Path):
        """Walk the tree to find and select an item matching path."""
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            for j in range(cat_item.childCount()):
                tpl_item = cat_item.child(j)
                p: Optional[Path] = tpl_item.data(0, Qt.ItemDataRole.UserRole)
                if p and p == path:
                    self._tree.setCurrentItem(tpl_item)
                    return

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    def _rename_current(self):
        if not self._current_path:
            return
        old_name = self._current_path.name
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Template",
            "New filename:",
            text=old_name,
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        new_path = self._current_path.parent / new_name
        if new_path.exists():
            QMessageBox.warning(self, "Exists", f"A file named '{new_name}' already exists in this category.")
            return
        # Save pending changes first
        if self._dirty:
            self._save_current()
        try:
            self._current_path.rename(new_path)
            self._current_path = new_path
            self.refresh()
        except OSError as exc:
            QMessageBox.critical(self, "Rename Error", str(exc))

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _delete_current(self):
        if not self._current_path:
            return
        rel = self._relative_display(self._current_path)
        reply = QMessageBox.question(
            self,
            "Delete Template",
            f"Permanently delete <b>templates/{rel}</b>?<br><br>"
            "<i>This cannot be undone.</i>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._current_path.unlink()
        except OSError as exc:
            QMessageBox.critical(self, "Delete Error", str(exc))
            return
        self._clear_editor()
        self.refresh()

    # ------------------------------------------------------------------
    # Context menu on tree
    # ------------------------------------------------------------------

    def _tree_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        path: Optional[Path] = item.data(0, Qt.ItemDataRole.UserRole)
        if path is None:
            return

        menu = QMenu(self)

        if path.is_dir():
            # Category node
            add_act = menu.addAction("＋  Add template here…")
            add_act.triggered.connect(lambda: self._add_template_in(path))
            menu.addSeparator()
            rename_cat_act = menu.addAction("✎  Rename category…")
            rename_cat_act.triggered.connect(lambda: self._rename_category(path))
            del_cat_act = menu.addAction("🗑  Delete category…")
            del_cat_act.triggered.connect(lambda: self._delete_category(path))
        else:
            # File node
            open_act = menu.addAction("Open / Edit")
            open_act.triggered.connect(lambda: self._load_file(path))
            menu.addSeparator()
            rename_act = menu.addAction("✎  Rename…")
            rename_act.triggered.connect(self._rename_current)
            del_act = menu.addAction("🗑  Delete…")
            del_act.triggered.connect(self._delete_current)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _add_template_in(self, cat_dir: Path):
        tdir = _templates_dir()
        if not tdir:
            return
        dlg = NewTemplateDialog(tdir, default_category=cat_dir.name, parent=self)
        if dlg.exec():
            new_path = dlg.created_path()
            self.refresh()
            self._select_path(new_path)

    def _rename_category(self, cat_dir: Path):
        new_name, ok = QInputDialog.getText(
            self, "Rename Category", "New category name:", text=cat_dir.name
        )
        new_name = new_name.strip().lower().replace(" ", "-")
        if not ok or not new_name or new_name == cat_dir.name:
            return
        new_path = cat_dir.parent / new_name
        if new_path.exists():
            QMessageBox.warning(self, "Exists", f"Category '{new_name}' already exists.")
            return
        try:
            cat_dir.rename(new_path)
            if self._current_path and self._current_path.parent == cat_dir:
                self._current_path = new_path / self._current_path.name
            self.refresh()
        except OSError as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _delete_category(self, cat_dir: Path):
        files = list(cat_dir.iterdir())
        msg = (
            f"Delete category <b>{cat_dir.name}/</b>?"
            if not files
            else f"Delete category <b>{cat_dir.name}/</b> and all <b>{len(files)}</b> template(s) inside it?"
        )
        reply = QMessageBox.question(
            self,
            "Delete Category",
            msg + "<br><br><i>This cannot be undone.</i>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(cat_dir)
            if self._current_path and self._current_path.parent == cat_dir:
                self._clear_editor()
            self.refresh()
        except OSError as exc:
            QMessageBox.critical(self, "Error", str(exc))


# ---------------------------------------------------------------------------
# New template dialog
# ---------------------------------------------------------------------------

class NewTemplateDialog(QDialog):
    """Dialog to create a new template file (optionally with starter content)."""

    def __init__(self, templates_dir: Path, default_category: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Template")
        self.setMinimumWidth(500)
        self._templates_dir = templates_dir
        self._created: Optional[Path] = None

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Category selector (existing dirs + option to type new one)
        from PyQt6.QtWidgets import QComboBox
        self._cat_combo = QComboBox()
        self._cat_combo.setEditable(True)
        cats = sorted(d.name for d in templates_dir.iterdir() if d.is_dir())
        for c in cats:
            self._cat_combo.addItem(c)
        if default_category:
            idx = self._cat_combo.findText(default_category)
            if idx >= 0:
                self._cat_combo.setCurrentIndex(idx)
            else:
                self._cat_combo.setCurrentText(default_category)
        form.addRow("Category:", self._cat_combo)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g. my-healthcheck  (no extension needed)")
        form.addRow("Template name:", self._name_input)

        layout.addLayout(form)

        layout.addWidget(QLabel("Starter content (optional):"))

        self._content_editor = QPlainTextEdit()
        font = QFont("Monospace", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._content_editor.setFont(font)
        self._content_editor.setPlaceholderText(
            "# Paste or type Jinja2/YAML snippet here.\n"
            "# Leave empty to create a blank file."
        )
        self._content_editor.setMinimumHeight(200)
        Jinja2YamlHighlighter(self._content_editor.document())
        layout.addWidget(self._content_editor)

        self._preview_label = QLabel("")
        self._preview_label.setStyleSheet("font-size:11px; color:#888;")
        layout.addWidget(self._preview_label)

        self._name_input.textChanged.connect(self._update_preview)
        self._cat_combo.currentTextChanged.connect(self._update_preview)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _update_preview(self):
        cat = self._cat_combo.currentText().strip()
        name = self._name_input.text().strip()
        if cat and name:
            fname = name if name.endswith(".j2") else f"{name}.yml.j2"
            self._preview_label.setText(f"Will create: templates/{cat}/{fname}")
        else:
            self._preview_label.setText("")

    def _on_accept(self):
        cat = self._cat_combo.currentText().strip().lower().replace(" ", "-")
        name = self._name_input.text().strip()
        if not cat:
            QMessageBox.warning(self, "Validation", "Please enter a category name.")
            return
        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a template name.")
            return
        fname = name if name.endswith(".j2") else f"{name}.yml.j2"
        target_dir = self._templates_dir / cat
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / fname
        if target.exists():
            reply = QMessageBox.question(
                self, "Overwrite?",
                f"{fname} already exists in {cat}/. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        content = self._content_editor.toPlainText()
        try:
            target.write_text(content, encoding="utf-8")
            self._created = target
            self.accept()
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not create file:\n{exc}")

    def created_path(self) -> Optional[Path]:
        return self._created
