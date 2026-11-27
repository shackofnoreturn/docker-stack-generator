import json
import os
from pathlib import Path
import shutil
import subprocess
import traceback
from unittest.mock import Mock

import pytest

from docker_stack_generator import age_secrets
from docker_stack_generator.age_secrets import (
    AgeError,
    decrypt_secret,
    encrypt_secret,
    secret_key,
    validate_secret_name,
)


PLAINTEXT = b"private secret\n\x00\xff"
CIPHERTEXT = b"age-encryption.org/v1\nmock encrypted bytes"


@pytest.mark.parametrize("name", ["password", "A", "1", "db_password", "api-token_2"])
def test_secret_names_and_globally_shared_keys(name):
    assert validate_secret_name(name) == name
    assert json.loads(secret_key(name)) == ["secret", name]
    assert secret_key(name) == secret_key(name)
    assert secret_key(name) != secret_key(name + "other")


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", ".hidden", "name.age", "name.txt", "a.b", "../name",
     "a/b", "a\\b", "-name", "_name", "a b", "a\n", "a\x00", "é", None, 1],
)
def test_invalid_secret_names(name):
    with pytest.raises(ValueError) as error:
        validate_secret_name(name)
    assert isinstance(error.value, AgeError)
    with pytest.raises(AgeError):
        secret_key(name)


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "key file.txt"
    path.write_text("test key", encoding="utf-8")
    return path


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(age_secrets.shutil, "which", lambda name: "age-executable")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, CIPHERTEXT, b""))
    monkeypatch.setattr(age_secrets.subprocess, "run", run)
    return run


def test_encrypt_stdin_native_output_and_safe_process_options(key_file, runner):
    assert encrypt_secret(PLAINTEXT, str(key_file)) == CIPHERTEXT
    runner.assert_called_once_with(
        ["age-executable", "-R", str(key_file)],
        input=PLAINTEXT,
        capture_output=True,
        timeout=30,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert PLAINTEXT not in runner.call_args.args[0]


def test_decrypt_returns_bytes_only_on_success(key_file, runner):
    runner.return_value = subprocess.CompletedProcess([], 0, PLAINTEXT, b"")
    assert decrypt_secret(CIPHERTEXT, str(key_file)) == PLAINTEXT
    assert runner.call_args.args == (
        ["age-executable", "--decrypt", "-i", str(key_file)],
    )
    assert runner.call_args.kwargs["input"] == CIPHERTEXT
    assert runner.call_args.kwargs["capture_output"] is True
    assert runner.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("operation", [encrypt_secret, decrypt_secret])
@pytest.mark.parametrize("value", [b"", "", None, bytearray(b"secret")])
def test_empty_or_nonbyte_input_rejected(operation, value, key_file, runner):
    with pytest.raises(AgeError, match="nonempty bytes"):
        operation(value, str(key_file))
    runner.assert_not_called()


@pytest.mark.parametrize("operation", [encrypt_secret, decrypt_secret])
@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "null"])
def test_invalid_key_paths(operation, kind, tmp_path, runner):
    path = {
        "missing": str(tmp_path / "missing"),
        "directory": str(tmp_path),
        "empty": "",
        "null": "invalid\x00path",
    }[kind]
    with pytest.raises(AgeError, match="file"):
        operation(PLAINTEXT, path)
    runner.assert_not_called()


@pytest.mark.parametrize("operation", [encrypt_secret, decrypt_secret])
def test_paths_expand_home_and_environment(operation, key_file, runner, monkeypatch):
    monkeypatch.setenv("AGE_TEST_KEYS", str(key_file.parent))
    operation(PLAINTEXT, "$AGE_TEST_KEYS/" + key_file.name)
    assert runner.call_args.args[0][-1] == str(key_file)
    monkeypatch.setattr(Path, "home", lambda: key_file.parent)
    monkeypatch.setenv("USERPROFILE" if os.name == "nt" else "HOME", str(key_file.parent))
    operation(PLAINTEXT, "~/" + key_file.name)
    assert runner.call_args.args[0][-1] == str(key_file)


@pytest.mark.parametrize("operation", [encrypt_secret, decrypt_secret])
def test_missing_age_actionable(operation, key_file, runner, monkeypatch):
    monkeypatch.setattr(age_secrets.shutil, "which", lambda name: None)
    with pytest.raises(AgeError, match="Install age.*PATH"):
        operation(PLAINTEXT, str(key_file))
    runner.assert_not_called()


@pytest.mark.parametrize("operation", [encrypt_secret, decrypt_secret])
@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_failures_never_expose_secret_output(operation, failure, key_file, runner, capsys):
    sensitive = "never-show-private-secret"
    if failure == "exit":
        runner.return_value = subprocess.CompletedProcess(
            [], 1, sensitive.encode(), sensitive.encode()
        )
    elif failure == "timeout":
        runner.side_effect = subprocess.TimeoutExpired(
            ["age", sensitive], 30, output=sensitive.encode(), stderr=sensitive.encode()
        )
    else:
        runner.side_effect = OSError(sensitive)
    with pytest.raises(AgeError) as error:
        operation(PLAINTEXT, str(key_file))
    rendered = "".join(traceback.format_exception(error.value))
    assert sensitive not in rendered
    assert "age" in str(error.value).lower()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("output", [b"", b"not ciphertext", b"-----BEGIN AGE ENCRYPTED FILE-----"])
def test_encryption_rejects_non_native_output(output, key_file, runner):
    runner.return_value = subprocess.CompletedProcess([], 0, output, b"")
    with pytest.raises(AgeError, match="native age ciphertext"):
        encrypt_secret(PLAINTEXT, str(key_file))


@pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None,
    reason="Optional age and age-keygen executables are not installed",
)
def test_real_age_roundtrip(tmp_path):
    generated = subprocess.run(
        [shutil.which("age-keygen")],
        capture_output=True,
        timeout=30,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    identity = tmp_path / "identity.txt"
    identity.write_bytes(generated.stdout)
    recipient = next(
        line.removeprefix("# public key: ")
        for line in generated.stdout.decode("utf-8").splitlines()
        if line.startswith("# public key: ")
    )
    recipients = tmp_path / "recipients.txt"
    recipients.write_text(recipient + "\n", encoding="utf-8")
    ciphertext = encrypt_secret(PLAINTEXT, str(recipients))
    assert ciphertext.startswith(b"age-encryption.org/v1\n")
    assert PLAINTEXT not in ciphertext
    assert decrypt_secret(ciphertext, str(identity)) == PLAINTEXT
