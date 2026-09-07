#!/usr/bin/env python3
"""In-memory lookup index over registry/events.xml.

Small enough (10,433 occurrences, ~9MB as of the crosstables-derived build)
to load as a single DOM tree, unlike the player registry.

Two-tier match, cheapest first:
1. Exact ref hit: the importer already computes
   event:<start.compact()>-<end.compact()>-<slug(name)> for every
   tournament using the same PartialDate.compact() convention
   scripts/seed_event_registry.py used to build this registry in the first
   place (both precision-aware -- a year-only date and a day-precision date
   compact() to different strings, so this only matches when precision
   agrees too). If an incoming tournament's own synthesized ref is already
   a key in the registry, that's an exact, high-confidence match with no
   fuzzy logic involved.
2. Normalized-name + date-overlap: covers the case where the incoming
   source spells the event differently than whatever seeded the registry
   (e.g. Mega Database's "Event" text vs. TWIC's), or where precision
   differs. Dates are approximated (unknown month/day -> 1) for the overlap
   comparison only. Matches only when the incoming date range overlaps the
   candidate occurrence's date range, to avoid conflating two different
   editions of an annual event that happen to share a name.
3. Slug-prefix + date-overlap: TWIC's own crosstable-header convention
   (which seeded most of this registry) bakes place and date text into the
   occurrence name itself, e.g. "Dortmund GER (GER), 9-17 vii 1999", while
   Mega Database's Event tag for the same tournament is just "Dortmund".
   Tier 2's exact-slug match can never bridge that -- slug("dortmund") !=
   slug("dortmund-ger-ger-9-17-vii-1999") -- so this tier additionally
   checks whether the incoming slug is a prefix of a candidate's slug (on a
   "-" boundary), still gated by date overlap so a bare "dortmund" doesn't
   match every Dortmund-prefixed event ever recorded regardless of year.
"""

from __future__ import annotations

import datetime
import sys
from collections import defaultdict
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctml_source_common import PartialDate, slug

CTML_NS = "urn:ctml:2.0"
NS = {"ctml": CTML_NS}


def _approx(pd: PartialDate) -> datetime.date:
    return datetime.date(max(1, pd.y), pd.m or 1, pd.d or 1)


def _extract_date(el) -> datetime.date | None:
    """el is a <ctml:start> or <ctml:end> element containing a
    year/month/day precision choice element."""
    if el is None:
        return None
    for tag, has_m, has_d in (("day", True, True), ("month", True, False), ("year", False, False)):
        child = el.find(f"ctml:{tag}", NS)
        if child is not None:
            y = int(child.get("y"))
            m = int(child.get("m")) if has_m and child.get("m") else 1
            d = int(child.get("d")) if has_d and child.get("d") else 1
            try:
                return datetime.date(y, m, d)
            except ValueError:
                return datetime.date(y, 1, 1)
    return None


class EventIndex:
    def __init__(self) -> None:
        self.refs: set[str] = set()
        self.by_name: dict[str, list[tuple[str, datetime.date, datetime.date]]] = defaultdict(list)
        self.occurrence_count = 0

    def resolve(self, name: str, start: PartialDate, end: PartialDate) -> tuple[str | None, str]:
        """Returns (matched_ref_or_None, method): 'exact-ref', 'name-date-overlap', or 'unresolved'."""
        candidate_ref = f"event:{start.compact()}-{end.compact()}-{slug(name)}"
        if candidate_ref in self.refs:
            return candidate_ref, "exact-ref"

        key = slug(name)
        approx_start, approx_end = _approx(start), _approx(end)
        for ref, o_start, o_end in self.by_name.get(key, []):
            if approx_start <= o_end and o_start <= approx_end:
                return ref, "name-date-overlap"

        prefix = key + "-"
        for cand_key, entries in self.by_name.items():
            if cand_key != key and not cand_key.startswith(prefix):
                continue
            for ref, o_start, o_end in entries:
                if approx_start <= o_end and o_start <= approx_end:
                    return ref, "slug-prefix-date-overlap"
        return None, "unresolved"


def build_index(events_path: Path) -> EventIndex:
    idx = EventIndex()
    doc = etree.parse(str(events_path))
    for occ in doc.getroot().findall("ctml:eventOccurrence", NS):
        ref = occ.get("ref")
        idx.refs.add(ref)
        idx.occurrence_count += 1
        name_el = occ.find("ctml:name", NS)
        name = name_el.text if name_el is not None and name_el.text else None
        dates_el = occ.find("ctml:dates", NS)
        start = _extract_date(dates_el.find("ctml:start", NS)) if dates_el is not None else None
        end = _extract_date(dates_el.find("ctml:end", NS)) if dates_el is not None else None
        if name and start and end:
            idx.by_name[slug(name)].append((ref, start, end))
        # Also index every alias spelling the same way.
        for alias in occ.findall("ctml:aliases/ctml:alias", NS):
            if alias.text and start and end:
                idx.by_name[slug(alias.text)].append((ref, start, end))
    return idx
