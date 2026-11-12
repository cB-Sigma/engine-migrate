# engine-migrate

**Move an LLM serving config between vLLM, SGLang, llama.cpp, TGI and Ollama - or between two versions of one engine - and see exactly which options carry over, which only roughly, and which do not.**

[![CI](https://github.com/cb-Sigma/engine-migrate/actions/workflows/ci.yml/badge.svg)](https://github.com/cb-Sigma/engine-migrate/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fcb-Sigma%2Fengine-migrate%2Fmain%2Fpyproject.toml)](pyproject.toml)

`engine-migrate` does three things, all offline and without running any engine:

1. **`translate`** a command line, YAML config, env file, `docker run` line or Kubernetes container spec from one engine to another, and classify every option as `EXACT`, `APPROXIMATE`, `NO EQUIVALENT` or `UNKNOWN`, each with a pointer to the engine source line it is based on.
2. **`diff`** two versions of one engine: flags and metrics added, removed, renamed, deprecated, or with a changed default - optionally restricted to what touches *your* config, with a suggested rewrite.
3. **`lint-metrics`**: check Grafana dashboards and Prometheus rules for metric names and labels that do not exist in the engine version you are about to deploy. Exit code 1 for CI.

Everything it knows comes from a **database extracted statically from engine source at release tags** (27 engine versions; about 6,600 flag records, 3,300 environment-variable and parameter records, and 1,600 metric records). No option name, default or metric in this tool is written from memory; every record carries the file and line it came from, and the extraction is [measured against each engine's own generated reference](#coverage).

It is an independent project and is not affiliated with any of the engines.

## Install

```sh
pip install git+https://github.com/cb-Sigma/engine-migrate
engine-migrate --demo        # runs the three commands on bundled examples
```

Python 3.10+. One runtime dependency (PyYAML). The database ships inside the package; nothing is downloaded at run time.

## What it looks like

### Translate

```sh
engine-migrate translate --from vllm --to sglang "vllm serve Qwen/Qwen3-32B --max-model-len 32768 ..."
```

<!-- engine-migrate: translate --from vllm --to sglang --from-version 0.33.0 --to-version 0.5.23 "vllm serve Qwen/Qwen3-32B --max-model-len 32768 --gpu-memory-utilization 0.90 --tensor-parallel-size 2 --enable-auto-tool-choice --tool-call-parser hermes --kv-cache-memory-bytes 24G" -->
```text
# vllm 0.33.0 -> sglang 0.5.23  (input read as: command)

python3 -m sglang.launch_server \
  --model-path Qwen/Qwen3-32B \
  --context-length 32768 \
  --mem-fraction-static 0.90 \
  --tp-size 2 \
  --tool-call-parser hermes

## Report: 3 exact, 2 approximate, 2 no equivalent, 0 unknown

EXACT          Qwen/Qwen3-32B (positional)  ->  --model-path Qwen/Qwen3-32B
               source: vllm@v0.33.0:vllm/engine/arg_utils.py:969
               target: sglang@v0.5.23:python/sglang/srt/arg_groups/fields/model.py:51
EXACT          --max-model-len 32768  ->  --context-length 32768
               source: vllm@v0.33.0:vllm/engine/arg_utils.py:991
               target: sglang@v0.5.23:python/sglang/srt/arg_groups/fields/model.py:110
APPROXIMATE    --gpu-memory-utilization 0.90  ->  --mem-fraction-static 0.90
               vllm: Fraction (0-1) of GPU memory for the model executor of this instance. sglang:
               Fraction of memory used for static allocation (model weights and KV cache pool).
               source: vllm@v0.33.0:vllm/engine/arg_utils.py:1388
               target: sglang@v0.5.23:python/sglang/srt/arg_groups/fields/schedule.py:27
EXACT          --tensor-parallel-size 2  ->  --tp-size 2
               source: vllm@v0.33.0:vllm/engine/arg_utils.py:1208
               target: sglang@v0.5.23:python/sglang/srt/arg_groups/fields/parallel.py:45
NO EQUIVALENT  --enable-auto-tool-choice
               SGLang has no separate switch; selecting --tool-call-parser is the only tool-calling
               option.
               source: vllm@v0.33.0:vllm/entrypoints/common/cli_args.py:123
APPROXIMATE    --tool-call-parser hermes  ->  --tool-call-parser hermes
               vllm: Tool call parser for the model in use; built-in or registered by a plugin.
               sglang: Parser for tool-call interactions.
               note: Parser names are engine-specific; the value is copied and checked against the
               target's built-in names.
               source: vllm@v0.33.0:vllm/entrypoints/common/cli_args.py:129
               target: sglang@v0.5.23:python/sglang/srt/server_args.py:378
NO EQUIVALENT  --kv-cache-memory-bytes 24G
               SGLang sizes the pool in tokens, not bytes (--max-total-tokens), or as a fraction
               (--mem-fraction-static); there is no byte-sized option.
               source: vllm@v0.33.0:vllm/engine/arg_utils.py:1394

## Also check

- Listen address: not set, and the defaults differ - vllm 0.33.0 --host defaults to None, sglang
  0.5.23 --host defaults to '127.0.0.1'.
- Listen port: not set, and the defaults differ - vllm 0.33.0 --port defaults to 8000, sglang 0.5.23
  --port defaults to 30000.
- Prometheus metrics: sglang 0.5.23 has a switch for this (--enable-metrics); vllm has none, so
  decide whether to add it.
```

The config argument can be a file, `-` for stdin, or the command line itself. The output keeps the shape of the input: a Kubernetes manifest comes back as a manifest with `args`, `command` and `env` rewritten and everything else untouched; a `docker run` line keeps its Docker options. See [docs/usage.md](docs/usage.md) for each shape.

### Diff two versions, for your config

<!-- engine-migrate: diff sglang 0.5.18 0.5.23 --config examples/sglang-launch.sh -->
```text
# sglang: 0.5.18 -> 0.5.23, for this configuration  (input read as: command)

severity  option                      what changes
--------  --------------------------  ----------------------------------------
ERROR     --quantization              value 'marlin' is no longer a listed choice in 0.5.23
ERROR     --cuda-graph-max-bs         removed in 0.5.23 (replaced by --cuda-graph-max-bs-decode; was
                                      deprecated in 0.5.18)
ERROR     --stream-output             removed in 0.5.23 (replaced by --incremental-streaming-output;
                                      was deprecated in 0.5.18)
ERROR     --collect-tokens-histogram  removed in 0.5.23 (was deprecated in 0.5.18)

## Defaults that changed for options this configuration does not set (7)

option                       change
---------------------------  ----------------------------------------
--dcp-comm-backend           default 'ag_rs' -> unset
--enable-fused-qk-norm-rope  default False -> unset; type bool -> str
--enable-tf32-matmul         default False -> unset; type bool -> str
--file-storage-path          default 'sglang_storage' -> unset
--mamba-full-memory-ratio    default 0.9 -> unset
--mm-enable-dp-encoder       default False -> unset; type bool -> str
--swa-full-tokens-ratio      default 0.8 -> unset

## Suggested configuration for sglang 0.5.23

python3 -m sglang.launch_server \
  --model-path Qwen/Qwen3-32B-AWQ \
  --quantization marlin \
  --tp-size 2 \
  --mem-fraction-static 0.88 \
  --cuda-graph-max-bs-decode 128 \
  --incremental-streaming-output \
  --enable-metrics \
  --port 30000

- --quantization marlin was kept as written; pick a value that sglang 0.5.23 accepts.
- --cuda-graph-max-bs was rewritten to --cuda-graph-max-bs-decode, the replacement named by sglang's own deprecation notice.
- --stream-output was rewritten to --incremental-streaming-output, the replacement named by sglang's own deprecation notice.
- --collect-tokens-histogram was dropped from the rewritten config (removed in 0.5.23).
```

Without `--config` you get the full list (`engine-migrate diff vllm 0.32.0 0.33.0`). Renames are only applied to the suggested config when there is hard evidence: the old spelling is an alias of the new option, or the engine's own deprecation notice names the replacement. Renames guessed from help-text similarity are shown as `(inferred)` and never applied automatically.

### Lint dashboards and alert rules

<!-- engine-migrate: lint-metrics --engine vllm --to 0.31.0 examples/grafana-dashboard.json examples/alert-rules.yaml -->
```text
examples/grafana-dashboard.json: vLLM serving > KV cache usage [A]
  ERROR [unknown] vllm:gpu_cache_usage_perc
    not defined in any vllm version in the database
examples/grafana-dashboard.json: vLLM serving > Finished requests by reason [A]
  WARNING [label] vllm:request_success_total
    label 'finish_reason' is not a label of vllm:request_success in vllm 0.31.0 (labels: model_name,
    engine, finished_reason)
    source: vllm@v0.31.0:vllm/v1/metrics/loggers.py:729
examples/grafana-dashboard.json: vLLM serving > Preemptions [A]
  ERROR [type] vllm:num_preemptions_bucket
    vllm:num_preemptions is a counter in vllm 0.31.0; it has no _bucket series
    source: vllm@v0.31.0:vllm/v1/metrics/loggers.py:676
examples/alert-rules.yaml: vllm / VllmAdmissionRejections
  ERROR [not-in-version] vllm:admission_rejections_total
    not defined in vllm 0.31.0; it is defined in vllm 0.32.0 to 0.33.0

2 file(s), 14 expression(s), 15 vllm metric reference(s) checked against vllm 0.31.0: 3 error(s), 1 warning(s)
```

Histogram `_bucket` / `_sum` / `_count` and counter `_total` suffixes are resolved to the metric that produces them, so `..._bucket` on a counter is reported as a type error rather than an unknown metric. With `--from VERSION` the linter also says whether a missing metric was removed, renamed (inferred) or changed type since the version the dashboard was written for.

### Browse the database

```sh
engine-migrate flags vllm 0.33.0 --grep prefix-caching
engine-migrate metrics sglang --grep latency -f markdown
engine-migrate versions
```

All commands accept `--format table|json|markdown`.

## Coverage

<!-- generated:coverage -->
| Engine | Versions in the database | Flags | Env vars / params | Metrics | Extraction method | Measured agreement with the engine's own reference |
| --- | --- | --- | --- | --- | --- | --- |
| vLLM | 0.28.0, 0.29.0, 0.30.0, 0.31.0, 0.32.0, 0.33.0 | 284-320 | 295-318 | 66-99 | Python `ast`: argparse calls + config dataclasses (`get_kwargs`), `envs.py`, metric constructors | flags found 1823/1823 (100%); no spurious flags 1823/1823 (100%); aliases/negations 1823/1823 (100%); defaults 906/906 (100%); choices 251/262 (95.8%); metric names in the engine's docs/dashboards 211/211 (100%). Reference: CLI reference on docs.vllm.ai for each tag (built by vLLM from the live argparse parser) |
| SGLang | 0.5.18, 0.5.19, 0.5.20, 0.5.21, 0.5.22, 0.5.23 | 488-531 | 0 | 142-169 | Python `ast`: `A[T, Arg(...)]` annotated fields + argparse calls, metric constructors | documented flags found 1749/1765 (99.1%); metric names in the engine's docs/dashboards 156/172 (90.7%); flags found 1075/1075 (100%); no spurious flags 1048/1052 (99.6%). Reference: `server_arguments.mdx` in the repo; complete from 0.5.22 on, where SGLang's own test also keeps a list of undocumented flags |
| llama.cpp | b9000, b10000, b11000, b11500, b12000, b12323 | 239-265 | 123-157 | 11-16 | C++ scan of `add_opt(common_arg(...))` in `common/arg.cpp`, defaults from `common/common.h`, metric tables in the server | flags found 1487/1487 (100%); no spurious flags 1487/1520 (97.8%); aliases/negations 1473/1487 (99.1%); env names 1479/1487 (99.5%); rendered help incl. defaults 1352/1362 (99.3%); metric names in the engine's docs/dashboards 78/80 (97.5%). Reference: help tables in `tools/server/README.md` (written by `llama-gen-docs`) |
| TGI | 3.0.2, 3.1.1, 3.2.3, 3.3.7 | 52-54 | 51-53 | 27 | Rust scan of the `clap` struct `Args` in `launcher/src/main.rs`, `metrics::*!` macros | flags found 211/211 (100%); no spurious flags 211/211 (100%); env names 211/211 (100%); defaults 75/75 (100%); choices 24/24 (100%); metric names in the engine's docs/dashboards 144/144 (100%). Reference: `docs/source/reference/launcher.md` (a `--help` dump) |
| Ollama | 0.33.0, 0.34.0, 0.35.0, 0.40.0, 0.40.16 | 0 | 70-72 | 0 | Go scan of `envconfig/config.go`, `api/types.go`, `parser/parser.go` | env vars found 115/115 (100%). Reference: env vars referenced by command help in `cmd/cmd.go` (weak: no generated reference exists) |
<!-- /generated:coverage -->

Per-version numbers and every known disagreement are listed in [docs/coverage.md](docs/coverage.md); the raw report is [docs/extraction-validation.json](docs/extraction-validation.json).

How to read the agreement column:

- For **vLLM, llama.cpp and TGI** the reference is produced by the engine from its running argument parser, so the numbers measure the extractor directly.
- For **SGLang** the reference page is written by hand. From 0.5.22 on SGLang's own test suite keeps the page plus a list of undocumented flags complete, so flag coverage is measured there; for older versions only "documented flags found" is meaningful (the four flags not found are documented but no longer exist in source at those tags).
- For **Ollama** there is no generated reference, so the check is weak. Ollama has no Prometheus endpoint, hence zero metrics.
- **Metrics**: no engine publishes a machine-generated metric list, so metrics are validated indirectly - every metric name in the engine's own docs and example dashboards must resolve in the database for the same tag. The names that do not are stale documentation (checked by hand against the source; listed in docs/coverage.md).

## What EXACT, APPROXIMATE, NO EQUIVALENT and UNKNOWN mean

| Status | Meaning | Example (vLLM 0.33.0 → SGLang 0.5.23) |
| --- | --- | --- |
| `EXACT` | Both options are documented with the same meaning and the value carries over unchanged. | `--tensor-parallel-size 2` → `--tp-size 2` |
| `APPROXIMATE` | The nearest option exists, but it means something measurably different, or the value could not be carried over unchanged. The report quotes both meanings. | `--gpu-memory-utilization 0.90` → `--mem-fraction-static 0.90`: vLLM's number is the fraction of GPU memory for the model executor of that instance; SGLang's is the fraction used for static allocation (weights plus KV cache pool). Same number, different budget. |
| `NO EQUIVALENT` | The option is understood, and the target engine (in that version) has nothing that expresses it. The explanation names the nearest control when there is one. | `--kv-cache-memory-bytes 24G`: SGLang sizes its pool in tokens or as a fraction, never in bytes. |
| `UNKNOWN` | Either the option is not in the database for the source version (typo, plugin flag, other version), or it is a real option that no mapping row covers yet. The two cases are worded differently. | `--enforce-eager`: known vLLM flag, no mapping row yet. |

More `APPROXIMATE` cases worth knowing:

- **Units that cannot be converted.** vLLM `--gpu-memory-utilization` (a fraction) has no llama.cpp counterpart: llama.cpp budgets by layers (`--gpu-layers`) or a free-memory margin in MiB (`--fit-target`). That is reported as `NO EQUIVALENT` with those options named, not converted with a made-up formula.
- **Engine-specific names.** Tool-call parsers, reasoning parsers, quantisation methods and KV-cache dtypes are copied as written and checked against the target's list of accepted values; a value the target does not list is flagged.
- **Lists vs single values.** vLLM accepts several `--api-key` values, SGLang one: the first is kept and the loss is reported.
- **Defaults that differ when you set nothing.** If you did not set a port, vLLM listens on 8000 and SGLang on 30000; the report's "Also check" section says so.

The tool is deliberately conservative: when two options are not documented as meaning the same thing, the mapping row says `APPROXIMATE` or is left out.

## How the database is built

```text
reference clones (blob-less)          tools/extract/*.py                 src/engine_migrate/data/db/
  vllm @ v0.33.0  ── git show ──▶  parse definitions statically  ──▶  vllm/0.33.0.json   (one file per engine/version)
  sglang @ v0.5.23                 (never imports or runs engine code)  sglang/0.5.23.json ...
```

- **vLLM**: most flags are derived at run time from config dataclasses (`get_kwargs(ModelConfig)["max_model_len"]`). The extractor re-implements that derivation on the AST: field type, default, attribute docstring, `Literal` choices (following aliases across modules), `--no-x` / `--disable-x` negations.
- **SGLang**: `A[T, Arg(...)]` annotated fields plus the manual `add_argument` calls, including the parser-name registries.
- **llama.cpp**: every `add_opt(common_arg(...))` block in `common/arg.cpp`; `string_format` help strings are rendered with the struct defaults from `common/common.h`, which is how defaults are recovered.
- **TGI**: the `clap` derive struct of the launcher and the `metrics::*!` macros of the router and backends.
- **Ollama**: `envconfig`, the Modelfile parser and the `Options` struct.

Every record has `source.path` and `source.line`; every file has the repo URL, tag and commit. `tools/extract/validate.py` then compares the result with the reference each engine generates from its real argument parser and writes the measured agreement to `docs/extraction-validation.json`. Details: [docs/database.md](docs/database.md).

### Adding a version

```sh
git clone --filter=blob:none --no-checkout https://github.com/vllm-project/vllm ~/ref/vllm
# add the tag to PINS in tools/extract/run.py, then:
python -m tools.extract.run --ref ~/ref --engine vllm
python -m tools.extract.validate --ref ~/ref
python -m tools.gen_docs
ENGINE_MIGRATE_REF=~/ref pytest
```

The mapping integrity test fails if the new version drops a flag that a mapping row claims for it, which is the signal to add `until:` to that row.

## Limitations

- **Static extraction can miss dynamically registered options.** Flags added by platform plugins, choices filled from a runtime registry, and metrics whose names are computed at run time are invisible unless they follow a pattern the extractor knows. vLLM's HTTP-level metrics come from a third-party instrumentation library and are not listed. Most SGLang metrics take their base label names from a runtime dict, so label checks are skipped for them.
- **Mappings are curated and incomplete.** About 40 concepts are mapped today (model source, revision, dtype, quantisation, context length, GPU memory, tensor/pipeline/data parallelism, concurrency, batching, prefix cache, chat template, tool-call and reasoning parsers, LoRA, API key, host/port, TLS, metrics, trust-remote-code, download dir, tokenizer, seed, speculative decoding, logging). Everything else is reported as `UNKNOWN` rather than guessed. vLLM ↔ SGLang is the best-covered pair; llama.cpp next; TGI and Ollama cover the basics.
- **Behaviour beyond flags is out of scope.** Two engines given "the same" options still differ in scheduling, memory accounting, tokenisation edge cases, API surface and performance. `EXACT` means the *options* are documented as equivalent, not that the servers behave identically.
- **Container images are not translated.** Docker and Kubernetes output contains an `<engine-image>` placeholder; ports, probes and resource requests are copied unchanged.
- **Rename detection is heuristic** when there is no alias or deprecation notice to rely on; such results are labelled `inferred` and are never applied automatically. In the versions covered today no engine renamed a metric, so that path is exercised by synthetic tests only.
- **Defaults** are source-level defaults. An engine may replace `None` with a computed value at start-up; the database then says `unset`.
- **PromQL** is scanned, not fully parsed; label checks only look at label matchers written directly on a selector.
- The version range is what is listed under [Coverage](#coverage); nothing is known about other versions.

## Related work

- [apex-mochen/servediff](https://github.com/apex-mochen/servediff) reads vLLM / SGLang **startup logs** into named facts and diffs two runs. It answers "what did the server actually do", including choices the engine makes by itself (kernel backends, CUDA graph capture, library versions) that no flag diff can see - but it needs logs from both runs. `engine-migrate` answers the earlier question, "what will change in my config and dashboards", from source alone, before anything is deployed. The two are complementary: use this to plan an upgrade, servediff to verify it.
- [vllm-project/recipes](https://github.com/vllm-project/recipes) and community recipe collections answer "how do I run model X on hardware Y" for one engine; they are hand-written and not machine-readable across engines.
- [cloudflare/pint](https://github.com/cloudflare/pint) lints Prometheus rules, including checks against a live Prometheus. [grafana/dashboard-linter](https://github.com/grafana/dashboard-linter) checks dashboard conventions. Neither knows which metrics an inference engine exposes in a given release; `lint-metrics` adds exactly that and nothing else, so it fits next to them.
- [zhaoshuh/llm-serving-config-constraints](https://github.com/zhaoshuh/llm-serving-config-constraints) is a research artifact of extracted and execution-checked constraints *between* options of one engine version - a different question (which combinations are valid) from this one (which options correspond).

We searched GitHub and PyPI for a cross-engine option translator or a static per-version flag/metric diff and did not find one; if you know of one, please open an issue so it can be listed here.

## FAQ

**Does it run or import the engines?** No. Extraction reads source text through `git show`; the installed tool only reads JSON and YAML.

**Can I trust an `EXACT`?** It means both options exist in the selected versions (verified against the database) and a maintainer judged their documented meanings equal. Follow the two `source:` lines to check for yourself.

**Why is a flag I use `UNKNOWN`?** Either no mapping row covers it yet (the message says "no mapping row") - contributions are a few lines of YAML - or it is not in the database for that version (check `--from-version`).

**Why are there so few TGI/Ollama mappings?** Those engines expose far fewer serving options, and several (Ollama especially) work differently enough that honest answers are mostly `NO EQUIVALENT`.

**Which versions are the default?** The newest in the database for each engine. Pin them with `--from-version` / `--to-version` in scripts.

**Can I use it in CI?** Yes: `lint-metrics` exits 1 on errors (`--strict` includes warnings), `diff --config ... --exit-code` exits 1 if the config is affected, `translate --strict` exits 1 on `NO EQUIVALENT`/`UNKNOWN`. Exit code 2 means a usage error.

**llama.cpp has no version numbers - what are `b12323` etc.?** Its release tags are build numbers; the database holds a sample of them with their dates.

## Roadmap

- More mapping rows (CORS, request logging, CUDA graph controls, generation defaults, multimodal limits), and value maps for parser names where both engines document the same format.
- Docker Compose and Helm values as input shapes.
- More versions per engine, and SGLang's gateway.
- Environment-variable mappings for vLLM (`VLLM_*`) and SGLang.
- A machine-readable export of the concept table.

## Contributing

The most useful contribution is a mapping row: a few lines in [`src/engine_migrate/data/mappings/concepts.yaml`](src/engine_migrate/data/mappings/concepts.yaml) plus the source lines that justify it. [CONTRIBUTING.md](CONTRIBUTING.md) walks through it; a test fails if a row names a flag the database does not contain.

## License

Apache-2.0, see [LICENSE](LICENSE). The database contains short factual strings (option names, defaults, help texts) extracted from the engines' source; see [NOTICE](NOTICE) for where they come from.
