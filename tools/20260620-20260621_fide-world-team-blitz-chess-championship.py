from __future__ import annotations

import hashlib
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.pgn
import chess.polyglot

from ctml_build import (child, classify_eco, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_eco, load_pgn, norm, q, sha256, slug)



ROOT = Path(__file__).resolve().parents[1]
SRC = Path(r"D:\elysium\sources\twic")
CROSSTABLE = SRC / "2026-fide-world-blitz-team-chess-championship.txt"
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260620-20260621_fide-world-team-blitz-chess-championship.ctml"
RESOLVER = "ctml-wbtcc-builder/1"

# (tag, filename, kind, pool-letter-or-None)
SOURCES = [
    ("pa", "2026-fide-world-blitz-team-chess-championship-pool-a.pgn", "pool", "A"),
    ("pb", "2026-fide-world-blitz-team-chess-championship-pool-b.pgn", "pool", "B"),
    ("pc", "2026-fide-world-blitz-team-chess-championship-pool-c.pgn", "pool", "C"),
    ("pd", "2026-fide-world-blitz-team-chess-championship-pool-d.pgn", "pool", "D"),
    ("ko", "2026-fide-world-blitz-team-chess-championship-knockout.pgn", "ko", None),
    ("f5", "2026-fide-world-blitz-team-chess-championship-knockout-5th-place.pgn", "fifth", None),
]

ARBITERS = [
    ("Freyd, Laurent", "chief arbiter", "620700"),
    ("Kadimova, Ilaha", "deputy chief arbiter", "13400045"),
    ("Bauyrzhan, Kaussar", "arbiter", "13707019"),
]


# Knockout metadata (from the bracket tables); keys normalized at load.
_KO_SEEDS = {"WR Chess": 1, "Hexamind Chess Team": 2, "Endgame.AI": 3, "Mr Birdie and friends": 4,
             "Dragon Chilling": 5, "Team MGD1": 6, "Chessgurukul": 7, "Uzbekistan": 8,
             "Odlar Yurdu (Azerbaijan)": 9, "Chess United": 10, "Kazchess": 11, "Barys": 12,
             "Sky Chess": 13, "Schnappi Krokodil Team": 14, "Interstellar Club": 15, "Indonesia": 16}
_FIFTH_SEEDS = {"Mr Birdie and friends": 1, "Chessgurukul": 2, "Team MGD1": 3, "WR Chess": 4}
# Unambiguous final placements (from the decisive matches): champion..6th.
_PLACEMENT = {"Dragon Chilling": 1, "Endgame.AI": 2, "Uzbekistan": 3, "Hexamind Chess Team": 4,
              "WR Chess": 5, "Team MGD1": 6}


def _pairs(*items):
    return {frozenset((norm(a), norm(b))): norm(w) for a, b, w in items}


_KO_WINNERS = _pairs(
    ("WR Chess", "Indonesia", "WR Chess"), ("Hexamind Chess Team", "Interstellar Club", "Hexamind Chess Team"),
    ("Endgame.AI", "Schnappi Krokodil Team", "Endgame.AI"), ("Mr Birdie and friends", "Sky Chess", "Mr Birdie and friends"),
    ("Dragon Chilling", "Barys", "Dragon Chilling"), ("Team MGD1", "Kazchess", "Team MGD1"),
    ("Chessgurukul", "Chess United", "Chessgurukul"), ("Uzbekistan", "Odlar Yurdu (Azerbaijan)", "Uzbekistan"),
    ("Uzbekistan", "WR Chess", "Uzbekistan"), ("Hexamind Chess Team", "Chessgurukul", "Hexamind Chess Team"),
    ("Endgame.AI", "Team MGD1", "Endgame.AI"), ("Dragon Chilling", "Mr Birdie and friends", "Dragon Chilling"),
    ("Endgame.AI", "Hexamind Chess Team", "Endgame.AI"), ("Dragon Chilling", "Uzbekistan", "Dragon Chilling"),
    ("Dragon Chilling", "Endgame.AI", "Dragon Chilling"), ("Uzbekistan", "Hexamind Chess Team", "Uzbekistan"),
)
_FIFTH_WINNERS = _pairs(
    ("Mr Birdie and friends", "WR Chess", "WR Chess"), ("Chessgurukul", "Team MGD1", "Team MGD1"),
    ("Team MGD1", "WR Chess", "WR Chess"),
)
KO_SEEDS = {norm(k): v for k, v in _KO_SEEDS.items()}
FIFTH_SEEDS = {norm(k): v for k, v in _FIFTH_SEEDS.items()}
PLACEMENT = {norm(k): v for k, v in _PLACEMENT.items()}


