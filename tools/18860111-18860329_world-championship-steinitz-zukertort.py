from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, load_pgn, q, sha256, slug

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(__file__).resolve().parents[1] / "sources/curated/1st-world-championship-spelled.pgn"
METADATA = Path(r"D:\elysium\sources\twic\1886-world-championship-steinitz-zukertort.txt")
OUTPUT = ROOT / "tours" / "18860111-18860329_world-championship-steinitz-zukertort.ctml"
SITE_SOURCE = SOURCE_PGN.as_uri()
RESOLVER = "ctml-wcc1886-builder/1"

CHAMPION = "steinitz"
PLACEMENT = {"steinitz": 1, "zukertort": 2}
# Steinitz's per-game result (the match record), rounds 1-20.
EXPECTED = {1: 1, 2: 0, 3: 0, 4: 0, 5: 0, 6: 1, 7: 1, 8: 0.5, 9: 1, 10: 0.5,
            11: 1, 12: 1, 13: 0, 14: 0.5, 15: 0.5, 16: 1, 17: 0.5, 18: 1, 19: 1, 20: 1}


def famslug(name: str) -> str:
    return name.split(",")[0].strip().lower()


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
    if len(games) != 20:
        raise ValueError(f"Expected 20 games, got {len(games)}")

    players = {}
    for g in games:
        for who in ("White", "Black"):
            name = g.headers[who].strip()
            s = famslug(name)
            elo = g.headers.get(f"{who}Elo")
            if s not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                players[s] = {"display": name, "family": parts[0], "given": parts[1] if len(parts) > 1 else "",
                              "edo": int(elo) if elo and elo.isdigit() else None}
    if set(players) != {"steinitz", "zukertort"}:
        raise ValueError(f"Unexpected players: {sorted(players)}")

    # ---- audit against the match record ----
    score = {s: 0.0 for s in players}
    wins = {s: 0 for s in players}
    for g in games:
        h = g.headers
        rnd = int(h["Round"])
        w, b = famslug(h["White"]), famslug(h["Black"])
        st_side = "w" if w == CHAMPION else "b"
        st_pts = points(h["Result"], st_side)
        if abs(st_pts - EXPECTED[rnd]) > 1e-9:
            raise ValueError(f"Round {rnd}: Steinitz scored {st_pts}, record says {EXPECTED[rnd]}")
        score[w] += points(h["Result"], "w")
        score[b] += points(h["Result"], "b")
        if h["Result"] == "1-0":
            wins[w] += 1
        elif h["Result"] == "0-1":
            wins[b] += 1
    if {int(r) for r in (g.headers["Round"] for g in games)} != set(range(1, 21)):
        raise ValueError("Rounds are not 1-20")
    if abs(score["steinitz"] - 12.5) > 1e-9 or abs(score["zukertort"] - 7.5) > 1e-9:
        raise ValueError(f"Final points {score} != 12.5-7.5")
    if (wins["steinitz"], wins["zukertort"]) != (10, 5):
        raise ValueError(f"Win count {wins} != 10-5")

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-wcc-1886"})
    header = child(root, "header")
    child(header, "name", "World Chess Championship Match 1886")
    er = child(header, "eventRef", ref="event:18860111-18860329-world-championship-steinitz-zukertort", source=SITE_SOURCE)
    child(er, "name", "World Chess Championship Match 1886 (Steinitz-Zukertort)")
    child(header, "eventType", "match")
    child(header, "cadence", "classical")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1886, m=1, d=11, iso="1886-01-11")
    child(child(dates, "end"), "day", y=1886, m=3, d=29, iso="1886-03-29")
    place = child(header, "placeRef", ref="place:multi:USA-ny-stl-nola", kind="other")
    child(place, "name", "New York, St. Louis and New Orleans")
    child(place, "country", "USA")
    child(header, "venue", "New York City (games 1-5), St. Louis (6-10), New Orleans (11-20)")

    participants = child(root, "participants")
    for s in sorted(players, key=lambda s: PLACEMENT[s]):
        p = players[s]
        part = child(participants, "participant", id=f"p-{s}")
        ref = child(part, "playerRef", ref=f"player:name:{slug(p['display'])}")
        name = child(ref, "name", display=p["display"])
        child(name, "family", p["family"])
        if p["given"]:
            child(name, "given", p["given"])
        ids = child(ref, "ids")
        child(ids, "internalId", f"wcc1886:{s}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="1886 World Championship player; identified by name (no FIDE id).")
        if p["edo"] is not None:
            snap = child(part, "ratingSnapshot", system="edo", scope="standard")
            child(snap, "value", p["edo"])
            child(child(snap, "asOf"), "year", y=1886, raw="1886 Edo rating (from source PGN)")
            child(snap, "publishedForEvent", "false")
        sc = score[s]
        child(part, "score", str(int(sc)) if float(sc).is_integer() else str(sc))
        child(part, "placement", PLACEMENT[s])

    games_el = child(root, "games")
    eco_n = term_n = 0
    for g in sorted(games, key=lambda g: int(g.headers["Round"])):
        h = g.headers
        nodes = list(g.mainline())
        ge = child(games_el, "game", id=f"g-r{int(h['Round']):02d}", round=h["Round"], board=h.get("Board", "1"),
                   white=f"p-{famslug(h['White'])}", black=f"p-{famslug(h['Black'])}", result=h["Result"])
        if h.get("ECO"):
            child(ge, "eco", h["ECO"])
            eco_n += 1
        child(ge, "start", standard="true")
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo="false")
        for n in nodes:
            child(moves, "move", ply=n.ply(), value=n.move.uci())
        term = game_termination(g)
        if term:
            child(ge, "termination", term)
            term_n += 1
        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        src = child(ge, "source", kind="chessbase-pgn")
        child(src, "note", f"Date={h.get('Date', '')}; Site={h.get('Site', '')}. Untimed source (1886 time control "
                           "was 30 moves/2h then 15 moves/h); no clock data.")

    notes = (
        "The first official World Chess Championship: Wilhelm Steinitz (Austrian) vs Johannes Zukertort (Polish), "
        "a 20-game match across New York City (games 1-5), St. Louis (6-10) and New Orleans (11-20), 11 January - "
        "29 March 1886. The match was decided by WINS (first to 10; draws did not count): Steinitz won 10-5 "
        "(12.5-7.5 with draws) to become the first official world champion. Primary source: a ChessBase-derived "
        "PGN (20 games, moves only -- no clocks). The match record (per-game results, venues, dates) is "
        "transcribed from the chessgames.com collection. The builder recomputes each player's per-game result, "
        "total points, and win count from the games and reproduces the record exactly (Steinitz 12.5 / 10 wins, "
        "Zukertort 7.5 / 5 wins). Ratings are Edo historical ratings (event-time 1886) from the source: Steinitz "
        "2673, Zukertort 2542. No player titles or FIDE federations are asserted (neither existed in 1886). "
        "eventType is match; participant score is total points, with placement 1 Steinitz, 2 Zukertort."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chessbase-pgn")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note", f"20 games (moves + Edo Elo, no clocks); SHA-256 {sha256(SOURCE_PGN)}.")
    s2 = child(root, "source", kind="metadata")
    child(s2, "uri", METADATA.as_uri())
    child(s2, "note", f"Transcribed match record, venues, dates; SHA-256 {sha256(METADATA)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(players), "games": len(games),
            "eco_games": eco_n, "terminations": term_n, "champion": players[CHAMPION]["display"],
            "score": f"{score['steinitz']}-{score['zukertort']}", "wins": f"{wins['steinitz']}-{wins['zukertort']}",
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
