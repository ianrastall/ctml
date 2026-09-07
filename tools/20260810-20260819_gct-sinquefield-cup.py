from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import chess.pgn

from ctml_build import (child, classify_eco, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_eco, load_pgn, q, sha256)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\2026-gct-sinquefield-cup.pgn")
METADATA = Path(r"D:\elysium\sources\twic\2026-gct-sinquefield-cup.txt")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260810-20260819_gct-sinquefield-cup.ctml"
SITE_SOURCE = SOURCE_PGN.as_uri()
RESOLVER = "ctml-sinq2026-builder/1"

# Final standings (official crosstable, tiebreaks resolved). Keyed by surname
# part of the PGN name (name.split(",")[0]).
RANK = {"so": 1, "praggnanandhaa r": 2, "keymer": 3, "sindarov": 4, "aronian": 5,
        "sevian": 6, "vachier-lagrave": 7, "caruana": 8, "giri": 9, "van foreest": 10}
POINTS = {"so": 5.5, "praggnanandhaa r": 5.5, "keymer": 5.0, "sindarov": 5.0, "aronian": 4.5,
          "sevian": 4.5, "vachier-lagrave": 4.5, "caruana": 4.5, "giri": 4.0, "van foreest": 2.0}
# Head-to-head grid: CROSS[rank] = results vs columns rank 1..10 (index 0 == column rank 1; None on the diagonal).
CROSS = {
    1: [None, .5, .5, .5, .5, .5, .5, 1, .5, 1],
    2: [.5, None, 0, 1, .5, .5, 1, .5, .5, 1],
    3: [.5, 1, None, 0, 0, .5, .5, 1, 1, .5],
    4: [.5, 0, 1, None, .5, .5, .5, .5, .5, 1],
    5: [.5, .5, 1, .5, None, .5, .5, 0, .5, .5],
    6: [.5, .5, .5, .5, .5, None, 0, .5, 1, .5],
    7: [.5, 0, .5, .5, .5, 1, None, .5, .5, .5],
    8: [0, .5, 0, .5, 1, .5, .5, None, .5, 1],
    9: [.5, .5, 0, .5, .5, 0, .5, .5, None, 1],
    10: [0, 0, .5, 0, .5, .5, .5, 0, 0, None],
}
# Playoff: Wesley So beat Praggnanandhaa in the tiebreak (3 recorded rapid games drawn; So won the decider).
PLAYOFF_WINNER = "so"


def fam(name: str) -> str:
    return name.split(",")[0].strip().lower()


def points(result: str, side: str) -> float:
    if result == "1-0":
        return 1.0 if side == "w" else 0.0
    if result == "0-1":
        return 0.0 if side == "w" else 1.0
    if result == "1/2-1/2":
        return 0.5
    raise ValueError(f"Unsupported result {result!r}")


def _check_crosstable():
    for r, row in CROSS.items():
        if abs(sum(v for v in row if v is not None) - POINTS[[k for k, v in RANK.items() if v == r][0]]) > 1e-9:
            raise ValueError(f"CROSS row {r} does not sum to its points")
        for c in range(1, 11):
            if r != c and abs(row[c - 1] + CROSS[c][r - 1] - 1.0) > 1e-9:
                raise ValueError(f"CROSS not antisymmetric at {r},{c}")


