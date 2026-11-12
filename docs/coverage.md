# Database coverage and extraction accuracy

This page is built with `python -m tools.gen_docs` from the database index
and `docs/extraction-validation.json` (written by
`python -m tools.extract.validate`). Do not edit the tables by hand.

## Summary

<!-- generated:coverage -->
| Engine | Versions in the database | Flags | Env vars / params | Metrics | Extraction method | Measured agreement with the engine's own reference |
| --- | --- | --- | --- | --- | --- | --- |
| vLLM | 0.28.0, 0.29.0, 0.30.0, 0.31.0, 0.32.0, 0.33.0 | 284-320 | 295-318 | 66-99 | Python `ast`: argparse calls + config dataclasses (`get_kwargs`), `envs.py`, metric constructors | flags found 1823/1823 (100%); no spurious flags 1823/1823 (100%); aliases/negations 1823/1823 (100%); defaults 906/906 (100%); choices 251/262 (95.8%); metric names in the engine's docs/dashboards 211/211 (100%). Reference: CLI reference on docs.vllm.ai for each tag (built by vLLM from the live argparse parser) |
| SGLang | 0.5.18, 0.5.19, 0.5.20, 0.5.21, 0.5.22, 0.5.23 | 488-531 | 0 | 142-169 | Python `ast`: `A[T, Arg(...)]` annotated fields + argparse calls, metric constructors | documented flags found 1749/1765 (99.1%); metric names in the engine's docs/dashboards 156/172 (90.7%); flags found 1075/1075 (100%); no spurious flags 1048/1052 (99.6%). Reference: `server_arguments.mdx` in the repo; complete from 0.5.22 on, where SGLang's own test also keeps a list of undocumented flags |
| llama.cpp | b9000, b10000, b11000, b11500, b12000, b12323 | 239-265 | 123-157 | 11-16 | C++ scan of `add_opt(common_arg(...))` in `common/arg.cpp`, defaults from `common/common.h`, metric tables in the server | flags found 1487/1487 (100%); no spurious flags 1487/1520 (97.8%); aliases/negations 1473/1487 (99.1%); env names 1479/1487 (99.5%); rendered help incl. defaults 1352/1362 (99.3%); metric names in the engine's docs/dashboards 78/80 (97.5%). Reference: help tables in `tools/server/README.md` (written by `llama-gen-docs`) |
| TGI | 3.0.2, 3.1.1, 3.2.3, 3.3.7 | 52-54 | 51-53 | 27 | Rust scan of the `clap` struct `Args` in `launcher/src/main.rs`, `metrics::*!` macros | flags found 211/211 (100%); no spurious flags 211/211 (100%); env names 211/211 (100%); defaults 75/75 (100%); choices 24/24 (100%); metric names in the engine's docs/dashboards 144/144 (100%). Reference: `docs/source/reference/launcher.md` (a `--help` dump) |
| Ollama | 0.33.0, 0.34.0, 0.35.0, 0.40.0, 0.40.16 | 0 | 70-72 | 0 | Go scan of `envconfig/config.go`, `api/types.go`, `parser/parser.go` | env vars found 115/115 (100%). Reference: env vars referenced by command help in `cmd/cmd.go` (weak: no generated reference exists) |
<!-- /generated:coverage -->

## Per version

"Flags" are command-line options. "Env vars / params" are environment
variables (for llama.cpp and TGI: the environment form of a flag), Ollama
Modelfile instructions and model parameters. Each check is `agree/total`.

