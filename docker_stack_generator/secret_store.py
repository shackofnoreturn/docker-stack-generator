"""Read globally named secret values from the encrypted application vault."""
import base64
import binascii

from docker_stack_generator.age_secrets import secret_key
from docker_stack_generator.credential_vault import CredentialVault, VaultError


def read_secret(vault: CredentialVault, name: str) -> bytes:
    value = vault.get(secret_key(name))
    if value is None or value.get("kind") != "secret":
        raise VaultError(f"No value stored for secret '{name}'. Set its value in the service's Secrets tab.")
    try:
        contents = base64.b64decode(value.get("contents", ""), validate=True)
    except (ValueError, binascii.Error):
        raise VaultError(f"Invalid stored value for secret '{name}'. Import or set it again.") from None
    if not contents:
        raise VaultError(f"Secret '{name}' is empty. Set a nonempty value before generating.")
    return contents
