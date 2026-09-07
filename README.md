# CTML

**Canonical project: `D:\dev\proj\ctml`.** This is the maintained home for the
CTML schemas, curated tournament corpus, player/place/event registries, viewer,
and Python/Rust tooling. Start here for future CTML work.

The project was consolidated on 6 September 2026 from the current working files
in `D:\ctml` and `D:\dev\proj\ctml-clean`, preserving their original copies.
The import includes uncommitted code and the untracked 1894 tournament; it is
not limited to the last Git commits. `provenance/consolidation-import.json`
records every imported file, its source and SHA-256. Adaptations are recorded
separately in `provenance/consolidation-adjustments.json`.

## Start here

Python requires the packages in `requirements.txt`. The existing local Python
runtime already supplies these dependencies. From this folder:

```powershell
python tools/ctml_project.py status
python tools/ctml_project.py validate --samples
python tools/ctml_project.py validate --registries --samples
python -m unittest discover -s tests
cargo test --locked
cargo run --locked -- fingerprint-selftest
cargo run --locked -- movegen-selftest
cargo run --locked -- pgn-selftest
```

The Rust checks live in the executable selftests; `cargo test` currently has no
unit tests. The selftests exercise fingerprint vectors, legal move generation
and PGN processing against python-chess.

`validate` checks the pinned schema bundle and the curated tournaments. The
`--registries` option streams through all registry shards (about 5 GB). It
checks XML/schema validity; source accuracy, identity reconciliation and
tournament completeness require additional evidence. Reports go under
`build/validation/`; canonical data is never rewritten by validation.

`python tools/ctml_project.py verify-import` checks imported bytes against the
consolidation baseline, accounting for documented path adaptations. It is a
migration check, not a rule forbidding future reviewed edits. Update provenance
when deliberately refreshing imported data.

## Layout

| Path | Responsibility |
|---|---|
| `xsd/` | The single maintained schema set; umbrella `ctml.xsd`. |
| `tours/` | Curated tournament documents, including partial evidence where documented. |
| `registry/players`, `registry/places`, `registry/events.xml` | Imported CTML reference registries. See the data status below before treating every record as reviewed. |
| `tools/` | Curated event builders, corpus-index builder and project validation. |
| `src/`, `Cargo.toml` | Consolidated Rust tooling, retaining the `ctml-clean` executable name. |
| `readers/`, `scripts/` | Source adapters, registry generation and general Python import tools. |
| `assets/all.tsv` | Local opening reference used by the builders. |
| `crosstables/crosstables.json` | Consolidated source crosstables, with source-side provenance. |
| `inputs/ratings260801.ssp` | Explicit August SSP baseline used by the imported player registry tooling. |
| `sources/` | Small preserved corpus source inputs. Large raw archives remain catalogued in Elysium. |
| `app/`, `index.html`, `corpus.json` | Local corpus viewer and its generated index. |
| `provenance/` | Source catalog, schema lock, import lineage and adjustment hashes. |
| `docs/CONSOLIDATION.md` | Ownership, import decisions, limitations and next work. |
| `docs/history/`, `reference/` | Historical documentation and earlier implementations; not current instructions. |
| `build/`, `out/`, `target/` | Regenerated reports, temporary outputs and compiled code. |

To view the corpus locally, run `python -m http.server 8765` here and open
`http://localhost:8765`. To refresh its index after an intentional tournament
edit, run `python tools/build_corpus_index.py`.

Event builders now find this project relative to their own location. Their
source evidence can remain under `D:\elysium` and `D:\dev\pgn`; those paths
are not new output destinations. Several builders contain reviewed,
event-specific facts. Do not replace them with generic estimates.

## Schema authority

`provenance/schema-lock.json` pins all 13 imported modules, including the recent
September additions. The documents use CTML 2.0/2.1 in namespace `urn:ctml:2.0`;
the namespace alone does not identify the schema revision. An intentional
schema change requires updating the lock, validating the corpus and registries,
and documenting how consumers handle it. Older copies and bundled schemas must
be synchronized deliberately, not selected by their timestamps.

## Data status and Elysium

Consolidation establishes one maintained project and a verified import baseline.
It does **not** assert that every historical identity or source disagreement has
been resolved. In particular:

- The imported registries are the older CTML baseline from `ctml-clean`.
  The actively curated historical source at `D:\elysium\db\historical_ssp` and
  the assembled SSP at `D:\dev\proj\ssp` remain managed upstream. Their newer
  decisions must be reconciled explicitly before replacing historical records.
- `D:\elysium\sources\fide2` contains damaged IDs; Carlsen's `150` versus the
  official `1503014` was verified. It is not an ID-safe import source as-is.
- Raw archives and large source databases stay in their existing locations,
  described by `provenance/sources.json`. No source archive was culled or moved.
- Existing Elysium binaries are earlier experiments. A current-schema
  CTML-release-to-Minerva binary compiler remains the next publishing task.

Large source data is local-only and excluded from Git; import manifests retain
its exact hashes. The schema, code, curated tournaments and small registries
are versioned. No remote publishing or changes to other applications' settings
are part of this consolidation.
