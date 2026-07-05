#!/usr/bin/env bash
# Последовательная загрузка выбранных СВЕЖИХ uncensored-моделей в Ollama (WSL2).
set -u
models=(
  "huihui_ai/Qwen3.6-abliterated:27b"
  "huihui_ai/Qwen3.6-abliterated:35b-a3b"
  "huihui_ai/glm-4.7-flash-abliterated:q4_K"
  "goekdenizguelmez/JOSIEFIED-Qwen3:8b-health-q6_k"
)
for m in "${models[@]}"; do
  echo "=== PULL $m ==="
  ollama pull "$m" || echo "!!! FAILED: $m"
done
echo "=== DONE base set ==="
ollama list
