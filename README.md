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
- **Semaphore deployments** - launch configured playbooks, monitor task status and output, resume saved runs, and check service endpoint reachability
- **Preview** — live `stack.yml` and `compose.yml.j2` preview before writing any files
- **Generate & Sync** — generate a stack, commit generated files in the destination repository, and push to its upstream branch
- **Stack monitoring** - periodically check HTTP(S) endpoints for saved stacks and services, with encrypted per-service authentication
- **Recent stacks** — quick access to previously generated stacks
- **Same output format** as the original `new-stack.sh`

## Requirements

- Python 3.11+
- `python-pyqt6` (install via `sudo pacman -S python-pyqt6` on Arch)
- `requests`, `PyYAML`, and `Jinja2`
- `cryptography` (encrypted local credential vault)

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

## Stack Monitoring

Open **Monitoring** to see all stacks and services saved in **Stack Library**,
across environments. Save or import stack definitions first; this is not a Docker
Swarm inventory connection and does not inspect containers, replica counts, or
stacks that exist only on disk. **Refresh stacks** picks up library changes.

Homepage URLs and simple Traefik `Host` rules provide suggested endpoints.
Set the domain field to resolve `${DOMAIN}`, or select a service and use
**Edit endpoint** (also available by double-clicking). Use a dedicated health URL
where possible. Blank endpoints disable that service's check and keep the stack
incomplete rather than falsely healthy. URLs and polling preferences are stored
locally in `~/.config/docker-stack-generator/monitoring.json`; do not put passwords
or tokens in URL paths or query strings.

**Check now** runs one check of all services. **Start monitoring** repeats checks
while the application is open, including when another tab is selected, at the
configured interval (10-3600 seconds). Automatic monitoring is paused on launch.
Checks do not overlap. The tree shows each service's result, timestamp, and an
aggregate stack state; a stack is healthy only when all its services return 2xx.
HTTP errors, network/TLS errors, redirects, authorization failures, missing
endpoints, and unchecked services are distinguished. Redirects are not followed:
configure their final health URL instead. A 2xx login page cannot prove that the
underlying application works, so use an appropriate authenticated health endpoint.
TLS verification stays enabled; implicit `.netrc` credentials and environment
proxy settings are not used by monitoring.

For HTTP 401/403, select the service and choose **Set credentials / retry**.
Basic authentication and bearer tokens are supported (not browser/SSO form login).
Credentials are sent only over HTTPS, only to the exact endpoint they were saved
for; changing that URL requires configuring credentials again. Saving credentials
immediately retries checks. **Remove credentials** deletes the selected endpoint's
credential. Endpoint changes leave the old encrypted credential unused; to remove
it, temporarily restore the old endpoint and use **Remove credentials**.

### Encrypted credential vault

**Unlock / create vault** creates or unlocks the application's local encrypted
vault at `~/.config/docker-stack-generator/credentials.vault`. On creation choose
and confirm a master password of at least 12 characters. The password is not saved
and must be entered after each application launch. **There is no password recovery**:
keep a secure backup of both your vault and master password. Library backups and
exports do not include the vault or monitoring preferences.

The vault uses authenticated encryption with a salted password-derived key.
Credentials are decrypted in memory only while unlocked; locking or closing drops
the application's key and credential references (Python cannot guarantee memory
erasure). Locked checks run without stored credentials and may show authorization
failures. **Lock vault** clears displayed results, but does not stop periodic
unauthenticated checks. Wait for in-flight checks before locking or closing.
The vault protects data at rest, not against malware or another process controlling
your running account. Existing generated secret files and stack exports are
unchanged; this feature does not yet move Docker secrets into the vault.

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

## Generate & Sync

Click **Generate Stack** to generate locally as before, or use its dropdown arrow
and choose **Generate & Sync** to also commit and push in the repository containing
the configured stacks root. Git must be installed, the destination must be on a
branch with an existing remote upstream, and Git author identity and authentication
must already be configured. Git credentials are not stored by this application;
interactive credential prompts are disabled.

Only files written by this generation are included in the commit, including new
shared templates and `.gitkeep` files. Unrelated files and unrelated staged changes
are left untouched. Existing secret files and shared templates are not rewritten
or added; newly generated secret stubs are included unless ignored by Git.
Git ignore rules are respected for untracked files. Review service configuration
contents before generating: generated configuration files may contain sensitive data.
Commits are clearly attributed, for example:
`Docker Stack Generator: generate stack production/redis`.

