# Consolidation decisions

The user selected `D:\dev\proj\ctml` as the canonical project. Both that
project's `xsd/` folder and `ctml-clean/xsd/` were empty at inspection. The latest
schemas were physically in `D:\ctml/xsd`, with changes through 4 September.
Those modules were imported verbatim. No schema semantics were changed during
the consolidation.

## Preserved imports

- Current `D:\ctml`: all 24 curated tournaments, 13 schemas, event builders,
  shared helpers, web viewer, index and small source inputs. This includes the
  untracked 1894 tournament. Its filename has an apparent year typo, preserved
  for separate review rather than silently renamed during migration.
- Current `ctml-clean` working tree: all Rust source, Cargo files, Python
  scripts, specification/test vectors, ECO data, crosstables, August SSP and
  251 player/place/event registry XML files. Uncommitted edits are included.
- Existing canonical-root Python readers, samples, tests and older reference
  material stay in place. Historical handoffs are preserved under `docs/history`.

Every copied file was SHA-256 checked against the source. Conflicting destination
content would stop the import. The source trees remain available for their Git
history and recovery. They are no longer the intended editing homes for the
consolidated CTML code, schemas or corpus.

## Local adaptations

The dated builders write relative to this project and use its ECO table. The
preserved 1886 intermediate PGN is under `sources/curated`. The shared FIDE
federation cache writes under `build/cache`. Imported Python scripts use this
project for outputs; the enriched-SSP tool consults Elysium's existing archives.
Rust commands default to this root and its `registry/`, `crosstables/`,
`assets/all.tsv` and explicit August SSP input. Explicit legacy assets-directory
arguments remain supported. Adapted file hashes and reasons are recorded in
`provenance/consolidation-adjustments.json`.

## What this establishes

One schema authority, one editing home, one corpus and registry baseline, restored
Python and Rust tooling, provenance and a repeatable validation entry point.
Git records code and curated artifacts; the large imported data remains local
and is identified by its import manifest hashes. No remote is configured or
published as part of this work.

Git preserves file bytes through `.gitattributes`, so Windows line-ending
conversion cannot invalidate the schema lock or import hashes on checkout.
Inherited whitespace in preserved source documents is retained.

The older event registry is a filtered source-derived baseline. Its expected
round/game targets are unpopulated. Importing it does not make every event
complete or resolve its relationship to the newer curated tournament records.
Likewise, current historical SSP curation has not been replaced with the older
player shards. This distinction is preserved in the source catalog.

## Verified consolidation baseline

Verification completed on 7 September 2026: all 370 imported files matched
their recorded hashes (including documented adaptations). All 279 XML documents
validated against the pinned schemas: 24 curated tournaments, 251 registry files
and four samples. The nine Python tests passed. Rust compiled successfully;
its fingerprint selftest passed ten vectors, move-generation selftest passed
27 cases and PGN selftest matched python-chess for all 65 games. `cargo test`
currently contains no unit tests.

These checks establish that the consolidated files are intact and the tested
tooling works. They do not establish the completeness or historical accuracy
of every source record.

## Next publication work

Elysium remains the source-observation and identity-resolution workbench.
Reconcile its schema and reviewed decisions with the current CTML structures,
including teams, stages, non-game outcomes, rating provenance and authored
metadata. Produce explicit CTML releases from vetted inputs; compile Minerva's
reference binary from a pinned release. The compiler must preserve the facts
needed by normalization, downloader gates, event identity, completeness and
other consumers, with unknown and conflicting evidence represented explicitly.

Do not re-run the old Elysium binary builders as a shortcut: they read old
SQLite inputs directly and share a default output filename despite different
formats. Do not run data acquisition or rewrite active SSP files merely to
complete a project migration.
