"""Semaphore deployment controls, task monitoring, and service endpoint checks."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from docker_stack_generator.config import settings
from docker_stack_generator.generator import homepage_label_defaults
from docker_stack_generator.semaphore_client import (
    DEFAULT_TARGETS, TERMINAL_STATES, SemaphoreClient, SemaphoreError,
    check_endpoints, parse_endpoints, validate_targets, validate_url,
)
from docker_stack_generator.stack_library import stack_library


class _RequestWorker(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation())
        except (SemaphoreError, ValueError, OSError) as exc:
            self.error.emit(str(exc))
        except Exception:
            self.error.emit("Unexpected response. Check the task in Semaphore before retrying.")


class _TargetsDialog(QDialog):
    def __init__(self, targets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Semaphore Targets")
        self.resize(880, 360)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Action", "Environment / Swarm", "Project ID", "Template ID", "Extra survey variables (JSON)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setToolTip("Use the template that targets the intended Swarm. Extra variables are saved in plain text; do not enter secrets.")
        layout.addWidget(self.table)
        for target in targets:
            self._append(target)
        buttons = QHBoxLayout()
        add = QPushButton("Add target")
        add.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        add.clicked.connect(lambda: self._append({"action": "Deploy", "environment": "", "project": 1, "template": 1, "variables": {}}))
        buttons.addWidget(add)
        remove = QPushButton("Remove selected")
        remove.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogDiscardButton))
        remove.clicked.connect(self._remove)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _append(self, target):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((target["action"], target["environment"], target["project"], target["template"], json.dumps(target.get("variables", {})))):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))

    def _remove(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def values(self):
        targets = []
        for row in range(self.table.rowCount()):
            fields = [self.table.item(row, column).text().strip() for column in range(5)]
            targets.append({"action": fields[0], "environment": fields[1], "project": int(fields[2]),
                            "template": int(fields[3]), "variables": json.loads(fields[4] or "{}")})
        return validate_targets(targets)

    def _accept(self):
        try:
            self.values()
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Targets", str(exc))
            return
        self.accept()


class DeploymentWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._client = None
        self._worker = None
        self._pending = None
        self._active = None
        self._failures = 0
        self._monitor_started = 0.0
        self._health_started = 0.0
        self._targets = settings.get("semaphore_targets", json.loads(json.dumps(DEFAULT_TARGETS)))
        self._history = settings.get("semaphore_history", [])
        self._saved_stacks = {}
        self._loaded_endpoint_key = None
        self._http_approved = set()
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._refresh_task)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(10000)
        self._health_timer.timeout.connect(self._check_health)
        self._build_ui()
        self._fill_targets()
        self._fill_history()

    def _button(self, label, icon, callback):
        button = QPushButton(label)
        button.setIcon(self.style().standardIcon(icon))
        button.clicked.connect(callback)
        return button

    def _build_ui(self):
        layout = QVBoxLayout(self)
        connection = QFormLayout()
        self._url = QLineEdit(settings.get("semaphore_url", ""))
        self._url.setPlaceholderText("http://10.0.0.23:3000")
        connection.addRow("Semaphore URL:", self._url)
        auth = QHBoxLayout()
        self._username = QLineEdit(settings.get("semaphore_username", ""))
        self._username.setPlaceholderText("Username")
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Password (not saved)")
        self._token = QLineEdit()
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        self._token.setPlaceholderText("API token, optional (not saved)")
        for editor in (self._username, self._password, self._token):
            auth.addWidget(editor)
        self._connect_button = self._button("Connect", QStyle.StandardPixmap.SP_DialogApplyButton, self._connect)
        auth.addWidget(self._connect_button)
        connection.addRow("Authentication:", auth)
        layout.addLayout(connection)

        selection = QHBoxLayout()
        selection.addWidget(QLabel("Target:"))
        self._target = QComboBox()
        selection.addWidget(self._target, 2)
        self._configure_button = self._button("Targets", QStyle.StandardPixmap.SP_FileDialogDetailedView, self._configure)
        selection.addWidget(self._configure_button)
        selection.addWidget(QLabel("Stack:"))
        self._stack = QComboBox()
        self._stack.setEditable(True)
        self._stack.setMinimumWidth(140)
        selection.addWidget(self._stack, 1)
        self._reload_button = self._button("Refresh stacks", QStyle.StandardPixmap.SP_BrowserReload, self.refresh_stacks)
        selection.addWidget(self._reload_button)
        self._run_button = self._button("Run playbook", QStyle.StandardPixmap.SP_MediaPlay, self._start)
        selection.addWidget(self._run_button)
        layout.addLayout(selection)
        self._target.currentIndexChanged.connect(self.refresh_stacks)
        self._stack.activated.connect(self._stack_changed)
        self._stack.lineEdit().editingFinished.connect(self._stack_changed)

        controls = QHBoxLayout()
        self._state = QLabel("Disconnected")
        self._state.setWordWrap(True)
        self._state.setTextFormat(Qt.TextFormat.PlainText)
        controls.addWidget(self._state, 1)
        self._refresh_button = self._button("Refresh task", QStyle.StandardPixmap.SP_BrowserReload, self._resume_active)
        controls.addWidget(self._refresh_button)
        self._pause_button = self._button("Pause monitoring", QStyle.StandardPixmap.SP_MediaPause, self._pause)
        controls.addWidget(self._pause_button)
        self._open_button = self._button("Open in Semaphore", QStyle.StandardPixmap.SP_DirLinkIcon, self._open_task)
        controls.addWidget(self._open_button)
        layout.addLayout(controls)
        history = QHBoxLayout()
        self._history_combo = QComboBox()
        history.addWidget(self._history_combo, 1)
        self._resume_button = self._button("Resume selected run", QStyle.StandardPixmap.SP_MediaPlay, self._resume_history)
        history.addWidget(self._resume_button)
        self._attach_button = self._button("Attach task ID", QStyle.StandardPixmap.SP_FileDialogContentsView, self._attach)
        history.addWidget(self._attach_button)
        layout.addLayout(history)

        splitter = QSplitter(Qt.Orientation.Vertical)
        endpoints = QWidget()
        endpoint_layout = QVBoxLayout(endpoints)
        endpoint_layout.setContentsMargins(0, 0, 0, 0)
        endpoint_controls = QHBoxLayout()
        self._domain = QLineEdit(settings.get("semaphore_domain", ""))
        self._domain.setPlaceholderText("example.net")
        endpoint_controls.addWidget(QLabel("DOMAIN:"))
        endpoint_controls.addWidget(self._domain)
        self._auto_health = QCheckBox("Check after successful deploy")
        self._auto_health.setChecked(True)
        endpoint_controls.addWidget(self._auto_health)
        self._check_button = self._button("Check services", QStyle.StandardPixmap.SP_DialogApplyButton, self._manual_health)
        endpoint_controls.addWidget(self._check_button)
        endpoint_layout.addLayout(endpoint_controls)
        self._endpoints = QPlainTextEdit()
        self._endpoints.setPlaceholderText("traefik=https://traefik.${DOMAIN}\nhttps://service.example.net/health")
        self._endpoints.setMaximumHeight(80)
        endpoint_layout.addWidget(self._endpoints)
        self._health_state = QLabel("Service endpoints not checked")
        self._health_state.setWordWrap(True)
        self._health_state.setTextFormat(Qt.TextFormat.PlainText)
        endpoint_layout.addWidget(self._health_state)
        self._health_table = QTableWidget(0, 4)
        self._health_table.setHorizontalHeaderLabels(["Service", "URL", "State", "Detail"])
        self._health_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._health_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        endpoint_layout.addWidget(self._health_table)
        splitter.addWidget(endpoints)
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("Semaphore execution output")
        splitter.addWidget(self._output)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self._update_controls()

    def _update_controls(self):
        busy = self._worker is not None
        watching = self._poll.isActive() or self._health_timer.isActive()
        for widget in (self._connect_button, self._configure_button, self._run_button,
                       self._target, self._stack, self._reload_button, self._resume_button,
                       self._attach_button, self._check_button, self._url, self._username,
                       self._password, self._token, self._domain, self._endpoints, self._auto_health):
            widget.setEnabled(not busy and not watching)
        self._pause_button.setEnabled(watching)
        self._refresh_button.setEnabled(not busy and self._active is not None)
        self._open_button.setEnabled(self._active is not None)

    def _work(self, operation, success, failure=None):
        if self._worker is not None:
            return
        worker = _RequestWorker(operation, self)
        self._worker = worker
        worker.result.connect(success)
        worker.error.connect(failure or self._request_error)
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        self._update_controls()
        worker.start()

    def _finished(self):
        self._worker = None
        pending, self._pending = self._pending, None
        self._update_controls()
        if pending:
            pending()

    def _request_error(self, message):
        self._state.setText(message)

    def _new_client(self):
        url = validate_url(self._url.text().strip())
        if not self._token.text().strip() and not (self._username.text().strip() and self._password.text()):
            raise ValueError("Enter an API token or a username and password.")
        if url.startswith("http://") and url not in self._http_approved:
            answer = QMessageBox.warning(
                self, "Unencrypted Connection", "This server uses HTTP. Credentials and task output will travel unencrypted. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                raise ValueError("Use HTTPS or approve the HTTP connection to continue.")
            self._http_approved.add(url)
        if self._client:
            self._client.close()
        self._client = SemaphoreClient(url, self._username.text().strip(), self._password.text(), self._token.text().strip())
        settings.update({"semaphore_url": url, "semaphore_username": self._username.text().strip()})
        return self._client

    def _connect(self):
        try:
            client = self._new_client()
        except ValueError as exc:
            QMessageBox.warning(self, "Semaphore", str(exc))
            return
        self._state.setText("Connecting...")
        self._work(client.authenticate, lambda result: self._state.setText("Authenticated; select a target and stack"))

    def _configure(self):
        dialog = _TargetsDialog(self._targets, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._targets = dialog.values()
            settings.set("semaphore_targets", self._targets)
            self._fill_targets()

    def _fill_targets(self):
        self._target.clear()
        for target in self._targets:
            self._target.addItem(
                f"{target['action']} / {target['environment']} (project {target['project']}, template {target['template']})", target
            )
        self.refresh_stacks()

    def refresh_stacks(self):
        if self._worker is not None or self._poll.isActive() or self._health_timer.isActive():
            return
        target = self._target.currentData()
        if not target:
            return
        environment = target["environment"]
        previous = self._stack.currentText()
        self._saved_stacks = {
            entry.config.name: entry.config for entry in stack_library.list_stacks()
            if entry.config.environment == environment
        }
        names = set(self._saved_stacks)
        root = settings.stacks_root
        if root:
            directory = Path(root) / environment
            if directory.is_dir():
                try:
                    names.update(path.name for path in directory.iterdir() if path.is_dir() and
                                 any((path / filename).is_file() for filename in ("stack.yml", "compose.yml", "compose.yml.j2")))
                except OSError:
                    pass
        self._stack.blockSignals(True)
        self._stack.clear()
        self._stack.addItems(sorted(names))
        if previous in names:
            self._stack.setCurrentText(previous)
        self._stack.blockSignals(False)
        self._stack_changed()

    def _endpoint_key(self):
        target = self._target.currentData() or {}
        return f"{target.get('environment', '')}/{self._stack.currentText().strip()}"

    def _stack_changed(self):
        key = self._endpoint_key()
        if self._loaded_endpoint_key == key:
            return
        if self._loaded_endpoint_key:
            saved = dict(settings.get("semaphore_endpoints", {}))
            saved[self._loaded_endpoint_key] = self._endpoints.toPlainText()
            settings.set("semaphore_endpoints", saved)
        self._loaded_endpoint_key = key
        self._health_table.setRowCount(0)
        self._health_state.setText("Service endpoints not checked")
        saved = settings.get("semaphore_endpoints", {})
        if key in saved:
            self._endpoints.setPlainText(saved[key])
            return
        config = self._saved_stacks.get(self._stack.currentText())
        endpoints = []
        if config:
            for service in config.services:
                if service.homepage is not None:
                    values = {**homepage_label_defaults(service.name, service.display_name, service.description), **service.homepage}
                    endpoints.append(f"{service.name}={values['href']}")
                elif service.traefik is not None:
                    rule = service.traefik.get("rule")
                    if not rule:
                        endpoints.append(f"{service.name}=https://{service.name}.${{DOMAIN}}")
        self._endpoints.setPlainText("\n".join(endpoints))

    def _save_endpoints(self):
        endpoints = dict(settings.get("semaphore_endpoints", {}))
        endpoints[self._endpoint_key()] = self._endpoints.toPlainText()
        settings.update({"semaphore_endpoints": endpoints, "semaphore_domain": self._domain.text().strip()})

    def _start(self):
        self._stack_changed()
        target = self._target.currentData()
        stack = self._stack.currentText().strip()
        if not target or not stack:
            QMessageBox.warning(self, "Semaphore", "Select a target and stack first.")
            return
        previous = self._active
        warning = ""
        if previous and previous.get("status") not in TERMINAL_STATES:
            warning = "\nA previous task may still be running. This starts another playbook."
        answer = QMessageBox.question(self, "Confirm Playbook Run",
            f"{target['action']} stack '{stack}' in {target['environment']}?\n"
            f"Server: {self._url.text().strip()}\nProject {target['project']}, template {target['template']}\n"
            f"The playbook uses its configured repository and Swarm; local files are not uploaded.{warning}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            validate_targets([target])
            client = self._new_client()
        except ValueError as exc:
            QMessageBox.warning(self, "Semaphore", str(exc))
            return
        self._save_endpoints()
        self._output.clear()
        self._health_table.setRowCount(0)
        self._health_state.setText("Service endpoints not checked for this task")
        self._state.setText("Starting playbook...")
        record = {"server": client.url, "project": target["project"], "template": target["template"],
                  "stack": stack, "environment": target["environment"], "action": target["action"]}
        self._work(lambda: client.start(target["project"], target["template"], stack, target.get("variables")),
                   lambda task: self._started(record, task),
                   lambda message: self._state.setText(f"{message} Check Semaphore history before retrying; the task may have started."))

    def _started(self, record, task):
        record.update({"id": task["id"], "status": task.get("status", "waiting")})
        self._active = record
        self._remember()
        self._begin_monitoring()
        self._pending = self._refresh_task

    def _remember(self):
        if not self._active:
            return
        active = dict(self._active)
        self._history = [entry for entry in self._history if
                         (entry["server"], entry["project"], entry["id"]) != (active["server"], active["project"], active["id"])]
        self._history.insert(0, active)
        self._history = self._history[:30]
        settings.set("semaphore_history", self._history)
        self._fill_history()

    def _fill_history(self):
        self._history_combo.clear()
        for entry in self._history:
            self._history_combo.addItem(
                f"#{entry['id']} | {entry['action']} | {entry['environment']}/{entry['stack']} | {entry['status']} | {entry['server']}", entry
            )

    def _begin_monitoring(self):
        self._failures = 0
        self._monitor_started = time.monotonic()
        self._poll.start()
        self._state.setText(f"Task #{self._active['id']}: monitoring")
        self._update_controls()

    def _refresh_task(self):
        if self._worker is not None or not self._active:
            return
        if self._poll.isActive() and time.monotonic() - self._monitor_started > 1800:
            self._pause()
            self._state.setText("Monitoring paused after 30 minutes. The remote task was not stopped; refresh to resume.")
            return
        active = self._active
        self._work(lambda: self._client.snapshot(active["project"], active["id"]), self._snapshot, self._poll_error)

    def _snapshot(self, snapshot):
        monitoring = self._poll.isActive()
        task = snapshot["task"]
        status = str(task.get("status", "unknown"))
        self._active["status"] = status
        self._failures = 0
        message = str(task.get("message") or "")
        self._state.setText(f"Task #{self._active['id']} | {self._active['environment']}/{self._active['stack']} | {status} | {message}")
        if snapshot["output"]:
            scrollbar = self._output.verticalScrollBar()
            at_end = scrollbar.value() >= scrollbar.maximum() - 3
            position = scrollbar.value()
            self._output.setPlainText(snapshot["output"])
            scrollbar.setValue(scrollbar.maximum() if at_end else position)
        if snapshot["warning"]:
            self._state.setText(self._state.text() + " | Logs unavailable: " + snapshot["warning"])
        if status in TERMINAL_STATES:
            self._poll.stop()
            self._remember()
            if monitoring and status == "success" and self._active["action"].casefold() == "deploy" and self._auto_health.isChecked():
                self._pending = self._start_health
        elif not monitoring:
            self._state.setText(self._state.text() + " | Monitoring paused locally")

    def _poll_error(self, message):
        self._failures += 1
        self._state.setText(f"Task monitoring error ({self._failures}/5): {message}")
        if self._failures >= 5:
            self._poll.stop()
            self._state.setText(self._state.text() + " Monitoring paused; refresh to reconnect. The remote task was not stopped.")

    def _resume_active(self):
        if self._active:
            self._resume_record(self._active)

    def _resume_history(self):
        record = self._history_combo.currentData()
        if record:
            self._resume_record(dict(record))

    def _resume_record(self, record):
        if self._worker is not None:
            return
        try:
            if validate_url(self._url.text().strip()) != record["server"]:
                raise ValueError("Set the Semaphore URL to the selected run's server before resuming.")
            self._new_client()
        except ValueError as exc:
            QMessageBox.warning(self, "Resume Run", str(exc))
            return
        self._poll.stop()
        self._health_timer.stop()
        self._active = dict(record)
        for index in range(self._target.count()):
            target = self._target.itemData(index)
            if target["environment"] == record["environment"] and target["action"] == record["action"]:
                self._target.setCurrentIndex(index)
                break
        self._stack.setCurrentText(record["stack"])
        self._stack_changed()
        self._output.clear()
        self._health_table.setRowCount(0)
        self._health_state.setText("Service endpoints not checked for this task")
        self._remember()
        self._begin_monitoring()
        self._refresh_task()

    def _attach(self):
        target = self._target.currentData()
        if not target:
            return
        task_id, accepted = QInputDialog.getInt(self, "Attach Semaphore Task", "Task ID:", 1, 1, 2147483647)
        if accepted:
            try:
                server = validate_url(self._url.text().strip())
            except ValueError as exc:
                QMessageBox.warning(self, "Attach Task", str(exc))
                return
            self._resume_record({"server": server, "project": target["project"], "template": target["template"],
                                 "environment": target["environment"], "stack": self._stack.currentText().strip(),
                                 "action": target["action"], "id": task_id, "status": "unknown"})

    def _pause(self):
        self._poll.stop()
        self._health_timer.stop()
        self._state.setText("Monitoring paused locally. Remote playbooks are not stopped.")
        self._update_controls()

    def _open_task(self):
        if self._active:
            record = self._active
            url = f"{record['server']}/project/{record['project']}/history?t={record['id']}"
            QDesktopServices.openUrl(QUrl(url))

    def _manual_health(self):
        self._save_endpoints()
        self._start_health()

    def _start_health(self):
        try:
            endpoints = parse_endpoints(self._endpoints.toPlainText(), self._domain.text())
        except ValueError as exc:
            self._health_state.setText(str(exc))
            return
        if not endpoints:
            self._health_state.setText("No endpoint URLs configured; service reachability is unknown.")
            return
        self._health_started = time.monotonic()
        self._health_timer.start()
        self._check_health()

    def _check_health(self):
        if self._worker is not None:
            return
        try:
            endpoints = parse_endpoints(self._endpoints.toPlainText(), self._domain.text())
        except ValueError as exc:
            self._health_timer.stop()
            self._health_state.setText(str(exc))
            self._update_controls()
            return
        self._health_state.setText("Checking service endpoints...")
        self._work(lambda: check_endpoints(endpoints), self._health_result, self._health_error)

    def _health_error(self, message):
        self._health_timer.stop()
        self._health_state.setText(message)

    def _health_result(self, results):
        paused = not self._health_timer.isActive()
        self._health_table.setRowCount(len(results))
        for row, result in enumerate(results):
            for column, key in enumerate(("name", "url", "state", "detail")):
                self._health_table.setItem(row, column, QTableWidgetItem(result[key]))
        ready = bool(results) and all(result["state"] == "Reachable" for result in results)
        timed_out = time.monotonic() - self._health_started >= 180
        if ready or timed_out:
            self._health_timer.stop()
        if ready:
            self._health_state.setText("Configured endpoints are reachable from this machine. Swarm replica health is not verified.")
        elif timed_out:
            self._health_state.setText("Some endpoints are not ready after 3 minutes. Review HTTP/TLS errors and playbook logs; checks can be retried.")
        elif paused:
            self._health_state.setText("Service checks paused; last results shown above.")
        else:
            self._health_state.setText("Waiting for endpoints; retrying every 10 seconds for up to 3 minutes.")

    def prepare_close(self) -> bool:
        if self._worker is not None:
            QMessageBox.information(self, "Semaphore", "A network request is in progress. Wait for it to finish before closing.")
            return False
        if self._poll.isActive() or self._health_timer.isActive():
            answer = QMessageBox.question(self, "Stop Monitoring?", "Close local monitoring? Remote playbooks will continue running.")
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._poll.stop()
        self._health_timer.stop()
        self._save_endpoints()
        if self._client:
            self._client.close()
        self._password.clear()
        self._token.clear()
        return True

