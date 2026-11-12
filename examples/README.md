# Examples

Inputs used by the README, the docs and the golden tests. Run any of them
from the repository root.

| File | What it is | Try |
| --- | --- | --- |
| `vllm-serve.sh` | vLLM command line | `engine-migrate translate --from vllm --to sglang examples/vllm-serve.sh` |
| `vllm-docker.sh` | `docker run` line | `engine-migrate translate --from vllm --to llamacpp examples/vllm-docker.sh` |
| `vllm-deployment.yaml` | Kubernetes Deployment | `engine-migrate translate --from vllm --to sglang examples/vllm-deployment.yaml` |
| `vllm-config.yaml` | vLLM `--config` file | `engine-migrate translate --from vllm --to sglang examples/vllm-config.yaml` |
| `tgi.env` | TGI environment file | `engine-migrate translate --from tgi --to vllm examples/tgi.env` |
| `sglang-launch.sh` | SGLang command written for 0.5.18 | `engine-migrate diff sglang 0.5.18 0.5.23 --config examples/sglang-launch.sh` |
| `llamacpp-server.sh` | llama-server command written for b9000 | `engine-migrate diff llamacpp b9000 b12323 --config examples/llamacpp-server.sh` |
| `grafana-dashboard.json`, `alert-rules.yaml` | vLLM dashboard and alert rules with deliberate mistakes | `engine-migrate lint-metrics --engine vllm --to 0.31.0 examples/grafana-dashboard.json examples/alert-rules.yaml` |
| `sglang-rules.yaml` | SGLang rules using a metric name from old documentation | `engine-migrate lint-metrics --engine sglang examples/sglang-rules.yaml` |

The model names are placeholders for illustration; nothing here is downloaded.
