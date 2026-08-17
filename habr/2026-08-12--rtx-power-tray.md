# Tray GUI и MSI для явного управления RTX

> Дата начала: 2026-08-12 16:17
> Стек: Python 3.12, PySide6 6.11.1, PyInstaller 6.15.0, WiX Toolset CLI 7.0.0, Windows 11

## Постановка

Выделить проверенный PnP-переключатель RTX из медицинского проекта в отдельную Windows-утилиту.
Она должна жить в tray, показывать состояние GPU, позволять включать и отключать RTX мышью,
создавать автозапуск и распространяться в MSI.

Критерий успеха: установленный MSI запускает один tray-экземпляр, создаёт ярлык меню «Пуск»,
на первом запуске включает current-user autostart и безопасно управляет проверенным RTX PnP ID.

## Контекст и ограничения

- Ноутбук: ThinkPad P1 Gen 4, RTX 3080 Laptop GPU, hybrid graphics.
- PnP disable уже проверен: Intel продолжает выводить 3840×2400, а RTX становится
  `CM_PROB_DISABLED`; включение возвращает `CM_PROB_NONE`.
- RTX нельзя отключать при активном дисплее NVIDIA или загруженной модели Ollama.
- .NET SDK отсутствует; выбран PySide6, официально поддерживающий Python 3.14, но uv выбрал
  доступный CPython 3.12.9 для сборки.
- Публичный installer остаётся неподписанным по решению пользователя; это означает возможное
  предупреждение Windows о publisher/reputation.

## План

1. Создать отдельный Python проект с controller, tray GUI и current-user startup.
2. Протестировать PnP safety gate и single-instance поведение.
3. Собрать standalone EXE через PyInstaller и MSI через WiX.
4. Установить MSI, проверить ярлык/Run entry и провести текстовый аудит исходников.
5. Создать публичный GitHub-репозиторий, commit, release с MSI и README.

## Ход работы

### Шаг 1: выбор и подготовка стека

- PySide6 6.11.1 установлен через uv; PyInstaller 6.15.0 и pytest 8.3.5 добавлены как dev dependencies.
- WiX Toolset CLI 7.0.0 установлен из WinGet.
- PyInstaller собрал `RtxPowerTray.exe` размером 45,034,504 bytes.

### Сложность: WiX OSMF и установка в user profile

- **Симптом:** WiX v7 остановил первую MSI-сборку с `WIX7015`: требовалось принять OSMF EULA.
- **Что пробовал:** условия не принимались автоматически; после явного подтверждения пользователя
  выполнен `wix eula accept wix7` для текущего профиля.
- **Решение:** в README добавлен самостоятельный шаг принятия EULA; build script не принимает
  условия за другого пользователя.
- **Урок:** лицензионное принятие нельзя скрывать внутри build script.

Первая per-user MSI прошла установку, но elevated `msiexec` зарегистрировал её в HKLM при файлах
в LocalAppData. Authoring заменён на честный per-machine MSI: EXE идёт в Program Files, а первый
запуск приложения создаёт HKCU Run entry. WiX ICE validation финального MSI проходит без output.

### Шаг 2: tray UI и безопасность PnP

- `RtxController` читает PnP status, версию драйвера, `nvidia-smi` telemetry и `ollama ps`.
- Перед disable приложение блокирует операцию при active NVIDIA display или loaded Ollama model.
- Операция выполняется в UAC-elevated PowerShell; реальный `Disable → Enable` дал
  `CM_PROB_DISABLED`/недоступный `nvidia-smi` и затем `CM_PROB_NONE`/OEM driver 32.0.15.7391.
- Qt local server не даёт повторному запуску создавать второй tray instance.

### Шаг 3: проверка сборки и установки

- `uv run pytest`: 4 passed.
- `python -m compileall`: успешно.
- Проверка standalone EXE: процесс запущен и отвечает.
- Проверка single-instance: первый запуск — 2 процесса PyInstaller parent/child; второй запуск
  сохранил count=2.
- Финальный MSI: `msiexec /i ... /qn /norestart` вернул 0; EXE в Program Files и общий Start Menu
  shortcut существуют; первый запуск создал HKCU Run command.

### Шаг 4: устранение мигания консольных окон

