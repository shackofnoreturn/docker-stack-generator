import importlib.util
import os
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace

from docker_stack_generator.generator import StackConfig, generate_stack, slugify, stack_slugify
from docker_stack_generator.stack_library import StackLibrary, stack_key
from test_environment import make_service


class StackNameTests(unittest.TestCase):
    def test_stack_names_use_hyphens(self):
        for value in ("arr-media", "Arr Media", "Arr_Media", "  Arr -- Media! "):
            with self.subTest(value=value):
                self.assertEqual(stack_slugify(value), "arr-media")
        self.assertEqual(stack_slugify(" !!! "), "")
        self.assertEqual(slugify("Arr Media"), "arr_media")

    def test_library_keys_distinguish_legacy_underscores_and_hyphens(self):
        self.assertEqual(stack_key("development", "arr_media"), "development__arr_media")
        self.assertEqual(stack_key("development", "arr-media"), "development__arr-media")
        with TemporaryDirectory() as root:
            library = StackLibrary(Path(root) / "library")
            for name in ("arr_media", "arr-media"):
                library.save_stack(StackConfig(name, "Arr Media", "development", root))
            self.assertEqual(len(library.list_stacks()), 2)
            for name in ("arr_media", "arr-media"):
                self.assertEqual(library.load_stack(stack_key("development", name)).config.name, name)
            duplicate = library.duplicate_stack("development__arr_media", "My New Stack")
            self.assertEqual(duplicate.config.name, "my-new-stack")
            self.assertEqual(duplicate.key, "development__my-new-stack")

    def test_generated_path_and_stack_identifier_keep_hyphens(self):
        with TemporaryDirectory() as root:
            cfg = StackConfig(stack_slugify("Arr Media"), "Arr Media", "development", root,
                              services=[make_service()])
            generate_stack(cfg)
            self.assertTrue((Path(root) / "development" / "arr-media" / "stack.yml").is_file())
            self.assertIn("stack_name: arr-media", (cfg.stack_dir / "stack.yml").read_text(encoding="utf-8"))
            self.assertFalse((Path(root) / "development" / "arr_media").exists())


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class StackNameUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_new_names_and_loaded_legacy_identity(self):
        from docker_stack_generator.main_window import MainWindow
        window = SimpleNamespace(_loaded_stack_identity=None)
        self.assertEqual(MainWindow._stack_name(window, "Arr Media"), "arr-media")
        window._loaded_stack_identity = ("Arr Media", "arr_media")
        self.assertEqual(MainWindow._stack_name(window, "Arr Media"), "arr_media")
        self.assertEqual(MainWindow._stack_name(window, "arr-media"), "arr-media")
        self.assertEqual(MainWindow._stack_name(window, "Another Stack"), "another-stack")
