# {{event_name}}

## Tournament record

- Dates: {{dates}}
- Place: {{place}}
- Federation: {{federation}}
- Format: {{event_type}}
- Cadence: {{cadence}}
- Source CTML: `{{ctml_filename}}`
- CTML SHA-256: `{{ctml_sha256}}`
- PGN games with movetext: {{pgn_games}}

{{standings}}

## About CTML

Chess Tournament Markup Language (CTML) is an XML format for preserving a
chess event as a structured, machine-readable record.  It can represent the
event's identity, dates and place, participants and teams, standings, game
results, and move records.  This archive's CTML file is the authoritative
record; the accompanying PGN is a compatibility export containing the games
with movetext.

CTML is designed to make an event independently usable: readers can inspect
the XML directly, validate it against the maintained schema bundle, or open it
in CTML Workspace.  The CTML project, schemas, corpus, and validation tooling
are at <https://github.com/ianrastall/ctml>.

## Archive contents

- `{{ctml_filename}}` — authoritative CTML tournament record (UTF-8 XML).
- `{{pgn_filename}}` — SAN PGN export of games that contain movetext.
- `README.md` — this event record, its reported standings, and CTML explainer.

This archive was generated from the CTML source without changing its bytes.
