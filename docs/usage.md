# Usage

Engines: `vllm`, `sglang`, `llamacpp` (also `llama.cpp`), `tgi`, `ollama`.
Versions are those listed by `engine-migrate versions`; `v0.33.0` and
`0.33.0` are both accepted, and the newest version is the default.

<!-- engine-migrate: versions -->
```text
engine     version  tag date    flags  env/params  metrics
---------  -------  ----------  -----  ----------  ----------------------------------------
llama.cpp  b9000    2026-05-01  239    123         11
llama.cpp  b10000   2026-07-14  243    128         11
llama.cpp  b11000   2026-09-16  255    142         16
llama.cpp  b11500   2026-10-08  257    150         16
llama.cpp  b12000   2026-10-29  261    155         16
llama.cpp  b12323   2026-11-12  265    157         16
Ollama     0.33.0   2026-08-25  0      70          0
Ollama     0.34.0   2026-09-09  0      70          0
Ollama     0.35.0   2026-09-28  0      71          0
Ollama     0.40.0   2026-10-06  0      72          0
Ollama     0.40.16  2026-11-07  0      72          0
SGLang     0.5.18   2026-08-20  488    0           142
SGLang     0.5.19   2026-09-03  503    0           144
SGLang     0.5.20   2026-09-18  494    0           150
SGLang     0.5.21   2026-09-29  512    0           152
SGLang     0.5.22   2026-10-13  521    0           161
SGLang     0.5.23   2026-10-28  531    0           169
TGI        3.0.2    2025-01-24  52     51          27
TGI        3.1.1    2025-03-04  52     51          27
TGI        3.2.3    2025-04-08  53     52          27
TGI        3.3.7    2025-12-18  54     53          27
vLLM       0.28.0   2026-08-24  284    295         66
vLLM       0.29.0   2026-09-08  292    302         66
vLLM       0.30.0   2026-09-21  300    302         67
vLLM       0.31.0   2026-10-02  311    308         72
vLLM       0.32.0   2026-10-17  316    316         89
vLLM       0.33.0   2026-10-31  320    318         99
```

## translate

```sh
engine-migrate translate --from ENGINE --to ENGINE [--from-version V] [--to-version V]
                         [--shape auto|command|docker|kubernetes|yaml|env]
                         [--format table|json|markdown] [--output-only] [--strict] CONFIG
```

`CONFIG` is a file, `-` (stdin) or the command line itself.

| Shape | Recognised as | Output |
| --- | --- | --- |
| `command` | anything else, e.g. `vllm serve ...`, `python -m sglang.launch_server ...`, `llama-server ...` | the target's launcher followed by one option per line |
| `docker` | starts with `docker run` / `podman run` | same Docker options; engine env vars (`-e`) translated; image replaced by `<engine-image>` |
| `kubernetes` | YAML containing a container with `args`, `command` or `env` | the same manifest with that container's `image`, `command`, `args` and engine `env` entries rewritten |
| `yaml` | a flat YAML mapping of option names (vLLM / SGLang `--config` files) | a flat YAML mapping with the target's option names |
| `env` | `KEY=value` lines (TGI, llama.cpp `LLAMA_ARG_*`, Ollama; Modelfile lines allowed) | env lines when the target has env forms for the options, otherwise a command line |

A Kubernetes example (`examples/vllm-deployment.yaml`), output only:

<!-- engine-migrate: translate --from vllm --to sglang --from-version 0.33.0 --to-version 0.5.23 --output-only examples/vllm-deployment.yaml -->
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qwen3
spec:
  replicas: 1
  selector:
    matchLabels:
      app: qwen3
  template:
    metadata:
      labels:
        app: qwen3
    spec:
      containers:
      - name: vllm
        image: <sglang-image>
        command:
        - python3
        - -m
        - sglang.launch_server
        args:
        - --model-path
        - Qwen/Qwen3-32B
        - --context-length
        - '32768'
        - --tp-size
        - '2'
        - --mem-fraction-static
        - '0.90'
        - --disable-radix-cache
        - --lora-paths
        - sql=/adapters/sql
        - support=/adapters/support
        - --enable-lora
        - --port
        - '8000'
        env:
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf
              key: token
        ports:
        - containerPort: 8000
        resources:
          limits:
            nvidia.com/gpu: 2
