from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess.pgn

from ctml_build import (child, classify_eco, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_eco, load_pgn, q, sha256)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\international-chess-tournament-by-green-hills-resort-masters-2026-rapid.pgn")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260829-20260831_green-hills-resort-masters.ctml"

RESULTS_URL = "https://www.chess.com/events/international-chess-tournament-by-green-hills-resort-masters-2026-rapid/results"
RESOLVER = "ctml-greenhills2026-builder/1"
EVENT_REF = "event:20260829-20260831-green-hills-resort-masters"

RAPID_TC = "600+2"
BLITZ_TC = "180+2"

# Official standings transcribed from the chess.com results page (three tables).
# chess.com scores the RAPID round robin 2/1/0 (win/draw/loss) and the BLITZ
# round robin 1/1/2/0; the OVERALL column is their sum. The builder recomputes
# all three from the 56 games and refuses to emit on any mismatch.
OFFICIAL_RAPID = {  # 2/1/0 scoring
    "Yakubboev, Nodirbek": 12, "Artemiev, Vladislav": 9, "Abdusattorov, Nodirbek": 9,
    "Erdogmus, Yagiz Kaan": 8, "Suyarov, Mukhammadzokhid": 6, "Kasimdzhanov, Rustam": 5,
    "Madaminov, Mukhiddin": 4, "Mamedyarov, Shakhriyar": 3,
}
OFFICIAL_BLITZ = {  # 1/0.5/0 scoring
    "Abdusattorov, Nodirbek": 5.5, "Madaminov, Mukhiddin": 5, "Artemiev, Vladislav": 4.5,
    "Suyarov, Mukhammadzokhid": 4, "Erdogmus, Yagiz Kaan": 3.5, "Yakubboev, Nodirbek": 3,
    "Mamedyarov, Shakhriyar": 2, "Kasimdzhanov, Rustam": 0.5,
}
OFFICIAL_OVERALL = {  # rapid(2/1/0) + blitz(1/0.5/0); determines the placement
    "Yakubboev, Nodirbek": 15, "Abdusattorov, Nodirbek": 14.5, "Artemiev, Vladislav": 13.5,
    "Erdogmus, Yagiz Kaan": 11.5, "Suyarov, Mukhammadzokhid": 10, "Madaminov, Mukhiddin": 9,
    "Kasimdzhanov, Rustam": 5.5, "Mamedyarov, Shakhriyar": 5,
}
# chess.com's within-section ranks (for notes only; ties broken by chess.com).
RAPID_RANK = {"Yakubboev, Nodirbek": 1, "Artemiev, Vladislav": 2, "Abdusattorov, Nodirbek": 3,
              "Erdogmus, Yagiz Kaan": 4, "Suyarov, Mukhammadzokhid": 5, "Kasimdzhanov, Rustam": 6,
              "Madaminov, Mukhiddin": 7, "Mamedyarov, Shakhriyar": 8}
BLITZ_RANK = {"Abdusattorov, Nodirbek": 1, "Madaminov, Mukhiddin": 2, "Artemiev, Vladislav": 3,
              "Suyarov, Mukhammadzokhid": 4, "Erdogmus, Yagiz Kaan": 5, "Yakubboev, Nodirbek": 6,
              "Mamedyarov, Shakhriyar": 7, "Kasimdzhanov, Rustam": 8}


@dataclass(frozen=True)
class Player:
    name: str
    fide_id: str
    title: str
    rapid_elo: int
    blitz_elo: int

    @property
    def pid(self) -> str:
        return f"p-fide-{self.fide_id}"


def parse_hms(text: str) -> int:
    h, m, s = (int(part) for part in text.split(":"))
    return h * 3600 + m * 60 + s


def section_of(game: chess.pgn.Game) -> str:
    tc = game.headers["TimeControl"]
    if tc == RAPID_TC:
        return "rapid"
    if tc == BLITZ_TC:
        return "blitz"
    raise ValueError(f"Unexpected TimeControl {tc!r}")


def collect_roster(games: list[chess.pgn.Game]) -> dict[str, Player]:
    fide: dict[str, str] = {}
    title: dict[str, str] = {}
    elo: dict[tuple[str, str], int] = {}
    for g in games:
        tc = g.headers["TimeControl"]
        for c in ("White", "Black"):
            nm = g.headers[c]
            fide.setdefault(nm, g.headers[f"{c}FideId"])
            if fide[nm] != g.headers[f"{c}FideId"]:
                raise ValueError(f"Conflicting FIDE id for {nm}")
            title.setdefault(nm, g.headers[f"{c}Title"])
            if title[nm] != g.headers[f"{c}Title"]:
                raise ValueError(f"Conflicting title for {nm}")
            val = int(g.headers[f"{c}Elo"])
            if (nm, tc) in elo and elo[(nm, tc)] != val:
                raise ValueError(f"Conflicting Elo for {nm} at {tc}")
            elo[(nm, tc)] = val
    roster = {nm: Player(nm, fide[nm], title[nm], elo[(nm, RAPID_TC)], elo[(nm, BLITZ_TC)])
              for nm in fide}
    if len(roster) != 8:
        raise ValueError(f"Expected 8 players, got {len(roster)}")
    return roster


