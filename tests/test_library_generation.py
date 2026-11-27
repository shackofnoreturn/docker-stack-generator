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

    def test_double_clicking_service_row_requests_edit_for_selected_stack(self):
        from docker_stack_generator.stack_library_widget import StackLibraryWidget

        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        widget._show_entry(self.saved)
        requested = Mock()
        widget.edit_service_requested.connect(requested)

        widget._svc_table.cellDoubleClicked.emit(0, 0)

        requested.assert_called_once_with(self.saved, 0)

    def test_double_clicking_stack_title_requests_edit_for_selected_stack(self):
        from docker_stack_generator.stack_library_widget import StackLibraryWidget

        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        widget._show_entry(self.saved)
        requested = Mock()
        widget.edit_stack_requested.connect(requested)

        widget._detail_title.double_clicked.emit()

        requested.assert_called_once_with(self.saved)

    def test_accepted_stack_name_edit_rekeys_library_entry(self):
        from copy import deepcopy

        renamed = SavedStack(
            "production__changed-name",
            StackConfig("changed-name", "Changed Name", "production", "saved-root"),
        )
        self.window._stack_library_widget.refresh = Mock()
        with patch(
            "docker_stack_generator.main_window.QInputDialog.getText",
            return_value=("Changed Name", True),
        ), patch(
            "docker_stack_generator.main_window.stack_library.rename_stack",
            return_value=renamed,
        ) as rename:
            self.window._edit_library_stack(self.saved)

        cfg = rename.call_args.args[1]
        self.assertIsNot(cfg, self.saved.config)
        self.assertEqual(cfg.name, "changed-name")
        self.assertEqual(cfg.display_name, "Changed Name")
        self.assertEqual(self.saved.config.name, "demo")
        rename.assert_called_once_with(self.saved.key, cfg)
        self.window._stack_library_widget.refresh.assert_called_once_with(select_key=renamed.key)

    def test_accepted_library_service_edit_saves_copy_and_refreshes(self):
        from docker_stack_generator.generator import StackConfig

        updated_service = make_service(description="updated")
        self.window._monitoring_widget = Mock(_vault=object())
        self.window._stack_library_widget.refresh = Mock()
        with patch("docker_stack_generator.main_window.ServiceDialog") as dialog_class, \
                patch("docker_stack_generator.main_window.stack_library.save_stack", return_value=self.saved) as save:
            dialog_class.return_value.exec.return_value = 1
            dialog_class.return_value.result_service.return_value = updated_service

            self.window._edit_library_service(self.saved, 0)

        saved_config = save.call_args.args[0]
        self.assertIsInstance(saved_config, StackConfig)
        self.assertIsNot(saved_config, self.saved.config)
        self.assertIs(saved_config.services[0], updated_service)
        self.assertEqual(self.saved.config.services[0].description, "")
        save.assert_called_once_with(saved_config, generated=False)
        self.window._stack_library_widget.refresh.assert_called_once_with()
        dialog_args, dialog_kwargs = dialog_class.call_args
        self.assertIs(dialog_args[0], self.window)
        self.assertEqual(dialog_kwargs["existing"].name, self.saved.config.services[0].name)
        self.assertIsNot(dialog_kwargs["existing"], self.saved.config.services[0])
        self.assertIs(dialog_kwargs["vault"], self.window._monitoring_widget._vault)

    def test_cancelled_library_service_edit_does_not_save(self):
        self.window._monitoring_widget = Mock(_vault=object())
        with patch("docker_stack_generator.main_window.ServiceDialog") as dialog_class, \
                patch("docker_stack_generator.main_window.stack_library.save_stack") as save:
            dialog_class.return_value.exec.return_value = 0

            self.window._edit_library_service(self.saved, 0)

        save.assert_not_called()
        self.window._stack_library_widget.refresh.assert_not_called()
        self.assertEqual(self.saved.config.services[0].description, "")

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