@dataclass
class PoolRow:
    rank: int
    name: str
    tb1: float
    tb2: float
    tb3: str
    tb4: str
    cells: list  # index 1..12 -> board pts vs that column rank (None on diagonal)


def parse_pools(path):
    text = path.read_text(encoding="utf-8").splitlines()
    pools = {}
    current = None
    for line in text:
        m = re.match(r"=== POOL ([ABCD]) ", line)
        if m:
            current = m.group(1)
            pools[current] = {}
            continue
        if current is None:
            continue
        f = line.split("\t")
        if len(f) == 18 and f[0].strip().isdigit():
            cells = [None] * 13
            for col in range(1, 13):
                v = f[1 + col].strip()
                cells[col] = None if v == "*" else float(v)
            # Columns: ... TB1(14)=match points, TB2(15)=game points, TB3(16)/TB4(17)=Sonneborn-Berger.
            pools[current][int(f[0])] = PoolRow(int(f[0]), f[1].strip(), float(f[14]), float(f[15]),
                                                f[16].strip(), f[17].strip(), cells)
        elif line.startswith("=== ") and not line.startswith(f"=== POOL {current}"):
            current = None
    for p, rows in pools.items():
        if len(rows) != 12:
            raise ValueError(f"Pool {p}: expected 12 rows, got {len(rows)}")
    return pools


def rec(game, tag, kind, pool):
    h = game.headers
    nodes = list(game.mainline())
    return {"game": game, "tag": tag, "kind": kind, "pool": pool, "round": h["Round"],
            "board": int(h["Board"]), "wfide": h["WhiteFideId"], "bfide": h["BlackFideId"],
            "wname": h["White"], "bname": h["Black"], "wteam": norm(h["WhiteTeam"]), "bteam": norm(h["BlackTeam"]),
            "wteam_raw": h["WhiteTeam"].strip(), "bteam_raw": h["BlackTeam"].strip(), "result": h["Result"],
            "welo": h.get("WhiteElo"), "belo": h.get("BlackElo"), "wtitle": h.get("WhiteTitle"),
            "btitle": h.get("BlackTitle"), "nodes": nodes, "plies": len(nodes)}


def points(result, side):
    if result == "1-0":
        return 1.0 if side == "w" else 0.0
    if result == "0-1":
        return 0.0 if side == "w" else 1.0
    if result == "1/2-1/2":
        return 0.5
    if result == "0-0":
        return 0.0
    raise ValueError(f"bad result {result!r}")


def dedup_round(records):
    """One game per player per (tag, round); keep higher board (drops source strays)."""
    by = defaultdict(list)
    for r in records:
        by[(r["tag"], r["round"])].append(r)
    kept, dropped = [], 0
    for _, rs in by.items():
        used = set()
        for r in sorted(rs, key=lambda x: x["board"], reverse=True):
            if r["wfide"] in used or r["bfide"] in used:
                dropped += 1
                continue
            used.add(r["wfide"]); used.add(r["bfide"])
            kept.append(r)
    return kept, dropped


