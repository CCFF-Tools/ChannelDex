from dataclasses import dataclass
from .exceptions import CapabilityError

@dataclass(frozen=True)
class Capabilities:
    media_ftp: bool = True
    nmg_resource_append: bool = True
    nmg_schedule_insert: bool = True
    full_week_generation: bool = False
    schedule_bin_delivery: bool = False
    controller_image_delivery: bool = False

class CapabilityGate:
    """Explicit capability checks; unqualified operations fail closed."""
    def __init__(self, capabilities: Capabilities = Capabilities()):
        self.capabilities = capabilities

    def require(self, name: str) -> None:
        if not hasattr(self.capabilities, name) or not getattr(self.capabilities, name):
            raise CapabilityError(f"UltraNEXUS capability is disabled or unqualified: {name}")

    def require_schedule_bin_delivery(self):
        raise CapabilityError("schedule.bin delivery is disabled pending research")
