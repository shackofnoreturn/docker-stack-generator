"""
Docker Hub API helpers – search, metadata, port detection.
All network calls are synchronous; wrap in a QThread for the GUI.
"""
from __future__ import annotations

import re
from typing import Optional

import requests

TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query: str, page_size: int = 15) -> list[dict]:
    """
    Search Docker Hub. Returns a list of dicts with keys:
        name, short_description, official, star_count, pull_count
    """
    try:
        resp = requests.get(
            "https://hub.docker.com/v2/search/repositories/",
            params={"query": query, "page_size": page_size},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        out = []
        for r in results:
            out.append(
                {
                    "name": r.get("repo_name", ""),
                    "short_description": r.get("short_description") or "",
                    "official": r.get("is_official", False),
                    "star_count": r.get("star_count", 0),
                    "pull_count": r.get("pull_count", 0),
                }
            )
        return out
    except requests.RequestException as exc:
        raise ConnectionError(f"Docker Hub search failed: {exc}") from exc


def format_search_result(result: dict) -> tuple[str, str]:
    """Return visible list text and a complete tooltip for a search result."""
    name = result.get("name", "")
    official = " ✓" if result.get("official") else ""
    stars = result.get("star_count", 0) or 0
    description = result.get("short_description", "") or ""

    text = f"{name}{official}"
    if stars:
        text += f"\n★ {stars:,}"
    if description:
        text += f"\n{description[:80]}"

    tooltip = f"{name}{official}"
    if stars:
        tooltip += f"\n★ {stars:,}"
    if description:
        tooltip += f"\n{description}"
    return text, tooltip


# ---------------------------------------------------------------------------
# Registry auth + manifest inspection
# ---------------------------------------------------------------------------

def _normalize_image(image: str) -> tuple[str, str]:
    """Return (repo, tag) where repo may be 'library/nginx' etc."""
    image = image.removeprefix("docker.io/").removeprefix("https://").removeprefix("http://")
    if "/" not in image:
        image = f"library/{image}"
    if "@" in image.split("/")[-1]:
        # digest reference
        repo, rest = image.rsplit("@", 1)
        return repo, rest
    if ":" in image.split("/")[-1]:
        repo, tag = image.rsplit(":", 1)
        return repo, tag
    return image, "latest"


def _get_token(repo: str) -> Optional[str]:
    try:
        resp = requests.get(
            "https://auth.docker.io/token",
            params={"service": "registry.docker.io", "scope": f"repository:{repo}:pull"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("token")
    except requests.RequestException:
        return None


MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json,"
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)
SINGLE_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)


def _fetch_manifest(repo: str, ref: str, token: str) -> Optional[dict]:
    try:
        resp = requests.get(
            f"https://registry-1.docker.io/v2/{repo}/manifests/{ref}",
            headers={"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _resolve_manifest(repo: str, tag: str, token: str) -> Optional[dict]:
    manifest = _fetch_manifest(repo, tag, token)
    if manifest is None:
        return None
    # Multi-arch index → resolve to linux/amd64 or best match
    if isinstance(manifest.get("manifests"), list):
        import platform
        machine = platform.machine()
        arch_map = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm"}
        arch = arch_map.get(machine, "amd64")
        digest = None
        for m in manifest["manifests"]:
            p = m.get("platform", {})
            if p.get("os") == "linux" and p.get("architecture") == arch:
                digest = m["digest"]
                break
        if not digest:
            for m in manifest["manifests"]:
                if m.get("platform", {}).get("os") == "linux":
                    digest = m["digest"]
                    break
        if not digest:
            return None
        try:
            resp = requests.get(
                f"https://registry-1.docker.io/v2/{repo}/manifests/{digest}",
                headers={"Authorization": f"Bearer {token}", "Accept": SINGLE_MANIFEST_ACCEPT},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            return None
    return manifest


def _fetch_config(repo: str, config_digest: str, token: str) -> Optional[dict]:
    try:
        resp = requests.get(
            f"https://registry-1.docker.io/v2/{repo}/blobs/{config_digest}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _fetch_hub_repo(repo: str) -> dict:
    try:
        resp = requests.get(
            f"https://hub.docker.com/v2/repositories/{repo}/",
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {}


# ---------------------------------------------------------------------------
# Public metadata API
# ---------------------------------------------------------------------------

def fetch_metadata(image: str) -> dict:
    """
    Fetch comprehensive image metadata without pulling layers.
    Returns a rich dict ready for display / README generation.
    Always returns something even on partial failures.
    """
    repo, tag = _normalize_image(image)
    result: dict = {
        "image": image,
        "repository": repo,
        "tag": tag,
        "digest": "",
        "description": "",
        "short_description": "",
        "hub_url": f"https://hub.docker.com/r/{repo}",
        "stars": None,
        "pulls": None,
        "last_updated": None,
        "os": "",
        "architecture": "",
        "exposed_ports": {},
        "env": [],
        "volumes": {},
        "entrypoint": [],
        "cmd": [],
        "working_dir": "",
        "user": "",
        "labels": {},
        "layer_sizes": [],
        "compressed_size": None,
        "docker_config": {},
    }

    # Hub repo info (best-effort)
    hub = _fetch_hub_repo(repo)
    result["description"] = hub.get("description") or hub.get("short_description") or ""
    result["short_description"] = hub.get("short_description") or ""
    result["stars"] = hub.get("star_count")
    result["pulls"] = hub.get("pull_count")
    result["last_updated"] = hub.get("last_updated")

    # Registry metadata
    token = _get_token(repo)
    if token:
        manifest = _resolve_manifest(repo, tag, token)
        if manifest:
            config_digest = manifest.get("config", {}).get("digest", "")
            result["digest"] = config_digest
            layer_sizes = [lay.get("size", 0) for lay in manifest.get("layers", [])]
            result["layer_sizes"] = layer_sizes
            result["compressed_size"] = sum(layer_sizes) if layer_sizes else None

            if config_digest:
                cfg_blob = _fetch_config(repo, config_digest, token)
                if cfg_blob:
                    cfg = cfg_blob.get("config", {})
                    result["docker_config"] = cfg
                    result["exposed_ports"] = cfg.get("ExposedPorts") or {}
                    result["env"] = cfg.get("Env") or []
                    result["volumes"] = cfg.get("Volumes") or {}
                    result["entrypoint"] = cfg.get("Entrypoint") or []
                    result["cmd"] = cfg.get("Cmd") or []
                    result["working_dir"] = cfg.get("WorkingDir") or ""
                    result["user"] = cfg.get("User") or ""
                    result["labels"] = cfg.get("Labels") or {}
                    result["os"] = cfg_blob.get("os") or ""
                    result["architecture"] = cfg_blob.get("architecture") or ""

    return result


def get_exposed_ports(image: str) -> list[str]:
    """Return sorted list of exposed TCP port numbers as strings."""
    meta = fetch_metadata(image)
    ports = []
    for key in meta.get("exposed_ports", {}):
        m = re.match(r"^(\d+)/tcp$", key)
        if m:
            ports.append(m.group(1))
    return sorted(ports, key=int)