def build():
    pools = parse_pools(CROSSTABLE)
    pool_rank = {}  # norm name -> (pool letter, rank)
    for p, rows in pools.items():
        for rank, row in rows.items():
            pool_rank[norm(row.name)] = (p, rank)
    raw_name = {}  # norm -> display name (prefer crosstable spelling)
    for rows in pools.values():
        for row in rows.values():
            raw_name[norm(row.name)] = row.name

    all_records = []
    per_source_count = {}
    for tag, fname, kind, pool in SOURCES:
        games = load_pgn(SRC / fname)
        per_source_count[tag] = len(games)
        for g in games:
            all_records.append(rec(g, tag, kind, pool))
    records, dropped = dedup_round(all_records)

    # team display names: prefer crosstable; fall back to PGN raw
    for r in records:
        raw_name.setdefault(r["wteam"], r["wteam_raw"])
        raw_name.setdefault(r["bteam"], r["bteam_raw"])

    team_norms = {r["wteam"] for r in records} | {r["bteam"] for r in records}
    if team_norms != set(pool_rank):
        raise ValueError(f"team set mismatch.\n only in PGN: {team_norms - set(pool_rank)}\n only in pools: {set(pool_rank) - team_norms}")
    team_id = {t: f"t-{slug(raw_name[t])}" for t in team_norms}
    # ensure unique slugs
    seen = {}
    for t in sorted(team_norms):
        base = team_id[t]
        s, n = base, 2
        while s in seen.values():
            s, n = f"{base}-{n}", n + 1
        seen[t] = s
    team_id = seen

    # participants (union by fide id)
    players = {}
    player_team = {}
    for r in records:
        for fide, name, elo, title, team in ((r["wfide"], r["wname"], r["welo"], r["wtitle"], r["wteam"]),
                                             (r["bfide"], r["bname"], r["belo"], r["btitle"], r["bteam"])):
            if fide not in players:
                players[fide] = {"name": name, "title": title or None,
                                 "rating": int(elo) if elo and elo.isdigit() and int(elo) > 0 else None}
            else:
                pl = players[fide]
                if not pl["title"] and title:
                    pl["title"] = title
                if pl["rating"] is None and elo and elo.isdigit() and int(elo) > 0:
                    pl["rating"] = int(elo)
            player_team.setdefault(fide, team)

    # intra-match board (per tag+round+team-pair) and stable game ids
    match_key = lambda r: (r["tag"], r["round"], frozenset((r["wteam"], r["bteam"])))
    matches = defaultdict(list)
    for r in records:
        matches[match_key(r)].append(r)
    intra = {}
    gid = {}
    seen_ids = set()
    for key, rs in matches.items():
        for i, r in enumerate(sorted(rs, key=lambda x: x["board"]), start=1):
            intra[id(r)] = i
        for r in rs:
            base = f"g-{r['tag']}-r{r['round'].replace('.', '-')}-b{intra[id(r)]}"
            g, n = base, 2
            while g in seen_ids:
                g, n = f"{base}-{n}", n + 1
            seen_ids.add(g)
            gid[id(r)] = g

    board_counts = defaultdict(Counter)
    for r in records:
        if r["kind"] == "pool":
            board_counts[r["wfide"]][intra[id(r)]] += 1
            board_counts[r["bfide"]][intra[id(r)]] += 1

    # ---- POOL matches + head-to-head audit ----
    pool_matches = []  # (pool, round, home_norm, away_norm, home_score, away_score, mp_home, mp_away, [records])
    pool_conflicts = []  # boards where chess.com results disagree with the official crosstable
    conflict_matches = set()
    for key, rs in matches.items():
        tag, rnd, pair = key
        if not rs or rs[0]["kind"] != "pool":
            continue
        pool = rs[0]["pool"]
        teams = list({rs[0]["wteam"], rs[0]["bteam"]})
        if len(teams) != 2:
            raise ValueError(f"pool match with !=2 teams: {key}")
        top = min(rs, key=lambda x: intra[id(x)])
        home, away = top["wteam"], top["bteam"]
        ri, rj = pool_rank[home][1], pool_rank[away][1]
        exp_home = pools[pool][ri].cells[rj]
        exp_away = pools[pool][rj].cells[ri]
        # A handful of games have an unrecorded result ("*") in the source but
        # a definite board-point total in the crosstable. When the "*" game is
        # the only unknown board in its match, back its result out of the
        # crosstable (exactly one board of freedom); note it as inferred.
        stars = [r for r in rs if r["result"] == "*"]
        if stars:
            if len(stars) > 1 or exp_home is None:
                raise ValueError(f"Pool {pool} r{rnd} {raw_name[home]} vs {raw_name[away]}: "
                                 f"{len(stars)} unrecorded results, cannot infer")
            kh = sum(points(r["result"], "w" if r["wteam"] == home else "b") for r in rs if r["result"] != "*")
            ka = sum(points(r["result"], "w" if r["wteam"] == away else "b") for r in rs if r["result"] != "*")
            star = stars[0]
            wpts = (exp_home - kh) if star["wteam"] == home else (exp_away - ka)
            star["result"] = "1-0" if abs(wpts - 1) < 1e-9 else "0-1" if abs(wpts) < 1e-9 else "1/2-1/2" if abs(wpts - 0.5) < 1e-9 else None
            if star["result"] is None:
                raise ValueError(f"Pool {pool} r{rnd}: inferred board point {wpts} is not 0/0.5/1")
            star["inferred"] = True
        sc = {home: 0.0, away: 0.0}
        for r in rs:
            sc[r["wteam"]] += points(r["result"], "w")
            sc[r["bteam"]] += points(r["result"], "b")
        if exp_home is None:
            raise ValueError(f"Pool {pool} r{rnd}: no crosstable cell for {raw_name[home]} vs {raw_name[away]}")
        if abs(exp_home - sc[home]) > 1e-9 or abs(exp_away - sc[away]) > 1e-9:
            # chess.com game results disagree with the official crosstable on
            # one or more boards. Keep the games as recorded; the official
            # crosstable stands for standings; document the conflict.
            pool_conflicts.append((pool, rnd, raw_name[home], raw_name[away], sc[home], sc[away], exp_home, exp_away))
            conflict_matches.add((pool, rnd, frozenset((home, away))))
        mp_home, mp_away = (2, 0) if sc[home] > sc[away] else (0, 2) if sc[home] < sc[away] else (1, 1)
        pool_matches.append((pool, rnd, home, away, sc[home], sc[away], mp_home, mp_away, rs))

    # per-team pool totals audit (match points TB1, game points TB2)
    tmp = defaultdict(float)
    tgp = defaultdict(float)
    for pool, rnd, home, away, sh, sa, mph, mpa, rs in pool_matches:
        tmp[home] += mph; tmp[away] += mpa
        tgp[home] += sh; tgp[away] += sa
    gp_off = []
    for t, (pool, rank) in pool_rank.items():
        row = pools[pool][rank]
        if abs(tmp[t] - row.tb1) > 1e-9:
            raise ValueError(f"{raw_name[t]}: match points {tmp[t]} != crosstable TB1 {row.tb1} "
                             f"(a board conflict changed a match OUTCOME, not just a margin)")
        if abs(tgp[t] - row.tb2) > 1e-9:
            gp_off.append((raw_name[t], tgp[t], row.tb2))
    if len(pool_conflicts) > 8:
        raise ValueError(f"{len(pool_conflicts)} board-level chess.com/crosstable conflicts (>8): "
                         "systemic, stopping.\n" + "\n".join(map(str, pool_conflicts)))

    # ---- KNOCKOUT ties (main + fifth) ----
    def ko_stage(tag, major, tie_teams):
        if tag == "ko":
            if major == 1:
                return "round-of-16", 1
            if major == 2:
                return "quarterfinal", 2
            if major == 3:
                return "semifinal", 3
            if major == 4:
                return ("final", 4) if any("dragon" in t or "endgame" in t for t in tie_teams) and \
                    not any("hexamind" in t or "uzbekistan" in t for t in tie_teams) else ("third-place", 4)
        else:
            if major == 1:
                return "fifth-place-round-1", 5
            return "fifth-place-final", 6
        raise ValueError(f"unknown stage {tag} {major}")

    ties = defaultdict(lambda: defaultdict(list))  # (tag, major, pair) -> {minor: [records]}
    for r in records:
        if r["kind"] in ("ko", "fifth"):
            major, minor = r["round"].split(".")
            ties[(r["tag"], int(major), frozenset((r["wteam"], r["bteam"])))][int(minor)].append(r)

    ko_out = []
    for (tag, major, pair), legs in ties.items():
        team_list = list(pair)
        # first side = the one on the top board of the first leg
        first_leg = legs[min(legs)]
        top = min(first_leg, key=lambda x: intra[id(x)])
        first, second = top["wteam"], top["bteam"]
        leg_rows = []
        mp = {first: 0.0, second: 0.0}
        for minor in sorted(legs):
            rs = legs[minor]
            sc = {first: 0.0, second: 0.0}
            for r in rs:
                sc[r["wteam"]] += points(r["result"], "w")
                sc[r["bteam"]] += points(r["result"], "b")
            if sc[first] > sc[second]:
                mp[first] += 2
            elif sc[first] < sc[second]:
                mp[second] += 2
            else:
                mp[first] += 1; mp[second] += 1
            leg_rows.append((minor, sc[first], sc[second], sorted(rs, key=lambda x: intra[id(x)])))
        winner = first if mp[first] > mp[second] else second if mp[second] > mp[first] else None
        table = _KO_WINNERS if tag == "ko" else _FIFTH_WINNERS
        expected = table.get(pair)
        if expected is None:
            raise ValueError(f"unexpected {tag} tie: {[raw_name[t] for t in pair]}")
        if winner != expected:
            raise ValueError(f"{tag} tie {[raw_name[t] for t in pair]}: computed winner "
                             f"{raw_name.get(winner)} != expected {raw_name[expected]} (mp {mp})")
        stage, order = ko_stage(tag, major, pair)
        ko_out.append({"tag": tag, "stage": stage, "order": order, "first": first, "second": second,
                       "mp": mp, "winner": winner, "legs": leg_rows})

    # ================= EMIT =================
    eco = load_eco(ECO_TABLE)
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-wbtcc-2026"})
    header = child(root, "header")
    child(header, "name", "FIDE World Team Blitz Chess Championship 2026")
    er = child(header, "eventRef", ref="event:20260620-20260621-fide-world-team-blitz-chess-championship-2026",
               source=(SRC / SOURCES[0][1]).as_uri())
    child(er, "name", "FIDE World Team Blitz Chess Championship 2026")
    child(header, "eventType", "team")
    child(header, "eventType", "group-stage")
    child(header, "eventType", "knockout")
    child(header, "cadence", "blitz")
    child(header, "federation", "FID")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=6, d=20, iso="2026-06-20")
    child(child(dates, "end"), "day", y=2026, m=6, d=21, iso="2026-06-21")
    place = child(header, "placeRef", ref="place:city:HKG-hong-kong", kind="city")
    child(place, "name", "Hong Kong")
    child(place, "country", "HKG")
    child(place, "city", "Hong Kong")
    child(header, "venue", "Queen Elizabeth Stadium, Wan Chai")
    orgs = child(header, "organizers")
    for o in ("Hong Kong China Chess Federation Limited", "FIDE"):
        oe = child(orgs, "organizer"); child(oe, "name", o); child(oe, "role", "organizer")
    arb = child(header, "arbiters")
    for name, role, fid in ARBITERS:
        a = child(arb, "arbiter"); child(a, "name", name); child(a, "role", role)
        child(child(a, "ids"), "fideId", fid)

    # The broadcast PGN carries no federation; resolve it by FIDE id. Teams here
    # are clubs, not nations, so a player's federation cannot come from the team.
    feds = fide_federations(set(players))

    participants = child(root, "participants")
    for fide in sorted(players, key=lambda f: (-(players[f]["rating"] or 0), players[f]["name"])):
        pl = players[fide]
        part = child(participants, "participant", id=f"p-fide-{fide}")
        ref = child(part, "playerRef", ref=f"player:fide:{fide}", source=(SRC / SOURCES[0][1]).as_uri())
        nm = child(ref, "name", display=pl["name"])
        if "," in pl["name"]:
            fam, giv = (x.strip() for x in pl["name"].split(",", 1))
            child(nm, "family", fam)
            if giv:
                child(nm, "given", giv)
        else:
            child(nm, "unstructured", pl["name"])
        if fide in feds:
            child(ref, "federation", feds[fide])
        if pl["title"]:
            child(ref, "title", pl["title"])
        child(child(ref, "ids"), "fideId", fide)
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        if pl["rating"] is not None:
            snap = child(part, "ratingSnapshot", system="fide", scope="blitz")
            child(snap, "value", pl["rating"])
            child(child(snap, "asOf"), "month", y=2026, m=6, raw="event blitz rating in source PGN")
            child(snap, "publishedForEvent", "false")

    teams_el = child(root, "teams")
    for t in sorted(team_norms, key=lambda t: (pool_rank[t][0], pool_rank[t][1])):
        pool, rank = pool_rank[t]
        row = pools[pool][rank]
        team = child(teams_el, "team", id=team_id[t], ref=f"team:{team_id[t][2:]}")
        child(team, "name", row.name)
        roster = child(team, "roster")
        members = [f for f, tt in player_team.items() if tt == t]
        members.sort(key=lambda f: (min(board_counts[f], key=lambda b: (-board_counts[f][b], b)) if board_counts[f] else 99,
                                    -(players[f]["rating"] or 0)))
        for f in members:
            attrs = {"participant": f"p-fide-{f}"}
            if board_counts[f]:
                attrs["boardOrder"] = min(board_counts[f], key=lambda b: (-board_counts[f][b], b))
            child(roster, "member", **attrs)
        st = child(team, "standing")
        child(st, "rank", rank)
        child(st, "matchPoints", int(row.tb1) if float(row.tb1).is_integer() else row.tb1)
        child(st, "gamePoints", int(row.tb2) if float(row.tb2).is_integer() else row.tb2)
        tb = child(st, "tiebreaks")
        child(tb, "tiebreak", name="fide-sonneborn-berger", value=row.tb3)
        child(tb, "tiebreak", name="fide-sonneborn-berger-2", value=row.tb4)
        if t in PLACEMENT:
            child(st, "placement", PLACEMENT[t])

    groups_el = child(root, "groups")
    for pool in ("A", "B", "C", "D"):
        grp = child(groups_el, "group", id=f"grp-pool-{pool.lower()}", format="round-robin", rounds=11)
        child(grp, "name", f"Pool {pool}")
        for rank in range(1, 13):
            t = norm(pools[pool][rank].name)
            child(grp, "member", competitor=team_id[t], seed=rank)

    games_el = child(root, "games")
    clocked = eco_n = forfeits = 0
    for r in sorted(records, key=lambda x: (x["tag"], x["round"], x["board"])):
        g = r["game"]; nodes = r["nodes"]
        ge = child(games_el, "game", id=gid[id(r)], round=r["round"], board=intra[id(r)],
                   white=f"p-fide-{r['wfide']}", black=f"p-fide-{r['bfide']}",
                   whiteTeam=team_id[r["wteam"]], blackTeam=team_id[r["bteam"]], result=r["result"])
        uci = tuple(n.move.uci() for n in nodes)
        if uci:
            code = classify_eco(uci, eco)
            if code:
                child(ge, "eco", code); eco_n += 1
        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence="blitz")
        child(tc, "raw", g.headers.get("TimeControl", "180+2"))
        child(tc, "initialSeconds", 180); child(tc, "incrementSeconds", 2)
        if nodes:
            has = all(n.clock() is not None for n in nodes)
            mv = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(has).lower())
            for n in nodes:
                a = {"ply": n.ply(), "value": n.move.uci()}
                c = n.clock()
                if c is not None:
                    a["clockSeconds"] = int(round(c)); clocked += 1
                child(mv, "move", **a)
            term = game_termination(g)
            if term:
                child(ge, "termination", term)
            traj, final = fingerprints(g)
            fp = child(ge, "fingerprints")
            child(fp, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
            child(fp, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        else:
            forfeits += 1
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", (SRC / dict((t, f) for t, f, *_ in SOURCES)[r["tag"]]).as_uri())
        note = f"Source {r['tag']} raw board {r['board']}, intra-match board {intra[id(r)]}."
        if r.get("inferred"):
            note += " Result unrecorded in the source PGN; inferred from the pool crosstable (sole unknown board in the match)."
        child(src, "note", note)

    tms = child(root, "teamMatches")
    for pool, rnd, home, away, sh, sa, mph, mpa, rs in sorted(pool_matches, key=lambda m: (m[0], int(m[1]))):
        tm = child(tms, "teamMatch", round=rnd, home=team_id[home], away=team_id[away],
                   group=f"grp-pool-{pool.lower()}")
        child(tm, "homeScore", int(sh) if float(sh).is_integer() else sh)
        child(tm, "awayScore", int(sa) if float(sa).is_integer() else sa)
        child(tm, "matchPoints", home=mph, away=mpa)
        boards = child(tm, "boards")
        for r in sorted(rs, key=lambda x: intra[id(x)]):
            child(boards, "board", number=intra[id(r)], game=gid[id(r)])
        if (pool, rnd, frozenset((home, away))) in conflict_matches:
            child(tm, "notes", "One or more board results here differ between the chess.com PGN and the official "
                               "ChessResults crosstable; games are kept as chess.com recorded them, and the match "
                               "outcome is unchanged, but the official board-point margin differs.")

    bracket = child(root, "bracket", kind="single-elimination")
    for tie in sorted(ko_out, key=lambda x: (x["order"], -x["mp"][x["winner"]] if x["winner"] else 0)):
        stage = child(bracket, "stage", name=tie["stage"], order=tie["order"])
        te = child(stage, "tie", winner=team_id[tie["winner"]])
        for side_norm in (tie["first"], tie["second"]):
            seeds = KO_SEEDS if tie["tag"] == "ko" else FIFTH_SEEDS
            attrs = {"competitor": team_id[side_norm], "score": int(tie["mp"][side_norm]) if float(tie["mp"][side_norm]).is_integer() else tie["mp"][side_norm]}
            if side_norm in seeds:
                attrs["seed"] = seeds[side_norm]
            attrs["outcome"] = "win" if side_norm == tie["winner"] else "loss"
            child(te, "side", **attrs)
        for minor, s1, s2, rs in tie["legs"]:
            leg = child(te, "leg", number=minor,
                        firstScore=int(s1) if float(s1).is_integer() else s1,
                        secondScore=int(s2) if float(s2).is_integer() else s2)
            for r in rs:
                child(leg, "board", number=intra[id(r)], game=gid[id(r)])

    champion = raw_name[[t for t in PLACEMENT if PLACEMENT[t] == 1][0]]
    if pool_conflicts:
        cc = "; ".join(f"Pool {p} r{r} {h} vs {a} ({sh:g}-{sa:g} in the games vs {eh:g}-{ea:g} official)"
                       for p, r, h, a, sh, sa, eh, ea in pool_conflicts)
        conflict_clause = (
            f"Every pool-match OUTCOME (match points) and every knockout tie winner reproduce the official records "
            f"exactly. {len(pool_conflicts)} individual board(s) carry a result disagreement between the chess.com "
            f"PGN and the official crosstable that changes only the margin, not the match outcome; each game is kept "
            f"exactly as chess.com recorded it while the standings follow the official crosstable -- {cc}. ")
    else:
        conflict_clause = ("The builder reproduces all four pool crosstables and every knockout tie winner exactly. ")
    notes = (
        "FIDE World Team Blitz Championship: 48 teams in four 12-team pools (A-D), each an 11-round team "
        "round-robin on 20 June 2026 (blitz 3+2), then a 16-team Stage-2 knockout and a separate 5th-place "
        "play-off on 21 June, all at Queen Elizabeth Stadium, Wan Chai, Hong Kong. This one file combines the "
        f"whole championship. Champion: {champion}. Primary source: chess.com broadcast PGNs (per-move clocks, "
        "FIDE ids, teams); pool standings + the completeness proof come from the four ChessResults pool "
        "crosstables, and the knockout brackets from the ChessResults bracket tables. The builder recomputes "
        "every pool's head-to-head board points, match points and game points from the games. " + conflict_clause +
        f"{dropped} duplicate line-items were collapsed by (source, round, player-pair). {forfeits} result-only "
        "games (no movetext) are stored without moves. Boards use each match's intra-match numbering (1-6); the "
        "source's raw board is in each game's source note. Player federations are not in the source PGNs, so "
        "none are asserted. Final placements 1-6 (from the decisive knockout matches) are on team standings; the "
        "full bracket records every knockout result. Uses the CTML 2.1 model (teams, groups, teamMatches, "
        "bracket)."
    )
    child(root, "notes", notes)
    for tag, fname, kind, pool in SOURCES:
        s = child(root, "source", kind="chess.com-pgn")
        child(s, "uri", (SRC / fname).as_uri())
        child(s, "note", f"{fname} ({per_source_count[tag]} games); SHA-256 {sha256(SRC / fname)}.")
    fide_list = fide_rating_list()
    s_fide = child(root, "source", kind="fide-rating-list")
    child(s_fide, "uri", fide_list.as_uri())
    child(s_fide, "note", f"Official FIDE standard rating list ({fide_list.stem}); resolves player "
                          f"federations by FIDE id. SHA-256 {sha256(fide_list)}.")
    s = child(root, "source", kind="chess-results-crosstables")
    child(s, "uri", CROSSTABLE.as_uri())
    child(s, "note", f"Four pool crosstables + Stage-2 and 5th-place bracket tables; SHA-256 {sha256(CROSSTABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "teams": len(team_norms), "participants": len(players),
            "games_in": len(all_records), "games_out": len(records), "dropped": dropped,
            "pool_matches": len(pool_matches), "ko_ties": len(ko_out), "forfeits": forfeits,
            "board_conflicts": len(pool_conflicts), "gamepoint_teams_off": len(gp_off),
            "clocked_plies": clocked, "eco_games": eco_n, "champion": champion,
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
