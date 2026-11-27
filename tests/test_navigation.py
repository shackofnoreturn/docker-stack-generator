import importlib.util
import os
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_has_no_toolbar(self):
        from PyQt6.QtWidgets import QToolBar
        from docker_stack_generator.main_window import MainWindow
        with patch.object(MainWindow, "_build_ui"), \
                patch.object(MainWindow, "_restore_geometry"), \
                patch.object(MainWindow, "_check_settings"):
            window = MainWindow()
        try:
            self.assertEqual(window.findChildren(QToolBar), [])
            menus = {action.text(): action.menu() for action in window.menuBar().actions()}
            settings = [action for action in menus["&Edit"].actions() if "Settings" in action.text()]
            self.assertEqual(len(settings), 1)
            self.assertEqual(settings[0].shortcut().toString(), "Ctrl+,")
            self.assertTrue(settings[0].isEnabled())
            self.assertTrue(any("New Stack" in action.text() for action in menus["&File"].actions()))
        finally:
            window.deleteLater()

    def test_all_main_tabs_have_icons_without_a_settings_tab(self):
        from PyQt6.QtWidgets import QMainWindow, QWidget
        from docker_stack_generator.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)

        def widget():
            result = QWidget()
            result.load_requested = Mock()
            result.refresh_stacks = Mock()
            return result

        with patch.object(window, "_build_stack_tab", side_effect=QWidget), \
                patch("docker_stack_generator.main_window.StackLibraryWidget", side_effect=widget), \
                patch("docker_stack_generator.main_window.TemplateManagerWidget", side_effect=widget), \
                patch("docker_stack_generator.main_window.DeploymentWidget", side_effect=widget), \
                patch("docker_stack_generator.main_window.MonitoringWidget", side_effect=widget):
            window._build_ui()
        try:
            labels = [window._tabs.tabText(index) for index in range(window._tabs.count())]
            self.assertEqual(len(labels), 5)
            self.assertTrue(labels[3].endswith("Deployments"))
            self.assertTrue(labels[4].endswith("Monitoring"))
            self.assertTrue(all(any(ord(character) > 127 for character in label) for label in labels))
            self.assertFalse(any("Settings" in label for label in labels))
        finally:
            window.deleteLater()
