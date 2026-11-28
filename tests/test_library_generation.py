import importlib.util
import os
import unittest
from copy import deepcopy
from dataclasses import replace
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

    def test_new_stack_dialog_initializes_generator_from_stack_library_action(self):
        from PyQt6.QtWidgets import QMessageBox
        from docker_stack_generator.main_window import MainWindow

        self.window._stack_name_input = Mock()
        self.window._env_input = Mock()
        self.window._description_input = Mock()
        self.window._services = [make_service()]
        self.window._services_list = Mock()
        self.window._preview_rendered = Mock()
        self.window._preview_compose_j2 = Mock()
        self.window._preview_stack_yml = Mock()
        self.window._overwrite_check = Mock()
        self.window._tabs = Mock()
        self.window._preview_tab = object()
        self.window._loaded_stack_identity = ("Old", "old")
        self.window._refresh_services_list = Mock()
        with patch("docker_stack_generator.main_window.NewStackDialog") as dialog_class, \
                patch(
                    "docker_stack_generator.main_window.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
            dialog_class.return_value.exec.return_value = 1
            dialog_class.return_value.values.return_value = ("New Stack", "production", "Description")
            MainWindow._new_stack(self.window)

        dialog_class.assert_called_once()
        self.window._stack_name_input.setText.assert_called_once_with("New Stack")
        self.window._env_input.setText.assert_called_once_with("production")
        self.window._description_input.setPlainText.assert_called_once_with("Description")
        self.assertEqual(self.window._services, [])
        self.window._refresh_services_list.assert_called_once_with()
        self.window._overwrite_check.setChecked.assert_called_once_with(False)
        self.window._tabs.setCurrentWidget.assert_called_once_with(self.window._preview_tab)
        self.assertIsNone(self.window._loaded_stack_identity)

    def test_existing_stack_opens_prefilled_dialog_and_keeps_services(self):
        from docker_stack_generator.main_window import MainWindow

        self.window._stack_name_input = Mock()
        self.window._env_input = Mock()
        self.window._description_input = Mock()
        self.window._services = []
        self.window._services_list = Mock()
        self.window._preview_rendered = Mock()
        self.window._preview_compose_j2 = Mock()
        self.window._preview_stack_yml = Mock()
        self.window._overwrite_check = Mock()
        self.window._tabs = Mock()
        self.window._preview_tab = object()
        self.window._loaded_stack_identity = None
        self.window._refresh_services_list = Mock()
        with patch("docker_stack_generator.main_window.NewStackDialog") as dialog_class, \
                patch("docker_stack_generator.main_window.settings") as mock_settings:
            mock_settings.stacks_env = "development"
            mock_settings.stacks_root = ""
            dialog_class.return_value.exec.return_value = 1
            dialog_class.return_value.values.return_value = (
                "Demo",
                "production",
                "Edited description",
            )

            MainWindow._new_stack(self.window, self.saved)

        dialog_class.assert_called_once_with(
            "development", self.window, config=self.saved.config
        )
        self.window._stack_name_input.setText.assert_called_once_with("Demo")
        self.window._env_input.setText.assert_called_once_with("production")
        self.window._description_input.setPlainText.assert_called_once_with("Edited description")
        self.assertEqual(self.window._services, self.saved.config.services)
        self.assertIsNot(self.window._services[0], self.saved.config.services[0])
        self.assertEqual(self.window._loaded_stack_identity, ("Demo", "demo"))
        self.window._overwrite_check.setChecked.assert_called_once_with(False)

    def test_new_stack_cancel_preserves_current_form(self):
        from docker_stack_generator.main_window import MainWindow

        self.window._services = [make_service()]
        self.window._stack_name_input = Mock()
        with patch("docker_stack_generator.main_window.NewStackDialog") as dialog_class:
            dialog_class.return_value.exec.return_value = 0
            MainWindow._new_stack(self.window)

        self.assertEqual(len(self.window._services), 1)
        self.window._stack_name_input.setText.assert_not_called()

    def test_cancel_existing_stack_dialog_preserves_current_services(self):
        from docker_stack_generator.main_window import MainWindow

        current_service = replace(make_service(), name="current")
        self.window._services = [current_service]
        self.window._stack_name_input = Mock()
        with patch("docker_stack_generator.main_window.NewStackDialog") as dialog_class:
            dialog_class.return_value.exec.return_value = 0

            MainWindow._new_stack(self.window, self.saved)

        self.assertEqual(self.window._services, [current_service])
        self.window._stack_name_input.setText.assert_not_called()

    def test_new_stack_confirmation_rejection_preserves_current_services(self):
        from PyQt6.QtWidgets import QMessageBox
        from docker_stack_generator.main_window import MainWindow

        current_service = make_service()
        self.window._services = [current_service]
        with patch("docker_stack_generator.main_window.NewStackDialog") as dialog_class, \
                patch("docker_stack_generator.main_window.QMessageBox.question",
                      return_value=QMessageBox.StandardButton.No):
            dialog_class.return_value.exec.return_value = 1
            dialog_class.return_value.values.return_value = ("New Stack", "production", "")
            MainWindow._new_stack(self.window)

        self.assertEqual(self.window._services, [current_service])

    def test_file_new_action_routes_to_shared_new_stack_flow(self):
        from docker_stack_generator.main_window import MainWindow

        with patch.object(MainWindow, "_build_ui"), \
                patch.object(MainWindow, "_restore_geometry"), \
                patch.object(MainWindow, "_check_settings"), \
                patch.object(MainWindow, "_new_stack") as new_stack:
            window = MainWindow()
        self.addCleanup(window.deleteLater)
        file_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "&File"
        )
        new_action = next(action for action in file_menu.actions() if "New Stack" in action.text())
        new_action.trigger()

        new_stack.assert_called_once_with()

    def test_stack_library_new_button_emits_shared_flow_signal(self):
        from PyQt6.QtWidgets import QPushButton
        from docker_stack_generator.stack_library_widget import StackLibraryWidget

        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        requested = Mock()
        widget.new_stack_requested.connect(requested)
        button = next(
            candidate
            for candidate in widget.findChildren(QPushButton)
            if "New Stack" in candidate.text()
        )
        button.click()

        requested.assert_called_once_with()

    def test_new_stack_dialog_returns_trimmed_values(self):
        from docker_stack_generator.new_stack_dialog import NewStackDialog

        dialog = NewStackDialog("production")
        self.addCleanup(dialog.deleteLater)
        dialog._name_input.setText("  Media Stack  ")
        dialog._environment_input.setText(" production ")
        dialog._description_input.setPlainText("  Media services  ")

        self.assertEqual(dialog.values(), ("Media Stack", "production", "Media services"))

    def test_new_stack_dialog_prefills_existing_config(self):
        from docker_stack_generator.new_stack_dialog import NewStackDialog

        self.saved.config.description = "Existing description"
        dialog = NewStackDialog("development", config=self.saved.config)
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.windowTitle(), "Edit Stack")
        self.assertEqual(
            dialog.values(),
            ("Demo", "production", "Existing description"),
        )

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

    def test_library_add_service_opens_dialog_and_saves_appended_copy(self):
        from docker_stack_generator.main_window import MainWindow

        new_service = replace(make_service(), name="nginx", display_name="Nginx")
        self.window._monitoring_widget = Mock(_vault=object())
        self.window._stack_library_widget.refresh = Mock()
        with patch("docker_stack_generator.main_window.ServiceDialog") as dialog_class, \
                patch(
                    "docker_stack_generator.main_window.stack_library.save_stack",
                    return_value=self.saved,
                ) as save:
            dialog_class.return_value.exec.return_value = 1
            dialog_class.return_value.result_service.return_value = new_service

            MainWindow._add_library_service(self.window, self.saved)

        cfg = save.call_args.args[0]
        self.assertIsNot(cfg, self.saved.config)
        self.assertEqual([service.name for service in cfg.services], ["redis", "nginx"])
        save.assert_called_once_with(cfg, generated=False)
        self.window._stack_library_widget.refresh.assert_called_once_with(
            select_key=self.saved.key
        )

    def test_library_add_service_rejects_duplicate_name(self):
        from docker_stack_generator.main_window import MainWindow

        self.window._monitoring_widget = Mock(_vault=object())
        with patch("docker_stack_generator.main_window.ServiceDialog") as dialog_class, \
                patch("docker_stack_generator.main_window.QMessageBox.warning") as warning, \
                patch("docker_stack_generator.main_window.stack_library.save_stack") as save:
            dialog_class.return_value.exec.return_value = 1
            dialog_class.return_value.result_service.return_value = make_service()

            MainWindow._add_library_service(self.window, self.saved)

        warning.assert_called_once()
        save.assert_not_called()

    def test_library_remove_service_requires_confirmation_and_saves_remaining_copy(self):
        from PyQt6.QtWidgets import QMessageBox
        from docker_stack_generator.main_window import MainWindow

        saved = deepcopy(self.saved)
        saved.config.services.append(replace(make_service(), name="nginx", display_name="Nginx"))
        self.window._stack_library_widget.refresh = Mock()
        with patch(
            "docker_stack_generator.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "docker_stack_generator.main_window.stack_library.save_stack",
            return_value=saved,
        ) as save:
            MainWindow._remove_library_service(self.window, saved, 0)

        cfg = save.call_args.args[0]
        self.assertEqual([service.name for service in cfg.services], ["nginx"])
        self.assertEqual(len(saved.config.services), 2)
        save.assert_called_once_with(cfg, generated=False)
        self.window._stack_library_widget.refresh.assert_called_once_with(
            select_key=saved.key
        )

    def test_library_remove_service_cancel_keeps_saved_stack(self):
        from PyQt6.QtWidgets import QMessageBox
        from docker_stack_generator.main_window import MainWindow

        with patch(
            "docker_stack_generator.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ), patch("docker_stack_generator.main_window.stack_library.save_stack") as save:
            MainWindow._remove_library_service(self.window, self.saved, 0)

        save.assert_not_called()
        self.assertEqual(len(self.saved.config.services), 1)

    def test_service_action_buttons_follow_stack_and_row_selection(self):
        from docker_stack_generator.stack_library_widget import StackLibraryWidget

        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        self.assertFalse(widget._add_service_btn.isEnabled())
        self.assertFalse(widget._remove_service_btn.isEnabled())
        widget._show_entry(self.saved)
        self.assertTrue(widget._add_service_btn.isEnabled())
        self.assertFalse(widget._remove_service_btn.isEnabled())
        widget._svc_table.selectRow(0)
        self.assertTrue(widget._remove_service_btn.isEnabled())
        widget._show_entry(self.saved)
        self.assertEqual(widget._svc_table.currentRow(), -1)
        self.assertFalse(widget._remove_service_btn.isEnabled())
        widget._clear_details()
        self.assertFalse(widget._add_service_btn.isEnabled())
        self.assertFalse(widget._remove_service_btn.isEnabled())

    def test_service_action_buttons_emit_current_stack_and_row(self):
        from docker_stack_generator.stack_library_widget import StackLibraryWidget

        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        widget._show_entry(self.saved)
        add_requested = Mock()
        remove_requested = Mock()
        widget.add_service_requested.connect(add_requested)
        widget.remove_service_requested.connect(remove_requested)

        widget._add_service_btn.click()
        widget._svc_table.selectRow(0)
        widget._remove_service_btn.click()

        add_requested.assert_called_once_with(self.saved)
        remove_requested.assert_called_once_with(self.saved, 0)

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

    def test_double_clicking_stack_entry_requests_dialog_not_direct_load(self):
        from PyQt6.QtWidgets import QListWidgetItem
        from docker_stack_generator.stack_library_widget import StackLibraryWidget

        widget = StackLibraryWidget()
        self.addCleanup(widget.deleteLater)
        widget._show_entry(self.saved)
        requested = Mock()
        loaded = Mock()
        widget.open_stack_requested.connect(requested)
        widget.load_requested.connect(loaded)
        item = QListWidgetItem("Demo")
        widget._list.addItem(item)

        widget._list.itemDoubleClicked.emit(item)

        requested.assert_called_once_with(self.saved)
        loaded.assert_not_called()

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

