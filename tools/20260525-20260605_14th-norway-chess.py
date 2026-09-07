"""14th Norway Chess 2026 + Norway Chess Women 2026 (Oslo, 25 May - 5 June 2026).

Builds one CTML file per division from the chess.com broadcast PGN. Each division
is a 6-player DOUBLE round robin (10 rounds, 3 boards) played under the Norway
Chess scoring system: a decisive classical game is worth 3-0, and a DRAWN
classical game is followed immediately by an armageddon on the same board, worth
1.5 to its winner and 1 to its loser. Black has draw odds in the armageddon, so a
drawn armageddon board is a Black win. Both cadences therefore belong to one
event record (header cadence "mixed"), exactly as the Green Hills Resort Masters
builder stores its rapid+blitz legs: participant/score carries the official
Norway points and the classical-only crosstable score lives in participant/notes.

Splitting classical from armageddon:

  The source numbers rounds 1..20, interleaved - odd source round r is classical
  round (r+1)//2, even source round r is the armageddon following classical round
  r//2. Source rounds with no draws to settle are simply absent (open r12, women
  r20). That parity rule is cross-checked three ways before anything is emitted:

    * against TWIC's separately published split files (D:\\dev\\pgn\\norway*26.pgn),
      matched on (WhiteFideId, BlackFideId, Result) plus move-prefix - all 45/50
      games match with zero parity disagreements;
    * against the clocks, which are the physical signature of the format: every
      armageddon game opens with White near 600s and Black near 420s (10m vs 7m),
      and no classical game does;
    * against the official standings, since the 3/1.5/1/0 scoring only reproduces
      the published points if the split is right.

  The PGN's own TimeControl tag CANNOT be used for this: it is wrong on 26 of the
  35 armageddon games (11 open, 15 women), which carry the classical
  "40/7200:0+10" string. The clocks in those same games still show 600/420. Each
  armageddon game's source note records the tag actually present.

Movetext: the broadcast PGN is the fuller record. Five games run a few plies
longer here than in TWIC's website files (open 1.1 +1, 2.3 +1, 4.1 +9; women 4.1
+1, 5.1 +1); no game is shorter and none disagree on moves played.

The builder recomputes the official points table, the games-played column, and
the classical-only crosstable for both divisions and refuses to emit on any
mismatch.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import chess.pgn

from ctml_build import (child, classify_eco, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_eco, load_pgn, q, sha256)

ROOT = Path(__file__).resolve().parents[1]
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
RESOLVER = "ctml-norway2026-builder/1"
OFFICIAL_SITE = "https://norwaychess.no/"
TWIC_REPORT = "https://theweekinchess.com/chessnews/events/14th-norway-chess-2026"

# Armageddon clock signature. These are the clocks REMAINING after each side's
# first move, so they are upper-bounded by the starting time and never equal it -
# a small allowance above the nominal start absorbs any broadcast rounding. The
# two populations are an order of magnitude apart in the source (armageddon
# 393-600s, classical 7008-7200s), so the bands below cannot overlap.
ARM_WHITE_S, ARM_BLACK_S = 600, 420
ARM_CLOCK_CEILING = 700     # both clocks below this => armageddon
CLASSICAL_CLOCK_FLOOR = 6000  # both clocks above this => classical
CLASSICAL_TC = "40/7200:0+10"


@dataclass(frozen=True)
class Division:
    key: str
    name: str
    tid: str
    event_ref: str
    out: str
    pgn: Path
    twic_classical: Path
    twic_armageddon: Path
    n_classical: int
    n_armageddon: int
    # Official final standings: fide id -> (rank, Norway points, games played,
    # classical crosstable score, federation). Transcribed from the TWIC report's
    # "Leading Final Round 10 Standings" table and its conventional crosstable.
    official: dict[str, tuple[int, float, int, float, str]]
    champion: str
    summary: str


OPEN = Division(
    key="open",
    name="14th Norway Chess 2026",
    tid="tournament-norway-chess-2026",
    event_ref="event:20260525-20260605-14th-norway-chess-2026",
    out="20260525-20260605_14th-norway-chess.ctml",
    pgn=Path(r"D:\elysium\sources\twic\2026-norway-chess-open.pgn"),
    twic_classical=Path(r"D:\dev\pgn\norway26.pgn"),
    twic_armageddon=Path(r"D:\dev\pgn\norwaya26.pgn"),
    n_classical=30,
    n_armageddon=15,
    official={
        "25059530": (1, 18.0, 12, 6.0, "IND"),   # Praggnanandhaa R
        "5202213":  (2, 17.0, 18, 6.0, "USA"),   # So, Wesley
        "12573981": (3, 15.5, 15, 5.5, "FRA"),   # Firouzja, Alireza
        "1503014":  (4, 13.0, 13, 4.5, "NOR"),   # Carlsen, Magnus
        "12940690": (5, 11.0, 18, 5.0, "GER"),   # Keymer, Vincent
        "46616543": (6, 8.0, 14, 3.0, "IND"),    # Gukesh D
    },
    champion="Praggnanandhaa R",
    summary=(
        "Praggnanandhaa won on 18 points, a point clear of Wesley So, closing with four straight "
        "classical wins. Magnus Carlsen finished fourth with three wins and four losses. So and "
        "Praggnanandhaa tied the classical-only crosstable on 6/10, where So placed first on tiebreak; "
        "the armageddon points reverse that order in the official table."
    ),
)

WOMEN = Division(
    key="women",
    name="14th Norway Chess Women 2026",
    tid="tournament-norway-chess-women-2026",
    event_ref="event:20260525-20260605-14th-norway-chess-women-2026",
    out="20260525-20260605_14th-norway-chess-women.ctml",
    pgn=Path(r"D:\elysium\sources\twic\2026-norway-chess-women.pgn"),
    twic_classical=Path(r"D:\dev\pgn\norwayw26.pgn"),
    twic_armageddon=Path(r"D:\dev\pgn\norwayaw26.pgn"),
    n_classical=30,
    n_armageddon=20,
    official={
        "13708694": (1, 16.5, 16, 6.0, "KAZ"),   # Assaubayeva, Bibisara
        "8608059":  (2, 16.0, 15, 5.5, "CHN"),   # Zhu, Jiner
        "14111330": (3, 15.0, 19, 5.5, "UKR"),   # Muzychuk, Anna
        "8603006":  (4, 13.5, 17, 5.5, "CHN"),   # Ju, Wenjun
        "35006916": (5, 10.0, 15, 3.5, "IND"),   # Divya Deshmukh
        "5008123":  (6, 9.0, 18, 4.0, "IND"),    # Koneru, Humpy
    },
    champion="Assaubayeva, Bibisara",
    summary=(
        "Bibisara Assaubayeva led for most of the event and won on 16.5 points despite losing the final "
        "round to Ju Wenjun. Zhu Jiner finished half a point behind. The women's event carried the same "
        "prize fund as the open for the first time."
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
    proof of the split, independent of the (unreliable) TimeControl tag. Every game
    must land unambiguously in one band, and that band must agree with round
    parity."""
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


