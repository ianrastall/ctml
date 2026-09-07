"""Shared helpers for the CTML corpus builders.

Importing this module registers the ``ctml`` namespace prefix, so every builder
serializes ``ctml:`` rather than ``ns0:``. Event-specific logic (rosters,
scoring conventions, crosstable parsing, the bracket/pool assembly) stays in the
individual dated builder scripts; only the format-agnostic plumbing lives here.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import chess
import chess.pgn
import chess.polyglot


CTML_NS = "urn:ctml:2.0"
ET.register_namespace("ctml", CTML_NS)

# Official FIDE standard rating list (XML): <player><fideid/><name/><country/>...
# The authoritative source for a player's federation when a source PGN carries a
# FIDE id but no federation, which is the norm for broadcast PGNs. The newest
# list present is used; federation changes are rare, so a list a few months after
# an event is still the right answer for that event's players.
#
# NOT D:\elysium\sources\fide2: those per-letter dumps carry truncated fideid
# values (Carlsen appears as 150, not 1503014), so they cannot be keyed by id.
FIDE_LIST_DIR = Path(r"D:\elysium\sources\fide\xml-originals")
FIDE_LIST_GLOB = "standard_*frl_xml.xml"
FIDE_FED_CACHE = Path(__file__).resolve().parents[1] / "build/cache/fide-federations.json"


def q(local: str) -> str:
    return f"{{{CTML_NS}}}{local}"


def child(parent: ET.Element, local: str, text: object | None = None, **attrs: object) -> ET.Element:
    element = ET.SubElement(parent, q(local), {k: str(v) for k, v in attrs.items()})
    if text is not None:
        element.text = str(text)
    return element


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_pgn(path: Path) -> list[chess.pgn.Game]:
    games: list[chess.pgn.Game] = []
    with path.open(encoding="utf-8-sig") as stream:
        while True:
            game = chess.pgn.read_game(stream)
            if game is None:
                break
            if game.errors:
                raise ValueError(f"PGN errors in game {len(games) + 1}: {game.errors}")
            games.append(game)
    return games


def load_eco(path: Path) -> list[tuple[tuple[str, ...], str]]:
    entries: list[tuple[tuple[str, ...], str]] = []
    with path.open(encoding="utf-8") as stream:
        header = stream.readline().rstrip("\n").split("\t")
        if header[:4] != ["eco", "name", "pgn", "uci"]:
            raise ValueError(f"Unexpected ECO header: {header!r}")
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 4 and fields[3].strip():
                entries.append((tuple(fields[3].split()), fields[0]))
    entries.sort(key=lambda item: len(item[0]), reverse=True)
    return entries


def classify_eco(uci_moves: tuple[str, ...], entries: list[tuple[tuple[str, ...], str]]) -> str | None:
    for prefix, code in entries:
        if len(prefix) <= len(uci_moves) and uci_moves[: len(prefix)] == prefix:
            return code
    return None


def zobrist_bytes(board: chess.Board) -> bytes:
    return struct.pack(">Q", chess.polyglot.zobrist_hash(board))


def fingerprints(game: chess.pgn.Game) -> tuple[str, str]:
    board = game.board()
    digest = hashlib.sha256()
    current = zobrist_bytes(board)
    digest.update(current)
    for move in game.mainline_moves():
        board.push(move)
        current = zobrist_bytes(board)
        digest.update(current)
    return digest.hexdigest(), current.hex()


def game_termination(game: chess.pgn.Game) -> str | None:
    board = game.end().board()
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    return None


def fide_rating_list(list_dir: Path = FIDE_LIST_DIR, pattern: str = FIDE_LIST_GLOB) -> Path:
    """Newest official FIDE standard rating list XML available."""
    lists = sorted(list_dir.glob(pattern))
    if not lists:
        raise FileNotFoundError(f"No FIDE rating list matching {pattern!r} in {list_dir}")
    return lists[-1]


def fide_federations(
    fide_ids: set[str] | list[str],
    list_path: Path | None = None,
    cache_path: Path = FIDE_FED_CACHE,
) -> dict[str, str]:
    """Resolve FIDE ids to 3-letter federation codes from the official rating list.

    Returns only ids actually found, so a caller can tell "no federation on
    record" from a bad lookup and decide whether to assert one. Results are
    cached in ``cache_path`` (keyed by list filename) because the list is ~160 MB;
    a scan only happens when an id is not already cached.
    """
    path = list_path or fide_rating_list()
    wanted = {str(i) for i in fide_ids}
    blob: dict[str, dict[str, str]] = {}
    if cache_path.exists():
        blob = json.loads(cache_path.read_text(encoding="utf-8"))
    cache = blob.setdefault(path.name, {})
    # "" is the cached marker for "scanned, absent from this list".
    missing = wanted - cache.keys()
    if missing:
        found: dict[str, str] = {}
        for _, element in ET.iterparse(path, events=("end",)):
            # Only <player> is cleared: clearing on every end event would wipe
            # <fideid>/<country> before their parent's end event arrives.
            if element.tag != "player":
                continue
            fid = element.findtext("fideid")
            if fid in missing:
                country = (element.findtext("country") or "").strip().upper()
                if re.fullmatch(r"[A-Z]{3}", country):
                    found[fid] = country
            element.clear()
            if len(found) == len(missing):
                break
        cache.update(found)
        cache.update({fid: "" for fid in missing - found.keys()})
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(blob, indent=0, sort_keys=True), encoding="utf-8")
    return {fid: cache[fid] for fid in wanted if cache.get(fid)}


def norm(name: str) -> str:
    """Casefolded, whitespace- and punctuation-normalized team/name key."""
    name = unicodedata.normalize("NFKC", name)
    for a, b in (("–", "-"), ("—", "-"), ("‘", "'"), ("’", "'"), ("`", "'")):
        name = name.replace(a, b)
    return re.sub(r"\s+", " ", name).strip().casefold()


def slug(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return name or "team"
