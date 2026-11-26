import base64
import importlib.util
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from docker_stack_generator.generator import (
    GenerationError, StackConfig, generate_stack, validate_service_configs,
)
from docker_stack_generator.stack_library import SavedStack
from test_environment import make_service


def encoded(contents):
    return base64.b64encode(contents).decode("ascii")


class WorkspaceTaskTests(unittest.TestCase):
    def test_launch_and_install_tasks(self):
        path = Path(__file__).resolve().parents[1] / ".vscode/tasks.json"
        tasks = {task["label"]: task for task in json.loads(path.read_text(encoding="utf-8"))["tasks"]}
        launch = tasks["Run application"]
        install = tasks["Install prerequisites"]
        self.assertEqual(launch["command"], "python")
        self.assertEqual(launch["args"], ["${workspaceFolder}/run.py"])
        self.assertEqual(install["command"], "python")
        self.assertEqual(install["args"], ["-m", "pip", "install", "-e", "."])
        for task in (launch, install):
            self.assertEqual(task["options"]["cwd"], "${workspaceFolder}")


class ServiceConfigFileTests(unittest.TestCase):
    def test_generate_nested_files_and_empty_directories(self):
        with TemporaryDirectory() as root:
            service = make_service(config_files={
                "redis.conf": encoded(b"port 6379\r\n"),
                "dynamic/routes.yml": encoded(b"http: {}\n"),
                "binary/data.bin": encoded(bytes(range(256))),
            }, config_directories=["dynamic", "empty/nested"])
            config = StackConfig("demo", "Demo", "development", root, services=[service])
            generated = generate_stack(config)
            target = config.stack_dir / "configs" / "redis"
            for path, contents in service.config_files.items():
                self.assertEqual((target / path).read_bytes(), base64.b64decode(contents))
                self.assertIn(str((target / path).resolve()), generated)
            self.assertTrue((target / "empty/nested/.gitkeep").is_file())

    def test_service_files_are_isolated(self):
        with TemporaryDirectory() as root:
            first = make_service(config_files={"app.conf": encoded(b"first")})
            second = make_service(config_files={"app.conf": encoded(b"second")})
            second.name = "other"
            config = StackConfig("demo", "Demo", "development", root, services=[first, second])
            generate_stack(config)
            self.assertEqual((config.stack_dir / "configs/redis/app.conf").read_bytes(), b"first")
            self.assertEqual((config.stack_dir / "configs/other/app.conf").read_bytes(), b"second")

    def test_persistence_and_legacy_defaults(self):
        service = make_service(config_files={"app.conf": encoded(b"content")}, config_directories=["dynamic"])
        config = StackConfig("demo", "Demo", "development", "", services=[service])
        data = json.loads(json.dumps(SavedStack("development__demo", config).to_dict()))
        restored = SavedStack.from_dict(data).config.services[0]
        self.assertEqual(restored.config_files, service.config_files)
        self.assertEqual(restored.config_directories, ["dynamic"])
        del data["stack"]["services"][0]["config_files"]
        del data["stack"]["services"][0]["config_directories"]
        legacy = SavedStack.from_dict(data).config.services[0]
        self.assertEqual(legacy.config_files, {})
        self.assertEqual(legacy.config_directories, [])

    def test_invalid_paths_and_conflicts_are_rejected(self):
        for path in ("../escape", "/absolute", "C:/absolute", "a\\b", "a/../b", "", "a//b", "NUL", "x."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_service_configs({path: encoded(b"")}, [])
        for files, directories in (
            ({"app": encoded(b""), "app/nested": encoded(b"")}, []),
            ({"app": encoded(b"")}, ["app/nested"]),
            ({"app": encoded(b"")}, ["APP"]),
            ({"app": encoded(b""), "APP": encoded(b"")}, []),
            ({"app": "invalid base64!"}, []),
        ):
            with self.subTest(files=files, directories=directories), self.assertRaises(ValueError):
                validate_service_configs(files, directories)

    def test_invalid_config_does_not_create_stack(self):
        with TemporaryDirectory() as root:
            config = StackConfig("demo", "Demo", "development", root, services=[
                make_service(config_directories=["../escape"])
            ])
            with self.assertRaises(GenerationError):
                generate_stack(config)
            self.assertFalse(config.stack_dir.exists())

    def test_overwrite_updates_managed_files_not_unrelated_files(self):
        with TemporaryDirectory() as root:
            service = make_service(config_files={"app.conf": encoded(b"first")})
            config = StackConfig("demo", "Demo", "development", root, services=[service])
            generate_stack(config)
            target = config.stack_dir / "configs/redis"
            (target / "unrelated.conf").write_bytes(b"keep")
            service.config_files["app.conf"] = encoded(b"updated")
            with self.assertRaises(GenerationError):
                generate_stack(config)
            generate_stack(config, overwrite=True)
            self.assertEqual((target / "app.conf").read_bytes(), b"updated")
            self.assertEqual((target / "unrelated.conf").read_bytes(), b"keep")

    def test_existing_directory_and_file_conflicts(self):
        for conflict in ("file_is_directory", "parent_is_file", "configs_is_file"):
            with self.subTest(conflict=conflict), TemporaryDirectory() as root:
                service = make_service(config_files={"dynamic/routes.yml": encoded(b"content")})
                config = StackConfig("demo", "Demo", "development", root, services=[service])
                target = config.stack_dir / "configs/redis"
                if conflict == "configs_is_file":
                    config.stack_dir.mkdir(parents=True)
                    (config.stack_dir / "configs").write_bytes(b"not a directory")
                    with self.assertRaises(GenerationError):
                        generate_stack(config, overwrite=True)
                    self.assertFalse((config.stack_dir / "stack.yml").exists())
                    continue
                target.mkdir(parents=True)
                if conflict == "file_is_directory":
                    (target / "dynamic/routes.yml").mkdir(parents=True)
                else:
                    (target / "dynamic").write_bytes(b"not a directory")
                with self.assertRaises(GenerationError):
                    generate_stack(config, overwrite=True)
                self.assertFalse((config.stack_dir / "stack.yml").exists())


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class ConfigFileEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.service_dialog import ServiceDialog
        self.dialog = ServiceDialog(existing=make_service())
        self.editor = self.dialog._configs_editor
        self.addCleanup(self.dialog.close)

    def test_add_and_edit_nested_file(self):
        self.editor._folder.setText("dynamic")
        self.editor._add_file("routes.yml", b"http: {}\r\n")
        self.assertEqual(self.editor.values()[0], {"dynamic/routes.yml": encoded(b"http: {}\r\n")})
        self.editor._contents.setPlainText("http:\n  routers: {}\n")
        self.assertEqual(self.editor.values()[0]["dynamic/routes.yml"], encoded(b"http:\n  routers: {}\n"))

    def test_binary_file_preserved_without_editing(self):
        contents = bytes(range(256))
        self.editor._add_file("data.bin", contents)
        self.assertTrue(self.editor._contents.isReadOnly())
        self.assertEqual(self.editor.values()[0]["data.bin"], encoded(contents))

    def test_duplicate_and_invalid_paths_rejected(self):
        self.editor._add_file("app.conf", b"first")
        with self.assertRaises(ValueError):
            self.editor._add_file("APP.conf", b"second")
        self.editor._folder.setText("../escape")
        with self.assertRaises(ValueError):
            self.editor._add_file("file", b"")
        self.editor._files.item(0).setText("../renamed")
        with self.assertRaises(ValueError):
            self.editor.values()

    def test_remove_and_service_round_trip(self):
        from docker_stack_generator.service_dialog import ServiceDialog
        self.editor._add_file("app.conf", b"config")
        self.editor._directories.set_values(["dynamic"])
        service = self.dialog.result_service()
        reopened = ServiceDialog(existing=service)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.result_service().config_files, service.config_files)
        self.assertEqual(reopened.result_service().config_directories, ["dynamic"])
        self.editor._remove_file()
        self.assertEqual(self.editor.values(), ({}, ["dynamic"]))

    def test_configuration_directory_follows_service_name(self):
        self.dialog._name_input.setText("Traefik")
        self.assertEqual(self.editor._destination.text(), "configs/traefik/")


if __name__ == "__main__":
    unittest.main()

