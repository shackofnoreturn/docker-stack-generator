"""
Stack scaffold generator – pure Python equivalent of new-stack.sh.
No I/O side-effects on import; call generate_stack() to write files.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PortMapping:
    """A single published port entry (long-form Docker Compose syntax)."""
    target: str        # container port, e.g. "80" or "${TRAEFIK_PORT}"
    published: str     # host port, e.g. "80"
    protocol: str = "tcp"   # tcp / udp
    mode: str = "host"      # host / ingress


@dataclass
class VolumeMount:
    """A single volume/bind-mount entry for a service."""
    # kind: "socket" | "config" | "data"
    #   socket → literal host_path:container_path[:options]
    #   config → ${PATH_DOCKER_STACKS}/${env}/<host_subpath>:<container_path>[:ro]
    #   data   → ${PATH_DOCKER_DATA}/${env}/<host_subpath>:<container_path>
    kind: str           # "socket" | "config" | "data"
    host_path: str      # literal path (socket) or subpath under base var (config/data)
    container_path: str # path inside the container
    read_only: bool = True  # :ro suffix for socket/config; data defaults to False but user can set


@dataclass
class PlacementConstraint:
    """A single deploy.placement.constraints entry."""
    role: str = "worker"       # "manager" or "worker"
    label_key: str = ""        # e.g. "myapp"  → "node.labels.myapp == true"


@dataclass
class ServiceConfig:
    name: str                       # slugified
    display_name: str               # original user input
    image: str                      # e.g. linuxserver/qbittorrent
    image_base: str                 # image without tag
    image_tag: str                  # tag, e.g. "latest"
    description: str
    port: str                       # numeric string or "TODO"
    port_source: str                # "image metadata" / "manual" / …
    restart_profile: str            # low / medium / high
    resource_profile: str           # low / medium / high
    runtime_dirs: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # --- new per-service fields ---
    networks: list[str] = field(default_factory=list)
    """Extra networks for this service (merged with stack defaults at render time)."""
    secrets: list[str] = field(default_factory=list)
    """Secret names only; values live in the vault and generated files use age encryption."""
    ports: list[PortMapping] = field(default_factory=list)
    """Long-form port mappings."""
    volumes: list[VolumeMount] = field(default_factory=list)
    """Volume / bind-mount entries (socket, config, data)."""
    constraint: PlacementConstraint = field(
        default_factory=lambda: PlacementConstraint(role="worker", label_key="")
    )
    """Single placement constraint (role + optional label)."""
    environment: dict[str, str] = field(default_factory=dict)
    """Explicit container environment variables, merged with the default TZ."""
    traefik: Optional[dict[str, str]] = None
    homepage: Optional[dict[str, str]] = None
    config_files: dict[str, str] = field(default_factory=dict)
    """Relative configuration paths mapped to base64-encoded file contents."""
    config_directories: list[str] = field(default_factory=list)


@dataclass
class StackConfig:
    name: str                       # slugified
    display_name: str               # original user input
    environment: str                # e.g. "development"
    stacks_root: str                # absolute path to stacks/ parent
    description: str = ""           # human-readable stack description
    services: list[ServiceConfig] = field(default_factory=list)
    networks: list[str] = field(default_factory=lambda: ["traefik", "monitoring"])
    network_vars: dict[str, str] = field(default_factory=lambda: {
        "traefik": "NETWORK_TRAEFIK",
        "monitoring": "NETWORK_MONITORING",
    })

    @property
    def stack_dir(self) -> Path:
        return Path(self.stacks_root) / self.environment / self.name

    @property
    def templates_dir(self) -> Path:
        # stacks/templates relative to stacks_root
        return Path(self.stacks_root) / "templates"


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value


def split_image(image: str) -> tuple[str, str]:
    """Split 'repo/name:tag' → ('repo/name', 'tag'). Defaults tag to 'latest'."""
    last = image.split("/")[-1]
    if ":" in last:
        tag = last.split(":")[-1]
        base = image[: image.rfind(":")]
        return base, tag
    return image, "latest"


# ---------------------------------------------------------------------------
# File generators
# ---------------------------------------------------------------------------

def _env_prefix(service_name: str) -> str:
    return service_name.upper().replace("-", "_")


def traefik_label_defaults(name: str) -> dict[str, str]:
    return {
        "network": "${NETWORK_TRAEFIK}",
        "rule": f"Host(`{name}.${{DOMAIN}}`)",
        "entrypoints": "websecure",
        "service": name,
        "port": f"${{{_env_prefix(name)}_PORT}}",
    }


def homepage_label_defaults(name: str, display_name: str, description: str) -> dict[str, str]:
    return {
        "group": "Infrastructure",
        "name": display_name or name,
        "icon": name,
        "href": f"https://{name}.${{DOMAIN}}",
        "description": description,
    }


def service_labels(svc: ServiceConfig) -> dict[str, str]:
    labels = {}
    if svc.traefik is not None:
        values = {**traefik_label_defaults(svc.name), **svc.traefik}
        labels.update({
            "traefik.enable": "true",
            "traefik.swarm.network": values["network"],
            f"traefik.http.routers.{svc.name}.rule": values["rule"],
            f"traefik.http.routers.{svc.name}.entrypoints": values["entrypoints"],
            f"traefik.http.routers.{svc.name}.service": values["service"],
            f"traefik.http.services.{values['service']}.loadbalancer.server.port": values["port"],
        })
    if svc.homepage is not None:
        values = {**homepage_label_defaults(svc.name, svc.display_name, svc.description), **svc.homepage}
        for key in ("group", "name", "icon", "href", "description"):
            labels[f"homepage.{key}"] = values[key]
    return labels


def generate_stack_yml(cfg: StackConfig) -> str:
    lines = ["---"]
    lines.append("# " + "-" * 69)
    title = cfg.name.replace("_", " ").title()
    lines.append(f"# {title} Variables")
    lines.append("# " + "-" * 69)
    lines.append("")
    lines.append(f"stack_name: {cfg.name}")
    lines.append("compose_file: compose.yml")
    lines.append("")

    # Collect all runtime directories from all services
    runtime_dirs: list[str] = []
    for svc in cfg.services:
        if hasattr(svc, "runtime_dirs") and svc.runtime_dirs:
            for subdir in svc.runtime_dirs:
                subdir = subdir.strip().strip("/")
                if subdir:
                    runtime_dirs.append(f"{cfg.name}/{svc.name}/{subdir}")
                else:
                    runtime_dirs.append(f"{cfg.name}/{svc.name}")
        else:
            # No explicit runtime_dirs — still emit nothing (empty)
            pass

    if runtime_dirs:
        lines.append("runtime_directories:")
        for rd in runtime_dirs:
            lines.append(f"  - {rd}")
    else:
        lines.append("runtime_directories: []")
    lines.append("")
    lines.append("networks:")
    for net_name, net_var in cfg.network_vars.items():
        lines.append(f"  {net_name}:")
        lines.append(f"    name: {net_var}")
        lines.append("    driver: overlay")
        lines.append("    external: true")
        lines.append("")
    lines.append("environment:")
    for svc in cfg.services:
        pfx = _env_prefix(svc.name)
        lines.append(f"  {pfx}_IMAGE: \"{svc.image_base}\"")
        lines.append(f"  {pfx}_VERSION: \"{svc.image_tag}\"")
        if svc.port == "TODO":
            lines.append(f"  {pfx}_PORT: # TODO")
        else:
            lines.append(f"  {pfx}_PORT: {svc.port}")
        lines.append("")
    return "\n".join(lines)


def generate_compose_j2(cfg: StackConfig) -> str:
    # Collect all networks needed across all services
    all_net_names: list[str] = list(cfg.networks)
    for svc in cfg.services:
        for n in (svc.networks or []):
            if n not in all_net_names:
                all_net_names.append(n)

    # Collect all secrets across all services
    all_secrets: list[str] = []
    for svc in cfg.services:
        for s in (svc.secrets or []):
            if s not in all_secrets:
                all_secrets.append(s)

    lines = [f"# Stack: {cfg.name}", "", "services:", ""]
    for svc in cfg.services:
        pfx = _env_prefix(svc.name)
        title = svc.name.replace("_", " ").title()
        lines.append("# " + "=" * 75)
        lines.append(f"# {title}")
        lines.append("# " + "=" * 75)
        lines.append("")
        lines.append(f"  {svc.name}:")
        lines.append(f"    image: ${{{pfx}_IMAGE}}:${{{pfx}_VERSION}}")
        lines.append("")
        lines.append("    environment:")
        environment = {"TZ": "${TZ}", **svc.environment}
        for key, value in environment.items():
            lines.append(f"      {json.dumps(key)}: {json.dumps(value)}")
        lines.append("")
        lines.append("{% include '../../templates/common/logging.yml.j2' %}")

        # Ports (long-form)
        if svc.ports:
            lines.append("    ports:")
            for pm in svc.ports:
                lines.append(f"      - target:    {pm.target}")
                lines.append(f"        published: {pm.published}")
                lines.append(f"        protocol:  {pm.protocol}")
                lines.append(f"        mode:      {pm.mode}")
                lines.append("")

        # Volumes / bind-mounts
        if svc.volumes:
            lines.append("    volumes:")
            for vm in svc.volumes:
                ro_suffix = ":ro" if vm.read_only else ""
                if vm.kind == "socket":
                    lines.append(f"      - {vm.host_path}:{vm.container_path}{ro_suffix}")
                elif vm.kind == "config":
                    lines.append(
                        f"      - ${{PATH_DOCKER_STACKS}}/${{env}}/{vm.host_path}:{vm.container_path}{ro_suffix}"
                    )
                else:  # data
                    lines.append(
                        f"      - ${{PATH_DOCKER_DATA}}/${{env}}/{vm.host_path}:{vm.container_path}{ro_suffix}"
                    )

        # Secrets
        if svc.secrets:
            lines.append("    secrets:")
            for s in svc.secrets:
                lines.append(f"      - {s}")
            lines.append("")

        # Networks (merge stack defaults + service-specific)
        svc_nets = list(cfg.networks)
        for n in (svc.networks or []):
            if n not in svc_nets:
                svc_nets.append(n)
        lines.append("    networks:")
        for net in svc_nets:
            lines.append(f"      - {net}")
        lines.append("")

        lines.append("    deploy:")
        lines.append("      replicas: 1")
        lines.append("")
        
        labels = service_labels(svc)
        if labels:
            lines.append("      labels:")
            for key, value in labels.items():
                lines.append(f"        - {json.dumps(f'{key}={value}')}")
            lines.append("")

        # Placement constraints
        c = svc.constraint
        if c and (c.role or c.label_key):
            lines.append("      placement:")
            lines.append("        constraints:")
            if c.role:
                lines.append(f"          - node.role == {c.role}")
            if c.label_key:
                lines.append(f"          - node.labels.{c.label_key} == true")
            lines.append("")

        lines.append(f"{{% include '../../templates/policies/{svc.restart_profile}.yml.j2' %}}")
        lines.append(f"{{% include '../../templates/resources/{svc.resource_profile}.yml.j2' %}}")
        lines.append("")

    # Bottom-level networks block
    lines.append("networks:")
    for net_name in all_net_names:
        net_var = cfg.network_vars.get(net_name, net_name.upper().replace("-", "_"))
        lines.append(f"  {net_name}:")
        lines.append(f"    name: ${{{net_var}}}")
        lines.append("    external: true")
        lines.append("")

    lines.append("")

    # Bottom-level secrets block
    if all_secrets:
        lines.append("secrets:")
        for s in all_secrets:
            lines.append(f"  {s}:")
            lines.append("    external: true")
            lines.append("")

    return "\n".join(lines)


def generate_readme(cfg: StackConfig) -> str:
    title = cfg.display_name.strip().title() if cfg.display_name else cfg.name.replace("_", " ").title()
    lines = [f"# Stack: {title}", ""]
    if cfg.description and cfg.description.strip():
        lines.append(cfg.description.strip())
    else:
        lines.append("*TODO: STACK DESCRIPTION HERE.*")
    lines.append("")
    lines.append("## Services")

    for svc in cfg.services:
        meta = svc.metadata
        title = svc.name.replace("_", " ").title()
        lines.append("")
        lines.append(f"### {title}")
        lines.append("")
        lines.append("#### Image & Repository")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append(f"| Image | `{svc.image}` |")
        lines.append(f"| Tag | `{meta.get('tag', svc.image_tag)}` |")
        lines.append(f"| Repository | {meta.get('repository', svc.image_base)} |")
        lines.append(f"| Docker Hub | {meta.get('hub_url', '')} |")
        lines.append(f"| Digest | `{meta.get('digest', 'unknown')}` |")
        desc = (meta.get("description") or meta.get("short_description") or "TODO").replace("\n", " ")
        lines.append(f"| Description | {desc} |")
        lines.append(f"| Port | {svc.port} ({svc.port_source}) |")

        lines.append("")
        lines.append("#### Image Runtime Configuration")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append(f"| OS | {meta.get('os', 'unknown')} |")
        lines.append(f"| Architecture | {meta.get('architecture', 'unknown')} |")
        cfg_data = meta.get("docker_config", {})
        lines.append(f"| User | {cfg_data.get('User', 'default')} |")
        lines.append(f"| Working directory | {cfg_data.get('WorkingDir', 'unset')} |")
        ep = " ".join(cfg_data.get("Entrypoint") or []) or "default"
        cmd = " ".join(cfg_data.get("Cmd") or []) or "default"
        lines.append(f"| Entrypoint | `{ep}` |")
        lines.append(f"| Command | `{cmd}` |")

        lines.append("")
        lines.append("#### Network")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        ports = meta.get("exposed_ports") or {}
        port_str = ", ".join(ports.keys()) if ports else "none declared"
        lines.append(f"| Exposed ports | {port_str} |")

        env_vars = meta.get("env") or []
        lines.append("")
        lines.append("#### Environment Defaults")
        lines.append("")
        lines.append("| Variable | Default |")
        lines.append("|---|---|")
        for ev in env_vars:
            if "=" in ev:
                k, _, v = ev.partition("=")
                lines.append(f"| `{k}` | `{v or 'empty'}` |")

        volumes = meta.get("volumes") or {}
        lines.append("")
        lines.append("#### Persistent Volumes Declared by Image")
        lines.append("")
        lines.append("| Container Path |")
        lines.append("|---|")
        for vpath in volumes:
            lines.append(f"| `{vpath}` |")

        labels = meta.get("labels") or {}
        lines.append("")
        lines.append("#### OCI Labels")
        lines.append("")
        lines.append("| Label | Value |")
        lines.append("|---|---|")
        for lk, lv in labels.items():
            lv_clean = str(lv).replace("\n", " ")
            lines.append(f"| `{lk}` | {lv_clean} |")

        oci_fields = [
            ("title", "org.opencontainers.image.title"),
            ("description", "org.opencontainers.image.description"),
            ("url", "org.opencontainers.image.url"),
            ("source", "org.opencontainers.image.source"),
            ("documentation", "org.opencontainers.image.documentation"),
            ("vendor", "org.opencontainers.image.vendor"),
            ("licenses", "org.opencontainers.image.licenses"),
            ("created", "org.opencontainers.image.created"),
            ("revision", "org.opencontainers.image.revision"),
        ]
        lines.append("")
        lines.append("#### Project & Publisher Information")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        for display_name, label_key in oci_fields:
            val = labels.get(label_key, "not provided")
            lines.append(f"| {display_name} | {val} |")

        lines.append("")
        lines.append("#### Operational Notes")
        lines.append("")
        lines.append("- TODO: Document service-specific configuration, storage, routing, healthchecks, and operational behavior.")

    lines.append("")
    lines.append("# Runtime assumptions")
    lines.append("")
    lines.append("- Docker Swarm with the external `traefik` and `monitoring` networks available.")
    lines.append("- External Docker networks `NETWORK_TRAEFIK` and `NETWORK_MONITORING` must exist.")
    lines.append("- Required global variables such as `${TZ}`, `${PATH_DOCKER_DATA}`, `${NETWORK_TRAEFIK}`, and `${env}` are supplied by the deployment inventory.")
    lines.append("- TODO: Add any required node labels, NFS mounts, runtime directories, and external secrets.")
    lines.append("")
    lines.append("The stack is designed to work with the infrastructure repository that creates the required Docker networks, runtime directories, and deployment workflow.")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("- `compose.yml.j2`: Jinja2 Docker Compose template")
    lines.append("- `stack.yml`: Stack metadata, runtime directories, and environment variables")
    lines.append("- `configs/`: Stack-specific configuration files")
    lines.append("- `secrets/`: Age-encrypted `<secret-name>.age` files (plaintext values remain in the application vault)")
    lines.append("- `../../templates/`: Shared Jinja snippets for policies, resources, logging, labels, placement, healthchecks, and mounts")
    lines.append("- `README.md`: Stack documentation")
    lines.append("")
    lines.append("## Secrets")
    lines.append("")
    lines.append("- TODO: List required Docker Swarm secrets and explain their purpose; write None if not applicable.")
    lines.append("")
    lines.append("# How")
    lines.append("")
    lines.append("## Semaphore")
    lines.append("")
    lines.append("Use the [ansible-docker-swarm-services](https://github.com/shackofnoreturn/ansible-docker-swarm-services) repository in Semaphore and use the playbook `playbook-stack-deploy.yml` to deploy this stack.")
    lines.append("")
    lines.append("## CLI")
    lines.append("")
    lines.append("After rendering `compose.yml` from `compose.yml.j2`, deploy it with:")
    lines.append("")
    lines.append("```bash")
    lines.append(f"docker stack deploy --compose-file compose.yml {cfg.name}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Template snippets (written once, never overwritten)
# ---------------------------------------------------------------------------

POLICY_TEMPLATES = {
    "low": """\
      restart_policy:
        condition: on-failure
        delay: 10s
        max_attempts: 3
        window: 5m
      update_config:
        parallelism: 1
        delay: 10s
        order: stop-first
        failure_action: rollback
      rollback_config:
        parallelism: 1
        delay: 10s
        order: stop-first
