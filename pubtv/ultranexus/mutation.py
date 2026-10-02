"""Format-neutral immutable byte mutation plans and audit manifests."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable


class MutationError(ValueError):
    pass


def diff_ranges(before: bytes, after: bytes) -> tuple[tuple[int, int], ...]:
    if len(before) != len(after):
        raise MutationError("cannot diff buffers of different lengths")
    ranges = []
    start = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        if left == right and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(before)))
    return tuple(ranges)


@dataclass(frozen=True)
class MutationPlan:
    """Sorted, non-overlapping absolute byte replacements."""

    changes: tuple[tuple[int, bytes], ...]
    label: str = ""

    def __post_init__(self):
        if tuple(sorted(self.changes, key=lambda item: item[0])) != self.changes:
            raise MutationError("mutation changes must be sorted")
        previous_end = -1
        for offset, value in self.changes:
            if offset < 0 or offset < previous_end or not isinstance(value, bytes) or not value:
                raise MutationError("invalid mutation range")
            previous_end = offset + len(value)

    @classmethod
    def between(cls, before: bytes, after: bytes, label: str = "") -> "MutationPlan":
        return cls(tuple((start, after[start:end]) for start, end in diff_ranges(before, after)), label)


def apply_mutation_plan(base: bytes, plan: MutationPlan, *,
                        expected_ranges: Iterable[tuple[int, int]] | None = None) -> tuple[bytes, dict]:
    output = bytearray(base)
    for offset, value in plan.changes:
        if offset + len(value) > len(output):
            raise MutationError("mutation outside image")
        output[offset:offset + len(value)] = value
    result = bytes(output)
    ranges = diff_ranges(base, result)
    if expected_ranges is not None and ranges != tuple(expected_ranges):
        raise MutationError("undeclared byte changes")
    return result, {"label": plan.label, "ranges": ranges, "sha256": sha256(result).hexdigest()}
