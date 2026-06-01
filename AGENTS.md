# Справочник Разработки и Локального Запуска (AGENTS.md)

Файл содержит зафиксированную информацию по архитектуре проекта, командам сборки, локального запуска и схеме ручного/автоматического тестирования.

---

## 🛠️ 1. Системные требования и окружение

- **OS**: Windows 10/11 с установленной WSL2 (Ubuntu).
- **Python**: `>= 3.12` на Windows-хосте и в WSL2.
- **Движок моделей**: Ollama, запущенный внутри WSL2.
- **СУБД**: SQLite 3 встроенная в Python.

---

## 🚀 2. Схема и команды локального запуска

Для полноценной работы проекта и исключения сетевых конфликтов Windows <-> WSL2, запуск разделен на две части:

### Шаг А. Подготовка и запуск моделей (внутри WSL2)
1. Убедитесь, что служба Ollama запущена внутри WSL2 (Ubuntu) и слушает на всех интерфейсах (`OLLAMA_HOST=0.0.0.0:11434`):
   ```bash
   # Выполнить внутри WSL2 или как root-WSL с Windows-хоста
   sudo sed -i '/ExecStart/i Environment="OLLAMA_HOST=0.0.0.0:11434"' /etc/systemd/system/ollama.service
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```
2. Проверьте скачанные модели:
   ```bash
   ollama list
   # Ожидаются: qwen3-vl:8b, huihui_ai/qwen3-abliterated:14b, bge-m3:latest
   ```

### Шаг Б. Запуск бэкенда и Telegram-бота (на Windows-хосте)
1. Установите зависимости и пакет:
   ```powershell
   uv sync
   ```
2. Запустите FastAPI API-сервер:
   ```powershell
   uv run uvicorn botkin.api.app:app --host 0.0.0.0 --port 8000
   ```
3. В отдельном окне терминала запустите Telegram-бота:
   ```powershell
   # Убедитесь, что в .env прописан TG_BOT_TOKEN
   uv run python -m botkin.bot.main
   ```

---

## 🤖 3. Инструкция по проверке функционала через Telegram-бота

После запуска бота, пользователь может выполнить следующие сценарии тестирования:

1. **Активация и старт**:
   - Отправьте боту команду `/start`. Бот автоматически зарегистрирует вас и покажет приветствие.
2. **Загрузка выписки / рецепта (Анализы и Назначения)**:
   - Отправьте боту PDF-документ или фото бланка (например, `sample_001.pdf` или `sample_030.jpg`).
   - Бот примет документ и запустит фоновую обработку.
3. **Просмотр результатов и аналитики**:
   - **/last** или **/show**: Показывает результаты обработки последнего загруженного документа. Бот выведет тип (Анализы 🧪 или Рецепт 💊), дату, а также структурированный список показателей (показатели анализов с маркерами нормы ⬇️/⬆️, или названия препаратов с дозировками и длительностью).
   - **/dynamics <название_показателя>**: Бот сгенерирует PNG-график динамики этого показателя на `plotly`, отрендерит референсный коридор нормы зеленым цветом и пришлет картинку. (Например, `/dynamics гемоглобин`).

---

## 🧹 4. Администрирование и очистка дедлоков (WSL2 / Ollama)

Если во время инференса тяжелых моделей (Qwen3-14B или Qwen3-VL) произошли сетевые зависания или прерывания, в фоне WSL2 могут зависнуть зомби-процессы питона, держащие сокеты. Для полной очистки выполните:
```powershell
# Убить фоновые процессы Python в WSL
wsl -u root -d Ubuntu pkill -f python3

# Перезапустить Ollama для очистки VRAM и очереди
wsl -u root -d Ubuntu systemctl restart ollama
```

---

## 📁 5. Структура проекта

```
botkin/
├── src/botkin/              # Пакет (устанавливаемый через uv/pip)
│   ├── api/                 # FastAPI-приложение
│   │   ├── app.py           # Точка входа сервера
│   │   ├── deps.py          # Зависимости (get_user_id)
│   │   └── routes/          # Роуты
│   │       └── upload.py    # POST /upload
│   ├── bot/                 # Telegram-бот (aiogram)
│   │   ├── main.py          # Точка входа бота
│   │   └── handlers/        # /start, /help, /show, /dynamics, upload
│   ├── db/                  # База данных
│   │   ├── connection.py    # Подключение, init_db
│   │   ├── schema.sql       # DDL-схема (5 таблиц)
│   │   ├── queries.py       # Аналитические запросы
│   │   └── repos.py         # Репозитории (DocumentRepo, UserRepo)
│   ├── domain/              # Доменные модели
│   │   └── models.py        # LabResult, Prescription, DoctorReport, etc.
│   ├── llm/                 # VLM-интеграция (qwen3-vl)
│   │   ├── client.py        # Ollama OpenAI-совместимый клиент
│   │   ├── classify.py      # Классификация документа
│   │   ├── extract.py       # Извлечение данных
│   │   └── prompts.py       # Все VLM-промпты
│   ├── pipeline/            # Пайплайн обработки
│   │   ├── orchestrator.py  # classify → extract → persist
│   │   └── notifications.py # Telegram-уведомления
│   ├── viz/                 # Визуализация
│   │   └── plots.py         # Plotly-графики динамики
│   ├── config.py            # Централизованная конфигурация
│   └── exceptions.py        # Типизированные исключения
├── tests/                   # Тесты
│   ├── conftest.py          # Фикстуры
│   └── test_smoke.py        # 9 smoke-тестов
├── config.json              # Переопределения конфигурации
├── pyproject.toml           # Зависимости, entry points, tool config
├── .env.example             # Шаблон переменных окружения
├── AGENTS.md                # Этот файл
├── LICENSE                  # MIT
└── README.md
```

### Индексы Базы Данных для Оптимизации Производительности:
Для ускорения SQL-запросов в схему `src/botkin/db/schema.sql` добавлены индексы:
- `idx_documents_user` на `documents(user_id)`
- `idx_documents_status` на `documents(status)`
- `idx_lab_user_analyte` на `lab_results(user_id, analyte_name, taken_at)`
- `idx_presc_user_mnn` на `prescriptions(user_id, drug_mnn)`
- `idx_doctor_reports_user` на `doctor_reports(user_id, visit_date)`
- `idx_doctor_reports_document` на `doctor_reports(document_id)` — оптимизирует `/show`