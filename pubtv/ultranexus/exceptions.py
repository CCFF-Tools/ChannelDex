class UltraNexusError(Exception):
    """Base for adapter failures."""

class CompatibilityError(UltraNexusError):
    pass

class CapabilityError(UltraNexusError):
    pass

class TransferError(UltraNexusError):
    pass

class ValidationError(UltraNexusError):
    pass
