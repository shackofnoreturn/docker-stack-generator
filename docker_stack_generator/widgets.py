"""
Reusable PyQt6 widgets for the Docker Stack Generator.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

class GenerateButton(QToolButton):
    """Shared generation split button for the generator and saved stacks."""

    def __init__(self, generate, sync, parent=None):
        super().__init__(parent)
        action = QAction("🚀  Generate Stack", self)
        action.triggered.connect(lambda: generate())
        self.setDefaultAction(action)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        menu = QMenu(self)
        menu.addAction(action)
        self.sync_action = menu.addAction("Generate && Sync")
        self.sync_action.setToolTip(
            "Generate files, commit only generated files, and push to the destination branch's upstream"
        )
        self.sync_action.triggered.connect(lambda: sync())
        self.setMenu(menu)
        self.setMinimumHeight(40)
        self.setStyleSheet(
            "QToolButton { background: #2a7ae2; color: white; font-weight: bold; border-radius: 5px; padding: 0 20px 0 8px; }"
            "QToolButton:hover { background: #1a5fc5; }"
            "QToolButton:disabled { background: #aaa; }"
            "QToolButton::menu-button { width: 20px; border-left: 1px solid #fff; }"
        )


# ---------------------------------------------------------------------------
# Profile selector  (low / medium / high)
# ---------------------------------------------------------------------------

class ProfileSelector(QWidget):
    """Compact labeled dropdown for low/medium/high profile choices."""

    def __init__(self, label: str, default: str = "medium", parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(label)
        lbl.setMinimumWidth(90)
        self._layout.addWidget(lbl)

        self.combo = QComboBox()
        for p in ("low", "medium", "high"):
            self.combo.addItem(p.capitalize(), p)
        self.set_value(default)
        self._layout.addWidget(self.combo)

    def value(self) -> str:
        return self.combo.currentData()

    def set_value(self, v: str) -> None:
        idx = {"low": 0, "medium": 1, "high": 2}.get(v.lower(), 1)
        self.combo.setCurrentIndex(idx)


# ---------------------------------------------------------------------------
# Horizontal separator
# ---------------------------------------------------------------------------

class HSeparator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)


# ---------------------------------------------------------------------------
# Collapsible section
# ---------------------------------------------------------------------------

class CollapsibleSection(QWidget):
    """A button that shows/hides its child widget."""

    def __init__(self, title: str, parent=None, expanded: bool = True):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toggle = QPushButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.clicked.connect(self._on_toggle)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_toggle_text(title, expanded)
        layout.addWidget(self._toggle)

        self._title = title
        self._content = QWidget()
        self._content.setVisible(expanded)
        layout.addWidget(self._content)

        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 4, 0, 4)

    def _on_toggle(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._update_toggle_text(self._title, checked)

    def _update_toggle_text(self, title: str, expanded: bool) -> None:
        arrow = "▾" if expanded else "▸"
        self._toggle.setText(f"  {arrow}  {title}")
        self._toggle.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; border: none;"
            " background: transparent; padding: 4px; }"
        )

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def add_widget(self, w: QWidget) -> None:
        self._content_layout.addWidget(w)


# ---------------------------------------------------------------------------
# Worker threads (keep UI responsive)
# ---------------------------------------------------------------------------

class SearchWorker(QThread):
    """Background Docker Hub search."""
    results_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        from docker_stack_generator import dockerhub
        try:
            results = dockerhub.search(self.query)
            self.results_ready.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


class MetadataWorker(QThread):
    """Background image metadata + port detection."""
    done = pyqtSignal(dict, list)   # metadata dict, port list
    error = pyqtSignal(str)

    def __init__(self, image: str):
        super().__init__()
        self.image = image

    def run(self):
        from docker_stack_generator import dockerhub
        try:
            meta = dockerhub.fetch_metadata(self.image)
            ports: list[str] = []
            import re
            for key in meta.get("exposed_ports", {}):
                m = re.match(r"^(\d+)/tcp$", key)
                if m:
                    ports.append(m.group(1))
            ports.sort(key=int)
            self.done.emit(meta, ports)
        except Exception as exc:
            self.error.emit(str(exc))


class GenerateWorker(QThread):
    """Background stack generation with optional repository commit and push."""
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, cfg, overwrite: bool, sync: bool = False):
        super().__init__()
        self.cfg = cfg
        self.overwrite = overwrite
        self.sync = sync
        self.sync_result = ""
        self.sync_error = ""

    def run(self):
        from docker_stack_generator import generator
        from docker_stack_generator.repository_sync import RepositorySync, RepositorySyncError
        try:
            repository = RepositorySync.prepare(self.cfg) if self.sync else None
            created = generator.generate_stack(self.cfg, overwrite=self.overwrite)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        if repository is not None:
            try:
                self.sync_result = repository.sync(self.cfg, created)
            except RepositorySyncError as exc:
                self.sync_error = str(exc)
        self.done.emit(created)
