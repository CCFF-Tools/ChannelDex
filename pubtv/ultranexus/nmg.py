"""Conservative parser/writer for the qualified video NMG layout."""
from dataclasses import dataclass
from pathlib import Path
import struct
from .exceptions import CompatibilityError, ValidationError
from .utils import sha256_bytes, validate_ascii, validate_filename
from .mutation import MutationError, MutationPlan, apply_mutation_plan

MAGIC = b"WinLGX 7"
VERSION_OFFSET = 0x80
SCHEDULE_BASE, SCHEDULE_STRIDE, SCHEDULE_CAPACITY = 46586, 574, 3000
RESOURCE_BASE, RESOURCE_STRIDE, RESOURCE_CAPACITY = 31096976, 518, 6000

def _u32(buf, off): return struct.unpack_from("<I", buf, off)[0]
def _put32(buf, off, value): struct.pack_into("<I", buf, off, int(value))
def _text(buf, off, size): return bytes(buf[off:off+size]).split(b"\0", 1)[0].decode("ascii", "replace")
def _write(buf, off, size, value):
    validate_ascii(value, max_bytes=size, field="NMG text", allow_empty=True)
    encoded = value.encode("ascii") + b"\0"
    if len(encoded) > size:
        raise ValidationError("NMG text does not fit including NUL terminator")
    buf[off:off+size] = encoded + b"\0" * (size-len(encoded))

@dataclass(frozen=True)
class NMGHeader:
    magic: str
    version: str

@dataclass(frozen=True)
class Resource:
    base: int; reference: int; title: str; filename: str; storage: str; comment: str; media_id: int = 0; duration_units: int = 0; rounded_seconds: int = 0

@dataclass(frozen=True)
class Schedule:
    base: int; occurrence_id: int; reference: int; day: int; start: int; end: int; duration: int; title: str; filename: str; comment: str