def audit(games: list[chess.pgn.Game], roster: dict[str, Player]) -> list[tuple]:
    names = set(roster)
    if len(games) != 56:
        raise ValueError(f"Expected 56 games, got {len(games)}")

    rapid = [g for g in games if section_of(g) == "rapid"]
    blitz = [g for g in games if section_of(g) == "blitz"]
    if len(rapid) != 28 or len(blitz) != 28:
        raise ValueError(f"Expected 28+28 games, got {len(rapid)}+{len(blitz)}")

    # Whole event is a double round robin: each pair meets exactly twice; within
    # each cadence, exactly once. Each cadence has 7 rounds of 4 games.
    if any(c != 2 for c in Counter(frozenset((g.headers["White"], g.headers["Black"])) for g in games).values()):
        raise ValueError("Not a double round robin (some pair does not meet exactly twice)")
    for label, gs in (("rapid", rapid), ("blitz", blitz)):
        pairs = Counter(frozenset((g.headers["White"], g.headers["Black"])) for g in gs)
        if len(pairs) != 28 or any(c != 1 for c in pairs.values()) or any(len(k) != 2 for k in pairs):
            raise ValueError(f"{label}: not a complete single round robin")
        rounds = Counter(int(g.headers["Round"]) for g in gs)
        if any(v != 4 for v in rounds.values()) or len(rounds) != 7:
            raise ValueError(f"{label}: expected 7 rounds of 4 games")

    # Cross-check FIDE id, title, and cadence-specific rating on every game.
    for g in games:
        sec = section_of(g)
        for c in ("White", "Black"):
            p = roster[g.headers[c]]
            if g.headers[f"{c}FideId"] != p.fide_id:
                raise ValueError(f"FIDE id mismatch for {p.name}")
            expect = p.rapid_elo if sec == "rapid" else p.blitz_elo
            if int(g.headers[f"{c}Elo"]) != expect:
                raise ValueError(f"{sec} Elo mismatch for {p.name}")

    # Reproduce all three official standings from the games.
    rapid_pts, blitz_pts = _section_scores(rapid, 2, 1), _section_scores(blitz, 1, 0.5)
    overall = {nm: rapid_pts[nm] + blitz_pts[nm] for nm in names}
    for got, official, label in ((rapid_pts, OFFICIAL_RAPID, "rapid 2/1/0"),
                                 (blitz_pts, OFFICIAL_BLITZ, "blitz 1/0.5/0"),
                                 (overall, OFFICIAL_OVERALL, "overall")):
        for nm in names:
            if got[nm] != official[nm]:
                raise ValueError(f"{label} standing for {nm}: computed {got[nm]} != official {official[nm]}")

    # Clocks: every ply integral & non-negative; collect header/last-move mismatches.
    clock_issues: list[tuple] = []
    for g in games:
        nodes = list(g.mainline())
        for node in nodes:
            clk = node.clock()
            if clk is None or not float(clk).is_integer() or clk < 0:
                raise ValueError(f"bad clock at r{g.headers['Round']} b{g.headers['Board']} ply {node.ply()}")
        for c, parity in (("White", 1), ("Black", 0)):
            side = [n for n in nodes if n.ply() % 2 == parity]
            if side and parse_hms(g.headers[f"{c}Clock"]) != int(side[-1].clock()):
                clock_issues.append((section_of(g), g.headers["Round"], g.headers["Board"], c,
                                     int(side[-1].clock()), parse_hms(g.headers[f"{c}Clock"])))
    return clock_issues


def _section_scores(games: list[chess.pgn.Game], win: float, draw: float) -> dict[str, float]:
    s: defaultdict[str, float] = defaultdict(float)
    for g in games:
        a, b, r = g.headers["White"], g.headers["Black"], g.headers["Result"]
        if r == "1-0":
            s[a] += win
        elif r == "0-1":
            s[b] += win
        elif r == "1/2-1/2":
            s[a] += draw; s[b] += draw
        else:
            raise ValueError(f"Unsupported result {r!r}")
    return dict(s)


