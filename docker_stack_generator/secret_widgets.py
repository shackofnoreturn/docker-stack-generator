"""Vault-backed secret values, stored separately from exported definitions."""
from __future__ import annotations

import base64
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from docker_stack_generator.age_secrets import AgeError, decrypt_secret, secret_key, validate_secret_name
from docker_stack_generator.config import settings
from docker_stack_generator.credential_vault import CredentialVault, VaultError
from docker_stack_generator.vault_ui import unlock_vault
from docker_stack_generator.secret_store import read_secret


class SecretValueDialog(QDialog):
    def __init__(self, name: str, contents: bytes = b"", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Secret value: {name}")
        self.resize(600, 360)
        self._contents = contents
        self._text_edited = False
        layout = QVBoxLayout(self)
        note = QLabel(
            "Values are shared globally by secret name across stacks and environments. "
            "Saving updates the encrypted vault immediately, even if you later cancel the service dialog."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self._status = QLabel()
        layout.addWidget(self._status)
        self._show = QCheckBox("Show / edit secret text")
        layout.addWidget(self._show)
        self._text = QPlainTextEdit()
        self._text.setVisible(False)
        self._show.toggled.connect(self._text.setVisible)
        self._text.textChanged.connect(self._changed)
        layout.addWidget(self._text, 1)
        self._load(contents)
        button = QPushButton("Import plaintext or .age file")
        button.clicked.connect(self._import)
        layout.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self, contents):
        self._contents = contents
        self._text.blockSignals(True)
        try:
            text = contents.decode("utf-8")
            editable = "\x00" not in text
        except UnicodeError:
            text, editable = "", False
        self._text.setPlainText(text if editable else "")
        self._text.setReadOnly(not editable)
        self._text.blockSignals(False)
        self._text_edited = False
        self._status.setText(f"{len(contents)} bytes loaded. " + ("" if editable else "Binary value; import to replace."))

    def _changed(self):
        self._text_edited = True

    def contents(self) -> bytes:
        return self._text.toPlainText().encode("utf-8") if self._text_edited else self._contents

    def _import(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Import secret (plaintext or age encrypted)")
        if not filename:
            return
        try:
            contents = Path(filename).read_bytes()
            if filename.lower().endswith(".age"):
                contents = decrypt_secret(contents, settings.get("age_identity", "~/.config/age/key.pem"))
            if not contents:
                raise ValueError("Secret contents cannot be empty.")
            self._load(contents)
        except AgeError as exc:
            QMessageBox.warning(self, "Import secret", str(exc))
        except (OSError, ValueError):
            QMessageBox.warning(self, "Import secret",
                                "Cannot import secret. Check the file, age installation, and configured identity.")

    def _accept(self):
        if not self.contents():
            QMessageBox.warning(self, "Secret", "Secret contents cannot be empty.")
            return
        self.accept()

    def clear(self):
        self._contents = b""
        self._text.clear()
        self._show.setChecked(False)


def edit_secret(vault: CredentialVault, name: str, parent=None) -> bool:
    try:
        validate_secret_name(name)
    except (ValueError, AgeError) as exc:
        QMessageBox.warning(parent, "Secret", str(exc))
        return False
    if not unlock_vault(vault, parent):
        return False
    dialog = None
    try:
        existing = vault.get(secret_key(name))
        contents = read_secret(vault, name) if existing else b""
        dialog = SecretValueDialog(name, contents, parent)
        if not dialog.exec():
            return False
        vault.set(secret_key(name), {
            "kind": "secret", "contents": base64.b64encode(dialog.contents()).decode("ascii"),
        })
        return True
    except VaultError as exc:
        QMessageBox.warning(parent, "Secret", str(exc))
        return False
    finally:
        if dialog is not None:
            dialog.clear()
            dialog.deleteLater()
