# Docker Stack Generator

A Python GUI application to scaffold Docker Swarm stack files

## Features

- **GUI interface** — no more interactive terminal prompts
- **Stack description** — enter a human-readable description that is written directly into `README.md`
- **Runtime directories** — define per-service runtime directories in the service dialog; they are collected into `runtime_directories` in `stack.yml` as `<stack>/<service>` or `<stack>/<service>/<subdir>` paths
- **Persistent settings** — stacks root, environment, network variables, and profile defaults are saved across sessions
- **Docker Hub search** — search images in-app with star counts and descriptions
- **Automatic metadata** — fetches exposed ports, OS, architecture, image layers from Docker Hub without pulling images
- **Service environment variables** — a separate Environment tab to add, edit, and remove key/value pairs, saved with each service and included in its Compose `environment` block
- **Environment suggestions** — Fetch Metadata lists variables declared by the image with their defaults; selected suggestions can be added without overwriting existing values. Image metadata may not list every supported variable.
- **Traefik labels** — per-service tab with service-based router, hostname, backend port variable, and editable routing settings
- **Homepage labels** — per-service tab with customizable group, name, icon, URL, and description
- **Service configuration files** — import or create files, edit UTF-8 text, and add nested subdirectories under `configs/<service>/`
- **Preview** — live `stack.yml` and `compose.yml.j2` preview before writing any files
- **Recent stacks** — quick access to previously generated stacks
- **Same output format** as the original `new-stack.sh`

## Requirements

- Python 3.11+
- `python-pyqt6` (install via `sudo pacman -S python-pyqt6` on Arch)
- `requests`, `PyYAML`, and `Jinja2`

## Usage

```bash
# From the project directory:
python -m pip install -e .
python run.py

# Or as a module:
python -m docker_stack_generator
```

In VS Code, use **Terminal > Run Task > Install prerequisites** to install the
application and its declared dependencies into the `python` environment on your PATH.
Use **Run application** to launch the GUI from the workspace directory.

## Project Structure

```
docker-stack-generator/
├── docker_stack_generator/
│   ├── __init__.py        # version
│   ├── app.py             # QApplication entry point
│   ├── config.py          # persistent JSON settings (~/.config/docker-stack-generator/)
│   ├── dockerhub.py       # Docker Hub API (search, metadata, ports)
│   ├── generator.py       # file generation (stack.yml, compose.yml.j2, README.md)
│   ├── main_window.py     # main window
│   ├── service_dialog.py  # add/edit service dialog
│   ├── settings_dialog.py # settings dialog
│   └── widgets.py         # shared widgets + background worker threads
├── __main__.py
├── run.py
├── pyproject.toml
└── README.md
```

## Settings

Settings are stored in `~/.config/docker-stack-generator/settings.json`. Open **Edit → Settings** (or `Ctrl+,`) to configure:

- **Stacks root directory** — path to your `shackhq-docker-stacks/stacks/` directory
- **Environment** — sub-directory (e.g. `development`, `production`)
- **Default profiles** — restart and resource profile defaults
- **Network variable names** — `NETWORK_TRAEFIK`, `NETWORK_MONITORING`

## Service Labels

Open a service's **Traefik** or **Homepage** tab and select **Enable Traefik labels**
or **Enable Homepage labels**. Each set is optional and disabled by default, including
when opening older saved stacks. Labels are written under the service's `deploy.labels`
in `compose.yml.j2` and the rendered Compose preview, as required for Docker Swarm.

Blank fields use the displayed defaults and follow changes to the service name or
description. Enter a value to override a default; clear it to restore automatic values.
Settings are retained when saving and reopening a stack.

- **Traefik:** enabled routing, network `${NETWORK_TRAEFIK}`, rule
        ``Host(`<service>.${DOMAIN}`)``, entrypoint `websecure`, backend service `<service>`,
        and backend port `${<SERVICE>_PORT}` using the uppercase service identifier.
- **Homepage:** group `Infrastructure`, service display name, service identifier as
        the icon, URL `https://<service>.${DOMAIN}`, and the service description.

The Traefik network must also be attached to the service in the stack's networks or
the service's Networking tab; changing a label does not attach a network.

## Service Configuration Files

Each service has a **Configurations** tab. Import existing files or create new
ones, optionally specifying a destination subdirectory such as `dynamic`.
Imported files are saved with the stack, so the original files are not needed
when generating the stack later or on another machine after importing that stack.

Select a file to edit its UTF-8 text. Binary files are preserved without editing.
Double-click a file path to rename it or move it into a relative subdirectory.
Use the Subdirectories list for additional folders, including empty ones.

Generation writes files under `configs/<service-name>/` and creates their parent
directories. For example, a Traefik service can contain `traefik.yml` alongside
`dynamic/routes.yml`, or an empty `dynamic` directory. Empty directories receive
a `.gitkeep` so they can be retained in version control. Paths must be relative,
use forward slashes, and cannot escape the service directory.

Regeneration requires the existing overwrite option. It updates configured files
but leaves unrelated files alone. Removing an entry does not delete a previously
generated file from disk. File contents are included in saved stack definitions
and exports; do not import credentials that should instead be stored as secrets.

Configuration files are not automatically mounted into containers. Add the
appropriate config bind mounts in the service's **Volumes** tab.

## Generated Output


```
stacks/<env>/<stack-name>/
├── stack.yml          # environment variables and network config
├── compose.yml.j2     # Jinja2 Compose template for Docker Swarm
├── README.md          # auto-generated documentation
├── configs/
│   ├── .gitkeep
│   └── <service-name>/
│       ├── app.yml
│       └── dynamic/
│           └── routes.yml
└── secrets/.gitkeep

stacks/templates/      # shared Jinja snippets (created once, never overwritten)
├── common/logging.yml.j2
├── policies/{low,medium,high}.yml.j2
├── resources/{low,medium,high}.yml.j2
├── labels/{traefik,homepage}.yml.j2
├── healthchecks/http.yml.j2
├── mounts/runtime-config.yml.j2
└── placement/node-label.yml.j2
```

# Todo

- [x] Per-service Environment tab with image-declared variable suggestions and Compose output.
- [x] Per-service Traefik tab with service-based router labels and uppercase port variable.

        # ---------------------------------------------------------------------
        # Traefik
        # ---------------------------------------------------------------------

        - traefik.enable=true
        - traefik.swarm.network=${NETWORK_TRAEFIK}

        # HTTP
        - traefik.http.routers.whoami.rule=Host(`whoami.${DOMAIN}`)
        - traefik.http.routers.whoami.entrypoints=websecure
        - traefik.http.routers.whoami.service=whoami

        # Port
        - traefik.http.services.whoami.loadbalancer.server.port=${WHOAMI_PORT}

- [x] Per-service Homepage tab with customizable group, service-based name, icon, URL, and description.

        # ---------------------------------------------------------------------
        # Homepage
        # ---------------------------------------------------------------------

        - homepage.group=Infrastructure
        - homepage.name=Whoami
        - homepage.icon=mdi-account-question
        - homepage.href=https://whoami.${DOMAIN}
        - homepage.description=Traefik whoami test service

