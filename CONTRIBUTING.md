# Contributing

Thanks for helping. The project has three kinds of content, and they have
different rules:

| What | Where | Rule |
| --- | --- | --- |
| Database | `src/engine_migrate/data/db/` | built with `tools/extract/`; never edited by hand |
| Mapping table | `src/engine_migrate/data/mappings/concepts.yaml` | curated; every row must cite options that exist in the database |
| Code | `src/engine_migrate/`, `tools/` | typed, linted, tested |

## Development setup

```sh
git clone https://github.com/cb-Sigma/engine-migrate && cd engine-migrate
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

## Adding a mapping row (the most useful contribution)

Say you want `--enforce-eager` (vLLM) to stop being reported as `UNKNOWN`.

1. **Read both sides in the database**, not in a blog post:

   ```sh
   engine-migrate flags vllm --grep enforce-eager -f json
   engine-migrate flags sglang --grep cuda-graph
   ```

   The JSON output has `source.path` and `source.line`; open them in the
   engine repository at the tag if the help text leaves doubt.

2. **Decide honestly what the relation is.**
   - Same quantity, same unit, same scope → same `semantics` tag (`EXACT`).
   - Related but different → different tags (`APPROXIMATE`); the two
     `meaning` sentences are what users will read, so make the difference
     visible in them.
   - Nothing comparable → bind only the engines that have it and add an
     `absent:` note for the others, naming the nearest control if there is one.

   When unsure, choose the weaker claim. A wrong `EXACT` is the worst bug
   this project can have.

3. **Add the row** to `concepts.yaml` (format: [docs/mapping.md](docs/mapping.md)):

   ```yaml
   - id: execution.eager
     title: Eager execution
     summary: Run without CUDA graphs.
     bindings:
       vllm:
         - option: --enforce-eager
           semantics: disable-cuda-graph-switch
           meaning: Always use eager-mode PyTorch (disables CUDA graphs).
           value: {kind: flag}
     absent:
       sglang: SGLang selects a CUDA graph backend per phase (--cuda-graph-backend-decode / -prefill) instead of a single switch.
   ```

4. **Cite your sources in the pull request**: the two `engine@tag:path:line`
   references (the tool prints them) and one sentence on why the semantics
   are, or are not, the same.

5. **Run the tests.** `tests/test_mapping_integrity.py` fails if the row names
   a flag that the database does not contain for any version it claims. If a
   flag only exists from some version on, add `since: "<version>"`; if it was
   removed, `until:`. Golden files change when output changes; regenerate
   them with `UPDATE_GOLDEN=1 pytest` and review the diff.

## Adding an engine version

See "Adding a version" in the README and [docs/database.md](docs/database.md).
Commit the new JSON file, the updated `index.json`,
`docs/extraction-validation.json` and the regenerated docs
(`python -m tools.gen_docs`). If the validator reports new disagreements,
look into them and describe what you found in `docs/coverage.md`.

## Changing an extractor

- Extractors must stay static: no importing or executing engine code.
- Add a unit test with a small inline source snippet in `tests/test_extract.py`.
- Re-run extraction for all pinned tags and include the database diff in the
  pull request; explain every changed record.
- Re-run `tools.extract.validate`; agreement must not go down without an explanation.

## Style

- `ruff format` and `ruff check` decide formatting and lint questions.
- `mypy --strict` must pass for `src/` and `tools/`.
- User-facing text states what was verified and how; avoid claims about
  engine behaviour that are not backed by the database or a cited source line.
- Keep runtime dependencies to PyYAML.

## Reporting a wrong translation

Open an issue with the command you ran, the output, and what the engine
actually does (a source link or a `--help` excerpt). Wrong `EXACT` results
are treated as bugs with priority.
