#!/usr/bin/env python3
"""In-memory lookup index over the sharded CTML place registry
(registry/places/places-{countries,states,cities-<ISO3>}.xml).

Only city/admin2-level entries are indexed for Site-string resolution
(tournament venues are cities, not countries or states). Matching is plain
normalized-name equality; a Site string that matches more than one distinct
city (e.g. "Paris" -> France and several in the US) is left unresolved
rather than guessed, same policy as the player resolver.

Known limitation, not solved here: PGN sources that carry a country signal
(Mega Database's EventCountry, e.g. "FRA"/"ALG"/"BUL") use FIDE federation
codes, not the ISO 3166-1 alpha-3 codes this registry is keyed on -- they
overlap for most countries but not all (e.g. FIDE "ALG" vs ISO3 "DZA" for
Algeria). Using EventCountry to disambiguate same-named cities would need a
FIDE-code -> ISO3 mapping table first; deferred rather than guessed at
build time. See docs/HANDOFF.md.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from lxml import etree

CTML_NS = "urn:ctml:2.0"
NS = {"ctml": CTML_NS}


def normalize_place_key(raw: str) -> str:
    text = raw.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


class PlaceIndex:
    def __init__(self) -> None:
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.place_count = 0

    def add(self, ref: str, name: str) -> None:
        key = normalize_place_key(name)
        if ref not in self.by_name[key]:
            self.by_name[key].append(ref)

    def resolve(self, raw_site: str) -> tuple[str | None, str]:
        key = normalize_place_key(raw_site)
        candidates = self.by_name.get(key)
        if candidates and len(candidates) == 1:
            return candidates[0], "name-exact"
        return None, "unresolved"


def build_index(places_dir: Path) -> PlaceIndex:
    idx = PlaceIndex()
    for shard in sorted(places_dir.glob("places-cities-*.xml")):
        for _, elem in etree.iterparse(str(shard), tag=f"{{{CTML_NS}}}place"):
            ref = elem.get("ref")
            name_el = elem.find("ctml:name", NS)
            if name_el is not None and name_el.text:
                idx.add(ref, name_el.text)
                idx.place_count += 1
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                while elem.getprevious() is not None:
                    del parent[0]
    return idx