""",
    "medium": """\
      restart_policy:
        condition: on-failure
        delay: 10s
        max_attempts: 5
        window: 5m
      update_config:
        parallelism: 1
        delay: 10s
        order: stop-first
        failure_action: rollback
      rollback_config:
        parallelism: 1
        delay: 10s
        order: stop-first
""",
    "high": """\
      restart_policy:
        condition: on-failure
        delay: 15s
        max_attempts: 5
        window: 5m
      update_config:
        parallelism: 1
        delay: 10s
        order: stop-first
        failure_action: rollback
      rollback_config:
        parallelism: 1
        delay: 10s
        order: stop-first
""",
}

RESOURCE_TEMPLATES = {
    "low": """\
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
        reservations:
          cpus: '0.05'
          memory: 64M
""",
    "medium": """\
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.10'
          memory: 128M
""",
    "high": """\
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.25'
          memory: 512M
""",
}

LOGGING_TEMPLATE = """\
    logging:
      driver: json-file
      options:
        max-file: "10"
        max-size: 2m
"""

TRAEFIK_LABEL_TEMPLATE = """\
        - traefik.enable=true
        - traefik.swarm.network=${NETWORK_TRAEFIK}
        - traefik.http.routers.{{ service_name }}.rule=Host(`{{ service_name }}.${DOMAIN}`)
        - traefik.http.routers.{{ service_name }}.entrypoints=websecure
        - traefik.http.services.{{ service_name }}.loadbalancer.server.port=${{ service_env_prefix }}_PORT
