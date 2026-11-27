"""
Main application window.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from docker_stack_generator.config import settings
from docker_stack_generator.deployment_widget import DeploymentWidget
from docker_stack_generator.generator import (
    GenerationError,
    ServiceConfig,
    StackConfig,
    slugify,
)
from docker_stack_generator.highlighter import Jinja2YamlHighlighter
from docker_stack_generator.service_dialog import ServiceDialog
from docker_stack_generator.settings_dialog import SettingsDialog
from docker_stack_generator.stack_library import SavedStack, stack_library
from docker_stack_generator.stack_library_widget import StackLibraryWidget
from docker_stack_generator.template_manager import TemplateManagerWidget
from docker_stack_generator.widgets import GenerateWorker, HSeparator


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Docker Stack Generator")
        self.setMinimumSize(980, 680)
        self._services: list[ServiceConfig] = []
        self._generate_worker: Optional[GenerateWorker] = None

        self._build_menu()
        self._build_toolbar()
        self._build_ui()
        self._restore_geometry()
        self._check_settings()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        new_act = QAction("&New Stack", self)
        new_act.setShortcut("Ctrl+N")
        new_act.triggered.connect(self._reset_form)
        file_menu.addAction(new_act)

        save_lib_act = QAction("💾 &Save to Library", self)
        save_lib_act.setShortcut("Ctrl+S")
        save_lib_act.triggered.connect(self._save_to_library)
        file_menu.addAction(save_lib_act)

        file_menu.addSeparator()

        backup_act = QAction("💾 &Backup Library…", self)
        backup_act.triggered.connect(self._backup_library)
        file_menu.addAction(backup_act)

        restore_act = QAction("📂 &Restore Library…", self)
        restore_act.triggered.connect(self._restore_library)
        file_menu.addAction(restore_act)

        export_act = QAction("⬇ &Export to CSV…", self)
        export_act.triggered.connect(self._export_csv)
        file_menu.addAction(export_act)

        file_menu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        edit_menu = menu.addMenu("&Edit")
        settings_act = QAction("⚙ &Settings…", self)
        settings_act.setShortcut("Ctrl+,")
        settings_act.triggered.connect(self._open_settings)
        edit_menu.addAction(settings_act)

        view_menu = menu.addMenu("&View")
        stack_act = QAction("Stack &Generator  Ctrl+1", self)
        stack_act.setShortcut("Ctrl+1")
        stack_act.triggered.connect(lambda: self._tabs.setCurrentIndex(0))
        view_menu.addAction(stack_act)
        lib_act = QAction("Stack &Library  Ctrl+2", self)
        lib_act.setShortcut("Ctrl+2")
        lib_act.triggered.connect(lambda: self._tabs.setCurrentIndex(1))
        view_menu.addAction(lib_act)
        tpl_act = QAction("&Template Library  Ctrl+3", self)
        tpl_act.setShortcut("Ctrl+3")
        tpl_act.triggered.connect(lambda: self._tabs.setCurrentIndex(2))
        view_menu.addAction(tpl_act)
        deploy_act = QAction("&Deployments  Ctrl+4", self)
        deploy_act.setShortcut("Ctrl+4")
        deploy_act.triggered.connect(lambda: self._tabs.setCurrentIndex(3))
        view_menu.addAction(deploy_act)

        help_menu = menu.addMenu("&Help")
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        new_btn = QPushButton("🗋  New Stack")
        new_btn.clicked.connect(self._reset_form)
        tb.addWidget(new_btn)

        tb.addSeparator()

        lib_btn = QPushButton("📚  Stack Library")
        lib_btn.setToolTip("Open Stack Library (Ctrl+2)")
        lib_btn.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        tb.addWidget(lib_btn)

        tpl_btn = QPushButton("📂  Templates")
        tpl_btn.setToolTip("Open Template Library (Ctrl+3)")
        tpl_btn.clicked.connect(lambda: self._tabs.setCurrentIndex(2))
        tb.addWidget(tpl_btn)

        tb.addSeparator()

        settings_btn = QPushButton("⚙  Settings")
        settings_btn.clicked.connect(self._open_settings)
        tb.addWidget(settings_btn)

    # ------------------------------------------------------------------
    # Central UI  —  three tabs
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            "QTabBar::tab { padding: 7px 22px; font-size: 13px; }"
            "QTabBar::tab:selected { font-weight: bold; }"
        )
        outer.addWidget(self._tabs)

        # ---- Tab 1: Stack Generator ----------------------------------------
        self._tabs.addTab(self._build_stack_tab(), "🗋  Stack Generator")

        # ---- Tab 2: Stack Library ------------------------------------------
        self._stack_library_widget = StackLibraryWidget()
        self._stack_library_widget.load_requested.connect(self._load_from_library)
        self._tabs.addTab(self._stack_library_widget, "📚  Stack Library")

        # ---- Tab 3: Template Library ---------------------------------------
        self._tpl_manager = TemplateManagerWidget()
        self._tabs.addTab(self._tpl_manager, "📂  Template Library")
        self._deployment_widget = DeploymentWidget()
        self._tabs.addTab(self._deployment_widget, "Deployments")
        self._tabs.currentChanged.connect(
            lambda index: self._deployment_widget.refresh_stacks() if index == 3 else None
        )

        self._tabs.currentChanged.connect(self._on_main_tab_changed)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready.")

    # ------------------------------------------------------------------
    # Stack Generator tab contents
    # ------------------------------------------------------------------

    def _build_stack_tab(self) -> QWidget:
        tab = QWidget()
        root = QHBoxLayout(tab)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ---- Left: stack config -------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)

        lv.addWidget(QLabel("<b>Stack Configuration</b>"))
        lv.addWidget(HSeparator())

        # Stack name & environment
        name_group = QGroupBox("Stack Details")
        ng = QVBoxLayout(name_group)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._stack_name_input = QLineEdit()
        self._stack_name_input.setPlaceholderText("e.g. downloads, monitoring, arr-media")
        self._stack_name_input.textChanged.connect(self._update_preview_header)
        name_row.addWidget(self._stack_name_input, 1)
        self._slug_label = QLabel("")
        self._slug_label.setStyleSheet("color: #888; font-size: 11px;")
        name_row.addWidget(self._slug_label)
        ng.addLayout(name_row)

        env_row = QHBoxLayout()
        env_row.addWidget(QLabel("Env:"))
        self._env_input = QLineEdit(settings.stacks_env)
        self._env_input.setPlaceholderText("development")
        self._env_input.textChanged.connect(lambda _: self._update_preview_header(self._stack_name_input.text()))
        env_row.addWidget(self._env_input, 1)
        ng.addLayout(env_row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Description:"))
        self._description_input = QTextEdit()
        self._description_input.setPlaceholderText("Short description of what this stack does (written to README.md)…")
        self._description_input.setFixedHeight(60)
        self._description_input.textChanged.connect(lambda: self._refresh_preview())
        desc_row.addWidget(self._description_input, 1)
        ng.addLayout(desc_row)
        lv.addWidget(name_group)

        # Output path display
        path_group = QGroupBox("Output Path")
        pg = QVBoxLayout(path_group)
        self._path_label = QLabel("<i>Configure stacks root in Settings</i>")
        self._path_label.setWordWrap(True)
        self._path_label.setStyleSheet("font-size: 11px;")
        pg.addWidget(self._path_label)
        self._overwrite_check = QCheckBox("Overwrite existing files if stack already exists")
        pg.addWidget(self._overwrite_check)
        lv.addWidget(path_group)

        # Services list
        svc_group = QGroupBox("Services")
        sv = QVBoxLayout(svc_group)

        self._services_list = QListWidget()
        self._services_list.setAlternatingRowColors(True)
        self._services_list.setSpacing(2)
        self._services_list.itemDoubleClicked.connect(self._edit_service)
        sv.addWidget(self._services_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("＋ Add Service")
        add_btn.clicked.connect(self._add_service)
        btn_row.addWidget(add_btn)

        edit_btn = QPushButton("✎ Edit")
        edit_btn.clicked.connect(lambda: self._edit_service(self._services_list.currentItem()))
        btn_row.addWidget(edit_btn)

        up_btn = QPushButton("▲")
        up_btn.setMaximumWidth(32)
        up_btn.clicked.connect(self._move_up)
        btn_row.addWidget(up_btn)

        down_btn = QPushButton("▼")
        down_btn.setMaximumWidth(32)
        down_btn.clicked.connect(self._move_down)
        btn_row.addWidget(down_btn)

        remove_btn = QPushButton("✕ Remove")
        remove_btn.clicked.connect(self._remove_service)
        btn_row.addWidget(remove_btn)

        sv.addLayout(btn_row)
        lv.addWidget(svc_group, 1)

        # Save / Generate buttons
        action_row = QHBoxLayout()

        save_lib_btn = QPushButton("💾  Save to Library")
        save_lib_btn.setMinimumHeight(40)
        save_lib_btn.setToolTip("Save current stack configuration to the Stack Library (Ctrl+S)")
        save_lib_btn.clicked.connect(self._save_to_library)
        action_row.addWidget(save_lib_btn)

        gen_btn = QToolButton()
        generate_action = QAction("🚀  Generate Stack", self)
        generate_action.triggered.connect(lambda: self._generate())
        gen_btn.setDefaultAction(generate_action)
        gen_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        gen_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        gen_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        generate_menu = QMenu(gen_btn)
        generate_menu.addAction(generate_action)
        self._generate_sync_action = generate_menu.addAction("Generate && Sync")
        self._generate_sync_action.setToolTip(
            "Generate files, commit only generated files, and push to the destination branch's upstream"
        )
        self._generate_sync_action.triggered.connect(lambda: self._generate(sync=True))
        gen_btn.setMenu(generate_menu)
        gen_btn.setMinimumHeight(40)
        gen_btn.setStyleSheet(
            "QToolButton { background: #2a7ae2; color: white; font-weight: bold; border-radius: 5px; padding: 0 20px 0 8px; }"
            "QToolButton:hover { background: #1a5fc5; }"
            "QToolButton:disabled { background: #aaa; }"
            "QToolButton::menu-button { width: 20px; border-left: 1px solid #fff; }"
        )
        self._generate_btn = gen_btn
        action_row.addWidget(gen_btn, 1)

        lv.addLayout(action_row)

        splitter.addWidget(left)

        # ---- Right: preview -----------------------------------------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        rv.addWidget(QLabel("<b>Preview</b>"))
        rv.addWidget(HSeparator())

        self._preview_tabs = QTabWidget()
        self._preview_tabs.setDocumentMode(True)

        mono_font = QFont("Monospace", 10)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)

        def _make_preview_editor(placeholder: str) -> QPlainTextEdit:
            ed = QPlainTextEdit()
            ed.setReadOnly(True)
            ed.setFont(mono_font)
            ed.setPlaceholderText(placeholder)
            ed.setStyleSheet(
                "QPlainTextEdit { background: #282c34; color: #abb2bf; "
                "border: 1px solid #3e4451; border-radius: 4px; padding: 6px; }"
            )
            ed._highlighter = Jinja2YamlHighlighter(ed.document())
            return ed

        self._preview_rendered = _make_preview_editor(
            "Full Jinja-rendered compose.yml will appear here…"
        )
        self._preview_compose_j2 = _make_preview_editor(
            "compose.yml.j2 template will appear here…"
        )
        self._preview_stack_yml = _make_preview_editor(
            "stack.yml variables will appear here…"
        )

        self._preview_tabs.addTab(self._preview_rendered, "compose.yml (Rendered)")
        self._preview_tabs.addTab(self._preview_compose_j2, "compose.yml.j2")
        self._preview_tabs.addTab(self._preview_stack_yml, "stack.yml")
        rv.addWidget(self._preview_tabs, 1)

        # Recent stacks
        recent_group = QGroupBox("Recent Stacks")
        rg = QVBoxLayout(recent_group)
        self._recent_list = QListWidget()
        self._recent_list.setMaximumHeight(100)
        self._recent_list.itemDoubleClicked.connect(self._open_recent)
        rg.addWidget(self._recent_list)
        self._refresh_recent()
        rv.addWidget(recent_group)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        return tab

    # ------------------------------------------------------------------
    # Slug / path update
    # ------------------------------------------------------------------

    def _update_preview_header(self, text: str):
        slug = slugify(text)
        self._slug_label.setText(f"→ {slug}" if slug else "")
        self._update_path_label()
        self._refresh_preview()

    def _update_path_label(self):
        root = settings.stacks_root
        env = self._env_input.text().strip() or settings.stacks_env
        name = slugify(self._stack_name_input.text().strip())
        if root and env and name:
            path = str(Path(root) / env / name)
            self._path_label.setText(f"<b>{path}</b>")
        elif root:
            self._path_label.setText(f"<i>{root}/{env}/&lt;name&gt;</i>")
        else:
            self._path_label.setText("<i>Configure stacks root in Settings</i>")

    # ------------------------------------------------------------------
    # Services CRUD
    # ------------------------------------------------------------------

    def _add_service(self):
        dlg = ServiceDialog(self)
        if dlg.exec():
            svc = dlg.result_service()
            if any(s.name == svc.name for s in self._services):
                QMessageBox.warning(
                    self,
                    "Duplicate service",
                    f"A service named '{svc.name}' already exists in this stack.",
                )
                return
            self._services.append(svc)
            self._refresh_services_list()
            self._refresh_preview()

    def _edit_service(self, item: Optional[QListWidgetItem]):
        if item is None:
            item = self._services_list.currentItem()
        if item is None:
            return
        idx = self._services_list.row(item)
        svc = self._services[idx]
        dlg = ServiceDialog(self, existing=svc)
        if dlg.exec():
            new_svc = dlg.result_service()
            if any(s.name == new_svc.name and i != idx for i, s in enumerate(self._services)):
                QMessageBox.warning(
                    self,
                    "Duplicate service",
                    f"Another service named '{new_svc.name}' already exists.",
                )
                return
            self._services[idx] = new_svc
            self._refresh_services_list()
            self._refresh_preview()

    def _remove_service(self):
        idx = self._services_list.currentRow()
        if idx < 0:
            return
        name = self._services[idx].display_name
        reply = QMessageBox.question(
            self,
            "Remove Service",
            f"Remove service '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._services.pop(idx)
            self._refresh_services_list()
            self._refresh_preview()

    def _move_up(self):
        idx = self._services_list.currentRow()
        if idx <= 0:
            return
        self._services[idx - 1], self._services[idx] = self._services[idx], self._services[idx - 1]
        self._refresh_services_list()
        self._services_list.setCurrentRow(idx - 1)
        self._refresh_preview()

    def _move_down(self):
        idx = self._services_list.currentRow()
        if idx < 0 or idx >= len(self._services) - 1:
            return
        self._services[idx + 1], self._services[idx] = self._services[idx], self._services[idx + 1]
        self._refresh_services_list()
        self._services_list.setCurrentRow(idx + 1)
        self._refresh_preview()

    def _refresh_services_list(self):
        self._services_list.clear()
        for svc in self._services:
            text = (
                f"{svc.display_name}  ({svc.image})\n"
                f"  Port: {svc.port}  |  Restart: {svc.restart_profile}  |  Resources: {svc.resource_profile}"
            )
            item = QListWidgetItem(text)
            self._services_list.addItem(item)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _on_main_tab_changed(self, index: int):
        if index == 0:
            self._refresh_preview()

    def _refresh_preview(self):
        name_raw = self._stack_name_input.text().strip()
        name = slugify(name_raw)
        if not name or not self._services:
            self._preview_rendered.setPlainText("")
            self._preview_compose_j2.setPlainText("")
            self._preview_stack_yml.setPlainText("")
            return
        from docker_stack_generator.generator import (
            generate_compose_j2,
            generate_stack_yml,
            render_compose_preview,
        )
        cfg = self._build_stack_config(name, name_raw)
        try:
            self._preview_stack_yml.setPlainText(generate_stack_yml(cfg))
            self._preview_compose_j2.setPlainText(generate_compose_j2(cfg))
            self._preview_rendered.setPlainText(render_compose_preview(cfg))
        except Exception as exc:
            err = f"# Preview error: {exc}"
            self._preview_rendered.setPlainText(err)
            self._preview_compose_j2.setPlainText(err)
            self._preview_stack_yml.setPlainText(err)

    def _build_stack_config(self, name: str, display_name: str) -> StackConfig:
        env = self._env_input.text().strip() or settings.stacks_env
        return StackConfig(
            name=name,
            display_name=display_name,
            environment=env,
            description=self._description_input.toPlainText().strip(),
            stacks_root=settings.stacks_root,
            services=list(self._services),
            network_vars={
                "traefik": settings.get("network_traefik", "NETWORK_TRAEFIK"),
                "monitoring": settings.get("network_monitoring", "NETWORK_MONITORING"),
            },
        )

    # ------------------------------------------------------------------
    # Stack Library save / load
    # ------------------------------------------------------------------

    def _save_to_library(self):
        name_raw = self._stack_name_input.text().strip()
        name = slugify(name_raw)
        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a stack name before saving.")
            return
        if not self._services:
            QMessageBox.warning(self, "Validation", "Please add at least one service before saving.")
            return
        cfg = self._build_stack_config(name, name_raw)
        saved = stack_library.save_stack(cfg, generated=False)
        self._stack_library_widget.refresh()
        self._status.showMessage(
            f"Saved '{saved.config.display_name}' [{saved.config.environment}] to Stack Library."
        )

    def _load_from_library(self, saved: SavedStack):
        if self._services:
            reply = QMessageBox.question(
                self,
                "Load Stack",
                f"Load <b>{saved.config.display_name}</b> [{saved.config.environment}] into the generator?<br>"
                "Current unsaved services in the form will be replaced.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        cfg = saved.config
        self._stack_name_input.blockSignals(True)
        self._env_input.blockSignals(True)
        self._stack_name_input.setText(cfg.display_name)
        self._env_input.setText(cfg.environment)
        self._stack_name_input.blockSignals(False)
        self._env_input.blockSignals(False)

        self._description_input.blockSignals(True)
        self._description_input.setPlainText(getattr(cfg, "description", "") or "")
        self._description_input.blockSignals(False)

        self._services = list(cfg.services)
        self._refresh_services_list()
        self._update_preview_header(cfg.display_name)

        effective_root = settings.stacks_root or cfg.stacks_root
        if effective_root:
            target = Path(effective_root) / cfg.environment / cfg.name
            self._overwrite_check.setChecked(target.exists())

        self._tabs.setCurrentIndex(0)
        self._status.showMessage(
            f"Loaded '{cfg.display_name}' [{cfg.environment}] from Stack Library."
        )

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def _generate(self, sync: bool = False):
        if self._generate_worker is not None and self._generate_worker.isRunning():
            return
        name_raw = self._stack_name_input.text().strip()
        name = slugify(name_raw)

        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a stack name.")
            return
        if not self._services:
            QMessageBox.warning(self, "Validation", "Please add at least one service.")
            return
        if not settings.stacks_root:
            QMessageBox.warning(
                self,
                "Settings required",
                "Please configure the stacks root directory in Settings first.",
            )
            self._open_settings()
            return

        cfg = self._build_stack_config(name, name_raw)
        overwrite = self._overwrite_check.isChecked()

        if cfg.stack_dir.exists() and not overwrite:
            reply = QMessageBox.question(
                self,
                "Stack already exists",
                f"The stack directory already exists:\n{cfg.stack_dir}\n\nOverwrite generated files?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            overwrite = True

        self._generate_btn.setEnabled(False)
        self._status.showMessage("Generating and syncing stack…" if sync else "Generating stack…")

        self._generate_worker = GenerateWorker(cfg, overwrite, sync=sync)
        self._generate_worker.done.connect(self._on_generate_done)
        self._generate_worker.error.connect(self._on_generate_error)
        self._generate_worker.start()

    def _on_generate_done(self, created: list[str]):
        self._generate_btn.setEnabled(True)
        worker = self._generate_worker
        if worker is None:
            return
        cfg = worker.cfg
        name = cfg.name
        path = str(cfg.stack_dir)
        settings.add_recent_stack(name, path)
        stack_library.save_stack(cfg, generated=True)
        self._refresh_recent()
        self._stack_library_widget.refresh()

        created_str = "\n".join(f"  • {p}" for p in created)
        if worker.sync_error:
            QMessageBox.warning(
                self, "Stack Generated - Sync Failed",
                f"Stack '{cfg.environment}/{name}' was generated at:\n{path}\n"
                f"and saved to Stack Library, but repository sync failed.\n\n{worker.sync_error}",
            )
            self._status.showMessage(f"Stack '{name}' generated, but sync failed.")
            return
        sync_note = f"\n\n{worker.sync_result}" if worker.sync else ""
        QMessageBox.information(
            self,
            "Stack Generated & Synced!" if worker.sync else "Stack Generated!",
            f"Stack scaffold created successfully and saved to Stack Library.\n\nFiles written to:\n  {path}\n\n{created_str}{sync_note}",
        )
        self._status.showMessage(f"Stack '{name}' generated at {path}{sync_note}")

    def _on_generate_error(self, msg: str):
        self._generate_btn.setEnabled(True)
        QMessageBox.critical(self, "Generation Error", msg)
        self._status.showMessage("Generation failed.")

    # ------------------------------------------------------------------
    # Recent stacks
    # ------------------------------------------------------------------

    def _refresh_recent(self):
        self._recent_list.clear()
        for entry in settings.get("recent_stacks", []):
            item = QListWidgetItem(f"{entry['name']}  →  {entry['path']}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._recent_list.addItem(item)

    def _open_recent(self, item: QListWidgetItem):
        entry = item.data(Qt.ItemDataRole.UserRole)
        path = entry.get("path", "")
        if path and Path(path).exists():
            os.system(f"xdg-open '{path}'")
        else:
            QMessageBox.warning(self, "Not found", f"Directory not found:\n{path}")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            if not self._services:
                self._env_input.setText(settings.stacks_env)
            self._update_path_label()
            self._refresh_preview()
            self._stack_library_widget.refresh()
            self._tpl_manager.refresh()

    def _check_settings(self):
        if not settings.stacks_root:
            self._status.showMessage(
                "⚠  Stacks root directory not configured. Open Settings (Ctrl+,) to set it."
            )
        else:
            self._update_path_label()

    # ------------------------------------------------------------------
    # Form reset
    # ------------------------------------------------------------------

    def _reset_form(self):
        if self._services:
            reply = QMessageBox.question(
                self,
                "New Stack",
                "Start a new stack? Current services will be cleared.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._stack_name_input.clear()
        self._env_input.setText(settings.stacks_env)
        self._description_input.clear()
        self._services.clear()
        self._refresh_services_list()
        self._preview_rendered.clear()
        self._preview_compose_j2.clear()
        self._preview_stack_yml.clear()
        self._overwrite_check.setChecked(False)
        self._tabs.setCurrentIndex(0)
        self._status.showMessage("Ready.")

    # ------------------------------------------------------------------
    # Geometry persistence
    # ------------------------------------------------------------------

    def _restore_geometry(self):
        geom = settings.get("window_geometry")
        if geom:
            try:
                self.restoreGeometry(QByteArray.fromHex(bytes(geom, "ascii")))
            except Exception:
                pass

    def closeEvent(self, event):
        if self._generate_worker is not None and self._generate_worker.isRunning():
            QMessageBox.warning(self, "Generation in progress", "Wait for generation and repository sync to finish before closing.")
            event.ignore()
            return
        if not self._deployment_widget.prepare_close():
            event.ignore()
            return
        settings.set("window_geometry", bytes(self.saveGeometry().toHex()).decode("ascii"))
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # About
    # ------------------------------------------------------------------

    def _show_about(self):
        from docker_stack_generator import __version__
        QMessageBox.about(
            self,
            "Docker Stack Generator",
            f"<b>Docker Stack Generator</b> v{__version__}<br><br>"
            "A GUI tool to scaffold Docker Swarm stack files.<br>"
            "Replaces the <code>new-stack.sh</code> bash script with a "
            "persistent, user-friendly Python interface.<br><br>"
            "Built with PyQt6.",
        )

    # ------------------------------------------------------------------
    # Library backup / restore / export  (delegated to the library widget)
    # ------------------------------------------------------------------

    def _backup_library(self):
        self._tabs.setCurrentIndex(1)
        self._stack_library_widget._backup_library()

    def _restore_library(self):
        self._tabs.setCurrentIndex(1)
        self._stack_library_widget._restore_library()

    def _export_csv(self):
        self._tabs.setCurrentIndex(1)
        self._stack_library_widget._export_csv()
