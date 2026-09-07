from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import chess.pgn

from ctml_build import child, classify_eco, fingerprints, game_termination, load_eco, load_pgn, q, sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\2025-womens-world-chess-championship.pgn")
CHESS_RESULTS_PGN = Path(r"D:\elysium\sources\twic\1149784.pgn")
METADATA = Path(r"D:\elysium\sources\twic\2025-womens-world-chess-championship.txt")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20250403-20250420_fide-womens-world-championship-match.ctml"
SITE_SOURCE = SOURCE_PGN.as_uri()
RESOLVER = "ctml-wwcm-builder/1"

# Players (federation + starting rating from the ChessResults starting rank; the
# chess.com PGN carries FIDE ids/titles/live Elo but no federation).
PLAYERS = {
    "8603006": {"display": "Ju, Wenjun", "family": "Ju", "given": "Wenjun", "fed": "CHN", "title": "GM", "rating": 2561},
    "8603642": {"display": "Tan, Zhongyi", "family": "Tan", "given": "Zhongyi", "fed": "CHN", "title": "GM", "rating": 2555},
}
CHAMPION = "8603006"  # Ju Wenjun, 6.5-2.5
PLACEMENT = {"8603006": 1, "8603642": 2}
# Ju Wenjun's per-game result (the match crosstable), games 1-9.
EXPECTED_JU = {1: 0.5, 2: 0.0, 3: 1.0, 4: 0.5, 5: 1.0, 6: 1.0, 7: 1.0, 8: 1.0, 9: 0.5}

ARBITERS = [
    ("de San Vicente, Sabrina", "chief arbiter", "3003973"),
    ("Zhu, Jiaqi", "deputy chief arbiter", "8600929"),
    ("Wang, Junnan", "deputy chief arbiter", "8600910"),
    ("Bauyrzhan, Kaussar", "fair play officer", "13707019"),
]


