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
                        game_termination, load_eco, load_pgn, norm as normalize_team, q, sha256, slug)



ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\2026-fide-world-rapid-team-chess-championship.pgn")
CROSSTABLE = Path(r"D:\elysium\sources\twic\2026-fide-world-rapid-team-chess-championship.txt")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260617-20260619_fide-world-team-rapid-chess-championship.ctml"

SITE_SOURCE = SOURCE_PGN.as_uri()
RESOLVER = "ctml-wrtcc-builder/1"

# Arbiters/officials from the ChessResults metadata capture (.txt).
ARBITERS = [
    ("Freyd, Laurent", "chief arbiter", "620700"),
    ("Kadimova, Ilaha", "deputy chief arbiter", "13400045"),
    ("Bauyrzhan, Kaussar", "arbiter", "13707019"),
]


def points_for(result: str, side: str) -> float:
    if result == "1-0":
        return 1.0 if side == "white" else 0.0
    if result == "0-1":
        return 0.0 if side == "white" else 1.0
    if result == "1/2-1/2":
        return 0.5
    if result == "0-0":
        return 0.0
    raise ValueError(f"Unsupported result {result!r}")


# ---------------------------------------------------------------------------
# Crosstable (the completeness proof): rank -> standings + per-round pairings.
# ---------------------------------------------------------------------------
@dataclass
class TeamRow:
    rank: int
    name: str
    match_points: float
    game_points: float
    tb2: str
    tb4: str
    # round -> (opponent_rank, color, board_points_scored)
    rounds: dict[int, tuple[int, str, float]] = field(default_factory=dict)


CELL = re.compile(r"^(\d+)([wb])([0-9]+(?:\.[0-9]+)?)$")


def parse_crosstable(path: Path) -> dict[int, TeamRow]:
    rows: dict[int, TeamRow] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != 18 or not fields[0].strip().isdigit():
            continue
        rank = int(fields[0])
        name = fields[1].strip()
        round_cells = fields[2:14]
        match_points = float(fields[14])
        game_points = float(fields[16])
        row = TeamRow(rank, name, match_points, game_points, fields[15].strip(), fields[17].strip())
        for index, cell in enumerate(round_cells, start=1):
            cell = cell.strip()
            m = CELL.match(cell)
            if not m:
                raise ValueError(f"Unparseable crosstable cell {cell!r} (team {name}, round {index})")
            row.rounds[index] = (int(m.group(1)), m.group(2), float(m.group(3)))
        rows[rank] = row
    if len(rows) != 48:
        raise ValueError(f"Expected 48 crosstable rows, got {len(rows)}")
    return rows


