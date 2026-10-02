import struct
import unittest

from pubtv.ultranexus.bin import (
    BIN_LENGTH, CHECKSUM_OFFSET, MAGIC, VERSION_OFFSET, RESOURCE_BASE,
    RESOURCE_STRIDE, RESOURCE_CAPACITY, SCHEDULE_BASE, SCHEDULE_STRIDE,
    SCHEDULE_CAPACITY, BinFormatError, BinImage, MutationPlan, diff_ranges,
    plan_patch_resource, stored_checksum, week_seconds,
)
from pubtv.ultranexus.nmg import (NMGImage, RESOURCE_BASE as NMG_RESOURCE_BASE,
                                  SCHEDULE_BASE as NMG_SCHEDULE_BASE)
from pubtv.ultranexus.parity import ParityError, validate_nmg_bin_parity


def fixture():
    b = bytearray(BIN_LENGTH)
    b[:8] = MAGIC
    b[VERSION_OFFSET:VERSION_OFFSET + 9] = b"7.0.3.48\0"
    r = RESOURCE_BASE
    struct.pack_into("<II", b, r, 7, 100)
    b[r + 0x43:r + 0x48] = b"Clip\0"
    b[r + 0x83:r + 0x88] = b"Vol1\0"
    b[r + 0xA3:r + 0xAC] = b"clip.mp4\0"
    s = SCHEDULE_BASE
    struct.pack_into("<II", b, s, 200, 100)
    b[s + 0x1A] = 1
    for offset, value in ((0x1B, 3600), (0x1F, 3600), (0x23, 3661),
                          (0x33, 61), (0x13E, 61), (0x3B, 0),
                          (0x13A, 0), (0x3F, 1830), (0x143, 1830)):
        struct.pack_into("<I", b, s + offset, value)
    b[s + 0x57:s + 0x62] = b"Clip event\0"
    b[s + 0x97:s + 0x99] = b"\0\0"
    b[s + 0xB7:s + 0xBC] = b"Vol1\0"
    b[s + 0xD7:s + 0xE0] = b"clip.mp4\0"
    struct.pack_into("<I", b, CHECKSUM_OFFSET, stored_checksum(b))
    return bytes(b)