def overall_placement() -> dict[str, int]:
    ordered = sorted(OFFICIAL_OVERALL.items(), key=lambda kv: -kv[1])
    return {nm: i for i, (nm, _) in enumerate(ordered, start=1)}


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    roster = collect_roster(games)
    clock_issues = audit(games, roster)
    eco_entries = load_eco(ECO_TABLE)
    place = overall_placement()

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-greenhills-2026"})
    header = child(root, "header")
    child(header, "name", "Green Hills Resort Masters 2026")
    er = child(header, "eventRef", ref=EVENT_REF, source=RESULTS_URL)
    child(er, "name", "Green Hills Resort Masters 2026")
    child(header, "eventType", "round-robin")
    child(header, "cadence", "mixed")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=8, d=29, iso="2026-08-29")
    child(child(dates, "end"), "day", y=2026, m=8, d=31, iso="2026-08-31")
    # chess.com's event page lists the site as "Bostonliq, Uzbekistan"; the
    # host is the Green Hills Resort in the Bo'stonliq (Bostanlyk) district
    # of Tashkent Region. Kept as chess.com spells it so the placeRef ref/name
    # round-trip against the results page verbatim.
    place_el = child(header, "placeRef", ref="place:city:UZB-bostonliq", kind="city")
    child(place_el, "name", "Bostonliq")
    child(place_el, "country", "UZB")
    child(place_el, "city", "Bostonliq")
    child(header, "venue", "Green Hills Resort")

    # Field-strength figures per cadence, rounded to nearest integer, with the
    # FIDE norm category derived from the standard 25-point band starting at
    # 2251 (category 1 = 2251-2275, category 14 = 2576-2600, and so on). Both
    # cadences are stored: this is a mixed event and each section fielded a
    # different rating pool.
    def _avg_and_cat(elos: list[int]) -> tuple[int, int]:
        avg = int(round(sum(elos) / len(elos)))
        cat = max(0, (avg - 2251) // 25 + 1) if avg >= 2251 else 0
        return avg, cat
    rapid_avg, rapid_cat = _avg_and_cat([pl.rapid_elo for pl in roster.values()])
    blitz_avg, blitz_cat = _avg_and_cat([pl.blitz_elo for pl in roster.values()])
    child(header, "averageRating", rapid_avg, system="fide", scope="rapid", category=rapid_cat)
    child(header, "averageRating", blitz_avg, system="fide", scope="blitz", category=blitz_cat)

    # The chess.com PGN carries no federation; resolve it by FIDE id.
    feds = fide_federations({p.fide_id for p in roster.values()})

    participants = child(root, "participants")
    for p in sorted(roster.values(), key=lambda x: place[x.name]):
        part = child(participants, "participant", id=p.pid)
        ref = child(part, "playerRef", ref=f"player:fide:{p.fide_id}", source=RESULTS_URL)
        family, given = (x.strip() for x in p.name.split(",", 1))
        name = child(ref, "name", display=p.name)
        child(name, "family", family)
        if given:
            child(name, "given", given)
        if p.fide_id in feds:
            child(ref, "federation", feds[p.fide_id])
        child(ref, "title", p.title)
        ids = child(ref, "ids")
        child(ids, "fideId", p.fide_id)
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        # Both cadence-specific FIDE ratings (event carries a rapid and a blitz leg).
        for scope, elo, iso in (("rapid", p.rapid_elo, "2026-08-30"),
                                ("blitz", p.blitz_elo, "2026-08-31")):
            snap = child(part, "ratingSnapshot", system="fide", scope=scope)
            child(snap, "value", elo)
            y, m, d = (int(x) for x in iso.split("-"))
            child(child(snap, "asOf"), "day", y=y, m=m, d=d, iso=iso, raw="chess.com PGN Elo")
            child(snap, "publishedForEvent", "true")
        s = OFFICIAL_OVERALL[p.name]
        child(part, "score", str(int(s)) if float(s).is_integer() else str(s))
        child(part, "placement", place[p.name])
        rp, bp = OFFICIAL_RAPID[p.name], OFFICIAL_BLITZ[p.name]
        child(part, "notes",
              f"Overall {s:g} = rapid {rp:g} (2/1/0 scoring, section rank {RAPID_RANK[p.name]}) "
              f"+ blitz {bp:g} (1/1/2/0 scoring, section rank {BLITZ_RANK[p.name]}).")

    games_el = child(root, "games")
    pgn_uri = SOURCE_PGN.as_uri()
    eco_n = term_n = clocked = 0
    for g in sorted(games, key=lambda x: (section_of(x) != "rapid", int(x.headers["Round"]), int(x.headers["Board"]))):
        h = g.headers
        sec = section_of(g)
        # The shared PGN numbers rounds continuously 1-14; blitz is the second
        # section, so map its 8-14 back to the section's own 1-7.
        rnd = int(h["Round"]) - (7 if sec == "blitz" else 0)
        cad = "rapid" if sec == "rapid" else "blitz"
        tc_raw = RAPID_TC if sec == "rapid" else BLITZ_TC
        init, incr = (600, 2) if sec == "rapid" else (180, 2)
        nodes = list(g.mainline())
        ge = child(games_el, "game",
                   id=f"g-{sec}-r{rnd:02d}-b{int(h['Board']):02d}",
                   round=f"{'R' if sec == 'rapid' else 'B'}{rnd}", board=h["Board"],
                   white=roster[h["White"]].pid, black=roster[h["Black"]].pid,
                   result=h["Result"])
        eco = classify_eco(tuple(mv.uci() for mv in g.mainline_moves()), eco_entries)
        if eco:
            child(ge, "eco", eco)
            eco_n += 1
        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence=cad)
        child(tc, "raw", tc_raw)
        child(tc, "initialSeconds", init)
        child(tc, "incrementSeconds", incr)
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo="true")
        for node in nodes:
            child(moves, "move", ply=node.ply(), value=node.move.uci(), clockSeconds=int(node.clock()))
            clocked += 1
        term = game_termination(g)
        if term:
            child(ge, "termination", term)
            term_n += 1
        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", pgn_uri)
        child(src, "note",
              f"{sec} section, {sec} round {rnd}; chess.com Site={h.get('Site', '')}; "
              f"EndDate={h.get('EndDate', '')}; EndTime={h.get('EndTime', '')}; "
              f"WhiteClock={h.get('WhiteClock', '')}; BlackClock={h.get('BlackClock', '')}. "
              "Per-move %clk values are stored as clockSeconds.")

    notes = (
        "Green Hills Resort Masters 2026 (International Chess Tournament by Green Hills Resort), 29-31 "
        "August 2026: an 8-player event run as a DOUBLE round robin across two cadences - a 7-round rapid "
        "(600+2, played 30 August) and a 7-round blitz (180+2, played 31 August), so each pair meets "
        "twice, once per cadence (56 games total, 4 boards per round). chess.com publishes three standings "
        "tables: Rapid (scored 2 for a win, 1 for a draw), Blitz (scored 1/0.5/0), and Overall = their sum. "
        "The Overall table is the event result and is stored here as participant/score and placement "
        "(1 Yakubboev 15, 2 Abdusattorov 14.5, 3 Artemiev 13.5, 4 Erdogmus 11.5, 5 Suyarov 10, "
        "6 Madaminov 9, 7 Kasimdzhanov 5.5, 8 Mamedyarov 5); each participant's per-section points and "
        "ranks are in its notes. Because the individual model carries a single score per participant, the "
        "rapid and blitz sub-standings are not separate score tables here, but every game is tagged with "
        "its cadence (game/@round R1-R7 rapid, B1-B7 blitz; game/timeControl) so all three tables "
        "recompute from the 56 games - which the builder asserts against the official figures before "
        "emitting. Each player carries both FIDE ratings used by the event (a rapid and a blitz "
        "ratingSnapshot). ECO codes are classified by longest UCI-prefix match (the PGN carried no ECO "
        "headers). Every ply has a clock value. Identity is by FIDE id. No engine evaluations were present "
        "in the source, so none are invented. Uses the CTML 2.1 model (participant/placement)."
    )
    child(root, "notes", notes)

    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", pgn_uri)
    child(s1, "note",
          f"chess.com export (56 games; filename says 'rapid' but it carries both sections, distinguished "
          f"by TimeControl {RAPID_TC}/{BLITZ_TC}). Move clocks authoritative. {len(clock_issues)} game(s) "
          f"have a PGN header final-clock differing from the last %clk: {clock_issues!r}. "
          f"SHA-256 {sha256(SOURCE_PGN)}.")
    s2 = child(root, "source", kind="chess.com-results")
    child(s2, "uri", RESULTS_URL)
    child(s2, "note", "chess.com results page (JavaScript-rendered). The Overall/Rapid/Blitz standings and "
                      "the rapid 2/1/0 vs blitz 1/0.5/0 scoring were read from that page and are reproduced "
                      "by the builder from the game results.")
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

    return {"output": str(OUTPUT), "participants": len(roster), "games": len(games),
            "eco_games": eco_n, "terminations": term_n, "clocked_plies": clocked,
            "clock_header_mismatches": len(clock_issues), "overall_winner": "Yakubboev 15",
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