```

An env file for TGI translated to llama.cpp stays an env file:

<!-- engine-migrate: translate --from tgi --to llamacpp --from-version 3.3.7 --to-version b12323 examples/tgi.env -->
```text
# tgi 3.3.7 -> llamacpp b12323  (input read as: env)

LLAMA_ARG_HF_REPO=meta-llama/Llama-3.1-8B-Instruct
LLAMA_ARG_CTX_SIZE=8192
LLAMA_ARG_PORT=8080

## Report: 1 exact, 2 approximate, 4 no equivalent, 0 unknown

APPROXIMATE    MODEL_ID=meta-llama/Llama-3.1-8B-Instruct  ->  --hf-repo meta-llama/Llama-3.1-8B-Instruct
               tgi: Hugging Face model id or a local directory saved with save_pretrained. llamacpp:
               Hugging Face repository that contains GGUF files, optionally with a ":QUANT" suffix;
               other weight formats are not loaded.
               source: tgi@v3.3.7:launcher/src/main.rs:589
               target: llamacpp@b12323:common/arg.cpp:3062
NO EQUIVALENT  REVISION=main
               llama-server has no revision option; the GGUF file or repository tag selects the
               weights.
               source: tgi@v3.3.7:launcher/src/main.rs:594
NO EQUIVALENT  NUM_SHARD=2
               llama-server has no tensor-parallel degree; multi-GPU placement is controlled by
               --device and --split-mode.
               source: tgi@v3.3.7:launcher/src/main.rs:612
APPROXIMATE    MAX_TOTAL_TOKENS=8192  ->  --ctx-size 8192
               tgi: Per-request budget of input tokens plus max_new_tokens. llamacpp: Size of the
               prompt context of the server (0 = loaded from model); how it is shared between
               parallel slots depends on other options.
               source: tgi@v3.3.7:launcher/src/main.rs:695
               target: llamacpp@b12323:common/arg.cpp:1609
NO EQUIVALENT  MAX_CONCURRENT_REQUESTS=64
               No option of llamacpp b12323 is mapped to 'admission limit'.
               source: tgi@v3.3.7:launcher/src/main.rs:649
NO EQUIVALENT  TRUST_REMOTE_CODE=true
               llama-server never runs model-repository code, so there is nothing to switch.
               source: tgi@v3.3.7:launcher/src/main.rs:643
EXACT          PORT=8080  ->  --port 8080
               source: tgi@v3.3.7:launcher/src/main.rs:774
               target: llamacpp@b12323:common/arg.cpp:3339

## Also check

- Prometheus metrics: llamacpp b12323 has a switch for this (--metrics); tgi has none, so decide
  whether to add it.
```

Ollama is configured through environment variables and a Modelfile, so that
is what a translation to Ollama produces:

<!-- engine-migrate: translate --from vllm --to ollama --from-version 0.33.0 --to-version 0.40.16 --output-only examples/vllm-serve.sh -->
```text
OLLAMA_CONTEXT_LENGTH=32768
OLLAMA_NUM_PARALLEL=128
OLLAMA_HOST=0.0.0.0

# Modelfile
FROM Qwen/Qwen3-32B
```

## diff

```sh
engine-migrate diff ENGINE FROM TO [--config CONFIG] [--only options|metrics]
                    [--kind cli|env|param|modelfile ...] [--exit-code] [--format ...]
```

Without `--config`, all changes are listed (environment variables only with
`--kind env`):

<!-- engine-migrate: diff tgi 3.0.2 3.3.7 -->
```text
# tgi: 3.0.2 -> 3.3.7  (v3.0.2 .. v3.3.7)

## Options added (2)

