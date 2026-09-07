#!/usr/bin/env python3
"""Dedup-safe writer for CTML tournament documents in the OTB corpus
(corpus/otb/ by convention).

Implements the roadmap's "as overlapping/duplicate sources are processed,
append new data into the relevant existing CTML files instead of creating
duplicates" step. Two levels of identity:

- Tournament-level: one file per event ref (resolved registry ref if the
  importer matched one, else the same synthesized event:<start>-<end>-
  <slug> ref used everywhere else in this project). A second import run
  that resolves to the same event ref lands in the same file automatically.
- Game-level, within a tournament: identity is (white participant ref,
  black participant ref, round) -- the natural "one game per board per
  round per pairing" slot a real tournament has. This is deliberately NOT
  just the trajectory fingerprint alone: an empty or very short game (e.g.
  an aborted game) would fingerprint-collide with any other equally short
  game between different players, so the slot key is the real identity and
  the fingerprint is used to confirm/distinguish within a slot:
  - No existing game in that slot -> genuinely new, append it.
  - Existing game, fingerprints match (or either side lacks one) -> same
    game already recorded. If the existing game lacks fingerprints (it
    predates this project adding them) and the incoming one has them,
    enrich the existing record rather than treating it as a new game.
  - Existing game, fingerprints differ -> a genuine DIVERGENCE: two
    sources disagree about what happened in this exact game. Per the
    "pre-hash safety net" principle already on record in docs/HANDOFF.md,
    this is never silently resolved by picking a winner. The existing
    record is left untouched (first-recorded data wins, since it was
    presumably already reviewed by an earlier run) and the conflict is
    logged for a human to investigate -- logged externally (the caller's
    `log` callback), not embedded in the CTML document, since GameType is
    deliberately scoped to "what was recorded," not curation metadata.
"""

from __future__ import annotations

import datetime
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Callable

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctml_source_common import slug

CTML_NS = "urn:ctml:2.0"
NS = {"ctml": CTML_NS}


def event_ref_filename(event_ref: str) -> str:
    body = event_ref[len("event:"):] if event_ref.startswith("event:") else event_ref
    body = re.sub(r"[^A-Za-z0-9_-]", "_", body)
    return f"{body}.xml"


def _qn(tag: str) -> str:
    return f"{{{CTML_NS}}}{tag}"


def _extract_approx_date(el) -> datetime.date | None:
    """el is a <ctml:start> or <ctml:end> element containing a
    year/month/day precision choice element. Unknown month/day -> 1, for
    comparison purposes only (matches event_registry_index.py's approach)."""
    if el is None:
        return None
    for tag in ("day", "month", "year"):
        child = el.find(_qn(tag))
        if child is not None:
            y = int(child.get("y"))
            m = int(child.get("m")) if child.get("m") else 1
            d = int(child.get("d")) if child.get("d") else 1
            try:
                return datetime.date(max(1, y), m, d)
            except ValueError:
                return datetime.date(max(1, y), 1, 1)
    return None


def find_matching_file(corpus_dir: Path, event_name: str, start: datetime.date, end: datetime.date, max_gap_days: int) -> Path | None:
    """Catches the case an exact-filename lookup misses: an event that
    never resolved against the registry gets its corpus filename
    synthesized fresh from each import batch's OWN observed date range
    (readers/event_registry_index.py's exact-ref tier has the same
    property for registry lookups, for the same reason -- see
    docs/HANDOFF.md). A later, fuller capture of the same real tournament
    (e.g. one more round, pushing the end date later) would otherwise
    compute a different ref and create a duplicate file instead of
    extending the existing one. Scans existing corpus files for one whose
    header name slugs the same and whose date range overlaps or is within
    max_gap_days of the incoming range -- the same identity heuristic
    group_tournaments() itself already uses within a single import batch,
    just applied across batches/runs instead of within one."""
    if not corpus_dir.exists():
        return None
    target_slug = slug(event_name)
    for path in corpus_dir.glob("*.xml"):
        try:
            root = etree.parse(str(path)).getroot()
        except etree.XMLSyntaxError:
            continue
        if root.tag != _qn("tournament"):
            continue
        name_el = root.find(f"{_qn('header')}/{_qn('name')}")
        if name_el is None or not name_el.text or slug(name_el.text) != target_slug:
            continue
        dates_el = root.find(f"{_qn('header')}/{_qn('dates')}")
        if dates_el is None:
            continue
        e_start = _extract_approx_date(dates_el.find(_qn("start")))
        e_end = _extract_approx_date(dates_el.find(_qn("end")))
        if e_start is None or e_end is None:
            continue
        if start <= e_end and e_start <= end:
            return path
        gap = max((start - e_end).days, (e_start - end).days)
        if gap <= max_gap_days:
            return path
    return None


