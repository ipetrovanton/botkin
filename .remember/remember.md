# Handoff: release/habr-article — Qwen vs Gemma e2e benchmark завершён

**Ветка:** `release/habr-article`
**Статус:** structured audit + humanize пройдены для всех трёх пациентов (Петров А.И., Петрова И.И., Саулина И.И.).

## Итоги benchmark (structured audit → humanize)

| Пациент | Модель | structured audit | total_ids | invalid_ids | citation_ratio | cited_lines/claim_lines |
|---|---|---|---|---|---|---|
| Петров А.И. | Qwen 35B-a3b | passed | 98 | [] | 0.643 | 36/56 |
| Петров А.И. | Gemma 26B-QAT | passed | 98 | [] | 0.303 | 10/33 |
| Петрова И.И. | Qwen 35B-a3b | passed | 13 | [] | 0.500 | 9/18 |
| Петрова И.И. | Gemma 26B-QAT | passed | 13 | [] | 0.615 | 8/13 |
| Саулина И.И. | Qwen 35B-a3b | passed | 347 | [] | 0.800 | 20/25 |
| Саулина И.И. | Gemma 26B-QAT | passed | 346 | [] | 0.412 | 7/17 |

## Ключевые изменения

- `scripts/bench/structured_audit.py`: `num_ctx` 8192→16384, `num_predict` 4096→8192, `lab_batch_size` 16→32; добавлена `model_audit_config` (Gemma получает `num_predict=12288` и batch 16) и `batch_audit_json_schema` с `minItems`/`maxItems` + `enum` для `evidence_ids`, чтобы Gemma не галлюцинировала лишние assertions.
- `scripts/bench/run_e2e_patient_audit.py`: добавлен `--lab-batch-size`; `passed` теперь считается `score.passed and not error`.
- Ollama systemd unit: включены `OLLAMA_KV_CACHE_TYPE=q8_0` и `OLLAMA_FLASH_ATTENTION=1`, что ускорило Qwen на Саулиной с ~3 мин/batch до ~1 мин/batch.

## Тесты

- `uv run ruff check src tests scripts/bench` — чисто.
- `uv run pytest tests/test_structured_audit.py tests/test_run_e2e_patient_audit.py tests/test_score_e2e_report.py tests/test_summarize_patient.py tests/test_deep_model_benchmark.py` — 24 passed.
- Полный `pytest` — 7 pre-existing Windows-only failures (`/tmp/a.jpg` не существует на Windows), 652 passed.

## Следующий шаг

- Зафиксировать результаты в `habr/2026-08-13--e2e-patient-benchmark.md`.
- По желанию: сделать commit/PR ветки `release/habr-article`.

# Handoff: release/habr-article (после rebase на origin/master)

**Ветка:** `release/habr-article` (HEAD `c2fa21c`, перебазирована на `origin/master` `a567c15`)
**Локальный master:** `a567c15` (синхронизирован с origin/master)

**Что сделано в этой сессии (2026-08-12):**
- `git fetch --all --prune`: origin/master +97 коммитов (e812763→a567c15), 378 файлов.
  Удалены remote-ветки feature/web-cabinet, refactor/web-cabinet-quality.
  Новая remote-ветка: devin/1752755000-review-p0-restructure (1 коммит поверх master).
- `git stash push` правок статьи + build_habr_pdf.py перед rebase.
- `git pull --ff-only origin master` на локальном master — без конфликтов.
- `git rebase origin/master` на release/habr-article — 1 коммит af097c2, конфликты в
  .gitignore, pyproject.toml, uv.lock, habr/botkin-habr-article.md. Разрешены в пользу
  master (--ours). Уникальные файлы сохранены: DEPLOY-MAC.md, .gitattributes,
  LFS-фикстуры sample_001-020.pdf, data/botkin.db, benchmark-methodology.md.
- `git stash pop`: конфликт в habr/botkin-habr-article.md (стеш поверх старой 3-строчной
  версии vs полная статья из master). Разрешён в пользу master. Stash@{0} сохранён.
