"""Encrypt secret bytes with age without writing plaintext to disk."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


_TIMEOUT_SECONDS = 30
_AGE_HEADER = b"age-encryption.org/v1\n"
_SECRET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class AgeError(ValueError):
    """An invalid secret or a safely reported age operation failure."""


def validate_secret_name(name: str) -> str:
    """Return a logical, extensionless Docker secret basename."""
    if not isinstance(name, str) or _SECRET_NAME.fullmatch(name) is None:
        raise AgeError(
            "Secret names must start with an ASCII letter or digit and contain "
            "only ASCII letters, digits, underscores, or hyphens (no extension)."
        )
    return name


def secret_key(name: str) -> str:
    """Return the vault key shared by every service using this secret name."""
    return json.dumps(["secret", validate_secret_name(name)])


def _key_file(filename: str, role: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise AgeError(f"Configure an age {role} file before continuing.")
    try:
        path = Path(os.path.expandvars(filename)).expanduser()
        exists = path.is_file()
    except (OSError, ValueError, RuntimeError):
        raise AgeError(f"Cannot access the age {role} file. Check its path and permissions.") from None
    if not exists:
        raise AgeError(f"The age {role} file does not exist or is not a regular file. Check its path.")
    return str(path)


def _run_age(data: bytes, arguments: list[str], operation: str) -> bytes:
    executable = shutil.which("age")
    if executable is None:
        raise AgeError("The age executable was not found. Install age and add it to PATH.")
    try:
        result = subprocess.run(
            [executable, *arguments],
            input=data,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        raise AgeError(
            f"Age {operation} timed out after {_TIMEOUT_SECONDS} seconds. "
            "Check the configured key file and try again."
        ) from None
    except OSError:
        raise AgeError(
            f"Could not run age for {operation}. Check its installation and file permissions."
        ) from None
    if result.returncode != 0:
        raise AgeError(
            f"Age {operation} failed. Check the configured "
            f"{'recipients' if operation == 'encryption' else 'identity'} file "
            "and its permissions."
        )
    return result.stdout


def encrypt_secret(contents: bytes, recipients_file: str) -> bytes:
    """Encrypt nonempty bytes to age's native binary format using stdin."""
    if not isinstance(contents, bytes) or not contents:
        raise AgeError("Secret contents must be nonempty bytes.")
    recipients = _key_file(recipients_file, "recipients")
    ciphertext = _run_age(contents, ["-R", recipients], "encryption")
    if not ciphertext.startswith(_AGE_HEADER):
        raise AgeError("Age encryption returned invalid output; expected native age ciphertext.")
    return ciphertext


def decrypt_secret(ciphertext: bytes, identity_file: str) -> bytes:
    """Decrypt nonempty ciphertext, returning plaintext only on success."""
    if not isinstance(ciphertext, bytes) or not ciphertext:
        raise AgeError("Ciphertext must be nonempty bytes.")
    identity = _key_file(identity_file, "identity")
    return _run_age(ciphertext, ["--decrypt", "-i", identity], "decryption")
