# The crosstable contract

Every crosstable reader — no matter the source or how messy its input —
emits the **same** JSON. That is the entire point: the variety lives in the
readers; everything downstream sees only this shape.

One crosstable = one JSON object. A reader emits a JSON array of them.

```json
{
  "source": "twic",              // where it came from (free text)
  "ref": "twic-761",             // source-side provenance (issue, url, ...)
  "reader": "twic_reader/0.1",
  "event": "10th Karpov Poikovsky",
  "event_ref": "event:20090603-20090612-karpov-poikovsky",
  "place": "RUS",                // raw place/federation text from the header
  "start": "2009-06-03",         // ISO date if parseable, else null
  "end": "2009-06-12",
  "format": "round-robin",       // "round-robin" | "swiss" | "unknown"
  "rating_system": "fide",
  "url": "https://example.invalid/source-page",
  "source_path": "D:\\ctml\\raw\\capture.html",
  "players": [
    {
      "rank": 1,
      "name": "Motylev, Alexander",
      "ref": "player:example:123", // optional; otherwise player:syn is emitted
      "title": "GM",             // may be empty
      "fed": "RUS",              // may be empty
      "rating": 2677,            // may be null
      "fide_id": "4121830",      // optional source identifier
      "score": 6.5               // final points; may be null
    }
  ]
}
```

Only `event` and `players` (with at least `name`) are required. `start` or
`end` is required to emit CTML, because CTML tournament ids and event refs need
a date anchor. Everything else is best-effort: a reader fills what its source
provides and leaves the rest null. Downstream code resolves `name`/`event`/`place`
against the CTML registries; the `players` list is the tournament's roster,
which is what makes completeness knowable.

Two levels of detail:

- **Standings** (every crosstable): the `players` list above. Gives the
  roster and final scores.
- **Pairings** (some sources, e.g. a round-robin grid): an optional
  `games` array of `{round, white, black, result}`. Not required to start;
  add it where a source provides it. For a round-robin the game list is
  derivable from the roster, so standings alone suffice there.

Readers are per-source scripts (Python is ideal for scraping) and live here,
outside the Rust workspace. Adding a source is: write one small reader that
emits this shape, then pass its JSON through `emit_crosstable_sources.py` or
the shared `ctml_source_common.py` emitter.