- `uv sync` (Python 3.14.5), `git lfs pull` (фикстуры скачаны).
- `uv run pytest -m llm -s`: 36 passed, 8 failed, 4824.76s (1:20:24).
  - test_e2e_llm.py: 34/35 PASS, 1 FAIL (sample_023.jpg — VLM не дочитала diagnosis).
  - test_e2e_reasoning.py: 0/9 PASS (TimeoutError на Qwen3.6-abliterated:27b с CPU-оффлоадом).
- Фактура: habr/2026-08-12--rebase-and-e2e-run.md.

**Состояние:**
- build_habr_pdf.py в индексе (незакоммичен).
- Статья habr/botkin-habr-article.md — версия из master (полная).
- Stash@{0}: WIP правки статьи + build_habr_pdf (старая редакция, может не понадобиться).
- Untracked: дампы tests/_dump_*, scripts/_*, новые фикстуры sample_021-035.jpg,
  tests/fixtures/documents/samples/unsupported/.

**Следующий шаг после перезапуска Devin от администратора:**
1. Проверить, что сессия elevated: `[WindowsPrincipal]...IsInRole(Administrator)` → True.
2. Повторить чтение LibreHardwareMonitorLib.dll через Windows PowerShell 5.1: CPU Package
   temperature/clock/package power должны стать числовыми, а не пустыми/0.
3. Измерить 5-минутный idle-baseline и overhead sampler; модели пока не запускать.
4. Затем TDD: immutable fact package → telemetry sampler → benchmark harness.

**Последняя подзадача (2026-08-12): sample_023.jpg e2e-flake исправлен.**
- Причина: sample_023 — перевёрнутое фото ЭКГ; `prepare_images()` не корректировал 180°.
  До фикса: серия 5 запусков = 4 FAIL / 1 PASS; VLM пропускала экстрасистолию и гипертрофию.
- Фикс: `prepare_report_images()` возвращает для растрового doctor_report исходный и
  180°-повёрнутый JPEG; для PDF — исходные страницы. `run_doctor_report()` использует
  этот путь, prompt требует выбрать читаемую ориентацию и не дублировать поля.
- Изменены: src/botkin/preprocess/images.py, src/botkin/llm/extract.py,
  src/botkin/llm/prompts/doctor_report.md, tests/test_preprocess_images.py.
- Проверки: test_preprocess_images.py 11 passed; test_prompts.py 4 passed; ruff clean;
  целевой e2e + 5 независимых повторов = 5/5 PASS (9.15–10.85s).
- Фактура дополнена: habr/2026-08-12--rebase-and-e2e-run.md.

**Текущая задача (2026-08-12): benchmark Qwen/Gemma/MedGemma, подготовка.**
- План одобрен: Qwen3.6-35b-a3b; Gemma4-31B q4_K_M; Gemma4-26B QAT 16GB;
  MedGemma27B q4_K_M. На модель: 1 FACT_AUDIT + 3 CLINICAL_SYNTHESIS (seed 42/43/44).
- Фактура: habr/2026-08-12--deep-model-benchmark.md.
- LibreHardwareMonitor 0.9.6 и PawnIO 2.2.0 установлены через winget; драйвер PawnIO RUNNING.
- Elevated-сессия подтверждена; LHM читает CPU package temperature/power/clocks, GPU и EC RPM.
  Чистый baseline 300.17 с: CPU mean/p95/max 80/93/97°C, package power 28.7/38.3/46.4 W;
  RTX mean 32.5 W, 0% GPU/0 MiB VRAM, P0. Модели НЕ скачивались, inference НЕ запускался.
- Найден источник простаивающей dGPU: `Devin.exe` PID 22652 держал GPU 0/3D на 29%; RTX была
  P0, 32.09 W, 64°C при 0% CUDA utilization. Для `Devin.exe` установлено `GpuPreference=1;`
  в HKCU; нужен ручной перезапуск Devin, сессию не завершали.
- На текущей Balanced-схеме Turbo Boost отключён через `PERFBOOSTMODE=0` для AC и DC;
  `/qh` подтвердил AC/DC=0. BIOS: AdaptiveThermalManagementAC/Battery=Balanced,
  CoolQuietOnLap=Disable. Direct fan override не делали: EC `0x2F` не имеет подтверждённого
  безопасного контракта. Два telemetry-run после изменения пользователь прервал, новых чисел нет.
