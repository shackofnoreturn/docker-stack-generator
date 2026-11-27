"""Stack and service endpoint monitoring with encrypted per-service credentials."""
from __future__ import annotations

import json

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from docker_stack_generator.credential_vault import CredentialVault, VaultError
from docker_stack_generator.monitoring import (
    MonitorResult, MonitoringStore, MonitorTarget, check_targets, discover_targets,
    stack_state, validate_endpoint,
)
from docker_stack_generator.stack_library import stack_library
from docker_stack_generator.vault_ui import unlock_vault
from docker_stack_generator.secret_widgets import edit_secret
from docker_stack_generator.secret_store import read_secret


class _MonitorWorker(QThread):
    results = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, targets, credentials, parent=None):
        super().__init__(parent)
        self.targets = targets
        self.credentials = credentials

    def run(self):
        try:
            self.results.emit(check_targets(self.targets, self.credentials))
        except Exception:
            self.error.emit("Monitoring failed unexpectedly. Retry the check.")
        finally:
            self.credentials.clear()


class _CredentialDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Service credentials")
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.kind.addItems(["Basic", "Bearer token"])
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Authentication", self.kind)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("Bearer token", self.token)
        self.kind.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _mode_changed(self):
        basic = self.kind.currentIndex() == 0
        self.username.setEnabled(basic)
        self.password.setEnabled(basic)
        self.token.setEnabled(not basic)

    def _accept(self):
        if self.kind.currentIndex() == 0:
            valid = bool(self.username.text() and self.password.text()) and ":" not in self.username.text()
        else:
            valid = bool(self.token.text()) and not any(c.isspace() for c in self.token.text())
        if not valid:
            QMessageBox.warning(self, "Credentials", "Enter a username (without ':') and password, or a token without whitespace.")
            return
        self.accept()

    def value(self):
        if self.kind.currentIndex() == 0:
            return {"kind": "basic", "username": self.username.text(), "password": self.password.text()}
        return {"kind": "bearer", "token": self.token.text()}

    def set_value(self, value):
        self.kind.setCurrentIndex(1 if value.get("kind") == "bearer" else 0)
        self.username.setText(value.get("username", ""))
        self.password.setText(value.get("password", ""))
        self.token.setText(value.get("token", ""))

    def clear(self):
        self.username.clear()
        self.password.clear()
        self.token.clear()


