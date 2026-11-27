"""
Persistent Stack Library stored in ~/.config/docker-stack-generator/library/.
Each saved stack is stored as an individual JSON file keyed by
'<environment>__<stack_name>.json' so multiple environments can coexist.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from docker_stack_generator.generator import (
    PlacementConstraint,
    PortMapping,
    ServiceConfig,
    StackConfig,
    VolumeMount,
    slugify,
    stack_slugify,
)

_DEFAULT_LIBRARY_DIR = Path.home() / ".config" / "docker-stack-generator" / "library"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stack_key(environment: str, name: str) -> str:
    env_slug = slugify(environment) or "default"
    name_slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")
    return f"{env_slug}__{name_slug}"


@dataclass
class SavedStack:
    key: str
    config: StackConfig
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_generated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_generated_at": self.last_generated_at,
            "stack": {
                "name": self.config.name,
                "display_name": self.config.display_name,
                "environment": self.config.environment,
                "stacks_root": self.config.stacks_root,
                "description": self.config.description,
                "networks": list(self.config.networks),
                "network_vars": dict(self.config.network_vars),
                "services": [asdict(s) for s in self.config.services],
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> SavedStack:
        sdata = data["stack"]
        raw_services = sdata.get("services", [])
        services = []
        for svc in raw_services:
            # Backward-compat defaults for new fields
            svc.setdefault("runtime_dirs", [])
            svc.setdefault("networks", [])
            svc.setdefault("secrets", [])
            # ports: list of dicts → list of PortMapping
            raw_ports = svc.pop("ports", [])
            ports = [
                PortMapping(**p) if isinstance(p, dict) else p
                for p in raw_ports
            ]
            # volumes: list of dicts → list of VolumeMount
            raw_volumes = svc.pop("volumes", [])
            volumes = [
                VolumeMount(**v) if isinstance(v, dict) else v
                for v in raw_volumes
            ]
            # constraint: dict → PlacementConstraint
            raw_constraint = svc.pop("constraint", None)
            if isinstance(raw_constraint, dict):
                constraint = PlacementConstraint(**raw_constraint)
            else:
                constraint = PlacementConstraint(role="worker", label_key="")
            services.append(ServiceConfig(**svc, ports=ports, volumes=volumes, constraint=constraint))
        cfg = StackConfig(
            name=sdata["name"],
            display_name=sdata.get("display_name", sdata["name"]),
            environment=sdata.get("environment", "development"),
            stacks_root=sdata.get("stacks_root", ""),
            description=sdata.get("description", ""),
            services=services,
            networks=sdata.get("networks", ["traefik", "monitoring"]),
            network_vars=sdata.get(
                "network_vars",
                {"traefik": "NETWORK_TRAEFIK", "monitoring": "NETWORK_MONITORING"},
            ),
        )
        key = data.get("key") or stack_key(cfg.environment, cfg.name)
        return cls(
            key=key,
            config=cfg,
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            last_generated_at=data.get("last_generated_at"),
        )


class StackLibrary:
    """Manages saved stack definitions on disk."""

    def __init__(self, library_dir: Optional[Path] = None) -> None:
        self._dir = library_dir or _DEFAULT_LIBRARY_DIR

    @property
    def library_dir(self) -> Path:
        return self._dir

    def _file_for(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def list_stacks(self) -> list[SavedStack]:
        if not self._dir.exists():
            return []
        entries: list[SavedStack] = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append(SavedStack.from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    def load_stack(self, key: str) -> Optional[SavedStack]:
        path = self._file_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SavedStack.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def save_stack(self, cfg: StackConfig, generated: bool = False) -> SavedStack:
        self._dir.mkdir(parents=True, exist_ok=True)
        key = stack_key(cfg.environment, cfg.name)
        existing = self.load_stack(key)
        now = _now_iso()

        created_at = existing.created_at if existing else now
        last_generated_at = now if generated else (existing.last_generated_at if existing else None)

        saved = SavedStack(
            key=key,
            config=cfg,
            created_at=created_at,
            updated_at=now,
            last_generated_at=last_generated_at,
        )
        self._file_for(key).write_text(
            json.dumps(saved.to_dict(), indent=2),
            encoding="utf-8",
        )
        return saved

    def rename_stack(self, key: str, cfg: StackConfig) -> Optional[SavedStack]:
        """Save a renamed stack under its new key and preserve its history."""
        existing = self.load_stack(key)
        if existing is None:
            return None

        new_key = stack_key(cfg.environment, cfg.name)
        if new_key != key and self._file_for(new_key).exists():
            return None

        saved = SavedStack(
            key=new_key,
            config=cfg,
            created_at=existing.created_at,
            updated_at=_now_iso(),
            last_generated_at=existing.last_generated_at,
        )
        self._file_for(new_key).write_text(
            json.dumps(saved.to_dict(), indent=2),
            encoding="utf-8",
        )
        if new_key != key:
            self._file_for(key).unlink()
        return saved

    def delete_stack(self, key: str) -> bool:
        path = self._file_for(key)
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Backup / Restore
    # ------------------------------------------------------------------

    def backup(self, dest_path: Path) -> Path:
        """
        Create a ZIP archive of all saved stack JSON files at *dest_path*
        (which should end in .zip).  Returns the path written.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        dest_path = Path(dest_path)
        with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for json_file in sorted(self._dir.glob("*.json")):
                zf.write(json_file, arcname=json_file.name)
        return dest_path

    def restore(self, src_path: Path, merge: bool = False) -> int:
        """
        Restore stacks from a ZIP archive created by :meth:`backup`.

        Parameters
        ----------
        src_path : Path
            Path to the .zip backup file.
        merge : bool
            If True, existing stacks are kept and only new/updated ones from
            the archive are written.  If False (default), the library is
            cleared first.

        Returns
        -------
        int
            Number of stacks restored.
        """
        src_path = Path(src_path)
        self._dir.mkdir(parents=True, exist_ok=True)

        if not merge:
            for f in self._dir.glob("*.json"):
                f.unlink()

        count = 0
        with zipfile.ZipFile(src_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".json"):
                    continue
                data = json.loads(zf.read(name).decode("utf-8"))
                # Validate minimal structure
                if "stack" not in data:
                    continue
                (self._dir / name).write_text(
                    json.dumps(data, indent=2), encoding="utf-8"
                )
                count += 1
        return count

    # ------------------------------------------------------------------
    # CSV Export
    # ------------------------------------------------------------------

    def export_csv(self, dest_path: Optional[Path] = None) -> str:
        """
        Export the entire library as a CSV string (or write to *dest_path*).

        Each row represents one **service** within a stack.  Columns are:

        stack_key, stack_name, display_name, environment, stacks_root,
        description, created_at, updated_at, last_generated_at,
        service_name, service_display_name, image, image_base, image_tag,
        port, port_source, restart_profile, resource_profile, runtime_dirs
        """
        import csv
        import io

        fieldnames = [
            "stack_key",
            "stack_name",
            "display_name",
            "environment",
            "stacks_root",
            "description",
            "created_at",
            "updated_at",
            "last_generated_at",
            "service_name",
            "service_display_name",
            "image",
            "image_base",
            "image_tag",
            "port",
            "port_source",
            "restart_profile",
            "resource_profile",
            "runtime_dirs",
        ]

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()

        for entry in self.list_stacks():
            cfg = entry.config
            base = {
                "stack_key": entry.key,
                "stack_name": cfg.name,
                "display_name": cfg.display_name,
                "environment": cfg.environment,
                "stacks_root": cfg.stacks_root,
                "description": cfg.description,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "last_generated_at": entry.last_generated_at or "",
            }
            if cfg.services:
                for svc in cfg.services:
                    row = dict(base)
                    row.update(
                        {
                            "service_name": svc.name,
                            "service_display_name": svc.display_name,
                            "image": svc.image,
                            "image_base": svc.image_base,
                            "image_tag": svc.image_tag,
                            "port": svc.port,
                            "port_source": svc.port_source,
                            "restart_profile": svc.restart_profile,
                            "resource_profile": svc.resource_profile,
                            "runtime_dirs": "; ".join(svc.runtime_dirs),
                        }
                    )
                    writer.writerow(row)
            else:
                # Stack with no services — still emit a row
                row = dict(base)
                for col in fieldnames[len(base):]:
                    row[col] = ""
                writer.writerow(row)

        csv_text = buf.getvalue()

        if dest_path is not None:
            Path(dest_path).write_text(csv_text, encoding="utf-8")

        return csv_text

    def duplicate_stack(
        self,
        key: str,
        new_display_name: str,
        new_environment: Optional[str] = None,
    ) -> Optional[SavedStack]:
        existing = self.load_stack(key)
        if not existing:
            return None
        new_name = stack_slugify(new_display_name)
        if not new_name:
            return None
        env = (new_environment or existing.config.environment).strip() or "development"
        new_cfg = StackConfig(
            name=new_name,
            display_name=new_display_name.strip(),
            environment=env,
            stacks_root=existing.config.stacks_root,
            description=existing.config.description,
            services=[ServiceConfig(**asdict(s)) for s in existing.config.services],
            networks=list(existing.config.networks),
            network_vars=dict(existing.config.network_vars),
        )
        return self.save_stack(new_cfg, generated=False)


# Singleton instance
stack_library = StackLibrary()