name                            detail
------------------------------  ----------------------------------------
--graceful-termination-timeout
--prometheus-port
```

Rename evidence, strongest first:

| Evidence | Meaning | Applied to the suggested config |
| --- | --- | --- |
| `alias` | the old spelling is an alias of the new option (or vice versa) | yes |
| `field` | both flags are bound to the same config field in source | yes |
| `flag` | an environment variable follows a flag that was renamed | yes |
| replacement notice | the engine's deprecation metadata names the new flag | yes, when it is a plain flag-for-flag replacement |
| `help-similarity` | help texts are at least 82% similar; shown as `(inferred)` | never |

With `--config`, a llama.cpp example:

<!-- engine-migrate: diff llamacpp b9000 b12323 --config examples/llamacpp-server.sh -->
```text
# llamacpp: b9000 -> b12323, for this configuration  (input read as: command)

severity  option   what changes
--------  -------  ----------------------------------------
ERROR     --mlock  removed in b12323
INFO      --webui  primary name is now --ui; this spelling is still accepted

## Defaults that changed for options this configuration does not set (5)

option                change
--------------------  ----------------------------------------
--dry-penalty-last-n  default -1 -> 64
--spec-draft-n-max    default 16 -> 3
--spec-draft-p-min    default 0.75 -> 0.0
--spec-draft-poll     default 'same as --poll]' -> 'same as --poll'
--timeout             default 600 -> 3600

## Suggested configuration for llamacpp b12323

llama-server \
  -m /models/qwen3-8b-q4_k_m.gguf \
  -c 16384 \
  -np 4 \
  -ngl 99 \
  --webui \
  --metrics \
  --api-key sk-local \
  --host 0.0.0.0 \
  --port 8080

- --mlock was dropped from the rewritten config (removed in b12323).
```

## lint-metrics

```sh
engine-migrate lint-metrics --engine ENGINE [--to V] [--from V] [--strict] FILES...
```

Files ending in `.json` are read as Grafana dashboards (every `expr`, plus
`label_values(...)` variable queries), `.yaml` / `.yml` as Prometheus rule
files (also `PrometheusRule` resources), anything else as one expression per
line. Only metrics with the engine's prefix (`vllm:`, `sglang:`,
`llamacpp:`, `tgi_`) are checked; everything else is ignored.

| Kind | Severity | Meaning |
| --- | --- | --- |
| `unknown` | error | not defined in any version in the database |
| `not-in-version` | error | defined in other versions, not in the target |
| `removed` / `renamed` | error | with `--from`: existed there, gone (or probably renamed) in the target |
| `type` | error | e.g. `_bucket` of a counter, or the metric changed type |
| `label` | warning (error if the label was removed since `--from`) | a label matcher uses a label the metric does not have |

Exit codes: `0` clean, `1` errors (with `--strict` also warnings), `2` usage error.

## flags / metrics / versions

```sh
engine-migrate flags ENGINE [VERSION] [--grep TEXT] [--kind cli|env|param|modelfile] [--deprecated]
engine-migrate metrics ENGINE [VERSION] [--grep TEXT]
```

<!-- engine-migrate: metrics llamacpp b12323 --grep spec -->
```text
# llamacpp b12323 (b12323): 5 metric(s)

name                                                    type     labels    help
------------------------------------------------------  -------  --------  ----------------------------------------
llamacpp:n_decode_total                                 counter            Total number of llama_decode() calls, excluding
                                                                           speculative decoding and multimodal decoding
llamacpp:spec_decode_num_accepted_tokens_per_pos_total  counter  position  Accepted tokens per draft position
llamacpp:spec_decode_num_accepted_tokens_total          counter            Speculative: Total draft tokens accepted by the target
                                                                           model
llamacpp:spec_decode_num_draft_tokens_total             counter            Speculative: Total draft tokens generated
llamacpp:spec_decode_num_drafts_total                   counter            Speculative: Total speculative decoding verification
                                                                           steps
```

`--format json` prints the raw database records, including `source.path` and
`source.line`.

## Use from Python

```python
from engine_migrate import db, diffing, translate

result = translate.translate("vllm serve org/model --tensor-parallel-size 2", "vllm", "sglang")
print(result.output)
for item in result.items:
    print(item.status, item.source, "->", item.target)

changes = diffing.diff("sglang", "0.5.18", "0.5.23")
print(changes.summary())

option = db.load("vllm", "0.33.0").find("--max-model-len")
print(option.type, option.default, option.source_path, option.source_line)
```

The Python API is not yet stable.
