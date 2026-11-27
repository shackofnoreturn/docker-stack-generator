import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docker_stack_generator.generator import StackConfig, generate_stack
from docker_stack_generator.repository_sync import (
    RepositorySync, RepositorySyncError, _git_executable, _run_git,
)
from test_environment import make_service


class RepositorySyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "destination repo"
        self.root.mkdir()
        self.remote = self.base / "remote.git"
        self.executable = _git_executable()
        self.git("init", "--bare", str(self.remote))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Generator Test")
        self.git("config", "user.email", "generator@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "push.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.hooksPath", str(self.base / "no-hooks"))
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git("add", "seed.txt")
        self.git("commit", "-m", "Seed repository")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")
        self.cfg = StackConfig(
            "redis", "Redis Cache", "production", str(self.root / "stacks"),
            services=[make_service(secrets=["password"])],
        )

    def git(self, *args):
        return _run_git(self.executable, self.root, *args)

    def generate_and_sync(self, overwrite=False):
        repository = RepositorySync.prepare(self.cfg)
        created = generate_stack(self.cfg, overwrite=overwrite)
        return repository.sync(self.cfg, created)

    def remote_head(self):
        return self.git("ls-remote", "origin", "refs/heads/main").split()[0]

    def test_commits_generated_files_and_templates_then_pushes(self):
        result = self.generate_and_sync()
        self.assertIn("Committed and pushed", result)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.remote_head())
        self.assertIn("Docker Stack Generator: generate stack production/redis",
                      self.git("log", "-1", "--format=%B"))
        self.assertIn("Redis Cache", self.git("log", "-1", "--format=%B"))
        committed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
        for expected in (
            "stacks/production/redis/stack.yml",
            "stacks/production/redis/compose.yml.j2",
            "stacks/production/redis/README.md",
            "stacks/production/redis/configs/.gitkeep",
            "stacks/production/redis/secrets/.gitkeep",
            "stacks/production/redis/secrets/password.txt",
            "stacks/templates/common/logging.yml.j2",
        ):
            self.assertIn(expected, committed)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_unrelated_staged_unstaged_and_untracked_files_are_untouched(self):
        (self.root / "seed.txt").write_text("staged\n", encoding="utf-8")
        self.git("add", "seed.txt")
        staged_blob = self.git("rev-parse", ":seed.txt")
        (self.root / "seed.txt").write_text("unstaged\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("untracked", encoding="utf-8")
        self.cfg.stack_dir.mkdir(parents=True)
        unrelated = self.cfg.stack_dir / "custom.txt"
        unrelated.write_text("custom", encoding="utf-8")
        self.generate_and_sync(overwrite=True)
        self.assertEqual(self.git("rev-parse", ":seed.txt"), staged_blob)
        self.assertEqual((self.root / "seed.txt").read_text(encoding="utf-8"), "unstaged\n")
        self.assertEqual(self.git("show", "HEAD:seed.txt"), "seed")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "seed.txt")
        self.assertIn("?? untracked.txt", self.git("status", "--porcelain"))
        self.assertIn("custom.txt", self.git("ls-files", "--others", "--exclude-standard"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "custom")

    def test_ignored_new_files_are_not_added_but_tracked_ignored_files_update(self):
        self.generate_and_sync()
        (self.root / ".gitignore").write_text("*.txt\nstack.yml\n", encoding="utf-8")
        self.cfg.description = "Updated description"
        self.cfg.services[0].port = "6380"
        self.cfg.services[0].secrets.append("new_secret")
        self.generate_and_sync(overwrite=True)
        tracked = self.git("ls-tree", "-r", "--name-only", "HEAD")
        self.assertIn("stacks/production/redis/stack.yml", tracked)
        self.assertNotIn("new_secret.txt", tracked)
        self.assertNotIn(".gitignore", tracked)
        self.assertIn("REDIS_PORT: 6380", self.git("show", "HEAD:stacks/production/redis/stack.yml"))

    def test_no_changes_creates_no_empty_commit_and_still_pushes(self):
        self.generate_and_sync()
        head = self.git("rev-parse", "HEAD")
        result = self.generate_and_sync(overwrite=True)
        self.assertIn("No generated changes to commit; pushed", result)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(head, self.remote_head())

    def test_no_generated_changes_still_pushes_pending_commits(self):
        self.generate_and_sync()
        (self.root / "seed.txt").write_text("local pending change\n", encoding="utf-8")
        self.git("add", "seed.txt")
        self.git("commit", "-m", "Earlier local commit")
        pending_head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(pending_head, self.remote_head())
        self.generate_and_sync(overwrite=True)
        self.assertEqual(pending_head, self.git("rev-parse", "HEAD"))
        self.assertEqual(pending_head, self.remote_head())

    def test_existing_secrets_and_shared_templates_are_not_committed(self):
        generate_stack(self.cfg)
        secret = self.cfg.stack_dir / "secrets" / "password.txt"
        secret.write_text("existing-private-value", encoding="utf-8")
        template = self.cfg.templates_dir / "common" / "logging.yml.j2"
        template.write_text("custom existing template", encoding="utf-8")
        self.generate_and_sync(overwrite=True)
        tracked = self.git("ls-tree", "-r", "--name-only", "HEAD")
        self.assertNotIn("secrets/password.txt", tracked)
        self.assertNotIn("templates/common/logging.yml.j2", tracked)
        self.assertEqual(secret.read_text(encoding="utf-8"), "existing-private-value")
        self.assertEqual(template.read_text(encoding="utf-8"), "custom existing template")

    def test_missing_upstream_and_detached_head_fail_before_generation(self):
        self.git("branch", "--unset-upstream")
        with self.assertRaisesRegex(RepositorySyncError, "upstream"):
            RepositorySync.prepare(self.cfg)
        self.assertFalse(self.cfg.stack_dir.exists())
        self.git("checkout", "--detach")
        with self.assertRaises(RepositorySyncError):
            RepositorySync.prepare(self.cfg)

    def test_non_repository_and_git_operation_are_rejected(self):
        cfg = StackConfig("redis", "Redis", "production", str(self.base / "not-a-repo"))
        with self.assertRaises(RepositorySyncError):
            RepositorySync.prepare(cfg)
        merge_head = self.root / ".git" / "MERGE_HEAD"
        merge_head.write_text(self.git("rev-parse", "HEAD"), encoding="utf-8")
        with self.assertRaisesRegex(RepositorySyncError, "Git operation"):
            RepositorySync.prepare(self.cfg)
        self.assertFalse(self.cfg.stack_dir.exists())

    def test_branch_change_is_rejected(self):
        repository = RepositorySync.prepare(self.cfg)
        created = generate_stack(self.cfg)
        self.git("switch", "-c", "different")
        with self.assertRaisesRegex(RepositorySyncError, "branch changed"):
            repository.sync(self.cfg, created)

    def test_nested_repository_files_are_not_staged(self):
        self.cfg.stack_dir.mkdir(parents=True)
        self.git("init", str(self.cfg.stack_dir))
        repository = RepositorySync.prepare(self.cfg)
        created = generate_stack(self.cfg, overwrite=True)
        with self.assertRaisesRegex(RepositorySyncError, "nested repository"):
            repository.sync(self.cfg, created)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_rejected_push_retains_local_commit_and_can_be_retried(self):
        self.git("config", "remote.origin.pushurl", str(self.base / "missing-remote.git"))
        with self.assertRaisesRegex(RepositorySyncError, "local commit is retained"):
            self.generate_and_sync()
        local_head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(local_head, self.remote_head())
        self.assertTrue((self.cfg.stack_dir / "stack.yml").is_file())
        self.git("config", "--unset", "remote.origin.pushurl")
        self.generate_and_sync(overwrite=True)
        self.assertEqual(local_head, self.git("rev-parse", "HEAD"))
        self.assertEqual(local_head, self.remote_head())

    def test_non_fast_forward_does_not_pull_or_force_push(self):
        repository = RepositorySync.prepare(self.cfg)
        created = generate_stack(self.cfg)
        original = self.git("rev-parse", "HEAD")
        tree = self.git("rev-parse", "HEAD^{tree}")
        competing = self.git("commit-tree", tree, "-p", original, "-m", "Competing remote commit")
        self.git("push", "origin", f"{competing}:refs/heads/main")
        with self.assertRaisesRegex(RepositorySyncError, "No pull, merge, or force-push"):
            repository.sync(self.cfg, created)
        self.assertEqual(self.remote_head(), competing)
        self.assertEqual(self.git("rev-parse", "HEAD^"), original)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), competing)