- После перезапуска Devin подтверждён iGPU-переход: PID 8888 работает на LUID FE21 с shared
  memory и 0 dedicated bytes. RTX LUID 102C0 содержит только System, nvcontainer и ollama;
  `ollama ps` пуст, 0 MiB VRAM/0% util, но P0=30.26 W/59°C сохраняется. Причина этого
  driver/CUDA baseline не доказана; активных игр и иных пользовательских RTX-процессов нет.
- Игровые launchers не закреплять за RTX. При появлении конкретного game executable задать только
  ему `GpuPreference=2;`; iGPU для обычных приложений уже подтверждён на Devin.
- Изолирующая диагностика P0 выполнена: после stop обоих Ollama-процессов +45 с RTX осталась
  P0 30.24 W/58°C/0 MiB/0%; после restart NVDisplay.Container — P0 31.18 W. BIOS подтверждает
  `GraphicsDevice=SwitchableGfx`; PCIe ASPM=2 (Maximum Power Savings) на AC/DC. `nvidia-smi -pl 80`
  не поддержан в текущем WDDM scope, хотя default TGP=80 W, а dynamic ceiling ~94.64–98.44 W.
- Пользователь выбрал Lenovo OEM driver. DS551306 `n40da26w.exe` (signature Valid, SHA256 в now.md)
  установлен после reboot: RTX driver 32.0.15.7391 / `oem35.inf`, PnP Status=OK, Code=0.
  Переустановка не устранила P0: 29.16 W, 57°C, 0% / 0 MiB.
- DCH NVIDIA Control Panel отсутствовала, вызвав диалог «панель управления не найдена». Установлена
  штатно из msstore через `winget`: package `9NF8H0H7WMLT`, app 8.1.969.0, `nvcplui.exe` запускается.
- DCH Control Panel подтверждает, что выбор GPU управляется Windows, и не содержит Global
  `Power management mode`; ранняя гипотеза о доступном NVIDIA Global profile опровергнута.
  После закрытия панели +60 с RTX P0 27.54 W/49°C, 0%/0 MiB, 1245/6000 MHz; только Devin на iGPU,
  RTX user-processes отсутствуют. P0 не устраняется OEM-драйвером, закрытием панели, Ollama или
  NVDisplay.Container и находится ниже user-mode профилей.
- Elevated baseline после OEM: 302 sample / 300.96 s / errors=[]; CPU package mean/P95/max
  58.8/61/62°C и 12.9/14.6/23.0 W против прежних 80/93/97°C и 28.7/38.3/46.4 W.
  Clock P95/max=2611.2/2611.3 MHz, то есть Turbo cap=0 удерживает nominal 2.6GHz. Цель
  устранить CPU thermal throttling от сети без подставки достигнута.
- RTX проблема автономна: P0, 1245/6000 MHz, 29.32 W mean (P95 30.88), 57.0°C mean,
  0% P95 utilization/0 MiB P95 VRAM, 2.451 Wh за baseline. OEM уменьшил mean с 32.5 до
  29.3 W, но не вывел dGPU в low-power. Не повторять OEM install/Global profile/DRS/EC.
- PnP test прошёл: 90 с `Disable-PnpDevice` для RTX дали CM_PROB_DISABLED, `nvidia-smi` не видит
  driver, Intel сохранил 3840×2400; `finally` вернул RTX в Status=OK/CM_PROB_NONE, P0=28.76W.
  Это рабочий обратимый обход P0, не удаляющий драйвер/BIOS/EC. Не применять с внешним монитором,
  активной игрой/AI или remote session на RTX.
- Авто-watch по utilization небезопасен: игра/Ollama могут стартовать с кратким 0% GPU load. Пользователь
  выбрал явные toggles; добавлен `scripts/rtx_power.ps1` with `Status|Disable|Enable`. Safety gate
  блокирует disable при NVIDIA display или loaded Ollama model (bypass только `-Force`). Проверен
  реальный scripted Disable→Enable: CM_PROB_DISABLED/nvidia-smi unavailable → Status OK/CM_PROB_NONE,
  OEM 32.0.15.7391. RTX сейчас включена. Windows PowerShell 5.1 parsing fixed by ASCII-only text.
