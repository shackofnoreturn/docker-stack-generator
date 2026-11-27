import importlib.util
import json
import os
import ssl
import unittest
import base64
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import Mock, patch

import requests

from docker_stack_generator.monitoring import (
    MonitorResult, MonitoringStore, MonitorTarget, check_target, check_targets,
    discover_targets, stack_state, validate_endpoint,
)
from docker_stack_generator.generator import StackConfig
from docker_stack_generator.stack_library import SavedStack
from test_environment import make_service


def response(code):
    value = Mock(status_code=code)
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


class MonitoringTests(unittest.TestCase):
    def test_discovery_includes_unconfigured_services_and_environment_identity(self):
        basic = make_service()
        homepage = make_service()
        homepage.name = "web"
        homepage.homepage = {"href": "https://web.${DOMAIN}/health"}
        traefik = make_service()
        traefik.name = "proxy"
        traefik.traefik = {"rule": "Host(`proxy.example.test`)"}
        entries = [
            SavedStack("one", StackConfig("demo", "Demo", "production", "", services=[basic, homepage, traefik])),
            SavedStack("two", StackConfig("demo", "Demo", "testing", "", services=[basic])),
        ]
        targets = discover_targets(entries, {}, "example.test")
        self.assertEqual(len(targets), 4)
        urls = {(target.stack, target.service): target.url for target in targets}
        self.assertEqual(urls[("production/demo", "web")], "https://web.example.test/health")
        self.assertEqual(urls[("production/demo", "proxy")], "https://proxy.example.test")
        self.assertEqual(urls[("testing/demo", "redis")], "https://redis.example.test")
        self.assertEqual(len({target.key for target in targets}), 4)
        key = MonitorTarget("production/demo", "web", "").key
        edited = discover_targets(entries, {key: ""}, "example.test")
        self.assertEqual(next(target.url for target in edited if target.key == key), "")

    def test_custom_traefik_rules_are_not_guessed(self):
        service = make_service()
        service.traefik = {"rule": "Host(`one`) || Host(`two`)"}
        entries = [SavedStack("one", StackConfig("demo", "Demo", "production", "", services=[service]))]
        self.assertEqual(discover_targets(entries, {}, "")[0].url, "https://redis.${DOMAIN}")

    def test_domain_defaults_update_without_overwriting_custom_endpoints(self):
        service = make_service()
        entries = [SavedStack("one", StackConfig("demo", "Demo", "production", "", services=[service]))]
        initial = discover_targets(entries, {}, "")[0]
        self.assertEqual(check_target(initial).state, "Invalid endpoint")
        generated = discover_targets(entries, {}, "dev.example.test")[0]
        self.assertEqual(generated.url, "https://redis.dev.example.test")
        self.assertEqual(discover_targets(entries, {}, "prod.example.test")[0].url,
                         "https://redis.prod.example.test")
        custom = {generated.key: "https://redis.dev.example.test/dashboard/"}
        self.assertEqual(discover_targets(entries, custom, "prod.example.test")[0].url,
                         custom[generated.key])

    def test_endpoint_validation_and_credential_binding(self):
        for value in ("ftp://host", "https://", "https://user:pass@host", "https://host/#part",
                      "https://host.${DOMAIN}", "https://host:99999", "https://host:invalid", "https://host/ bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_endpoint(value)
        self.assertEqual(validate_endpoint(" https://host/health?a=b "), "https://host/health?a=b")
        one = MonitorTarget("production/demo", "redis", "https://one/health")
        two = MonitorTarget(one.stack, one.service, "https://two/health")
        self.assertEqual(one.key, two.key)
        self.assertNotEqual(one.credential_key, two.credential_key)

    @patch("docker_stack_generator.monitoring.requests.Session")
    def test_states_are_accurate_and_redirects_never_followed(self, session_type):
        session = session_type.return_value.__enter__.return_value
        target = MonitorTarget("production/demo", "web", "https://host")
        for code, expected in ((200, "Healthy"), (204, "Healthy"), (301, "Redirect"),
                               (401, "Authorization required"), (403, "Authorization required"),
                               (404, "HTTP error"), (503, "HTTP error")):
            session.get.return_value = response(code)
            result = check_target(target)
            self.assertEqual(result.state, expected)
            self.assertTrue(result.checked_at)
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
            self.assertTrue(session.get.call_args.kwargs["stream"])
            self.assertEqual(session.get.call_args.kwargs["timeout"], (3, 5))
            self.assertFalse(session.trust_env)

    @patch("docker_stack_generator.monitoring.requests.Session")
    def test_safe_network_and_tls_errors(self, session_type):
        session = session_type.return_value.__enter__.return_value
        for error, state in ((requests.exceptions.SSLError("private"), "TLS error"),
                             (requests.Timeout("private"), "Unreachable"),
                             (requests.ConnectionError("private"), "Unreachable")):
            session.get.side_effect = error
            result = check_target(MonitorTarget("stack", "service", "https://host"))
            self.assertEqual(result.state, state)
            self.assertNotIn("private", result.detail)

    @patch("docker_stack_generator.monitoring.requests.Session")
    def test_basic_and_bearer_are_scoped_and_https_only(self, session_type):
        session = session_type.return_value.__enter__.return_value
        session.get.return_value = response(200)
        target = MonitorTarget("stack", "service", "https://host")
        credential = {"kind": "basic", "username": "alice", "password": "private"}
        self.assertEqual(check_target(target, credential).state, "Healthy")
        auth = session.get.call_args.kwargs["auth"]
        self.assertEqual((auth.username, auth.password), ("alice", "private"))
        check_target(target, {"kind": "bearer", "token": "private-token"})
        self.assertEqual(session.get.call_args.kwargs["headers"], {"Authorization": "Bearer private-token"})
        session.get.reset_mock()
        http = MonitorTarget("stack", "service", "http://host")
        self.assertEqual(check_target(http, credential).state, "Invalid endpoint")
        session.get.assert_not_called()
        self.assertEqual(check_target(target, {"kind": "bearer", "token": "bad\r\ntoken"}).state,
                         "Authorization required")
        session.get.assert_not_called()

    def test_unconfigured_and_invalid_never_use_network(self):
        with patch("docker_stack_generator.monitoring.requests.Session") as session:
            self.assertEqual(check_target(MonitorTarget("stack", "service", "")).state, "Not configured")
            self.assertEqual(check_target(MonitorTarget("stack", "service", "not a URL")).state, "Invalid endpoint")
            session.assert_not_called()

    def test_aggregate_never_reports_partial_or_auth_checks_as_healthy(self):
        target = MonitorTarget("stack", "service", "https://host")
        def result(state):
            return MonitorResult(target, state, "", "")
        self.assertEqual(stack_state([]), "Not configured")
        self.assertEqual(stack_state([result("Healthy")]), "Healthy")
        for state in ("Not configured", "Redirect", "Not checked"):
            self.assertEqual(stack_state([result("Healthy"), result(state)]), "Incomplete")
        self.assertEqual(stack_state([result("Healthy"), result("Authorization required")]), "Authorization required")
        self.assertEqual(stack_state([result("Authorization required"), result("TLS error")]), "Issues")

    def test_concurrent_checks_do_not_share_credentials_between_services(self):
        targets = [MonitorTarget("stack", "one", "https://one"), MonitorTarget("stack", "two", "https://two")]
        credential = {"kind": "bearer", "token": "private"}
        with patch("docker_stack_generator.monitoring.check_target") as check:
            check_targets(targets, {targets[0].credential_key: credential})
        self.assertEqual(check.call_args_list[0].args, (targets[0], credential))
        self.assertEqual(check.call_args_list[1].args, (targets[1], None))

    def test_real_local_http_checks_and_authorization_classification(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/health" else 401)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            results = check_targets([MonitorTarget("stack", "one", url + "/health"),
                                     MonitorTarget("stack", "two", url + "/private")], {})
            self.assertEqual([result.state for result in results], ["Healthy", "Authorization required"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_https_credentials_change_authorization_failure_to_healthy(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                basic = "Basic " + base64.b64encode(b"alice:private").decode("ascii")
                accepted = self.headers.get("Authorization") in (basic, "Bearer private-token")
                self.send_response(200 if accepted else 401)
                self.end_headers()

            def log_message(self, *_):
                pass

        with TemporaryDirectory() as directory:
            path = Path(directory)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                    .sign(key, hashes.SHA256()))
            cert_path, key_path = path / "cert.pem", path / "key.pem"
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                  serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = Thread(target=server.serve_forever)
            thread.start()
            real_session = requests.Session

            def trusted_session():
                session = real_session()
                session.verify = str(cert_path)
                return session

            try:
                target = MonitorTarget("stack", "private", f"https://localhost:{server.server_port}/health")
                with patch("docker_stack_generator.monitoring.requests.Session", side_effect=trusted_session):
                    self.assertEqual(check_target(target).state, "Authorization required")
                    self.assertEqual(check_target(target, {"kind": "basic", "username": "alice",
                                                           "password": "private"}).state, "Healthy")
                    self.assertEqual(check_target(target, {"kind": "bearer", "token": "private-token"}).state,
                                     "Healthy")
                    self.assertEqual(check_target(target, {"kind": "bearer", "token": "wrong"}).state,
                                     "Authorization required")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class MonitoringStoreTests(unittest.TestCase):
    def test_preferences_roundtrip_and_explicit_corruption_failure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring.json"
            store = MonitoringStore(path)
            self.assertEqual(store.load()["interval"], 60)
            values = {"endpoints": {"key": "https://host"}, "domain": "example.test", "interval": 30}
            store.save(values)
            self.assertEqual(store.load(), values)
            for value in ("not json", "[]", '{"endpoints": {}, "domain": "", "interval": 0}'):
                path.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Cannot load"):
                    store.load()

    def test_failed_atomic_save_preserves_previous_preferences(self):
        with TemporaryDirectory() as directory:
            store = MonitoringStore(Path(directory) / "monitoring.json")
            values = {"endpoints": {}, "domain": "", "interval": 60}
            store.save(values)
            with patch("docker_stack_generator.monitoring.os.replace", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    store.save({**values, "interval": 30})
            self.assertEqual(store.load(), values)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class MonitoringWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.monitoring_widget import MonitoringWidget
        service = make_service()
        service.homepage = {"href": "https://redis.example.test/health"}
        entry = SavedStack("one", StackConfig("demo", "Demo", "production", "", services=[service]))
        library_patch = patch("docker_stack_generator.monitoring_widget.stack_library")
        library_patch.start().list_stacks.return_value = [entry]
        self.addCleanup(library_patch.stop)
        store_patch = patch("docker_stack_generator.monitoring_widget.MonitoringStore")
        self.store = store_patch.start().return_value
        self.store.load.return_value = {"endpoints": {}, "domain": "", "interval": 60}
        self.addCleanup(store_patch.stop)
        vault_patch = patch("docker_stack_generator.monitoring_widget.CredentialVault")
        self.vault = vault_patch.start().return_value
        self.vault.unlocked = False
        self.addCleanup(vault_patch.stop)
        self.widget = MonitoringWidget()
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.shutdown)

    def test_status_aggregation_and_authorization_is_visible(self):
        target = self.widget._targets[0]
        self.widget._checked([MonitorResult(target, "Authorization required", "HTTP 401", "now")])
        parent = self.widget._tree.topLevelItem(0)
        self.assertEqual(parent.text(1), "Authorization required")
        self.assertEqual(parent.child(0).text(1), "Authorization required")
        self.assertIn("1 need attention", self.widget._state.text())

    def test_vault_locked_checks_never_retrieve_credentials(self):
        with patch("docker_stack_generator.monitoring_widget._MonitorWorker") as worker:
            self.widget.check_now()
        self.vault.get.assert_not_called()
        self.assertEqual(worker.call_args.args[1], {})
        self.widget._worker = None

    def test_auth_credentials_persist_only_in_vault_and_retry(self):
        self.vault.unlocked = True
        self.widget._tree.setCurrentItem(self.widget._tree.topLevelItem(0).child(0))
        credential = {"kind": "basic", "username": "alice", "password": "private"}
        with patch("docker_stack_generator.monitoring_widget._CredentialDialog") as dialog_type, \
                patch.object(self.widget, "check_now") as check:
            dialog = dialog_type.return_value
            dialog.exec.return_value = True
            dialog.value.return_value = credential
            self.widget._set_credentials()
        self.vault.set.assert_called_once_with(self.widget._targets[0].credential_key, credential)
        check.assert_called_once()
        self.assertNotIn("private", json.dumps(self.widget._preferences))
        dialog.clear.assert_called_once()

    def test_edit_endpoint_invalidates_result_and_credentials_binding(self):
        original = self.widget._targets[0]
        self.widget._checked([MonitorResult(original, "Healthy", "HTTP 200", "now")])
        self.widget._tree.setCurrentItem(self.widget._tree.topLevelItem(0).child(0))
        with patch("docker_stack_generator.monitoring_widget.QInputDialog.getText",
                   return_value=("https://other.example.test/health", True)):
            self.widget._edit_endpoint()
        self.assertNotEqual(original.credential_key, self.widget._targets[0].credential_key)
        self.assertEqual(self.widget._results, {})
        self.vault.get.assert_not_called()

    def test_timer_and_close_while_check_running(self):
        with patch.object(self.widget, "check_now"):
            self.widget._toggle_monitoring()
            self.assertTrue(self.widget._timer.isActive())
            self.widget._toggle_monitoring()
            self.assertFalse(self.widget._timer.isActive())
        self.widget._worker = Mock()
        with patch("docker_stack_generator.monitoring_widget.QMessageBox.warning"):
            self.assertFalse(self.widget.prepare_close())
        self.widget._worker = None
        self.assertTrue(self.widget.prepare_close())

    def test_timer_does_not_start_checks_while_editing_credentials(self):
        self.widget._editing = True
        with patch("docker_stack_generator.monitoring_widget._MonitorWorker") as worker:
            self.widget.check_now()
        worker.assert_not_called()

    def test_vault_viewer_masks_reveals_and_clears_credentials(self):
        from PyQt6.QtWidgets import QLineEdit
        from docker_stack_generator.monitoring_widget import _VaultDialog
        target = self.widget._targets[0]
        self.vault.keys.return_value = [target.credential_key]
        self.vault.get.return_value = {"kind": "basic", "username": "alice", "password": "private"}
        dialog = _VaultDialog(self.vault)
        try:
            row = dialog._entries.topLevelItem(0)
            self.assertEqual(row.text(2), target.url)
            dialog._entries.setCurrentItem(row)
            self.assertEqual(dialog._secret.text(), "private")
            self.assertEqual(dialog._secret.echoMode(), QLineEdit.EchoMode.Password)
            dialog._reveal.setChecked(True)
            self.assertEqual(dialog._secret.echoMode(), QLineEdit.EchoMode.Normal)
            dialog.clear()
            self.assertEqual(dialog._secret.text(), "")
            self.assertFalse(dialog._reveal.isChecked())
        finally:
            dialog.deleteLater()

    def test_view_vault_unlocks_and_pauses_checks_while_open(self):
        self.vault.unlocked = True
        with patch("docker_stack_generator.monitoring_widget._VaultDialog") as dialog_type:
            dialog_type.return_value.exec.side_effect = lambda: self.assertTrue(self.widget._editing)
            self.widget._show_vault()
        dialog_type.assert_called_once_with(self.vault, self.widget)
        dialog_type.return_value.clear.assert_called_once()
        self.assertFalse(self.widget._editing)

    def test_vault_deletion_requires_confirmation_and_supports_old_endpoints(self):
        from PyQt6.QtWidgets import QMessageBox
        from docker_stack_generator.monitoring_widget import _VaultDialog
        old = MonitorTarget("production/old-stack", "old-service", "https://old.example.test")
        self.vault.keys.return_value = [old.credential_key]
        self.vault.get.return_value = {"kind": "bearer", "token": "private"}
        dialog = _VaultDialog(self.vault)
        try:
            dialog._entries.setCurrentItem(dialog._entries.topLevelItem(0))
            with patch("docker_stack_generator.monitoring_widget.QMessageBox.question",
                       return_value=QMessageBox.StandardButton.No):
                dialog._delete_entry()
            self.vault.delete.assert_not_called()
            with patch("docker_stack_generator.monitoring_widget.QMessageBox.question",
                       return_value=QMessageBox.StandardButton.Yes):
                dialog._delete_entry()
            self.vault.delete.assert_called_once_with(old.credential_key)
            self.assertEqual(dialog._secret.text(), "")
        finally:
            dialog.clear()
            dialog.deleteLater()

    def test_vault_edit_preserves_endpoint_key(self):
        from docker_stack_generator.monitoring_widget import _VaultDialog
        key = self.widget._targets[0].credential_key
        original = {"kind": "bearer", "token": "original"}
        updated = {"kind": "bearer", "token": "updated"}
        self.vault.keys.return_value = [key]
        self.vault.get.return_value = original
        dialog = _VaultDialog(self.vault)
        try:
            dialog._entries.setCurrentItem(dialog._entries.topLevelItem(0))
            with patch("docker_stack_generator.monitoring_widget._CredentialDialog") as editor_type:
                editor = editor_type.return_value
                editor.exec.return_value = True
                editor.value.return_value = updated
                dialog._edit_entry()
            editor.set_value.assert_called_once_with(original)
            self.vault.set.assert_called_once_with(key, updated)
            editor.clear.assert_called_once()
        finally:
            dialog.clear()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