def check_against_twic(div: Division, games: list[chess.pgn.Game]) -> int:
    """Match every source game to TWIC's separately published split files, keyed
    on FIDE ids + result with a move-prefix comparison. Returns the number of
    games whose movetext is longer in the broadcast PGN."""
    def key(g):
        return (g.headers["WhiteFideId"], g.headers["BlackFideId"], g.headers["Result"])

    def moves(g):
        return [m.uci() for m in g.mainline_moves()]

    index = defaultdict(list)
    for g in games:
        index[key(g)].append(g)

    consumed, longer = set(), 0
    for path, expect_arm, n in ((div.twic_classical, False, div.n_classical),
                                (div.twic_armageddon, True, div.n_armageddon)):
        twic = load_pgn(path)
        if len(twic) != n:
            raise ValueError(f"{path.name}: expected {n} games, found {len(twic)}")
        for t in twic:
            tm, hit = moves(t), None
            for cand in index.get(key(t), []):
                if id(cand) in consumed:
                    continue
                cm = moves(cand)
                if cm == tm or cm[:len(tm)] == tm or tm[:len(cm)] == cm:
                    hit = (cand, len(cm) - len(tm))
                    break
            if hit is None:
                raise ValueError(f"{path.name} round {t.headers['Round']}: no match in {div.pgn.name}")
            cand, delta = hit
            consumed.add(id(cand))
            if is_armageddon(cand) != expect_arm:
                raise ValueError(
                    f"{path.name} round {t.headers['Round']}: TWIC says "
                    f"{'armageddon' if expect_arm else 'classical'}, parity says otherwise")
            if delta < 0:
                raise ValueError(f"{path.name} round {t.headers['Round']}: broadcast movetext is shorter")
            longer += delta > 0
    if len(consumed) != len(games):
        raise ValueError(f"{div.key}: {len(games) - len(consumed)} source games unmatched by TWIC")
    return longer


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
    players: dict[str, dict[str, object]] = {}
    for g in games:
        h = g.headers
        for who in ("White", "Black"):
            fid = h[f"{who}FideId"]
            name = h[who].strip()
            elo = int(h[f"{who}Elo"]) if h.get(f"{who}Elo", "").isdigit() else None
            if fid not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                players[fid] = {"display": name, "family": parts[0],
                                "given": parts[1] if len(parts) > 1 else "",
                                "comma": "," in name, "title": h.get(f"{who}Title"), "elo": elo}
            elif players[fid]["display"] != name:
                raise ValueError(f"FIDE {fid} spelled both {players[fid]['display']!r} and {name!r}")
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
    longer = check_against_twic(div, games)
    audit = audit_scores(div, games)

    players = collect_roster(games)
    pid = {fid: f"p-fide-{fid}" for fid in players}
    site_source = div.pgn.as_uri()
    mistagged = sum(1 for g in armageddon if g.headers["TimeControl"] == CLASSICAL_TC)

    # Federations: resolved by FIDE id against the official rating list, then
    # checked against the codes transcribed from the TWIC crosstable. The source
    # PGN carries no federation, so neither value is invented and the two agreeing
    # is what licenses asserting one.
    feds = fide_federations(set(players))
    for fid in players:
        expected = div.official[fid][4]
        if fid not in feds:
            raise ValueError(f"{div.key}/{fid}: no federation in {fide_rating_list().name}")
        if feds[fid] != expected:
            raise ValueError(f"{div.key}/{fid}: FIDE list says {feds[fid]}, TWIC crosstable says {expected}")

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": div.tid})
    header = child(root, "header")
    child(header, "name", div.name)
    er = child(header, "eventRef", ref=div.event_ref, source=site_source)
    child(er, "name", div.name)
    child(header, "eventType", "round-robin")
    child(header, "cadence", "mixed")
    child(header, "federation", "NOR")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=5, d=25, iso="2026-05-25")
    child(child(dates, "end"), "day", y=2026, m=6, d=5, iso="2026-06-05")
    place = child(header, "placeRef", ref="place:city:NOR-oslo", kind="city")
    child(place, "name", "Oslo")
    child(place, "country", "NOR")
    child(place, "city", "Oslo")
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
        if p["elo"] is not None:
            snap = child(part, "ratingSnapshot", system="fide", scope="standard")
            child(snap, "value", p["elo"])
            child(child(snap, "asOf"), "month", y=2026, m=5, raw="event starting rating")
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
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", site_source)
        note = f"Source round {h['Round']} board {h['Board']}. EndDate={h.get('EndDate', '')}; EndTime={h.get('EndTime', '')}."
        if arm:
            outcome = "White won" if h["Result"] == "1-0" else (
                "Black won on the board" if h["Result"] == "0-1" else "drawn on the board, so Black won on draw odds")
            note = (f"Armageddon tiebreak after the drawn classical game of round {rnd} on this board; "
                    f"{outcome}. " + note)
            if h["TimeControl"] == CLASSICAL_TC:
                note += (" Source TimeControl tag reads the classical '40/7200:0+10' and is wrong here; "
                         "the game's own clocks (600s/420s) give the armageddon control recorded above.")
        child(src, "note", note)

    out_path = ROOT / "tours" / div.out
    notes = (
        f"{div.name} (Oslo, Norway, 25 May - 5 June 2026): a 6-player DOUBLE round robin over 10 rounds, "
        f"3 boards per round, under the Norway Chess scoring system. A decisive classical game (120 min "
        f"for 40 moves, then 10 s/move) scores 3-0. A DRAWN classical game is followed immediately by an "
        f"armageddon on the same board - White 10 minutes, Black 7 minutes with draw odds - worth 1.5 to "
        f"its winner and 1 to its loser, so a drawn classical pairing yields 2.5 points between the two "
        f"players rather than 1. Both cadences are one event here (header cadence \"mixed\"): "
        f"participant/score carries the official Norway points and participant/placement the official "
        f"finishing order; each participant's classical-only crosstable score and armageddon record are "
        f"in its notes. Games are tagged by cadence - game/@round \"1\"-\"10\" classical, \"A1\"-\"A10\" "
        f"armageddon, with the armageddon carrying the same board number as the drawn classical game it "
        f"settled, plus game/timeControl - so both tables recompute from the "
        f"{div.n_classical + div.n_armageddon} games. The builder asserts the official points, the "
        f"games-played column, and the classical crosstable before emitting. {div.summary} "
        f"Final standings: " + ", ".join(
            f"{r} {players[f]['display']} {decimal(p)}"
            for f, (r, p, _, _, _) in sorted(div.official.items(), key=lambda kv: kv[1][0])) + ". "
        f"The source numbers rounds 1-{2 * 10} interleaved (odd classical, even armageddon); the split is "
        f"cross-checked against TWIC's separately published classical and armageddon files, against the "
        f"opening clocks (every armageddon game starts 600s/420s and no classical game does), and against "
        f"the official points. The source TimeControl tag is unusable for this: {mistagged} of the "
        f"{div.n_armageddon} armageddon games carry the classical tag, and those games' recorded "
        f"time control here comes from their clocks. Movetext is the broadcast record, which runs longer "
        f"than TWIC's website files in {longer} game(s) and shorter in none. ECO codes are classified by "
        f"longest UCI-prefix match (no ECO headers in the source). Every ply has a clock value. Identity "
        f"is by FIDE id. The source PGN carries no federation, so each player's is resolved by FIDE id "
        f"against the official FIDE standard rating list and cross-checked against the TWIC "
        f"crosstable's federation column; the builder refuses to emit if the two disagree. "
        f"No engine evaluations were present in the source, so none are invented. Uses the CTML 2.1 model "
        f"(participant/placement)."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", site_source)
    child(s1, "note", f"{len(games)} games ({div.n_classical} classical + {div.n_armageddon} armageddon); "
                      f"SHA-256 {sha256(div.pgn)}. Moves, clocks, FIDE ids and results authoritative.")
    for label, path in (("twic-pgn-classical", div.twic_classical), ("twic-pgn-armageddon", div.twic_armageddon)):
        s = child(root, "source", kind=label)
        child(s, "uri", path.as_uri())
        child(s, "note", f"TWIC's separately published split of this event, used to verify the "
                         f"classical/armageddon classification; SHA-256 {sha256(path)}.")
    s3 = child(root, "source", kind="twic-report")
    child(s3, "uri", TWIC_REPORT)
    child(s3, "note", "Mark Crowther, The Week in Chess: final standings, conventional crosstable, "
                      "player federations, and round-by-round results.")
    fide_list = fide_rating_list()
    s_fide = child(root, "source", kind="fide-rating-list")
    child(s_fide, "uri", fide_list.as_uri())
    child(s_fide, "note", f"Official FIDE standard rating list ({fide_list.stem}); resolves player "
                          f"federations by FIDE id. SHA-256 {sha256(fide_list)}.")
    s4 = child(root, "source", kind="official-site")
    child(s4, "uri", OFFICIAL_SITE)
    child(s4, "note", "Norway Chess official site (event regulations and scoring system).")
    s5 = child(root, "source", kind="eco-table")
    child(s5, "uri", ECO_TABLE.as_uri())
    child(s5, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with out_path.open("ab") as stream:
        stream.write(b"\n")

    return {"output": str(out_path), "participants": len(players), "classical": len(classical),
            "armageddon": len(armageddon), "clocked_plies": clocked, "eco_games": eco_n,
            "terminations": term_n, "mistagged_tc": mistagged, "longer_than_twic": longer,
            "champion": div.champion, "bytes": out_path.stat().st_size, "sha256": sha256(out_path)}


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