"""

HOMEPAGE_LABEL_TEMPLATE = """\
        - homepage.group=TODO
        - homepage.name={{ service_display_name }}
        - homepage.icon=TODO
        - homepage.href=https://{{ service_name }}.${DOMAIN}
        - homepage.description=TODO
"""

RUNTIME_CONFIG_MOUNT = """\
      - ${PATH_DOCKER_DATA}/${env}/{{ stack_name }}/{{ service_name }}:/config
"""

HEALTHCHECK_HTTP = """\
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:${SERVICE_PORT}/"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 90s
"""


def ensure_shared_templates(templates_dir: Path) -> list[str]:
    """Create missing shared snippets and return only the paths written."""
    created = []
    for relative, content in _builtin_template_map().items():
        path = templates_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(str(path))
    return created


# ---------------------------------------------------------------------------
# Jinja2 rendering for full compose.yml preview
# ---------------------------------------------------------------------------

def _builtin_template_map() -> dict[str, str]:
    """Return built-in fallback contents keyed by path relative to templates/."""
    mapping: dict[str, str] = {
        "common/logging.yml.j2": LOGGING_TEMPLATE,
        "labels/traefik.yml.j2": TRAEFIK_LABEL_TEMPLATE,
        "labels/homepage.yml.j2": HOMEPAGE_LABEL_TEMPLATE,
        "mounts/runtime-config.yml.j2": RUNTIME_CONFIG_MOUNT,
        "healthchecks/http.yml.j2": HEALTHCHECK_HTTP,
        "placement/node-label.yml.j2": "        - node.role == manager\n        - node.labels.TODO == true\n",
    }
    for profile, content in POLICY_TEMPLATES.items():
        mapping[f"policies/{profile}.yml.j2"] = content
    for profile, content in RESOURCE_TEMPLATES.items():
        mapping[f"resources/{profile}.yml.j2"] = content
    return mapping


def render_compose_preview(cfg: StackConfig) -> str:
    """
    Render compose.yml.j2 through Jinja2, resolving `../../templates/...`
    includes from cfg.templates_dir on disk (or built-in defaults if absent).
    """
    import jinja2

    compose_source = generate_compose_j2(cfg)
    builtins = _builtin_template_map()
    templates_dir = cfg.templates_dir if cfg.stacks_root else None

    class _StackTemplateLoader(jinja2.BaseLoader):
        def get_source(self, environment, template: str):
            rel = template
            if rel.startswith("../../templates/"):
                rel = rel[len("../../templates/"):]
            elif rel.startswith("templates/"):
                rel = rel[len("templates/"):]

            if templates_dir is not None:
                candidate = templates_dir / rel
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8")
                    return text, str(candidate), lambda: True

            if rel in builtins:
                return builtins[rel], f"builtin:{rel}", lambda: True

            raise jinja2.TemplateNotFound(template)

    env = jinja2.Environment(
        loader=_StackTemplateLoader(),
        keep_trailing_newline=True,
        undefined=jinja2.ChainableUndefined,
    )
    tpl = env.from_string(compose_source)
    return tpl.render(stack_name=cfg.name, env=cfg.environment)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

class GenerationError(Exception):
    pass


def validate_service_configs(files: dict[str, str], directories: list[str]) -> dict[str, bytes]:
    paths = [*files, *directories]
    for path in paths:
        parts = path.split("/")
        if not path or any(
            part in ("", ".", "..") or part.endswith((".", " "))
            or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in parts
        ):
            raise ValueError(f"Invalid configuration path: {path!r}. Use relative paths with forward slashes.")
    file_names = {path.casefold() for path in files}
    if len(file_names) != len(files):
        raise ValueError("Configuration file paths must be unique, ignoring case.")
    for path in paths:
        parts = path.casefold().split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in file_names:
                raise ValueError(f"Configuration file is also used as a directory: {path!r}")
    if file_names.intersection(path.casefold() for path in directories):
        raise ValueError("A configuration path cannot be both a file and a directory.")
    decoded = {}
    for path, contents in files.items():
        try:
            decoded[path] = base64.b64decode(contents, validate=True)
        except (ValueError, binascii.Error, TypeError) as exc:
            raise ValueError(f"Invalid configuration file contents: {path!r}") from exc
    return decoded


def _service_config_plan(cfg: StackConfig) -> tuple[list[Path], dict[Path, bytes]]:
    directories = []
    files = {}
    for service in cfg.services:
        if not service.config_files and not service.config_directories:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", service.name):
            raise ValueError(f"Invalid configuration service name: {service.name!r}")
        contents = validate_service_configs(service.config_files, service.config_directories)
        root = cfg.stack_dir.resolve() / "configs" / service.name
        service_directories = [root, *(root / path for path in service.config_directories)]
        service_files = {root / path: data for path, data in contents.items()}
        for target in [*service_directories, *service_files]:
            if not target.resolve().is_relative_to(root):
                raise ValueError(f"Configuration path escapes its service directory: {target}")
            for parent in [target, *target.parents]:
                if parent == cfg.stack_dir.resolve().parent:
                    break
                if parent.exists() and not parent.is_dir() and parent not in service_files:
                    raise ValueError(f"Configuration directory is an existing file: {parent}")
            if target in service_files and target.exists() and not target.is_file():
                raise ValueError(f"Configuration file is an existing directory: {target}")
        directories.extend(service_directories)
        files.update(service_files)
    return directories, files


def generate_stack(
    cfg: StackConfig, overwrite: bool = False, encrypted_secrets: Optional[dict[str, bytes]] = None,
) -> list[str]:
    """
    Write all stack files to disk. Returns a list of created/updated paths.
    Raises GenerationError if the directory already exists and overwrite=False.
    """
    stack_dir = cfg.stack_dir
    if stack_dir.exists() and not overwrite:
        raise GenerationError(
            f"Stack directory already exists: {stack_dir}\n"
            "Enable overwrite to continue."
        )

    try:
        config_directories, config_files = _service_config_plan(cfg)
    except ValueError as exc:
        raise GenerationError(str(exc)) from exc

    from docker_stack_generator.age_secrets import validate_secret_name
    names = sorted({name for svc in cfg.services for name in svc.secrets})
    secrets_dir = stack_dir / "secrets"
    secret_files: dict[Path, bytes] = {}
    legacy_files: list[Path] = []
    for name in names:
        try:
            validate_secret_name(name)
        except ValueError as exc:
            raise GenerationError(str(exc)) from exc
        contents = (encrypted_secrets or {}).get(name)
        if not isinstance(contents, bytes) or not contents.startswith(b"age-encryption.org/v1\n"):
            raise GenerationError(f"Encrypted value missing for secret '{name}'. Set its vault value and configure age encryption.")
        if secrets_dir.is_symlink() or not secrets_dir.resolve().is_relative_to(stack_dir.resolve()):
            raise GenerationError("The secrets directory must remain inside the stack directory.")
        target = secrets_dir / f"{name}.age"
        candidates = [target, secrets_dir / name, secrets_dir / f"{name}.txt"]
        for path in candidates:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise GenerationError(f"Secret output must be a regular file: {path}")
        secret_files[target] = contents
        legacy_files.extend(path for path in candidates[1:] if path.exists())
    marker = secrets_dir / ".gitkeep"
    if names and (marker.is_symlink() or (marker.exists() and not marker.is_file())):
        raise GenerationError("The secrets .gitkeep path must be a regular file.")

    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / "configs").mkdir(exist_ok=True)
    secrets_dir.mkdir(exist_ok=True)
    configs_gitkeep = stack_dir / "configs" / ".gitkeep"
    configs_gitkeep.touch()

    created = ensure_shared_templates(cfg.templates_dir)
    created.append(str(configs_gitkeep))

    for directory in config_directories:
        directory.mkdir(parents=True, exist_ok=True)
    for path, contents in config_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        created.append(str(path))
    for directory in config_directories:
        if not any(directory.iterdir()):
            gitkeep = directory / ".gitkeep"
            gitkeep.touch()
            created.append(str(gitkeep))

    for path, contents in secret_files.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=secrets_dir, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise GenerationError("Cannot write encrypted secrets. Check directory permissions and retry.") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        created.append(str(path))
    # Delete legacy plaintext only after every replacement ciphertext is on disk.
    for path in legacy_files:
        path.unlink()
        created.append(str(path))
    if names and marker.exists():
        marker.unlink()
        created.append(str(marker))
    elif not names and not marker.exists():
        marker.touch()
        created.append(str(marker))

    stack_yml_path = stack_dir / "stack.yml"
    stack_yml_path.write_text(generate_stack_yml(cfg), encoding="utf-8")
    created.append(str(stack_yml_path))

    compose_path = stack_dir / "compose.yml.j2"
    compose_path.write_text(generate_compose_j2(cfg), encoding="utf-8")
    created.append(str(compose_path))

    readme_path = stack_dir / "README.md"
    readme_path.write_text(generate_readme(cfg), encoding="utf-8")
    created.append(str(readme_path))

    return created
