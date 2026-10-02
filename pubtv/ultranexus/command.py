"""XPASS controller command framing and a bounded telnet transport.

The controller protocol is deliberately kept behind this small module.  No
network connection is opened by these helpers; callers provide a socket-like
object (and can therefore test it with an in-memory object).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import secrets
from typing import Callable

from .exceptions import CompatibilityError, ValidationError

IAC, DONT, DO, WONT, WILL, SB, SE = (255, 254, 253, 252, 251, 250, 240)
PASSWORD_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ@ "
TOKEN_ALPHABET = "!@#$%^&*()<>{}1A2B3C4D5E6F7G8H9I0JZYXWVUTSRQPONMLKasdfghjklpoiqw"
LOADSCH_PATH = "/internal/schedule/schedule.bin"

# A stable, public smoke-test command.  It intentionally contains no secret
# or target-specific bytes and is useful for checking line formatting offline.
XPASS_DUMMY_VECTOR = b"XPASS 1A2B3C4D CCoKAY2R\r\n"


def compute_xpass_response(password: str, nonce: str) -> str:
    """Calculate the qualified eight-character legacy XPASS response."""
    if len(nonce) != 8 or any(ch not in TOKEN_ALPHABET for ch in nonce):
        raise ValidationError("nonce must be exactly 8 token-alphabet characters")
    if not isinstance(password, str) or any(ch not in PASSWORD_ALPHABET for ch in password):
        raise ValidationError("password contains an unsupported XPASS character")
    block = (password + " " * 8)[:8]
    p = [PASSWORD_ALPHABET.index(ch) for ch in block]
    n = [TOKEN_ALPHABET.index(ch) for ch in nonce]
    previous, result = 0, []
    for i in range(8):
        mixed = (previous + p[i] + n[i]) % 64
        previous = mixed ^ n[(i + 1) % 8]
        result.append(TOKEN_ALPHABET[previous])
    return "".join(result)


def generate_nonce(randbelow=secrets.randbelow) -> str:
    return "".join(TOKEN_ALPHABET[randbelow(64)] for _ in range(8))


def build_xpass_command(username: str, password: str, *, nonce: str | None = None) -> tuple[str, str]:
    """Return username and XPASS lines; callers must send them only in memory."""
    if not username or any(ch in username for ch in "\r\n\0\xff "):
        raise ValidationError("username contains a command delimiter")
    nonce = generate_nonce() if nonce is None else nonce
    response = compute_xpass_response(password, nonce)
    return f"user {username}\r\n", f"XPASS {nonce} {response}\r\n"


def build_load_schedule_command(path: str = LOADSCH_PATH) -> str:
    if path != LOADSCH_PATH:
        raise ValidationError("LOADSCH path is fixed for the qualified target")
    return f"LOADSCH {LOADSCH_PATH}\r\n"


class CommandError(CompatibilityError):
    """A structured controller transport failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def redact_secret(value: object, *, replacement: str = "[REDACTED]") -> str:
    """Return safe diagnostic text without exposing credentials or nonce data."""
    if value is None:
        return ""
    return replacement


class TelnetNegotiator:
    """Bounded IAC parser; unsupported options are refused explicitly."""

    def __init__(self, *, max_bytes: int = 65536):
        self.max_bytes = max_bytes
        self._pending = b""

    def consume(self, data: bytes) -> tuple[bytes, bytes]:
        data = self._pending + bytes(data)
        self._pending = b""
        if len(data) > self.max_bytes:
            raise CommandError("TELNET_LIMIT", "negotiation input exceeds bound")
        out, replies, i = bytearray(), bytearray(), 0
        while i < len(data):
            byte = data[i]; i += 1
            if byte != IAC:
                out.append(byte); continue
            if i >= len(data):
                self._pending = bytes((IAC,))
                break
            op = data[i]; i += 1
            if op == IAC:
                out.append(IAC); continue
            if op in (DO, DONT, WILL, WONT):
                if i >= len(data):
                    self._pending = bytes((IAC, op))
                    break
                option = data[i]; i += 1
                # Refuse all options: this transport has no terminal mode.
                replies.extend((IAC, WONT if op in (DO, DONT) else DONT, option))
                continue
            if op == SB:
                end = data.find(bytes((IAC, SE)), i)
                if end < 0:
                    self._pending = data[i - 2:]
                    break
                i = end + 2
                continue
            raise CommandError("TELNET_OPTION", "unsupported telnet command")
        return bytes(out), bytes(replies)


@dataclass
class ControllerCommandTransport:
    sock: object
    timeout: float = 10.0
    max_frame: int = 65536
    nonce_factory: Callable[[], str] = generate_nonce
    _negotiator: TelnetNegotiator = field(default_factory=TelnetNegotiator, init=False)
    _line_buffer: bytearray = field(default_factory=bytearray, init=False)

    def _read_lines(self, *, expected_code: str, max_bytes: int | None = None) -> str:
        limit = max_bytes or self.max_frame
        while len(self._line_buffer) < limit:
            while b"\n" in self._line_buffer:
                line, _, remainder = self._line_buffer.partition(b"\n")
                self._line_buffer = bytearray(remainder)
                try:
                    text = line.rstrip(b"\r").decode("ascii", "strict")
                except UnicodeDecodeError as exc:
                    raise CommandError("BAD_RESPONSE", "controller response is not ASCII") from exc
                if text.startswith(expected_code + " ") or text == expected_code:
                    return text
                if text[:3].isdigit() and text[:3] != expected_code:
                    raise CommandError("CONTROLLER_REJECTED", f"controller returned {text[:3]}")
            try:
                chunk = self.sock.recv(min(4096, limit - len(self._line_buffer)))
            except Exception as exc:
                raise CommandError("IO_ERROR", "controller read failed") from exc
            if not chunk:
                raise CommandError("EARLY_EOF", "controller closed the connection")
            clean, reply = self._negotiator.consume(chunk)
            if reply:
                self.sock.sendall(reply)
            self._line_buffer.extend(clean)
        raise CommandError("RESPONSE_LIMIT", "controller response exceeds bound")

    def authenticate(self, username: str, password: str, *, nonce: str | None = None) -> str:
        if hasattr(self.sock, "settimeout"):
            self.sock.settimeout(self.timeout)
        user_line, xpass_line = build_xpass_command(username, password, nonce=nonce or self.nonce_factory())
        try:
            self.sock.sendall(user_line.encode("ascii"))
            self._read_lines(expected_code="331")
            self.sock.sendall(xpass_line.encode("ascii"))
            return self._read_lines(expected_code="230")
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError("IO_ERROR", "controller authentication failed") from exc

    def activate_schedule(self) -> str:
        """Send the sole qualified activation command exactly once."""
        try:
            self.sock.sendall(build_load_schedule_command().encode("ascii"))
            return self._read_lines(expected_code="200")
        except CommandError as exc:
            if exc.code in {"EARLY_EOF", "IO_ERROR"}:
                raise CommandError("AMBIGUOUS", "activation outcome is unknown") from exc
            raise
        except Exception as exc:
            raise CommandError("AMBIGUOUS", "activation outcome is unknown") from exc

# Friendly aliases used by integrations while the protocol remains internal.
XPASSTransport = ControllerCommandTransport
