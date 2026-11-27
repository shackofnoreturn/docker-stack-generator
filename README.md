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
- **Semaphore deployments** - launch configured playbooks, monitor task status and execution output, and resume saved runs
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
- `age` command-line tool on PATH (required when generating or importing encrypted secrets)

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

Navigate using the main tabs. **Edit > Settings** (`Ctrl+,`) opens settings,
and **File > New Stack** (`Ctrl+N`) starts a fresh stack. There is no separate
navigation toolbar or Settings tab.

## Stack Monitoring

Open **Monitoring** to see all stacks and services saved in **Stack Library**,
across environments. Save or import stack definitions first; this is not a Docker
Swarm inventory connection and does not inspect containers, replica counts, or
stacks that exist only on disk. **Refresh stacks** picks up library changes.

Endpoints default to `https://<service-name>.<domain>` once you enter the domain.
Homepage URLs and simple Traefik `Host` rules take precedence when configured.
Automatically derived URLs follow domain changes; saved endpoint overrides remain
unchanged. Set the domain field to resolve `${DOMAIN}`, or select a service and use
**Edit endpoint** (also available by double-clicking). Use a dedicated health URL
where possible (for example, add `/dashboard/` for Traefik). Explicitly saved
blank endpoints disable that service's check and keep the stack
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
credential. Endpoint changes leave the old encrypted credential unused; use
**View vault** to inspect or delete it without restoring the old URL.

### Encrypted credential vault

**Unlock / create vault** creates or unlocks the application's local encrypted
vault at `~/.config/docker-stack-generator/credentials.vault`. On creation choose
and confirm a master password of at least 12 characters. The password is not saved
and must be entered after each application launch. **There is no password recovery**:
keep a secure backup of both your vault and master password. Library backups and
exports do not include the vault or monitoring preferences.

**View vault** prompts for the master password if locked and lists all stored
entries, including credentials for old endpoints. Select an entry to inspect its
username and masked password/token. Use **Show password / token** to explicitly
reveal it, **Edit credentials** to update it, or **Delete entry** to remove it
after confirmation. Monitoring checks pause while the vault viewer is open;
closing it clears displayed secrets and invalidates previous monitoring results.

The vault uses authenticated encryption with a salted password-derived key.
Credentials are decrypted in memory only while unlocked; locking or closing drops
the application's key and credential references (Python cannot guarantee memory
erasure). Locked checks run without stored credentials and may show authorization
failures. **Lock vault** clears displayed results, but does not stop periodic
unauthenticated checks. Wait for in-flight checks before locking or closing.
The vault protects data at rest, not against malware or another process controlling
your running account. Docker secret contents also use this vault; saved stack
definitions and exports include secret names only, never their values.

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
are left untouched. Existing shared templates are not rewritten or added.
Encrypted `.age` secrets are generated from vault values; corresponding legacy
plaintext files are deleted after successful encryption, and tracked deletions
are included in the generated commit. Git ignore rules apply to new `.age` files.
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
The text editor uses a monospace font and syntax highlighting for common
configuration syntax, including YAML/Jinja2, JSON, INI, and key/value settings.
Double-click a file path to rename it or move it into a relative subdirectory.
Set **Destination subdirectory** before importing or creating files to place
them in a nested folder; parent directories are created automatically. The
separate Subdirectories panel has been removed. Empty directories in previously
saved definitions are still preserved when editing and generating those stacks.

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

## Encrypted Docker Secrets

In **Edit > Settings > Age encryption**, configure the **recipients / public key
file** (default `~/.config/age/key.pub`) and **identity / private key file**
(default `~/.config/age/key.pem`). Only file paths are saved in settings; keep the
identity outside your stack repository. Install `age` on PATH. The recipients
file is read with `age -R`, just like the shell script; it must contain supported
age recipients, not arbitrary PEM certificate data. The identity is only needed
when importing an encrypted `.age` file. Keys must be usable without interactive
terminal prompts; failures are reported in the application.

In a service's **Secrets** tab, add an extensionless secret name using letters,
numbers, underscores, or hyphens, select it, and click **Set / edit value (vault)**.
Unlock or create the existing master-password vault, then enter text or import a
file. **Show / edit secret text** explicitly reveals the value for editing.
Import supports plaintext files and `.age` files (decrypted using the configured
identity). Imported bytes are preserved exactly unless you edit them as UTF-8;
binary values can be imported and replaced without displaying them as text.

Saving a value writes it immediately to the encrypted vault, independently of
whether you later cancel the service dialog. **Values are shared globally by
secret name**, across services, stacks, and environments: use different names
when values must differ. Removing a name from a service does not delete the vault
value. Use **Monitoring > View vault** to inspect, edit, or delete global secrets
as well as monitoring credentials.

Both generator and library generation prompt to unlock the vault if necessary.
Missing or empty values block generation; no placeholders are generated. All
values are encrypted in memory before stack generation writes any files, with
plaintext passed to age through standard input, not a temporary file. Output is
`secrets/<name>.age`, never `<name>.txt` or extensionless plaintext. Age encryption
is randomized, so regenerating secrets can produce new ciphertext and commits
even when values have not changed. The `secrets` directory gets a `.gitkeep` only
when no secrets are configured.

After replacement ciphertext files are successfully written, generation removes
matching legacy `secrets/<name>` and `secrets/<name>.txt` files for the configured
secrets. Unrelated files are not deleted. Imported source files elsewhere are not
deleted either. **This does not erase plaintext from Git history or backups**;
rotate any secret that was previously published. The application never puts
vault contents or private keys in library exports or generated commits.
Keep a secure separate backup of your vault, age identity, and master password.

Compose continues to reference external Docker Swarm secrets by name. The
deployment playbook must decrypt the `.age` files and create/update the external
Swarm secrets; generation does not upload values directly to Docker.

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

Service endpoint checks are managed exclusively in the main **Monitoring** tab.
Deployments does not run automatic service checks after a successful playbook;
its remaining space is dedicated to Semaphore execution output. A successful
playbook does not guarantee service health. Use **Monitoring** to check service
endpoints independently.

### Additional Playbooks

Targets can also map actions such as **Remove**, **Backup**, **Restore**, or **Move**
to your own Semaphore templates, with extra survey variables for their inputs.
Every action requires confirmation and uses the same status/log monitoring.
The application does not supply these playbooks or implement backup/restore
semantics itself.

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
└── secrets/          # <name>.age files, or .gitkeep when no secrets are configured

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

- [ ] When I type arr-media to create a stack the stack name automatically turns into arr_media. Which is troublesome because my stack names need to use hyphens ("-"). So even when I name the stack Arr Media. The space should be a "-" not a "_"
