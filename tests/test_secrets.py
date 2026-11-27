import base64
import importlib.util
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from docker_stack_generator.credential_vault import CredentialVault, VaultError
from docker_stack_generator.generator import GenerationError, StackConfig, generate_stack
from docker_stack_generator.stack_library import SavedStack
from test_environment import make_service


CIPHERTEXT = b"age-encryption.org/v1\nmock ciphertext\n"


class EncryptedSecretGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cfg = StackConfig("demo", "Demo", "production", self.temporary.name,
                               services=[make_service(secrets=["api_token"])])

    def test_missing_empty_and_plaintext_payloads_fail_without_creating_stack(self):
        for value in (None, {}, {"api_token": b""}, {"api_token": b"private-plaintext"}):
            with self.subTest(value=value), self.assertRaisesRegex(GenerationError, "Encrypted value missing"):
                generate_stack(self.cfg, encrypted_secrets=value)
            self.assertFalse(self.cfg.stack_dir.exists())

    def test_only_age_files_generated_and_duplicate_names_write_once(self):
        self.cfg.services.append(make_service(secrets=["api_token"]))
        created = generate_stack(self.cfg, encrypted_secrets={"api_token": CIPHERTEXT})
        directory = self.cfg.stack_dir / "secrets"
        self.assertEqual([path.name for path in directory.iterdir()], ["api_token.age"])
        self.assertEqual((directory / "api_token.age").read_bytes(), CIPHERTEXT)
        self.assertEqual(created.count(str(directory / "api_token.age")), 1)

    def test_legacy_plaintext_removed_only_after_ciphertext_written(self):
        directory = self.cfg.stack_dir / "secrets"
        directory.mkdir(parents=True)
        for name in ("api_token", "api_token.txt", ".gitkeep"):
            (directory / name).write_bytes(b"old")
        with patch("docker_stack_generator.generator.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(GenerationError):
                generate_stack(self.cfg, overwrite=True, encrypted_secrets={"api_token": CIPHERTEXT})
        self.assertTrue((directory / "api_token").exists())
        self.assertTrue((directory / "api_token.txt").exists())
        created = generate_stack(self.cfg, overwrite=True, encrypted_secrets={"api_token": CIPHERTEXT})
        self.assertEqual([path.name for path in directory.iterdir()], ["api_token.age"])
        for name in ("api_token", "api_token.txt", ".gitkeep"):
            self.assertIn(str(directory / name), created)

    def test_rejects_path_traversal_and_existing_directory_conflicts(self):
        for name in ("../escape", "/absolute", "C:\\escape", "token.txt", "token.age", "."):
            self.cfg.services[0].secrets = [name]
            with self.subTest(name=name), self.assertRaises((ValueError, GenerationError)):
                generate_stack(self.cfg, encrypted_secrets={name: CIPHERTEXT})
        self.assertFalse(self.cfg.stack_dir.exists())
        self.cfg.services[0].secrets = ["api_token"]
        (self.cfg.stack_dir / "secrets" / "api_token.age").mkdir(parents=True)
        with self.assertRaisesRegex(GenerationError, "regular file"):
            generate_stack(self.cfg, overwrite=True, encrypted_secrets={"api_token": CIPHERTEXT})

    def test_secret_values_never_appear_in_saved_definition(self):
        data = SavedStack("production__demo", self.cfg).to_dict()
        self.assertEqual(data["stack"]["services"][0]["secrets"], ["api_token"])
        self.assertNotIn("contents", json.dumps(data))


class SecretStoreTests(unittest.TestCase):
    def test_global_vault_values_roundtrip_binary_and_never_appear_on_disk(self):
        from docker_stack_generator.age_secrets import secret_key
        from docker_stack_generator.secret_store import read_secret
        with TemporaryDirectory() as root:
            vault = CredentialVault(Path(root) / "credentials.vault")
            vault.unlock("a very long master password")
            secret = b"unique-private-value\x00\xff\r\n"
            vault.set(secret_key("api_token"), {
                "kind": "secret", "contents": base64.b64encode(secret).decode("ascii"),
            })
            self.assertEqual(read_secret(vault, "api_token"), secret)
            self.assertNotIn(secret, vault.path.read_bytes())
            vault.lock()
            with self.assertRaises(VaultError):
                read_secret(vault, "api_token")
            vault.unlock("a very long master password")
            self.assertEqual(read_secret(vault, "api_token"), secret)

    def test_missing_empty_and_invalid_values_are_explicit(self):
        from docker_stack_generator.secret_store import read_secret
        vault = Mock()
        for value in (None, {"kind": "secret", "contents": ""},
                      {"kind": "secret", "contents": "!"}, {"kind": "basic"}):
            vault.get.return_value = value
            with self.subTest(value=value), self.assertRaises(VaultError):
                read_secret(vault, "api_token")


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class SecretUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_secret_editor_preserves_imported_bytes_until_edited(self):
        from docker_stack_generator.secret_widgets import SecretValueDialog
        for contents in (b"secret\r\n", b"\x00\xff"):
            dialog = SecretValueDialog("api_token", contents)
            try:
                self.assertEqual(dialog.contents(), contents)
                self.assertFalse(dialog._show.isChecked())
                dialog.clear()
                self.assertEqual(dialog.contents(), b"")
            finally:
                dialog.deleteLater()

    def test_worker_encrypts_before_generating_and_clears_plaintext(self):
        from docker_stack_generator.widgets import GenerateWorker
        worker = GenerateWorker(Mock(), False, secret_values={"api_token": b"private"}, age_recipients="key.pub")
        with patch("docker_stack_generator.age_secrets.encrypt_secret", return_value=CIPHERTEXT) as encrypt, \
                patch("docker_stack_generator.generator.generate_stack", return_value=[]) as generate:
            worker.run()
        encrypt.assert_called_once_with(b"private", "key.pub")
        generate.assert_called_once_with(worker.cfg, overwrite=False, encrypted_secrets={"api_token": CIPHERTEXT})
        self.assertEqual(worker.secret_values, {})

    def test_encryption_failure_never_generates_or_syncs(self):
        from docker_stack_generator.age_secrets import AgeError
        from docker_stack_generator.widgets import GenerateWorker
        worker = GenerateWorker(Mock(), False, sync=True, secret_values={"api_token": b"private"})
        errors = Mock()
        worker.error.connect(errors)
        with patch("docker_stack_generator.repository_sync.RepositorySync.prepare") as prepare, \
                patch("docker_stack_generator.age_secrets.encrypt_secret", side_effect=AgeError("Encryption failed")), \
                patch("docker_stack_generator.generator.generate_stack") as generate:
            worker.run()
        generate.assert_not_called()
        prepare.return_value.sync.assert_not_called()
        errors.assert_called_once_with("Encryption failed")
        self.assertEqual(worker.secret_values, {})

    def test_edit_secret_saves_only_to_vault_and_preserves_binary(self):
        from docker_stack_generator.age_secrets import secret_key
        from docker_stack_generator.secret_widgets import edit_secret
        vault = Mock()
        vault.unlocked = True
        vault.get.return_value = None
        contents = b"private\x00\xff\r\n"
        with patch("docker_stack_generator.secret_widgets.SecretValueDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = True
            dialog.contents.return_value = contents
            self.assertTrue(edit_secret(vault, "api_token"))
        vault.set.assert_called_once_with(secret_key("api_token"), {
            "kind": "secret", "contents": base64.b64encode(contents).decode("ascii"),
        })
        dialog.clear.assert_called_once()

    def test_import_age_uses_configured_identity_without_plaintext_files(self):
        from docker_stack_generator.secret_widgets import SecretValueDialog
        with TemporaryDirectory() as root:
            path = Path(root) / "existing.age"
            path.write_bytes(CIPHERTEXT)
            dialog = SecretValueDialog("api_token")
            try:
                with patch("docker_stack_generator.secret_widgets.QFileDialog.getOpenFileName",
                           return_value=(str(path), "")), \
                        patch("docker_stack_generator.secret_widgets.settings") as settings, \
                        patch("docker_stack_generator.secret_widgets.decrypt_secret",
                              return_value=b"private\r\n") as decrypt:
                    settings.get.return_value = "identity.pem"
                    dialog._import()
                decrypt.assert_called_once_with(CIPHERTEXT, "identity.pem")
                self.assertEqual(dialog.contents(), b"private\r\n")
                self.assertEqual([entry.name for entry in Path(root).iterdir()], ["existing.age"])
            finally:
                dialog.clear()
                dialog.deleteLater()

    def test_main_generation_unlocks_vault_and_passes_secret_values(self):
        from PyQt6.QtWidgets import QMainWindow
        from docker_stack_generator.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window._generate_worker = None
        window._monitoring_widget = Mock()
        window._monitoring_widget._unlock_vault.return_value = True
        window._generate_btn = Mock()
        window._stack_library_widget = Mock()
        window._status = Mock()
        try:
            with TemporaryDirectory() as root:
                cfg = StackConfig("demo", "Demo", "production", root,
                                  services=[make_service(secrets=["api_token"])])
                with patch("docker_stack_generator.main_window.read_secret", return_value=b"private"), \
                        patch("docker_stack_generator.main_window.settings") as settings, \
                        patch("docker_stack_generator.main_window.GenerateWorker") as worker:
                    settings.get.return_value = "recipients.pub"
                    window._start_generation(cfg, False, True)
                self.assertEqual(worker.call_args.kwargs["secret_values"], {"api_token": b"private"})
                self.assertEqual(worker.call_args.kwargs["age_recipients"], "recipients.pub")
                self.assertTrue(worker.call_args.kwargs["sync"])
                worker.return_value.start.assert_called_once()
        finally:
            window.deleteLater()

    def test_age_settings_load_and_save_paths_not_key_contents(self):
        from docker_stack_generator.settings_dialog import SettingsDialog
        defaults = {"age_recipients": "public-keys.pub", "age_identity": "private-key.pem"}
        with patch("docker_stack_generator.settings_dialog.settings") as settings:
            settings.get.side_effect = lambda name, default=None: defaults.get(name, default)
            settings.stacks_root = ""
            settings.stacks_env = "development"
            dialog = SettingsDialog()
            try:
                self.assertEqual(dialog._age_recipients.text(), "public-keys.pub")
                self.assertEqual(dialog._age_identity.text(), "private-key.pem")
                dialog._age_recipients.setText("new-public.pub")
                dialog._on_accept()
                saved = settings.update.call_args.args[0]
                self.assertEqual(saved["age_recipients"], "new-public.pub")
                self.assertEqual(saved["age_identity"], "private-key.pem")
            finally:
                dialog.deleteLater()

    def test_missing_vault_value_blocks_worker_without_generating(self):
        from PyQt6.QtWidgets import QMainWindow
        from docker_stack_generator.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window._generate_worker = None
        window._monitoring_widget = Mock()
        window._monitoring_widget._unlock_vault.return_value = True
        try:
            with TemporaryDirectory() as root:
                cfg = StackConfig("demo", "Demo", "production", root,
                                  services=[make_service(secrets=["api_token"])])
                with patch("docker_stack_generator.main_window.read_secret",
                           side_effect=VaultError("No value stored")), \
                        patch("docker_stack_generator.main_window.QMessageBox.warning") as warning, \
                        patch("docker_stack_generator.main_window.GenerateWorker") as worker:
                    window._start_generation(cfg, False, True)
                warning.assert_called_once()
                worker.assert_not_called()
                self.assertFalse(cfg.stack_dir.exists())
        finally:
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