class NMGImage:
    def __init__(self, data: bytes, *, expected_sha256=None, expected_version="7.0.3.48"):
        self.data = bytes(data)
        if not self.data.startswith(MAGIC): raise CompatibilityError("unsupported NMG magic")
        version = _text(self.data, VERSION_OFFSET, 32)
        if expected_version is not None and version != expected_version: raise CompatibilityError(f"unsupported NMG version: {version!r}")
        if expected_sha256 and sha256_bytes(self.data) != expected_sha256.lower(): raise CompatibilityError("NMG base SHA-256 mismatch")
        self.header = NMGHeader(MAGIC.decode("ascii"), version)
    @classmethod
    def from_file(cls, path, **kwargs): return cls(Path(path).read_bytes(), **kwargs)
    def manifest(self): return {"sha256": sha256_bytes(self.data), "size": len(self.data), "magic": self.header.magic, "version": self.header.version}
    def apply(self, plan: MutationPlan, *, expected_changes=None):
        try:
            data, audit = apply_mutation_plan(self.data, plan, expected_ranges=expected_changes)
        except MutationError as exc:
            raise ValidationError(str(exc)) from exc
        result = NMGImage(data, expected_version=self.header.version)
        result.validate_restricted()
        return result, audit
    def _resource_bases(self):
        end = min(len(self.data), RESOURCE_BASE + RESOURCE_STRIDE * RESOURCE_CAPACITY)
        return range(RESOURCE_BASE, end, RESOURCE_STRIDE) if len(self.data) > RESOURCE_BASE else ()
    def _require_layout(self):
        if len(self.data) != 34_923_624:
            raise CompatibilityError("NMG writer requires the qualified 34,923,624-byte image layout")
        if RESOURCE_BASE + RESOURCE_STRIDE * RESOURCE_CAPACITY > len(self.data):
            raise CompatibilityError("NMG resource table is truncated")
        if SCHEDULE_BASE + SCHEDULE_STRIDE * SCHEDULE_CAPACITY > len(self.data):
            raise CompatibilityError("NMG schedule table is truncated")
    def validate_restricted(self):
        """Scan both complete qualified tables before or after selected edits."""
        self._require_layout()
        references = set()
        blank_resource = None
        saw_resource_blank = False
        for slot in range(RESOURCE_CAPACITY):
            base = RESOURCE_BASE + slot * RESOURCE_STRIDE
            record = self.data[base:base + RESOURCE_STRIDE]
            group, reference = struct.unpack_from("<II", record)
            title = _text(record, 0x43, 32)
            if group == reference == 0:
                if title != "<Unnamed>" and record != bytes(RESOURCE_STRIDE):
                    raise CompatibilityError(f"unrecognized NMG resource blank at slot {slot}")
                if blank_resource is None:
                    blank_resource = record
                elif record != blank_resource:
                    raise CompatibilityError(f"inconsistent NMG resource blank at slot {slot}")
                saw_resource_blank = True
                continue
            if saw_resource_blank or not group or not reference or not title or reference in references:
                raise CompatibilityError(f"invalid or sparse NMG resource at slot {slot}")
            references.add(reference)
        saw_schedule_blank = False
        for slot in range(SCHEDULE_CAPACITY):
            base = SCHEDULE_BASE + slot * SCHEDULE_STRIDE
            record = self.data[base:base + SCHEDULE_STRIDE]
            title = _text(record, 0x57, 64)
            if not title:
                if record != bytes(SCHEDULE_STRIDE):
                    raise CompatibilityError(f"unrecognized NMG schedule blank at slot {slot}")
                saw_schedule_blank = True
                continue
            if saw_schedule_blank or record[0x1a] > 6:
                raise CompatibilityError(f"invalid or sparse NMG schedule at slot {slot}")
            occurrence, reference = struct.unpack_from("<II", record)
            start, end, duration = (_u32(record, field) for field in (0x1b, 0x23, 0x33))
            if end not in (start + duration, start + duration - 7 * 86400):
                raise CompatibilityError(f"invalid NMG interval at slot {slot}")
            if title.startswith("Switchback"):
                if occurrence or reference:
                    raise CompatibilityError(f"invalid NMG Switchback at slot {slot}")
            elif not occurrence or reference not in references or any(
                _u32(record, left) != _u32(record, right)
                for left, right in ((0x1b, 0x1f), (0x33, 0x13e), (0x3b, 0x13a), (0x3f, 0x143))
            ):
                raise CompatibilityError(f"invalid NMG executable event at slot {slot}")
        return True
    def resources(self):
        out=[]
        for base in self._resource_bases():
            ref = _u32(self.data, base+4)
            title = _text(self.data, base+0x43, 32)
            if ref and title: out.append(Resource(base, ref, title, _text(self.data, base+0xA3, 110), _text(self.data, base+0x83, 32), _text(self.data, base+0x111, 32), struct.unpack_from("<H", self.data, base+0x103)[0], _u32(self.data,base+0x32), _u32(self.data,base+0x26)))
        return out
    def schedules(self):
        out=[]; end=min(len(self.data), SCHEDULE_BASE + SCHEDULE_STRIDE*SCHEDULE_CAPACITY)
        for base in range(SCHEDULE_BASE, end, SCHEDULE_STRIDE):
            ref=_u32(self.data,base+4)
            title=_text(self.data,base+0x57,64)
            if title: out.append(Schedule(base,_u32(self.data,base),ref,self.data[base+0x1a],_u32(self.data,base+0x1b),_u32(self.data,base+0x23),_u32(self.data,base+0x33),title,_text(self.data,base+0xd7,99),_text(self.data,base+0x97,32)))
        return out
    def append_resource(self, *, template: Resource, title, filename, storage="Vol1", comment="", reference=None, media_id=0, duration_units=None, rounded_seconds=None, video_bitrate=None, width=None, height=None):
        self._require_layout()
        validate_ascii(title, max_bytes=32, field="title"); validate_filename(filename); validate_ascii(storage,max_bytes=32,field="storage"); validate_ascii(comment,max_bytes=32,field="comment")
        target = next((b for b in self._resource_bases() if _u32(self.data,b+4)==0), None)
        if target is None: raise ValidationError("NMG resource table has no verified blank slot")
        if reference is None: reference = max([r.reference for r in self.resources()] or [0])+1
        if any(r.reference==reference for r in self.resources()): raise ValidationError("resource reference already exists")
        output=bytearray(self.data); output[target:target+RESOURCE_STRIDE]=self.data[template.base:template.base+RESOURCE_STRIDE]
        _put32(output,target+4,reference)
        _write(output,target+0x43,32,title); _write(output,target+0xa3,110,filename); _write(output,target+0x83,32,storage); _write(output,target+0x111,32,comment)
        struct.pack_into("<H", output, target+0x103, media_id)
        if duration_units is not None:
            _put32(output, target+0x32, duration_units); _put32(output, target+0x10b, duration_units)
        if rounded_seconds is not None:
            _put32(output, target+0x26, rounded_seconds); _put32(output, target+0x2a, rounded_seconds)
        if video_bitrate is not None: _put32(output, target+0x3a, video_bitrate)
        if width is not None: struct.pack_into("<H", output, target+0x150, width)
        if height is not None: struct.pack_into("<H", output, target+0x152, height)
        return NMGImage(bytes(output), expected_version=self.header.version)
    def insert_schedule(self, *, template_base, target_base, resource: Resource, reference=None, occurrence_id, day, start, duration, title, filename, comment="", in_frames=0, out_frames=None, expected_sha256=None, template_record=None):
        """Insert inside a qualified Switchback, preserving leading/remainder gaps."""
        self._require_layout()
        if expected_sha256 and sha256_bytes(self.data) != expected_sha256.lower(): raise CompatibilityError("NMG base SHA-256 mismatch")
        if not 0 <= day <= 6 or start < 0 or duration <= 0: raise ValidationError("invalid schedule time")
        if target_base < SCHEDULE_BASE or (target_base-SCHEDULE_BASE) % SCHEDULE_STRIDE: raise ValidationError("target is not an aligned schedule record")
        if template_record is None:
            template_record = self.data[template_base:template_base+SCHEDULE_STRIDE]
        template_record = bytes(template_record)
        if len(template_record) != SCHEDULE_STRIDE or _text(template_record,0x57,64).startswith("Switchback"):
            raise ValidationError("template must be one complete non-Switchback schedule record")
        target = target_base
        existing = Schedule(target,_u32(self.data,target),_u32(self.data,target+4),self.data[target+0x1a],_u32(self.data,target+0x1b),_u32(self.data,target+0x23),_u32(self.data,target+0x33),_text(self.data,target+0x57,64),_text(self.data,target+0xd7,99),_text(self.data,target+0x97,32))
        if existing.day != day or not existing.title.startswith("Switchback") or start < existing.start or existing.end < start + duration:
            raise ValidationError("target must be a same-day Switchback containing the requested interval")
        if resource.base < RESOURCE_BASE or resource.reference == 0: raise ValidationError("resource must be a qualified parsed resource")
        if reference is None: reference = resource.reference
        if any(s.occurrence_id == occurrence_id for s in self.schedules()): raise ValidationError("occurrence identifier already exists")
        if any(s.day == day and not (s.end <= start or s.start >= start + duration) for s in self.schedules() if s.base != target): raise ValidationError("schedule overlap")
        active = [s.base for s in self.schedules()]
        last = max(active + [target])
        leading_duration = start - existing.start
        inserted_count = 2 if leading_duration > 0 else 1
        schedule_end = SCHEDULE_BASE + SCHEDULE_STRIDE*SCHEDULE_CAPACITY
        for index in range(1, inserted_count + 1):
            blank = last + index*SCHEDULE_STRIDE
            if blank >= schedule_end or _u32(self.data, blank+4) != 0 or _text(self.data, blank+0x57,64):
                raise ValidationError("schedule capacity exhausted or shift destination is not blank")
        if out_frames is None: out_frames = in_frames + duration*30
        output=bytearray(self.data)
        for base in range(last, target, -SCHEDULE_STRIDE):
            destination = base + inserted_count*SCHEDULE_STRIDE
            output[destination:destination+SCHEDULE_STRIDE] = self.data[base:base+SCHEDULE_STRIDE]
        program = target
        if leading_duration > 0:
            _put32(output,target+0x23,start); _put32(output,target+0x33,leading_duration)
            hours, rem = divmod(leading_duration,3600); minutes, seconds = divmod(rem,60)
            _write(output,target+0x57,64,f"Switchback {hours}:{minutes:02d}:{seconds:02d}")
            program += SCHEDULE_STRIDE
        output[program:program+SCHEDULE_STRIDE]=template_record
        _put32(output,program,occurrence_id); _put32(output,program+4,reference); output[program+0x1a]=day
        for off,val in ((0x1b,start),(0x1f,start),(0x23,start+duration),(0x33,duration),(0x3b,in_frames),(0x3f,out_frames),(0x13a,in_frames),(0x13e,duration),(0x143,out_frames)): _put32(output,program+off,val)
        for off,size,val in ((0x57,64,title),(0x97,32,comment),(0xd7,99,filename)):_write(output,program+off,size,val)
        # Preserve the selected resource's qualified technical/storage block.
        output[program+0x4e:program+0x57] = self.data[resource.base+0x3a:resource.base+0x43]
        _write(output,program+0xb7,32,_text(self.data,resource.base+0x83,32))
        # Always leave a remainder switchback, including a zero-length exact fill.
        remainder = program + SCHEDULE_STRIDE
        output[remainder:remainder+SCHEDULE_STRIDE] = self.data[target:target+SCHEDULE_STRIDE]
        remainder_start = start + duration
        _put32(output,remainder+0x1b,remainder_start); _put32(output,remainder+0x1f,remainder_start); _put32(output,remainder+0x23,existing.end)
        _put32(output,remainder+0x33,max(0, existing.end - remainder_start)); _put32(output,remainder+0x13e,max(0, existing.end - remainder_start))
        _put32(output,remainder+0x3b,0); _put32(output,remainder+0x3f,0); _put32(output,remainder+0x13a,0); _put32(output,remainder+0x143,0)
        day_name = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")[day]
        remainder_duration = max(0, existing.end - remainder_start)
        hours, rem = divmod(remainder_duration, 3600); minutes, seconds = divmod(rem, 60)
        _write(output,remainder+0x57,64,f"Switchback {hours}:{minutes:02d}:{seconds:02d}")
        return NMGImage(bytes(output),expected_version=self.header.version)

    def remove_schedule(self, *, target_base: int, expected_record_sha256: str):
        """Replace one bound video event with a qualified same-day Switchback."""
        from hashlib import sha256
        self._require_layout()
        if target_base < SCHEDULE_BASE or (target_base - SCHEDULE_BASE) % SCHEDULE_STRIDE:
            raise ValidationError("target is not an aligned schedule record")
        original = self.data[target_base:target_base + SCHEDULE_STRIDE]
        if len(original) != SCHEDULE_STRIDE or sha256(original).hexdigest() != expected_record_sha256.lower():
            raise ValidationError("source NMG schedule record changed")
        selected = next((item for item in self.schedules() if item.base == target_base), None)
        if selected is None or selected.title.startswith("Switchback") or selected.duration <= 0:
            raise ValidationError("source must be a qualified video occurrence")
        template = next((item for item in self.schedules() if item.day == selected.day and item.title.startswith("Switchback")), None)
        if template is None:
            raise ValidationError("a same-day qualified Switchback template is required")
        output = bytearray(self.data)
        output[target_base:target_base + SCHEDULE_STRIDE] = self.data[template.base:template.base + SCHEDULE_STRIDE]
        for off, value in ((0x1b, selected.start), (0x1f, selected.start),
                           (0x23, selected.end), (0x33, selected.duration),
                           (0x13e, selected.duration), (0x3b, 0), (0x13a, 0),
                           (0x3f, 0), (0x143, 0)):
            _put32(output, target_base + off, value)
        output[target_base + 0x1a] = selected.day
        hours, rem = divmod(selected.duration, 3600)
        minutes, seconds = divmod(rem, 60)
        _write(output, target_base + 0x57, 64, f"Switchback {hours}:{minutes:02d}:{seconds:02d}")
        return NMGImage(bytes(output), expected_version=self.header.version)

def validate_nmg(data: bytes, *, expected_sha256=None, expected_version="7.0.3.48") -> dict:
    image=NMGImage(data,expected_sha256=expected_sha256,expected_version=expected_version)
    return {**image.manifest(),"resources":len(image.resources()),"schedules":len(image.schedules())}
