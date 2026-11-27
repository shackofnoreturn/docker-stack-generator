"""Install a per-user desktop launcher for the application on Omarchy/Linux."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


APP_ID = "docker-stack-generator"


def _desktop_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )


def _desktop_escape_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def install_shortcut(workspace: Path, data_home: Path) -> Path:
    workspace = workspace.resolve()
    icon_source = workspace / "assets" / f"{APP_ID}.svg"
    icon_path = data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    desktop_path = data_home / "applications" / f"{APP_ID}.desktop"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon_source, icon_path)

    executable = _desktop_escape(sys.executable)
    application = _desktop_escape(str(workspace / "run.py"))
    working_directory = _desktop_escape_value(str(workspace))
    desktop_entry = "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=Docker Stack Generator",
            "Comment=Create Docker Swarm stack files",
            f'Exec="{executable}" "{application}"',
            f"Path={working_directory}",
            f"Icon={APP_ID}",
            "Terminal=false",
            "Categories=Development;Utility;",
        ]
    )
    desktop_path.write_text(desktop_entry + "\n\n", encoding="utf-8")
    return desktop_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Application checkout directory")
    args = parser.parse_args()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    desktop_path = install_shortcut(args.workspace, data_home)
    print(f"Installed application shortcut: {desktop_path}")


if __name__ == "__main__":
    main()