Пользователь заметил регулярно появляющиеся окна PowerShell/cmd. Причина: tray timer обновляет
status каждые 5 секунд и `RtxController` запускал `powershell.exe`, `nvidia-smi` и `ollama ps`
без creation flags. Во всех четырёх subprocess вызовах добавлен `CREATE_NO_WINDOW`; unit test
явно проверяет флаг на пути `nvidia-smi`.

MSI обновлён до 1.0.3. В elevated UAC-path добавлен `Start-Process -WindowStyle Hidden` и
`powershell.exe -WindowStyle Hidden`: UAC consent остаётся, но дочерняя консоль не должна мигать.
`pytest` снова: 5 passed; PyInstaller и WiX ICE validation успешны; `msiexec /i` вернул 0.
Установленный tray сейчас версии 1.0.3, с двумя ожидаемыми процессами PyInstaller parent/child
и HKCU Run entry. Проверка исключает код, который мог бы показывать консоль при status polling
или PnP action.

### Сложность: Git identity и удалённый GitHub repository

- **Симптом:** `git commit` первоначально завершился с `Author identity unknown`; `gh auth status`
  сообщает, что GitHub CLI не авторизован. Четыре попытки `github-mcp-server.create_repository`
  завершились connection failure.
- **Что пробовал:** author identity прочитана из локальной истории `botkin` (`Botkin Dev <dev@botkin.local>`);
  commit создан одноразовыми `git -c user.name/user.email`, без изменения Git config. Commit
  `7ef7b89` содержит исходники, инструкции и `uv.lock`; MSI/EXE не добавлены в Git.
- **Решение:** для remote всё ещё нужен восстановленный GitHub MCP либо `gh auth login`; локальный
  commit и артефакты готовы. Git config не менялся агентом.
- **Урок:** готовность исходников и build artifacts не заменяет настройку remote credentials для
  публичной публикации.

## Архитектурные решения

### Решение: explicit toggle вместо idle watcher

- **Альтернативы:** отключать RTX по нулевому utilization; выключать только на батарее; явные toggles.
- **Выбрано:** явные tray actions Enable/Disable с safety gate.
- **Компромисс:** пользователю нужно включить RTX до игры или GPU workload.
- **Когда пересмотреть:** после появления точного allowlist game executables или надёжного события
  старта Ollama workload.

### Решение: per-machine MSI и per-user startup

- **Альтернативы:** per-user MSI с файлами LocalAppData; per-machine autostart через service/task;
  per-machine MSI и HKCU Run от первого запуска.
- **Выбрано:** per-machine MSI + first-run HKCU Run.
- **Компромисс:** один ручной запуск после установки.
- **Когда пересмотреть:** если понадобится автозапуск до входа пользователя.

## Итог

- Исходники отдельной утилиты собраны в `C:\Sandbox\rtx-power-tray`; commit `7ef7b89` на `main`.
- MSI 1.0.3 валиден, установлен и проверен; SHA-256
  `944BC0CB6E6656AAAC2B7BA8C6CA37E8C26E947CBE9C4CE1CBC2E10DA424739F`.
- Публичный repository опубликован: https://github.com/ipetrovanton/rtx-power-tray
- GitHub Release `v1.0.3` содержит `RtxPowerTray-1.0.3.msi` (44,732,416 bytes) с тем же SHA-256.
- **Обновление 2026-08-17:** релиз `v1.0.4` устраняет зависание GUI при старте путём добавления
  таймаутов ко всем subprocess-вызовам; MSI SHA-256
  `5E53A0705EBD8D77000A682146D3B7C6CACBC252317D63CD8DE6DFDF4711421B`.
- Source и EXE/MSI прошли поиск по `chatgpt/openai/gpt/devin/generated by/artificial intelligence`: совпадений нет.

## Материалы

- https://doc.qt.io/qtforpython/release_notes/pyside6_release_notes.html — Python 3.14 support, обращение 2026-08-12.
- https://www.pyinstaller.org/en/stable/CHANGES.html — Python 3.14 support, обращение 2026-08-12.
- https://docs.firegiant.com/wix/osmf/ — WiX OSMF/EULA, обращение 2026-08-12.
- https://docs.firegiant.com/wix/tools/wixexe/ — `wix build`, обращение 2026-08-12.
- https://learn.microsoft.com/en-us/powershell/module/pnpdevice/disable-pnpdevice?view=windowsserver2025-ps — PnP device control, обращение 2026-08-12.

