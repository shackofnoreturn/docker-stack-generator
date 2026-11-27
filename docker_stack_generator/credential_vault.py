"""Encrypted local storage for small collections of credentials."""

import base64
import binascii
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class VaultError(Exception):
    """A vault operation failed without exposing credential data."""


_VERSION = 1
_ITERATIONS = 600_000
_SALT_LENGTH = 16
_ENVELOPE_KEYS = {"version", "iterations", "salt", "ciphertext"}
_IS_WINDOWS = os.name == "nt"
_REPLACE_ATTEMPTS = 6
_REPLACE_RETRY_ERRORS = {5, 32, 33}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _decode_json(data: bytes) -> object:
    return json.loads(data, object_pairs_hook=_unique_object)


def _derive_fernet(password: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Invalid base64 value")
    return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)


def _os_error_detail(error: OSError) -> str:
    codes = []
    if error.errno is not None:
        codes.append(f"errno {error.errno}")
    winerror = getattr(error, "winerror", None)
    if winerror is not None:
        codes.append(f"winerror {winerror}")
    return f" ({', '.join(codes)})" if codes else ""


def _replace_atomically(source: str, destination: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if (
                not _IS_WINDOWS
                or getattr(error, "winerror", None) not in _REPLACE_RETRY_ERRORS
                or attempt == _REPLACE_ATTEMPTS - 1
            ):
                raise
            time.sleep(0.02 * (2 ** attempt))


def _validate_entries(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ValueError("Invalid vault contents")
    entries: dict[str, dict[str, str]] = {}
    for key, credentials in value.items():
        if not isinstance(key, str) or not isinstance(credentials, dict):
            raise ValueError("Invalid vault entry")
        if any(
            not isinstance(name, str) or not isinstance(secret, str)
            for name, secret in credentials.items()
        ):
            raise ValueError("Invalid vault credential")
        entries[key] = dict(credentials)
    return entries


class CredentialVault:
    """Password-protected credential storage with atomic updates."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else (
            Path.home() / ".config" / "docker-stack-generator" / "credentials.vault"
        )
        self._fernet: Fernet | None = None
        self._salt: bytes | None = None
        self._entries: dict[str, dict[str, str]] | None = None

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def unlocked(self) -> bool:
        return self._entries is not None

    def unlock(self, master_password: str) -> None:
        self.lock()
        if not isinstance(master_password, str) or not master_password:
            raise VaultError("A nonempty master password is required")
        try:
            if self.exists:
                envelope = _decode_json(self.path.read_bytes())
                if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_KEYS:
                    raise ValueError("Invalid vault envelope")
                if type(envelope["version"]) is not int or envelope["version"] != _VERSION:
                    raise ValueError("Unsupported vault version")
                if type(envelope["iterations"]) is not int or envelope["iterations"] != _ITERATIONS:
                    raise ValueError("Unsupported vault work factor")
                salt = _decode_base64(envelope["salt"])
                token = envelope["ciphertext"]
                token_bytes = _decode_base64(token)
                if len(salt) != _SALT_LENGTH:
                    raise ValueError("Invalid salt length")
                if (
                    len(token_bytes) < 73
                    or (len(token_bytes) - 57) % 16 != 0
                    or token_bytes[0] != 0x80
                ):
                    raise ValueError("Invalid ciphertext length or format")
                fernet = _derive_fernet(master_password, salt)
                entries = _validate_entries(_decode_json(fernet.decrypt(token.encode("ascii"))))
            else:
                if len(master_password) < 12:
                    raise VaultError("A new vault requires a master password of at least 12 characters")
                salt = os.urandom(_SALT_LENGTH)
                fernet = _derive_fernet(master_password, salt)
                entries = {}
                self._persist(entries, fernet, salt)
        except VaultError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, binascii.Error, InvalidToken):
            raise VaultError("Unable to unlock credential vault") from None
        self._fernet = fernet
        self._salt = salt
        self._entries = entries

    def lock(self) -> None:
        self._fernet = None
        self._salt = None
        self._entries = None

    def _require_unlocked(self) -> tuple[dict[str, dict[str, str]], Fernet, bytes]:
        if self._entries is None or self._fernet is None or self._salt is None:
            raise VaultError("Credential vault is locked")
        return self._entries, self._fernet, self._salt

    def get(self, key: str) -> dict[str, str] | None:
        entries, _, _ = self._require_unlocked()
        if not isinstance(key, str):
            raise VaultError("Credential key must be a string")
        value = entries.get(key)
        return dict(value) if value is not None else None

    def set(self, key: str, value: dict[str, str]) -> None:
        entries, fernet, salt = self._require_unlocked()
        if not isinstance(key, str) or not isinstance(value, dict) or any(
            not isinstance(name, str) or not isinstance(secret, str)
            for name, secret in value.items()
        ):
            raise VaultError("Credential key and values must be strings")
        updated = dict(entries)
        updated[key] = dict(value)
        self._persist(updated, fernet, salt)
        self._entries = updated

    def delete(self, key: str) -> None:
        entries, fernet, salt = self._require_unlocked()
        if not isinstance(key, str):
            raise VaultError("Credential key must be a string")
        if key not in entries:
            return
        updated = dict(entries)
        del updated[key]
        self._persist(updated, fernet, salt)
        self._entries = updated

    def _persist(self, entries: dict[str, dict[str, str]], fernet: Fernet, salt: bytes) -> None:
        operation = "serialize"
        try:
            plaintext = json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            envelope = {
                "version": _VERSION,
                "iterations": _ITERATIONS,
                "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
                "ciphertext": fernet.encrypt(plaintext).decode("ascii"),
            }
            data = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
            operation = "create directory"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            operation = "create temporary file"
            fd, temporary_path = tempfile.mkstemp(prefix=".credentials-", dir=self.path.parent)
            try:
                operation = "write temporary file"
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                operation = "replace vault"
                _replace_atomically(temporary_path, self.path)
            finally:
                if os.path.exists(temporary_path):
                    prior_error = sys.exception()
                    try:
                        os.unlink(temporary_path)
                    except OSError as cleanup_error:
                        if isinstance(prior_error, OSError):
                            raise VaultError(
                                f"Unable to save credential vault: {operation}"
                                f"{_os_error_detail(prior_error)}; remove temporary file"
                                f"{_os_error_detail(cleanup_error)}"
                            ) from None
                        operation = "remove temporary file"
                        raise
        except OSError as error:
            raise VaultError(
                f"Unable to save credential vault: {operation}{_os_error_detail(error)}"
            ) from None
        except (ValueError, TypeError, UnicodeError):
            raise VaultError("Unable to save credential vault") from None