<!-- generated:coverage-detail -->
| Engine | Version | Tag | Tag date | Flags | Env vars / params | Metrics | Checks (agree/total) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| vLLM | 0.28.0 | `v0.28.0` | 2026-08-24 | 284 | 295 | 66 | flags found: 284/284<br>no spurious flags: 284/284<br>aliases/negations: 284/284<br>defaults: 145/145<br>choices: 38/40<br>metric names in the engine's docs/dashboards: 25/25 |
| vLLM | 0.29.0 | `v0.29.0` | 2026-09-08 | 292 | 302 | 66 | flags found: 292/292<br>no spurious flags: 292/292<br>aliases/negations: 292/292<br>defaults: 147/147<br>choices: 39/41<br>metric names in the engine's docs/dashboards: 28/28 |
| vLLM | 0.30.0 | `v0.30.0` | 2026-09-21 | 300 | 302 | 67 | flags found: 300/300<br>no spurious flags: 300/300<br>aliases/negations: 300/300<br>defaults: 150/150<br>choices: 41/43<br>metric names in the engine's docs/dashboards: 31/31 |
| vLLM | 0.31.0 | `v0.31.0` | 2026-10-02 | 311 | 308 | 72 | flags found: 311/311<br>no spurious flags: 311/311<br>aliases/negations: 311/311<br>defaults: 155/155<br>choices: 45/46<br>metric names in the engine's docs/dashboards: 31/31 |
| vLLM | 0.32.0 | `v0.32.0` | 2026-10-17 | 316 | 316 | 89 | flags found: 316/316<br>no spurious flags: 316/316<br>aliases/negations: 316/316<br>defaults: 155/155<br>choices: 44/46<br>metric names in the engine's docs/dashboards: 48/48 |
| vLLM | 0.33.0 | `v0.33.0` | 2026-10-31 | 320 | 318 | 99 | flags found: 320/320<br>no spurious flags: 320/320<br>aliases/negations: 320/320<br>defaults: 154/154<br>choices: 44/46<br>metric names in the engine's docs/dashboards: 48/48 |
| SGLang | 0.5.18 | `v0.5.18` | 2026-08-20 | 488 | 0 | 142 | documented flags found: 442/446<br>help text identical to the documented description: 313/406<br>metric names in the engine's docs/dashboards: 25/29 |
| SGLang | 0.5.19 | `v0.5.19` | 2026-09-03 | 503 | 0 | 144 | documented flags found: 444/448<br>help text identical to the documented description: 311/408<br>metric names in the engine's docs/dashboards: 25/29 |
| SGLang | 0.5.20 | `v0.5.20` | 2026-09-18 | 494 | 0 | 150 | documented flags found: 432/436<br>help text identical to the documented description: 300/398<br>metric names in the engine's docs/dashboards: 25/29 |
| SGLang | 0.5.21 | `v0.5.21` | 2026-09-29 | 512 | 0 | 152 | documented flags found: 431/435<br>help text identical to the documented description: 299/397<br>metric names in the engine's docs/dashboards: 25/29 |
| SGLang | 0.5.22 | `v0.5.22` | 2026-10-13 | 521 | 0 | 161 | flags found: 532/532<br>no spurious flags: 519/521<br>help text identical to the documented description: 303/404<br>metric names in the engine's docs/dashboards: 28/28 |
| SGLang | 0.5.23 | `v0.5.23` | 2026-10-28 | 531 | 0 | 169 | flags found: 543/543<br>no spurious flags: 529/531<br>help text identical to the documented description: 308/418<br>metric names in the engine's docs/dashboards: 28/28 |
| llama.cpp | b9000 | `b9000` | 2026-05-01 | 239 | 123 | 11 | flags found: 213/213<br>no spurious flags: 213/239<br>aliases/negations: 199/213<br>env names: 205/213<br>rendered help incl. defaults: 189/195<br>metric names in the engine's docs/dashboards: 7/9 |
| llama.cpp | b10000 | `b10000` | 2026-07-14 | 243 | 128 | 11 | flags found: 241/241<br>no spurious flags: 241/243<br>aliases/negations: 241/241<br>env names: 241/241<br>rendered help incl. defaults: 219/221<br>metric names in the engine's docs/dashboards: 11/11 |
| llama.cpp | b11000 | `b11000` | 2026-09-16 | 255 | 142 | 16 | flags found: 255/255<br>no spurious flags: 255/255<br>aliases/negations: 255/255<br>env names: 255/255<br>rendered help incl. defaults: 234/234<br>metric names in the engine's docs/dashboards: 15/15 |
| llama.cpp | b11500 | `b11500` | 2026-10-08 | 257 | 150 | 16 | flags found: 255/255<br>no spurious flags: 255/257<br>aliases/negations: 255/255<br>env names: 255/255<br>rendered help incl. defaults: 233/233<br>metric names in the engine's docs/dashboards: 15/15 |
| llama.cpp | b12000 | `b12000` | 2026-10-29 | 261 | 155 | 16 | flags found: 261/261<br>no spurious flags: 261/261<br>aliases/negations: 261/261<br>env names: 261/261<br>rendered help incl. defaults: 238/239<br>metric names in the engine's docs/dashboards: 15/15 |
| llama.cpp | b12323 | `b12323` | 2026-11-12 | 265 | 157 | 16 | flags found: 262/262<br>no spurious flags: 262/265<br>aliases/negations: 262/262<br>env names: 262/262<br>rendered help incl. defaults: 239/240<br>metric names in the engine's docs/dashboards: 15/15 |
| TGI | 3.0.2 | `v3.0.2` | 2025-01-24 | 52 | 51 | 27 | flags found: 52/52<br>no spurious flags: 52/52<br>env names: 52/52<br>defaults: 18/18<br>choices: 6/6<br>metric names in the engine's docs/dashboards: 36/36 |
| TGI | 3.1.1 | `v3.1.1` | 2025-03-04 | 52 | 51 | 27 | flags found: 52/52<br>no spurious flags: 52/52<br>env names: 52/52<br>defaults: 18/18<br>choices: 6/6<br>metric names in the engine's docs/dashboards: 36/36 |
| TGI | 3.2.3 | `v3.2.3` | 2025-04-08 | 53 | 52 | 27 | flags found: 53/53<br>no spurious flags: 53/53<br>env names: 53/53<br>defaults: 19/19<br>choices: 6/6<br>metric names in the engine's docs/dashboards: 36/36 |
| TGI | 3.3.7 | `v3.3.7` | 2025-12-18 | 54 | 53 | 27 | flags found: 54/54<br>no spurious flags: 54/54<br>env names: 54/54<br>defaults: 20/20<br>choices: 6/6<br>metric names in the engine's docs/dashboards: 36/36 |
| Ollama | 0.33.0 | `v0.33.0` | 2026-08-25 | 0 | 70 | 0 | env vars found: 23/23 |
| Ollama | 0.34.0 | `v0.34.0` | 2026-09-09 | 0 | 70 | 0 | env vars found: 23/23 |
| Ollama | 0.35.0 | `v0.35.0` | 2026-09-28 | 0 | 71 | 0 | env vars found: 23/23 |
| Ollama | 0.40.0 | `v0.40.0` | 2026-10-06 | 0 | 72 | 0 | env vars found: 23/23 |
| Ollama | 0.40.16 | `v0.40.16` | 2026-11-07 | 0 | 72 | 0 | env vars found: 23/23 |
<!-- /generated:coverage-detail -->

