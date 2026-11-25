"""
Service editor dialog – add or edit a single service within a stack.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from docker_stack_generator.config import settings
from docker_stack_generator.generator import ServiceConfig, split_image, slugify
from docker_stack_generator.widgets import MetadataWorker, ProfileSelector, SearchWorker


class ServiceDialog(QDialog):
    """
    Modal dialog to configure a single service.
    Contains:
      - Service name
      - Docker Hub search + result list
      - Manual image entry
      - Port detection / override
      - Restart & resource profile selectors
    """

    def __init__(self, parent=None, existing: Optional[ServiceConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("Add Service" if existing is None else "Edit Service")
        self.setMinimumSize(820, 580)
        self._existing = existing
        self._metadata: dict = {}
        self._search_worker: Optional[SearchWorker] = None
        self._meta_worker: Optional[MetadataWorker] = None
        self._search_results: list[dict] = []

        self._build_ui()
        if existing:
            self._populate(existing)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ---- Left panel: search -------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)

        lv.addWidget(QLabel("<b>Docker Hub Search</b>"))

        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search term (e.g. qbittorrent, redis…)")
        self._search_input.returnPressed.connect(self._start_search)
        search_row.addWidget(self._search_input)

        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._start_search)
        search_row.addWidget(self._search_btn)
        lv.addLayout(search_row)

        # Search history combo
        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("History:"))
        self._history_combo = QComboBox()
        self._history_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._refresh_history()
        self._history_combo.currentTextChanged.connect(
            lambda t: self._search_input.setText(t) if t else None
        )
        history_row.addWidget(self._history_combo)
        lv.addLayout(history_row)

        self._result_list = QListWidget()
        self._result_list.setAlternatingRowColors(True)
        self._result_list.itemClicked.connect(self._on_result_selected)
        lv.addWidget(self._result_list, 1)

        self._search_status = QLabel("")
        self._search_status.setWordWrap(True)
        lv.addWidget(self._search_status)

        splitter.addWidget(left)

        # ---- Right panel: service details ---------------------------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 4, 4, 4)

        rv.addWidget(QLabel("<b>Service Configuration</b>"))

        form_group = QGroupBox()
        form = QFormLayout(form_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g. qbittorrent")
        form.addRow("Service name:", self._name_input)

        image_row = QHBoxLayout()
        self._image_input = QLineEdit()
        self._image_input.setPlaceholderText("e.g. linuxserver/qbittorrent:latest")
        self._image_input.textChanged.connect(self._on_image_changed)
        image_row.addWidget(self._image_input)

        self._fetch_btn = QPushButton("Fetch Metadata")
        self._fetch_btn.clicked.connect(self._fetch_metadata)
        image_row.addWidget(self._fetch_btn)
        form.addRow("Docker image:", image_row)

        port_row = QHBoxLayout()
        self._port_input = QLineEdit()
        self._port_input.setPlaceholderText("e.g. 8080  (or TODO)")
        port_row.addWidget(self._port_input)

        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(80)
        self._port_combo.currentTextChanged.connect(
            lambda t: self._port_input.setText(t) if t else None
        )
        port_row.addWidget(QLabel("Detected:"))
        port_row.addWidget(self._port_combo)
        form.addRow("Port:", port_row)

        self._desc_input = QLineEdit()
        self._desc_input.setPlaceholderText("Short description (optional)")
        form.addRow("Description:", self._desc_input)

        rv.addWidget(form_group)

        profile_group = QGroupBox("Deployment Profiles")
        pgl = QVBoxLayout(profile_group)
        default_restart = settings.get("default_restart_profile", "medium")
        default_resource = settings.get("default_resource_profile", "medium")
        self._restart_profile = ProfileSelector("Restart policy:", default_restart)
        self._resource_profile = ProfileSelector("Resources:", default_resource)
        pgl.addWidget(self._restart_profile)
        pgl.addWidget(self._resource_profile)
        rv.addWidget(profile_group)

        self._meta_label = QLabel("No metadata fetched yet.")
        self._meta_label.setWordWrap(True)
        self._meta_label.setStyleSheet("color: #666; font-size: 11px;")
        rv.addWidget(self._meta_label)

        rv.addStretch()

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        # ---- Dialog buttons -----------------------------------------------
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Populate existing data
    # ------------------------------------------------------------------

    def _populate(self, svc: ServiceConfig):
        self._name_input.setText(svc.display_name)
        self._image_input.setText(svc.image)
        self._port_input.setText(svc.port)
        self._desc_input.setText(svc.description)
        self._restart_profile.set_value(svc.restart_profile)
        self._resource_profile.set_value(svc.resource_profile)
        self._metadata = svc.metadata

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _refresh_history(self):
        self._history_combo.blockSignals(True)
        self._history_combo.clear()
        self._history_combo.addItem("")
        for term in settings.get("search_history", []):
            self._history_combo.addItem(term)
        self._history_combo.blockSignals(False)

    def _start_search(self):
        query = self._search_input.text().strip()
        if not query:
            return
        settings.add_search_history(query)
        self._refresh_history()

        self._search_btn.setEnabled(False)
        self._search_status.setText("Searching…")
        self._result_list.clear()
        self._search_results = []

        self._search_worker = SearchWorker(query)
        self._search_worker.results_ready.connect(self._on_search_done)
        self._search_worker.error.connect(self._on_search_error)
        self._search_worker.start()

    def _on_search_done(self, results: list[dict]):
        self._search_btn.setEnabled(True)
        self._search_results = results
        self._result_list.clear()
        if not results:
            self._search_status.setText("No results found.")
            return
        for r in results:
            official = " ✓" if r.get("official") else ""
            stars = r.get("star_count", 0) or 0
            stars_str = f" ★{stars:,}" if stars else ""
            text = f"{r['name']}{official}{stars_str}"
            if r.get("short_description"):
                text += f"\n  {r['short_description'][:80]}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self._result_list.addItem(item)
        self._search_status.setText(f"{len(results)} result(s). Click one to select.")

    def _on_search_error(self, msg: str):
        self._search_btn.setEnabled(True)
        self._search_status.setText(f"Search error: {msg}")

    def _on_result_selected(self, item: QListWidgetItem):
        r = item.data(Qt.ItemDataRole.UserRole)
        name = r["name"].split("/")[-1]
        if not self._name_input.text().strip():
            self._name_input.setText(name)
        image = r["name"]
        self._image_input.setText(image)
        if r.get("short_description"):
            self._desc_input.setText(r["short_description"][:120])
        # Trigger metadata fetch
        QTimer.singleShot(100, self._fetch_metadata)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _on_image_changed(self, text: str):
        self._port_combo.clear()
        self._meta_label.setText("")

    def _fetch_metadata(self):
        image = self._image_input.text().strip()
        if not image:
            return
        self._fetch_btn.setEnabled(False)
        self._meta_label.setText("Fetching metadata from Docker Hub…")

        self._meta_worker = MetadataWorker(image)
        self._meta_worker.done.connect(self._on_meta_done)
        self._meta_worker.error.connect(self._on_meta_error)
        self._meta_worker.start()

    def _on_meta_done(self, meta: dict, ports: list[str]):
        self._fetch_btn.setEnabled(True)
        self._metadata = meta

        # Update port combo
        self._port_combo.clear()
        for p in ports:
            self._port_combo.addItem(p)
        if ports:
            self._port_input.setText(ports[0])
            port_src = f"image metadata ({len(ports)} port(s) detected)"
        else:
            port_src = "not detected"

        # Summary label
        desc = meta.get("short_description") or meta.get("description") or ""
        stars = meta.get("stars") or 0
        pulls = meta.get("pulls") or 0
        os_ = meta.get("os", "")
        arch = meta.get("architecture", "")
        compressed = meta.get("compressed_size")
        size_str = f"{compressed / 1_000_000:.1f} MB" if compressed else "unknown"
        summary = (
            f"<b>{meta['image']}</b> | ★ {stars:,} | {pulls:,} pulls<br>"
            f"OS: {os_}/{arch} | Compressed size: {size_str} | Ports: {port_src}<br>"
            f"{desc[:160]}"
        )
        self._meta_label.setText(summary)

        # Auto-fill description if empty
        if not self._desc_input.text().strip() and desc:
            self._desc_input.setText(desc[:120])

    def _on_meta_error(self, msg: str):
        self._fetch_btn.setEnabled(True)
        self._meta_label.setText(f"<span style='color:red;'>Metadata error: {msg}</span>")

    # ------------------------------------------------------------------
    # Validation & result
    # ------------------------------------------------------------------

    def _on_accept(self):
        display_name = self._name_input.text().strip()
        if not display_name:
            QMessageBox.warning(self, "Validation", "Service name cannot be empty.")
            return
        image = self._image_input.text().strip()
        if not image:
            QMessageBox.warning(self, "Validation", "Docker image cannot be empty.")
            return
        self.accept()

    def result_service(self) -> ServiceConfig:
        display_name = self._name_input.text().strip()
        name = slugify(display_name)
        image = self._image_input.text().strip()
        image_base, image_tag = split_image(image)
        port = self._port_input.text().strip() or "TODO"
        port_source = "manual"
        if self._port_combo.count() > 0 and port in [
            self._port_combo.itemText(i) for i in range(self._port_combo.count())
        ]:
            port_source = "image metadata"
        return ServiceConfig(
            name=name,
            display_name=display_name,
            image=image,
            image_base=image_base,
            image_tag=image_tag,
            description=self._desc_input.text().strip(),
            port=port,
            port_source=port_source,
            restart_profile=self._restart_profile.value(),
            resource_profile=self._resource_profile.value(),
            metadata=self._metadata,
        )
