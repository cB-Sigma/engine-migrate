## What this changes

## Checklist

- [ ] `ruff check . && ruff format --check . && mypy && pytest` pass
- [ ] Mapping rows: source references (`engine@tag:path:line`) for each option are listed below, with one sentence on why the semantics are equal / different
- [ ] Database changes come from `python -m tools.extract.run`, not manual edits, and `docs/extraction-validation.json` was regenerated
- [ ] `python -m tools.gen_docs` was run if output or coverage changed
- [ ] CHANGELOG.md updated for user-visible changes

## Sources