Sync pushes the current destination branch to its configured upstream, including
any earlier unpushed commits on that branch. It never pulls, merges, rebases, or
force-pushes. Invalid repository setup is reported before generation. If commit or
push fails after generation, the files remain generated and saved to the library,
and the error is displayed. Local commits are retained on push failure; resolve
the problem and retry **Generate & Sync** (allowing overwrite), or push manually.
If there are no generated changes, sync still attempts to push pending commits.

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

## Semaphore Deployments

The **Deployments** tab beside Template Library uses the same API flow as the
reference files in `docker_stack_generator/semaphore/`. It does not require Bash,
curl, jq, a local Docker socket, or a local Ansible installation.

### Connection and Targets

1. Enter your Semaphore server URL and either a username/password or an API token.
        Click **Connect** to verify authentication. Credentials are kept in memory only,
        not saved to settings or run history. Prefer HTTPS; HTTP requires explicit
        approval because it sends credentials and logs unencrypted. TLS certificate
        verification is enabled.
2. Open **Targets** and verify the project and template IDs for each environment.
        Defaults follow the supplied scripts: project `1`, development `41`, testing
        `42`, production `43`, management `44`. The template's inventory and playbook
        decide which Swarm is used; an environment label alone does not choose a Swarm.
3. Select a stack from the saved library or generated directories under your
        configured stacks root and environment. You can also enter a repository stack
        name directly. Click **Refresh stacks** after saving or importing definitions.
4. Click **Run playbook**, check the confirmation, and approve the selected server,
        environment, project, template, and stack.

The request supplies `stack_name` as a survey variable inside Semaphore's
JSON-encoded `environment` string. Optional extra survey variables can be set per
target as a JSON object; the selected stack always wins over a `stack_name`
override. Extra variables are saved in plain text, so keep secrets in Semaphore's
credential/environment configuration instead.

Semaphore runs its configured playbook against its configured stack repository.
The app does **not** upload, commit, or push local definitions when deploying.
Generate, commit, and push changes to the stack-definition repository first, and
ensure the playbook checks out the correct branch and environment.

### Status and Diagnostics

Task status and actual execution output refresh in the background every three
seconds. The app retrieves formatted output and falls back to raw output, exposing
playbook failures and task messages rather than treating a started task as a
successful deployment. **Open in Semaphore** opens the remote task details.

The last 30 task references are saved locally without credentials or logs.
Reconnect after reopening the app, then use **Resume selected run**. Use **Attach
task ID** to monitor an existing task in the selected project, including after an
ambiguous start response. Before retrying a failed start request, check Semaphore
history: a network interruption can occur after Semaphore accepted the task.

**Pause monitoring** only pauses local monitoring; it never cancels the remote
playbook. Monitoring also pauses after 30 minutes or five consecutive polling
errors; **Refresh task** reconnects and resumes it. Closing the app leaves remote
tasks running. Wait for in-flight network requests to finish before closing.

### Service Reachability

Endpoint checks run after a successful **Deploy** action when enabled, or via
**Check services**. Saved service Homepage URLs are suggested automatically;
default Traefik URLs are suggested where no custom router rule is present.
Otherwise enter one HTTP(S) URL per line, optionally as `service=https://host/health`.
Set **DOMAIN** to resolve `${DOMAIN}` in suggested URLs. Endpoint lists are saved
per environment/stack. Non-HTTP services need an appropriate HTTP health endpoint
or separate checks in the playbook.

Checks retry every ten seconds for approximately three minutes while services
start. The table distinguishes successful HTTP responses, authentication-required
responses, redirects with unverified destinations, TLS errors, HTTP errors, and
connection/DNS/timeout failures. You can pause checks, adjust URLs, and retry.

These are checks **from the machine running this app**, not from inside the Swarm.
A reachable endpoint does not verify replica counts, container health, or application
correctness, and a successful playbook does not guarantee endpoint readiness.
Prefer service-specific health URLs; use the playbook output and Semaphore task
details for deployment diagnostics. No live Swarm inspection is performed.

### Additional Playbooks

Targets can also map actions such as **Remove**, **Backup**, **Restore**, or **Move**
to your own Semaphore templates, with extra survey variables for their inputs.
Every action requires confirmation and uses the same status/log monitoring.
Only actions named **Deploy** start automatic endpoint checks. The application
does not supply these playbooks or implement backup/restore semantics itself.

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