def build() -> dict[str, object]:
    _check_crosstable()
    games = load_pgn(SOURCE_PGN)
    rr = [g for g in games if int(g.headers["Round"]) <= 9]
    playoff = [g for g in games if int(g.headers["Round"]) >= 10]
    if len(rr) != 45 or len(playoff) != 3:
        raise ValueError(f"Expected 45 RR + 3 playoff games, got {len(rr)} + {len(playoff)}")

    players = {}  # fam -> dict(fide, display, family, given, title, rating)
    for g in rr:
        h = g.headers
        for who in ("White", "Black"):
            name = h[who].strip()
            f = fam(name)
            if f not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                players[f] = {"fide": h[f"{who}FideId"], "display": name, "family": parts[0],
                              "given": parts[1] if len(parts) > 1 else "", "title": h.get(f"{who}Title"),
                              "rating": int(h[f"{who}Elo"]) if h.get(f"{who}Elo", "").isdigit() else None}
    if set(players) != set(RANK):
        raise ValueError(f"Player set mismatch: {set(players) ^ set(RANK)}")
    pid = {f: f"p-fide-{players[f]['fide']}" for f in players}

    # ---- audit: head-to-head + totals reproduce the official crosstable ----
    computed = defaultdict(float)
    for g in rr:
        h = g.headers
        fw, fb = fam(h["White"]), fam(h["Black"])
        wp = points(h["Result"], "w")
        rw, rb = RANK[fw], RANK[fb]
        if CROSS[rw][rb - 1] != wp:
            raise ValueError(f"Round {h['Round']} {fw} vs {fb}: result {wp} != crosstable {CROSS[rw][rb - 1]}")
        computed[fw] += wp
        computed[fb] += 1 - wp
    for f in players:
        if abs(computed[f] - POINTS[f]) > 1e-9:
            raise ValueError(f"{f}: computed {computed[f]} != crosstable {POINTS[f]}")

    eco = load_eco(ECO_TABLE)
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-sinquefield-2026"})
    header = child(root, "header")
    child(header, "name", "2026 Sinquefield Cup")
    er = child(header, "eventRef", ref="event:20260810-20260819-gct-sinquefield-cup-2026", source=SITE_SOURCE)
    child(er, "name", "2026 Sinquefield Cup")
    child(header, "eventType", "round-robin")
    child(header, "cadence", "classical")
    child(header, "federation", "USA")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=8, d=10, iso="2026-08-10")
    child(child(dates, "end"), "day", y=2026, m=8, d=19, iso="2026-08-19")
    place = child(header, "placeRef", ref="place:city:USA-saint-louis-mo", kind="city")
    child(place, "name", "Saint Louis")
    child(place, "country", "USA")
    child(place, "admin1", "Missouri")
    child(place, "city", "Saint Louis")
    child(header, "venue", "Saint Louis Chess Club")
    orgs = child(header, "organizers")
    for o in ("Saint Louis Chess Club", "Grand Chess Tour"):
        oe = child(orgs, "organizer")
        child(oe, "name", o)
        child(oe, "role", "organizer")

    # Federations are absent from both the PGN and the crosstable capture, so they
    # are resolved by FIDE id against the official FIDE standard rating list.
    feds = fide_federations({p["fide"] for p in players.values()})

    participants = child(root, "participants")
    for f in sorted(players, key=lambda f: RANK[f]):
        p = players[f]
        part = child(participants, "participant", id=pid[f])
        ref = child(part, "playerRef", ref=f"player:fide:{p['fide']}", source=SITE_SOURCE)
        name = child(ref, "name", display=p["display"])
        if "," in p["display"]:
            child(name, "family", p["family"])
            if p["given"]:
                child(name, "given", p["given"])
        else:
            child(name, "unstructured", p["display"])
        if p["fide"] in feds:
            child(ref, "federation", feds[p["fide"]])
        if p["title"]:
            child(ref, "title", p["title"])
        child(child(ref, "ids"), "fideId", p["fide"])
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        if p["rating"] is not None:
            snap = child(part, "ratingSnapshot", system="fide", scope="standard")
            child(snap, "value", p["rating"])
            child(child(snap, "asOf"), "month", y=2026, m=8, raw="event starting rating")
            child(snap, "publishedForEvent", "true")
        s = POINTS[f]
        child(part, "score", str(int(s)) if float(s).is_integer() else str(s))
        child(part, "placement", RANK[f])

    games_el = child(root, "games")
    clocked = eco_n = term_n = 0
    for g in sorted(games, key=lambda g: (int(g.headers["Round"]), int(g.headers.get("Board", "1")))):
        h = g.headers
        nodes = list(g.mainline())
        rnd = int(h["Round"])
        is_po = rnd >= 10
        gid = f"g-r{rnd:02d}" if is_po else f"g-r{rnd:02d}-b{h.get('Board', '1')}"
        ge = child(games_el, "game", id=gid, round=h["Round"], board=h.get("Board", "1"),
                   white=pid[fam(h["White"])], black=pid[fam(h["Black"])], result=h["Result"])
        code = classify_eco(tuple(n.move.uci() for n in nodes), eco)
        if code:
            child(ge, "eco", code)
            eco_n += 1
        child(ge, "start", standard="true")
        initial, inc = (int(x) for x in h["TimeControl"].split("+"))
        tc = child(ge, "timeControl", cadence="rapid" if is_po else "classical")
        child(tc, "raw", h["TimeControl"])
        child(tc, "initialSeconds", initial)
        child(tc, "incrementSeconds", inc)
        has = all(n.clock() is not None for n in nodes)
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(has).lower())
        for n in nodes:
            attrs = {"ply": n.ply(), "value": n.move.uci()}
            c = n.clock()
            if c is not None:
                attrs["clockSeconds"] = int(round(c))
                clocked += 1
            child(moves, "move", **attrs)
        term = game_termination(g)
        if term:
            child(ge, "termination", term)
            term_n += 1
        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", SITE_SOURCE)
        child(src, "note", ("Tie-break playoff game (rapid). " if is_po else "") +
              f"EndDate={h.get('EndDate', '')}; EndTime={h.get('EndTime', '')}.")

    # ---- playoff bracket (the tiebreak for 1st) ----
    loser = "praggnanandhaa r" if PLAYOFF_WINNER == "so" else "so"
    bracket = child(root, "bracket", kind="single-elimination")
    stage = child(bracket, "stage", name="tiebreak-playoff", order=1)
    tie = child(stage, "tie", winner=pid[PLAYOFF_WINNER])
    for f in (PLAYOFF_WINNER, loser):
        child(tie, "side", competitor=pid[f], score="1.5",
              outcome="win" if f == PLAYOFF_WINNER else "loss")
    for i, g in enumerate(sorted(playoff, key=lambda g: int(g.headers["Round"])), start=1):
        child(tie, "leg", number=i, firstScore="0.5", secondScore="0.5", game=f"g-r{int(g.headers['Round']):02d}")
    child(tie, "notes", "Tie-break for 1st place between So and Praggnanandhaa (both 5.5 in the round robin). The "
                        "three recorded rapid (10+5) games were all drawn (1.5 each); Wesley So won the ensuing "
                        "decider to take the title. The decider game is not in the source PGN.")

    notes = (
        "2026 Sinquefield Cup (Grand Chess Tour): a 10-player classical round robin (9 rounds, 90/40 SD 30 +30) at "
        "the Saint Louis Chess Club, 10-19 August 2026. Samuel Sevian played in place of the announced Firouzja. "
        "So and Praggnanandhaa tied for first on 5.5/9; Wesley So won the rapid tie-break playoff to defend his "
        "title (final standings 1 So, 2 Praggnanandhaa, 3 Keymer, 4 Sindarov, 5 Aronian, 6 Sevian, 7 "
        "Vachier-Lagrave, 8 Caruana, 9 Giri, 10 Van Foreest). Primary source: the chess.com broadcast PGN "
        "(per-move clocks, FIDE ids). Final standings and the head-to-head crosstable come from the chess.com "
        "event page. The builder recomputes every head-to-head result and total from the 45 round-robin games and "
        "reproduces the crosstable exactly. The three-game rapid playoff is recorded as a tie-break bracket; the "
        "final decider that settled the title is not in the source PGN. Neither the PGN nor the crosstable "
        "capture carries player federations, so each is resolved by FIDE id against the official FIDE "
        "standard rating list cited below."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note", f"48 games (45 round-robin + 3 rapid playoff); SHA-256 {sha256(SOURCE_PGN)}. Moves, clocks, "
                      "and results authoritative.")
    s2 = child(root, "source", kind="chess.com-crosstable")
    child(s2, "uri", METADATA.as_uri())
    child(s2, "note", f"Final standings crosstable + playoff result; SHA-256 {sha256(METADATA)}.")
    fide_list = fide_rating_list()
    s_fide = child(root, "source", kind="fide-rating-list")
    child(s_fide, "uri", fide_list.as_uri())
    child(s_fide, "note", f"Official FIDE standard rating list ({fide_list.stem}); resolves player "
                          f"federations by FIDE id. SHA-256 {sha256(fide_list)}.")
    s3 = child(root, "source", kind="eco-table")
    child(s3, "uri", ECO_TABLE.as_uri())
    child(s3, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(players), "rr_games": len(rr),
            "playoff_games": len(playoff), "clocked_plies": clocked, "eco_games": eco_n,
            "champion": players[PLAYOFF_WINNER]["display"], "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
