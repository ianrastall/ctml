# CTML project

The canonical home is this project, `D:\dev\proj\ctml`. Read `README.md` and
`docs/CONSOLIDATION.md` before changing data or build paths. Historical handoffs
describe older layouts and are reference material.

- `xsd/` is the maintained schema set. Verify its lock and run relevant XML
  validation after changes; update the schema lock only for deliberate changes.
- Preserve curated tournament content and source evidence. Never infer missing
  rounds, players or results merely to make a tournament appear complete.
- `registry/` is the imported baseline; upstream reviewed historical decisions
  are authoritative within their scope and have not yet all been reconciled.
- Raw archives remain external and are catalogued in `provenance/sources.json`.
  Never run destructive legacy rebuild scripts merely to inspect their inputs.
- `tools/ctml_project.py` writes reports to `build/validation`. New generated
  datasets belong in `build/` or an explicit new release directory until vetted.
- Keep the Python and Rust paths aligned with this root. Do not revive output
  defaults under `D:\ctml` or `D:\dev\proj\ctml-clean`.
- `D:\dev\proj\ssp` and its Elysium review pipeline may be actively edited by
  another agent. Preserve that work and consume an identified validated batch.
- Validate changes with the appropriate Python tests, Rust tests and corpus
  checks. XSD validity does not establish historical accuracy or completeness.