## What each check compares

| Check | Meaning |
| --- | --- |
| flags found | Share of the flags in the engine's reference that the extractor found (recall). |
| no spurious flags | Share of extracted flags that the reference also lists (precision). |
| aliases/negations | The full set of spellings (`-tp`, `--no-x`, `--disable-x`) is identical. |
| defaults | The default is identical, where both sides state one. |
| choices | The list of accepted values is identical, where the reference lists one. |
| env names | The environment variable bound to the flag is identical. |
| rendered help incl. defaults | llama.cpp only: the help text rendered from `string_format(...)` and the struct defaults equals the generated README text, for options whose help could be rendered completely. |
| metric names in the engine's docs/dashboards | Every metric name mentioned in the engine's own usage docs and example dashboards at that tag resolves to a metric in the database (after stripping `_bucket`, `_sum`, `_count`, `_total`). |

## Known disagreements

Every disagreement found by the validator is listed here. The ones we looked
into:

- **vLLM `--mm-processor-device`, `--all2all-backend`, `--collect-detailed-traces` choices**: the accepted values are assembled at run time (platform device name, a registry), so the static list is incomplete.
- **llama.cpp b9000**: the README at that tag predates a rename of the speculative-decoding flags in `arg.cpp` at the same tag (for example `--hf-repo-draft` gained `--spec-draft-hf`), so the README, not the extraction, is behind. The 26 "extra" flags at that tag are the same effect.
- **llama.cpp "extracted but not in reference"** at other tags: flags registered in `arg.cpp` that the README table had not been regenerated for.
- **SGLang 0.5.18 - 0.5.21**: four flags are documented on the hand-written page but no longer exist in source at those tags.
- **SGLang 0.5.22 / 0.5.23**: `--boundary-reduction` and `--flashinfer-a2a-dispatch-type` are declared as CLI fields in `arg_groups/fields/exec_.py` but appear neither on the documentation page nor in SGLang's list of undocumented flags. We could not determine without running SGLang whether they are registered; they are kept in the database.
- **SGLang help-text agreement** is low by construction: the documentation page paraphrases the help strings. It is reported for completeness and says little about the extractor.
- **Stale metric names in engine docs**: `sglang:time_per_output_token_seconds` (0.5.18 - 0.5.21) and `llamacpp:kv_cache_tokens`, `llamacpp:kv_cache_usage_ratio` (b9000) are mentioned in the docs at those tags but are not defined anywhere in the source at the same tag.

<!-- generated:known-gaps -->
- **vLLM 0.28.0**
  - --mm-processor-device: choices differ
  - --collect-detailed-traces: choices differ
- **vLLM 0.29.0**
  - --mm-processor-device: choices differ
  - --collect-detailed-traces: choices differ
- **vLLM 0.30.0**
  - --mm-processor-device: choices differ
  - --collect-detailed-traces: choices differ
- **vLLM 0.31.0**
  - --mm-processor-device: choices differ
- **vLLM 0.32.0**
  - --all2all-backend: choices differ
  - --mm-processor-device: choices differ
