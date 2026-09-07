"""13th Norway Chess 2025 + 2nd Norway Chess Women 2025 (Stavanger, 26 May - 6 June 2025).

Builds one CTML file per division from the TWIC broadcast PGN. Each division is
a 6-player DOUBLE round robin (10 rounds, 3 boards) played under the Norway
Chess scoring system: a decisive classical game is worth 3-0, and a DRAWN
classical game is followed immediately by an armageddon on the same board, worth
1.5 to its winner and 1 to its loser. Black has draw odds in the armageddon, so a
drawn armageddon board is a Black win. Both cadences therefore belong to one
event record (header cadence "mixed"), exactly as the 2026 edition's builder
stores its classical+armageddon legs: participant/score carries the official
Norway points and the classical-only crosstable score lives in participant/notes.

Splitting classical from armageddon:

  The source numbers rounds 1..20, interleaved - odd source round r is classical
  round (r+1)//2, even source round r is the armageddon following classical round
  r//2. Source rounds with no draws to settle are simply absent (women r18).
  Parity is cross-checked two ways before anything is emitted:

    * against the source TimeControl tag, which is correct in this year's file:
      every armageddon game carries "600+0" and every classical game carries
      "40/7200:0+10" (unlike the 2026 broadcast PGN, where 26 armageddons were
      mistagged);
    * against the clocks, which are the physical signature of the format: every
      armageddon game opens with White near 600s and Black near 420s (10m vs 7m),
      and no classical game does.

  Both agree with round parity for all 93 games (main 45, women 48). The 3/1.5/1/0
  scoring also reproduces the published Wikipedia final points for both divisions.

The builder recomputes the official points table, the games-played column, and
the classical-only crosstable for both divisions and refuses to emit on any
mismatch. Unlike the 2026 edition, TWIC did not publish separately split
classical/armageddon files for 2025, so that cross-check is not available here;
the TimeControl tag makes it unnecessary.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess.pgn

from ctml_build import (child, classify_eco, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_eco, load_pgn, q, sha256)

ROOT = Path(__file__).resolve().parents[1]
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
RESOLVER = "ctml-norway2025-builder/1"
OFFICIAL_SITE = "https://norwaychess.no/"
OFFICIAL_STATS = "https://stats.norwaychess.no/2025/games"
WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/Norway_Chess_2025"

# Armageddon clock signature. These are the clocks REMAINING after each side's
# first move, so they are upper-bounded by the starting time and never equal it -
# a small allowance above the nominal start absorbs any broadcast rounding. The
# two populations are an order of magnitude apart in the source (armageddon
# well under 605s, classical above 7000s), so the bands below cannot overlap.
ARM_WHITE_S, ARM_BLACK_S = 600, 420
ARM_CLOCK_CEILING = 700     # both clocks below this => armageddon
CLASSICAL_CLOCK_FLOOR = 6000  # both clocks above this => classical
ARMAGEDDON_TC = "600+0"
CLASSICAL_TC = "40/7200:0+10"


@dataclass(frozen=True)
class Division:
    key: str
    name: str
    tid: str
    event_ref: str
    out: str
    pgn: Path
    n_classical: int
    n_armageddon: int
    # Official final standings from Wikipedia's crosstable:
    #   fide id -> (rank, Norway points, games played, classical crosstable score, federation).
    official: dict[str, tuple[int, float, int, float, str]]
    champion: str
    summary: str


OPEN = Division(
    key="open",
    name="13th Norway Chess 2025",
    tid="tournament-norway-chess-2025",
    event_ref="event:20250526-20250606-13th-norway-chess-2025",
    out="20250526-20250606_13th-norway-chess.ctml",
    pgn=Path(r"D:\elysium\sources\twic\2025-norway-chess-main.pgn"),
    n_classical=30,
    n_armageddon=15,
    official={
        "1503014":  (1, 16.0, 16, 6.0, "NOR"),   # Carlsen, Magnus
        "2020009":  (2, 15.5, 13, 5.5, "USA"),   # Caruana, Fabiano
        "46616543": (3, 14.5, 12, 5.0, "IND"),   # Gukesh D
        "2016192":  (4, 14.0, 17, 5.5, "USA"),   # Nakamura, Hikaru
        "35009192": (5, 13.0, 15, 4.5, "IND"),   # Erigaisi Arjun
        "8603405":  (6,  9.5, 17, 3.5, "CHN"),   # Wei, Yi
    },
    champion="Carlsen, Magnus",
    summary=(
        "Magnus Carlsen won his seventh Norway Chess title with 16 points, half a point clear of "
        "Fabiano Caruana. Gukesh Dommaraju finished third after handing Carlsen his first classical "
        "loss to a reigning world champion in round 6 (Carlsen slammed the table in frustration on "
        "resigning). Caruana beat Gukesh in the final round to secure second and hand Carlsen the "
        "title after Carlsen drew a lost position with Black against Arjun."
    ),
)

WOMEN = Division(
    key="women",
    name="2nd Norway Chess Women 2025",
    tid="tournament-norway-chess-women-2025",
    event_ref="event:20250526-20250606-2nd-norway-chess-women-2025",
    out="20250526-20250606_2nd-norway-chess-women.ctml",
    pgn=Path(r"D:\elysium\sources\twic\2025-norway-chess-women.pgn"),
    n_classical=30,
    n_armageddon=18,
    official={
        "14111330": (1, 16.5, 18, 6.0, "UKR"),   # Muzychuk, Anna
        "8605114":  (2, 16.0, 16, 6.0, "CHN"),   # Lei, Tingjie
        "5008123":  (3, 15.0, 15, 5.5, "IND"),   # Koneru, Humpy
        "8603006":  (4, 13.5, 18, 5.0, "CHN"),   # Ju, Wenjun
        "5091756":  (5, 11.0, 16, 4.0, "IND"),   # Vaishali, Rameshbabu
        "12512214": (6,  9.0, 13, 3.5, "ESP"),   # Khademalsharieh, Sarasadat
    },
    champion="Muzychuk, Anna",
    summary=(
        "Anna Muzychuk won the second edition of Norway Chess Women on 16.5 points, half a point "
        "clear of Lei Tingjie after drawing her final classical game with Vaishali. Defending "
        "champion Ju Wenjun finished fourth. Muzychuk and Lei tied the classical-only crosstable "
        "on 6/10; the armageddon points separated them in the official table."
    ),
)

DIVISIONS = (OPEN, WOMEN)


# --------------------------------------------------------------------------- #
# classification + audit
# --------------------------------------------------------------------------- #

def is_armageddon(game: chess.pgn.Game) -> bool:
    """Source round parity: even source rounds are armageddon tiebreaks."""
    return int(game.headers["Round"]) % 2 == 0


def classical_round(game: chess.pgn.Game) -> int:
    r = int(game.headers["Round"])
    return (r + 1) // 2 if r % 2 else r // 2


def opening_clocks(game: chess.pgn.Game) -> tuple[float | None, float | None]:
    nodes = list(game.mainline())
    w = nodes[0].clock() if len(nodes) > 0 else None
    b = nodes[1].clock() if len(nodes) > 1 else None
    return w, b


def check_clock_signature(div: Division, games: list[chess.pgn.Game]) -> None:
    """Armageddon games start 10m vs 7m; classical games start 120m each. Physical
    proof of the split, independent of any tag. Every game must land unambiguously
    in one band, and that band must agree with round parity."""
    for g in games:
        where = f"{div.key} source round {g.headers['Round']} board {g.headers['Board']}"
        w, b = opening_clocks(g)
        if w is None or b is None:
            raise ValueError(f"{where}: missing opening clocks")
        looks_arm = w <= ARM_CLOCK_CEILING and b <= ARM_CLOCK_CEILING
        looks_classical = w >= CLASSICAL_CLOCK_FLOOR and b >= CLASSICAL_CLOCK_FLOOR
        if looks_arm == looks_classical:
            raise ValueError(f"{where}: clocks ({w}s/{b}s) fit neither cadence band cleanly")
        if looks_arm != is_armageddon(g):
            raise ValueError(f"{where}: clock signature ({w}s/{b}s) contradicts round parity")
        if looks_arm and not (w <= ARM_WHITE_S + 5 and b <= ARM_BLACK_S + 5 and b < w):
            raise ValueError(f"{where}: clocks ({w}s/{b}s) are not the asymmetric 10m/7m armageddon control")


def check_tc_tag(div: Division, games: list[chess.pgn.Game]) -> None:
    """Every armageddon game must carry TC "600+0", every classical "40/7200:0+10".
    Unlike the 2026 broadcast source, the 2025 TWIC file gets this right for every
    game; if that ever changes we want to know at build time."""
    for g in games:
        h = g.headers
        want = ARMAGEDDON_TC if is_armageddon(g) else CLASSICAL_TC
        if h["TimeControl"] != want:
            raise ValueError(f"{div.key} source round {h['Round']} board {h['Board']}: "
                             f"TimeControl tag {h['TimeControl']!r} != expected {want!r}")


def audit_scores(div: Division, games: list[chess.pgn.Game]) -> dict[str, dict[str, float]]:
    """Recompute the official points table, games played, and the classical-only
    crosstable. Raises unless all three reproduce the published figures."""
    pts: defaultdict[str, float] = defaultdict(float)
    gms: defaultdict[str, int] = defaultdict(int)
    cls: defaultdict[str, float] = defaultdict(float)
    arm_w: defaultdict[str, int] = defaultdict(int)
    arm_l: defaultdict[str, int] = defaultdict(int)

    for g in games:
        h = g.headers
        w, b, res = h["WhiteFideId"], h["BlackFideId"], h["Result"]
        gms[w] += 1
        gms[b] += 1
        if is_armageddon(g):
            # Black holds draw odds: only an outright 1-0 is a White win.
            white_won = res == "1-0"
            pts[w] += 1.5 if white_won else 1.0
            pts[b] += 1.0 if white_won else 1.5
            arm_w[w if white_won else b] += 1
            arm_l[b if white_won else w] += 1
        else:
            if res == "1-0":
                pts[w] += 3.0
                cls[w] += 1.0
            elif res == "0-1":
                pts[b] += 3.0
                cls[b] += 1.0
            elif res == "1/2-1/2":
                cls[w] += 0.5
                cls[b] += 0.5
            else:
                raise ValueError(f"{div.key}: unsupported classical result {res!r}")

    if set(pts) != set(div.official):
        raise ValueError(f"{div.key}: roster mismatch {set(pts) ^ set(div.official)}")
    for fid, (_, points, played, classical, _) in div.official.items():
        if abs(pts[fid] - points) != 0:
            raise ValueError(f"{div.key}/{fid}: points {pts[fid]} != official {points}")
        if gms[fid] != played:
            raise ValueError(f"{div.key}/{fid}: games {gms[fid]} != official {played}")
        if abs(cls[fid] - classical) != 0:
            raise ValueError(f"{div.key}/{fid}: classical {cls[fid]} != official {classical}")
    return {"points": dict(pts), "games": dict(gms), "classical": dict(cls),
            "armWins": dict(arm_w), "armLosses": dict(arm_l)}


# --------------------------------------------------------------------------- #
# emit
# --------------------------------------------------------------------------- #

def decimal(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def collect_roster(games: list[chess.pgn.Game]) -> dict[str, dict[str, object]]:
    """One entry per FIDE id. Prefers the classical rating (source rounds are
    odd for classical), because the armageddon games carry the blitz rating and
    should not overwrite the event's classical starting Elo."""
    players: dict[str, dict[str, object]] = {}
    for g in sorted(games, key=lambda g: int(g.headers["Round"])):
        h = g.headers
        arm = is_armageddon(g)
        for who in ("White", "Black"):
            fid = h[f"{who}FideId"]
            name = h[who].strip()
            elo = int(h[f"{who}Elo"]) if h.get(f"{who}Elo", "").isdigit() else None
            if fid not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                # Erigaisi Arjun is stored without a comma in the source; keep as-is.
                players[fid] = {"display": name, "family": parts[0],
                                "given": parts[1] if len(parts) > 1 else "",
                                "comma": "," in name, "title": h.get(f"{who}Title"),
                                "classical_elo": elo if not arm else None,
                                "blitz_elo": elo if arm else None}
            else:
                if players[fid]["display"] != name:
                    raise ValueError(f"FIDE {fid} spelled both {players[fid]['display']!r} and {name!r}")
                if not arm and players[fid]["classical_elo"] is None:
                    players[fid]["classical_elo"] = elo
                if arm and players[fid]["blitz_elo"] is None:
                    players[fid]["blitz_elo"] = elo
    return players


