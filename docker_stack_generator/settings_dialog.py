"""
Settings dialog – configure defaults and paths.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from docker_stack_generator.config import settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(540)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # --- Paths tab -------------------------------------------------------
        paths_tab = QWidget()
        pf = QFormLayout(paths_tab)

        root_row = QHBoxLayout()
        self._stacks_root = QLineEdit()
        self._stacks_root.setPlaceholderText("/path/to/shackhq-docker-stacks/stacks")
        root_row.addWidget(self._stacks_root)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_root)
        root_row.addWidget(browse_btn)
        pf.addRow("Stacks root directory:", root_row)

        self._stacks_env = QLineEdit()
        self._stacks_env.setPlaceholderText("development")
        pf.addRow("Environment (sub-directory):", self._stacks_env)

        note = QLabel(
            "<i>Stack files will be written to: &lt;stacks root&gt;/&lt;environment&gt;/&lt;stack name&gt;</i>"
        )
        note.setWordWrap(True)
        pf.addRow("", note)

        tabs.addTab(paths_tab, "Paths")

        # --- Defaults tab ----------------------------------------------------
        defaults_tab = QWidget()
        df = QFormLayout(defaults_tab)

        self._default_restart = QComboBox()
        for p in ("low", "medium", "high"):
            self._default_restart.addItem(p.capitalize(), p)
        df.addRow("Default restart profile:", self._default_restart)

        self._default_resource = QComboBox()
        for p in ("low", "medium", "high"):
            self._default_resource.addItem(p.capitalize(), p)
        df.addRow("Default resource profile:", self._default_resource)

        tabs.addTab(defaults_tab, "Defaults")

        # --- Networks tab ----------------------------------------------------
        nets_tab = QWidget()
        nf = QFormLayout(nets_tab)

        self._net_traefik = QLineEdit()
        nf.addRow("Traefik network var:", self._net_traefik)

        self._net_monitoring = QLineEdit()
        nf.addRow("Monitoring network var:", self._net_monitoring)

        tabs.addTab(nets_tab, "Networks")

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _browse_root(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Stacks Root Directory",
            self._stacks_root.text() or "",
        )
        if path:
            self._stacks_root.setText(path)

    def _load(self):
        self._stacks_root.setText(settings.stacks_root)
        self._stacks_env.setText(settings.stacks_env)
        idx_map = {"low": 0, "medium": 1, "high": 2}
        self._default_restart.setCurrentIndex(
            idx_map.get(settings.get("default_restart_profile", "medium"), 1)
        )
        self._default_resource.setCurrentIndex(
            idx_map.get(settings.get("default_resource_profile", "medium"), 1)
        )
        self._net_traefik.setText(settings.get("network_traefik", "NETWORK_TRAEFIK"))
        self._net_monitoring.setText(settings.get("network_monitoring", "NETWORK_MONITORING"))

    def _on_accept(self):
        settings.update(
            {
                "stacks_root": self._stacks_root.text().strip(),
                "stacks_env": self._stacks_env.text().strip() or "development",
                "default_restart_profile": self._default_restart.currentData(),
                "default_resource_profile": self._default_resource.currentData(),
                "network_traefik": self._net_traefik.text().strip() or "NETWORK_TRAEFIK",
                "network_monitoring": self._net_monitoring.text().strip() or "NETWORK_MONITORING",
            }
        )
        self.accept()
