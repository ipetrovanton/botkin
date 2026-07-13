#!/usr/bin/env bash
# Ждёт появления всех целевых моделей в ollama list. Печатает по мере готовности.
set -u
targets=(
  "Qwen3.6-abliterated:27b"
  "Qwen3.6-abliterated:35b-a3b"
  "glm-4.7-flash-abliterated:q4_K"
  "JOSIEFIED-Qwen3:8b-health-q6_k"
  "deepseek-r1-32b-uncensored"
)
for i in $(seq 1 240); do
  have=$(ollama list)
  missing=0
  for t in "${targets[@]}"; do
    echo "$have" | grep -q "$t" || missing=1
  done
  ready=$(echo "$have" | grep -icE 'Qwen3.6-abliterated|glm-4.7-flash-abliterated|JOSIEFIED-Qwen3:8b-health|deepseek-r1-32b-uncensored')
  echo "[$(date +%H:%M:%S)] готово тегов: $ready/5"
  if [ "$missing" -eq 0 ]; then echo "ALL READY"; ollama list | grep -iE 'Qwen3.6|glm-4.7|JOSIEFIED|deepseek-r1-32b'; exit 0; fi
  sleep 60
done
echo "TIMEOUT"