class _VaultDialog(QDialog):
    def __init__(self, vault: CredentialVault, parent=None):
        super().__init__(parent)
        self._vault = vault
        self.setWindowTitle("Credential vault")
        self.resize(820, 420)
        layout = QVBoxLayout(self)
        self._entries = QTreeWidget()
        self._entries.setHeaderLabels(["Scope / stack", "Service / secret", "Endpoint"])
        self._entries.currentItemChanged.connect(self._selection_changed)
        layout.addWidget(self._entries)
        self._username = QLineEdit()
        self._username.setReadOnly(True)
        self._secret = QLineEdit()
        self._secret.setReadOnly(True)
        self._secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._reveal = QCheckBox("Show password / token")
        self._reveal.toggled.connect(
            lambda visible: self._secret.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        )
        form = QFormLayout()
        form.addRow("Username / authentication", self._username)
        form.addRow("Password / token", self._secret)
        form.addRow(self._reveal)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self._edit = QPushButton("Edit credentials")
        self._edit.clicked.connect(self._edit_entry)
        self._delete = QPushButton("Delete entry")
        self._delete.clicked.connect(self._delete_entry)
        actions.addWidget(self._edit)
        actions.addWidget(self._delete)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self):
        self._entries.clear()
        for key in self._vault.keys():
            try:
                parts = json.loads(key)
            except ValueError:
                parts = None
            if (isinstance(parts, list) and len(parts) == 4 and parts[0] == "monitoring"
                    and all(isinstance(part, str) for part in parts)):
                labels = parts[1:]
            elif (isinstance(parts, list) and len(parts) == 2 and parts[0] == "secret"
                  and isinstance(parts[1], str)):
                labels = ["Global secret", parts[1], "Age encrypted output"]
            else:
                labels = [key, "", ""]
            row = QTreeWidgetItem(labels)
            row.setData(0, Qt.ItemDataRole.UserRole, key)
            self._entries.addTopLevelItem(row)
        self._selection_changed()

    def _key(self):
        row = self._entries.currentItem()
        return row.data(0, Qt.ItemDataRole.UserRole) if row else None

    def _selection_changed(self, *_):
        self.clear()
        key = self._key()
        self._edit.setEnabled(key is not None)
        self._delete.setEnabled(key is not None)
        if key is not None:
            value = self._vault.get(key)
            if value is not None:
                self._username.setText(value.get("username", value.get("kind", "")))
                self._secret.setText(value.get("password", value.get("token", "")))
                if value.get("kind") == "secret":
                    try:
                        name = json.loads(key)[1]
                        contents = read_secret(self._vault, name)
                        self._secret.setText(contents.decode("utf-8"))
                    except (ValueError, UnicodeError, VaultError):
                        self._secret.setText("(Binary or invalid secret; use the Secrets tab to import a replacement.)")

    def _edit_entry(self):
        key = self._key()
        if key is None:
            return
        value = self._vault.get(key)
        if value is not None and value.get("kind") == "secret":
            parts = json.loads(key)
            edit_secret(self._vault, parts[1], self)
            self._selection_changed()
            return
        dialog = _CredentialDialog(self)
        try:
            value = self._vault.get(key)
            if value is None:
                raise VaultError("The credential entry no longer exists.")
            if value.get("kind") not in ("basic", "bearer"):
                raise VaultError("This entry is not a supported monitoring credential.")
            dialog.set_value(value)
            if dialog.exec():
                self._vault.set(key, dialog.value())
                self._selection_changed()
        except VaultError as exc:
            QMessageBox.warning(self, "Credential vault", str(exc))
        finally:
            dialog.clear()
            dialog.deleteLater()

    def _delete_entry(self):
        key = self._key()
        if key is None:
            return
        if QMessageBox.question(self, "Delete credential", "Permanently delete the selected credential?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._vault.delete(key)
            self._reload()
        except VaultError as exc:
            QMessageBox.warning(self, "Credential vault", str(exc))

    def clear(self):
        self._username.clear()
        self._secret.clear()
        self._reveal.setChecked(False)


class MonitoringWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vault = CredentialVault()
        self._store = MonitoringStore()
        self._worker = None
        self._editing = False
        self._targets: list[MonitorTarget] = []
        self._stacks: list[str] = []
        self._results: dict[str, MonitorResult] = {}
        self._preferences_error = ""
        try:
            self._preferences = self._store.load()
        except ValueError as exc:
            self._preferences_error = str(exc)
            self._preferences = {"endpoints": {}, "domain": "", "interval": 60}
        layout = QVBoxLayout(self)
        note = QLabel(
            "HTTP(S) checks from this machine for all saved stacks. "
            "This does not verify Swarm replicas or container health. "
            "2xx is healthy; redirects and unconfigured services are not."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QHBoxLayout()
        self._refresh = QPushButton("Refresh stacks")
        self._refresh.clicked.connect(self.refresh_stacks)
        self._check = QPushButton("Check now")
        self._check.clicked.connect(self.check_now)
        self._toggle = QPushButton("Start monitoring")
        self._toggle.clicked.connect(self._toggle_monitoring)
        self._interval = QSpinBox()
        self._interval.setRange(10, 3600)
        self._interval.setSuffix(" s")
        self._interval.setValue(self._preferences["interval"])
        self._domain = QLineEdit(self._preferences["domain"])
        self._domain.setPlaceholderText("Domain to replace ${DOMAIN}")
        for widget in (self._refresh, self._check, self._toggle, QLabel("Interval"), self._interval, self._domain):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Stack / service", "State", "Endpoint", "Detail", "Last checked"])
        self._tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.itemDoubleClicked.connect(lambda *_: self._edit_endpoint())
        layout.addWidget(self._tree)
        actions = QHBoxLayout()
        self._endpoint = QPushButton("Edit endpoint")
        self._endpoint.clicked.connect(self._edit_endpoint)
        self._credentials = QPushButton("Set credentials / retry")
        self._credentials.clicked.connect(self._set_credentials)
        self._remove = QPushButton("Remove credentials")
        self._remove.clicked.connect(self._remove_credentials)
        self._unlock = QPushButton("Unlock / create vault")
        self._unlock.clicked.connect(self._unlock_vault)
        self._lock = QPushButton("Lock vault")
        self._lock.clicked.connect(self._lock_vault)
        self._view_vault = QPushButton("View vault")
        self._view_vault.clicked.connect(self._show_vault)
        for widget in (self._endpoint, self._credentials, self._remove, self._unlock, self._view_vault, self._lock):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self._tree.itemSelectionChanged.connect(self._selection_changed)
        self._state = QLabel(self._preferences_error or "Monitoring paused. Vault locked.")
        self._state.setWordWrap(True)
        layout.addWidget(self._state)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_now)
        self._interval.valueChanged.connect(self._preferences_changed)
        self._domain.editingFinished.connect(self._preferences_changed)
        self.refresh_stacks()

    def _save_preferences(self, preferences) -> bool:
        if self._preferences_error:
            QMessageBox.warning(self, "Monitoring", self._preferences_error)
            return False
        try:
            self._store.save(preferences)
        except OSError:
            QMessageBox.warning(self, "Monitoring", "Cannot save monitoring preferences. Check directory permissions.")
            return False
        self._preferences = preferences
        return True

    def _preferences_changed(self):
        preferences = {**self._preferences, "domain": self._domain.text().strip(),
                       "interval": self._interval.value()}
        if self._save_preferences(preferences):
            if self._timer.isActive():
                self._timer.setInterval(self._interval.value() * 1000)
            self.refresh_stacks()
        else:
            self._interval.blockSignals(True)
            self._interval.setValue(self._preferences["interval"])
            self._interval.blockSignals(False)
            self._domain.setText(self._preferences["domain"])

    def refresh_stacks(self):
        if self._worker is not None:
            return
        entries = stack_library.list_stacks()
        self._stacks = sorted({f"{entry.config.environment}/{entry.config.name}" for entry in entries})
        targets = discover_targets(entries, self._preferences["endpoints"],
                                   self._preferences["domain"])
        unchanged = {target.key for target in targets if target in self._targets}
        self._results = {key: value for key, value in self._results.items() if key in unchanged}
        self._targets = targets
        self._render()
        if self._preferences_error:
            self._state.setText(self._preferences_error)

    def _render(self):
        selected = self._selected()
        self._tree.clear()
        parents = {}
        for stack in self._stacks:
            parent = QTreeWidgetItem([stack])
            self._tree.addTopLevelItem(parent)
            parents[stack] = parent
        for target in self._targets:
            result = self._results.get(target.key)
            row = QTreeWidgetItem([
                target.service, result.state if result else "Not checked", target.url,
                result.detail if result else "", result.checked_at if result else "",
            ])
            row.setData(0, Qt.ItemDataRole.UserRole, target)
            parents[target.stack].addChild(row)
            if selected and selected.key == target.key:
                self._tree.setCurrentItem(row)
            self._color(row, result.state if result else "Not checked")
        for stack, parent in parents.items():
            targets = [target for target in self._targets if target.stack == stack]
            results = [self._results[target.key] for target in targets if target.key in self._results]
            state = stack_state(results) if len(results) == len(targets) else "Not checked"
            parent.setText(1, state)
            self._color(parent, state)
        self._tree.expandAll()
        self._selection_changed()

    @staticmethod
    def _color(row, state):
        color = "#218739" if state == "Healthy" else (
            "#c62828" if state in ("Issues", "HTTP error", "Unreachable", "TLS error", "Invalid endpoint") else "#a66b00"
        )
        row.setForeground(1, QColor(color))

    def _selected(self) -> MonitorTarget | None:
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self):
        enabled = self._selected() is not None and self._worker is None
        for widget in (self._endpoint, self._credentials, self._remove):
            widget.setEnabled(enabled)

    def _edit_endpoint(self):
        target = self._selected()
        if target is None or self._worker is not None:
            return
        self._editing = True
        try:
            url, accepted = QInputDialog.getText(self, "Service endpoint", "HTTP(S) URL (blank disables checks):",
                                               text=target.url)
        finally:
            self._editing = False
        if not accepted:
            return
        try:
            url = validate_endpoint(url) if url.strip() else ""
        except ValueError as exc:
            QMessageBox.warning(self, "Endpoint", str(exc))
            return
        endpoints = {**self._preferences["endpoints"], target.key: url}
        if self._save_preferences({**self._preferences, "endpoints": endpoints}):
            self.refresh_stacks()

    def _unlock_vault(self) -> bool:
        self._editing = True
        try:
            return self._prompt_unlock()
        finally:
            self._editing = False

    def _prompt_unlock(self) -> bool:
        if unlock_vault(self._vault, self):
            self._state.setText("Vault unlocked for this session. Monitoring credentials are sent only to their exact HTTPS endpoint.")
            return True
        return False

    def _lock_vault(self):
        if self._worker is not None:
            QMessageBox.warning(self, "Monitoring", "Wait for the current checks to finish before locking.")
            return
        self._vault.lock()
        self._results.clear()
        self._render()
        self._state.setText("Vault locked. Future checks run without stored credentials.")

    def _show_vault(self):
        if self._worker is not None or not self._unlock_vault():
            return
        self._editing = True
        dialog = None
        try:
            dialog = _VaultDialog(self._vault, self)
            dialog.exec()
        except VaultError as exc:
            QMessageBox.warning(self, "Credential vault", str(exc))
        finally:
            self._editing = False
            if dialog is not None:
                dialog.clear()
                dialog.deleteLater()
            self._results.clear()
            self._render()

    def _set_credentials(self):
        target = self._selected()
        if target is None or self._worker is not None:
            return
        try:
            if not validate_endpoint(target.url).startswith("https://"):
                raise ValueError("Credentials require HTTPS. Configure the service endpoint first.")
        except ValueError as exc:
            QMessageBox.warning(self, "Credentials", str(exc))
            return
        if not self._unlock_vault():
            return
        dialog = _CredentialDialog(self)
        self._editing = True
        try:
            if not dialog.exec():
                return
            self._vault.set(target.credential_key, dialog.value())
        except VaultError as exc:
            QMessageBox.warning(self, "Credentials", str(exc))
            return
        finally:
            self._editing = False
            dialog.clear()
            dialog.deleteLater()
        self.check_now()

    def _remove_credentials(self):
        target = self._selected()
        if target is None or self._worker is not None or not self._unlock_vault():
            return
        try:
            self._vault.delete(target.credential_key)
        except VaultError as exc:
            QMessageBox.warning(self, "Credentials", str(exc))
            return
        self._results.pop(target.key, None)
        self._render()
        self._state.setText("Credentials removed for the selected endpoint.")

    def _toggle_monitoring(self):
        if self._timer.isActive():
            self._timer.stop()
            self._toggle.setText("Start monitoring")
            self._state.setText("Monitoring paused. Last results remain visible.")
        else:
            if self._worker is not None:
                return
            self.refresh_stacks()
            self._timer.start(self._interval.value() * 1000)
            self._toggle.setText("Pause monitoring")
            self.check_now()

    def check_now(self):
        if self._worker is not None or self._editing:
            return
        self.refresh_stacks()
        if not self._targets:
            self._state.setText("No saved services. Save stacks to Stack Library first.")
            return
        credentials = {}
        if self._vault.unlocked:
            try:
                for target in self._targets:
                    credential = self._vault.get(target.credential_key)
                    if credential:
                        credentials[target.credential_key] = credential
            except VaultError as exc:
                QMessageBox.warning(self, "Credential vault", str(exc))
                return
        self._set_busy(True)
        self._state.setText("Checking service endpoints...")
        self._worker = _MonitorWorker(list(self._targets), credentials, self)
        self._worker.results.connect(self._checked)
        self._worker.error.connect(self._failed)
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def _set_busy(self, busy):
        for widget in (self._check, self._refresh, self._endpoint, self._credentials,
                       self._remove, self._lock, self._unlock, self._view_vault, self._domain, self._interval):
            widget.setEnabled(not busy)
        if not busy:
            self._selection_changed()

    def _checked(self, results):
        self._results = {result.target.key: result for result in results}
        self._render()
        issues = sum(result.state != "Healthy" for result in results)
        self._state.setText(
            f"{len(results)} services checked; {issues} need attention. "
            + ("Vault unlocked." if self._vault.unlocked else "Vault locked; authenticated checks require unlocking.")
        )

    def _failed(self, message):
        self._results.clear()
        self._render()
        self._state.setText(message)

    def _finished(self):
        worker = self._worker
        self._worker = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    def prepare_close(self) -> bool:
        if self._worker is not None:
            QMessageBox.warning(self, "Monitoring", "Wait for active service checks to finish before closing.")
            return False
        return True

    def shutdown(self):
        self._timer.stop()
        self._vault.lock()
