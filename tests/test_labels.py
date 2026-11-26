import importlib.util
import json
import os
import unittest
from tempfile import TemporaryDirectory

import yaml

from docker_stack_generator.generator import StackConfig, generate_stack, render_compose_preview
from docker_stack_generator.stack_library import SavedStack
from test_environment import make_service


class ServiceLabelTests(unittest.TestCase):
    def render(self, service):
        config = StackConfig("demo", "Demo", "development", "", services=[service])
        return yaml.safe_load(render_compose_preview(config))["services"][service.name]["deploy"]

    def labels(self, service):
        return dict(entry.split("=", 1) for entry in self.render(service)["labels"])

    def test_existing_services_have_no_labels(self):
        self.assertNotIn("labels", self.render(make_service()))

    def test_traefik_defaults(self):
        service = make_service(traefik={})
        service.name = "my_service"
        self.assertEqual(self.labels(service), {
            "traefik.enable": "true",
            "traefik.swarm.network": "${NETWORK_TRAEFIK}",
            "traefik.http.routers.my_service.rule": "Host(`my_service.${DOMAIN}`)",
            "traefik.http.routers.my_service.entrypoints": "websecure",
            "traefik.http.routers.my_service.service": "my_service",
            "traefik.http.services.my_service.loadbalancer.server.port": "${MY_SERVICE_PORT}",
        })

    def test_homepage_defaults(self):
        service = make_service(homepage={})
        service.description = "Redis database"
        self.assertEqual(self.labels(service), {
            "homepage.group": "Infrastructure", "homepage.name": "Redis", "homepage.icon": "redis",
            "homepage.href": "https://redis.${DOMAIN}", "homepage.description": "Redis database",
        })

    def test_custom_values_and_combined_labels(self):
        service = make_service(
            traefik={"service": "cache", "port": "6379", "entrypoints": "web", "network": "proxy",
                     "rule": "Host(`cache.${DOMAIN}`)"},
            homepage={"group": "Data: #1", "name": 'Cache "server"', "icon": "redis.png",
                      "href": "https://cache.${DOMAIN}/?a=1&b=2", "description": "first\nsecond"},
        )
        labels = self.labels(service)
        self.assertEqual(len(labels), 11)
        self.assertEqual(labels["traefik.http.routers.redis.service"], "cache")
        self.assertEqual(labels["traefik.http.services.cache.loadbalancer.server.port"], "6379")
        self.assertEqual(labels["traefik.swarm.network"], "proxy")
        self.assertEqual(labels["homepage.group"], "Data: #1")
        self.assertEqual(labels["homepage.description"], "first\nsecond")
        self.assertEqual(labels["homepage.name"], 'Cache "server"')
        self.assertEqual(labels["homepage.href"], "https://cache.${DOMAIN}/?a=1&b=2")

    def test_saved_stack_round_trip_and_old_defaults(self):
        service = make_service(traefik={}, homepage={"group": "Databases"})
        config = StackConfig("demo", "Demo", "development", "", services=[service])
        data = json.loads(json.dumps(SavedStack("development__demo", config).to_dict()))
        restored = SavedStack.from_dict(data).config.services[0]
        self.assertEqual(restored.traefik, {})
        self.assertEqual(restored.homepage, {"group": "Databases"})
        del data["stack"]["services"][0]["traefik"]
        del data["stack"]["services"][0]["homepage"]
        legacy = SavedStack.from_dict(data).config.services[0]
        self.assertIsNone(legacy.traefik)
        self.assertIsNone(legacy.homepage)

    def test_labels_are_independent_per_service(self):
        first = make_service(traefik={}, homepage={"group": "Databases"})
        second = make_service(traefik={}, homepage={})
        second.name = "cache"
        second.display_name = "Cache"
        config = StackConfig("demo", "Demo", "development", "", services=[first, second])
        services = yaml.safe_load(render_compose_preview(config))["services"]
        cache_labels = dict(entry.split("=", 1) for entry in services["cache"]["deploy"]["labels"])
        self.assertEqual(cache_labels["homepage.group"], "Infrastructure")
        self.assertEqual(cache_labels["homepage.name"], "Cache")
        self.assertEqual(cache_labels["traefik.http.services.cache.loadbalancer.server.port"], "${CACHE_PORT}")
        self.assertFalse(any("routers.redis" in key for key in cache_labels))

    def test_generated_compose_file_has_swarm_labels(self):
        with TemporaryDirectory() as root:
            config = StackConfig("demo", "Demo", "development", root, services=[
                make_service(traefik={}, homepage={})
            ])
            generate_stack(config)
            source = (config.stack_dir / "compose.yml.j2").read_text(encoding="utf-8")
            self.assertIn('"traefik.http.services.redis.loadbalancer.server.port=${REDIS_PORT}"', source)
            self.assertIn('"homepage.href=https://redis.${DOMAIN}"', source)
            service = yaml.safe_load(render_compose_preview(config))["services"]["redis"]
            self.assertNotIn("labels", service)
            self.assertEqual(len(service["deploy"]["labels"]), 11)


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class LabelTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.service_dialog import ServiceDialog
        self.dialog = ServiceDialog(existing=make_service())
        self.addCleanup(self.dialog.close)

    def test_tabs_start_disabled_for_existing_service(self):
        self.assertIsNone(self.dialog.result_service().traefik)
        self.assertIsNone(self.dialog.result_service().homepage)

    def test_service_changes_update_defaults_not_overrides(self):
        self.dialog._traefik_editor._enabled.setChecked(True)
        self.dialog._homepage_editor._enabled.setChecked(True)
        self.dialog._homepage_editor._fields["group"].setText("Databases")
        self.dialog._name_input.setText("My Cache")
        self.dialog._desc_input.setText("Shared cache")
        self.assertEqual(self.dialog._traefik_editor._fields["port"].placeholderText(), "${MY_CACHE_PORT}")
        self.assertEqual(self.dialog._homepage_editor._fields["name"].placeholderText(), "My Cache")
        self.assertEqual(self.dialog._homepage_editor._fields["description"].placeholderText(), "Shared cache")
        labels = ServiceLabelTests().labels(self.dialog.result_service())
        self.assertEqual(labels["homepage.group"], "Databases")
        self.assertEqual(labels["homepage.href"], "https://my_cache.${DOMAIN}")
        self.assertEqual(labels["traefik.http.routers.my_cache.rule"], "Host(`my_cache.${DOMAIN}`)")

    def test_overrides_survive_reopening(self):
        from docker_stack_generator.service_dialog import ServiceDialog
        self.dialog._traefik_editor.set_values({"entrypoints": "web", "port": "8080"})
        self.dialog._homepage_editor.set_values({"group": "Databases", "name": "Custom Redis"})
        service = self.dialog.result_service()
        reopened = ServiceDialog(existing=service)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.result_service().traefik, service.traefik)
        self.assertEqual(reopened.result_service().homepage, service.homepage)

    def test_disabling_one_label_set_keeps_the_other(self):
        self.dialog._traefik_editor.set_values({})
        self.dialog._homepage_editor.set_values({"group": "Data"})
        self.dialog._traefik_editor._enabled.setChecked(False)
        labels = ServiceLabelTests().labels(self.dialog.result_service())
        self.assertFalse(any(key.startswith("traefik.") for key in labels))
        self.assertEqual(labels["homepage.group"], "Data")


if __name__ == "__main__":
    unittest.main()

