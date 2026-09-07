"""Builder for the 2nd World Chess Championship Match 1889: Steinitz vs. Chigorin.

Source PGN: D:/dev/pgn/2nd-w-ch-rated.pgn  (ChessBase-derived, 17 games, Edo ratings in Elo tags)
Official record: Steinitz +10-6=1  (10.5-6.5)
Location: Club de Ajedrez de La Habana, Havana, Cuba
Dates: 1889-01-20 to 1889-02-24
Time control: 30 moves/2 h then 15 moves/1 h (noted in PGN game annotations)
Match condition: first to WIN 10 games (draws did not count toward match decision)

PDF source for the 1894 match (Lasker-Steinitz) is noted but out of scope here.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, load_pgn, q, sha256, slug

ROOT      = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\dev\pgn\2nd-w-ch-rated.pgn")
OUTPUT    = ROOT / "tours" / "18890120-18890224_world-championship-steinitz-chigorin.ctml"

RESOLVER  = "ctml-wcc1889-builder/1"
EVENT_REF = "event:18890120-18890224-world-championship-steinitz-chigorin"

CHAMPION = "steinitz"

# Steinitz's expected score (from his perspective) for each round.
# 0 = loss, 1 = win, 0.5 = draw — verified against the official record +10-6=1.
EXPECTED: dict[int, float] = {
    1:  0.0,   # Chigorin W wins (1-0)
    2:  1.0,   # Steinitz W wins (1-0)
    3:  0.0,   # Chigorin W wins (1-0)
    4:  1.0,   # Steinitz W wins (1-0)
    5:  1.0,   # Chigorin W loses (0-1)
    6:  0.0,   # Steinitz W loses (0-1)
    7:  0.0,   # Chigorin W wins (1-0)
    8:  1.0,   # Steinitz W wins (1-0)
    9:  1.0,   # Chigorin W loses (0-1)
    10: 1.0,   # Steinitz W wins (1-0)
    11: 0.0,   # Chigorin W wins (1-0)
    12: 1.0,   # Steinitz W wins (1-0)
    13: 0.0,   # Chigorin W wins (1-0)
    14: 1.0,   # Steinitz W wins (1-0)
    15: 1.0,   # Chigorin W loses (0-1)
    16: 1.0,   # Steinitz W wins (1-0)
    17: 0.5,   # Draw (1/2-1/2)
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
    if len(games) != 17:
        raise ValueError(f"Expected 17 games, got {len(games)}")

    # Collect player names and any Elo/Edo headers present.
    players: dict[str, dict] = {}
    for g in games:
        for who in ("White", "Black"):
            name = g.headers[who].strip()
            s = famslug(name)
            elo = g.headers.get(f"{who}Elo")
            if s not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                players[s] = {
                    "display": name,
                    "family":  parts[0],
                    "given":   parts[1] if len(parts) > 1 else "",
                    "edo":     int(elo) if elo and elo.isdigit() else None,
                }

    expected_names = {"steinitz", "chigorin"}
    if set(players) != expected_names:
        raise ValueError(f"Unexpected players: {sorted(players)}")

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
    if rounds_seen != set(range(1, 18)):
        raise ValueError(f"Rounds not 1-17: {sorted(rounds_seen)}")
    if abs(score["steinitz"] - 10.5) > 1e-9 or abs(score["chigorin"] - 6.5) > 1e-9:
        raise ValueError(f"Final score {score} != 10.5-6.5")
    if (wins["steinitz"], wins["chigorin"]) != (10, 6):
        raise ValueError(f"Win counts {wins} != 10-6")

    # --- Build XML ---
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-wcc-1889"})

    header = child(root, "header")
    child(header, "name", "World Chess Championship Match 1889")
    er = child(header, "eventRef", ref=EVENT_REF, source=SOURCE_PGN.as_uri())
    child(er, "name", "World Chess Championship Match 1889 (Steinitz-Chigorin)")
    child(header, "eventType", "match")
    child(header, "cadence", "classical")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1889, m=1, d=20, iso="1889-01-20")
    child(child(dates, "end"),   "day", y=1889, m=2, d=24, iso="1889-02-24")
    place = child(header, "placeRef",
                  ref="place:city:CUB-havana", kind="city",
                  source="https://www.wikidata.org/wiki/Q1563")
    child(place, "name",    "Havana")
    child(place, "country", "CUB")
    child(place, "city",    "Havana")
    child(header, "venue", "Club de Ajedrez de La Habana")

    # Field average Edo rating — mean of the two Edo values in the source PGN,
    # rounded (banker/half-up gives the same result here: (2675+2586)/2 = 2630.5).
    # @category is the 25-point FIDE band starting at 2251, used here as a
    # field-strength descriptor rather than a norm claim (FIDE norms don't
    # apply to a pre-FIDE Edo-rated event).
    edos = [p["edo"] for p in players.values() if p["edo"] is not None]
    if len(edos) == len(players):
        avg = int(round(sum(edos) / len(edos) + 1e-9))
        attrs = {"system": "edo"}
        if avg >= 2251:
            attrs["category"] = (avg - 2251) // 25 + 1
        child(header, "averageRating", avg, **attrs)

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
        child(ids, "internalId", f"wcc1889:{s}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="1889 World Championship player; identified by name (no FIDE id).")
        if p["edo"] is not None:
            snap = child(part, "ratingSnapshot", system="edo", scope="standard")
            child(snap, "value", p["edo"])
            child(child(snap, "asOf"), "year", y=1889, raw="1889 Edo rating (from source PGN)")
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
        src = child(ge, "source", kind="chessbase-pgn")
        child(src, "uri", SOURCE_PGN.as_uri())
        child(src, "note",
              f"Date={h.get('Date', '')}; Site={h.get('Site', '')}; ECO={eco!r}. "
              "ChessBase-derived (annotations stripped); no per-move clock data.")

    notes = (
        "The second World Chess Championship: Wilhelm Steinitz (defending champion, Austrian-American) "
        "vs. Mikhail Chigorin (Russian), a 17-game match at the Club de Ajedrez de La Habana, Havana, Cuba, "
        "20 January – 24 February 1889. Match condition: first to win 10 games; draws did not count toward "
        "the match decision. Steinitz won 10-6 with 1 draw (10.5-6.5 in points) to retain the championship. "
        "Chigorin played 1.e4 in his White games throughout, employing the Evans Gambit; Steinitz with White "
        "varied between 1.Nf3 and 1.Nf3 systems. Time control: 30 moves in 2 hours then 15 moves per hour "
        "(30/120'+15/60'), noted in game annotations; no per-move clock data in source. "
        "Source: ChessBase-derived PGN (17 games with ECO codes, ChessBase annotations in German/English "
        "stripped). The builder verifies every game's result against the official match record "
        "and confirms Steinitz 10.5 / 10 wins, Chigorin 6.5 / 6 wins. "
        "No FIDE ids or player titles — neither existed in 1889. Edo historical ratings from the "
        "ChessBase-derived source PGN (Steinitz 2675, Chigorin 2586; field average 2631). "
        "eventType is match; participant score is total points (1/0.5/0 per game); "
        "placement 1 Steinitz (champion), 2 Chigorin (challenger)."
    )
    child(root, "notes", notes)

    s1 = child(root, "source", kind="chessbase-pgn")
    child(s1, "uri", SOURCE_PGN.as_uri())
    child(s1, "note",
          f"ChessBase-derived PGN, 17 games (moves + ECO, no clocks, annotations stripped). "
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
        "bytes":        OUTPUT.stat().st_size,
        "sha256":       sha256(OUTPUT),
    }


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
