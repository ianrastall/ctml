"""Sofia Cup 2026 - Balkan Rapid & Blitz Championships (two separate Swiss events).

Builds one CTML file per championship from the chess.com PGN (the only complete
field: every board, every result, byes, and per-move clocks on the broadcast
boards). Movetext exists only for the ~top-20 boards each round; the rest are
recorded result-only. ECO is classified from movetext via all.tsv (no ECO in the
export). Official final standings (top 50, transcribed) supply placement, seed,
and federation for the top 50 and are the arbiter for two source problems:

  * chess.com vs the Lichess/TWIC broadcast disagree on the winner of 8 rapid + 1
    blitz games (identical moves, non-mate endings). chess.com's results
    reproduce the official points exactly, so chess.com is authoritative and the
    broadcast result tags are the errors.
  * a blitz Round-8 mispairing cluster duplicated four boards; the official
    standings prove which four records are real (see BLITZ_R8_DROP) - two of the
    dropped duplicates carry (mis-attributed) movetext, which is discarded.
  * two blitz games are '*' in the source but have full movetext; their result is
    inferred from the official points of a top-50 opponent (single unknown).

The builder recomputes every player's score from the games + byes and asserts it
equals the official points for all top-50 players before emitting.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import chess.pgn

from ctml_build import (child, classify_eco, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_eco, load_pgn, q, sha256, slug)

ROOT = Path(__file__).resolve().parents[1]
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
REGS = Path(r"D:\elysium\sources\twic\Sofia_Cup_2026_Regulations-2.pdf")
RESOLVER = "ctml-sofiacup2026-builder/1"
OFFICIAL_SITE = "https://sofiachesscup.com/"

VALID_TITLES = {"GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM"}

# Blitz Round-8 mispairing: keep the four real pairings (per official standings),
# drop these four mis-attributed duplicate records (two of them carry movetext).
BLITZ_R8_DROP = {
    ("8", "Papakonstantinou, Dimitrios", "Georgiev, Kiril"),
    ("8", "Kanov, Nikola", "Ljepic, Andrej"),
    ("8", "Lodici, Lorenzo", "Jacobson, Brandon"),
    ("8", "Stoyanov, Tsvetan", "Saraci, Nderim"),
}


@dataclass
class Section:
    key: str
    champ: str            # "Rapid" / "Blitz"
    cadence: str
    rounds: int
    tc_raw: str
    init: int
    incr: int
    date_iso: str
    pgn: Path
    standings: Path
    slug: str
    tid: str
    r8drop: frozenset = frozenset()


SECTIONS = [
    Section("rapid", "Rapid", "rapid", 9, "600+5", 600, 5, "2026-08-29",
            Path(r"D:\dev\pgn\sofia-cup-2026-balkan-rapid-and-blitz-championships-rapid.pgn"),
            ROOT / "temp" / "sofia" / "rapid_top50.tsv",
            "sofia-cup-balkan-rapid-championship", "tournament-sofia-cup-2026-rapid"),
    Section("blitz", "Blitz", "blitz", 11, "180+2", 180, 2, "2026-08-30",
            Path(r"D:\dev\pgn\sofia-cup-2026-balkan-rapid-and-blitz-championships-blitz.pgn"),
            ROOT / "temp" / "sofia" / "blitz_top50.tsv",
            "sofia-cup-balkan-blitz-championship", "tournament-sofia-cup-2026-blitz",
            frozenset(BLITZ_R8_DROP)),
]


@dataclass
class Player:
    name: str
    fide_id: str | None
    title: str | None
    elo: int | None

    @property
    def pid(self) -> str:
        return f"p-fide-{self.fide_id}" if self.fide_id else f"p-name-{slug(self.name)}"

    @property
    def ref(self) -> str:
        return f"player:fide:{self.fide_id}" if self.fide_id else f"player:name:{slug(self.name)}"


@dataclass
class Official:
    rk: int
    seed: int
    fed: str
    rtg: int
    pts: float
    title: str


def load_official(path: Path) -> dict[str, Official]:
    out: dict[str, Official] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rk, sno, name, ti, fed, rtg, pts = line.split("\t")
        out[name] = Official(int(rk), int(sno), fed.strip(), int(rtg), float(pts), ti.strip())
    return out


def partition(games, r8drop):
    """Return (real_games, bye_list). Byes are (player_name, round)."""
    real, byes = [], []
    for g in games:
        w, b = g.headers["White"], g.headers["Black"]
        if b == "bye":
            byes.append((w, g.headers["Round"]))
            continue
        if w == "bye":
            byes.append((b, g.headers["Round"]))
            continue
        if (g.headers["Round"], w, b) in r8drop:
            continue
        real.append(g)
    return real, byes


def build_roster(real_games) -> dict[str, Player]:
    fide, title, elo = {}, {}, {}
    for g in real_games:
        for c in ("White", "Black"):
            nm = g.headers[c]
            fid = g.headers.get(f"{c}FideId")
            if fid:
                fide.setdefault(nm, fid)
            t = g.headers.get(f"{c}Title")
            if t and nm not in title:
                title[nm] = t
            e = g.headers.get(f"{c}Elo")
            if e:
                elo.setdefault(nm, int(e))
    roster = {}
    for nm in set(list(fide) + list(title) + list(elo)):
        t = title.get(nm)
        roster[nm] = Player(nm, fide.get(nm), t if t in VALID_TITLES else None, elo.get(nm))
    return roster


def infer_stars(real_games, official) -> dict[int, str]:
    """Infer each '*' game's result from a top-50 opponent's official points."""
    base = defaultdict(float)
    for g in real_games:
        w, b, r = g.headers["White"], g.headers["Black"], g.headers["Result"]
        if r == "1-0":
            base[w] += 1
        elif r == "0-1":
            base[b] += 1
        elif r == "1/2-1/2":
            base[w] += 0.5
            base[b] += 0.5
    inferred: dict[int, str] = {}
    for g in real_games:
        if g.headers["Result"] != "*":
            continue
        w, b = g.headers["White"], g.headers["Black"]
        votes = set()
        for side, res in ((w, "1-0"), (b, "0-1")):
            if side in official:
                gap = round(official[side].pts - base[side], 1)
                if gap == 1.0:
                    votes.add(res)
                elif gap == 0.5:
                    votes.add("1/2-1/2")
                elif gap == 0.0:
                    votes.add("0-1" if side == w else "1-0")
        if len(votes) != 1:
            raise ValueError(f"Cannot uniquely infer '*' result for {w} vs {b}: {votes}")
        inferred[id(g)] = votes.pop()
    return inferred


def result_of(game, inferred):
    return inferred.get(id(game), game.headers["Result"])


def compute_scores(real_games, byes, inferred) -> dict[str, float]:
    s: defaultdict[str, float] = defaultdict(float)
    for g in real_games:
        w, b = g.headers["White"], g.headers["Black"]
        r = result_of(g, inferred)
        if r == "1-0":
            s[w] += 1
        elif r == "0-1":
            s[b] += 1
        elif r == "1/2-1/2":
            s[w] += 0.5
            s[b] += 0.5
        elif r == "*":
            raise ValueError(f"Unresolved '*' in {w} vs {b}")
    for who, _rnd in byes:
        s[who] += 1.0
    return dict(s)


def decimal(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def build_section(sec: Section, eco_entries) -> dict[str, object]:
    games = load_pgn(sec.pgn)
    real, byes = partition(games, sec.r8drop)
    official = load_official(sec.standings)
    roster = build_roster(real)
    # ensure bye recipients exist in the roster
    for who, _r in byes:
        roster.setdefault(who, Player(who, None, None, None))

    inferred = infer_stars(real, official)
    scores = compute_scores(real, byes, inferred)

    # AUDIT: every top-50 player's computed score must equal the official points.
    mismatches = [(nm, scores.get(nm, 0.0), o.pts) for nm, o in official.items()
                  if abs(scores.get(nm, 0.0) - o.pts) > 1e-9]
    if mismatches:
        raise ValueError(f"{sec.key}: top-50 score mismatches vs official points: {mismatches}")

    # computed placement for the whole field (score-rank, ties shared) - used only
    # to order participants; official Rk is authoritative for the top 50.
    def rank_key(nm):
        return official[nm].rk if nm in official else 10_000
    order = sorted(roster.values(), key=lambda p: (rank_key(p.name), -scores.get(p.name, 0.0), p.name))

    y, m, d = (int(x) for x in sec.date_iso.split("-"))
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": sec.tid})
    header = child(root, "header")
    champ_name = f"Sofia Cup 2026 - Balkan {sec.champ} Championship"
    child(header, "name", champ_name)
    er = child(header, "eventRef",
               ref=f"event:{sec.date_iso.replace('-', '')}-{sec.date_iso.replace('-', '')}-{sec.slug}",
               source=OFFICIAL_SITE)
    child(er, "name", champ_name)
    child(header, "eventType", "swiss")
    child(header, "cadence", sec.cadence)
    child(header, "federation", "BUL")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=y, m=m, d=d, iso=sec.date_iso)
    child(child(dates, "end"), "day", y=y, m=m, d=d, iso=sec.date_iso)
    place = child(header, "placeRef", ref="place:city:BUL-sofia", kind="city")
    child(place, "name", "Sofia")
    child(place, "country", "BUL")
    child(place, "city", "Sofia")
    child(header, "venue", "Grand Hotel Sofia")
    orgs = child(header, "organizers")
    org = child(orgs, "organizer")
    child(org, "name", "Chess Club \"Maritza-Iztok\", Radnevo")
    child(org, "role", "organizer")

    # The official standings carry a federation for the top 50 only. Resolve the
    # rest by FIDE id against the official FIDE rating list, and use the overlap
    # as a cross-check: where both sources speak they must agree.
    feds = fide_federations({p.fide_id for p in order if p.fide_id})
    conflicts = [(p.name, official[p.name].fed, feds[p.fide_id]) for p in order
                 if p.fide_id in feds and official.get(p.name) and official[p.name].fed
                 and official[p.name].fed != feds[p.fide_id]]
    if conflicts:
        raise ValueError(f"{sec.key}: official standings and FIDE list disagree on federation: {conflicts}")

    participants = child(root, "participants")
    for p in order:
        o = official.get(p.name)
        part = child(participants, "participant", id=p.pid)
        ref = child(part, "playerRef", ref=p.ref, source=OFFICIAL_SITE)
        family, given = (x.strip() for x in p.name.split(",", 1)) if "," in p.name else (p.name, "")
        name = child(ref, "name", display=p.name)
        child(name, "family", family)
        if given:
            child(name, "given", given)
        fed = (o.fed if o and o.fed else None) or (feds.get(p.fide_id) if p.fide_id else None)
        if fed:
            child(ref, "federation", fed)
        title = p.title or (o.title if o and o.title in VALID_TITLES else None)
        if title:
            child(ref, "title", title)
        if p.fide_id:
            ids = child(ref, "ids")
            child(ids, "fideId", p.fide_id)
            child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        else:
            child(ref, "resolution", method="manual", resolver=RESOLVER,
                  note="No FIDE id in the source PGN; identified by name.")
        if p.elo is not None:
            snap = child(part, "ratingSnapshot", system="fide", scope=sec.cadence)
            child(snap, "value", p.elo)
            child(child(snap, "asOf"), "day", y=y, m=m, d=d, iso=sec.date_iso, raw="chess.com PGN Elo")
            child(snap, "publishedForEvent", "true")
        if o:
            child(part, "seed", o.seed)
        child(part, "score", decimal(scores.get(p.name, 0.0)))
        if o:
            child(part, "placement", o.rk)

    games_el = child(root, "games")
    pgn_uri = sec.pgn.as_uri()
    eco_n = term_n = moved_n = clocked = star_n = 0
    for g in sorted(real, key=lambda x: (int(x.headers["Round"]), int(x.headers["Board"]))):
        h = g.headers
        nodes = list(g.mainline())
        has_moves = len(nodes) >= 2
        res = result_of(g, inferred)
        ge = child(games_el, "game",
                   id=f"g-r{int(h['Round']):02d}-b{int(h['Board']):02d}",
                   round=h["Round"], board=h["Board"],
                   white=roster[h["White"]].pid, black=roster[h["Black"]].pid,
                   result=res)
        if has_moves:
            eco = classify_eco(tuple(mv.uci() for mv in g.mainline_moves()), eco_entries)
            if eco:
                child(ge, "eco", eco)
                eco_n += 1
        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence=sec.cadence)
        child(tc, "raw", sec.tc_raw)
        child(tc, "initialSeconds", sec.init)
        child(tc, "incrementSeconds", sec.incr)
        if has_moves:
            has_clocks = all(n.clock() is not None for n in nodes)
            moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(has_clocks).lower())
            for n in nodes:
                attrs = {"ply": n.ply(), "value": n.move.uci()}
                if n.clock() is not None:
                    attrs["clockSeconds"] = int(n.clock())
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
            moved_n += 1
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", pgn_uri)
        note = f"chess.com Site={h.get('Site', '')}."
        if not has_moves:
            note += " Moves not exported for this board; recorded result-only."
        if id(g) in inferred:
            star_n += 1
            note += (" Result was '*' (unrecorded) in the source; inferred from the official final "
                     "standings (a top-50 opponent's point total pins this game).")
        child(src, "note", note)

    nong = child(games_el, "nonGames")
    for who, rnd in sorted(byes, key=lambda x: (int(x[1]), x[0])):
        child(nong, "nonGame", participant=roster[who].pid, round=rnd, kind="bye-full")

    winner = order[0]
    notes = (
        f"{champ_name}, Sofia (Grand Hotel Sofia), {sec.date_iso}: a {sec.rounds}-round individual Swiss, "
        f"{sec.cadence} time control {sec.tc_raw}, organized by Chess Club \"Maritza-Iztok\" (Radnevo) and "
        f"FIDE-rated for {sec.cadence}. Part of Sofia Cup 2026, which ran two SEPARATE championships (this "
        f"{sec.champ} and the {'Blitz' if sec.key == 'rapid' else 'Rapid'}); they have different round counts "
        "and fields, so each is its own file. Winner: "
        f"{winner.name} ({decimal(scores.get(winner.name, 0.0))}/{sec.rounds}). "
        f"{len(roster)} players, {len(real)} games and {len(byes)} full-point byes (byes recorded as "
        "nonGame kind=bye). The complete field, all results, byes, and per-move clocks come from the "
        "chess.com PGN; movetext exists only for the broadcast (top ~20) boards, so lower boards are "
        f"recorded result-only ({len(real) - moved_n} of {len(real)}). ECO is classified from movetext by "
        "longest UCI-prefix match (no ECO in the export). Official final standings (top 50, transcribed) "
        "give placement, seed, and federation for the top 50; every player's score is recomputed from the "
        "games plus byes and REPRODUCES the official points for all top 50 exactly (placements below 50 "
        "are not asserted - only the top 50 were available). Rating snapshots are the FIDE "
        f"{sec.cadence} ratings carried by the chess.com PGN (they match the official standings' rating "
        "column). Identity is by FIDE id (two blitz players lacking one are identified by name). "
    )
    if sec.key == "rapid":
        notes += ("Source note: an independent broadcast PGN (Lichess/TWIC, top boards only) disagreed with "
                  "chess.com on the winner of 8 rapid games (identical moves, resignation/time endings); "
                  "chess.com's results reproduce the official points exactly, so chess.com is authoritative "
                  "and those broadcast result tags are errors. ")
    else:
        notes += ("Source notes: (a) the same broadcast disagreed with chess.com on 1 blitz game's winner; "
                  "chess.com matches the official points. (b) A Round-8 export glitch duplicated four boards; "
                  "the official standings identify the four real pairings, so the four mis-attributed "
                  "duplicate records were dropped (two carried movetext, which is discarded). (c) Two games "
                  "were '*' in the source but have full movetext; their results were inferred from the "
                  "official points of a top-50 opponent (Gurel beat Yordanov; Piorun beat Hristov). ")
    notes += "Uses the CTML 2.1 model (participant/placement/seed; nonGame byes)."
    child(root, "notes", notes)

    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", pgn_uri)
    child(s1, "note", f"Complete field, all results, byes, and per-move clocks (broadcast boards). "
                      f"{len(games)} records. SHA-256 {sha256(sec.pgn)}.")
    s2 = child(root, "source", kind="official-standings")
    child(s2, "uri", sec.standings.as_uri())
    child(s2, "note", "Official final standings (top 50; Rk/SNo/Name/Title/FED/Rtg/Pts + tiebreaks) "
                      "transcribed from the official results. Arbiter for placements/seeds and for the "
                      "result reconciliation. Computed scores match the Pts column for all top 50.")
    s3 = child(root, "source", kind="regulations")
    child(s3, "uri", REGS.as_uri())
    child(s3, "note", f"Sofia Cup 2026 regulations (two separate championships, schedule, organizer, venue, "
                      f"prizes, Hort prize system). SHA-256 {sha256(REGS)}.")
    fide_list = fide_rating_list()
    s_fide = child(root, "source", kind="fide-rating-list")
    child(s_fide, "uri", fide_list.as_uri())
    child(s_fide, "note", f"Official FIDE standard rating list ({fide_list.stem}); resolves the "
                          f"federations the official top-50 standings do not cover, by FIDE id. "
                          f"SHA-256 {sha256(fide_list)}.")
    s4 = child(root, "source", kind="eco-table")
    child(s4, "uri", ECO_TABLE.as_uri())
    child(s4, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    out = ROOT / "tours" / f"{sec.date_iso.replace('-', '')}-{sec.date_iso.replace('-', '')}_{sec.slug}.ctml"
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with out.open("ab") as st:
        st.write(b"\n")

    return {"file": out.name, "players": len(roster), "games": len(real), "byes": len(byes),
            "moved_games": moved_n, "result_only": len(real) - moved_n, "eco": eco_n,
            "clocked_plies": clocked, "stars_inferred": star_n, "winner": winner.name,
            "bytes": out.stat().st_size, "sha256": sha256(out)}


def build() -> dict[str, object]:
    eco_entries = load_eco(ECO_TABLE)
    res = {}
    for sec in SECTIONS:
        r = build_section(sec, eco_entries)
        for k, v in r.items():
            res[f"{sec.key}.{k}"] = v
    return res


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
