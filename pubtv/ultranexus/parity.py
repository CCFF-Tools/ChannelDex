"""Logical NMG/BIN parity for the qualified output-1 ordinary-video timeline."""
from __future__ import annotations

from collections import Counter
import struct


WEEK_SECONDS = 7 * 86400


class ParityError(ValueError):
    pass


def _canonical_end(start: int, raw_end: int, duration: int) -> int:
    expected = start + duration
    if raw_end not in (expected, expected - WEEK_SECONDS):
        raise ParityError("record end does not match its qualified week-wrap duration")
    return expected


def _event_signature(image, record, *, offset_attr: str) -> tuple:
    offset = getattr(record, offset_attr)
    data = image.data
    end = _canonical_end(record.start, record.end, record.duration)
    return (
        record.reference, record.day, record.start, end, record.duration,
        struct.unpack_from("<I", data, offset + 0x3B)[0],
        struct.unpack_from("<I", data, offset + 0x3F)[0],
        record.title, record.filename, record.comment,
    )


def analyze_nmg_timeline(image) -> dict:
    """Validate adjacency/overlap and describe retained virtual-channel coverage."""
    rows = []
    for record in image.schedules():
        end = _canonical_end(record.start, record.end, record.duration)
        switchback = record.title.startswith("Switchback")
        if not switchback and record.duration <= 0:
            raise ParityError("executable NMG event must have positive duration")
        rows.append((record.start, end, switchback, record.title, record.base))
    rows.sort(key=lambda item: (item[0], item[1], item[4]))
    gaps = []
    exact_adjacencies = 0
    previous_end = None
    for start, end, _switchback, _title, _base in rows:
        if end < start:
            raise ParityError("NMG interval ends before it starts")
        if previous_end is not None:
            if start < previous_end:
                raise ParityError("NMG output-1 intervals overlap")
            if start == previous_end:
                exact_adjacencies += 1
            else:
                gaps.append([previous_end, start])
        previous_end = max(previous_end or end, end)
    switchbacks = [[start, end] for start, end, is_switchback, _title, _base in rows
                   if is_switchback and end > start]
    zero_length_switchbacks = sum(1 for start, end, is_switchback, _title, _base in rows
                                  if is_switchback and end == start)
    return {
        "records": len(rows),
        "executable_events": sum(not item[2] for item in rows),
        "switchbacks": len(rows) - sum(not item[2] for item in rows),
        "switchback_intervals": switchbacks,
        "zero_length_switchbacks": zero_length_switchbacks,
        "exact_adjacencies": exact_adjacencies,
        "intentional_uncovered_gaps": gaps,
    }


def validate_nmg_bin_parity(nmg, binary) -> dict:
    """Require exact executable-event equivalence and valid NMG gap coverage."""
    timeline = analyze_nmg_timeline(nmg)
    nmg_events = Counter(
        _event_signature(nmg, record, offset_attr="base")
        for record in nmg.schedules() if not record.title.startswith("Switchback")
    )
    bin_events = Counter(
        _event_signature(binary, record, offset_attr="offset")
        for record in binary.executable_schedules
    )
    if nmg_events != bin_events:
        raise ParityError("NMG/BIN executable events are not logically equivalent")
    ordered = sorted((signature[2], signature[3]) for signature in bin_events.elements())
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ParityError("BIN executable events overlap")
    return {
        "status": "passed",
        "output": 1,
        "event_count": sum(nmg_events.values()),
        "timeline": timeline,
        "weekday_epoch": "thursday-based-weekly-seconds",
    }
