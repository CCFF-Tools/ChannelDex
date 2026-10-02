import hashlib
from pathlib import Path
from .exceptions import ValidationError

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def validate_ascii(value: str, *, max_bytes: int | None = None, field="value", allow_empty=False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValidationError(f"{field} must be a non-empty ASCII string")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValidationError(f"{field} must contain ASCII only") from exc
    if any(byte < 0x20 or byte == 0x7f for byte in encoded):
        raise ValidationError(f"{field} contains a control character")
    if max_bytes is not None and len(encoded) >= max_bytes:
        raise ValidationError(f"{field} must fit in {max_bytes - 1} bytes including terminator")
    return value

def validate_filename(filename: str, *, max_base_length=27) -> str:
    validate_ascii(filename, field="filename")
    if "/" in filename or "\\" in filename or filename in {".", ".."} or ".." in filename:
        raise ValidationError("filename must be a bare safe filename")
    if not Path(filename).name == filename:
        raise ValidationError("filename must not contain a path")
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    if not base or len(base.encode("ascii")) > max_base_length:
        raise ValidationError(f"filename base must be 1..{max_base_length} ASCII bytes")
    return filename
