import json
import struct
import unittest

from pubtv.ultranexus.capabilities import CapabilityGate
from pubtv.ultranexus.exceptions import CapabilityError, CompatibilityError, ValidationError
from pubtv.ultranexus.media import parse_ffprobe_json, validate_nexus_mono
from pubtv.ultranexus.utils import sha256_bytes, validate_filename
from pubtv.ultranexus.encoding import FFmpegEncoder, AdobeMediaEncoder
from pubtv.ultranexus.nmg import NMGImage, MAGIC, VERSION_OFFSET, SCHEDULE_BASE, SCHEDULE_STRIDE, RESOURCE_BASE, Resource
from pubtv.ultranexus.mutation import MutationPlan
from pubtv.ultranexus.secrets import KeychainSecretStore, SecretReference

class UltraNexusAdapterTests(unittest.TestCase):
    def test_hash_and_filename_rules(self):
        self.assertEqual(sha256_bytes(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        validate_filename("SHOW-001.mp4")
        with self.assertRaises(ValidationError): validate_filename("../secret.mp4")
        with self.assertRaises(ValidationError): validate_filename("é.mp4")

    def test_probe_decimal_ceiling_and_reject_profile(self):
        def probe(duration):
            return parse_ffprobe_json(json.dumps({"streams":[{"codec_type":"video","codec_name":"h264","width":1920,"height":1080,"duration":duration,"r_frame_rate":"30000/1001"},{"codec_type":"audio","codec_name":"aac"}]}))
        self.assertEqual(probe("0.0001").duration_units, 1)
        self.assertEqual(probe("1").duration_units, 30)
        p = probe("1.0001")
        self.assertEqual(p.nominal_frames, 31)
        self.assertEqual(p.duration_units, 31)
        validate_nexus_mono(p)
        bad = parse_ffprobe_json({"streams":[{"codec_type":"video","codec_name":"mpeg2video","width":720,"height":480,"duration":"1"}]})
        with self.assertRaises(CompatibilityError): validate_nexus_mono(bad)

    def test_gates_and_argument_commands(self):
        gate = CapabilityGate()
        with self.assertRaises(CapabilityError): gate.require("full_week_generation")
        with self.assertRaisesRegex(CapabilityError, "schedule.bin"): gate.require_schedule_bin_delivery()
        command = FFmpegEncoder().command("in file.mp4", "out file.mp4").argv
        self.assertIn("-n", command); self.assertIn("in file.mp4", command)

    def test_ame_bridge_script(self):
        ame = AdobeMediaEncoder(executable="ame", preset="Nexus Mono")
        script = ame.extend_script("a'file", "out.mp4")
        self.assertIn("app.getExporter()", script); self.assertIn("exportItem", script); self.assertIn("app.quit", script)
        self.assertEqual(ame.bridge_command("job.jsx"), ("ame", "--console", "es.processFile", "job.jsx"))
        with self.assertRaises(CapabilityError): ame.command("a", "b")

    def test_nmg_header_and_keychain(self):
        data = bytearray(256); data[:8] = MAGIC; data[VERSION_OFFSET:VERSION_OFFSET+8] = b"7.0.3.48"
        image = NMGImage(bytes(data)); self.assertEqual(image.manifest()["version"], "7.0.3.48")
        with self.assertRaises(CompatibilityError): NMGImage(bytes(data), expected_sha256="0"*64)
        calls = []
        def runner(argv, **kwargs): calls.append((argv, kwargs)); return type("R", (), {"stdout":"secret\n"})()
        self.assertEqual(KeychainSecretStore(runner).get(SecretReference("svc", "acct")), "secret")

    def test_restricted_nmg_scan_rejects_sparse_resource(self):
        data = bytearray(34_923_624)
        data[:8] = MAGIC
        data[VERSION_OFFSET:VERSION_OFFSET + 8] = b"7.0.3.48"
        self.assertTrue(NMGImage(bytes(data)).validate_restricted())
        second = RESOURCE_BASE + 518
        struct.pack_into("<II", data, second, 1, 42)
        data[second + 0x43:second + 0x48] = b"Clip\0"
        with self.assertRaises(CompatibilityError):
            NMGImage(bytes(data)).validate_restricted()

    def test_nmg_switchback_shift_and_metadata(self):
        data = bytearray(34_923_624); data[:8] = MAGIC; data[VERSION_OFFSET:VERSION_OFFSET+8] = b"7.0.3.48"
        rb = RESOURCE_BASE; struct.pack_into("<II", data, rb, 1, 77); data[rb+0x43:rb+0x48] = b"Clip\0"; data[rb+0x83:rb+0x88] = b"Vol1\0"
        tb, later = SCHEDULE_BASE, SCHEDULE_BASE + SCHEDULE_STRIDE
        for base, ref, title, start, end in ((tb, 0, b"Switchback 1:00:00\0", 3600, 4000), (later, 88, b"Later\0", 4000, 4010)):
            struct.pack_into("<II", data, base, 123, ref); data[base+0x1a] = 1; data[base+0x57:base+0x57+len(title)] = title; struct.pack_into("<III", data, base+0x1b, start, start, end)
        original = bytes(data); image = NMGImage(original)
        out = image.insert_schedule(template_base=later, target_base=tb, resource=Resource(rb,77,"Clip","clip.mp4","Vol1",""), occurrence_id=9, day=1, start=3600, duration=400, title="Clip", filename="clip.mp4", out_frames=12000)
        self.assertEqual(image.data, original); self.assertEqual(struct.unpack_from("<I", out.data, tb+4)[0], 77)
        self.assertEqual(struct.unpack_from("<I", out.data, later+SCHEDULE_STRIDE+4)[0], 88)
        self.assertTrue(out.data[later+0x57:later+0x57+10].startswith(b"Switchback"))
        inside = image.insert_schedule(template_base=later, target_base=tb, resource=Resource(rb,77,"Clip","clip.mp4","Vol1",""), occurrence_id=10, day=1, start=3700, duration=200, title="Clip", filename="clip.mp4", out_frames=6000)
        self.assertTrue(inside.data[tb+0x57:tb+0x57+10].startswith(b"Switchback"))
        self.assertEqual(struct.unpack_from("<I", inside.data, later+4)[0], 77)
        self.assertEqual(struct.unpack_from("<I", inside.data, later+2*SCHEDULE_STRIDE+4)[0], 88)

    def test_nmg_uses_shared_immutable_mutation_plan_and_audit(self):
        data = bytearray(34_923_624)
        data[:8] = MAGIC
        data[VERSION_OFFSET:VERSION_OFFSET + 8] = b"7.0.3.48"
        image = NMGImage(bytes(data))
        changed = bytearray(data)
        changed[200:204] = b"test"
        plan = MutationPlan.between(image.data, bytes(changed), "header-evidence")
        result, audit = image.apply(plan)
        self.assertEqual(image.data[200:204], b"\0" * 4)
        self.assertEqual(result.data[200:204], b"test")
        self.assertEqual(audit["ranges"], ((200, 204),))

if __name__ == "__main__": unittest.main()
