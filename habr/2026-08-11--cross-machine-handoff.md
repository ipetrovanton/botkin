# Организация кросс-машинного handoff и уборка веток

> Дата начала: 2026-08-11 20:10
> Стек: Python 3.14, FastAPI, Alpine.js, SQLite, Ollama, git

## Постановка

Пользователь попросил залить изменения в `master` и одновременно придумать,
как переносить `.remember/`-файлы на другой компьютер, чтобы не терять
handoff-контекст и находки для Хабра. Нужно было объединить ранее готовую
ветку `feat/queue-and-lifestyle-recs` в `master`, почистить мёртвые ветки,
а затем зафиксировать понятный протокол переноса состояния.

Критерий успеха: `master` на GitHub синхронизирован, тесты проходят,
handoff-файлы отслеживаются git и есть инструкция/скрипт для их синхронизации.

## Контекст и ограничения

- Локальный `master` уже был впереди `origin/master` на 5 коммитов,
  а `feat/queue-and-lifestyle-recs` — впереди `master` ещё на 6 коммитов.
- В репозитории скопилось 11 локальных и 5 удалённых веток, уже влитых в `master`.
- `.remember/` был заигнорирован в `.gitignore` целиком, поэтому любые
  новые handoff-файлы не подхватывались git.
- В `.agents/` нет external интеграций — state переносится только через git.

## План

1. Fast-forward merge `feat/queue-and-lifestyle-recs` → `master`.
2. Push `master` на `origin`.
3. Удалить локальные и удалённые ветки, которые уже влиты в `master`.
4. Почистить remote-tracking и worktree-метаданные.
5. Придумать и внедрить протокол кросс-машинного handoff.
6. Зафиксировать фактуру.

## Ход работы

### Шаг 1: merge и push

```bash
git checkout master
git merge --ff-only feat/queue-and-lifestyle-recs
git push origin master
```

Прошло без конфликтов. В `master` попали 11 коммитов, включая новые
`queue.py`, `lifestyle_recommend.md`, `rag/context.py`, `defaults.json`,
обновлённый `web/app.js` и тесты.

### Шаг 2: уборка веток

Удалены локальные:
`feat/email-auth`, `feat/pipeline-speed-accuracy`, `feat/queue-and-lifestyle-recs`,
`feature/lab-results-recognition`, `feature/rag-health-sync`,
`feature/uncensored-med-llm`, `feature/ux-multiuser-cabinet`,
`feature/web-cabinet`, `fix/ocr-stability-accuracy`,
`refactor/architecture-cleanup`, `refactor/web-cabinet-quality`.

Удалены удалённые:
`origin/feat/email-auth`, `origin/feature/uncensored-med-llm`,
`origin/feature/ux-multiuser-cabinet`, `origin/feature/web-cabinet`,
`origin/refactor/web-cabinet-quality`.

После этого:

```bash
git remote prune origin
git worktree prune
```

### Шаг 3: кросс-машинный handoff

Решение: хранить `.remember/` и `habr/` прямо в `master`, добавить
`AGENTS.md`-секцию и автоматизировать коммит через `scripts/sync-handoff.sh`.

Скрипт `scripts/sync-handoff.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
MESSAGE="${1:-docs(handoff): session checkpoint}"
cd "$(dirname "$0")/.."
if [ -z "$(git status --porcelain .remember/ habr/)" ]; then
    echo "No handoff changes to sync."
    exit 0
fi
CURRENT_BRANCH=$(git branch --show-current)
git add -A .remember/ habr/
git commit -m "$MESSAGE"
git push origin "${CURRENT_BRANCH}"
```

### Сложность: `.remember/` был в `.gitignore`

Симптом: созданный `.remember/README.md` не попадал в `git status`,
и `sync-handoff.sh` сообщал `No handoff changes to sync`.

Причина: в `.gitignore` была строка `.remember/`.

Решение: заменить её на `.remember/last-stop.md` — единственный
автоматически генерируемый файл в папке, который не нужен в git.

```gitignore
# .remember/ tracks handoff files (remember.md, now.md, README.md) for cross-machine sync.
# Only last-stop.md is auto-generated and should stay ignored.
.remember/last-stop.md
```

## Архитектурные решения

### Решение: handoff-файлы в основном репозитории, а не в отдельном

- **Альтернатива A:** приватный `botkin-journal` с submodule — чистое разделение,
  но лишняя операция `git submodule update` на старте и риск рассинхрона.
- **Альтернатива B:** cloud-диск (iCloud/Dropbox) — удобно, но не версионируется
  вместе с кодом и не виден CI/партнёрам.
- **Выбрано:** handoff в `master`. Критерий — минимум трений: `git pull`
  на другой машине сразу даёт и код, и контекст.
- **Компромисс:** в `master` попадают служебные коммиты с `docs(handoff)`. Это
  приемлемо, пока репо приватное.
- **Когда пересмотреть:** если репо станет публичным и `.remember/` захочется
  скрыть — тогда submodule или отдельный приватный репозиторий.

## Итог

- `master` на `origin/master`: `c630294`.
- 11 локальных и 5 удалённых веток удалены.
- `git status` чистый, `ruff` чистый, `pytest -m "not llm"`: `613 passed`.
- Добавлены:
  - `.remember/README.md` — описание файлов и протокола,
  - `AGENTS.md` секция `11. Handoff и перенос состояния`,
  - `scripts/sync-handoff.sh` — команда для сохранения handoff и `habr/`.
- Не трогал: `stash@{0}` с незакоммиченными изменениями от
  `refactor/web-cabinet-quality` — его судьбу решает пользователь.

## Материалы

- Внутренние: `AGENTS.md`, `.remember/README.md`, `scripts/sync-handoff.sh`.
