# CTML consumer workflow

`D:\dev\proj\ctml` is the source of truth. CTML Workspace reads this corpus
directly; Chess Nerd publishes a derived PGN/HTML archive through the separate
PgnTours repository.

## 1. Finish and validate the CTML source

Build the event-specific file into `tours/`, validate it against the pinned
schema, run the relevant semantic audits, and refresh `corpus.json`:

```powershell
cd D:\dev\proj\ctml
python tools\<event-builder>.py
python tools\ctml_project.py validate --samples
python tools\build_corpus_index.py
python -m unittest discover -s tests
```

Do not publish a generated event merely because it passes XSD validation.
Participant identity, completeness, standings, and source disagreements still
need evidence-based review.

## 2. CTML Workspace

CTML Workspace does not import or copy tournaments. Its library is a direct
view of the canonical `tours` directory, using the canonical `xsd` directory
for validation:

```text
LibraryDirectory = D:\dev\proj\ctml\tours
SchemaDirectory  = D:\dev\proj\ctml\xsd
```

Choose `D:\dev\proj\ctml` with **Open folder**, or set the same paths in
`%LOCALAPPDATA%\CTML Workspace\settings.json`. A newly added `.ctml` file then
appears after restarting the app or choosing the folder again. The application's
source defaults use these canonical paths and migrate settings that still point
at the retired `D:\ctml` tree.

The application repository keeps a fallback schema snapshot in
`D:\dev\proj\ctml-workspace\schemas`. When the maintained schema changes,
synchronize that entire XSD set deliberately and run the Workspace tests.

## 3. Chess Nerd Tournament Archive

Chess Nerd does not read `corpus.json` at runtime. For each vetted CTML file,
run its exporter from the Astro repository:

```powershell
cd D:\dev\proj\chessnerd\chessnerd
python scripts\build_tournament_archive_zip.py `
  D:\dev\proj\ctml\tours\<tournament>.ctml
npm test
npm run build
```

The exporter performs two writes:

- `D:\dev\proj\chessnerd\PgnTours\<decade>s\<tournament>.zip`, containing
  derived PGN and crosstable HTML;
- `public\data\tournament-archive\manifest.json` in the Chess Nerd site,
  which supplies the Tournament Archive page.

Process one new CTML file at a time when either checkout is dirty. Directory
mode rebuilds every archive ZIP and can obscure unrelated work.

## 4. Publication order

The manifest's download URL points at the PgnTours GitHub repository. Publish
in this order so the public page never links to a missing archive:

1. Commit/publish the authoritative CTML source and its builder.
2. Commit/push the generated ZIP in `PgnTours`.
3. Commit/push the Chess Nerd manifest and site changes; its GitHub Actions
   build deploys the updated Tournament Archive page.

Local generation and validation do not publish or deploy anything.