class GitCommandTests(unittest.TestCase):
    def test_missing_git_is_actionable(self):
        with patch("shutil.which", return_value=None), patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(RepositorySyncError, "Install Git"):
                _git_executable()

    def test_timeout_and_process_errors_are_explicit(self):
        for error in (subprocess.TimeoutExpired("git", 120), OSError("not available")):
            with self.subTest(error=error), patch("subprocess.run", side_effect=error):
                with self.assertRaises(RepositorySyncError):
                    _run_git("git", Path.cwd(), "push")


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class GenerateSyncUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_generation_only_does_not_call_git(self):
        from docker_stack_generator.widgets import GenerateWorker
        worker = GenerateWorker(Mock(), False)
        done = Mock()
        worker.done.connect(done)
        with patch("docker_stack_generator.generator.generate_stack", return_value=["stack.yml"]), \
                patch.object(RepositorySync, "prepare") as prepare:
            worker.run()
        prepare.assert_not_called()
        done.assert_called_once_with(["stack.yml"])

    def test_worker_sync_failure_preserves_generation_success(self):
        from docker_stack_generator.widgets import GenerateWorker
        worker = GenerateWorker(Mock(), False, sync=True)
        done, error = Mock(), Mock()
        worker.done.connect(done)
        worker.error.connect(error)
        with patch("docker_stack_generator.generator.generate_stack", return_value=["stack.yml"]), \
                patch.object(RepositorySync, "prepare") as prepare:
            prepare.return_value.sync.side_effect = RepositorySyncError("push rejected")
            worker.run()
        done.assert_called_once_with(["stack.yml"])
        error.assert_not_called()
        self.assertEqual(worker.sync_error, "push rejected")

    def test_worker_preflight_failure_does_not_generate(self):
        from docker_stack_generator.widgets import GenerateWorker
        worker = GenerateWorker(Mock(), False, sync=True)
        error = Mock()
        worker.error.connect(error)
        with patch("docker_stack_generator.generator.generate_stack") as generate, \
                patch.object(RepositorySync, "prepare", side_effect=RepositorySyncError("no upstream")):
            worker.run()
        generate.assert_not_called()
        error.assert_called_once_with("no upstream")

    def test_split_button_routes_default_and_sync_actions(self):
        from PyQt6.QtWidgets import QMainWindow, QToolButton
        from docker_stack_generator.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window._services = []
        with patch.object(window, "_generate") as generate:
            tab = window._build_stack_tab()
            self.assertEqual(window._generate_btn.popupMode(),
                             QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            window._generate_btn.click()
            generate.assert_called_once_with()
            generate.reset_mock()
            window._generate_sync_action.trigger()
            generate.assert_called_once_with(sync=True)
            self.assertEqual(len(window._generate_btn.menu().actions()), 2)
            tab.deleteLater()
        window.deleteLater()

    def test_completion_uses_worker_config_and_reports_partial_failure(self):
        from docker_stack_generator.main_window import MainWindow
        cfg = StackConfig("original", "Original", "production", "destination")
        worker = SimpleNamespace(cfg=cfg, sync=True, sync_error="push rejected", sync_result="")
        window = SimpleNamespace(
            _generate_worker=worker, _generate_btn=Mock(), _refresh_recent=Mock(),
            _stack_library_widget=Mock(), _status=Mock(),
        )
        with patch("docker_stack_generator.main_window.settings"), \
                patch("docker_stack_generator.main_window.stack_library") as library, \
                patch("docker_stack_generator.main_window.QMessageBox.warning") as warning:
            MainWindow._on_generate_done(window, ["stack.yml"])
        library.save_stack.assert_called_once_with(cfg, generated=True)
        self.assertIn("push rejected", warning.call_args.args[2])
        self.assertIn("original", window._status.showMessage.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
