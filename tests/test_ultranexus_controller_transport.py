import tempfile
import unittest
from pathlib import Path

from pubtv.ultranexus.command import (
    PASSWORD_ALPHABET,
    TOKEN_ALPHABET,
    CommandError,
    ControllerCommandTransport,
    TelnetNegotiator,
    build_load_schedule_command,
    build_xpass_command,
    compute_xpass_response,
)
from pubtv.ultranexus.delivery import deliver_schedule, recover_schedule
from pubtv.ultranexus.exceptions import ValidationError


class Socket:
    def __init__(self, reads):
        self.reads = list(reads)
        self.sent = []

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, value):
        self.sent.append(value)

    def recv(self, _size):
        return self.reads.pop(0) if self.reads else b""


class Adapter:
    def __init__(self):
        self.files = {"/internal/schedule/schedule.bin": b"known good"}

    def stage(self, local, remote):
        self.files[remote] = Path(local).read_bytes()

    def backup(self, remote, backup):
        if remote not in self.files:
            return False
        self.files[backup] = self.files.pop(remote)
        return True

    def promote(self, staged, remote):
        self.files[remote] = self.files.pop(staged)

    def rollback(self, staged, remote, backup):
        self.files.pop(staged, None)
        self.files.pop(remote, None)
        self.files[remote] = self.files.pop(backup)


class ControllerTransportTests(unittest.TestCase):
    def test_xpass_dummy_and_alphabets(self):
        self.assertEqual(len(PASSWORD_ALPHABET), 64)
        self.assertEqual(len(TOKEN_ALPHABET), 64)
        self.assertEqual(compute_xpass_response("example", "1A2B3C4D"), "CCoKAY2R")

    def test_xpass_rejects_bad_nonce_and_delimiters(self):
        with self.assertRaises(ValidationError):
            compute_xpass_response("example", "bad")
        with self.assertRaises(ValidationError):
            compute_xpass_response("example8\n", "1A2B3C4D")
        with self.assertRaises(ValidationError):
            build_xpass_command("operator\n", "example", nonce="1A2B3C4D")
        self.assertEqual(build_load_schedule_command(), "LOADSCH /internal/schedule/schedule.bin\r\n")

    def test_telnet_fragment_and_unsupported_option(self):
        parser = TelnetNegotiator()
        self.assertEqual(parser.consume(b"ULTRA"), (b"ULTRA", b""))
        self.assertEqual(parser.consume(bytes([255])), (b"", b""))
        text, reply = parser.consume(bytes([251, 1]) + b"NEXUS\r\n")
        self.assertEqual(text, b"NEXUS\r\n")
        self.assertEqual(reply, bytes([255, 254, 1]))

    def test_auth_and_single_activation(self):
        sock = Socket([b"331 User name OK\r\n", b"230 User Logged in\r\n", b"200 Command OK\r\n"])
        client = ControllerCommandTransport(sock)
        client.authenticate("operator", "example", nonce="1A2B3C4D")
        client.activate_schedule()
        self.assertEqual(sock.sent[0], b"user operator\r\n")
        self.assertEqual(sock.sent[1], b"XPASS 1A2B3C4D CCoKAY2R\r\n")
        self.assertEqual(sock.sent[2], b"LOADSCH /internal/schedule/schedule.bin\r\n")

    def test_auth_rejection_and_no_blind_loadsch_retry(self):
        sock = Socket([b"331 need password\n", b"530 denied\n"])
        with self.assertRaisesRegex(CommandError, "CONTROLLER_REJECTED"):
            ControllerCommandTransport(sock).authenticate("operator", "example", nonce="1A2B3C4D")

    def test_delivery_hash_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidate.bin"
            source.write_bytes(b"synthetic schedule")
            adapter = Adapter()
            result = deliver_schedule(adapter, source, "/internal/schedule/schedule.bin")
            self.assertTrue(result.promoted)
            self.assertEqual(len(result.sha256), 64)
            self.assertEqual(adapter.files["/internal/schedule/schedule.bin"], b"synthetic schedule")
            self.assertEqual(adapter.files[result.backup_path], b"known good")
        with self.assertRaises(ValidationError):
            deliver_schedule(Adapter(), "/missing", "/internal/schedule/schedule.txt")

    def test_delivery_can_rollback_retained_backup(self):
        adapter = Adapter()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidate.bin"
            source.write_bytes(b"candidate")
            result = deliver_schedule(adapter, source, "/internal/schedule/schedule.bin")
        self.assertTrue(recover_schedule(adapter, result.staging_path, result.remote_path, rollback=True, backup_path=result.backup_path))
        self.assertEqual(adapter.files[result.remote_path], b"known good")


if __name__ == "__main__":
    unittest.main()
