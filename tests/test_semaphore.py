import importlib.util
import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from docker_stack_generator.semaphore_client import (
    SemaphoreClient, SemaphoreError, check_endpoints, parse_endpoints, validate_targets, DEFAULT_TARGETS,
)


def response(data=None, code=200, text=""):
    result = Mock(status_code=code, text=text)
    result.json.return_value = data
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class SemaphoreClientTests(unittest.TestCase):
    def setUp(self):
        self.client = SemaphoreClient("https://semaphore.example", "admin", "secret")
        self.client.session = Mock()

    def test_start_matches_supplied_script(self):
        self.client.session.request.side_effect = [response(code=204), response({"id": 7})]
        self.assertEqual(self.client.start(1, 41, "traefik", {"other": True})["id"], 7)
        payload = self.client.session.request.call_args.kwargs["json"]
        self.assertEqual(payload["template_id"], 41)
        self.assertEqual(json.loads(payload["environment"]), {"stack_name": "traefik", "other": True})
        self.assertFalse(self.client.session.request.call_args.kwargs["allow_redirects"])

    def test_failure_does_not_leak_response_secrets(self):
        self.client.session.request.return_value = response(code=401, text="secret")
        with self.assertRaises(SemaphoreError) as caught:
            self.client.authenticate()
        self.assertNotIn("secret", str(caught.exception))

    def test_snapshot_and_raw_fallback(self):
        self.client._authenticated = True
        self.client.session.request.side_effect = [response({"status": "failed"}), response([]), response(text="fatal: task failed")]
        snapshot = self.client.snapshot(1, 7)
        self.assertEqual(snapshot["task"]["status"], "failed")
        self.assertEqual(snapshot["output"], "fatal: task failed")

    def test_status_survives_output_error(self):
        self.client._authenticated = True
        self.client.session.request.side_effect = [response({"status": "running"}), response(code=404), response(code=404)]
        snapshot = self.client.snapshot(1, 7)
        self.assertEqual(snapshot["task"]["status"], "running")
        self.assertTrue(snapshot["warning"])

    def test_invalid_stack_never_starts(self):
        with self.assertRaises(ValueError):
            self.client.start(1, 41, "../bad")
        self.client.session.request.assert_not_called()

    def test_targets_and_endpoints(self):
        validate_targets(DEFAULT_TARGETS)
        self.assertEqual(parse_endpoints("traefik=https://traefik.${DOMAIN}\nhttps://host/?a=b", "example.test"),
                         [("traefik", "https://traefik.example.test"), ("https://host/?a=b", "https://host/?a=b")])
        with self.assertRaises(ValueError):
            parse_endpoints("http://user:password@host")
        with self.assertRaises(ValueError):
            parse_endpoints("https://host.${DOMAIN}")

    @patch("docker_stack_generator.semaphore_client.requests.Session")
    def test_http_results_distinguish_auth_and_failure(self, session_type):
        session = session_type.return_value.__enter__.return_value
        session.get.side_effect = lambda url, **kwargs: response(code={"https://one": 200, "https://two": 401, "https://three": 503}[url])
        results = check_endpoints([("one", "https://one"), ("two", "https://two"), ("three", "https://three")])
        self.assertEqual([entry["state"] for entry in results],
                         ["Reachable", "Responding; authentication required", "HTTP error"])

    def test_api_timeout_has_safe_error_message(self):
        self.client.session.request.side_effect = requests.Timeout("secret token")
        with self.assertRaises(SemaphoreError) as caught:
            self.client.authenticate()
        self.assertNotIn("secret token", str(caught.exception))

    def test_api_token_skips_password_login(self):
        with patch("docker_stack_generator.semaphore_client.requests.Session") as session_type:
            session_type.return_value.headers = {}
            session_type.return_value.request.return_value = response({"id": 1})
            client = SemaphoreClient("https://example.test", token="private-token")
            client.authenticate()
            self.assertEqual(session_type.return_value.request.call_args.args,
                             ("GET", "https://example.test/api/user"))
            self.assertEqual(session_type.return_value.headers["Authorization"], "Bearer private-token")

    def test_empty_task_id_and_invalid_json_fail_cleanly(self):
        self.client._authenticated = True
        self.client.session.request.return_value = response({})
        with self.assertRaisesRegex(SemaphoreError, "task ID"):
            self.client.start(1, 41, "traefik")
        self.client.session.request.return_value.json.side_effect = ValueError()
        with self.assertRaisesRegex(SemaphoreError, "invalid JSON"):
            self.client.snapshot(1, 7)

    def test_extra_variables_cannot_replace_selected_stack(self):
        self.client._authenticated = True
        self.client.session.request.return_value = response({"id": 7})
        self.client.start(1, 41, "selected", {"stack_name": "other"})
        payload = self.client.session.request.call_args.kwargs["json"]
        self.assertEqual(json.loads(payload["environment"])["stack_name"], "selected")

    def test_close_clears_session_credentials(self):
        with patch("docker_stack_generator.semaphore_client.requests.Session") as session_type:
            session_type.return_value.headers = {}
            client = SemaphoreClient("https://example.test", password="password", token="private-token")
            client.close()
            self.assertEqual(client._password, "")
            self.assertNotIn("Authorization", client.session.headers)
            client.session.cookies.clear.assert_called_once()

    @patch("docker_stack_generator.semaphore_client.requests.Session")
    def test_tls_network_and_redirect_results(self, session_type):
        def result(url, **kwargs):
            if url == "https://tls":
                raise requests.exceptions.SSLError()
            if url == "https://network":
                raise requests.ConnectionError()
            return response(code=302)
        session_type.return_value.__enter__.return_value.get.side_effect = result
        results = check_endpoints([("tls", "https://tls"), ("network", "https://network"), ("redirect", "https://redirect")])
        self.assertEqual([entry["state"] for entry in results],
                         ["TLS error", "Unreachable", "Redirect; destination not verified"])

    def test_target_validation(self):
        for targets in ([], [DEFAULT_TARGETS[0], DEFAULT_TARGETS[0]],
                        [{**DEFAULT_TARGETS[0], "project": 0}],
                        [{**DEFAULT_TARGETS[0], "variables": []}]):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                validate_targets(targets)


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class DeploymentWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.deployment_widget import DeploymentWidget
        self.values = {}
        self.settings_patch = patch("docker_stack_generator.deployment_widget.settings")
        config = self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        config.get.side_effect = self.values.get
        config.set.side_effect = lambda key, value: self.values.__setitem__(key, value)
        config.update.side_effect = self.values.update
        config.stacks_root = ""
        self.library_patch = patch("docker_stack_generator.deployment_widget.stack_library")
        self.library_patch.start().list_stacks.return_value = []
        self.addCleanup(self.library_patch.stop)
        self.widget = DeploymentWidget()
        self.addCleanup(self.widget.close)
        self.addCleanup(self.widget._poll.stop)
        self.addCleanup(self.widget._health_timer.stop)

    def record(self):
        return {"server": "https://semaphore.example", "project": 1, "template": 41,
                "stack": "traefik", "environment": "development", "action": "Deploy", "id": 7, "status": "running"}

    def test_success_queues_health_checks_and_remembers_task(self):
        self.widget._started(self.record(), {"id": 7, "status": "running"})
        self.widget._snapshot({"task": {"status": "success"}, "output": "ok=1 failed=0", "warning": ""})
        self.assertFalse(self.widget._poll.isActive())
        self.assertEqual(self.widget._pending, self.widget._start_health)
        self.assertEqual(self.values["semaphore_history"][0]["status"], "success")
        self.assertIn("ok=1", self.widget._output.toPlainText())

    def test_pause_does_not_restart_health_after_inflight_success(self):
        self.widget._active = self.record()
        self.widget._pause()
        self.widget._snapshot({"task": {"status": "success"}, "output": "", "warning": ""})
        self.assertIsNone(self.widget._pending)
        self.assertFalse(self.widget._health_timer.isActive())

    def test_repeated_poll_failure_pauses_monitoring(self):
        self.widget._active = self.record()
        self.widget._begin_monitoring()
        for _ in range(5):
            self.widget._poll_error("network unavailable")
        self.assertFalse(self.widget._poll.isActive())
        self.assertIn("not stopped", self.widget._state.text())

    def test_credentials_are_not_saved(self):
        self.widget._url.setText("https://semaphore.example")
        self.widget._username.setText("admin")
        self.widget._password.setText("do-not-store")
        self.widget._token.setText("private-token")
        client = self.widget._new_client()
        self.addCleanup(client.close)
        self.assertNotIn("do-not-store", json.dumps(self.values))
        self.assertNotIn("private-token", json.dumps(self.values))

    def test_endpoint_edits_survive_stack_selection(self):
        self.widget._stack.setCurrentText("first")
        self.widget._stack_changed()
        self.widget._endpoints.setPlainText("first=https://first.example")
        self.widget._stack.setCurrentText("second")
        self.widget._stack_changed()
        self.widget._stack.setCurrentText("first")
        self.widget._stack_changed()
        self.assertEqual(self.widget._endpoints.toPlainText(), "first=https://first.example")


if __name__ == "__main__":
    unittest.main()

