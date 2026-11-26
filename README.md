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
- **Preview** — live `stack.yml` and `compose.yml.j2` preview before writing any files
- **Recent stacks** — quick access to previously generated stacks
- **Same output format** as the original `new-stack.sh`

## Requirements

- Python 3.11+
- `python-pyqt6` (install via `sudo pacman -S python-pyqt6` on Arch)
- `requests` and `PyYAML` (already installed system-wide)

## Usage

```bash
# From the project directory:
python run.py

# Or as a module:
python -m docker_stack_generator
```

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

## Generated Output


```
stacks/<env>/<stack-name>/
├── stack.yml          # environment variables and network config
├── compose.yml.j2     # Jinja2 Compose template for Docker Swarm
├── README.md          # auto-generated documentation
├── configs/.gitkeep
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
- [ ] Have a tab per service for the traefik labels and add these. "whoami" in this case needs to be swapped out with the service name. WHOAMI in the port variable needs to swapped with the service name in all caps.

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

- [ ] Add a tab per service for the Homepage labels. Group is custumizable. Name is the service name by default. Icon is the servicename by default. href needs whoami to be replaced by the service name. And add the service description at the description.

        # ---------------------------------------------------------------------
        # Homepage
        # ---------------------------------------------------------------------

        - homepage.group=Infrastructure
        - homepage.name=Whoami
        - homepage.icon=mdi-account-question
        - homepage.href=https://whoami.${DOMAIN}
        - homepage.description=Traefik whoami test service

