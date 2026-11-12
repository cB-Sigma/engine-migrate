docker run --gpus all --ipc=host -p 8000:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -e HF_TOKEN=$HF_TOKEN \
  vllm/vllm-openai:v0.33.0 \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --dtype bfloat16 \
  --max-model-len 16384 \
  --max-num-seqs 64 \
  --seed 7 \
  --trust-remote-code \
  --download-dir /root/.cache/huggingface
