#!/usr/bin/env python
"""Build a trajectory-fingerprint index across chess game sources.

Computes the `zobrist-polyglot-1` trajectory hash (per
`spec/fingerprint.md`, byte-exact) for every game in each input source and
groups games by that hash. A bucket with more than one game is a set of
games that are almost certainly the same game -- the identity seed the
CTML dedup/identity cascade runs on: a trajectory match hands you two
paired player equivalences (white==white, black==black) for free, and
becomes strong evidence two event records are the same event.

Confidence scales with ply count: a long game matching is near-certain
identity, a very short game (miniature, stub, forfeit) can collide between
genuinely different games. Every bucket therefore carries `min_ply`, and
zero-ply games are counted but never treated as seeds.

Sources are given as `label=path`. `.pgn` files are read game-by-game;
`.jsonl` files are treated as rusbase-style records with an embedded
`pgn_data` string (one or more games) per line.

Outputs into --out-dir:
  fp-collisions.jsonl   one JSON object per trajectory bucket with >1 game
  fp-summary.json       run statistics

Usage:
  python fingerprint_index.py --out-dir out/fpindex \
      argbase=/d/elysium/sources/argbase.pgn \
      supercuba=/d/elysium/sources/supercuba.pgn
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import struct
import sys
import time

import chess
import chess.pgn
import chess.polyglot


def zobrist_bytes(board):
    return struct.pack(">Q", chess.polyglot.zobrist_hash(board))


def fingerprints_from_board_walk(game):
    """Return (trajectory_hex, final_hex, ply_count) for one parsed game.

    Walks the mainline exactly as spec/fingerprint.md's reference algorithm
    does: hash p0 (start, respecting any SetUp/FEN header), then each
    position after each mainline move.
    """
    board = game.board()  # respects FEN header if present
    h = hashlib.sha256()
    zb = zobrist_bytes(board)
    h.update(zb)
    ply = 0
    for move in game.mainline_moves():
        board.push(move)
        zb = zobrist_bytes(board)
        h.update(zb)
        ply += 1
    return h.hexdigest(), zb.hex(), ply


# ---- self-test against spec/fingerprint.md vectors -------------------------

SPEC_VECTORS = [
    ([], "89dffe8f5406d6237e8428fc8a89912f97175c7b72d3282aa4216868247cfbae",
         "463b96181691fc9c"),
    (["e2e4"], "f4caae704c166a7e30380f4ade3a580e72218d6429885435fed80e08fdede0d1",
         "823c9b50fd114196"),
    (["e2e4", "e7e5"], "64ad2d50921adf0d62d0dfdf76b5625379628b34e60a86e40e5605fac6742fab",
         "0844931a6ef4b9a0"),
    (["c2c4", "e7e5", "b1c3"], "bc9bc142c93f4c34ce5097cd09315da7e20173ef3aa052e2267b4908be818d3b",
         "bbf719d404992d74"),
    (["b1c3", "e7e5", "c2c4"], "e386f8ee8bbf00145718d9b07d3276f3445e91db6771c6f8712460a2280695c4",
         "bbf719d404992d74"),
    (["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"],
         "1b94d3d89d69e839e0338b20c40e71b461ac347d501ebe929408ab2a90037129",
         "c3116e611017a62f"),
    (["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "e1g1"],
         "968f46864648853b6831143e240bdf3718e2d55b0ae5b287941a3e22125eae5a",
         "c8162c4989019aab"),
    (["a2a4", "h7h5", "a4a5", "h5h4", "a5a6", "h4h3", "a6b7", "h3g2", "b7a8q", "g2h1q"],
         "f2080091bc06fa8692cdd55a15d5035f286e6f70d23fbf9643638962b40701b9",
         "e23dff4ec4e43cdd"),
]


def _fingerprints_from_uci(uci_moves):
    board = chess.Board()
    h = hashlib.sha256()
    zb = zobrist_bytes(board)
    h.update(zb)
    for uci in uci_moves:
        board.push(chess.Move.from_uci(uci))
        zb = zobrist_bytes(board)
        h.update(zb)
    return h.hexdigest(), zb.hex()


def self_test():
    for moves, want_traj, want_final in SPEC_VECTORS:
        traj, final = _fingerprints_from_uci(moves)
        if traj != want_traj or final != want_final:
            raise SystemExit(
                "SELF-TEST FAILED for %r\n  traj  got %s want %s\n  final got %s want %s"
                % (moves, traj, want_traj, final, want_final))
    print("self-test: %d/%d spec vectors match" % (len(SPEC_VECTORS), len(SPEC_VECTORS)))


# ---- occurrence record -----------------------------------------------------

def occ(source, game, ply):
    hdr = game.headers
    return {
        "src": source,
        "event": hdr.get("Event", ""),
        "site": hdr.get("Site", ""),
        "date": hdr.get("Date", ""),
        "round": hdr.get("Round", ""),
        "white": hdr.get("White", ""),
        "black": hdr.get("Black", ""),
        "result": hdr.get("Result", ""),
        "ply": ply,
    }


def iter_games_pgn(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        while True:
            try:
                game = chess.pgn.read_game(fh)
            except Exception:
                # a malformed game block; skip to next
                continue
            if game is None:
                return
            yield game


def iter_games_jsonl(path):
    """rusbase-style: each line has a `pgn_data` string with 1+ games."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            pgn_text = rec.get("pgn_data") or ""
            if not pgn_text:
                continue
            handle = io.StringIO(pgn_text)
            while True:
                try:
                    game = chess.pgn.read_game(handle)
                except Exception:
                    break
                if game is None:
                    break
                yield game


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", metavar="label=path")
    ap.add_argument("--out-dir", default="out/fpindex")
    ap.add_argument("--max-games-per-source", type=int, default=0,
                    help="0 = no cap")
    ap.add_argument("--seed-min-ply", type=int, default=8,
                    help="buckets whose shortest game has fewer plies than this "
                         "are flagged low-confidence (default 8)")
    ap.add_argument("--self-test-only", action="store_true")
    args = ap.parse_args()

    self_test()
    if args.self_test_only:
        return

    if not args.sources:
        ap.error("no sources given (label=path ...)")

    os.makedirs(args.out_dir, exist_ok=True)

    index = {}  # trajectory_hex -> list[occ]
    per_source = {}
    errors = {}
    t0 = time.time()

    for spec in args.sources:
        if "=" not in spec:
            ap.error("source must be label=path, got %r" % spec)
        label, path = spec.split("=", 1)
        if not os.path.exists(path):
            ap.error("no such file: %s" % path)
        it = iter_games_jsonl(path) if path.lower().endswith(".jsonl") else iter_games_pgn(path)
        n = 0
        err = 0
        t_src = time.time()
        for game in it:
            try:
                traj, _final, ply = fingerprints_from_board_walk(game)
            except Exception:
                err += 1
                continue
            index.setdefault(traj, []).append(occ(label, game, ply))
            n += 1
            if args.max_games_per_source and n >= args.max_games_per_source:
                break
            if n % 20000 == 0:
                print("  [%s] %d games (%.0f g/s)"
                      % (label, n, n / max(1e-9, time.time() - t_src)), flush=True)
        per_source[label] = n
        errors[label] = err
        print("%-12s %8d games  %6d parse-skips  %.1fs"
              % (label, n, err, time.time() - t_src), flush=True)

    # analyze buckets
    total_games = sum(per_source.values())
    collisions = []
    n_cross = 0
    n_within = 0
    zero_ply_bucketed = 0
    for traj, occs in index.items():
        if len(occs) < 2:
            continue
        srcs = sorted({o["src"] for o in occs})
        min_ply = min(o["ply"] for o in occs)
        if min_ply == 0:
            zero_ply_bucketed += 1
        cross = len(srcs) > 1
        if cross:
            n_cross += 1
        else:
            n_within += 1
        collisions.append({
            "trajectory": traj,
            "count": len(occs),
            "sources": srcs,
            "cross_source": cross,
            "min_ply": min_ply,
            "low_confidence": min_ply < args.seed_min_ply,
            "occurrences": occs,
        })

    # write collisions sorted: cross-source first, then by count, then ply desc
    collisions.sort(key=lambda b: (not b["cross_source"], -b["count"], -b["min_ply"]))
    coll_path = os.path.join(args.out_dir, "fp-collisions.jsonl")
    with open(coll_path, "w", encoding="utf-8") as fh:
        for b in collisions:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")

    unique = len(index)
    summary = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scheme": "zobrist-polyglot-1",
        "sources": per_source,
        "parse_skips": errors,
        "total_games": total_games,
        "unique_trajectories": unique,
        "collision_buckets": len(collisions),
        "cross_source_buckets": n_cross,
        "within_source_buckets": n_within,
        "zero_ply_buckets": zero_ply_bucketed,
        "seed_min_ply": args.seed_min_ply,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(args.out_dir, "fp-summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print("\n=== summary ===")
    print(json.dumps(summary, indent=2))
    print("\ncollisions written to", coll_path)
    # show a few cross-source seeds
    shown = 0
    for b in collisions:
        if b["cross_source"] and not b["low_confidence"]:
            print("\nCROSS-SOURCE seed  count=%d  min_ply=%d  sources=%s"
                  % (b["count"], b["min_ply"], b["sources"]))
            for o in b["occurrences"][:6]:
                print("   [%s] %s | %s %s | %s vs %s | R%s | %sply | %s"
                      % (o["src"], o["date"], o["event"], o["site"],
                         o["white"], o["black"], o["round"], o["ply"], o["result"]))
            shown += 1
            if shown >= 8:
                break
    if shown == 0:
        print("\n(no high-confidence cross-source seeds in this run)")


if __name__ == "__main__":
    main()
