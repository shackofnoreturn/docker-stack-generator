"""
Service editor dialog – add or edit a single service within a stack.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
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
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from docker_stack_generator.config import settings
from docker_stack_generator.generator import (
    PlacementConstraint,
    PortMapping,
    ServiceConfig,
    split_image,
    slugify,
)
from docker_stack_generator.widgets import MetadataWorker, ProfileSelector, SearchWorker


# ---------------------------------------------------------------------------
# Small reusable list-editor helper
# ---------------------------------------------------------------------------

class _TagListEditor(QWidget):
    """
    A compact inline list editor: input field + Add/Remove buttons +
    a small list widget below.  Used for plain-string lists (networks,
    secrets).
    """

    def __init__(self, placeholder: str = "", max_height: int = 90, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.returnPressed.connect(self._add)
        row.addWidget(self._input, 1)

        add_btn = QPushButton("＋")
        add_btn.setMaximumWidth(30)
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)

        rm_btn = QPushButton("✕")
        rm_btn.setMaximumWidth(30)
        rm_btn.clicked.connect(self._remove)
        row.addWidget(rm_btn)

        layout.addLayout(row)

        self._list = QListWidget()
        self._list.setMaximumHeight(max_height)
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list)

    def _add(self):
        text = self._input.text().strip()
        if not text:
            return
        # avoid duplicates
        existing = [self._list.item(i).text() for i in range(self._list.count())]
        if text not in existing:
            self._list.addItem(QListWidgetItem(text))
        self._input.clear()

    def _remove(self):
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def values(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_values(self, items: list[str]) -> None:
        self._list.clear()
        for v in items:
            self._list.addItem(QListWidgetItem(v))


# ---------------------------------------------------------------------------
# Port-mapping table editor
# ---------------------------------------------------------------------------

class _PortTableEditor(QWidget):
    """
    Editor for a list of PortMapping objects.
    Shows a list of "published → target  protocol  mode" rows and
    provides Add / Edit (inline via a row of fields) / Remove.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Field row for input
        fields_row = QHBoxLayout()

        self._target_in = QLineEdit()
        self._target_in.setPlaceholderText("Target (container)")
        self._target_in.setMaximumWidth(130)

        self._published_in = QLineEdit()
        self._published_in.setPlaceholderText("Published (host)")
        self._published_in.setMaximumWidth(130)

        self._proto_combo = QComboBox()
        self._proto_combo.addItems(["tcp", "udp"])
        self._proto_combo.setMaximumWidth(60)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["host", "ingress"])
        self._mode_combo.setMaximumWidth(80)

        add_btn = QPushButton("＋ Add port")
        add_btn.clicked.connect(self._add)

        rm_btn = QPushButton("✕ Remove")
        rm_btn.clicked.connect(self._remove)

        for w in (
            QLabel("Target:"), self._target_in,
            QLabel("Published:"), self._published_in,
            QLabel("Protocol:"), self._proto_combo,
            QLabel("Mode:"), self._mode_combo,
            add_btn, rm_btn,
        ):
            fields_row.addWidget(w)
        fields_row.addStretch()
        layout.addLayout(fields_row)

        self._list = QListWidget()
        self._list.setMaximumHeight(100)
        self._list.setAlternatingRowColors(True)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

    def _add(self):
        target = self._target_in.text().strip()
        published = self._published_in.text().strip()
        if not target or not published:
            return
        proto = self._proto_combo.currentText()
        mode = self._mode_combo.currentText()
        pm = PortMapping(target=target, published=published, protocol=proto, mode=mode)
        item = QListWidgetItem(self._pm_label(pm))
        item.setData(Qt.ItemDataRole.UserRole, pm)
        self._list.addItem(item)
        self._target_in.clear()
        self._published_in.clear()

    def _remove(self):
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def _on_item_clicked(self, item: QListWidgetItem):
        pm: PortMapping = item.data(Qt.ItemDataRole.UserRole)
        self._target_in.setText(pm.target)
        self._published_in.setText(pm.published)
        self._proto_combo.setCurrentText(pm.protocol)
        self._mode_combo.setCurrentText(pm.mode)

    @staticmethod
    def _pm_label(pm: PortMapping) -> str:
        return f"{pm.published} → {pm.target}  |  {pm.protocol}  |  {pm.mode}"

    def values(self) -> list[PortMapping]:
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]

    def set_values(self, ports: list[PortMapping]) -> None:
        self._list.clear()
        for pm in ports:
            item = QListWidgetItem(self._pm_label(pm))
            item.setData(Qt.ItemDataRole.UserRole, pm)
            self._list.addItem(item)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class ServiceDialog(QDialog):
    """
    Modal dialog to configure a single service.
    Contains:
      - Service name + image + port
      - Docker Hub search
      - Restart & resource profile selectors
      - Runtime directories
      - Per-service networks
      - Per-service secrets
      - Per-service ports (long-form)
      - Placement constraint (role + label)
    """

    def __init__(self, parent=None, existing: Optional[ServiceConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("Add Service" if existing is None else "Edit Service")
        self.setMinimumSize(900, 640)
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

        # ---- Right panel: tabbed config -----------------------------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 4, 4, 4)

        rv.addWidget(QLabel("<b>Service Configuration</b>"))

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        rv.addWidget(tabs, 1)

        tabs.addTab(self._build_basic_tab(), "⚙  Basic")
        tabs.addTab(self._build_networking_tab(), "🌐  Networking")
        tabs.addTab(self._build_deploy_tab(), "🚀  Deployment")

        self._meta_label = QLabel("No metadata fetched yet.")
        self._meta_label.setWordWrap(True)
        self._meta_label.setStyleSheet("color: #666; font-size: 11px;")
        rv.addWidget(self._meta_label)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # ---- Dialog buttons -----------------------------------------------
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Tab: Basic (name, image, port, description, runtime dirs)
    # ------------------------------------------------------------------

    def _build_basic_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        form_group = QGroupBox("Service Details")
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
        self._port_input.setPlaceholderText("e.g. 8080  (or TODO) — used in stack.yml env var")
        port_row.addWidget(self._port_input)

        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(80)
        self._port_combo.currentTextChanged.connect(
            lambda t: self._port_input.setText(t) if t else None
        )
        port_row.addWidget(QLabel("Detected:"))
        port_row.addWidget(self._port_combo)
        form.addRow("Primary port:", port_row)

        self._desc_input = QLineEdit()
        self._desc_input.setPlaceholderText("Short description (optional)")
        form.addRow("Description:", self._desc_input)

        layout.addWidget(form_group)

        # Runtime directories
        rdir_group = QGroupBox("Runtime Directories")
        rdir_group.setToolTip(
            "Subdirectories under <stack>/<service>/  that will be created at runtime.\n"
            "Leave empty to add just <stack>/<service>/ itself."
        )
        rdl = QVBoxLayout(rdir_group)

        rdir_hint = QLabel(
            "<i>Each entry creates <b>&lt;stack&gt;/&lt;service&gt;/&lt;subdir&gt;</b>. "
            "Leave subdir blank to include the base path.</i>"
        )
        rdir_hint.setWordWrap(True)
        rdir_hint.setStyleSheet("color: #666; font-size: 11px;")
        rdl.addWidget(rdir_hint)

        self._rdir_list = QListWidget()
        self._rdir_list.setMaximumHeight(80)
        self._rdir_list.setAlternatingRowColors(True)
        rdl.addWidget(self._rdir_list)

        rdir_input_row = QHBoxLayout()
        self._rdir_input = QLineEdit()
        self._rdir_input.setPlaceholderText("subdirectory name (or leave blank for base path)")
        self._rdir_input.returnPressed.connect(self._add_rdir)
        rdir_input_row.addWidget(self._rdir_input, 1)

        rdir_add_btn = QPushButton("＋ Add")
        rdir_add_btn.setMaximumWidth(60)
        rdir_add_btn.clicked.connect(self._add_rdir)
        rdir_input_row.addWidget(rdir_add_btn)

        rdir_remove_btn = QPushButton("✕")
        rdir_remove_btn.setMaximumWidth(32)
        rdir_remove_btn.clicked.connect(self._remove_rdir)
        rdir_input_row.addWidget(rdir_remove_btn)

        rdl.addLayout(rdir_input_row)
        layout.addWidget(rdir_group)
        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Tab: Networking (extra networks, secrets, ports)
    # ------------------------------------------------------------------

    def _build_networking_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Extra networks
        nets_group = QGroupBox("Additional Networks")
        nets_group.setToolTip(
            "Networks added specifically to this service on top of the stack defaults.\n"
            "They will be defined in the global networks: block at the bottom of the compose file."
        )
        nl = QVBoxLayout(nets_group)
        nets_hint = QLabel(
            "<i>Stack-level networks (traefik, monitoring) are always included. "
            "Add extra names here if this service needs more.</i>"
        )
        nets_hint.setWordWrap(True)
        nets_hint.setStyleSheet("color: #666; font-size: 11px;")
        nl.addWidget(nets_hint)
        self._networks_editor = _TagListEditor(placeholder="e.g. database", max_height=70)
        nl.addWidget(self._networks_editor)
        layout.addWidget(nets_group)

        # Secrets
        secrets_group = QGroupBox("Secrets")
        secrets_group.setToolTip(
            "Docker Swarm secrets to mount into this service.\n"
            "They will appear in the service's secrets: block and in the global secrets: block.\n"
            "Stub files will be written to secrets/<name>.txt on disk."
        )
        sl = QVBoxLayout(secrets_group)
        secrets_hint = QLabel(
            "<i>Enter a secret name, e.g. <b>my_api_token</b>. "
            "Stub files are created under <code>secrets/</code>; "
            "replace them with real values before deploying.</i>"
        )
        secrets_hint.setWordWrap(True)
        secrets_hint.setStyleSheet("color: #666; font-size: 11px;")
        sl.addWidget(secrets_hint)
        self._secrets_editor = _TagListEditor(placeholder="e.g. cloudflare_api_token", max_height=70)
        sl.addWidget(self._secrets_editor)
        layout.addWidget(secrets_group)

        # Ports
        ports_group = QGroupBox("Published Ports (long-form)")
        ports_group.setToolTip(
            "Long-form port mappings written directly into the compose file.\n"
            "Target = container port, Published = host port."
        )
        pl = QVBoxLayout(ports_group)
        self._ports_editor = _PortTableEditor()
        pl.addWidget(self._ports_editor)
        layout.addWidget(ports_group)

        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Tab: Deployment (profiles, constraint)
    # ------------------------------------------------------------------

    def _build_deploy_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        profile_group = QGroupBox("Deployment Profiles")
        pgl = QVBoxLayout(profile_group)
        default_restart = settings.get("default_restart_profile", "medium")
        default_resource = settings.get("default_resource_profile", "medium")
        self._restart_profile = ProfileSelector("Restart policy:", default_restart)
        self._resource_profile = ProfileSelector("Resources:", default_resource)
        pgl.addWidget(self._restart_profile)
        pgl.addWidget(self._resource_profile)
        layout.addWidget(profile_group)

        # Placement constraint
        constraint_group = QGroupBox("Placement Constraint")
        constraint_group.setToolTip(
            "Restricts this service to nodes matching the given role and/or label."
        )
        cgl = QVBoxLayout(constraint_group)

        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("Node role:"))
        self._role_combo = QComboBox()
        self._role_combo.addItems(["(none)", "worker", "manager"])
        self._role_combo.setMaximumWidth(120)
        role_row.addWidget(self._role_combo)
        role_row.addStretch()
        cgl.addLayout(role_row)

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Node label key:"))
        self._label_input = QLineEdit()
        self._label_input.setPlaceholderText("e.g. myapp  →  node.labels.myapp == true")
        self._label_input.setMaximumWidth(300)
        label_row.addWidget(self._label_input)
        hint = QLabel("<i>(leave blank for no label constraint)</i>")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        label_row.addWidget(hint)
        label_row.addStretch()
        cgl.addLayout(label_row)

        layout.addWidget(constraint_group)
        layout.addStretch()
        return tab

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

        self._rdir_list.clear()
        for rd in (svc.runtime_dirs or []):
            self._rdir_list.addItem(QListWidgetItem(rd))

        self._networks_editor.set_values(svc.networks or [])
        self._secrets_editor.set_values(svc.secrets or [])
        self._ports_editor.set_values(svc.ports or [])

        # Constraint
        c = svc.constraint
        if c:
            if c.role and c.role != "(none)":
                self._role_combo.setCurrentText(c.role)
            else:
                self._role_combo.setCurrentText("(none)")
            self._label_input.setText(c.label_key or "")

    # ------------------------------------------------------------------
    # Runtime directories
    # ------------------------------------------------------------------

    def _add_rdir(self):
        subdir = self._rdir_input.text().strip().strip("/")
        self._rdir_list.addItem(QListWidgetItem(subdir))
        self._rdir_input.clear()

    def _remove_rdir(self):
        row = self._rdir_list.currentRow()
        if row >= 0:
            self._rdir_list.takeItem(row)

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

        self._port_combo.clear()
        for p in ports:
            self._port_combo.addItem(p)
        if ports:
            self._port_input.setText(ports[0])
            port_src = f"image metadata ({len(ports)} port(s) detected)"
        else:
            port_src = "not detected"

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

        runtime_dirs = [
            self._rdir_list.item(i).text()
            for i in range(self._rdir_list.count())
        ]

        role_text = self._role_combo.currentText()
        constraint = PlacementConstraint(
            role="" if role_text == "(none)" else role_text,
            label_key=self._label_input.text().strip(),
        )

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
            runtime_dirs=runtime_dirs,
            metadata=self._metadata,
            networks=self._networks_editor.values(),
            secrets=self._secrets_editor.values(),
            ports=self._ports_editor.values(),
            constraint=constraint,
        )
