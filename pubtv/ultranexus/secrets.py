"""Replaceable secret references; credentials never enter settings or logs."""
from dataclasses import dataclass
import subprocess
from .exceptions import CapabilityError

@dataclass(frozen=True)
class SecretReference:
    service: str
    account: str

class SecretStore:
    def get(self, reference: SecretReference) -> str: raise NotImplementedError

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
