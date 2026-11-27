import base64
import errno
import json
import os

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import docker_stack_generator.credential_vault as vault_module
from docker_stack_generator.credential_vault import CredentialVault, VaultError


PASSWORD = "a long master password"


@pytest.fixture
def vault(tmp_path):
    return CredentialVault(tmp_path / "nested" / "credentials.vault")


def test_creation_password_and_locked_operations(vault):
    assert not vault.exists
    assert not vault.unlocked
    for password in ("", "too short"):
        with pytest.raises(VaultError):
            vault.unlock(password)
        assert not vault.exists
        assert not vault.unlocked
    for operation in (lambda: vault.get("a"), lambda: vault.set("a", {}), lambda: vault.delete("a")):
        with pytest.raises(VaultError, match="locked"):
            operation()
    vault.unlock(PASSWORD)
    assert vault.exists
    assert vault.unlocked
    vault.lock()
    vault.lock()
    assert not vault.unlocked


def test_updates_deletes_copies_and_reopen(vault):
    vault.unlock(PASSWORD)
    original = {"username": "alice", "password": "top-secret-value"}
    vault.set("registry.example", original)
    original["password"] = "changed"
    result = vault.get("registry.example")
    assert result == {"username": "alice", "password": "top-secret-value"}
    result["password"] = "changed again"
    assert vault.get("registry.example")["password"] == "top-secret-value"
    vault.set("other", {"token": "another-secret"})
    vault.set("other", {"token": "replacement"})
    vault.delete("missing")
    vault.delete("registry.example")
    vault.lock()
    vault.unlock(PASSWORD)
    assert vault.get("registry.example") is None
    assert vault.get("other") == {"token": "replacement"}


def test_list_keys_requires_unlock_and_returns_independent_list(vault):
    with pytest.raises(VaultError, match="locked"):
        vault.keys()
    vault.unlock(PASSWORD)
    vault.set("second", {"token": "two"})
    vault.set("first", {"token": "one"})
    keys = vault.keys()
    assert keys == ["first", "second"]
    keys.clear()
    assert vault.keys() == ["first", "second"]
    vault.lock()
    with pytest.raises(VaultError, match="locked"):
        vault.keys()


def test_disk_contains_no_plaintext_and_wrong_password_stays_locked(vault):
    vault.unlock(PASSWORD)
    vault.set("private-registry", {"username": "secret-user", "password": "secret-password"})
    data = vault.path.read_bytes()
    for secret in (PASSWORD, "private-registry", "secret-user", "secret-password"):
        assert secret.encode() not in data
    with pytest.raises(VaultError) as error:
        vault.unlock("incorrect password")
    assert PASSWORD not in str(error.value)
    assert not vault.unlocked
    with pytest.raises(VaultError, match="locked"):
        vault.get("private-registry")
    vault.unlock(PASSWORD)
    assert vault.get("private-registry")["password"] == "secret-password"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda envelope: envelope.update(version=2),
        lambda envelope: envelope.update(version=True),
        lambda envelope: envelope.update(iterations=600_001),
        lambda envelope: envelope.update(iterations=10**100),
        lambda envelope: envelope.update(salt=base64.urlsafe_b64encode(b"short").decode()),
        lambda envelope: envelope.update(ciphertext="invalid!"),
        lambda envelope: envelope.update(ciphertext=base64.urlsafe_b64encode(b"x" * 73).decode()),
        lambda envelope: envelope.update(extra="unexpected"),
        lambda envelope: envelope.pop("salt"),
    ],
)
def test_invalid_envelope_rejected(vault, mutation):
    vault.unlock(PASSWORD)
    vault.lock()
    envelope = json.loads(vault.path.read_bytes())
    mutation(envelope)
    vault.path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    assert not vault.unlocked


@pytest.mark.parametrize("data", [b"not json", b'{"version":1,"version":1}', b"\xff"])
def test_corrupt_json_rejected(vault, data):
    vault.path.parent.mkdir(parents=True)
    vault.path.write_bytes(data)
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    assert not vault.unlocked
    assert vault.path.read_bytes() == data


def test_ciphertext_tampering_rejected(vault):
    vault.unlock(PASSWORD)
    vault.set("service", {"token": "secret"})
    vault.lock()
    envelope = json.loads(vault.path.read_bytes())
    token = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
    token[-1] ^= 1
    envelope["ciphertext"] = base64.urlsafe_b64encode(token).decode()
    vault.path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    assert not vault.unlocked


@pytest.mark.parametrize("contents", [[], {"key": "not a dictionary"}, {"key": {"name": 1}}, b'{"key":{},"key":{}}'])
def test_authenticated_invalid_contents_rejected(vault, contents):
    vault.unlock(PASSWORD)
    vault.lock()
    envelope = json.loads(vault.path.read_bytes())
    salt = base64.urlsafe_b64decode(envelope["salt"])
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(PASSWORD.encode())))
    plaintext = contents if isinstance(contents, bytes) else json.dumps(contents).encode()
    envelope["ciphertext"] = fernet.encrypt(plaintext).decode()
    vault.path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    assert not vault.unlocked


