"""HTTP service monitoring, endpoint discovery, and non-secret preferences."""
from __future__ import annotations

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from requests.auth import HTTPBasicAuth

from docker_stack_generator.generator import homepage_label_defaults
from docker_stack_generator.stack_library import SavedStack


@dataclass(frozen=True)
class MonitorTarget:
    stack: str
    service: str
    url: str

    @property
    def key(self) -> str:
        return json.dumps([self.stack, self.service], separators=(",", ":"))

    @property
    def credential_key(self) -> str:
        # Bind secrets to the exact endpoint, not just a reusable service name.
        return json.dumps(["monitoring", self.stack, self.service, self.url], separators=(",", ":"))


@dataclass(frozen=True)
class MonitorResult:
    target: MonitorTarget
    state: str
    detail: str
    checked_at: str


def validate_endpoint(url: str) -> str:
    url = url.strip()
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or "${" in url or any(c.isspace() for c in url)):
        raise ValueError("Use a resolved HTTP(S) URL without embedded credentials, whitespace, or fragments.")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Endpoint port must be between 1 and 65535.")
    return url


def discover_targets(entries: list[SavedStack], endpoints: dict[str, str], domain: str) -> list[MonitorTarget]:
    targets = []
    for entry in entries:
        stack = f"{entry.config.environment}/{entry.config.name}"
        for service in entry.config.services:
            suggested = "https://" + service.name + ".${DOMAIN}"
            if service.homepage is not None:
                suggested = {
                    **homepage_label_defaults(service.name, service.display_name, service.description),
                    **service.homepage,
                }["href"]
            elif service.traefik is not None:
                rule = service.traefik.get("rule", "")
                match = re.fullmatch(r"Host\(`([^`]+)`\)", rule.strip())
                if match:
                    suggested = f"https://{match.group(1)}"
                elif not rule:
                    suggested = f"https://{service.name}.${{DOMAIN}}"
            target = MonitorTarget(stack, service.name, suggested)
            url = endpoints.get(target.key, suggested)
            resolved = url.replace("${DOMAIN}", domain.strip()) if domain.strip() else url
            targets.append(MonitorTarget(stack, service.name, resolved))
    return sorted(targets, key=lambda target: (target.stack, target.service))


def stack_state(results: list[MonitorResult]) -> str:
    if not results:
        return "Not configured"
    states = {result.state for result in results}
    if states == {"Healthy"}:
        return "Healthy"
    if states & {"HTTP error", "Unreachable", "TLS error", "Invalid endpoint"}:
        return "Issues"
    if "Authorization required" in states:
        return "Authorization required"
    if "Vault locked" in states:
        return "Vault locked"
    return "Incomplete"


def check_target(target: MonitorTarget, credential: dict[str, str] | None = None) -> MonitorResult:
    checked = datetime.now().astimezone().isoformat(timespec="seconds")

    def result(state: str, detail: str) -> MonitorResult:
        return MonitorResult(target, state, detail, checked)

    if not target.url:
        return result("Not configured", "Configure an HTTP(S) endpoint for this service.")
    try:
        url = validate_endpoint(target.url)
    except ValueError:
        return result("Invalid endpoint", "Configure a valid URL and resolve ${DOMAIN}.")
    if credential and urlsplit(url).scheme != "https":
        return result("Invalid endpoint", "Credentials require HTTPS; use a TLS endpoint.")
    with requests.Session() as session:
        # Never use implicit .netrc credentials or persist response cookies.
        session.trust_env = False
        kwargs: dict = {}
        if credential:
            kind = credential.get("kind")
            if kind == "basic":
                kwargs["auth"] = HTTPBasicAuth(credential.get("username", ""), credential.get("password", ""))
            elif kind == "bearer":
                token = credential.get("token", "")
                if not token or "\r" in token or "\n" in token:
                    return result("Authorization required", "Configure a valid bearer token.")
                kwargs["headers"] = {"Authorization": f"Bearer {token}"}
            else:
                return result("Authorization required", "Configure Basic authentication or a bearer token.")
        try:
            with session.get(url, timeout=(3, 5), allow_redirects=False, stream=True, **kwargs) as response:
                code = response.status_code
            if 200 <= code < 300:
                return result("Healthy", f"HTTP {code}")
            if code in (401, 403):
                return result("Authorization required", f"HTTP {code}; configure service credentials.")
            if 300 <= code < 400:
                return result("Redirect", f"HTTP {code}; set the final health URL (redirects are not followed).")
            return result("HTTP error", f"HTTP {code}")
        except requests.exceptions.SSLError:
            return result("TLS error", "Certificate verification failed.")
        except requests.RequestException:
            return result("Unreachable", "Connection, DNS, timeout, or request error.")


def check_targets(targets: list[MonitorTarget], credentials: dict[str, dict[str, str]]) -> list[MonitorResult]:
    def check(target: MonitorTarget) -> MonitorResult:
        return check_target(target, credentials.get(target.credential_key))

    with ThreadPoolExecutor(max_workers=5) as executor:
        return list(executor.map(check, targets))


class MonitoringStore:
    """Keep endpoints and polling preferences separate from encrypted secrets."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".config" / "docker-stack-generator" / "monitoring.json"

    def load(self) -> dict:
        if not self.path.exists():
            return {"endpoints": {}, "domain": "", "interval": 60}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or not isinstance(data.get("endpoints"), dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["endpoints"].items())
                    or not isinstance(data.get("domain"), str)
                    or type(data.get("interval")) is not int or not 10 <= data["interval"] <= 3600):
                raise ValueError("Invalid monitoring preferences.")
            return data
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot load monitoring preferences; repair monitoring.json before saving.") from exc

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             delete=False) as handle:
                name = handle.name
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            if name is not None and Path(name).exists():
                Path(name).unlink()