- **vLLM 0.33.0**
  - --all2all-backend: choices differ
  - --mm-processor-device: choices differ
- **SGLang 0.5.18**
  - in the reference but not extracted: `--custom-sigquit-handler`, `--debug-tensor-dump-inject`, `--hybrid-kvcache-ratio`, `--optimistic-prefill-retries`
  - metric names in the engine's docs that the source at this tag does not define: `sglang:time_per_output_token_seconds`, `sglang:time_per_output_token_seconds_bucket`, `sglang:time_per_output_token_seconds_count`, `sglang:time_per_output_token_seconds_sum`
- **SGLang 0.5.19**
  - in the reference but not extracted: `--custom-sigquit-handler`, `--debug-tensor-dump-inject`, `--hybrid-kvcache-ratio`, `--optimistic-prefill-retries`
  - metric names in the engine's docs that the source at this tag does not define: `sglang:time_per_output_token_seconds`, `sglang:time_per_output_token_seconds_bucket`, `sglang:time_per_output_token_seconds_count`, `sglang:time_per_output_token_seconds_sum`
- **SGLang 0.5.20**
  - in the reference but not extracted: `--custom-sigquit-handler`, `--debug-tensor-dump-inject`, `--hybrid-kvcache-ratio`, `--optimistic-prefill-retries`
  - metric names in the engine's docs that the source at this tag does not define: `sglang:time_per_output_token_seconds`, `sglang:time_per_output_token_seconds_bucket`, `sglang:time_per_output_token_seconds_count`, `sglang:time_per_output_token_seconds_sum`
- **SGLang 0.5.21**
  - in the reference but not extracted: `--custom-sigquit-handler`, `--debug-tensor-dump-inject`, `--hybrid-kvcache-ratio`, `--optimistic-prefill-retries`
  - metric names in the engine's docs that the source at this tag does not define: `sglang:time_per_output_token_seconds`, `sglang:time_per_output_token_seconds_bucket`, `sglang:time_per_output_token_seconds_count`, `sglang:time_per_output_token_seconds_sum`
- **SGLang 0.5.22**
  - extracted but not in the reference: `--boundary-reduction`, `--flashinfer-a2a-dispatch-type`
- **SGLang 0.5.23**
  - extracted but not in the reference: `--boundary-reduction`, `--flashinfer-a2a-dispatch-type`
- **llama.cpp b10000**
  - --checkpoint-min-step: help text differs
  - --tools: help text differs
- **llama.cpp b12000**
  - --reasoning-preserve: help text differs
- **llama.cpp b12323**
  - --reasoning-preserve: help text differs
- **llama.cpp b9000**
  - --split-mode: help text differs
  - --hf-repo-draft: spellings ['--hf-repo-draft', '--spec-draft-hf', '-hfd', '-hfrd'] != ['--hf-repo-draft', '-hfd', '-hfrd']
  - --hf-repo-draft: env LLAMA_ARG_SPEC_DRAFT_HF_REPO != LLAMA_ARG_HFD_REPO
  - --cache-type-k-draft: spellings ['--cache-type-k-draft', '--spec-draft-type-k', '-ctkd'] != ['--cache-type-k-draft', '-ctkd']
  - --cache-type-k-draft: env LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K != LLAMA_ARG_CACHE_TYPE_K_DRAFT
  - --cache-type-v-draft: spellings ['--cache-type-v-draft', '--spec-draft-type-v', '-ctvd'] != ['--cache-type-v-draft', '-ctvd']
  - --cache-type-v-draft: env LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V != LLAMA_ARG_CACHE_TYPE_V_DRAFT
  - --override-tensor-draft: spellings ['--override-tensor-draft', '--spec-draft-override-tensor', '-otd'] != ['--override-tensor-draft', '-otd']
  - --cpu-moe-draft: spellings ['--cpu-moe-draft', '--spec-draft-cpu-moe', '-cmoed'] != ['--cpu-moe-draft', '-cmoed']
  - --cpu-moe-draft: env LLAMA_ARG_SPEC_DRAFT_CPU_MOE != LLAMA_ARG_CPU_MOE_DRAFT
  - --n-cpu-moe-draft: spellings ['--n-cpu-moe-draft', '--spec-draft-n-cpu-moe', '--spec-draft-ncmoe', '-ncmoed'] != ['--n-cpu-moe-draft', '-ncmoed']
  - --n-cpu-moe-draft: env LLAMA_ARG_SPEC_DRAFT_N_CPU_MOE != LLAMA_ARG_N_CPU_MOE_DRAFT
  - metric names in the engine's docs that the source at this tag does not define: `llamacpp:kv_cache_tokens`, `llamacpp:kv_cache_usage_ratio`
<!-- /generated:known-gaps -->