class BinTests(unittest.TestCase):
    def test_strict_layout_checksum_and_join(self):
        image = BinImage(fixture())
        self.assertEqual(image.manifest()["resources"], 1)
        self.assertEqual(image.schedules[0].reference, 100)
        with self.assertRaises(BinFormatError): BinImage(fixture()[:-1])

    def test_checksum_and_exact_ranges(self):
        source = fixture(); image = BinImage(source)
        plan = plan_patch_resource(image, image.resources[0], title="New")
        result, audit = image.apply(plan)
        self.assertTrue(all(CHECKSUM_OFFSET <= start < CHECKSUM_OFFSET + 4 or
                            RESOURCE_BASE + 0x43 <= start < RESOURCE_BASE + 0x43 + 32
                            for start, _ in diff_ranges(source, result.data)))
        self.assertEqual(audit["ranges"], diff_ranges(source, result.data))
        unchanged, unchanged_audit = image.apply(MutationPlan(()))
        self.assertEqual(unchanged.data, source)
        self.assertEqual(unchanged_audit["ranges"], ())

    def test_rejects_sparse_and_duplicate_schedule_fields(self):
        b = bytearray(fixture()); s = SCHEDULE_BASE
        struct.pack_into("<I", b, s + 0x1F, 3601)
        struct.pack_into("<I", b, CHECKSUM_OFFSET, stored_checksum(b))
        with self.assertRaises(BinFormatError): BinImage(bytes(b))

    def test_weekday_epoch(self):
        self.assertEqual(week_seconds(1, 3, 14, 15), 357255)
        with self.assertRaises(ValueError): week_seconds(7, 0, 0, 0)

    def test_selected_replace_insert_move_delete_and_technical_parity(self):
        image = BinImage(fixture())
        original = image.data[SCHEDULE_BASE:SCHEDULE_BASE + 574]
        replacement = bytearray(original)
        struct.pack_into("<I", replacement, 0, 201)
        for offset, value in ((0x1B, 4000), (0x1F, 4000), (0x23, 4061)):
            struct.pack_into("<I", replacement, offset, value)
        replacement[0x57:0x57 + 64] = b"Replacement\0" + b"\0" * (64 - len(b"Replacement\0"))
        replaced, _ = image.replace_schedule(0, bytes(replacement))
        self.assertEqual(replaced.schedules[0].occurrence, 201)

        insertion = bytearray(replacement)
        struct.pack_into("<I", insertion, 0, 202)
        for offset, value in ((0x1B, 5000), (0x1F, 5000), (0x23, 5061)):
            struct.pack_into("<I", insertion, offset, value)
        inserted, _ = replaced.insert_schedule(1, bytes(insertion))
        self.assertEqual(len(inserted.schedules), 2)
        moved, _ = inserted.move_schedule(1, 0)
        self.assertEqual(moved.schedules[0].occurrence, 202)
        deleted, _ = moved.delete_schedule(0)
        self.assertEqual(len(deleted.schedules), 1)

        broken = bytearray(fixture())
        broken[SCHEDULE_BASE + 0x4E] = 1
        struct.pack_into("<I", broken, CHECKSUM_OFFSET, stored_checksum(broken))
        with self.assertRaises(BinFormatError): BinImage(bytes(broken))

    def test_append_resource_from_template_is_audited_and_guarded(self):
        image = BinImage(fixture())
        appended, audit = image.append_resource_from_template(
            image.resources[0], reference=101, title="Second", filename="second.mp4",
            media_id=42, duration_units=1830, rounded_seconds=61, bitrate=10_000_000,
            width=720, height=480, comment="derived")
        self.assertEqual(len(appended.resources), 2)
        self.assertEqual(appended.resources[1].filename, "second.mp4")
        self.assertTrue(audit["ranges"])
        with self.assertRaises(ValueError):
            image.append_resource_from_template(image.resources[0], reference=102,
                                                title="x", filename="a" * 28 + ".mp4", media_id=43)
        with self.assertRaises(ValueError):
            image.append_resource_from_template(image.resources[0], reference=103,
                                                title="x", filename="CLIP.MP4", media_id=43)

    def test_logical_parity_includes_switchback_adjacency(self):
        binary = BinImage(fixture())
        nmg = bytearray(34_923_624)
        nmg[:8] = MAGIC
        nmg[VERSION_OFFSET:VERSION_OFFSET + 9] = b"7.0.3.48\0"
        nmg[NMG_RESOURCE_BASE:NMG_RESOURCE_BASE + RESOURCE_STRIDE] = binary.data[
            RESOURCE_BASE:RESOURCE_BASE + RESOURCE_STRIDE]
        nmg[NMG_SCHEDULE_BASE:NMG_SCHEDULE_BASE + SCHEDULE_STRIDE] = binary.data[
            SCHEDULE_BASE:SCHEDULE_BASE + SCHEDULE_STRIDE]
        switchback = NMG_SCHEDULE_BASE + SCHEDULE_STRIDE
        nmg[switchback + 0x1A] = 1
        for offset, value in ((0x1B, 3661), (0x1F, 3661), (0x23, 3700), (0x33, 39)):
            struct.pack_into("<I", nmg, switchback + offset, value)
        title = b"Switchback 0:00:39\0"
        nmg[switchback + 0x57:switchback + 0x57 + len(title)] = title
        nmg_image = NMGImage(bytes(nmg))
        nmg_image.validate_restricted()
        report = validate_nmg_bin_parity(nmg_image, binary)
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["timeline"]["exact_adjacencies"], 1)
        struct.pack_into("<I", nmg, switchback + 0x1B, 3660)
        struct.pack_into("<I", nmg, switchback + 0x1F, 3660)
        struct.pack_into("<I", nmg, switchback + 0x33, 40)
        with self.assertRaises((ParityError, ValueError)):
            validate_nmg_bin_parity(NMGImage(bytes(nmg)), binary)

    def test_full_tables_are_detected_as_exhausted(self):
        resources = bytearray(fixture())
        template = resources[RESOURCE_BASE:RESOURCE_BASE + RESOURCE_STRIDE]
        for slot in range(1, RESOURCE_CAPACITY):
            offset = RESOURCE_BASE + slot * RESOURCE_STRIDE
            resources[offset:offset + RESOURCE_STRIDE] = template
            struct.pack_into("<I", resources, offset + 4, 100 + slot)
        struct.pack_into("<I", resources, CHECKSUM_OFFSET, stored_checksum(resources))
        full_resources = BinImage(bytes(resources))
        with self.assertRaises(ValueError):
            full_resources.append_resource_from_template(
                full_resources.resources[0], reference=7000, title="overflow",
                filename="overflow.mp4", media_id=42)

        schedules = bytearray(fixture())
        template = schedules[SCHEDULE_BASE:SCHEDULE_BASE + SCHEDULE_STRIDE]
        for slot in range(SCHEDULE_CAPACITY):
            offset = SCHEDULE_BASE + slot * SCHEDULE_STRIDE
            schedules[offset:offset + SCHEDULE_STRIDE] = template
            struct.pack_into("<I", schedules, offset, 200 + slot)
            start = 3600 + slot * 61
            for field, value in ((0x1B, start), (0x1F, start), (0x23, start + 61)):
                struct.pack_into("<I", schedules, offset + field, value)
        struct.pack_into("<I", schedules, CHECKSUM_OFFSET, stored_checksum(schedules))
        full_schedules = BinImage(bytes(schedules))
        with self.assertRaises(ValueError):
            full_schedules.insert_schedule(SCHEDULE_CAPACITY, template)


if __name__ == "__main__": unittest.main()
