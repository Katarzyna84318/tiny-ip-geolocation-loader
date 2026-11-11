# tiny-ip-geolocation-loader

Load an IP-to-country CSV into a sorted index and look up an address with a
binary search over the range start addresses.

```python
import io

from tiny_ip_geolocation_loader import load_csv, IPRangeIndex, IPRange, ip_to_int

csv_data = """start,end,country
10.0.0.0,10.0.0.255,AA
203.0.113.0,203.0.113.255,CC
"""

index = load_csv(io.StringIO(csv_data))  # header must name start, end, country

index.lookup("203.0.113.7")       # -> "CC" or None
index.lookup_int(ip_to_int("10.0.0.5"))  # -> "AA" or None

# You can also build an index by hand:
manual = IPRangeIndex([
    IPRange(start=ip_to_int("10.0.0.0"), end=ip_to_int("10.0.0.255"), country="AA"),
])
manual.lookup("10.0.0.1")         # -> "AA"
```

The CSV is read from an already-open text stream. The header row must contain
columns named `start`, `end` and `country` (case-insensitive, whitespace
trimmed); their order in the file does not matter. Addresses are inclusive
dotted-quad IPv4 literals. Blank lines are skipped. Malformed rows raise
`ValueError` with the offending line number.

## Why this exists

Looking up a country for an address in a flat CSV is a linear scan, which is
fine for a few hundred rows and painful for the multi-million-row tables that
RIR and commercial feeds ship. The fix is to sort by the numeric start address
and binary search, but doing that ad hoc every time means re-parsing the file
and re-sorting on each lookup.

This library does the parse-and-sort once and hands back an immutable index.
The trade-off is memory: the index keeps every range in memory as a dataclass
plus a parallel list of start integers, so it does not stream and it is not
suitable for tables that do not fit in RAM. In exchange, lookups are O(log n)
and the index is cheap to share between threads because nothing mutates after
construction.

## The awkward edge: overlapping ranges

Real feeds contain overlapping ranges. This library does **not** merge them.
When two ranges overlap, the one with the greater start address wins for the
overlapping region, because lookup finds the last start that is `<=` the query
and checks only that range. Merging would silently paper over bad data; keeping
the rightmost-start rule is predictable and lets you detect overlaps yourself
by walking `index.ranges`.

## API

- `ip_to_int(address: str) -> int` — parse a dotted-quad IPv4 literal. Raises
  `ValueError` for IPv6 or anything malformed.
- `IPRange(start: int, end: int, country: str)` — frozen dataclass, inclusive
  bounds. Validates ordering and non-empty country at construction.
- `IPRangeIndex(ranges)` — sorts the ranges by start. Supports `len()`,
  iteration, and a `ranges` property returning a tuple.
  - `lookup(address: str) -> str | None` — raises `ValueError` on invalid
    addresses, returns `None` for valid addresses outside every range.
  - `lookup_int(value: int) -> str | None` — same, for pre-parsed integers;
    values outside `0..2**32-1` return `None`.
- `load_csv(source) -> IPRangeIndex` — parse an open text stream into an index.
