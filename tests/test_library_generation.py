import importlib.util
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from docker_stack_generator.generator import StackConfig
from docker_stack_generator.stack_library import SavedStack
from test_environment import make_service


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class LibraryGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.main_window import MainWindow
        from PyQt6.QtWidgets import QMainWindow
        self.window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(self.window)
        self.window._generate_worker = None
        self.window._generate_btn = Mock()
        self.window._stack_library_widget = Mock()
        self.window._status = Mock()
        self.addCleanup(self.window.deleteLater)
        self.saved = SavedStack("production__demo", StackConfig(
            "demo", "Demo", "production", "saved-root", services=[make_service()]
        ))

    def test_library_split_button_routes_both_actions_and_obeys_busy_state(self):
        from docker_stack_generator.stack_library_widget import StackLibraryWidget
        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        self.assertFalse(widget._generate_btn.isEnabled())
        widget._show_entry(self.saved)
        requested = Mock()
        widget.generate_requested.connect(requested)
        widget._generate_btn.click()
        requested.assert_called_once_with(self.saved, False)
        requested.reset_mock()
        widget._generate_btn.sync_action.trigger()
        requested.assert_called_once_with(self.saved, True)
        details = widget._generate_btn.parentWidget().layout()
        actions = details.itemAt(details.count() - 1).layout()
        self.assertIs(actions.itemAt(0).widget(), widget._load_btn)
        self.assertIs(actions.itemAt(1).widget(), widget._generate_btn)
        widget.set_generation_busy(True)
        widget._show_entry(self.saved)
        self.assertFalse(widget._generate_btn.isEnabled())
        requested.reset_mock()
        widget._generate_selected(sync=True)
        requested.assert_not_called()
        widget.set_generation_busy(False)
        self.assertTrue(widget._generate_btn.isEnabled())
        widget._clear_details()
        self.assertFalse(widget._generate_btn.isEnabled())

    def test_library_generation_copies_saved_config_without_touching_form(self):
        with patch.object(self.window, "_start_generation") as start, \
                patch("docker_stack_generator.main_window.settings") as settings:
            settings.stacks_root = "settings-root"
            self.window._generate_from_library(self.saved, True)
        cfg = start.call_args.args[0]
        self.assertEqual(cfg.stacks_root, "saved-root")
        self.assertEqual(cfg.environment, "production")
        self.assertIsNot(cfg, self.saved.config)
        self.assertIsNot(cfg.services[0], self.saved.config.services[0])
        self.assertEqual(start.call_args.kwargs, {"overwrite": False, "sync": True})

    def test_library_generation_falls_back_to_settings_root(self):
        self.saved.config.stacks_root = ""
        with patch.object(self.window, "_start_generation") as start, \
                patch("docker_stack_generator.main_window.settings") as settings:
            settings.stacks_root = "settings-root"
            self.window._generate_from_library(self.saved, False)
        self.assertEqual(start.call_args.args[0].stacks_root, "settings-root")
        self.assertEqual(self.saved.config.stacks_root, "")

    def test_existing_output_requires_confirmation_and_starts_shared_worker(self):
        from PyQt6.QtWidgets import QMessageBox
        with TemporaryDirectory() as root:
            self.saved.config.stacks_root = root
            self.saved.config.stack_dir.mkdir(parents=True)
            with patch("docker_stack_generator.main_window.QMessageBox.question",
                       return_value=QMessageBox.StandardButton.No), \
                    patch("docker_stack_generator.main_window.GenerateWorker") as worker:
                self.window._generate_from_library(self.saved, True)
            worker.assert_not_called()
            with patch("docker_stack_generator.main_window.QMessageBox.question",
                       return_value=QMessageBox.StandardButton.Yes), \
                    patch("docker_stack_generator.main_window.GenerateWorker") as worker:
                self.window._generate_from_library(self.saved, True)
            self.assertEqual(worker.call_args.args[0].stack_dir, Path(root) / "production" / "demo")
            self.assertTrue(worker.call_args.args[1])
            self.assertTrue(worker.call_args.kwargs["sync"])
            worker.return_value.start.assert_called_once()
            self.window._generate_btn.setEnabled.assert_called_with(False)
            self.window._stack_library_widget.set_generation_busy.assert_called_with(True)

    def test_error_reenables_both_generation_surfaces(self):
        with patch("docker_stack_generator.main_window.QMessageBox.critical"):
            self.window._on_generate_error("generation failed")
        self.window._generate_btn.setEnabled.assert_called_with(True)
        self.window._stack_library_widget.set_generation_busy.assert_called_with(False)

    def test_running_worker_blocks_second_generation(self):
        self.window._generate_worker = Mock()
        self.window._generate_worker.isRunning.return_value = True
        with patch("docker_stack_generator.main_window.GenerateWorker") as worker:
            self.window._generate_from_library(self.saved, False)
        worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
