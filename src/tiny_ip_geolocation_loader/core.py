"""Core loading and lookup logic for tiny-ip-geolocation-loader.

Design notes
------------
The CSV format accepted here is the common "start,end,country" triple where
both addresses are inclusive dotted-quad IPv4 literals.  Rows are sorted by
their start address (as a 32-bit integer) and looked up with :func:`bisect_right`
on a parallel list of start integers.  We keep the start integers in their own
list because bisect needs a plain sequence and re-deriving the integer from the
range object on every probe would waste time on the hot path.

Overlapping ranges are not merged.  When ranges overlap, the first matching
range in sorted order wins.  This is a deliberate choice: merging would hide
malformed data sources, and the loader's job is to be a faithful index, not a
sanitiser.  Callers who want to detect overlaps can compare adjacent entries.
"""

from __future__ import annotations

import csv
import ipaddress
from bisect import bisect_right
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Sequence, TextIO, Tuple

__all__ = ["IPRange", "IPRangeIndex", "load_csv", "ip_to_int"]


def ip_to_int(address: str) -> int:
    """Convert a dotted-quad IPv4 string to its unsigned 32-bit integer value.

    Raises :class:`ValueError` for anything that is not a plain IPv4 address.
    IPv6 is rejected on purpose: the CSV sources this loader targets are
    IPv4-only, and silently accepting v6 would produce nonsense comparisons.
    """
    try:
        parsed = ipaddress.IPv4Address(address.strip())
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ValueError(f"not a valid IPv4 address: {address!r}") from exc
    return int(parsed)


@dataclass(frozen=True)
class IPRange:
    """A single inclusive IPv4 range and the country code assigned to it."""

    start: int
    end: int
    country: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.start > 0xFFFFFFFF:
            raise ValueError(f"start out of range: {self.start}")
        if self.end < 0 or self.end > 0xFFFFFFFF:
            raise ValueError(f"end out of range: {self.end}")
        if self.end < self.start:
            raise ValueError(
                f"end {self.end} precedes start {self.start}"
            )
        if not self.country:
            raise ValueError("country must be a non-empty string")

    def contains(self, value: int) -> bool:
        """Return True when *value* falls inside this inclusive range."""
        return self.start <= value <= self.end


class IPRangeIndex:
    """Sorted index over :class:`IPRange` objects for binary-search lookup.

    The index stores ranges sorted by ``start``.  Lookup finds the last range
    whose start is <= the query, then checks whether the query is also <= that
    range's end.  Because ranges may overlap, we do not assume the match is
    unique; the rightmost start that qualifies is the one compared.
    """

    def __init__(self, ranges: Iterable[IPRange]) -> None:
        ordered = sorted(ranges, key=lambda r: (r.start, r.end))
        self._ranges: List[IPRange] = list(ordered)
        # Parallel list of starts so bisect can operate on plain ints.
        self._starts: List[int] = [r.start for r in self._ranges]

    def __len__(self) -> int:
        return len(self._ranges)

    def __iter__(self) -> Iterator[IPRange]:
        return iter(self._ranges)

    @property
    def ranges(self) -> Tuple[IPRange, ...]:
        """Immutable view of the indexed ranges in sorted order."""
        return tuple(self._ranges)

    def lookup_int(self, value: int) -> Optional[str]:
        """Return the country for the integer *value*, or ``None``.

        Only the range with the greatest start <= *value* is considered.  With
        overlapping input the later-starting range shadows the earlier one,
        which matches how longest-prefix-ish tables are usually read.
        """
        if value < 0 or value > 0xFFFFFFFF:
            return None
        idx = bisect_right(self._starts, value) - 1
        if idx < 0:
            return None
        candidate = self._ranges[idx]
        if candidate.contains(value):
            return candidate.country
        return None

    def lookup(self, address: str) -> Optional[str]:
        """Return the country for a dotted-quad *address*, or ``None``.

        Raises :class:`ValueError` when the address is not valid IPv4; an
        unknown-but-valid address simply returns ``None``.
        """
        return self.lookup_int(ip_to_int(address))


_REQUIRED_COLUMNS = ("start", "end", "country")


def _read_rows(stream: TextIO) -> Iterator[IPRange]:
    reader = csv.reader(stream)
    try:
        header = next(reader)
    except StopIteration:
        return
    normalised = [col.strip().lower() for col in header]
    try:
        start_i = normalised.index("start")
        end_i = normalised.index("end")
        country_i = normalised.index("country")
    except ValueError as exc:
        raise ValueError(
            "CSV header must contain start, end and country columns"
        ) from exc

    for line_no, row in enumerate(reader, start=2):
        if not row or all(cell.strip() == "" for cell in row):
            continue
        if len(row) <= max(start_i, end_i, country_i):
            raise ValueError(
                f"line {line_no}: expected at least "
                f"{max(start_i, end_i, country_i) + 1} columns, got {len(row)}"
            )
        start_text = row[start_i].strip()
        end_text = row[end_i].strip()
        country = row[country_i].strip()
        if not start_text or not end_text:
            raise ValueError(f"line {line_no}: empty address field")
        try:
            start = ip_to_int(start_text)
            end = ip_to_int(end_text)
        except ValueError as exc:
            raise ValueError(f"line {line_no}: {exc}") from exc
        try:
            yield IPRange(start=start, end=end, country=country)
        except ValueError as exc:
            raise ValueError(f"line {line_no}: {exc}") from exc


def load_csv(source: TextIO) -> IPRangeIndex:
    """Load an IP-to-country CSV from an open text stream.

    The stream must have a header row naming at least ``start``, ``end`` and
    ``country``.  Column order is not assumed; names are matched
    case-insensitively after stripping whitespace.  Blank lines are skipped.

    Returns an :class:`IPRangeIndex`.  Raises :class:`ValueError` on malformed
    rows so the caller can decide whether to abort or skip the file.
    """
    return IPRangeIndex(_read_rows(source))