def points(result: str, side: str) -> float:
    if result == "1-0":
        return 1.0 if side == "w" else 0.0
    if result == "0-1":
        return 0.0 if side == "w" else 1.0
    if result == "1/2-1/2":
        return 0.5
    raise ValueError(f"Unsupported result {result!r}")


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    if len(games) != 9:
        raise ValueError(f"Expected 9 games, got {len(games)}")

    # ---- audit against the ChessResults match crosstable ----
    rounds_seen = set()
    score = {fid: 0.0 for fid in PLAYERS}
    for game in games:
        h = game.headers
        rnd = int(h["Round"])
        rounds_seen.add(rnd)
        wf, bf = h["WhiteFideId"], h["BlackFideId"]
        if {wf, bf} != set(PLAYERS):
            raise ValueError(f"Unexpected players in round {rnd}: {wf}, {bf}")
        for fid in PLAYERS:
            if h[f"{'White' if fid == wf else 'Black'}Title"] != PLAYERS[fid]["title"]:
                raise ValueError(f"Title disagreement for {fid}")
        ju_side = "w" if wf == CHAMPION else "b"
        ju_pts = points(h["Result"], ju_side)
        if abs(ju_pts - EXPECTED_JU[rnd]) > 1e-9:
            raise ValueError(f"Round {rnd}: Ju scored {ju_pts}, crosstable says {EXPECTED_JU[rnd]}")
        score[wf] += points(h["Result"], "w")
        score[bf] += points(h["Result"], "b")
    if rounds_seen != set(range(1, 10)):
        raise ValueError(f"Rounds not 1-9: {sorted(rounds_seen)}")
    if abs(score[CHAMPION] - 6.5) > 1e-9 or abs(score["8603642"] - 2.5) > 1e-9:
        raise ValueError(f"Final score {score} != 6.5-2.5")

    # ---- corroborate against the independent ChessResults PGN (tnr 1149784) ----
    cr_games = load_pgn(CHESS_RESULTS_PGN)
    if len(cr_games) != 9:
        raise ValueError(f"Expected 9 ChessResults games, got {len(cr_games)}")
    by_round = {int(g.headers["Round"]): g for g in games}
    for cg in cr_games:
        rnd = int(cg.headers["Round"])
        pg = by_round[rnd]
        if (cg.headers["White"], cg.headers["Black"], cg.headers["Result"]) != \
           (pg.headers["White"], pg.headers["Black"], pg.headers["Result"]):
            raise ValueError(f"Round {rnd}: ChessResults headers disagree with chess.com")
        if [m.uci() for m in cg.mainline_moves()] != [m.uci() for m in pg.mainline_moves()]:
            raise ValueError(f"Round {rnd}: ChessResults movetext disagrees with chess.com")

    eco = load_eco(ECO_TABLE)
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-cr-418246"})
    header = child(root, "header")
    child(header, "name", "FIDE Women's World Championship Match 2025")
    er = child(header, "eventRef",
               ref="event:20250403-20250420-fide-womens-world-championship-match-2025", source=SITE_SOURCE)
    child(er, "name", "FIDE Women's World Championship Match 2025")
    child(header, "eventType", "match")
    child(header, "cadence", "classical")
    child(header, "federation", "FID")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2025, m=4, d=3, iso="2025-04-03")
    child(child(dates, "end"), "day", y=2025, m=4, d=20, iso="2025-04-20")
    place = child(header, "placeRef", ref="place:multi:CHN-shanghai-chongqing", kind="other")
    child(place, "name", "Shanghai / Chongqing")
    child(place, "country", "CHN")
    child(header, "venue", "Shanghai (games 1-6) and Chongqing (games 7-9)")
    orgs = child(header, "organizers")
    for o in ("FIDE", "Chinese Chess Association"):
        oe = child(orgs, "organizer")
        child(oe, "name", o)
        child(oe, "role", "organizer")
    arb = child(header, "arbiters")
    for name, role, fid in ARBITERS:
        a = child(arb, "arbiter")
        child(a, "name", name)
        child(a, "role", role)
        child(child(a, "ids"), "fideId", fid)

    participants = child(root, "participants")
    for fid in sorted(PLAYERS, key=lambda f: PLACEMENT[f]):
        p = PLAYERS[fid]
        part = child(participants, "participant", id=f"p-fide-{fid}")
        ref = child(part, "playerRef", ref=f"player:fide:{fid}", source=SITE_SOURCE)
        name = child(ref, "name", display=p["display"])
        child(name, "family", p["family"])
        child(name, "given", p["given"])
        child(ref, "federation", p["fed"])
        child(ref, "title", p["title"])
        child(child(ref, "ids"), "fideId", fid)
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        snap = child(part, "ratingSnapshot", system="fide", scope="standard")
        child(snap, "value", p["rating"])
        as_of = child(snap, "asOf")
        child(as_of, "month", y=2025, m=4, raw="event starting rating (ChessResults)")
        child(snap, "publishedForEvent", "true")
        s = score[fid]
        child(part, "score", str(int(s)) if float(s).is_integer() else str(s))
        child(part, "placement", PLACEMENT[fid])

    games_el = child(root, "games")
    clocked = eco_n = term_n = 0
    for game in sorted(games, key=lambda g: int(g.headers["Round"])):
        h = game.headers
        nodes = list(game.mainline())
        ge = child(games_el, "game", id=f"g-r{int(h['Round']):02d}", round=h["Round"], board=h["Board"],
                   white=f"p-fide-{h['WhiteFideId']}", black=f"p-fide-{h['BlackFideId']}", result=h["Result"])
        uci = tuple(n.move.uci() for n in nodes)
        code = classify_eco(uci, eco)
        if code:
            child(ge, "eco", code)
            eco_n += 1
        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence="classical")
        child(tc, "raw", h["TimeControl"])
        child(tc, "initialSeconds", 5400)
        child(tc, "incrementSeconds", 30)
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(bool(nodes)).lower())
        for n in nodes:
            attrs = {"ply": n.ply(), "value": n.move.uci()}
            c = n.clock()
            if c is not None:
                attrs["clockSeconds"] = int(round(c))
                clocked += 1
            child(moves, "move", **attrs)
        term = game_termination(game)
        if term:
            child(ge, "termination", term)
            term_n += 1
        traj, final = fingerprints(game)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", SITE_SOURCE)
        child(src, "note", f"Site={h.get('Site', '')}; EndDate={h.get('EndDate', '')}; EndTime={h.get('EndTime', '')}. "
                           f"Second time control 30 min after move 40 (raw {h['TimeControl']}).")

    notes = (
        "FIDE Women's World Championship Match 2025: a head-to-head classical match (up to 12 games, first to 6.5) "
        "between defending champion Ju Wenjun and challenger Tan Zhongyi, both of China, across their home cities "
        "of Shanghai (games 1-6) and Chongqing (games 7-9), 3-20 April 2025. Ju Wenjun won 6.5-2.5 after game 9 "
        "(16 April), retaining the title; games 10-12 and all tiebreaks (rapid 15+10, then 10+5, then 3+2) were "
        "unneeded. Primary source: the chess.com broadcast PGN (per-move clocks, FIDE ids, live Elo); the match "
        "crosstable, officials, and player federations/start ratings come from ChessResults (event 418246). The "
        "builder recomputes each player's per-game and total score from the games and reproduces the crosstable "
        "exactly (Ju 6.5, Tan 2.5). Two independent sources -- the chess.com broadcast and the ChessResults PGN "
        "(tnr 1149784) -- agree on all nine games move for move. Time control 40 moves/90 min + 30 min, 30 sec "
        "increment from move 1."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note", f"9 games; SHA-256 {sha256(SOURCE_PGN)}. Moves, clocks, and results authoritative.")
    scr = child(root, "source", kind="chess-results-pgn")
    child(scr, "uri", CHESS_RESULTS_PGN.as_uri())
    child(scr, "note", f"9 games (tnr 1149784); SHA-256 {sha256(CHESS_RESULTS_PGN)}. Independent movetext/result "
                       "corroboration: agrees with the chess.com PGN on all nine games, move for move.")
    s2 = child(root, "source", kind="chess-results-metadata")
    child(s2, "uri", METADATA.as_uri())
    child(s2, "note", f"Match crosstable, officials, schedule, start ratings; SHA-256 {sha256(METADATA)}.")
    s3 = child(root, "source", kind="eco-table")
    child(s3, "uri", ECO_TABLE.as_uri())
    child(s3, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(PLAYERS), "games": len(games),
            "clocked_plies": clocked, "eco_games": eco_n, "terminations": term_n,
            "champion": PLAYERS[CHAMPION]["display"], "score": f"{score[CHAMPION]}-{score['8603642']}",
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
