"""Builder for the 3rd World Chess Championship Match 1890-91: Steinitz vs. Gunsberg.

Source PGN: D:/dev/pgn/3rd-world-championship-rated.pgn  (ChessBase-derived, 19 games)
Official record: Steinitz +6-4=9  (10.5-8.5)
Location: Manhattan Chess Club, New York City, USA
Dates: 1890-12-09 to 1891-01-22
Time control: 30 moves/2 h then 15 moves/1 h (same conditions as 1889 match)
Match condition: first to win 8 games (draws did not count toward match decision).
  — Neither player reached 8 wins; the match ended at 19 games, Steinitz
    declared winner by margin of wins (6-4).

Edo ratings in the source PGN: Steinitz 2658 (games 1-9, Edo 1890) then 2642
(games 10-19, Edo 1891 — a rating update effective 1 Jan 1891); Gunsberg 2522
throughout. The CTML ratingSnapshot uses the 1890 event-starting value for Steinitz;
the mid-match change is recorded in the source note.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, load_pgn, q, sha256, slug

ROOT       = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\dev\pgn\3rd-world-championship-rated.pgn")
OUTPUT     = ROOT / "tours" / "18901209-18910122_world-championship-steinitz-gunsberg.ctml"

RESOLVER  = "ctml-wcc1890-builder/1"
EVENT_REF = "event:18901209-18910122-world-championship-steinitz-gunsberg"

CHAMPION = "steinitz"

# Steinitz's expected score for each round (0/0.5/1).
# Verified against the official record ½1½0011½½1½01½½0½1½  (+6-4=9)
EXPECTED: dict[int, float] = {
    1:  0.5,   # Steinitz W, 1/2-1/2
    2:  1.0,   # Gunsberg W, 0-1  (Steinitz Black wins)
    3:  0.5,   # Steinitz W, 1/2-1/2
    4:  0.0,   # Gunsberg W, 1-0  (Steinitz Black loses)
    5:  0.0,   # Steinitz W, 0-1  (Steinitz White loses)
    6:  1.0,   # Gunsberg W, 0-1  (Steinitz Black wins)
    7:  1.0,   # Steinitz W, 1-0
    8:  0.5,   # Gunsberg W, 1/2-1/2
    9:  0.5,   # Steinitz W, 1/2-1/2
    10: 1.0,   # Gunsberg W, 0-1  (Steinitz Black wins)
    11: 0.5,   # Steinitz W, 1/2-1/2
    12: 0.0,   # Gunsberg W, 1-0  (Steinitz Black loses)
    13: 1.0,   # Steinitz W, 1-0
    14: 0.5,   # Gunsberg W, 1/2-1/2
    15: 0.5,   # Steinitz W, 1/2-1/2
    16: 0.0,   # Gunsberg W, 1-0  (Steinitz Black loses)
    17: 0.5,   # Steinitz W, 1/2-1/2
    18: 1.0,   # Gunsberg W, 0-1  (Steinitz Black wins)
    19: 0.5,   # Steinitz W, 1/2-1/2
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
    if len(games) != 19:
        raise ValueError(f"Expected 19 games, got {len(games)}")

    # Collect players. Track all Elo values seen per player.
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

    expected_names = {"steinitz", "gunsberg"}
    if set(players) != expected_names:
        raise ValueError(f"Unexpected players: {sorted(players)}")

    # Use the highest Edo value as the event-starting rating (Steinitz 2658 = 1890 rating).
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
    if rounds_seen != set(range(1, 20)):
        raise ValueError(f"Rounds not 1-19: {sorted(rounds_seen)}")
    if abs(score["steinitz"] - 10.5) > 1e-9 or abs(score["gunsberg"] - 8.5) > 1e-9:
        raise ValueError(f"Final score {score} != 10.5-8.5")
    if (wins["steinitz"], wins["gunsberg"]) != (6, 4):
        raise ValueError(f"Win counts {wins} != 6-4")

    # --- Build XML ---
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-wcc-1890"})

    header = child(root, "header")
    child(header, "name", "World Chess Championship Match 1890-91")
    er = child(header, "eventRef", ref=EVENT_REF, source=SOURCE_PGN.as_uri())
    child(er, "name", "World Chess Championship Match 1890-91 (Steinitz-Gunsberg)")
    child(header, "eventType", "match")
    child(header, "cadence", "classical")
    child(header, "federation", "USA")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1890, m=12, d=9,  iso="1890-12-09")
    child(child(dates, "end"),   "day", y=1891, m=1,  d=22, iso="1891-01-22")
    place = child(header, "placeRef",
                  ref="place:city:USA-new-york", kind="city",
                  source="https://www.wikidata.org/wiki/Q60")
    child(place, "name",    "New York City")
    child(place, "country", "USA")
    child(place, "city",    "New York City")
    child(header, "venue", "Manhattan Chess Club, New York City")

    # Participants — winner first.
    placement = {"steinitz": 1, "gunsberg": 2}
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
        child(ids, "internalId", f"wcc1890:{s}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="1890-91 World Championship player; identified by name (no FIDE id).")
        if p["edo_start"] is not None:
            snap = child(part, "ratingSnapshot", system="edo", scope="standard")
            child(snap, "value", p["edo_start"])
            child(child(snap, "asOf"), "year",
                  y=1890, raw="Edo rating 1890 from source PGN (event-starting value)")
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
        # Note the per-game Elo values from the source PGN.
        w_elo = h.get("WhiteElo", "")
        b_elo = h.get("BlackElo", "")
        src = child(ge, "source", kind="chessbase-pgn")
        child(src, "uri", SOURCE_PGN.as_uri())
        child(src, "note",
              f"Date={h.get('Date', '')}; Site={h.get('Site', '')}; ECO={eco!r}. "
              f"Source PGN Elo: White {w_elo}, Black {b_elo}. "
              "ChessBase-derived (annotations stripped); no per-move clock data.")

    notes = (
        "The third World Chess Championship: Wilhelm Steinitz (defending champion) vs. Isidor Gunsberg "
        "(Hungarian-British), a 19-game match at the Manhattan Chess Club, New York City, "
        "9 December 1890 – 22 January 1891. Match condition: first to win 8 games; draws did not count "
        "toward the match decision. Neither player reached 8 wins: Steinitz won 6-4 with 9 draws "
        "(10.5-8.5 in points) and was declared the winner by margin of wins, retaining the championship. "
        "Source: ChessBase-derived PGN (19 games with ECO codes, engine eval annotations and ChessBase "
        "annotations stripped). The builder verifies every result against the official match record "
        "and confirms Steinitz 10.5 / 6 wins, Gunsberg 8.5 / 4 wins. "
        "Edo historical ratings in the source PGN: Steinitz 2658 (games 1-9, reflecting the 1890 annual "
        "rating) then 2642 (games 10-19, reflecting a 1 January 1891 rating update); Gunsberg 2522 "
        "throughout. The ratingSnapshot for each participant uses the 1890 (event-starting) value; "
        "the mid-match change is recorded in each game's source note. "
        "No FIDE ids; no player titles (neither existed in 1890). "
        "eventType is match; participant score is total points (1/0.5/0 per game); "
        "placement 1 Steinitz (champion), 2 Gunsberg (challenger). "
        "Note on Steinitz identity: this match's source PGN uses 'Steinitz, Wilhelm' (as in 1886), "
        "while the 1889 match PGN used 'Steinitz, William'; both refer to the same person."
    )
    child(root, "notes", notes)

    s1 = child(root, "source", kind="chessbase-pgn")
    child(s1, "uri", SOURCE_PGN.as_uri())
    child(s1, "note",
          f"ChessBase-derived PGN (3rd-world-championship-rated.pgn), 19 games with ECO codes "
          f"and Edo Elo ratings; engine evals and ChessBase annotations stripped. "
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
        "score":        f"{score['steinitz']}-{score['gunsberg']}",
        "wins":         f"{wins['steinitz']}-{wins['gunsberg']}",
        "steinitz_edo": players["steinitz"]["edo_start"],
        "gunsberg_edo": players["gunsberg"]["edo_start"],
        "bytes":        OUTPUT.stat().st_size,
        "sha256":       sha256(OUTPUT),
    }


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
