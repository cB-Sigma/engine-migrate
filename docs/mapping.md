# The mapping table

`src/engine_migrate/data/mappings/concepts.yaml` is the only curated data in
the project. It links options of different engines through neutral
*concepts*.

```yaml
- id: parallel.tensor
  title: Tensor parallelism
  summary: Number of GPUs a single model replica is split across.
  bindings:
    vllm:
      - option: --tensor-parallel-size
        semantics: tensor-parallel-degree
        meaning: Number of tensor parallel groups.
    sglang:
      - option: --tp-size
        semantics: tensor-parallel-degree
        meaning: The tensor parallelism size.
    tgi:
      - option: --num-shard
        semantics: shard-count
        meaning: Number of shards (GPUs) the model is split over; TGI uses all GPUs when unset.
  absent:
    llamacpp: llama-server has no tensor-parallel degree; multi-GPU placement is controlled by --device and --split-mode.
```

## How a translation is classified

1. The input option is looked up in the database for the source version.
   Not found → `UNKNOWN`.
2. The row that binds it is looked up. No row → `UNKNOWN` ("no mapping row").
3. The target engine's binding for the same concept is looked up, and its
   option must exist in the database for the target version. No binding →
   `NO EQUIVALENT`, explained by the `absent` text (or by the version range
   in which the option exists).
4. The value is converted (`value.kind`). If the two bindings have different
   kinds of value → `NO EQUIVALENT`.
5. Equal `semantics` tags and a loss-free value → `EXACT`. Otherwise
   `APPROXIMATE`, with both `meaning` sentences quoted and any value notes.

## Rules for rows

- **Every `option` must exist in the database** for every database version
  between `since` and `until` (default: all). `tests/test_mapping_integrity.py`
  fails otherwise, version by version.
- **`meaning` paraphrases the option's own help text.** Run
  `engine-migrate flags ENGINE --grep NAME` and stay within what it says.
- **Equal `semantics` tags are a claim.** Use the same tag only when both
  help texts describe the same quantity in the same unit. Different units,
  different scope (per GPU / per instance / per request), different value
  vocabularies → different tags.
- **Never convert units with a formula** the engines do not document. Leave
  the concept unbound for that engine and write an `absent` note that names
  the nearest control.
- **Do not add options to the output that were not asked for.** If the target
  needs a companion flag, say so in `advice`. A property test checks that
  A → B → A never comes back with a concept that was not in the input.

## Value kinds

| `value.kind` | Use for | Options |
| --- | --- | --- |
| `scalar` (default) | one value copied as written; checked against the target's `choices` and numeric type | `map: {engine value: neutral value}` |
| `flag` | switches. Negative spellings (`--no-x`, `--disable-x`) and YAML/env booleans are understood | `invert: true` when giving the flag turns the feature *off* |
| `list` | several values | `style: nargs` (space separated), `comma`, `single` (only one accepted) |
| `lora` | adapter lists | `style: nargs` (`name=path` items) or `comma-paths` |

`prefer: repo_id | local_path | file_path | inline` chooses between several
bindings of one engine by the look of the value (llama.cpp `--hf-repo` vs
`--model`).