## Сессия 2026-08-17: восстановление контекста

Репозиторий подтверждён: `C:\Sandbox\rtx-power-tray`, public remote `ipetrovanton/rtx-power-tray`, последний commit `7ef7b89` (`Add RTX tray control application`), рабочее дерево чистое. Установленная версия 1.0.3 запущена из `C:\Program Files\RTX Power Tray\RtxPowerTray.exe`: два процесса — ожидаемая пара PyInstaller parent/child. HKCU Run entry содержит `"C:\Program Files\RTX Power Tray\RtxPowerTray.exe" --minimized`.

На момент проверки RTX PnP status=OK/Code=0, но `nvidia-smi` показал P0, 29.49 W, 56°C, 0% GPU и 0 MiB VRAM. Это тот самый idle draw, для которого создана утилита. Никаких Enable/Disable действий в этой сессии не выполнялось.

## Сессия 2026-08-17: иконка не появляется, приложение Not Responding

### Симптом

Пользователь сообщил, что RTX Power Tray автоматически не запускается. В трее иконки нет даже
после включения в «Другие значки панели задач». Проверка `HKCU\Run` показала, что запись
автозапуска корректна: `"C:\Program Files\RTX Power Tray\RtxPowerTray.exe" --minimized`.

### Диагноз

В `tasklist /v` обнаружились два процесса:

```text
RtxPowerTray.exe  20596  Running
RtxPowerTray.exe  21316  Not Responding
```

Приложение запускалось, но дочерний процесс зависал. В `MainWindow.__init__` первый вызов
`refresh_status()` идёт в GUI-потоке и синхронно дергает `RtxController.status()`, который
без таймаутов запускает `powershell.exe`, `nvidia-smi` и `ollama ps`. Если любой из этих
вызовов (особенно `ollama ps` при неответе WSL) зависал, GUI-поток блокировался до бесконечности,
и иконка не регистрировалась.

### Решение

- В `src/rtx_power_tray/controller.py` введены константы `QUERY_TIMEOUT_S = 10` и
  `ELEVATED_TIMEOUT_S = 60`.
- Все `subprocess.run()` обернуты `try/except subprocess.TimeoutExpired`.
- При таймауте `ollama ps` и `nvidia-smi` возвращаются безопасные пустые значения; при таймауте
  PowerShell бросается `ControllerError`, который `refresh_status()` уже ловит и показывает в UI.
- Версия поднята до `1.0.4` в `pyproject.toml`, `installer/product.wxs`, `scripts/build.ps1` и
  `README.md`.

### Проверка

- `uv run pytest`: 5 passed.
- `powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Clean`: PyInstaller + WiX ICE
  прошли успешно, создан `dist\RtxPowerTray-1.0.4.msi` (44,736,512 bytes).
- `msiexec /i "dist\RtxPowerTray-1.0.4.msi" /qn /norestart`: exit code 0; в
  `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall` отображается `RTX Power Tray 1.0.4`.
- Зависшие процессы убиты, приложение запущено вручную с `--minimized`: оба процесса
  `Responding = True`.
- Коммит `e720d82` в локальном репозитории `C:\Sandbox\rtx-power-tray`.
- GitHub push: `main` обновлён до `e720d82`.
- Release `v1.0.4` опубликован: https://github.com/ipetrovanton/rtx-power-tray/releases/tag/v1.0.4
- MSI `RtxPowerTray-1.0.4.msi` (44,736,512 bytes), SHA-256:
  `5E53A0705EBD8D77000A682146D3B7C6CACBC252317D63CD8DE6DFDF4711421B`.

### Урок

Tray-приложение не должно выполнять синхронные внешние вызовы в GUI-потоке без таймаутов.
Даже «быстрая» команда вроде `ollama ps` может зависнуть, если WSL/Ollama не отвечает,
и превратить приложение в `Not Responding`, скрыв иконку и сделав автозапуск бесполезным.
