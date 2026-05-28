# Полное руководство по настройке Ollama с GPU в WSL2

Инструкция подходит для настройки Ollama с NVIDIA GPU поддержкой в WSL2 для ML-стека.

---

## Шаг 1. Проверка NVIDIA драйвера и GPU passthrough

Вся работа с GPU в Ollama строится на сквозном доступе (pass-through) из Windows в WSL2.

1. **Проверьте драйвер в Windows:**
   ```powershell
   nvidia-smi
   ```
   *Должна показаться RTX 3080 с версией драйвера ≥570*

2. **Проверьте GPU доступ в WSL2:**
   ```bash
   nvidia-smi
   ls /usr/lib/wsl/lib/libcuda.so*
   ```
   *Должны быть видны те же данные, что и в Windows, и библиотеки CUDA*

> ⚠️ **Важно:** Никогда не устанавливайте CUDA Toolkit в WSL2 отдельно! Он наследуется из Windows через `/usr/lib/wsl/lib/libcuda.so`.

---

## Шаг 2. Установка зависимостей

Ollama требует zstd для распаковки:

```bash
sudo apt-get update
sudo apt-get install -y zstd
```

---

## Шаг 3. Установка Ollama

Используйте официальный установщик:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

При успешной установке вы увидите:
```
>>> Installing ollama to /usr/local
>>> Adding ollama user to render group...
>>> Adding ollama user to video group...
>>> Adding current user to ollama group...
>>> Creating ollama systemd service...
>>> NVIDIA GPU installed.
```

Если **NVIDIA GPU installed** не появилось — Ollama не видит CUDA.

---

## Шаг 4. Проверка установки

```bash
ollama --version  # должно быть ≥ 0.5.x
ps aux | grep ollama  # должен быть запущен процесс ollama serve
```

---

## Шаг 5. Проверка GPU детекции

Загрузите тестовую модель и проверьте VRAM:

```bash
ollama pull tinyllama:latest
ollama run tinyllama --verbose "привет"
```

В другом терминале:
```bash
nvidia-smi
```
*Должен быть процесс `ollama` использующий VRAM (~700 MB)*

---

## Шаг 6. Загрузка рабочих моделей

```bash
# Chat + extract LLM (~9 GB)
ollama pull qwen3:14b

# Vision LLM для рукописи (~6 GB)  
ollama pull qwen3-vl:8b

# Embedding модель (~1.2 GB)
ollama pull bge-m3

# Проверка
ollama list
```

---

## Шаг 7. Smoke тесты производительности

Создайте директорию для тестов:
```bash
mkdir -p ~/projects/medknow/parsing
```

### Тест чата (smoke_test.py):
```python
import time, httpx, os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = "qwen3:14b"

def smoke_chat():
    prompt = "Перечисли три нормальных показателя гемоглобина у взрослой женщины."
    
    t0 = time.perf_counter()
    response = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
        timeout=120.0,
    )
    response.raise_for_status()
    elapsed = time.perf_counter() - t0
    data = response.json()
    
    tokens_per_sec = data.get("eval_count", 0) / (data.get("eval_duration", 1) / 1e9)
    print(f"Generation speed: {tokens_per_sec:.1f} t/s")
    
if __name__ == "__main__":
    smoke_chat()
```

### Тест эмбеддингов (smoke_embed.py):
```python
import time, httpx, os, math

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

def smoke_embed():
    chunks = ["Гемоглобин 145 г/л"] * 30
    
    t0 = time.perf_counter()
    vectors = []
    for chunk in chunks:
        r = httpx.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "bge-m3", "prompt": chunk},
            timeout=60.0,
        )
        r.raise_for_status()
        vectors.append(r.json()["embedding"])
    
    elapsed = time.perf_counter() - t0
    cps = len(chunks) / elapsed * 60
    print(f"Throughput: {cps:.0f} chunks/min")
    print(f"Dimension: {len(vectors[0])}")
    
    # NaN проверка
    for v in vectors:
        assert all(not math.isnan(x) for x in v), "NaN в embedding"
    print("✅ No NaN found")

if __name__ == "__main__":
    smoke_embed()
```

Запустите тесты:
```bash
python smoke_test.py    # Цель: ≥20 t/s
python smoke_embed.py   # Цель: ≥1000 chunks/min, 1024-dim
```

---

## Шаг 8. Измерение VRAM

После тестов проверьте использование памяти:
```bash
nvidia-smi
```

Ожидаемое на RTX 3080 16GB:
- Qwen3-14B: ~9GB
- BGE-M3: ~1.5GB  
- KV cache: ~1.5GB
- Итого: ~13GB / 16GB

---

## Шаг 9. Решение проблем

### Ollama не видит GPU:
```bash
# Проверьте логи
journalctl --user -u ollama | grep -i "gpu\|cuda\|error"

# Обновите WSL2
wsl --update
wsl --shutdown  # в Windows
```

### Модель работает на CPU (низкая скорость):
- Проверьте `nvidia-smi` во время генерации
- Если VRAM не растет — проблема с CUDA детекцией

### BGE-M3 дает NaN:
- Ограничьте chunks до 512 токенов
- Или используйте `nomic-embed-text` (768-dim)

### VLM hot-swap медленный:
- Используйте `keep_alive: 0` для выгрузки моделей
- Проверьте, что модель действительно выгружается

---

## Шаг 10. Документация результатов

Создайте `parsing/MODELS.md` с фактическими замерами:
- Размеры моделей на диске
- VRAM использование
- Throughput для каждой модели
- Проблемы и решения

---

## Типичные проблемы и их решения

1. **`nvidia-smi: command not found` в WSL2**
   - Решение: `export PATH=$PATH:/usr/lib/wsl/lib` в `~/.bashrc`

2. **Ollama пишет "no NVIDIA GPU detected"**
   - Причина: старый драйвер NVIDIA в Windows
   - Решение: обновить драйвер до версии ≥570

3. **Модель отвечает очень медленно (<5 t/s)**
   - Причина: модель работает на CPU
   - Решение: проверить CUDA детекцию в логах Ollama

4. **BGE-M3 throughput очень низкий**
   - Причина: особенность реализации в Ollama
   - Решение: оптимизировать размер chunks или использовать другую модель

5. **VLM swap занимает >2 минуты**
   - Причина: модель не выгружается из VRAM
   - Решение: использовать `keep_alive: 0` и проверять выгрузку

---

## Полезные команды

```bash
# Проверить статус Ollama
systemctl --user status ollama

# Перезапустить Ollama
systemctl --user restart ollama

# Остановить все модели
ollama stop --all

# Проверить доступные модели
ollama list

# Посмотреть логи Ollama
journalctl --user -u ollama -f
```
