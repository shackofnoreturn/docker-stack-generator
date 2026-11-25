"""
Persistent application settings stored in ~/.config/docker-stack-generator/settings.json.
All mutable defaults are set here so the GUI always has sensible starting values.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path.home() / ".config" / "docker-stack-generator"
_CONFIG_FILE = _CONFIG_DIR / "settings.json"

_DEFAULTS: dict[str, Any] = {
    # Paths
    "stacks_root": "",           # e.g. /home/user/docker-stacks
    "stacks_env": "development", # sub-directory environment name
    # Network defaults
    "network_traefik": "NETWORK_TRAEFIK",
    "network_monitoring": "NETWORK_MONITORING",
    # Resource/policy profile labels
    "profiles": {
        "low": {
            "restart": {"max_attempts": 3, "delay": "10s", "window": "5m"},
            "resources": {"cpus": "0.5", "memory": "256M", "reserved_cpus": "0.05", "reserved_memory": "64M"},
        },
        "medium": {
            "restart": {"max_attempts": 5, "delay": "10s", "window": "5m"},
            "resources": {"cpus": "1.0", "memory": "512M", "reserved_cpus": "0.10", "reserved_memory": "128M"},
        },
        "high": {
            "restart": {"max_attempts": 5, "delay": "15s", "window": "5m"},
            "resources": {"cpus": "2.0", "memory": "2G", "reserved_cpus": "0.25", "reserved_memory": "512M"},
        },
    },
    # UI state
    "window_geometry": None,
    "last_stack_name": "",
    "default_restart_profile": "medium",
    "default_resource_profile": "medium",
    # DockerHub search history (list of strings)
    "search_history": [],
    # Recently created stacks
    "recent_stacks": [],
}


class AppConfig:
    """Thread-safe, JSON-backed persistent settings."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def update(self, mapping: dict[str, Any]) -> None:
        self._data.update(mapping)
        self._save()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    @property
    def stacks_root(self) -> str:
        return self._data.get("stacks_root", "")

    @stacks_root.setter
    def stacks_root(self, v: str) -> None:
        self.set("stacks_root", v)

    @property
    def stacks_env(self) -> str:
        return self._data.get("stacks_env", "development")

    @stacks_env.setter
    def stacks_env(self, v: str) -> None:
        self.set("stacks_env", v)

    def add_search_history(self, term: str) -> None:
        history: list[str] = self._data.get("search_history", [])
        if term in history:
            history.remove(term)
        history.insert(0, term)
        self._data["search_history"] = history[:30]
        self._save()

    def add_recent_stack(self, name: str, path: str) -> None:
        recent: list[dict] = self._data.get("recent_stacks", [])
        entry = {"name": name, "path": path}
        # Remove duplicate by name
        recent = [r for r in recent if r.get("name") != name]
        recent.insert(0, entry)
        self._data["recent_stacks"] = recent[:10]
        self._save()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    def _load(self) -> None:
        import copy
        self._data = copy.deepcopy(_DEFAULTS)
        if _CONFIG_FILE.exists():
            try:
                with open(_CONFIG_FILE, "r", encoding="utf-8") as fh:
                    saved = json.load(fh)
                # Deep-merge: saved values win
                self._deep_merge(self._data, saved)
            except (json.JSONDecodeError, OSError):
                pass  # corrupt file → keep defaults

    def _save(self) -> None:
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_CONFIG_FILE, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except OSError:
            pass

    @staticmethod
    def _deep_merge(base: dict, overlay: dict) -> None:
        for key, value in overlay.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                AppConfig._deep_merge(base[key], value)
            else:
                base[key] = value


# Singleton instance used throughout the app
settings = AppConfig()
