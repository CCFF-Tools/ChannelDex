"""Fail-closed reader and template-preserving editor for qualified schedule.bin."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import struct
from typing import Iterable
from .mutation import MutationError, MutationPlan, apply_mutation_plan, diff_ranges

BIN_LENGTH = 5_649_624
MAGIC = b"WinLGX 7"
VERSION_OFFSET, VERSION_SIZE = 0x80, 32
VERSION = "7.0.3.48"
CHECKSUM_OFFSET = 0x138
RESOURCE_BASE, RESOURCE_STRIDE, RESOURCE_CAPACITY = 100_976, 518, 6_000
SCHEDULE_BASE, SCHEDULE_STRIDE, SCHEDULE_CAPACITY = 3_208_976, 574, 3_000
RESOURCE_END = RESOURCE_BASE + RESOURCE_STRIDE * RESOURCE_CAPACITY
SCHEDULE_END = SCHEDULE_BASE + SCHEDULE_STRIDE * SCHEDULE_CAPACITY

class BinFormatError(ValueError): pass
class BinValidationError(BinFormatError): pass

def _u32(b, o): return struct.unpack_from("<I", b, o)[0]
def _u16(b, o): return struct.unpack_from("<H", b, o)[0]
def _put32(b, o, v): struct.pack_into("<I", b, o, int(v))
def _put16(b, o, v): struct.pack_into("<H", b, o, int(v))

def _field(b: bytes, offset: int, size: int, label: str = "field") -> str:
    raw = b[offset:offset + size]
    end = raw.find(b"\0")
    if end < 0: raise BinFormatError(f"{label} has no NUL terminator")
    try: return raw[:end].decode("ascii")
    except UnicodeDecodeError as exc: raise BinFormatError(f"{label} is not ASCII") from exc

def _text(out: bytearray, offset: int, size: int, value: str, label: str) -> None:
    try: raw = value.encode("ascii")
    except UnicodeEncodeError as exc: raise BinValidationError(f"{label} must be ASCII") from exc
    if "\0" in value or len(raw) >= size: raise BinValidationError(f"{label} does not fit NUL-terminated field")
    out[offset:offset + size] = raw + b"\0" * (size - len(raw))

_CRC_TABLE = tuple((lambda n: n)(n) for n in range(256))
def _make_crc_table():
    result=[]
    for n in range(256):
        c=n
        for _ in range(8): c = 0xEDB88320 ^ (c >> 1) if c & 1 else c >> 1
        result.append(c & 0xffffffff)
    return tuple(result)
_CRC_TABLE = _make_crc_table()

def crc32(data: bytes) -> int:
    c = 0xffffffff
    for x in data: c = _CRC_TABLE[(c ^ x) & 255] ^ (c >> 8)
    return (c ^ 0xffffffff) & 0xffffffff

def stored_checksum(data: bytes) -> int:
    work = bytearray(data); work[CHECKSUM_OFFSET:CHECKSUM_OFFSET + 4] = b"\0" * 4
    return (~crc32(work)) & 0xffffffff

def validate_checksum(data: bytes) -> None:
    if len(data) < CHECKSUM_OFFSET + 4 or _u32(data, CHECKSUM_OFFSET) != stored_checksum(data):
        raise BinFormatError("schedule.bin checksum mismatch")

@dataclass(frozen=True)
class ResourceRecord:
    slot: int; offset: int; group: int; reference: int; title: str; filename: str
    storage: str; comment: str; media_id: int; duration_units: int; rounded_seconds: int

@dataclass(frozen=True)
class ScheduleRecord:
    slot: int; offset: int; occurrence: int; reference: int; day: int; start: int; end: int
    duration: int; in_point: int; out_point: int; title: str; comment: str; storage: str; filename: str

class BinImage:
    def __init__(self, data: bytes, *, expected_sha256: str | None = None, validate=True):
        self.data=bytes(data)
        if len(self.data) != BIN_LENGTH: raise BinFormatError(f"qualified BIN length required ({BIN_LENGTH})")
        if not self.data.startswith(MAGIC): raise BinFormatError("unsupported BIN magic")
        try: version=_field(self.data, VERSION_OFFSET, VERSION_SIZE, "version")
        except BinFormatError: raise
        if version != VERSION: raise BinFormatError(f"unsupported BIN version: {version!r}")
        if expected_sha256 and sha256(self.data).hexdigest() != expected_sha256.lower(): raise BinFormatError("base SHA-256 mismatch")
        if validate: validate_checksum(self.data)
        self.version=version
        self._resources=self._scan_resources()
        self._schedules=self._scan_schedules()
        if len({r.reference for r in self._resources}) != len(self._resources): raise BinFormatError("duplicate active resource reference")
        refs={r.reference for r in self._resources}
        if any(s.reference not in refs for s in self._schedules): raise BinFormatError("schedule references missing resource")
        for s in self._schedules:
            r = next(r for r in self._resources if r.reference == s.reference)
            if self.data[s.offset + 0x4e:s.offset + 0x57] != self.data[r.offset + 0x3a:r.offset + 0x43]:
                raise BinFormatError(f"schedule/resource technical block mismatch at slot {s.slot}")
        for day in range(7):
            day_rows = sorted((s for s in self._schedules if s.day == day), key=lambda s: s.start)
            for previous, current in zip(day_rows, day_rows[1:]):
                if current.start < previous.end:
                    raise BinFormatError(f"overlapping schedule intervals on weekday {day}")
    @classmethod
    def from_bytes(cls, data, **kwargs): return cls(data, **kwargs)
    @classmethod
    def from_file(cls, path, **kwargs): return cls(Path(path).read_bytes(), **kwargs)
    def manifest(self):
        return {"format":"UltraNEXUS schedule.bin", "magic":MAGIC.decode(), "version":self.version,
                "size":len(self.data), "sha256":sha256(self.data).hexdigest(), "checksum":f"{_u32(self.data,CHECKSUM_OFFSET):08x}",
                "resources":len(self._resources), "schedules":len(self._schedules)}
    @property
    def resources(self): return self._resources
    @property
    def schedules(self): return self._schedules
    @property
    def executable_schedules(self):
        """Executable video occurrences; explicit switchback fillers are excluded."""
        return tuple(s for s in self._schedules if not s.title.startswith("Switchback"))
    def _scan_resources(self):
        out=[]
        saw_blank=False
        for slot in range(RESOURCE_CAPACITY):
            o=RESOURCE_BASE+slot*RESOURCE_STRIDE; rec=self.data[o:o+RESOURCE_STRIDE]; group, ref=_u32(rec,0),_u32(rec,4)
            if not group and not ref:
                saw_blank=True
                if rec not in (b"\0"*RESOURCE_STRIDE,) and _field(rec,0x43,32,"blank resource title") != "<Unnamed>": raise BinFormatError(f"unrecognized resource blank slot {slot}")
                continue
            if not group or not ref: raise BinFormatError(f"partial resource record at slot {slot}")
            if saw_blank: raise BinFormatError(f"sparse resource table at slot {slot} is fail-closed")
            out.append(ResourceRecord(slot,o,group,ref,_field(rec,0x43,32,"resource title"),_field(rec,0xa3,110,"resource filename"),_field(rec,0x83,32,"resource storage"),_field(rec,0x111,32,"resource comment"),_u16(rec,0x103),_u32(rec,0x32),_u32(rec,0x26)))
        return tuple(out)
    def _scan_schedules(self):
        out=[]
        saw_blank=False
        for slot in range(SCHEDULE_CAPACITY):
            o=SCHEDULE_BASE+slot*SCHEDULE_STRIDE; rec=self.data[o:o+SCHEDULE_STRIDE]; ident, ref=_u32(rec,0),_u32(rec,4)
            if not ident and not ref:
                saw_blank=True
                if rec != b"\0"*SCHEDULE_STRIDE: raise BinFormatError(f"unrecognized schedule blank slot {slot}")
                continue
            if not ident or not ref: raise BinFormatError(f"partial schedule record at slot {slot}")
            if saw_blank: raise BinFormatError(f"sparse schedule table at slot {slot} is fail-closed")
            title=_field(rec,0x57,64,"schedule title"); comment=_field(rec,0x97,32,"schedule comment")
            vals=(_u32(rec,0x1b),_u32(rec,0x1f),_u32(rec,0x33),_u32(rec,0x13e),_u32(rec,0x3b),_u32(rec,0x13a),_u32(rec,0x3f),_u32(rec,0x143))
            if vals[0]!=vals[1] or vals[2]!=vals[3] or vals[4]!=vals[5] or vals[6]!=vals[7]: raise BinFormatError(f"schedule duplicate mismatch at slot {slot}")
            if not 0 <= rec[0x1a] <= 6: raise BinFormatError(f"invalid schedule weekday at slot {slot}")
            end = _u32(rec, 0x23)
            if title.startswith("Switchback"):
                if end < vals[0] or vals[2] != end - vals[0]: raise BinFormatError(f"invalid switchback interval at slot {slot}")
            elif vals[2] == 0 or end not in (vals[0] + vals[2], vals[0] + vals[2] - 7 * 86400):
                raise BinFormatError(f"invalid schedule interval at slot {slot}")
            out.append(ScheduleRecord(slot,o,ident,ref,rec[0x1a],vals[0],end,vals[2],vals[4],vals[6],title,comment,_field(rec,0xb7,32,"schedule storage"),_field(rec,0xd7,99,"schedule filename")))
        return tuple(out)

    def apply(self, plan: MutationPlan, *, expected_changes: Iterable[tuple[int,int]] | None = None):
        try:
            mutated, _ = apply_mutation_plan(self.data, plan)
        except MutationError as exc:
            raise BinValidationError(str(exc)) from exc
        out=bytearray(mutated)
        out[CHECKSUM_OFFSET:CHECKSUM_OFFSET+4]=b"\0"*4; _put32(out,CHECKSUM_OFFSET,stored_checksum(out))
        actual=diff_ranges(self.data,out)
        if expected_changes is not None and actual != tuple(expected_changes): raise BinValidationError("undeclared byte changes")
        result=BinImage(bytes(out)); return result, {"label":plan.label,"ranges":actual,"sha256":sha256(out).hexdigest()}

    def _schedule_result(self, data: bytearray, label: str):
        plan = MutationPlan.between(self.data, bytes(data), label)
        return self.apply(plan)

    def replace_schedule(self, slot: int, record: bytes):
        """Replace exactly one selected 574-byte record, retaining all other bytes."""
        if not 0 <= slot < SCHEDULE_CAPACITY or len(record) != SCHEDULE_STRIDE:
            raise BinValidationError("invalid schedule replacement")
        data = bytearray(self.data); o = SCHEDULE_BASE + slot * SCHEDULE_STRIDE
        data[o:o + SCHEDULE_STRIDE] = record
        return self._schedule_result(data, "schedule-replace")

    def append_resource_from_template(self, template: ResourceRecord | int, *, reference: int,
                                      title: str, filename: str, media_id: int,
                                      storage: str = "Vol1", comment: str = "",
                                      group: int | None = None, icon: str | None = None,
                                      duration_units: int | None = None, rounded_seconds: int | None = None,
                                      bitrate: int | None = None, width: int | None = None,
                                      height: int | None = None):
        """Clone one qualified ordinary-video resource into the first verified blank slot."""
        if isinstance(template, int):
            template = next((r for r in self._resources if r.slot == template), None)
        if template is None or template not in self._resources: raise BinValidationError("template resource is not active")
        if not 1 <= int(reference) <= 0xffffffff or reference in {r.reference for r in self._resources}:
            raise BinValidationError("resource reference must be nonzero and unique")
        if not 1 <= int(media_id) <= 65535 or media_id in {r.media_id for r in self._resources if r.media_id}:
            raise BinValidationError("Media ID must be nonzero and unique")
        try: encoded = filename.encode("ascii")
        except UnicodeEncodeError as exc: raise BinValidationError("filename must be ASCII") from exc
        if not encoded or b"\0" in encoded or any(c < 0x20 or c == 0x7f for c in encoded):
            raise BinValidationError("filename contains control characters")
        if any(c in filename for c in ("/", "\\")) or filename in (".", "..") or "." not in filename:
            raise BinValidationError("filename must be a bare file with an extension")
        if len(filename.rsplit(".", 1)[0]) > 27: raise BinValidationError("filename base exceeds 27 characters")
        if filename.casefold() in {r.filename.casefold() for r in self._resources}:
            raise BinValidationError("filename collides case-insensitively")
        for value, size, label in ((title,32,"title"),(storage,32,"storage"),(comment,32,"comment")):
            try: raw=value.encode("ascii")
            except UnicodeEncodeError as exc: raise BinValidationError(f"{label} must be ASCII") from exc
            if any(c < 0x20 or c == 0x7f for c in raw): raise BinValidationError(f"{label} contains control characters")
            if len(raw) >= size: raise BinValidationError(f"{label} does not fit")
        target_slot=len(self._resources); target=RESOURCE_BASE+target_slot*RESOURCE_STRIDE
        blank=self.data[target:target+RESOURCE_STRIDE]
        if blank != b"\0"*RESOURCE_STRIDE and _field(blank,0x43,32,"blank resource title") != "<Unnamed>":
            raise BinValidationError("target resource slot is not a recognized blank")
        data=bytearray(self.data); data[target:target+RESOURCE_STRIDE]=self.data[template.offset:template.offset+RESOURCE_STRIDE]
        _put32(data,target,template.group if group is None else group); _put32(data,target+4,reference)
        _text(data,target+0x43,32,title,"title"); _text(data,target+0x63,32,icon or "Hard Disk 1","icon")
        _text(data,target+0x83,32,storage,"storage"); _text(data,target+0xa3,110,filename,"filename")
        _put16(data,target+0x103,media_id); _text(data,target+0x111,32,comment,"comment")
        if duration_units is not None: _put32(data,target+0x32,duration_units); _put32(data,target+0x10b,duration_units)
        if rounded_seconds is not None: _put32(data,target+0x26,rounded_seconds); _put32(data,target+0x2a,rounded_seconds)
        if bitrate is not None: _put32(data,target+0x3a,bitrate)
        if width is not None: _put16(data,target+0x150,width)
        if height is not None: _put16(data,target+0x152,height)
        return self._schedule_result(data, "resource-append")

    def delete_schedule(self, slot: int):
        """Delete one record and compact later records toward the first slot."""
        if not 0 <= slot < len(self._schedules): raise BinValidationError("schedule slot is not active")
        data = bytearray(self.data)
        first = SCHEDULE_BASE + slot * SCHEDULE_STRIDE
        end_slot = len(self._schedules)
        end = SCHEDULE_BASE + end_slot * SCHEDULE_STRIDE
        data[first:end - SCHEDULE_STRIDE] = self.data[first + SCHEDULE_STRIDE:end]
        data[end - SCHEDULE_STRIDE:end] = b"\0" * SCHEDULE_STRIDE
        return self._schedule_result(data, "schedule-delete")

    def move_schedule(self, source_slot: int, target_slot: int):
        """Move a selected record by shifting the intervening records."""
        if not 0 <= source_slot < len(self._schedules) or not 0 <= target_slot < len(self._schedules):
            raise BinValidationError("schedule slot is not active")
        if source_slot == target_slot: return self, {"label": "schedule-move", "ranges": (), "sha256": sha256(self.data).hexdigest()}
        records=[self.data[SCHEDULE_BASE+i*SCHEDULE_STRIDE:SCHEDULE_BASE+(i+1)*SCHEDULE_STRIDE] for i in range(len(self._schedules))]
        chosen=records.pop(source_slot); records.insert(target_slot, chosen)
        data=bytearray(self.data)
        for i, rec in enumerate(records): data[SCHEDULE_BASE+i*SCHEDULE_STRIDE:SCHEDULE_BASE+(i+1)*SCHEDULE_STRIDE]=rec
        return self._schedule_result(data, "schedule-move")

    def insert_schedule(self, slot: int, record: bytes):
        """Insert a complete already-qualified record into a blank slot."""
        if not 0 <= slot <= len(self._schedules) or len(record) != SCHEDULE_STRIDE:
            raise BinValidationError("invalid schedule insertion")
        if len(self._schedules) >= SCHEDULE_CAPACITY: raise BinValidationError("schedule capacity exhausted")
        data=bytearray(self.data); active=len(self._schedules)
        start=SCHEDULE_BASE+slot*SCHEDULE_STRIDE; end=SCHEDULE_BASE+(active+1)*SCHEDULE_STRIDE
        data[start+SCHEDULE_STRIDE:end]=self.data[start:end-SCHEDULE_STRIDE]
        data[start:start+SCHEDULE_STRIDE]=record
        return self._schedule_result(data, "schedule-insert")

def week_seconds(day: int, hour: int, minute: int, second: int) -> int:
    if not 0 <= day <= 6 or not 0 <= hour < 24 or not 0 <= minute < 60 or not 0 <= second < 60: raise BinValidationError("invalid station time")
    return ({0:3,1:4,2:5,3:6,4:7,5:8,6:9}[day] * 86400) + hour*3600 + minute*60 + second

BinParser = BinImage

def serialize_plan(base: bytes | BinImage, plan: MutationPlan):
    image = base if isinstance(base, BinImage) else BinImage(base)
    return image.apply(plan)

def plan_patch_resource(image: BinImage, resource: ResourceRecord, *, title=None, filename=None,
                        storage=None, comment=None, media_id=None, duration_units=None,
                        rounded_seconds=None) -> MutationPlan:
    """Build edits for qualified resource fields while preserving all other bytes."""
    edits=[]; o=resource.offset
    for off,size,value in ((0x43,32,title),(0xa3,110,filename),(0x83,32,storage),(0x111,32,comment)):
        if value is not None:
            b=bytearray(size); _text(b,0,size,value,"resource text"); edits.append((o+off,bytes(b)))
    if media_id is not None:
        if not 1 <= int(media_id) <= 65535: raise BinValidationError("new Media ID must be 1..65535")
        edits.append((o+0x103,struct.pack("<H",media_id)))
    if duration_units is not None: edits.append((o+0x32,struct.pack("<I",duration_units))); edits.append((o+0x10b,struct.pack("<I",duration_units)))
    if rounded_seconds is not None: edits.append((o+0x26,struct.pack("<I",rounded_seconds))); edits.append((o+0x2a,struct.pack("<I",rounded_seconds)))
    return MutationPlan(tuple(sorted(edits)), "resource-patch")

def plan_patch_schedule(image: BinImage, schedule: ScheduleRecord, *, day=None, start=None, end=None,
                        occurrence=None, title=None, comment=None, filename=None, in_point=None,
                        out_point=None) -> MutationPlan:
    edits=[]; o=schedule.offset
    def put(off, value): edits.append((o+off,struct.pack("<I",int(value))))
    if occurrence is not None: put(0,occurrence)
    if day is not None:
        if not 0 <= day <= 6: raise BinValidationError("invalid weekday")
        edits.append((o+0x1a,bytes((day,))))
    if start is not None: put(0x1b,start); put(0x1f,start)
    if end is not None: put(0x23,end)
    if in_point is not None: put(0x3b,in_point); put(0x13a,in_point)
    if out_point is not None: put(0x3f,out_point); put(0x143,out_point)
    if in_point is not None or out_point is not None:
        ip = schedule.in_point if in_point is None else in_point; op = schedule.out_point if out_point is None else out_point
        if not 0 <= ip < op: raise BinValidationError("in-point must precede out-point")
        duration=(op-ip + 29)//30; put(0x33,duration); put(0x13e,duration)
        if start is not None or end is not None: put(0x23, (start if start is not None else schedule.start)+duration)
    for off,size,value in ((0x57,64,title),(0x97,32,comment),(0xd7,99,filename)):
        if value is not None:
            b=bytearray(size); _text(b,0,size,value,"schedule text"); edits.append((o+off,bytes(b)))
    return MutationPlan(tuple(sorted(edits)), "schedule-patch")