def _next_participant_id(participants_el) -> Callable[[], str]:
    max_id = 0
    for p in participants_el.findall(_qn("participant")):
        m = re.match(r"p(\d+)$", p.get("id") or "")
        if m:
            max_id = max(max_id, int(m.group(1)))

    def allocate() -> str:
        nonlocal max_id
        max_id += 1
        return f"p{max_id:04}"

    return allocate


def merge_tournament(
    corpus_dir: Path,
    tournament_xml_str: str,
    event_ref: str,
    log: Callable[[str], None],
    max_gap_days: int = 21,
) -> str:
    """Writes a fresh file, or merges into an existing one for the same
    tournament. Returns a short human-readable status line."""
    corpus_dir.mkdir(parents=True, exist_ok=True)
    incoming_root = etree.fromstring(tournament_xml_str.encode("utf-8"))

    path = corpus_dir / event_ref_filename(event_ref)
    if not path.exists():
        name_el = incoming_root.find(f"{_qn('header')}/{_qn('name')}")
        dates_el = incoming_root.find(f"{_qn('header')}/{_qn('dates')}")
        incoming_start = _extract_approx_date(dates_el.find(_qn("start"))) if dates_el is not None else None
        incoming_end = _extract_approx_date(dates_el.find(_qn("end"))) if dates_el is not None else None
        if name_el is not None and name_el.text and incoming_start and incoming_end:
            matched = find_matching_file(corpus_dir, name_el.text, incoming_start, incoming_end, max_gap_days)
            if matched is not None:
                path = matched

    if not path.exists():
        path.write_text(tournament_xml_str, encoding="utf-8")
        return "created"

    existing_tree = etree.parse(str(path))
    existing_root = existing_tree.getroot()

    # Broaden recorded date coverage if the incoming batch extends past
    # what's currently on file -- "including broadening a tournament's
    # round/game coverage if an earlier source was incomplete" is an
    # explicit roadmap requirement, not just a game-list append.
    existing_dates_el = existing_root.find(f"{_qn('header')}/{_qn('dates')}")
    incoming_dates_el = incoming_root.find(f"{_qn('header')}/{_qn('dates')}")
    if existing_dates_el is not None and incoming_dates_el is not None:
        e_start = _extract_approx_date(existing_dates_el.find(_qn("start")))
        e_end = _extract_approx_date(existing_dates_el.find(_qn("end")))
        i_start = _extract_approx_date(incoming_dates_el.find(_qn("start")))
        i_end = _extract_approx_date(incoming_dates_el.find(_qn("end")))
        if i_start is not None and e_start is not None and i_start < e_start:
            existing_start_el = existing_dates_el.find(_qn("start"))
            existing_dates_el.replace(existing_start_el, deepcopy(incoming_dates_el.find(_qn("start"))))
        if i_end is not None and e_end is not None and i_end > e_end:
            existing_end_el = existing_dates_el.find(_qn("end"))
            existing_dates_el.replace(existing_end_el, deepcopy(incoming_dates_el.find(_qn("end"))))

    existing_participants_el = existing_root.find(_qn("participants"))
    existing_games_el = existing_root.find(_qn("games"))
    incoming_participants_el = incoming_root.find(_qn("participants"))
    incoming_games_el = incoming_root.find(_qn("games"))
    if existing_participants_el is None or existing_games_el is None:
        # Existing doc has no participants/games sections to merge into
        # (e.g. a header-only draft) -- treat incoming as authoritative
        # for those sections rather than guessing how to merge with nothing.
        if existing_participants_el is None and incoming_participants_el is not None:
            existing_root.insert(1, deepcopy(incoming_participants_el))
        if existing_games_el is None and incoming_games_el is not None:
            existing_root.append(deepcopy(incoming_games_el))
        existing_tree.write(str(path), xml_declaration=True, encoding="UTF-8")
        return "created participants/games (existing draft had none)"

    ref_to_id: dict[str, str] = {}
    for p in existing_participants_el.findall(_qn("participant")):
        pref_el = p.find(f"{_qn('playerRef')}")
        if pref_el is not None and pref_el.get("ref"):
            ref_to_id[pref_el.get("ref")] = p.get("id")
    allocate_id = _next_participant_id(existing_participants_el)

    id_to_ref = {v: k for k, v in ref_to_id.items()}
    existing_slots: dict[tuple[str | None, str | None, str | None], etree._Element] = {}
    for g in existing_games_el.findall(_qn("game")):
        w = id_to_ref.get(g.get("white"))
        b = id_to_ref.get(g.get("black"))
        existing_slots[(w, b, g.get("round"))] = g

    incoming_id_to_ref: dict[str, str] = {}
    if incoming_participants_el is not None:
        for p in incoming_participants_el.findall(_qn("participant")):
            pref_el = p.find(_qn("playerRef"))
            incoming_id_to_ref[p.get("id")] = pref_el.get("ref") if pref_el is not None else None

    added_games = 0
    added_participants = 0
    matched_same = 0
    enriched = 0
    divergences = 0

    def ensure_participant(ref: str | None) -> str | None:
        nonlocal added_participants
        if ref is None:
            return None
        if ref in ref_to_id:
            return ref_to_id[ref]
        src_p = None
        for p in incoming_participants_el.findall(_qn("participant")):
            pref_el = p.find(_qn("playerRef"))
            if pref_el is not None and pref_el.get("ref") == ref:
                src_p = p
                break
        if src_p is None:
            return None
        new_id = allocate_id()
        clone = deepcopy(src_p)
        clone.set("id", new_id)
        existing_participants_el.append(clone)
        ref_to_id[ref] = new_id
        added_participants += 1
        return new_id

    def trajectory_value(game_el) -> str | None:
        fp = game_el.find(f"{_qn('fingerprints')}/{_qn('fingerprint')}[@scope='trajectory']")
        return fp.get("value") if fp is not None else None

    if incoming_games_el is not None:
        for g in incoming_games_el.findall(_qn("game")):
            w_ref = incoming_id_to_ref.get(g.get("white"))
            b_ref = incoming_id_to_ref.get(g.get("black"))
            slot = (w_ref, b_ref, g.get("round"))
            existing_game = existing_slots.get(slot)

            if existing_game is None:
                new_white_id = ensure_participant(w_ref)
                new_black_id = ensure_participant(b_ref)
                new_game = deepcopy(g)
                if new_white_id:
                    new_game.set("white", new_white_id)
                if new_black_id:
                    new_game.set("black", new_black_id)
                existing_games_el.append(new_game)
                existing_slots[slot] = new_game
                added_games += 1
                continue

            incoming_traj = trajectory_value(g)
            existing_traj = trajectory_value(existing_game)
            if existing_traj is None and incoming_traj is not None:
                incoming_fp_el = g.find(_qn("fingerprints"))
                if incoming_fp_el is not None:
                    existing_game.append(deepcopy(incoming_fp_el))
                enriched += 1
            elif existing_traj is not None and incoming_traj is not None and existing_traj != incoming_traj:
                divergences += 1
                log(
                    f"DIVERGENCE in {path.name}: round={g.get('round')} white={w_ref} black={b_ref} "
                    f"existing_trajectory={existing_traj[:16]}... incoming_trajectory={incoming_traj[:16]}... "
                    f"-- keeping existing, incoming NOT merged"
                )
            else:
                matched_same += 1

    existing_tree.write(str(path), xml_declaration=True, encoding="UTF-8")
    return (
        f"merged: +{added_games} games, +{added_participants} participants, "
        f"{matched_same} already present, {enriched} enriched, {divergences} divergences"
    )