def build_division(div: Division, eco_entries) -> dict[str, object]:
    games = load_pgn(div.pgn)
    if len(games) != div.n_classical + div.n_armageddon:
        raise ValueError(f"{div.pgn.name}: expected {div.n_classical + div.n_armageddon}, got {len(games)}")

    classical = [g for g in games if not is_armageddon(g)]
    armageddon = [g for g in games if is_armageddon(g)]
    if (len(classical), len(armageddon)) != (div.n_classical, div.n_armageddon):
        raise ValueError(f"{div.key}: parity split {len(classical)}/{len(armageddon)} "
                         f"!= {div.n_classical}/{div.n_armageddon}")
    # Every drawn classical game must have produced exactly one armageddon.
    drawn = sum(1 for g in classical if g.headers["Result"] == "1/2-1/2")
    if drawn != len(armageddon):
        raise ValueError(f"{div.key}: {drawn} classical draws but {len(armageddon)} armageddon games")
    for g in armageddon:
        rnd, board = classical_round(g), g.headers["Board"]
        parent = [c for c in classical if classical_round(c) == rnd and c.headers["Board"] == board]
        if len(parent) != 1 or parent[0].headers["Result"] != "1/2-1/2":
            raise ValueError(f"{div.key}: armageddon A{rnd} board {board} has no drawn classical parent")

    check_clock_signature(div, games)
    check_tc_tag(div, games)
    audit = audit_scores(div, games)

    players = collect_roster(games)
    pid = {fid: f"p-fide-{fid}" for fid in players}
    site_source = div.pgn.as_uri()

    # Federations: resolved by FIDE id against the official rating list, then
    # checked against the country codes transcribed from the Wikipedia final
    # standings table. The source PGN carries no federation, so neither value is
    # invented and the two agreeing is what licenses asserting one.
    feds = fide_federations(set(players))
    for fid in players:
        expected = div.official[fid][4]
        if fid not in feds:
            raise ValueError(f"{div.key}/{fid}: no federation in {fide_rating_list().name}")
        if feds[fid] != expected:
            raise ValueError(f"{div.key}/{fid}: FIDE list says {feds[fid]}, Wikipedia says {expected}")

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": div.tid})
    header = child(root, "header")
    child(header, "name", div.name)
    er = child(header, "eventRef", ref=div.event_ref, source=site_source)
    child(er, "name", div.name)
    child(header, "eventType", "round-robin")
    child(header, "cadence", "mixed")
    child(header, "federation", "NOR")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2025, m=5, d=26, iso="2025-05-26")
    child(child(dates, "end"), "day", y=2025, m=6, d=6, iso="2025-06-06")
    place = child(header, "placeRef", ref="place:city:NOR-stavanger", kind="city")
    child(place, "name", "Stavanger")
    child(place, "country", "NOR")
    child(place, "city", "Stavanger")
    orgs = child(header, "organizers")
    org = child(orgs, "organizer")
    child(org, "name", "Norway Chess")
    child(org, "federation", "NOR")
    child(org, "role", "organizer")

    participants = child(root, "participants")
    for fid in sorted(players, key=lambda f: div.official[f][0]):
        p = players[fid]
        rank, points, played, classical_score, fed = div.official[fid]
        part = child(participants, "participant", id=pid[fid])
        ref = child(part, "playerRef", ref=f"player:fide:{fid}", source=site_source)
        name = child(ref, "name", display=p["display"])
        if p["comma"]:
            child(name, "family", p["family"])
            if p["given"]:
                child(name, "given", p["given"])
        else:
            child(name, "unstructured", p["display"])
        child(ref, "federation", feds[fid])
        if p["title"]:
            child(ref, "title", p["title"])
        child(child(ref, "ids"), "fideId", fid)
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        if p["classical_elo"] is not None:
            snap = child(part, "ratingSnapshot", system="fide", scope="standard")
            child(snap, "value", p["classical_elo"])
            child(child(snap, "asOf"), "month", y=2025, m=5, raw="event starting rating")
            child(snap, "publishedForEvent", "true")
        child(part, "score", decimal(points))
        child(part, "placement", rank)
        aw, al = audit["armWins"].get(fid, 0), audit["armLosses"].get(fid, 0)
        child(part, "notes",
              f"Official Norway points {decimal(points)} from {played} games "
              f"(10 classical + {aw + al} armageddon). Classical-only crosstable score "
              f"{decimal(classical_score)}/10. Armageddon record {aw}-{al}.")

    games_el = child(root, "games")
    clocked = eco_n = term_n = 0
    ordered = sorted(games, key=lambda g: (classical_round(g), is_armageddon(g), int(g.headers["Board"])))
    for g in ordered:
        h = g.headers
        arm = is_armageddon(g)
        rnd = classical_round(g)
        label = f"A{rnd}" if arm else str(rnd)
        gid = f"g-{'a' if arm else 'r'}{rnd:02d}-b{h['Board']}"
        ge = child(games_el, "game", id=gid, round=label, board=h["Board"],
                   white=pid[h["WhiteFideId"]], black=pid[h["BlackFideId"]], result=h["Result"])
        nodes = list(g.mainline())
        code = classify_eco(tuple(n.move.uci() for n in nodes), eco_entries)
        if code:
            child(ge, "eco", code)
            eco_n += 1
        child(ge, "start", standard="true")
        if arm:
            tc = child(ge, "timeControl", cadence="blitz")
            child(tc, "raw", "600+0 (White) / 420+0 (Black), draw odds to Black")
            child(tc, "initialSeconds", ARM_WHITE_S)
            child(tc, "incrementSeconds", 0)
        else:
            tc = child(ge, "timeControl", cadence="classical")
            child(tc, "raw", h["TimeControl"])
            child(tc, "initialSeconds", 7200)
            child(tc, "incrementSeconds", 10)
        has_clocks = all(n.clock() is not None for n in nodes)
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(has_clocks).lower())
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
        src = child(ge, "source", kind="twic-pgn")
        child(src, "uri", site_source)
        note = f"Source round {h['Round']} board {h['Board']}. EndDate={h.get('EndDate', '')}; EndTime={h.get('EndTime', '')}."
        if arm:
            outcome = "White won" if h["Result"] == "1-0" else (
                "Black won on the board" if h["Result"] == "0-1" else "drawn on the board, so Black won on draw odds")
            note = (f"Armageddon tiebreak after the drawn classical game of round {rnd} on this board; "
                    f"{outcome}. " + note)
        child(src, "note", note)

    out_path = ROOT / "tours" / div.out
    notes = (
        f"{div.name} (Stavanger, Norway, 26 May - 6 June 2025): a 6-player DOUBLE round robin over 10 "
        f"rounds, 3 boards per round, under the Norway Chess scoring system. A decisive classical game "
        f"(120 min for the whole game with a 10 s/move increment from move 41) scores 3-0. A DRAWN "
        f"classical game is followed immediately by an armageddon on the same board - White 10 minutes, "
        f"Black 7 minutes with draw odds - worth 1.5 to its winner and 1 to its loser, so a drawn "
        f"classical pairing yields 2.5 points between the two players rather than 1. Both cadences are "
        f"one event here (header cadence \"mixed\"): participant/score carries the official Norway "
        f"points and participant/placement the official finishing order; each participant's classical-"
        f"only crosstable score and armageddon record are in its notes. Games are tagged by cadence - "
        f"game/@round \"1\"-\"10\" classical, \"A1\"-\"A10\" armageddon, with the armageddon carrying "
        f"the same board number as the drawn classical game it settled, plus game/timeControl - so both "
        f"tables recompute from the {div.n_classical + div.n_armageddon} games. The builder asserts the "
        f"official points, the games-played column, and the classical crosstable before emitting. "
        f"{div.summary} Final standings: " + ", ".join(
            f"{r} {players[f]['display']} {decimal(p)}"
            for f, (r, p, _, _, _) in sorted(div.official.items(), key=lambda kv: kv[1][0])) + ". "
        f"The source numbers rounds 1-20 interleaved (odd classical, even armageddon); the split is "
        f"cross-checked against the source TimeControl tag (every armageddon carries \"600+0\", every "
        f"classical \"40/7200:0+10\") and against the opening clocks (every armageddon game starts "
        f"600s/420s and no classical game does), and the 3/1.5/1/0 scoring reproduces the published "
        f"points. Unlike the 2026 edition, TWIC did not publish separately split classical/armageddon "
        f"files for 2025, so that further cross-check is not available here. ECO codes are classified "
        f"by longest UCI-prefix match (no ECO headers in the source). Every ply has a clock value. "
        f"Identity is by FIDE id. The source PGN carries no federation, so each player's is resolved "
        f"by FIDE id against the official FIDE standard rating list and cross-checked against the "
        f"Wikipedia final standings table; the builder refuses to emit if the two disagree. Ratings "
        f"in participant/ratingSnapshot are the classical Elo carried on the classical games (the "
        f"armageddon games carry a separate blitz rating and are ignored for the event starting Elo). "
        f"No engine evaluations were present in the source, so none are invented. Uses the CTML 2.1 "
        f"model (participant/placement)."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="twic-pgn")
    child(s1, "uri", site_source)
    child(s1, "note", f"{len(games)} games ({div.n_classical} classical + {div.n_armageddon} armageddon); "
                      f"SHA-256 {sha256(div.pgn)}. Moves, clocks, FIDE ids, TimeControl tags and results "
                      f"authoritative.")
    fide_list = fide_rating_list()
    s_fide = child(root, "source", kind="fide-rating-list")
    child(s_fide, "uri", fide_list.as_uri())
    child(s_fide, "note", f"Official FIDE standard rating list ({fide_list.stem}); resolves player "
                          f"federations by FIDE id. SHA-256 {sha256(fide_list)}.")
    s2 = child(root, "source", kind="official-site")
    child(s2, "uri", OFFICIAL_SITE)
    child(s2, "note", "Norway Chess official site (event regulations and scoring system).")
    s3 = child(root, "source", kind="official-stats")
    child(s3, "uri", OFFICIAL_STATS)
    child(s3, "note", "Official 2025 games archive at stats.norwaychess.no (round-by-round results).")
    s4 = child(root, "source", kind="wikipedia")
    child(s4, "uri", WIKIPEDIA_URL)
    child(s4, "note", "Wikipedia \"Norway Chess 2025\": final standings, crosstable, player "
                      "federations, and round-by-round narrative used to cross-check the audit.")
    s5 = child(root, "source", kind="eco-table")
    child(s5, "uri", ECO_TABLE.as_uri())
    child(s5, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with out_path.open("ab") as stream:
        stream.write(b"\n")

    return {"output": str(out_path), "participants": len(players), "classical": len(classical),
            "armageddon": len(armageddon), "clocked_plies": clocked, "eco_games": eco_n,
            "terminations": term_n, "champion": div.champion,
            "bytes": out_path.stat().st_size, "sha256": sha256(out_path)}


def build() -> dict[str, object]:
    eco_entries = load_eco(ECO_TABLE)
    out: dict[str, object] = {}
    for div in DIVISIONS:
        for k, v in build_division(div, eco_entries).items():
            out[f"{div.key}.{k}"] = v
    return out


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
