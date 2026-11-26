import importlib.util
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from docker_stack_generator.generator import (
    ServiceConfig, StackConfig, generate_stack, render_compose_preview,
)
from docker_stack_generator.stack_library import SavedStack


def make_service(**kwargs):
    return ServiceConfig(
        name="redis", display_name="Redis", image="redis:latest", image_base="redis",
        image_tag="latest", description="", port="6379", port_source="manual",
        restart_profile="medium", resource_profile="medium", **kwargs,
    )


class ComposeEnvironmentTests(unittest.TestCase):
    def render(self, services):
        config = StackConfig("demo", "Demo", "development", "", services=services)
        return yaml.safe_load(render_compose_preview(config))

    def test_default_timezone(self):
        self.assertEqual(self.render([make_service()])["services"]["redis"]["environment"],
                         {"TZ": "${TZ}"})

    def test_values_remain_strings_and_override_timezone(self):
        values = {"TZ": "Europe/Brussels", "EMPTY": "", "BOOL": "true", "NUMBER": "0123",
                  "SPECIAL": 'a: b # comment\n"quoted"\\path=value', "REF": "${PASSWORD}"}
        self.assertEqual(
            self.render([make_service(environment=values)])["services"]["redis"]["environment"],
            values,
        )

    def test_service_values_are_independent(self):
        first = make_service(environment={"CUSTOM": "one"})
        second = make_service()
        second.name = "other"
        services = self.render([first, second])["services"]
        self.assertEqual(services["redis"]["environment"]["CUSTOM"], "one")
        self.assertNotIn("CUSTOM", services["other"]["environment"])

    def test_saved_stack_round_trip(self):
        config = StackConfig("demo", "Demo", "development", "", services=[
            make_service(environment={"TOKEN": "a=b", "EMPTY": ""})
        ])
        data = json.loads(json.dumps(SavedStack("development__demo", config).to_dict()))
        restored = SavedStack.from_dict(data)
        self.assertEqual(restored.config.services[0].environment, config.services[0].environment)

    def test_old_saved_stack_defaults_to_empty_environment(self):
        config = StackConfig("demo", "Demo", "development", "", services=[make_service()])
        data = SavedStack("development__demo", config).to_dict()
        del data["stack"]["services"][0]["environment"]
        self.assertEqual(SavedStack.from_dict(data).config.services[0].environment, {})

    def test_generated_compose_file_contains_variables(self):
        with TemporaryDirectory() as root:
            config = StackConfig("demo", "Demo", "development", root, services=[
                make_service(environment={"CUSTOM": "a: b # value"})
            ])
            generated = generate_stack(config)
            compose_path = config.stack_dir / "compose.yml.j2"
            self.assertIn(str(compose_path), generated)
            self.assertIn('"CUSTOM": "a: b # value"', Path(compose_path).read_text(encoding="utf-8"))
            self.assertEqual(
                yaml.safe_load(render_compose_preview(config))["services"]["redis"]["environment"],
                {"TZ": "${TZ}", "CUSTOM": "a: b # value"},
            )


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class EnvironmentEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.service_dialog import ServiceDialog
        self.dialog = ServiceDialog()
        self.editor = self.dialog._environment_editor
        self.addCleanup(self.dialog.close)

    def test_suggestions_do_not_overwrite_explicit_values(self):
        self.editor.set_values({"TOKEN": "custom", "EMPTY": ""})
        self.editor.set_available({"env": ["TOKEN=default", "OTHER=a=b", "EMPTY="]})
        self.editor._available.selectAll()
        self.editor._add_available()
        self.assertEqual(self.editor.values(), {"TOKEN": "custom", "EMPTY": "", "OTHER": "a=b"})

    def test_invalid_and_duplicate_names_are_rejected(self):
        for name in ("BAD NAME", "1NAME", "", "NAME=VALUE"):
            with self.subTest(name=name):
                self.editor.set_values({name: "value"})
                with self.assertRaises(ValueError):
                    self.editor.values()
        self.editor.set_values({"DUPLICATE": "one"})
        self.editor._append("DUPLICATE", "two")
        with self.assertRaises(ValueError):
            self.editor.values()

    def test_remove_variable(self):
        self.editor.set_values({"FIRST": "one", "SECOND": "two"})
        self.editor._table.selectRow(0)
        self.editor._remove_rows()
        self.assertEqual(self.editor.values(), {"SECOND": "two"})

    def test_service_dialog_round_trip(self):
        from docker_stack_generator.service_dialog import ServiceDialog
        service = make_service(environment={"TOKEN": "a=b", "EMPTY": ""})
        service.metadata = {"image": service.image, "env": ["AVAILABLE=default"]}
        dialog = ServiceDialog(existing=service)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.result_service().environment, service.environment)
        self.assertEqual(dialog._environment_editor._available.count(), 1)

    def test_changing_image_clears_only_suggestions(self):
        self.editor.set_values({"CUSTOM": "value"})
        self.editor.set_available({"env": ["OLD=default"]})
        self.dialog._image_input.setText("redis")
        self.assertEqual(self.editor._available.count(), 0)
        self.assertEqual(self.editor.values(), {"CUSTOM": "value"})
        self.dialog._on_meta_done({"image": "nginx", "env": ["STALE=default"]}, [])
        self.assertEqual(self.editor._available.count(), 0)


if __name__ == "__main__":
    unittest.main()

