"""
Persistent Stack Library stored in ~/.config/docker-stack-generator/library/.
Each saved stack is stored as an individual JSON file keyed by
'<environment>__<stack_name>.json' so multiple environments can coexist.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from docker_stack_generator.generator import ServiceConfig, StackConfig, slugify

_DEFAULT_LIBRARY_DIR = Path.home() / ".config" / "docker-stack-generator" / "library"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stack_key(environment: str, name: str) -> str:
    env_slug = slugify(environment) or "default"
    name_slug = slugify(name)
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
                "networks": list(self.config.networks),
                "network_vars": dict(self.config.network_vars),
                "services": [asdict(s) for s in self.config.services],
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> SavedStack:
        sdata = data["stack"]
        services = [ServiceConfig(**svc) for svc in sdata.get("services", [])]
        cfg = StackConfig(
            name=sdata["name"],
            display_name=sdata.get("display_name", sdata["name"]),
            environment=sdata.get("environment", "development"),
            stacks_root=sdata.get("stacks_root", ""),
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

    def delete_stack(self, key: str) -> bool:
        path = self._file_for(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def duplicate_stack(
        self,
        key: str,
        new_display_name: str,
        new_environment: Optional[str] = None,
    ) -> Optional[SavedStack]:
        existing = self.load_stack(key)
        if not existing:
            return None
        new_name = slugify(new_display_name)
        if not new_name:
            return None
        env = (new_environment or existing.config.environment).strip() or "development"
        new_cfg = StackConfig(
            name=new_name,
            display_name=new_display_name.strip(),
            environment=env,
            stacks_root=existing.config.stacks_root,
            services=[ServiceConfig(**asdict(s)) for s in existing.config.services],
            networks=list(existing.config.networks),
            network_vars=dict(existing.config.network_vars),
        )
        return self.save_stack(new_cfg, generated=False)


# Singleton instance
stack_library = StackLibrary()
