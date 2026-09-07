"""Builder for the 4th World Chess Championship Match 1892: Steinitz vs. Chigorin.

Source PGN: D:/dev/pgn/4th-world-championship.pgn  (ChessBase-derived, 23 games)
Official record: Steinitz +10-8=5  (12.5-10.5)
Location: Club de Ajedrez de La Habana, Havana, Cuba
Dates: 1892-01-01 to 1892-02-28
Time control: 30 moves/2 h then 15 moves/1 h (same conditions as 1889/1890 matches)
Match condition: first to win 10 games (draws did not count toward match decision).
  — Steinitz reached his 10th win in the final game (round 23), ending the match.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, load_pgn, q, sha256, slug

ROOT       = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\dev\pgn\4th-world-championship.pgn")
OUTPUT     = ROOT / "tours" / "18920101-18920228_world-championship-steinitz-chigorin.ctml"

RESOLVER  = "ctml-wcc1892-builder/1"
EVENT_REF = "event:18920101-18920228-world-championship-steinitz-chigorin"

CHAMPION = "steinitz"

# Steinitz's expected score for each round (0/0.5/1).
# Verified against the official record +10-8=5  (12.5-10.5)
# Steinitz result sequence: 0½½1½100½01011010101½11
EXPECTED: dict[int, float] = {
    1:  0.0,   # Chigorin W, 1-0     (Steinitz Black loses)
    2:  0.5,   # Steinitz W, 1/2-1/2
    3:  0.5,   # Chigorin W, 1/2-1/2 (Steinitz Black draws)
    4:  1.0,   # Steinitz W, 1-0
    5:  0.5,   # Chigorin W, 1/2-1/2 (Steinitz Black draws)
    6:  1.0,   # Steinitz W, 1-0
    7:  0.0,   # Chigorin W, 1-0     (Steinitz Black loses)
    8:  0.0,   # Steinitz W, 0-1     (Steinitz White loses)
    9:  0.5,   # Chigorin W, 1/2-1/2 (Steinitz Black draws)
    10: 0.0,   # Steinitz W, 0-1     (Steinitz White loses)
    11: 1.0,   # Chigorin W, 0-1     (Steinitz Black wins)
    12: 0.0,   # Steinitz W, 0-1     (Steinitz White loses)
    13: 1.0,   # Chigorin W, 0-1     (Steinitz Black wins)
    14: 1.0,   # Steinitz W, 1-0
    15: 0.0,   # Chigorin W, 1-0     (Steinitz Black loses)
    16: 1.0,   # Steinitz W, 1-0
    17: 0.0,   # Chigorin W, 1-0     (Steinitz Black loses)
    18: 1.0,   # Steinitz W, 1-0
    19: 0.0,   # Chigorin W, 1-0     (Steinitz Black loses)
    20: 1.0,   # Steinitz W, 1-0
    21: 0.5,   # Chigorin W, 1/2-1/2 (Steinitz Black draws)
    22: 1.0,   # Steinitz W, 1-0
    23: 1.0,   # Chigorin W, 0-1     (Steinitz Black wins, clinches match)
}


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
    if len(games) != 23:
        raise ValueError(f"Expected 23 games, got {len(games)}")

    # Collect players and Elo values seen per player.
    players: dict[str, dict] = {}
    elo_seen: dict[str, set] = {}
    for g in games:
        for who in ("White", "Black"):
            name = g.headers[who].strip()
            s    = famslug(name)
            elo  = g.headers.get(f"{who}Elo")
            if s not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                players[s]  = {"display": name, "family": parts[0],
                                "given": parts[1] if len(parts) > 1 else "",
                                "edo_start": None}
                elo_seen[s] = set()
            if elo and elo.isdigit():
                elo_seen[s].add(int(elo))

    expected_names = {"steinitz", "chigorin"}
    if set(players) != expected_names:
        raise ValueError(f"Unexpected players: {sorted(players)}")

    # Use the highest Elo value as the event-starting rating.
    for s in players:
        if elo_seen[s]:
            players[s]["edo_start"] = max(elo_seen[s])

    # Audit per-game results against the official record.
    score: dict[str, float] = {s: 0.0 for s in players}
    wins:  dict[str, int]   = {s: 0   for s in players}
    for g in games:
        h   = g.headers
        rnd = int(h["Round"])
        w, b = famslug(h["White"]), famslug(h["Black"])
        st_side = "w" if w == CHAMPION else "b"
        st_pts  = points(h["Result"], st_side)
        if abs(st_pts - EXPECTED[rnd]) > 1e-9:
            raise ValueError(f"Round {rnd}: Steinitz scored {st_pts}, record says {EXPECTED[rnd]}")
        score[w] += points(h["Result"], "w")
        score[b] += points(h["Result"], "b")
        if h["Result"] == "1-0":
            wins[w] += 1
        elif h["Result"] == "0-1":
            wins[b] += 1

    rounds_seen = {int(g.headers["Round"]) for g in games}
    if rounds_seen != set(range(1, 24)):
        raise ValueError(f"Rounds not 1-23: {sorted(rounds_seen)}")
    if abs(score["steinitz"] - 12.5) > 1e-9 or abs(score["chigorin"] - 10.5) > 1e-9:
        raise ValueError(f"Final score {score} != 12.5-10.5")
    if (wins["steinitz"], wins["chigorin"]) != (10, 8):
        raise ValueError(f"Win counts {wins} != 10-8")

    # --- Build XML ---
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-wcc-1892"})

    header = child(root, "header")
    child(header, "name", "World Chess Championship Match 1892")
    er = child(header, "eventRef", ref=EVENT_REF, source=SOURCE_PGN.as_uri())
    child(er, "name", "World Chess Championship Match 1892 (Steinitz-Chigorin)")
    child(header, "eventType", "match")
    child(header, "cadence", "classical")
    child(header, "federation", "CUB")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1892, m=1,  d=1,  iso="1892-01-01")
    child(child(dates, "end"),   "day", y=1892, m=2,  d=28, iso="1892-02-28")
    place = child(header, "placeRef",
                  ref="place:city:CUB-havana", kind="city",
                  source="https://www.wikidata.org/wiki/Q1563")
    child(place, "name",    "Havana")
    child(place, "country", "CUB")
    child(place, "city",    "Havana")
    child(header, "venue", "Club de Ajedrez de La Habana, Havana, Cuba")

    # Participants — winner first.
    placement = {"steinitz": 1, "chigorin": 2}
    participants = child(root, "participants")
    for s in sorted(players, key=lambda s: placement[s]):
        p    = players[s]
        part = child(participants, "participant", id=f"p-{s}")
        ref  = child(part, "playerRef", ref=f"player:name:{slug(p['display'])}")
        name_el = child(ref, "name", display=p["display"])
        child(name_el, "family", p["family"])
        if p["given"]:
            child(name_el, "given", p["given"])
        ids = child(ref, "ids")
        child(ids, "internalId", f"wcc1892:{s}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="1892 World Championship player; identified by name (no FIDE id).")
        if p["edo_start"] is not None:
            snap = child(part, "ratingSnapshot", system="edo", scope="standard")
            child(snap, "value", p["edo_start"])
            child(child(snap, "asOf"), "year",
                  y=1892, raw="Estimated historical Elo from source PGN (event-starting value)")
            child(snap, "publishedForEvent", "false")
        sc = score[s]
        child(part, "score", str(int(sc)) if float(sc).is_integer() else str(sc))
        child(part, "placement", placement[s])

    # Games
    games_el = child(root, "games")
    eco_n = term_n = 0
    for g in sorted(games, key=lambda g: int(g.headers["Round"])):
        h     = g.headers
        rnd   = int(h["Round"])
        nodes = list(g.mainline())
        ge = child(games_el, "game",
                   id=f"g-r{rnd:02d}",
                   round=str(rnd),
                   board="1",
                   white=f"p-{famslug(h['White'])}",
                   black=f"p-{famslug(h['Black'])}",
                   result=h["Result"])
        eco = h.get("ECO", "")
        if eco:
            child(ge, "eco", eco)
            eco_n += 1
        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence="classical")
        child(tc, "raw", "30/120'+15/60'")
        child(tc, "note", "30 moves in 2 hours, then 15 moves per hour; no per-move clock data in source.")
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo="false")
        for n in nodes:
            child(moves, "move", ply=n.ply(), value=n.move.uci())
        term = game_termination(g)
        if term:
            child(ge, "termination", term)
            term_n += 1
        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory",    value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        w_elo = h.get("WhiteElo", "")
        b_elo = h.get("BlackElo", "")
        src = child(ge, "source", kind="chessbase-pgn")
        child(src, "uri", SOURCE_PGN.as_uri())
        child(src, "note",
              f"Date={h.get('Date', '')}; Site={h.get('Site', '')}; ECO={eco!r}. "
              f"Source PGN Elo: White {w_elo}, Black {b_elo}. "
              "ChessBase-derived (annotations stripped); no per-move clock data.")

    notes = (
        "The fourth World Chess Championship: Wilhelm Steinitz (defending champion) vs. Mikhail Chigorin "
        "(Russian), a 23-game match at the Club de Ajedrez de La Habana, Havana, Cuba, "
        "1 January – 28 February 1892. Match condition: first to win 10 games; draws did not count "
        "toward the match decision. Steinitz reached 10 wins in the final game (round 23), retaining "
        "the championship with a record of +10-8=5 (12.5-10.5 in points). "
        "This was a rematch of the 2nd World Championship (1889), in which Chigorin also faced Steinitz. "
        "Source: ChessBase-derived PGN (23 games with ECO codes, engine eval annotations and ChessBase "
        "annotations stripped). The builder verifies every result against the official match record "
        "and confirms Steinitz 12.5 / 10 wins, Chigorin 10.5 / 8 wins. "
        "Estimated historical Elo values in the source PGN: Steinitz 2637, Chigorin 2600. "
        "No FIDE ids; no player titles (neither existed in 1892). "
        "eventType is match; participant score is total points (1/0.5/0 per game); "
        "placement 1 Steinitz (champion), 2 Chigorin (challenger). "
        "Cuba in 1892 was a Spanish colony; EventCountry 'CUB' follows the source PGN."
    )
    child(root, "notes", notes)

    s1 = child(root, "source", kind="chessbase-pgn")
    child(s1, "uri", SOURCE_PGN.as_uri())
    child(s1, "note",
          f"ChessBase-derived PGN (4th-world-championship.pgn), 23 games with ECO codes "
          f"and estimated historical Elo ratings; engine evals and ChessBase annotations stripped. "
          f"SHA-256 {sha256(SOURCE_PGN)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True,
                               short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {
        "output":       str(OUTPUT),
        "participants": len(players),
        "games":        len(games),
        "eco_games":    eco_n,
        "terminations": term_n,
        "champion":     players[CHAMPION]["display"],
        "score":        f"{score['steinitz']}-{score['chigorin']}",
        "wins":         f"{wins['steinitz']}-{wins['chigorin']}",
        "steinitz_edo": players["steinitz"]["edo_start"],
        "chigorin_edo": players["chigorin"]["edo_start"],
        "bytes":        OUTPUT.stat().st_size,
        "sha256":       sha256(OUTPUT),
    }


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
