#!/usr/bin/env python3
"""Zobrist-based game fingerprinting for CTML's <ctml:fingerprint> elements
(xsd/ctml-analysis.xsd).

Uses chess.polyglot.zobrist_hash() -- python-chess's implementation of the
standard Polyglot Zobrist scheme -- rather than a hand-rolled one, per the
reasoning already recorded in docs/HANDOFF.md: a from-scratch Zobrist
implementation's most common bugs are around castling rights and en passant
state, and this is a widely-used, already-correct implementation instead of
a new place for that exact bug to reappear.

scheme = "zobrist-polyglot-1" for both fingerprint scopes below. What the
value bytes mean is scope-dependent, and pinned here precisely because the
whole point of the schema's explicit `scheme` token is to let a future
change coexist with old fingerprints instead of forcing a silent
reinterpretation of what a stored value means:

- scope="finalPosition": the raw 8-byte big-endian encoding of
  chess.polyglot.zobrist_hash() for the final position, hex-encoded (16 hex
  chars). Deliberately NOT run through a second hash layer -- a future
  position-search index wants the raw Zobrist value directly, since that's
  the whole point of using a scheme designed for fast incremental/positional
  comparison. Two unrelated games that happen to reach the same final
  position (short draws, common endgames) WILL collide here on purpose;
  this scope must never be used for dedup, only the trajectory scope below.
- scope="trajectory": SHA-256 (32 bytes, 64 hex chars) over the
  concatenation of the 8-byte big-endian Zobrist hash of every position in
  the game, in order, INCLUDING the starting position at ply 0 (so two
  games with identical move sequences but different starting FENs still
  get different trajectory fingerprints, not just an edge case worth
  ignoring). This is the actual identity/dedup key.

Moves are consumed as python-chess Move objects (or UCI strings via the
convenience wrapper), not SAN text, so the fingerprint is invariant to
whatever notation a given CTML document happens to store
(moves/@notation="uci"|"san"|"lan"|"pgn") -- it depends only on the
resulting position sequence.
"""

from __future__ import annotations

import hashlib
import struct

import chess
import chess.polyglot

SCHEME = "zobrist-polyglot-1"


def zobrist_bytes(board: chess.Board) -> bytes:
    return struct.pack(">Q", chess.polyglot.zobrist_hash(board))


class FingerprintAccumulator:
    """Feed it the board after every ply (starting with the pre-move
    starting position) and it accumulates both fingerprints in one pass --
    meant to piggyback on a move-walk a caller is already doing (e.g. to
    extract UCI moves), so fingerprinting costs nothing extra at import
    time rather than requiring a second replay later."""

    def __init__(self, start_board: chess.Board) -> None:
        self._hasher = hashlib.sha256()
        self._final = b""
        self.update(start_board)

    def update(self, board: chess.Board) -> None:
        zb = zobrist_bytes(board)
        self._hasher.update(zb)
        self._final = zb

    def result(self) -> tuple[str, str]:
        """Returns (trajectory_hex, final_position_hex)."""
        return self._hasher.hexdigest(), self._final.hex()


def compute_fingerprints(uci_moves: list[str], start_fen: str | None = None) -> tuple[str, str]:
    """Standalone convenience: given a full UCI move list (as CTML stores
    them) and an optional starting FEN (standard chess start if omitted),
    replays the game and returns (trajectory_hex, final_position_hex). For
    reuse where the caller isn't already walking the game with
    FingerprintAccumulator (tests, a future backfill pass over existing
    CTML files)."""
    board = chess.Board(start_fen) if start_fen else chess.Board()
    acc = FingerprintAccumulator(board)
    for uci in uci_moves:
        board.push(chess.Move.from_uci(uci))
        acc.update(board)
    return acc.result()


def fingerprints_xml(trajectory_hex: str, final_position_hex: str, indent: str = "    ") -> str:
    lines = [
        f"{indent}<ctml:fingerprints>",
        f'{indent}  <ctml:fingerprint scheme="{SCHEME}" scope="trajectory" value="{trajectory_hex}"/>',
        f'{indent}  <ctml:fingerprint scheme="{SCHEME}" scope="finalPosition" value="{final_position_hex}"/>',
        f"{indent}</ctml:fingerprints>",
    ]
    return "\n".join(lines)
