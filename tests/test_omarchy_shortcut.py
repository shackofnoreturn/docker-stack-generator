import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT_ROOT / "scripts" / "install_omarchy_shortcut.py"
ICON_NAME = "docker-stack-generator.svg"


def desktop_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )


def desktop_escape_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


class OmarchyShortcutTests(unittest.TestCase):
    def test_installs_desktop_entry_and_icon_in_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            workspace = temporary_root / "checkout with spaces"
            (workspace / "assets").mkdir(parents=True)
            (workspace / "run.py").write_text("", encoding="utf-8")
            icon_source = workspace / "assets" / ICON_NAME
            icon_source.write_bytes((PROJECT_ROOT / "assets" / ICON_NAME).read_bytes())
            data_home = temporary_root / "xdg data"
            environment = os.environ.copy()
            environment["XDG_DATA_HOME"] = str(data_home)

            result = subprocess.run(
                [sys.executable, str(INSTALLER), str(workspace)],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            desktop_file = data_home / "applications" / "docker-stack-generator.desktop"
            installed_icon = (
                data_home
                / "icons"
                / "hicolor"
                / "scalable"
                / "apps"
                / ICON_NAME
            )
            contents = desktop_file.read_text(encoding="utf-8")
            expected_executable = desktop_escape(sys.executable)
            expected_application = desktop_escape(str((workspace / "run.py").resolve()))
            expected_workspace = desktop_escape_value(str(workspace.resolve()))

            self.assertIn("Installed application shortcut:", result.stdout)
            self.assertIn("Type=Application", contents)
            self.assertIn('Name=Docker Stack Generator', contents)
            self.assertIn(f'Exec="{expected_executable}" "{expected_application}"', contents)
            self.assertIn(f"Path={expected_workspace}", contents)
            self.assertIn("Icon=docker-stack-generator", contents)
            self.assertIn("Terminal=false", contents)
            self.assertEqual(installed_icon.read_bytes(), icon_source.read_bytes())


if __name__ == "__main__":
    unittest.main()

