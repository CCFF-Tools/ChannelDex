"""Replaceable secret references; credentials never enter settings or logs."""
from dataclasses import dataclass
import subprocess
import hashlib
import secrets
from .exceptions import CapabilityError

@dataclass(frozen=True)
class SecretReference:
    service: str
    account: str

class SecretStore:
    def get(self, reference: SecretReference) -> str: raise NotImplementedError
    def set(self, reference: SecretReference, secret: str) -> None: raise NotImplementedError

class KeychainSecretStore(SecretStore):
    def __init__(self, runner=subprocess.run): self.runner = runner
    def get(self, reference):
        try:
            result = self.runner(("security", "find-generic-password", "-w", "-s", reference.service, "-a", reference.account), check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CapabilityError("macOS Keychain secret unavailable") from exc
        secret = result.stdout.rstrip("\r\n")
        if not secret: raise CapabilityError("macOS Keychain returned an empty secret")
        return secret

    def set(self, reference: SecretReference, secret: str) -> None:
        """Create/update a login Keychain item without putting the secret in argv."""
        if not isinstance(secret, str) or not secret:
            raise ValueError("Keychain secret must not be empty")
        try:
            # With no argument after -w, security reads the password from its
            # prompt/input stream.  This keeps it out of process listings and
            # subprocess logs while -U makes updates idempotent.
            self.runner(
                ("security", "add-generic-password", "-U", "-s", reference.service,
                 "-a", reference.account, "-w"),
                check=True, input=secret + "\n", capture_output=True, text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CapabilityError("macOS Keychain secret could not be saved") from exc

    def delete(self, reference: SecretReference) -> None:
        try:
            self.runner(("security", "delete-generic-password", "-s", reference.service, "-a", reference.account), check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CapabilityError("macOS Keychain secret could not be removed") from exc


def device_secret_reference(device_pk: int, *, service: str = "ChannelDex.UltraNEXUS", version_token: str = "") -> SecretReference:
    """Return a stable opaque service/account pair for one device."""
    digest = hashlib.sha256(f"device:{int(device_pk)}".encode("utf-8")).hexdigest()[:24]
    suffix = f"-{version_token}" if version_token else ""
    return SecretReference(service=service, account=f"device-{digest}{suffix}")


def new_device_secret_reference(device_pk: int) -> SecretReference:
    """Return an opaque, collision-resistant reference for a credential revision."""
    return device_secret_reference(device_pk, version_token=secrets.token_hex(8))


def save_device_secret(device_pk: int, password: str, *, store=None) -> SecretReference:
    reference = device_secret_reference(device_pk)
    (store or KeychainSecretStore()).set(reference, password)
    return reference
