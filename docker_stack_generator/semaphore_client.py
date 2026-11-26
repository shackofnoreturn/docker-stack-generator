"""Semaphore API and endpoint checks without GUI dependencies."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests


DEFAULT_TARGETS = [
    {"action": "Deploy", "environment": environment, "project": 1,
     "template": template, "variables": {}}
    for environment, template in (
        ("development", 41), ("testing", 42), ("production", 43), ("management", 44)
    )
]
TERMINAL_STATES = {"success", "failed", "error", "stopped", "cancelled", "canceled"}


class SemaphoreError(Exception):
    pass


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an HTTP(S) URL without embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("The Semaphore server URL cannot contain a query or fragment.")
    return url.rstrip("/")


def validate_targets(targets: list[dict]) -> list[dict]:
    if not targets:
        raise ValueError("Configure at least one Semaphore target.")
    names = set()
    for target in targets:
        action = target["action"].strip()
        environment = target["environment"].strip()
        if not action or not re.fullmatch(r"[A-Za-z0-9_-]+", environment):
            raise ValueError("Each target needs an action and a valid environment name.")
        if (action.casefold(), environment.casefold()) in names:
            raise ValueError("Action/environment combinations must be unique.")
        names.add((action.casefold(), environment.casefold()))
        if int(target["project"]) < 1 or int(target["template"]) < 1:
            raise ValueError("Project and template IDs must be positive integers.")
        if not isinstance(target.get("variables", {}), dict):
            raise ValueError("Extra survey variables must be a JSON object.")
    return targets


class SemaphoreClient:
    def __init__(self, url: str, username: str = "", password: str = "", token: str = ""):
        self.url = validate_url(url)
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.username = username
        self._password = password
        self._token_auth = bool(token)
        self._authenticated = False

    def close(self):
        self._password = ""
        self.session.headers.pop("Authorization", None)
        self.session.cookies.clear()
        self.session.close()

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.session.request(
                method, f"{self.url}/api/{path}", timeout=(5, 20),
                allow_redirects=False, **kwargs,
            )
        except requests.RequestException as exc:
            raise SemaphoreError("Cannot reach Semaphore. Check the URL, network, and TLS certificate.") from exc
        if not 200 <= response.status_code < 300:
            raise SemaphoreError(
                f"Semaphore returned HTTP {response.status_code} for {method} {path}. "
                "Check authentication, permissions, and project/template IDs."
            )
        return response

    def authenticate(self):
        if self._authenticated:
            return
        if self._token_auth:
            self._request("GET", "user")
            self._authenticated = True
            return
        if not self.username or not self._password:
            raise SemaphoreError("Enter a Semaphore API token or a username and password.")
        self._request("POST", "auth/login", json={"auth": self.username, "password": self._password})
        self._authenticated = True

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError as exc:
            raise SemaphoreError("Semaphore returned invalid JSON; check the server URL and API version.") from exc

    def start(self, project: int, template: int, stack: str, variables: dict | None = None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", stack):
            raise ValueError("Stack names may contain letters, numbers, underscores, and hyphens.")
        self.authenticate()
        task = self._json(self._request("POST", f"project/{project}/tasks", json={
            "template_id": template,
            "environment": json.dumps({**(variables or {}), "stack_name": stack}),
        }))
        if not isinstance(task, dict) or not task.get("id"):
            raise SemaphoreError("Start response did not contain a task ID. Check Semaphore history before retrying.")
        return task

    def snapshot(self, project: int, task_id: int) -> dict:
        self.authenticate()
        path = f"project/{project}/tasks/{task_id}"
        task = self._json(self._request("GET", path))
        if not isinstance(task, dict):
            raise SemaphoreError("Semaphore returned invalid task metadata.")
        output = ""
        warning = ""
        try:
            data = self._json(self._request("GET", f"{path}/output"))
            entries = data if isinstance(data, list) else [data]
            output = "\n".join(str(entry.get("output") or "") for entry in entries if isinstance(entry, dict))
            if not output.strip():
                output = self._request("GET", f"{path}/raw_output").text
        except SemaphoreError as exc:
            warning = str(exc)
            try:
                output = self._request("GET", f"{path}/raw_output").text
                warning = ""
            except SemaphoreError:
                pass
        return {"task": task, "output": output, "warning": warning}

    def task_url(self, project: int, task_id: int) -> str:
        return f"{self.url}/project/{project}/history?t={task_id}"


def parse_endpoints(text: str, domain: str = "") -> list[tuple[str, str]]:
    endpoints = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "://" in line and ("=" not in line or line.index("://") < line.index("=")):
            name, url = line, line
        else:
            name, separator, url = line.partition("=")
            if not separator:
                raise ValueError("Endpoints must be URLs or name=https://host entries, one per line.")
        url = url.strip().replace("${DOMAIN}", domain.strip()) if domain.strip() else url.strip()
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or "${" in url):
            raise ValueError(f"Invalid or unresolved endpoint for {name.strip()}.")
        endpoints.append((name.strip(), url))
    if len(endpoints) > 30:
        raise ValueError("At most 30 service endpoints can be checked at once.")
    return endpoints


def _check_endpoint(endpoint: tuple[str, str]) -> dict:
    name, url = endpoint
    with requests.Session() as session:
        try:
            response = session.get(url, timeout=(3, 5), allow_redirects=False, stream=True)
            with response:
                code = response.status_code
            if 200 <= code < 300:
                state = "Reachable"
            elif 300 <= code < 400:
                state = "Redirect; destination not verified"
            elif code in (401, 403):
                state = "Responding; authentication required"
            else:
                state = "HTTP error"
            return {"name": name, "url": url, "state": state, "detail": f"HTTP {code}"}
        except requests.exceptions.SSLError:
            return {"name": name, "url": url, "state": "TLS error", "detail": "Certificate verification failed"}
        except requests.RequestException:
            return {"name": name, "url": url, "state": "Unreachable", "detail": "Connection, DNS, or timeout error"}


def check_endpoints(endpoints: list[tuple[str, str]]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=5) as executor:
        return list(executor.map(_check_endpoint, endpoints))

