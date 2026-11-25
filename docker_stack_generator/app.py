"""Application entry point."""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from docker_stack_generator.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Docker Stack Generator")
    app.setOrganizationName("shackofnoreturn.com")
    app.setStyle("Fusion")  # consistent cross-platform look

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
