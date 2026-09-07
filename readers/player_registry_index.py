#!/usr/bin/env python3
"""In-memory lookup index over the sharded CTML player registry.

Built once per import run from registry/players/players-*.xml (617,358
players as of the .ssp-derived build). Streams each shard with
lxml.etree.iterparse rather than loading full DOM trees, since holding all
27 files as parsed trees simultaneously would be wasteful.

Resolution cascade (matches the ResolutionMethodType vocabulary in
xsd/ctml-vocab.xsd and the design already described in
docs/HANDOFF.md/corpus-policy.md): fide-id is handled by the caller (most
PGN sources with no id tag skip straight to this index); this module
implements name-exact (display or alias name, normalized, unique match),
surname-unique (single-token query name, unique among that surname), and
otherwise leaves the caller to mark the participant unresolved. No fuzzy/
edit-distance matching -- ambiguous or unmatched names are meant to surface
as registry candidates for later curation, not be guessed at import time.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from lxml import etree

CTML_NS = "urn:ctml:2.0"
NS = {"ctml": CTML_NS}


def normalize_name_key(raw: str) -> str:
    text = raw.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.replace(".", "")
    return text


def surname_key(raw: str) -> str | None:
    """Only meaningful for a bare single-token name (no comma, one word)."""
    text = raw.strip()
    if "," in text or " " in text:
        return None
    return normalize_name_key(text)


class PlayerIndex:
    def __init__(self) -> None:
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.by_surname: dict[str, list[str]] = defaultdict(list)
        self.player_count = 0

    def _add_name(self, key: str, ref: str) -> None:
        if ref not in self.by_name[key]:
            self.by_name[key].append(ref)

    def add(self, ref: str, display_names: list[str], family: str | None) -> None:
        for name in display_names:
            if not name:
                continue
            self._add_name(normalize_name_key(name), ref)
        if family:
            fam_key = normalize_name_key(family)
            if ref not in self.by_surname[fam_key]:
                self.by_surname[fam_key].append(ref)

    def resolve(self, raw_name: str) -> tuple[str | None, str]:
        """Returns (ref_or_None, ResolutionMethodType value)."""
        key = normalize_name_key(raw_name)
        candidates = self.by_name.get(key)
        if candidates and len(candidates) == 1:
            return candidates[0], "name-exact"
        sk = surname_key(raw_name)
        if sk:
            surname_candidates = self.by_surname.get(sk)
            if surname_candidates and len(surname_candidates) == 1:
                return surname_candidates[0], "surname-unique"
        return None, "unresolved"


def build_index(players_dir: Path) -> PlayerIndex:
    idx = PlayerIndex()
    shards = sorted(players_dir.glob("players-*.xml"))
    for shard in shards:
        for _, elem in etree.iterparse(str(shard), tag=f"{{{CTML_NS}}}player"):
            ref = elem.get("ref")
            names: list[str] = []
            name_el = elem.find("ctml:name", NS)
            family = None
            if name_el is not None:
                display = name_el.get("display")
                if display:
                    names.append(display)
                family_el = name_el.find("ctml:family", NS)
                if family_el is not None and family_el.text:
                    family = family_el.text
            for alias_el in elem.findall("ctml:aliases/ctml:alias", NS):
                display = alias_el.get("display")
                if display:
                    names.append(display)
            idx.add(ref, names, family)
            idx.player_count += 1
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                while elem.getprevious() is not None:
                    del parent[0]
    return idx
