from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, load_pgn, q, sha256, slug

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\dev\pgn\1st-chess-congress-spelled.pgn")
METADATA = Path(r"D:\elysium\sources\twic\1st-american-chess-congress-1857.txt")
FISKE_BOOK = Path(r"C:\Users\Ian\Downloads\Telegram Desktop\Fiske_The_Book_of_the_First_American_Chess_Congress_1859_NY.pdf")
OUTPUT = ROOT / "tours" / "18571006-18571110_1st-american-chess-congress.ctml"
SITE_SOURCE = SOURCE_PGN.as_uri()
RESOLVER = "ctml-acc1857-builder/1"


def fam(name: str) -> str:
    return name.split(",")[0].strip().lower()


def fs(*names):
    return frozenset(names)


# Authoritative match record (Fiske). Keyed by frozenset of surname slugs.
TIE_META = {
    fs("morphy", "thompson"): dict(winner="morphy", target=3),
    fs("meek", "fuller"): dict(winner="meek", target=3, missing=[(2, "meek")]),
    fs("lichtenhein", "stanley"): dict(winner="lichtenhein", target=3),
    fs("perrin", "knott"): dict(winner="perrin", target=3, missing=[(5, "draw")]),
    fs("paulsen", "calthrop"): dict(winner="paulsen", target=3),
    fs("montgomery", "allison"): dict(winner="montgomery", target=3),
    fs("raphael", "kennicott"): dict(winner="raphael", target=3),
    fs("marache", "fiske"): dict(winner="marache", target=3),
    fs("morphy", "meek"): dict(winner="morphy", target=3),
    fs("lichtenhein", "perrin"): dict(winner="lichtenhein", target=3),
    fs("raphael", "marache"): dict(winner="raphael", target=3),
    fs("paulsen", "montgomery"): dict(winner="paulsen", target=3, resign="montgomery"),
    fs("morphy", "lichtenhein"): dict(winner="morphy", target=3),
    fs("paulsen", "raphael"): dict(winner="paulsen", target=3, resign="raphael"),
    fs("morphy", "paulsen"): dict(winner="morphy", target=5, stage="final"),
    fs("lichtenhein", "raphael"): dict(winner="lichtenhein", target=3, stage="third-place"),
}
STAGE_BY_MAJOR = {1: "round-of-16", 2: "quarterfinal", 3: "semifinal"}
STAGE_ORDER = {"round-of-16": 1, "quarterfinal": 2, "semifinal": 3, "third-place": 4, "final": 5}
PLACEMENT = {"morphy": 1, "paulsen": 2, "lichtenhein": 3, "raphael": 4}


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
    if len(games) != 68:
        raise ValueError(f"Expected 68 games, got {len(games)}")

    players = {}  # slug -> dict(display, family, given, edo)
    ties = defaultdict(list)  # (major, frozenset(slugs)) -> [game]
    for g in games:
        h = g.headers
        for who in ("White", "Black"):
            name = h[who].strip()
            s = fam(name)
            elo = h.get(f"{who}Elo")
            if s not in players:
                parts = [p.strip() for p in name.split(",", 1)]
                players[s] = {"display": name, "family": parts[0],
                              "given": parts[1] if len(parts) > 1 else "",
                              "edo": int(elo) if elo and elo.lstrip("-").isdigit() and int(elo) > 0 else None}
            elif players[s]["edo"] is None and elo and elo.lstrip("-").isdigit() and int(elo) > 0:
                players[s]["edo"] = int(elo)
        major = int(h["Round"].split(".")[0])
        ties[(major, fs(fam(h["White"]), fam(h["Black"])))].append(g)
    if len(players) != 16:
        raise ValueError(f"Expected 16 players, got {len(players)}: {sorted(players)}")

    # game ids
    gid = {}
    for (major, pair), gl in ties.items():
        ps = "-".join(sorted(pair))
        for g in gl:
            gid[id(g)] = f"g-r{major}-g{g.headers['Round'].split('.')[1]}-{ps}"

    # ---- assemble ties, supplement missing games, audit winners ----
    tie_records = []
    overall = defaultdict(lambda: [0, 0, 0])  # slug -> [wins, draws, losses]
    for (major, pair), gl in ties.items():
        meta = TIE_META[pair]
        winner, loser = meta["winner"], next(s for s in pair if s != meta["winner"])
        legs = []  # (slot, first_pts, second_pts, game_or_None)  first=winner side
        wins = {winner: 0, loser: 0}
        for g in gl:
            h = g.headers
            slot = int(h["Round"].split(".")[1])
            wf = fam(h["White"])
            wpts_white = points(h["Result"], "w")
            first_pts = wpts_white if wf == winner else 1.0 - wpts_white if h["Result"] != "1/2-1/2" else 0.5
            legs.append((slot, first_pts, 1.0 - first_pts, g))
            # overall W/D/L tally
            if h["Result"] == "1/2-1/2":
                overall[fam(h["White"])][1] += 1
                overall[fam(h["Black"])][1] += 1
            else:
                w = fam(h["White"]) if h["Result"] == "1-0" else fam(h["Black"])
                l = fam(h["Black"]) if h["Result"] == "1-0" else fam(h["White"])
                overall[w][0] += 1
                overall[l][2] += 1
        for slot, who in meta.get("missing", []):
            fp = 0.5 if who == "draw" else (1.0 if who == winner else 0.0)
            legs.append((slot, fp, 1.0 - fp, None))
            if who == "draw":
                overall[winner][1] += 1
                overall[loser][1] += 1
            else:
                overall[who][0] += 1
                overall[winner if who == loser else loser][2] += 1
        legs.sort()
        for _, fp, sp, _ in legs:
            wins[winner] += 1 if fp == 1.0 else 0
            wins[loser] += 1 if sp == 1.0 else 0
        if wins[winner] <= wins[loser]:
            raise ValueError(f"Tie {sorted(pair)}: winner {winner} does not lead ({wins})")
        if "resign" not in meta and wins[winner] != meta["target"]:
            raise ValueError(f"Tie {sorted(pair)}: {winner} has {wins[winner]} wins, target {meta['target']}")
        stage = meta["stage"] if "stage" in meta else STAGE_BY_MAJOR[major]
        tie_records.append(dict(major=major, stage=stage, winner=winner, loser=loser,
                                wins=wins, legs=legs, meta=meta))

    if tuple(overall["morphy"]) != (14, 3, 1):
        raise ValueError(f"Morphy overall {tuple(overall['morphy'])} != 14-3-1 (wins-draws-losses)")

    # ================= EMIT =================
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-cr-acc-1857"})
    header = child(root, "header")
    child(header, "name", "1st American Chess Congress")
    er = child(header, "eventRef", ref="event:18571006-18571110-1st-american-chess-congress", source=SITE_SOURCE)
    child(er, "name", "1st American Chess Congress")
    child(header, "eventType", "knockout")
    child(header, "cadence", "classical")
    child(header, "federation", "USA")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1857, m=10, d=6, iso="1857-10-06")
    child(child(dates, "end"), "day", y=1857, m=11, d=10, iso="1857-11-10")
    place = child(header, "placeRef", ref="place:city:USA-new-york", kind="city")
    child(place, "name", "New York")
    child(place, "country", "USA")
    child(place, "city", "New York")
    orgs = child(header, "organizers")
    for o in ("Fiske, Daniel Willard", "Frere, Thomas"):
        oe = child(orgs, "organizer")
        child(oe, "name", o)
        child(oe, "role", "organizer")

    participants = child(root, "participants")
    for s in sorted(players, key=lambda s: (-(players[s]["edo"] or 0), players[s]["family"])):
        p = players[s]
        part = child(participants, "participant", id=f"p-{s}")
        ref = child(part, "playerRef", ref=f"player:name:{slug(p['display'])}")
        name = child(ref, "name", display=p["display"])
        child(name, "family", p["family"])
        if p["given"]:
            child(name, "given", p["given"])
        ids = child(ref, "ids")
        child(ids, "internalId", f"acc1857:{s}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="1857 American Chess Congress participant; identified by name (no FIDE id).")
        if p["edo"] is not None:
            snap = child(part, "ratingSnapshot", system="edo", scope="standard")
            child(snap, "value", p["edo"])
            child(child(snap, "asOf"), "year", y=1857, raw="1857 Edo rating (from source PGN)")
            child(snap, "publishedForEvent", "false")
        if s in PLACEMENT:
            child(part, "placement", PLACEMENT[s])

    games_el = child(root, "games")
    term_n = eco_n = 0
    for g in sorted(games, key=lambda g: (int(g.headers["Round"].split(".")[0]),
                                          int(g.headers["Round"].split(".")[1]),
                                          "-".join(sorted((fam(g.headers["White"]), fam(g.headers["Black"])))))):
        h = g.headers
        nodes = list(g.mainline())
        ge = child(games_el, "game", id=gid[id(g)], round=h["Round"], board=h.get("Board", "1"),
                   white=f"p-{fam(h['White'])}", black=f"p-{fam(h['Black'])}", result=h["Result"])
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
        src = child(ge, "source", kind="chessgames-pgn")
        child(src, "note", f"Date={h.get('Date', '')}; Site={h.get('Site', '')}. Untimed (1857); no clock data.")

    # ---- bracket ----
    bracket = child(root, "bracket", kind="single-elimination")
    for tr in sorted(tie_records, key=lambda t: (STAGE_ORDER[t["stage"]],
                                                 -PLACEMENT.get(t["winner"], 99))):
        stage = child(bracket, "stage", name=tr["stage"], order=STAGE_ORDER[tr["stage"]])
        tie = child(stage, "tie", winner=f"p-{tr['winner']}")
        for side in (tr["winner"], tr["loser"]):
            w = tr["wins"][side]
            child(tie, "side", competitor=f"p-{side}",
                  score=int(w) if float(w).is_integer() else w,
                  outcome="win" if side == tr["winner"] else "loss")
        for slot, fp, sp, g in tr["legs"]:
            attrs = {"number": slot,
                     "firstScore": int(fp) if float(fp).is_integer() else fp,
                     "secondScore": int(sp) if float(sp).is_integer() else sp}
            if g is not None:
                attrs["game"] = gid[id(g)]
            child(tie, "leg", **attrs)
        note_bits = [f"First to {tr['meta']['target']} wins; draws replayed (do not count)."]
        if tr["meta"].get("resign"):
            note_bits.append(f"{players[tr['meta']['resign']]['display']} resigned the match.")
        if tr["meta"].get("missing"):
            note_bits.append("Some games' moves are not preserved in the source; recorded here from Fiske's record "
                             "as result-only legs (no game link).")
        child(tie, "notes", " ".join(note_bits))

    notes = (
        "1st American Chess Congress, New York, 6 October - 10 November 1857: a 16-player single-elimination "
        "knockout of matches (each match first to 3 wins, draws replayed; the final first to 5), organized by "
        "Daniel Willard Fiske and Thomas Frere. Paul Morphy won every match and took the title with an overall "
        "14 wins, 3 draws, 1 loss; placings 1 Morphy, 2 Paulsen, 3 Lichtenhein, 4 Raphael. Primary game source: a "
        "chessgames.com PGN (68 games, moves only -- no clocks; these were untimed 1857 games). The authoritative "
        "MATCH record and completeness proof is Fiske, The Book of the First American Chess Congress (New York, "
        "1859); the bracket, winners, and match scores follow it. The builder recomputes every match winner from "
        "the games and reproduces the bracket, and it verifies Morphy's 14-3-1 overall record. Two round-1 games "
        "(Meek-Fuller game 2, a Meek win; Perrin-Knott game 5, a draw) are absent from the PGN; their moves are "
        "lost but their results are recorded from Fiske as result-only bracket legs, so the matches remain "
        "complete. Two matches ended in resignation (Montgomery, called home, in round 2; Raphael in round 3). "
        "Ratings are Edo historical ratings (event-time 1857) and are present in the source for only 7 of the 16 "
        "players; no player federations or titles are asserted (neither existed in this form in 1857). "
        "Uses the CTML 2.1 bracket model."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chessgames-pgn")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note", f"68 games (moves only); SHA-256 {sha256(SOURCE_PGN)}.")
    s2 = child(root, "source", kind="metadata")
    child(s2, "uri", METADATA.as_uri())
    child(s2, "note", f"Transcribed crosstables, bracket, placings, Edo ratings; SHA-256 {sha256(METADATA)}.")
    s3 = child(root, "source", kind="fiske-book-1859")
    if FISKE_BOOK.exists():
        child(s3, "uri", FISKE_BOOK.as_uri())
        child(s3, "note", "Fiske, The Book of the First American Chess Congress (New York, 1859); the authoritative "
                          f"record for match results and completeness. SHA-256 {sha256(FISKE_BOOK)}.")
    else:
        child(s3, "note", "Fiske, The Book of the First American Chess Congress (New York, 1859); authoritative "
                          "record for match results and completeness.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(players), "games": len(games),
            "ties": len(tie_records), "eco_games": eco_n, "terminations": term_n,
            "morphy_record": "-".join(map(str, overall["morphy"])),
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