@dataclass
class PlayerAgg:
    fide_id: str
    display: str
    title: str | None
    rating: int | None
    team_norm: str

    @property
    def participant_id(self) -> str:
        return f"p-fide-{self.fide_id}"


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    crosstable = parse_crosstable(CROSSTABLE)
    rank_by_norm = {normalize_team(row.name): row.rank for row in crosstable.values()}

    # ---- 1. Deduplicate. A given player pair meets once per round; the extra
    #         line-items in rounds 1-2 are duplicates. Keep the copy with more
    #         plies (a real game over an empty placeholder), then the lower
    #         board. Conflicting results are reported.
    def rec(game: chess.pgn.Game) -> dict:
        h = game.headers
        nodes = list(game.mainline())
        return {
            "game": game,
            "round": int(h["Round"]),
            "board": int(h["Board"]),
            "wfide": h["WhiteFideId"],
            "bfide": h["BlackFideId"],
            "wname": h["White"],
            "bname": h["Black"],
            "wteam": normalize_team(h["WhiteTeam"]),
            "bteam": normalize_team(h["BlackTeam"]),
            "result": h["Result"],
            "welo": h.get("WhiteElo"),
            "belo": h.get("BlackElo"),
            "wtitle": h.get("WhiteTitle"),
            "btitle": h.get("BlackTitle"),
            "nodes": nodes,
            "plies": len(nodes),
        }

    records = [rec(g) for g in games]
    # The source (rounds 1-2) carries a handful of stray game line-items. Two
    # kinds, each removed against the crosstable, which is authoritative for
    # both the round pairings and every team's board points:
    #
    #   Filter A -- wrong pairing: some strays cross-pair players from two teams
    #   that never actually met that round. Drop any game whose two teams are
    #   not a crosstable pairing for its round.
    #
    #   Filter B -- duplicate board within the correct pairing: some strays
    #   repeat a real match's players on extra boards, so a player ends up with
    #   two games in one round. Keep one game per player per round (higher
    #   board = the complete match block).
    #
    # The per-team, per-round board-point audit below is the real validator and
    # rejects the build if either filter left the wrong games.
    legit_pairs: dict[int, set[frozenset]] = defaultdict(set)
    for rank, row in crosstable.items():
        for rnd, (opp, _c, _p) in row.rounds.items():
            legit_pairs[rnd].add(frozenset((rank, opp)))

    wrong_pairing = 0
    paired: list[dict] = []
    for r in records:
        wr = rank_by_norm.get(r["wteam"])
        br = rank_by_norm.get(r["bteam"])
        if wr is not None and br is not None and frozenset((wr, br)) in legit_pairs[r["round"]]:
            paired.append(r)
        else:
            wrong_pairing += 1

    by_round: dict[int, list[dict]] = defaultdict(list)
    for r in paired:
        by_round[r["round"]].append(r)
    kept: list[dict] = []
    intra_dups = 0
    for rnd, rs in sorted(by_round.items()):
        used: set[str] = set()
        for r in sorted(rs, key=lambda x: x["board"], reverse=True):
            if r["wfide"] in used or r["bfide"] in used:
                intra_dups += 1
                continue
            used.add(r["wfide"])
            used.add(r["bfide"])
            kept.append(r)
    records = kept
    duplicates = wrong_pairing + intra_dups

    # ---- 2. Participants (unique by FIDE id) + player -> team.
    players: dict[str, PlayerAgg] = {}
    player_team: dict[str, str] = {}
    for r in records:
        for fide, name, elo, title, team in (
            (r["wfide"], r["wname"], r["welo"], r["wtitle"], r["wteam"]),
            (r["bfide"], r["bname"], r["belo"], r["btitle"], r["bteam"]),
        ):
            if fide not in players:
                players[fide] = PlayerAgg(fide, name, title or None,
                                          int(elo) if elo and elo.isdigit() else None, team)
            else:
                p = players[fide]
                if p.title is None and title:
                    p.title = title
                if p.rating is None and elo and elo.isdigit():
                    p.rating = int(elo)
            player_team.setdefault(fide, team)
            if player_team[fide] != team:
                raise ValueError(f"Player {name} ({fide}) appears on two teams: "
                                 f"{player_team[fide]!r} and {team!r}")

    # ---- 3. Teams: match PGN team names to crosstable ranks.
    team_norms = {r["wteam"] for r in records} | {r["bteam"] for r in records}
    if len(team_norms) != 48:
        raise ValueError(f"Expected 48 teams in PGN, got {len(team_norms)}")
    unmatched = [t for t in team_norms if t not in rank_by_norm]
    if unmatched:
        raise ValueError(f"PGN teams not found in crosstable: {unmatched}\n"
                         f"crosstable keys: {sorted(rank_by_norm)}")
    rank_to_norm = {rank_by_norm[t]: t for t in team_norms}
    team_id: dict[str, str] = {}
    used_slugs: set[str] = set()
    for norm in team_norms:
        rank = rank_by_norm[norm]
        base = slug(crosstable[rank].name)
        s = base
        n = 2
        while s in used_slugs:
            s, n = f"{base}-{n}", n + 1
        used_slugs.add(s)
        team_id[norm] = f"t-{s}"

    # ---- 4. Team matches: group by (round, team pair); derive intra-board 1..k.
    match_games: dict[tuple[int, frozenset], list[dict]] = defaultdict(list)
    for r in records:
        match_games[(r["round"], frozenset((r["wteam"], r["bteam"])))].append(r)

    # Assign a stable game id and intra-match board number.
    game_ids: dict[int, str] = {}  # id(record) -> game id
    intra_board: dict[int, int] = {}
    seen_ids: set[str] = set()
    for (rnd, _pair), rs in match_games.items():
        for local, r in enumerate(sorted(rs, key=lambda x: x["board"]), start=1):
            intra_board[id(r)] = local
        for r in rs:
            base = f"g-r{r['round']}-b{r['board']}"
            gid = base
            k = 2
            while gid in seen_ids:
                gid = f"{base}-{k}"
                k += 1
            seen_ids.add(gid)
            game_ids[id(r)] = gid

    # Board-order per player: modal intra-match board.
    board_counts: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        board_counts[r["wfide"]][intra_board[id(r)]] += 1
        board_counts[r["bfide"]][intra_board[id(r)]] += 1

    # ---- 5. Accumulate team match points + game points; audit vs crosstable.
    team_match_points: Counter = Counter()
    team_game_points: dict[str, float] = defaultdict(float)
    team_matches_out: list[dict] = []
    for (rnd, _pair), rs in sorted(match_games.items(), key=lambda kv: (kv[0][0],)):
        teams = list({r["wteam"] for r in rs} | {r["bteam"] for r in rs})
        if len(teams) != 2:
            raise ValueError(f"Round {rnd} match does not have exactly two teams: {teams}")
        # home = team with White on the top intra-board.
        top = min(rs, key=lambda x: intra_board[id(x)])
        home = top["wteam"]
        away = top["bteam"]
        score = {home: 0.0, away: 0.0}
        for r in rs:
            score[r["wteam"]] += points_for(r["result"], "white")
            score[r["bteam"]] += points_for(r["result"], "black")
        for t in (home, away):
            team_game_points[t] += score[t]
        if score[home] > score[away]:
            mp_home, mp_away = 2, 0
        elif score[home] < score[away]:
            mp_home, mp_away = 0, 2
        else:
            mp_home, mp_away = 1, 1
        team_match_points[home] += mp_home
        team_match_points[away] += mp_away
        # audit: opponent + board points against the crosstable
        hr, ar = rank_by_norm[home], rank_by_norm[away]
        for t, opp, sc in ((home, ar, score[home]), (away, hr, score[away])):
            opp_rank, _color, board_pts = crosstable[rank_by_norm[t]].rounds[rnd]
            if opp_rank != opp:
                raise ValueError(f"Round {rnd}: {crosstable[rank_by_norm[t]].name} opponent "
                                 f"{opp_rank} (crosstable) != {opp} (games)")
            if abs(board_pts - sc) > 1e-9:
                raise ValueError(f"Round {rnd}: {crosstable[rank_by_norm[t]].name} board points "
                                 f"{board_pts} (crosstable) != {sc} (games)")
        team_matches_out.append({
            "round": rnd, "home": home, "away": away,
            "home_score": score[home], "away_score": score[away],
            "mp_home": mp_home, "mp_away": mp_away,
            "boards": sorted(rs, key=lambda x: intra_board[id(x)]),
        })

    for norm in team_norms:
        row = crosstable[rank_by_norm[norm]]
        if abs(team_match_points[norm] - row.match_points) > 1e-9:
            raise ValueError(f"{row.name}: match points {team_match_points[norm]} != crosstable {row.match_points}")
        if abs(team_game_points[norm] - row.game_points) > 1e-9:
            raise ValueError(f"{row.name}: game points {team_game_points[norm]} != crosstable {row.game_points}")

    # ---- 6. Emit.
    eco_entries = load_eco(ECO_TABLE)
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-cr-1435703"})
    header = child(root, "header")
    child(header, "name", "FIDE World Team Rapid Chess Championship 2026")
    eref = child(header, "eventRef", ref="event:20260617-20260619-fide-world-team-rapid-chess-championship-2026", source=SITE_SOURCE)
    child(eref, "name", "FIDE World Team Rapid Chess Championship 2026")
    child(header, "eventType", "team")
    child(header, "eventType", "swiss")
    child(header, "cadence", "rapid")
    child(header, "federation", "FID")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=6, d=17, iso="2026-06-17")
    child(child(dates, "end"), "day", y=2026, m=6, d=19, iso="2026-06-19")
    place = child(header, "placeRef", ref="place:city:HKG-hong-kong", kind="city")
    child(place, "name", "Hong Kong")
    child(place, "country", "HKG")
    child(place, "city", "Hong Kong")
    child(header, "venue", "Queen Elizabeth Stadium, Wan Chai")
    organizers = child(header, "organizers")
    for org in ("Hong Kong China Chess Federation Limited", "FIDE"):
        o = child(organizers, "organizer")
        child(o, "name", org)
        child(o, "role", "organizer")
    arbiters = child(header, "arbiters")
    for name, role, fid in ARBITERS:
        a = child(arbiters, "arbiter")
        child(a, "name", name)
        child(a, "role", role)
        ids = child(a, "ids")
        child(ids, "fideId", fid)

    # The broadcast PGN carries no federation; resolve it by FIDE id. Teams here
    # are clubs, not nations, so a player's federation cannot come from the team.
    feds = fide_federations({p.fide_id for p in players.values()})

    # participants (sorted by rating desc then name for readability)
    participants = child(root, "participants")
    for p in sorted(players.values(), key=lambda x: (-(x.rating or 0), x.display)):
        participant = child(participants, "participant", id=p.participant_id)
        pref = child(participant, "playerRef", ref=f"player:fide:{p.fide_id}", source=SITE_SOURCE)
        name = child(pref, "name", display=p.display)
        if "," in p.display:
            fam, giv = (s.strip() for s in p.display.split(",", 1))
            child(name, "family", fam)
            if giv:
                child(name, "given", giv)
        else:
            child(name, "unstructured", p.display)
        if p.fide_id in feds:
            child(pref, "federation", feds[p.fide_id])
        if p.title:
            child(pref, "title", p.title)
        ids = child(pref, "ids")
        child(ids, "fideId", p.fide_id)
        child(pref, "resolution", method="fide-id", resolver=RESOLVER)
        if p.rating is not None:
            snap = child(participant, "ratingSnapshot", system="fide", scope="rapid")
            child(snap, "value", p.rating)
            child(child(snap, "asOf"), "month", y=2026, m=6, raw="event rapid rating in source PGN")
            child(snap, "publishedForEvent", "false")

    # teams
    teams_el = child(root, "teams")
    for norm in sorted(team_norms, key=lambda n: rank_by_norm[n]):
        row = crosstable[rank_by_norm[norm]]
        team = child(teams_el, "team", id=team_id[norm], ref=f"team:{team_id[norm][2:]}")
        child(team, "name", row.name)
        roster = child(team, "roster")
        members = [fide for fide, t in player_team.items() if t == norm]
        members.sort(key=lambda fide: (min(board_counts[fide], key=lambda b: (-board_counts[fide][b], b)),
                                       -(players[fide].rating or 0)))
        for fide in members:
            board_order = min(board_counts[fide], key=lambda b: (-board_counts[fide][b], b))
            child(roster, "member", participant=players[fide].participant_id, boardOrder=board_order)
        standing = child(team, "standing")
        child(standing, "rank", row.rank)
        child(standing, "matchPoints", int(row.match_points) if row.match_points.is_integer() else row.match_points)
        child(standing, "gamePoints", int(row.game_points) if row.game_points.is_integer() else row.game_points)
        tbs = child(standing, "tiebreaks")
        child(tbs, "tiebreak", name="olympiad-sonneborn-berger", value=row.tb2)
        child(tbs, "tiebreak", name="olympiad-adjusted-matchpoints", value=row.tb4)

    # games
    games_el = child(root, "games")
    clocked = 0
    eco_count = 0
    forfeits = 0
    for r in sorted(records, key=lambda x: (x["round"], x["board"])):
        g = r["game"]
        nodes = r["nodes"]
        ge = child(games_el, "game", id=game_ids[id(r)], round=str(r["round"]), board=str(r["board"]),
                   white=players[r["wfide"]].participant_id, black=players[r["bfide"]].participant_id,
                   whiteTeam=team_id[r["wteam"]], blackTeam=team_id[r["bteam"]], result=r["result"])
        uci = tuple(n.move.uci() for n in nodes)
        if uci:
            eco = classify_eco(uci, eco_entries)
            if eco:
                child(ge, "eco", eco)
                eco_count += 1
        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence="rapid")
        child(tc, "raw", g.headers.get("TimeControl", "900+10"))
        child(tc, "initialSeconds", 900)
        child(tc, "incrementSeconds", 10)
        if nodes:
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
            trajectory, final_pos = fingerprints(g)
            fps = child(ge, "fingerprints")
            child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=trajectory)
            child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final_pos)
        else:
            forfeits += 1
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", SITE_SOURCE)
        child(src, "note", f"Global board {r['board']}, intra-match board {intra_board[id(r)]}. "
                           f"EndDate={g.headers.get('EndDate','')}; EndTime={g.headers.get('EndTime','')}.")

    # team matches
    tms = child(root, "teamMatches")
    for m in team_matches_out:
        tm = child(tms, "teamMatch", round=str(m["round"]), home=team_id[m["home"]], away=team_id[m["away"]])
        child(tm, "homeScore", int(m["home_score"]) if float(m["home_score"]).is_integer() else m["home_score"])
        child(tm, "awayScore", int(m["away_score"]) if float(m["away_score"]).is_integer() else m["away_score"])
        child(tm, "matchPoints", home=m["mp_home"], away=m["mp_away"])
        boards = child(tm, "boards")
        for r in m["boards"]:
            child(boards, "board", number=intra_board[id(r)], game=game_ids[id(r)])

    champion = crosstable[1].name
    notes = (
        f"World Team Rapid Championship: a 12-round Swiss for teams (48 teams, six boards, rapid 15+10) at "
        f"Queen Elizabeth Stadium, Wan Chai, Hong Kong, 17-19 June 2026. Champion: {champion} (rank 1). "
        "The separately-run World Team BLITZ Championship (pools + knockout, 20-21 June) is a different event "
        "and is not in this file. Primary source: the chess.com broadcast PGN (per-move clocks, FIDE ids, teams); "
        "team standings and the completeness proof come from the ChessResults Final Ranking crosstable. "
        f"The builder recomputed every team's match points and game points from the games and every round's "
        f"pairing, and they reproduce the crosstable exactly for all 48 teams. {duplicates} duplicate game "
        "line-items (rounds 1-2) were collapsed by (round, player-pair), keeping the copy with movetext. "
        f"{forfeits} result-only games (no movetext in the source) are stored without moves. "
        "Boards use the source's global per-round numbering; the intra-match board (1-6) is given on each "
        "teamMatch board and in each game's source note. Player federations are not in the source PGN, so none "
        "are asserted. Uses the CTML 2.1 team model (ctml:teams, ctml:teamMatches, game/@whiteTeam/@blackTeam)."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note", f"{len(games)} game line-items ({len(records)} after de-duplication); SHA-256 {sha256(SOURCE_PGN)}. "
                      "Moves, clocks, and results authoritative.")
    s2 = child(root, "source", kind="chess-results-crosstable")
    child(s2, "uri", CROSSTABLE.as_uri())
    child(s2, "note", f"Final Ranking crosstable after 12 rounds (tnr 1435703); SHA-256 {sha256(CROSSTABLE)}. "
                      "Team standings, tiebreaks, and per-round pairings; completeness proof.")
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
    with OUTPUT.open("ab") as stream:
        stream.write(b"\n")

    return {
        "output": str(OUTPUT), "teams": len(team_norms), "participants": len(players),
        "games_in": len(games), "games_out": len(records), "duplicates_collapsed": duplicates,
        "team_matches": len(team_matches_out), "forfeits": forfeits, "clocked_plies": clocked,
        "eco_games": eco_count, "champion": champion, "bytes": OUTPUT.stat().st_size,
        "sha256": sha256(OUTPUT),
    }


if __name__ == "__main__":
    for key, value in build().items():
        print(f"{key}={value}")