- Использование: `powershell -ExecutionPolicy Bypass -File .\scripts\rtx_power.ps1 -Action Disable -Confirm:$false`
  перед обычной работой; `... -Action Enable -Confirm:$false` перед игрой/Ollama; `Status` для проверки.
  Не отключать при внешнем дисплее, remote session, активной AI/игре.
- Hardware: i9-11950H 8C/16T, 48GB DDR4-3200 (16+32), RTX 3080 Laptop 16GB,
  Windows 11 Pro build 26200; C: ~230GB free.
- Рабочая ветка release/habr-article, HEAD c2fa21c. Изменения не коммитились.

**Исследование Turbo Boost (2026-08-12, без применения настроек):**
- Подтверждён хост Lenovo ThinkPad P1 Gen 4i 20Y3S0K900, CPU i9-11950H, BIOS N40ET53W 1.35
  (2026-03-30). Balanced active; `PERFBOOSTMODE=0` на AC и DC, `PROCTHROTTLEMAX=100`.
  Turbo выключен программно; CPU в снимке ровно 2611 MHz. BIOS WMI: `CPUPowerManagement=Enable`,
  `AdaptiveThermalManagementAC/Battery=Balanced`; настройки voltage/Undervolt Protection не объявлены.
- Ничего в Windows, BIOS или утилитах не менялось и ничего не устанавливалось. Intel XTU/ThrottleStop
  не установлены. VBS running; HVCI registry key отсутствует. Исследование завершено: официальный
  путь Turbo — `PERFBOOSTMODE=1` только на AC с workload-baseline; XTU 7.14 не поддерживает данный
  H/WM590, а ThrottleStop V/F tuning заявлен только для unlocked HX/K. Firmware WMI не содержит
  Undervolt Protection/offset. Не предлагать BIOS downgrade/UEFI variable bypass из-за SA-00289.
  Последняя повторная проверка: `PERFBOOSTMODE=0` AC/DC (2/2), `PROCTHROTTLEMAX=100` AC/DC (2/2),
  CPU=2611 MHz; системных записей не было. `git diff --check` сигнализирует CRLF trailing whitespace
  в старых незакоммиченных журналах (Iteration 35–36); эти чужие изменения не форматировались.
- TPL: PL1/PL2/Tau ограничивают sustained/short Turbo package power и не требуют undervolt, но
  фактические значения не прочитаны: BIOS WMI не публикует их, LHM sampler отдаёт только power,
  XTU/ThrottleStop не установлены. Не поднимать лимиты и не трогать MMIO Lock/FIVR lock/PL4/IccMax.
  Если пользователь разрешит: сначала read-only MSR+MMIO audit, затем Turbo AC baseline с default TPL;
  при перегреве уменьшать PL1/PL2 только в отдельном профиле.
- 2026-08-12: пользователь разрешил apply. Turbo включён только на AC: `PERFBOOSTMODE=1`, DC=0.
  ThrottleStop 9.7 portable: SHA256 (winget) совпал, Authenticode Valid; read-only TPL MSR=109/135/56,
  MMIO=109/109/56, lock flags off. 180s CPU-only safety baseline прерван за 2.464s при 95C:
  4619.99MHz max, 71.08W max, fan1/fan2 5184/4285 RPM, errors=[]. OEM limits для all-core Turbo
  слишком высоки. Попытка `30/40/8` через UI НЕ применилась (screenshot остался 109/135/56); Cancel,
  TS process и временная папка удалены. Не писать MSR напрямую/не обходить EC. Сейчас Turbo AC включён,
  TPL OEM; при длительной all-core нагрузке возможен почти мгновенный 95C thermal cap.
- Итерация apply завершена: пользователь вручную применял TPL 30/40/8, 45/70/8, 45/55/8 и 35/45/8
  через временный signed ThrottleStop 9.7. Единственный controlled all-core success — 30/40/8:
  180.97s/182 samples, 99.28% CPU, package 29.95W, temp P95=76C, но sustained clock P95=2498MHz.
  Все профили выше 30W достигали 95–97C уже при 15–20% фоновой CPU загрузке. Даже 30/40/8 в
  60s real-background preflight дал 94C P95/97C max. Поэтому AC Turbo возвращён в Disabled;
  финальный 60s cooldown: CPU package 63.97/67/68C и 13.99/16.88/17.75W, clock 2611MHz.
  ThrottleStop process/audit dir removed. Current safe state: PERFBOOSTMODE=0 AC/DC. Reconsider
  only after cooling/background CPU root cause is resolved and a new workload-equivalent baseline passes.

