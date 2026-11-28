"""Dialog for the basic details of a new stack."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from docker_stack_generator.generator import StackConfig, stack_slugify


class NewStackDialog(QDialog):
    def __init__(
        self,
        environment: str = "development",
        parent=None,
        config: StackConfig | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Stack" if config else "New Stack")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g. arr-media, monitoring")
        if config:
            self._name_input.setText(config.display_name)
        form.addRow("Stack name:", self._name_input)

        self._environment_input = QLineEdit(config.environment if config else environment)
        self._environment_input.setPlaceholderText("development")
        form.addRow("Environment:", self._environment_input)

        self._description_input = QTextEdit()
        self._description_input.setPlaceholderText("What this stack is for")
        self._description_input.setFixedHeight(90)
        if config:
            self._description_input.setPlainText(config.description)
        form.addRow("Description:", self._description_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_valid(self):
        if not stack_slugify(self._name_input.text().strip()):
            QMessageBox.warning(self, "Validation", "Please enter a valid stack name.")
            return
        if not self._environment_input.text().strip():
            QMessageBox.warning(self, "Validation", "Please enter an environment.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        display_name = self._name_input.text().strip()
        return (
            display_name,
            self._environment_input.text().strip(),
            self._description_input.toPlainText().strip(),
        )

