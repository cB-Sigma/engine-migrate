# The database

`src/engine_migrate/data/db/<engine>/<version>.json` holds everything the
tool knows about one engine at one release tag. `index.json` lists the
versions in release order. The files are generated; never edit them by hand.

## File format

```json
{
  "schema": 1,
  "engine": "vllm",
  "version": "0.33.0",
  "provenance": {
    "repo": "https://github.com/vllm-project/vllm",
    "tag": "v0.33.0",
    "commit": "<sha the tag points to>",
    "tag_date": "YYYY-MM-DD",
    "method": "static source extraction",
    "sources": ["vllm/engine/arg_utils.py", "..."]
  },
  "notes": ["what this file does and does not cover"],
  "options": [
    {
      "name": "--tensor-parallel-size",
      "kind": "cli",
      "aliases": ["-tp"],
      "type": "int",
      "default": 1,
      "help": "Number of tensor parallel groups.",
      "deprecated": false,
      "source": {"path": "vllm/engine/arg_utils.py", "line": 1208}
    }
  ],
  "metrics": [
    {
      "name": "vllm:num_requests_running",
      "type": "gauge",
      "labels": ["model_name", "engine"],
      "help": "Number of requests in model execution batches.",
      "source": {"path": "vllm/v1/metrics/loggers.py", "line": 527}
    }
  ]
}
```

Option fields:

| Field | Meaning |
| --- | --- |
| `name` | primary spelling (first long flag, env var name, parameter name) |
| `kind` | `cli`, `env`, `param` (Ollama model parameter) or `modelfile` (Ollama instruction) |
| `aliases` | other accepted spellings |
| `negations` | negative spellings of a boolean flag (`--no-x`, `--disable-x`) |
| `type` | `bool`, `int`, `float`, `str`, `json`, `list[...]`, or the name of a custom parser |
| `default` | source-level default; `null` means "unset". Absent when it could not be determined. |
| `default_expr` | the source expression, when the default is not a compile-time constant |
| `choices`, `choices_open` | accepted values; `choices_open` means other values are also accepted (plugins, free-form) |
| `help` | help text with whitespace collapsed; unresolved pieces appear as `{expr}` or `<expr>` |
| `help_partial` | llama.cpp: the help contains a value that is only known at run time |
| `deprecated`, `replaced_by` | structured marker, or a help text that announces deprecation |
| `env` | environment variable bound to the flag (llama.cpp, TGI); `flag` is the reverse link on `env` records |
| `config_field` | the config class field the flag is bound to (vLLM, SGLang) |
| `source` | file and line in the engine repository at the tag |

Metric fields: `name` (as written in source - for `prometheus_client`
counters the exposed series has a `_total` suffix), `type`, `labels`,
`labels_dynamic` (some label names are passed in at run time), `help`,
`source`.

## Building it

You need blob-less clones of the engines (about 200 MB in total):

```sh
mkdir -p ~/ref && cd ~/ref
for r in vllm-project/vllm sgl-project/sglang ggml-org/llama.cpp \
         huggingface/text-generation-inference ollama/ollama; do
  git clone --filter=blob:none --no-checkout https://github.com/$r
done
```

Only the handful of files each extractor needs are fetched, through
`git show <tag>:<path>`. Do not run `git grep` or `git log -p` in those
clones: that would download every blob.

```sh
python -m tools.extract.run --ref ~/ref                    # all pinned tags
python -m tools.extract.run --ref ~/ref --engine vllm --tag v0.33.0
python -m tools.extract.validate --ref ~/ref               # writes docs/extraction-validation.json
python -m tools.gen_docs                                   # refresh README / docs tables
ENGINE_MIGRATE_REF=~/ref pytest tests/test_extract.py      # re-extract and compare with the committed files
```

The pinned tags are the `PINS` table in `tools/extract/run.py`. Extraction is
deterministic: the same tag always gives a byte-identical file, and
`tests/test_extract.py` asserts that for one tag per engine when the clones
are available.

## How each extractor works

**vLLM** (`tools/extract/vllm.py`). `vllm serve` builds its parser in
`make_arg_parser`. Engine arguments are registered as
`group.add_argument("--flag", **kwargs["field"])`, where `kwargs` comes from
`get_kwargs(SomeConfig)` and is computed at run time from the dataclass field
(type annotation, default, attribute docstring). The extractor parses the
config modules, mirrors `_compute_kwargs` on the AST, applies the
`kwargs["field"]["type"] = ...` style adjustments it finds, and follows
imports to resolve `Literal` aliases defined in other modules. Front-end
arguments are one flag per field of `FrontendArgs`. Boolean flags get their
`--no-x` form, and from v0.33.0 the `--enable-x` / `--disable-x` pair.
Environment variables are the keys of `environment_variables` in `envs.py`
with the comment above each as help. Metrics are every
`prometheus_client`-style constructor in the metric modules, including names
that are loop variables over a literal list.

**SGLang** (`tools/extract/sglang.py`). Arguments are `A[T, "help"]` or
`A[T, Arg(...)]` annotated fields on `ServerArgs` (up to v0.5.19) or on the
classes in `arg_groups/fields/` (from v0.5.20), plus a few manual
`add_argument` calls. Parser-name choices are read from the registries.
Metrics come from the modules under `srt/observability/`.

**llama.cpp** (`tools/extract/llamacpp.py`). A small C++ scanner splits each
`add_opt(common_arg(...))` call into names, negative names, value hint, help
expression and handler, then reads the chained `.set_examples`,
`.set_excludes`, `.set_env`. Options are kept when they apply to the server.
`string_format("... (default: %d)", params.n_ctx)` is rendered by evaluating
`params.n_ctx` against the member initialisers in `common/common.h` (and the
server-specific overrides in `arg.cpp`); the default is then read from the
rendered text. Metrics are read from the metric tables in the server source
(three historical styles).

**TGI** (`tools/extract/tgi.py`).
The `clap` derive struct `Args` gives the flag name, env var, default, type
and doc comment; `ValueEnum` enums give choices. Metrics are the
`metrics::counter!/gauge!/histogram!` and `describe_*!` macros across router
and backends.

**Ollama** (`tools/extract/ollama.py`). Environment variables from
`envconfig.AsMap()`, model parameters from the `Options` / `Runner` structs
and `DefaultOptions()`, Modelfile instructions from the parser.

## What static extraction cannot see

- options registered by plugins or platform hooks at start-up;
- choices appended to a list at import time by other modules;
- defaults that are computed (`None` in source, resolved when the engine starts);
- metrics created by third-party libraries (for example the HTTP metrics of
  `prometheus-fastapi-instrumentator` in vLLM), and metric names assembled
  from strings at run time;
- label names passed in as a runtime dict (`labels_dynamic`).

`docs/coverage.md` lists every disagreement the validator found.