**Отдельный repo RTX Power Tray (2026-08-12):**
- Локальный repo: `C:\Sandbox\rtx-power-tray`, Git branch `main`, всё staged, commit ещё не создан.
  App: Python 3.12/PySide6 6.11.1, PyInstaller 6.15.0; MSI WiX v7 1.0.1. 4 pytest passed,
  compileall/ICE validation clean; final MSI установлен и работает: Program Files, common Start Menu,
  first-run HKCU Run, single-instance count stays 2 (PyInstaller parent/child).
- Core PnP Disable→Enable с UAC проверен из Python. `dist` ignored; MSI SHA256
  `820FA2DF3954342C227307EC130BBDFFD9241BB3D67EFA17DEC380DE08CA7394`, intentionally unsigned.
  Source and EXE/MSI scans found no `chatgpt/openai/gpt/devin/generated by/artificial intelligence` strings.
- Publication blocked: git has no `user.name`/`user.email`; do NOT configure it. GitHub MCP create_repository
  failed connection twice; local `gh` has no auth. Need user provide identity/one-shot author values and recover
  GitHub MCP or run `gh auth login`, then commit/push public `ipetrovanton/rtx-power-tray` and publish MSI release.
- Публикация завершена после `gh auth login`: https://github.com/ipetrovanton/rtx-power-tray,
  `main`/`origin/main` на 7ef7b89. Release https://github.com/ipetrovanton/rtx-power-tray/releases/tag/v1.0.3
  содержит unsigned `RtxPowerTray-1.0.3.msi` (44,732,416 bytes, SHA256
  `944BC0CB6E6656AAAC2B7BA8C6CA37E8C26E947CBE9C4CE1CBC2E10DA424739F`).
- После feedback пользователя устранено мигание PowerShell/cmd: status timer каждые 5 с вызывал
  powershell/nvidia-smi/ollama без `CREATE_NO_WINDOW`. Версия 1.0.2 добавляет flag ко всем subprocess,
  unit test проверяет его. MSI 1.0.3 собран/ICE validated/установлен (`msiexec=0`); tray running,
  Program Files/Start Menu/HKCU Run сохранены. В UAC Start-Process добавлен WindowStyle Hidden.
  Source trace scan снова пуст. Local commit `7ef7b89` создан one-shot Botkin Dev identity; no Git config change.
  GitHub remote по-прежнему не создан: MCP create_repository failure x4, local gh no auth.

**Benchmark Qwen/Gemma/MedGemma (2026-08-12) завершён.**
- Temporary Ollama server `127.0.0.1:11435`: q8 KV-cache, Flash Attention, max one model, stopped after run.
- Fact package: SHA256 `c04efae696a780716ba8500fd697fe285974e6fae42fc7ae0655ea271f3081bf`;
  318 labs, 87 series, 9 reports, 29 meds, 209 health aggregates, 16 activities, 20 RAG sources.
- Calibration tok/s (off4K/off8K/think8K): Qwen35 29.3/28.7/25.2; Gemma31 2.15/2.18/1.96;
  Gemma26 QAT 47.95/48.19/47.37; MedGemma27 3.89/—/4.17.
- Full q8 quality-run: Qwen35 synthesis 621.5s total, 19,824 chars; Gemma31 3,665.9s,
  10,599 chars; Gemma26 QAT 270.3s, 12,974 chars; MedGemma27 3,192.4s, 24,117 chars.
  All 3 seed synthesis runs per model completed; all marked thermally_constrained=true.
- Repeatability: token-set Jaccard Qwen .250, Gemma31 .385, Gemma26 .306, MedGemma .484;
  number Jaccard .644/.644/.648/.885; exact hashes 3/3 for all. Evidence bracket coverage 0/0:
  models did not follow required `[LAB:...]` citation format, so traceability needs a separate fix.
- Harness bug fixed during run: stream final chunk had empty content, `setdefault()` discarded accumulated
  chunks. TDD after fix: 21 passed, ruff clean; Qwen/Gemma outputs non-empty afterward.
