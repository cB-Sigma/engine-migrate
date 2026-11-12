llama-server -m /models/qwen3-8b-q4_k_m.gguf -c 16384 -np 4 -ngl 99 \
  --mlock --webui --metrics --api-key sk-local --host 0.0.0.0 --port 8080
