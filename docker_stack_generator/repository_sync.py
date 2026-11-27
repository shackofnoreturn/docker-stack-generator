"""Commit generated files and push to the destination branch's upstream."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from docker_stack_generator.generator import StackConfig


class RepositorySyncError(Exception):
    """A destination repository could not be committed or pushed."""


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable:
        return executable
    if os.name == "nt":
        for directory in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git",
        ):
            candidate = directory / "cmd" / "git.exe"
            if candidate.is_file():
                return str(candidate)
    raise RepositorySyncError("Git was not found. Install Git or add it to PATH.")


def _run_git(executable: str, directory: Path, *args: str) -> str:
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_LITERAL_PATHSPECS": "1",
    }
    try:
        result = subprocess.run(
            [executable, "-C", str(directory), *args],
            capture_output=True, encoding="utf-8", errors="replace",
            env=environment, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositorySyncError(f"Git {args[0]} could not complete: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RepositorySyncError(f"Git {args[0]} failed:\n{detail}")
    return result.stdout.rstrip("\r\n")


@dataclass(frozen=True)
class RepositorySync:
    executable: str
    root: Path
    branch: str
    remote: str
    remote_ref: str

    def git(self, *args: str) -> str:
        return _run_git(self.executable, self.root, *args)

    @classmethod
    def prepare(cls, cfg: StackConfig) -> RepositorySync:
        executable = _git_executable()
        directory = Path(cfg.stacks_root).resolve()
        while not directory.exists():
            directory = directory.parent
        root = Path(_run_git(executable, directory, "rev-parse", "--show-toplevel")).resolve()
        for target in (cfg.stack_dir, cfg.templates_dir):
            if not target.resolve().is_relative_to(root):
                raise RepositorySyncError(f"Output directory is outside the repository: {target}")
        branch = _run_git(executable, root, "symbolic-ref", "--quiet", "--short", "HEAD")
        try:
            remote = _run_git(executable, root, "config", "--get", f"branch.{branch}.remote")
            remote_ref = _run_git(executable, root, "config", "--get", f"branch.{branch}.merge")
            _run_git(executable, root, "rev-parse", "--verify", "@{upstream}")
        except RepositorySyncError as exc:
            raise RepositorySyncError(
                f"Configure an existing remote upstream for destination branch '{branch}' first.\n\n{exc}"
            ) from exc
        if remote == "." or not remote_ref.startswith("refs/heads/"):
            raise RepositorySyncError("The destination branch must track a remote branch.")
        if _run_git(executable, root, "ls-files", "--unmerged"):
            raise RepositorySyncError("Resolve the destination repository's merge conflicts first.")
        for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            state_path = Path(_run_git(executable, root, "rev-parse", "--git-path", state))
            if not state_path.is_absolute():
                state_path = root / state_path
            if state_path.exists():
                raise RepositorySyncError("Finish the destination repository's Git operation first.")
        _run_git(executable, root, "var", "GIT_AUTHOR_IDENT")
        _run_git(executable, root, "var", "GIT_COMMITTER_IDENT")
        return cls(executable, root, branch, remote, remote_ref)

    def sync(self, cfg: StackConfig, generated: list[str]) -> str:
        if self.git("symbolic-ref", "--quiet", "--short", "HEAD") != self.branch:
            raise RepositorySyncError("The destination branch changed during generation; sync was cancelled.")
        paths = []
        for filename in generated:
            path = Path(filename).resolve()
            if not path.is_relative_to(self.root):
                raise RepositorySyncError(f"Generated file is outside the repository: {filename}")
            owner = Path(_run_git(self.executable, path.parent, "rev-parse", "--show-toplevel")).resolve()
            if owner != self.root:
                raise RepositorySyncError(f"Generated file belongs to a nested repository: {filename}")
            paths.append(path.relative_to(self.root).as_posix())
        paths = sorted(set(paths))
        # Respect ignore rules, but retain tracked files even if they now match an ignore rule.
        eligible = (
            self.git("ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *paths)
            if paths else ""
        )
        paths = sorted(set(filter(None, eligible.split("\0")))) if paths else []
        committed = False
        if paths:
            self.git("add", "--", *paths)
            changed = self.git("diff", "--cached", "--name-only", "-z", "--", *paths)
            if changed:
                message = (
                    f"Docker Stack Generator: generate stack {cfg.environment}/{cfg.name}\n\n"
                    f"Generated by Docker Stack Generator for stack '{cfg.display_name}' "
                    f"({cfg.environment}/{cfg.name})."
                )
                self.git("commit", "--only", "-m", message, "--", *paths)
                committed = True
        try:
            self.git("push", "--", self.remote, f"HEAD:{self.remote_ref}")
        except RepositorySyncError as exc:
            raise RepositorySyncError(
                f"{exc}\n\nGenerated files remain on disk. Any local commit is retained. "
                "Resolve the push problem, then retry Generate & Sync or push manually. "
                "No pull, merge, or force-push was attempted."
            ) from exc
        action = "Committed and pushed" if committed else "No generated changes to commit; pushed"
        return f"{action} to {self.remote}/{self.remote_ref.removeprefix('refs/heads/')}."