- Artifacts: `benchmarks/deep_model_benchmark_q8_full/`, analyzer `scripts/bench/analyze_deep_model.py`,
  fact/telemetry modules and tests. Habr: `habr/2026-08-12--deep-model-benchmark.md` and lab journal iteration 39.
- Structured audit: `scripts/bench/structured_audit.py` + Pydantic FactAudit/golden scorer.
  Synthetic: 24 passed, ruff clean. Real package: Qwen 445.6s schema-valid but golden FAIL;
  Gemma31 626.5s schema-valid but empty assertions; Gemma26 160.0s same; MedGemma stopped
  after 42m as structured_output_failed (free content, no JSON). Fact/quality evidence recorded
  in Habr benchmark and lab journal iteration 40.
- Next: decompose audit into short domain prompts (labs/dates/medications), then rerun real audit.
  Current temporary Ollama 11435 is stopped; no model loaded.

**Системная задача 2026-08-13: ThrottleStop установлен и запущен.**
- `winget install --id TechPowerUp.ThrottleStop --exact` завершился успешно; версия `9.7.0.0`.
- Источник: TechPowerUp; winget сообщил `Successfully verified installer hash`.
- EXE SHA-256: `5846F38B6671DA8626A560BF1543EB496348AB11659E6EFA5E1ED6E9739C27E2`;
  Authenticode `Valid`, signer `TechPowerUp LLC`.
- Создан Desktop shortcut `ThrottleStop.lnk`; target — установленный EXE.
- Запущен PID `32672`, `Responding=True`. Настройки TPL/voltage не менялись.

**Structured domain audit decision (2026-08-13):**
- Synthetic domain batching: 27 passed, ruff clean.
- Qwen35 real domain audit completed: 324 valid evidence IDs, dates 9/9, contradictions 20/20,
  labs 10 matched + missing/mismatches, medications IDs present but strict schedule/raw mismatch,
  findings provenance 0. Artifacts: benchmarks/structured_audit_q8_domain/huihui_ai_Qwen3.6-abliterated_35b-a3b/.
- Gemma31 domain audit stopped by user after 2 lab batches (988s, 1226.7s) + date batch (141s),
  due CPU-offload cost. Future main comparison: Qwen35 + Gemma26 QAT.
- Temporary Ollama 11435 stopped; no model loaded.

**Per-patient e2e benchmark (2026-08-13):**
- Packages: Петров Антон Игоревич 20 docs/79 labs/11 reports/8 meds/209 Garmin aggregates/16 activities;
  Петрова Инна Игоревна 2 docs/13 labs/no Garmin; Саулина Инна Игоревна 12 docs/347 labs/no Garmin.
- Qwen35 wall: 474.2s / 350.4s / 316.9s; Gemma26 QAT: 470.1s / 244.8s / 331.1s.
- Garmin attached only to Petrov; weather absent in all packages; missing analysis dates kept missing.
- Guard rescore: all 6 `garmin_leak=false`, `weather_leak=false`, `passed_guards=true`; evidence citations in free-form outputs remain 0.
- Outputs: `benchmarks/e2e_patient_reports/`; builder/tests: `scripts/bench/e2e_patient_facts.py`,
  `scripts/bench/e2e_patient_benchmark.py`, `tests/test_e2e_patient_facts.py`.
- Strict Garmin audit: `scripts/bench/garmin_audit.py` + `scripts/bench/run_garmin_audit.py`.
  Package loader now preserves sleep `value_json`. Both Qwen35 and Gemma26 passed strict real
  audit: 225 valid HLT/ACT IDs, 209/209 metrics, 29/29 sleep phase objects, 16/16 activities,
  invalid IDs=0. Artifacts: `benchmarks/garmin_audit_strict3/`. Temporary Ollama stopped.
- Deterministic second step: `scripts/bench/summarize_garmin.py` generates only from passed audit;
  both models produced `verified_garmin_summary.md/.json`: sleep avg 7.48h/29d, HRV 32.72,
  resting HR 60.2, steps 5609.93, stress 36.1, Body Battery 63.17, activities 45738.17s /
  67658.72m / 10556 calories. Tests: 8 passed, ruff clean.
