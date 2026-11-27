"""Shared master-password prompt for the local encrypted vault."""
from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from docker_stack_generator.credential_vault import CredentialVault, VaultError


def unlock_vault(vault: CredentialVault, parent=None) -> bool:
    if vault.unlocked:
        return True
    creating = not vault.exists
    prompt = ("Create master password (at least 12 characters). It cannot be recovered:"
              if creating else "Master password:")
    password, accepted = QInputDialog.getText(parent, "Credential vault", prompt, QLineEdit.EchoMode.Password)
    if not accepted:
        return False
    try:
        if creating:
            confirmation, accepted = QInputDialog.getText(
                parent, "Credential vault", "Confirm master password:", QLineEdit.EchoMode.Password)
            if not accepted:
                return False
            if password != confirmation:
                QMessageBox.warning(parent, "Credential vault", "Passwords do not match.")
                return False
        vault.unlock(password)
    except VaultError as exc:
        QMessageBox.warning(parent, "Credential vault", str(exc))
        return False
    finally:
        password = ""
    return True
