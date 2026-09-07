# CTML Corpus Policy

The CTML *schemas* describe any chess tournament. This document defines what
is admitted into **this** registry set — the curated, published corpus. The
schema is universal; the corpus is curated. Scope restrictions live here,
never in the schema.

## Scope

- **Era:** the 20th century first (with the 19th-century Edo-rated canon
  already represented). The 21st century is a future corpus, not a redesign.
- **Play:** over-the-board play. Correspondence archives are out of scope.

## The rating-floor ratchet

The registry grows only through vetted events, bootstrapped from strength:

1. **Seed:** the player registry is seeded from authoritative rating sources
   (FIDE lists, Edo historical ratings) at or above the floor.
2. **Admission:** a tournament is admitted to the corpus only when every
   participant resolves — via the resolution cascade recorded in each
   document's `resolution` elements — to an event-time rating at or above the
   floor. Missing or unrated participants fail the automatic gate until manual
   curation supplies a defensible rating. (Primary tooling gate:
   `--min-player-elo 2000`; average-rating gates are diagnostic only.)
3. **Ratchet:** unresolved participants in *admitted* tournaments become
   registry **candidates**. A candidate is promoted once identity is
   confirmed (external id, unique alias chain, or manual curation).
4. Tournaments are admitted or excluded **whole**. Never publish a partial
   tournament: completeness is the point.

Current floor: **2000** (Elo, Edo, Chessmetrics, or combined), evaluated per
event/player row. If any participant in any source roster or game is below
2000, or has no usable rating, the tournament is excluded whole. The floor is
a corpus parameter, not a format feature.

## Identity rules

- Published player records carry stable refs (`player:fide:`, `player:edo:`,
  `player:syn:`) and never change ref once published; corrections merge via
  aliases, not ref rewrites.
- Every tournament in the corpus references one canonical event occurrence
  in the event registry. Duplicate detection across source databases is an
  identity lookup against that registry, not a fuzzy match.

## Source data

Raw source databases (TWIC, Mega Database, Lumbras, chess.com dumps) are
**not** republished here — for size and license reasons they stay in local
staging. What this repository publishes is the curated work product:
registries and, eventually, vetted CTML tournament documents.

The full FIDE-derived player registry (~1 GB sharded XML) also stays in
local staging; the published player registry is the curated subset that the
corpus actually references.