@pytest.mark.parametrize("operation", ["set", "delete"])
def test_failed_write_is_transactional(vault, monkeypatch, operation):
    vault.unlock(PASSWORD)
    vault.set("service", {"token": "original"})
    before = vault.path.read_bytes()

    def fail_replace(source, destination):
        raise PermissionError(errno.EACCES, "sensitive filesystem detail")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(VaultError) as error:
            if operation == "set":
                vault.set("service", {"token": "new-secret"})
            else:
                vault.delete("service")
    assert "sensitive filesystem detail" not in str(error.value)
    assert f"replace vault (errno {errno.EACCES})" in str(error.value)
    assert vault.get("service") == {"token": "original"}
    assert vault.path.read_bytes() == before
    assert list(vault.path.parent.iterdir()) == [vault.path]
    vault.lock()
    vault.unlock(PASSWORD)
    assert vault.get("service") == {"token": "original"}


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_locked_destination_fails_safely_after_retries(vault):
    import ctypes
    from ctypes import wintypes

    vault.unlock(PASSWORD)
    before = vault.path.read_bytes()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = create_file(str(vault.path), 0x80000000, 0, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(VaultError, match=r"replace vault .*winerror") as error:
            vault.set("service", {"token": "new-secret"})
        assert "new-secret" not in str(error.value)
        assert vault.get("service") is None
    finally:
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    assert vault.path.read_bytes() == before
    assert list(vault.path.parent.iterdir()) == [vault.path]


def test_failed_creation_remains_locked(vault, monkeypatch):
    def fail_replace(source, destination):
        raise OSError("write failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    assert not vault.unlocked
    assert not vault.exists
    assert list(vault.path.parent.iterdir()) == []


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_transient_windows_replace_errors_retry_atomically(vault, monkeypatch, winerror):
    vault.unlock(PASSWORD)
    replace = os.replace
    attempts = []
    delays = []

    def transient_replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) <= 2:
            error = PermissionError(errno.EACCES, "sensitive detail")
            error.winerror = winerror
            raise error
        replace(source, destination)

    monkeypatch.setattr(vault_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(os, "replace", transient_replace)
    monkeypatch.setattr(vault_module.time, "sleep", delays.append)
    vault.set("service", {"token": "secret"})
    assert len(attempts) == 3
    assert all(attempt == attempts[0] for attempt in attempts)
    assert delays == [0.02, 0.04]
    vault.lock()
    vault.unlock(PASSWORD)
    assert vault.get("service") == {"token": "secret"}


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_windows_replace_retry_exhaustion_is_transactional(vault, monkeypatch, winerror):
    vault.unlock(PASSWORD)
    before = vault.path.read_bytes()
    attempts = []
    delays = []

    def denied_replace(source, destination):
        attempts.append((source, destination))
        error = PermissionError(errno.EACCES, "sensitive detail")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(vault_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(os, "replace", denied_replace)
    monkeypatch.setattr(vault_module.time, "sleep", delays.append)
    with pytest.raises(VaultError, match=rf"replace vault .*winerror {winerror}") as error:
        vault.set("service", {"token": "secret"})
    assert "secret" not in str(error.value)
    assert len(attempts) == 6
    assert all(attempt == attempts[0] for attempt in attempts)
    assert delays == [0.02, 0.04, 0.08, 0.16, 0.32]
    assert sum(delays) < 1
    assert vault.get("service") is None
    assert vault.path.read_bytes() == before
    assert list(vault.path.parent.iterdir()) == [vault.path]


@pytest.mark.parametrize(("windows", "winerror"), [(False, 5), (True, 123), (True, None)])
def test_other_replace_errors_are_not_retried(vault, monkeypatch, windows, winerror):
    vault.unlock(PASSWORD)
    before = vault.path.read_bytes()
    attempts = []
    delays = []

    def failed_replace(source, destination):
        attempts.append((source, destination))
        error = OSError(errno.EIO, "sensitive detail")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(vault_module, "_IS_WINDOWS", windows)
    monkeypatch.setattr(os, "replace", failed_replace)
    monkeypatch.setattr(vault_module.time, "sleep", delays.append)
    with pytest.raises(VaultError, match="replace vault"):
        vault.set("service", {"token": "secret"})
    assert len(attempts) == 1
    assert not delays
    assert vault.get("service") is None
    assert vault.path.read_bytes() == before
    assert list(vault.path.parent.iterdir()) == [vault.path]


def test_replace_and_cleanup_errors_are_both_reported(vault, monkeypatch):
    vault.unlock(PASSWORD)
    before = vault.path.read_bytes()

    def fail_replace(source, destination):
        raise PermissionError(errno.EACCES, "sensitive replace detail")

    def fail_unlink(path):
        raise PermissionError(errno.EPERM, "sensitive cleanup detail")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        patch.setattr(os, "unlink", fail_unlink)
        with pytest.raises(VaultError) as error:
            vault.set("service", {"token": "secret"})
    assert f"replace vault (errno {errno.EACCES})" in str(error.value)
    assert f"remove temporary file (errno {errno.EPERM})" in str(error.value)
    assert "sensitive" not in str(error.value)
    assert vault.get("service") is None
    assert vault.path.read_bytes() == before
    leftovers = [path for path in vault.path.parent.iterdir() if path != vault.path]
    assert len(leftovers) == 1
    assert b"secret" not in leftovers[0].read_bytes()
    leftovers[0].unlink()


def test_invalid_inputs_do_not_change_entries(vault):
    vault.unlock(PASSWORD)
    for key, value in ((1, {}), ("key", []), ("key", {"name": 1}), ("key", {1: "secret"})):
        with pytest.raises(VaultError):
            vault.set(key, value)
    with pytest.raises(VaultError):
        vault.get(1)
    with pytest.raises(VaultError):
        vault.delete(1)
    assert vault.get("key") is None
