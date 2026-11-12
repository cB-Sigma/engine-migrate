python3 -m sglang.launch_server \
  --model-path Qwen/Qwen3-32B-AWQ \
  --quantization marlin \
  --tp-size 2 \
  --mem-fraction-static 0.88 \
  --cuda-graph-max-bs 128 \
  --stream-output \
  --collect-tokens-histogram \
  --enable-metrics \
  --port 30000
