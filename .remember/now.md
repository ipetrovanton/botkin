2026-08-17 — аудит состояния проекта на master.
- pytest: 659 passed, 44 deselected; ruff check src tests: clean.
- FastAPI + uvicorn работает; /health, статика, API аутентификации и загрузка документа до extracted — OK.
- UI-аудит Playwright: все экраны, кнопка камеры, мобильный viewport, загрузка через UI, темы — OK.
- Очистка выполнена: удалено 104 файла в корне (491 КБ), proposals/ (70 файлов), _ui_audit_screenshots/, scripts/_ui_audit.py.
- Оставлены bench_*.log и e2e_run.log (первичные данные статьи); data/botkin.db возвращён git restore.
- Логотип НЕ меняем — вариант со змеёй отклонён, proposals/ удалена.
- PNG во фронтенде не было: всё уже inline SVG. Заменены только эмодзи 🌤️/🧲/♈ на SVG-иконки.
- Отказы загрузки: accept="image/*" предлагал GIF/BMP, сервер их отвергал, а фронт показывал общий тост.
  Добавлен uploadErrorText(name, status) для 400/413/415 + константы MAX_UPLOAD_BYTES/SUPPORTED_FORMATS_LABEL.
  Побочно починен run_js в test_cabinet_web.py: не хватало encoding="utf-8" (cp1251 ломал кириллицу).
- После правок: 661 passed, ruff check src tests чист, node --check app.js OK.
- Фактура: habr/2026-08-17--frontend-audit-state.md; журнал обновлён итерацией 48.

2026-08-17 (2) | master | Погодный блок удалён целиком (итерация 49 журнала).
- Снесены: пакет external/ (weather+astrology), роут /api/external/today, reference/cities.py+json,
  эндпоинт /api/directory/cities, tests/test_external.py.
- Отвязаны: app.py, rag/context.py (_external_context), config.py (ExternalConfig + EXT_*),
  defaults.json (секция external), промпты rag_recommend.md и lifestyle_recommend.md.
- БД: TDD-миграция _drop_profile_coordinates() убирает latitude/longitude из patient_profile.
- Находка: пикер города был мёртвым — в ProfileRequest нет полей lat/lon, Pydantic их отбрасывал,
  координаты никогда не сохранялись, погода всегда шла по дефолтной Москве.
- scripts/bench/* НЕ трогали: их external.weather.available — guard от галлюцинаций для статьи.
- 643 passed, ruff чист, консоль браузера чиста. Сервер перезапущен на 127.0.0.1:8000.

2026-08-13 — e2e patient benchmark Qwen/Gemma, release/habr-article.
- Перезапущен Ollama с OLLAMA_KV_CACHE_TYPE=q8_0 + OLLAMA_FLASH_ATTENTION=1.
- structured_audit.py: lab_batch_size 16→32, num_ctx 8192→16384, num_predict 4096→8192.
- Qwen и Gemma structured audit пройдены для Петрова А.И. и Петровой И.И.
- Humanize (verified report → связный prose) работает; Qwen citation ratio 0.64–0.82, Gemma 0.30–0.62.
- В процессе: Qwen на Саулиной И.И. (347 labs), затем Gemma и humanize.
- Следующий шаг: собрать итоговое сравнение, прогнать ruff/pytest, обновить habr/lab-results-journal.md.

2026-07-03 — Итерация 27 (веб-кабинет) завершена. Ветка fix/ocr-stability-accuracy.
- Фронтенд: SPA в src/botkin/web/ (index.html, styles.css, app.js, vendor/alpine.min.js 3.15.12).
- Бэкенд: 11 новых /api/* роутов (documents.py, analytics.py), StaticFiles mount в app.py.
- Репозитории: DocumentRepo.search/distinct_clinics/date_range/stats, LabRepo.distinct_analytes,
  ReportRepo.distinct_doctors/for_period.
- Баг-фикс: LOWER() для кириллицы в SQLite — create_function("lower",...) в get_conn().
- Тесты: 341 passed (316 + 25 новых), ruff clean.

2026-07-03 (6) — GPU-бенчмарк завершён и закоммичен (44ba1e6). Ветка refactor/web-cabinet-quality.
3 модели на корпусе 34 доков:
- qwen3-vl:8b-instruct: 100% (325/325), 34/34 PASS, 25.7с/док
- glm-ocr: 81.8% (248/303), 28/34 PASS, 13.7с/док
- qwen2.5vl:7b: 76.3% (248/325), 27/34 PASS, 27.3с/док

## Чекпоинт: Task 3 HE4 — RED-тест написан, фикс не начат
RED-тест в tests/test_text_layer_extract.py::test_parse_text_line_he4_takes_first_threshold_and_clean_unit.
Тест падает: unit='пмоль/л Cobas 6000...' вместо 'пмоль/л', ref_high=140 вместо 70.
Файл НЕ закоммичен. Следующий шаг: фикс в text_layer.py → GREEN → коммит.

2026-07-04 — Итерация 34: новые модели qwen3.5:9b и GLM-4.6V-Flash-9B. Ветка refactor/web-cabinet-quality.
Baseline 3 моделей на RTX 3080:
- qwen3-vl:8b-instruct: 100% (34/34), 25.4с/док — эталон
- GLM-4.6V-Flash-9B: 98.5% (31/34), 98.0с/док — новый кандидат №2
- qwen3.5:9b: 74.1% (26/34), 125.4с/док — провал (thinking mode ломает vision)
Оптимизация:
- qwen3.5:9b thinking off: не помогло (3/5 FAIL), модель не пригодна для vision/OCR через Ollama
- GLM-4.6V без structured output: 751.5с/док (7.7× медленнее), XGrammar критически важен
- XGrammar двойственный эффект: критичен для большинства, но теряет 1-2 значения на отдельных
qwen2.5vl:7b исключена из покрытия (76.3%, слишком слабая).
Обновлено: docs/ocr-models-research-2026-07.md (Часть 4), habr/lab-results-journal.md (итерация 34).
Добавлено: VLM_DISABLE_THINKING env в client.py (для оптимизационных прогонов).
367 passed, ruff clean. Следующий шаг: коммит.
- 2026-07-28 20:19 | feat/email-auth | Валидация expected vs sample: найдено ~12 серьёзных рассинхронов (023,024,025,028,029,030,031 и др.)
2026-08-03 17:12 | master | e2e bench qwen3-vl 34/34 100% 17.6s; gemma4:latest 27/34 88%; gemma4:26b 26/34 80%; journal it37
2026-08-03 19:08 | feat/pipeline-speed-accuracy | phase1+3 ship: long_side=1600 e2e 34/34 13.3s; glm-ocr reject
2026-08-04 10:17 | feat/pipeline-speed-accuracy | commit 56e6309 pipeline speed e2e 34/34 13.3s
2026-08-04 12:09 | feat/pipeline-speed-accuracy | 97ef735 phase4/5 reject; phase6/7 ship e2e 34/34 13.4s
2026-08-09 10:15 | feat/queue-and-lifestyle-recs | queue+lifestyle ship: 611 passed, ruff clean, статья обновлена (13-14с, 640+ тестов)
2026-08-09 10:41 | feat/queue-and-lifestyle-recs | f7a58ef live run: 404.5s 27b, 2 боевых бага (bge-m3 404, sqlite3.Row.get), 613 passed
2026-08-09 — refactor web/app.js cabinet() into four module factories (documents/health/assistant/admin). node --check passes. /tmp/refactor_app_js.py removed.
2026-08-09 11:15 | feat/queue-and-lifestyle-recs | 2609879 refactor: defaults.json, rag/context.py, app.js modules; 613 passed, ruff clean, node --check OK
2026-08-11 | master | merged + pushed feat/queue-and-lifestyle-recs, deleted 11 local/5 remote stale branches, 613 passed, ruff clean
2026-08-12 13:51:54 | release/habr-article | deep-model benchmark paused before inference: plan approved; LHM 0.9.6 + PawnIO installed; restart Devin as Administrator, then verify CPU temp/package power and sample idle baseline
2026-08-12 14:52:06 | release/habr-article | thermal policy: `PERFBOOSTMODE=0` verified for AC+DC; `Devin.exe` set to `GpuPreference=1` (iGPU, applies after restart). Root cause dGPU idle: Devin PID 22652 GPU 0/3D 29%, RTX P0 32W. EC fan override deliberately not used; rerun 60–300s LHM baseline after restarting Devin.
2026-08-12 14:52:06 | release/habr-article | restarted Devin PID 8888 now uses iGPU LUID FE21, 0 dedicated memory; only System/nvcontainer/ollama map to RTX LUID 102C0. `ollama ps` empty, but RTX remains P0 ~30W/59°C with zero VRAM/util; needs separate diagnostics if it persists after exiting Ollama. No games active; only real game .exe should get `GpuPreference=2`, never launcher.
2026-08-12 15:03:54 | release/habr-article | P0 isolation complete: stop Ollama +45s left RTX P0 30.24W; restart NVDisplay.Container left P0 31.18W. BIOS SwitchableGfx, ASPM=2. `nvidia-smi -pl 80` unsupported WDDM scope; dynamic TGP ceiling ~95–98W vs 80W default. Next requires user decision: NVIDIA Global Power Management UI vs Lenovo OEM driver; do not edit DRS/EC.
2026-08-12 15:03:54 | release/habr-article | user selected Lenovo OEM driver. DS551306 `n40da26w.exe`: Lenovo CDN HEAD 200, 1,862,234,424 bytes, Last-Modified 2026-03-30. Current NVIDIA Game Ready driver=610.88. Download to Downloads pending; verify Authenticode and file version before launching installer; reboot will be needed.
2026-08-12 15:17:08 | release/habr-article | user confirmed all work saved and authorized install. `n40da26w.exe` verified: Lenovo signature Valid, version 32.0.15.7391, SHA256 256161E40648D86BA5B63ABFC66532FD3A8A4ADAB444BBAA94E7054896CF14AD. Starting official `/verysilent /norestart`; stopping Ollama to release CUDA. Await installer exit then reboot manually, remeasure P0.
2026-08-12 15:32:40 | release/habr-article | OEM NVIDIA installation reached PnP success: RTX 3080 Status=OK/Code=0, `oem35.inf`, 32.0.15.7391. Game Ready 32.0.16.1088 replaced. Self-extractor/setup cleanup remains; PendingFileRenameOperations true. Do not measure P0 until user manually reboots, then verify OEM version and LHM 60–300s baseline.
2026-08-12 15:33:17 | release/habr-article | user explicitly confirmed immediate reboot. Next session: verify `oem35.inf`/32.0.15.7391, wait 60s after login with no Ollama model, inspect RTX P-state/power and run LHM 60–300s baseline. Then update Habr step 11 with outcome.
2026-08-12 post-reboot | release/habr-article | OEM driver verified: RTX Status=OK/Code=0, 32.0.15.7391/oem35.inf. DCH NVIDIA Control Panel missing (dialog); installed Microsoft Store package 9NF8H0H7WMLT via winget, app version 8.1.969.0 launches. P0 persists 29.16W/57C zero VRAM; user must set NVIDIA Global Power management to Normal/Optimal, never Prefer maximum, then agent measures baseline.
2026-08-12 15:48:48 | release/habr-article | correction: Control Panel has no global Power management because it delegates GPU selection to Windows. Closed panel +60s: RTX P0 27.54W/49C, zero VRAM/util; only Devin on iGPU, no RTX user processes. OEM driver/Control Panel/Ollama stop/NVDisplay restart did not fix P0. User will restart Devin elevated; next: confirm elevation, LHM 60–300s baseline, then decide if supported PnP disable dGPU outside games/AI is acceptable.
2026-08-12 15:51:03 | release/habr-article | elevated confirmed=True post reboot; OEM 32.0.15.7391/oem35.inf active, Ollama ps empty, PERFBOOSTMODE AC/DC=0. Preflight RTX still P0 27.74W/50C, zero VRAM/util. 300s LHM baseline started; compare against prior CPU mean/p95/max=80/93/97C and GPU mean=32.5W.
2026-08-12 15:56:58 | release/habr-article | LHM baseline completed: 302/300.96s/errors=[]; CPU package 58.8/61/62C and 12.9/14.6/23W vs previous 80/93/97C & 28.7/38.3/46.4W. Turbo cap holds 2.611GHz nominal; CPU throttle goal achieved. RTX remains P0 29.32W mean/P95 30.88, 57C, no VRAM/util, 2.451Wh/5m. Need user choice: PnP disable dGPU outside games/AI vs keep 29W warm state.
2026-08-12 16:08:35 | release/habr-article | reversible PnP test successful: disable RTX 90s -> CM_PROB_DISABLED + nvidia-smi driver unavailable, Intel 3840x2400 stays; finally enable -> Status OK/CM_PROB_NONE, P0 28.76W returns. Need user policy for automation; do NOT use util-only watcher (race at game/Ollama startup).
2026-08-12 16:17:54 | release/habr-article | automation shipped: new scripts/rtx_power.ps1 (Status|Disable|Enable, known RTX instance ID, PnP safety gate for active NVIDIA display/loaded Ollama, -Force escape hatch). Actual scripted roundtrip passed: disabled CM_PROB_DISABLED/no nvidia-smi -> reenabled Status OK/CM_PROB_NONE OEM 32.0.15.7391. Script uses ASCII to remain parseable in Windows PS 5.1; RTX left enabled.
2026-08-13 | release/habr-article | user requested launch ThrottleStop and a Desktop shortcut. Search of standard paths and recursive search in user profile/Program Files found no ThrottleStop.exe. Prior temporary copy was removed per handoff. Next: ask whether to download the verified portable TechPowerUp/winget package; do not create a broken shortcut or launch absent executable.
2026-08-12 | release/habr-article | исследование Turbo Boost/undervolt завершено без применения: ThinkPad P1 Gen 4i 20Y3S0K900, i9-11950H, BIOS N40ET53W 1.35. Balanced: PERFBOOSTMODE AC=0/DC=0, PROCTHROTTLEMAX=100, CPU=2611 MHz; Turbo программно выключен. BIOS WMI: CPUPowerManagement=Enable, thermal Balanced; voltage/Undervolt Protection не экспонируется. Intel XTU 7.14 не поддерживает этот H/WM590; ThrottleStop V/F tuning — only unlocked HX/K. Не делать BIOS downgrade/UEFI bypass (INTEL-SA-00289). Если пользователь даст отдельное разрешение: включить только AC PERFBOOSTMODE=1, проверить powercfg, снять controlled telemetry baseline; DC оставить 0.
2026-08-12 16:53:29 | release/habr-article | separate `C:\Sandbox\rtx-power-tray` built: PySide6 tray GUI; core UAC PnP toggle, first-run HKCU startup, single-instance. WiX v7 MSI 1.0.1 built/ICE validated/installed: Program Files + common Start Menu + Run entry; 4 pytest passed; source and EXE/MSI no authorship trace strings. Public GitHub publish blocked only by absent git identity and GitHub remote auth/MCP connection; staged local repo has no commit.
2026-08-12 16:58:47 | release/habr-article | user saw flashing PowerShell/cmd: root cause status QTimer spawned powershell/nvidia-smi/ollama without CREATE_NO_WINDOW. Fixed in tray 1.0.2; test asserts creationflags; rebuilt/ICE validated/reinstalled MSI (`msiexec=0`). Tray startup verified 2 PyInstaller processes, no authorship scan matches. Git identity/remote still the only publication block.
2026-08-12 17:07:54 | release/habr-article | final tray 1.0.3 fixed disable UAC console flash too: parent CREATE_NO_WINDOW + elevated Start-Process/child PowerShell WindowStyle Hidden; 5 tests, ICE validation and install pass. Commit 7ef7b89 created one-shot with Botkin Dev identity; source/binaries trace scan empty. Publish pending: MCP create_repository connection failure x4, gh unauthorized.
2026-08-13 | release/habr-article | user authorized installation. `winget` installed `TechPowerUp.ThrottleStop` 9.7 from TechPowerUp; installer hash verified. EXE SHA256 `5846F38B6671DA8626A560BF1543EB496348AB11659E6EFA5E1ED6E9739C27E2`, Authenticode Valid, signer TechPowerUp LLC. Desktop `ThrottleStop.lnk` created; target verified. Process PID 32672 running/responding. No TPL/voltage changes.
2026-08-12 20:34:56 | release/habr-article | GitHub auth completed. Published public https://github.com/ipetrovanton/rtx-power-tray; origin/main=7ef7b89. Release v1.0.3 uploaded RtxPowerTray-1.0.3.msi (44,732,416 bytes, sha256 944bc0cb6e6656aaac2b7ba8c6ca37e8c26e947cbe9c4ce1cbc2e10da424739f). Local source/EXE/MSI trace scans empty; work complete.
2026-08-12 | release/habr-article | TPL research, no apply: PL1 is sustained and PL2/Tau burst Turbo power; current values unknown because Lenovo WMI exposes no limits, sampler only reads package power and tuning utilities are absent. Turbo remains off, so TPL raise cannot help. Later: read-only MSR/MMIO audit after consent, then Turbo AC baseline on OEM defaults; only lower PL1/PL2 in a separate profile if temperature requires it. No MMIO/FIVR locks, PL4/IccMax, or EC bypass.
2026-08-12 | release/habr-article | user explicitly authorized applying Turbo only on AC and controlled PL1/PL2 tuning. Preflight next: record AC/DC PERFBOOSTMODE and current clocks; inspect LHM CPU sensors for limits without installing a new tool; then apply AC PERFBOOSTMODE=1, baseline and tune only if telemetry supports it.
2026-08-12 | release/habr-article | Turbo applied: active Balanced has AC PERFBOOSTMODE=1, DC=0; powercfg verified. Pre-change LHM: CPU 2611 MHz, package 13.57 W/60C. Temporary signed ThrottleStop 9.7 archive SHA256 verified; read-only TPL window shows MSR PL1/PL2/Tau=109/135/56 W/s, MMIO=109/109/56; Disable Controls on and no locks. Next: 180s CPU-only baseline with current OEM limits; do not set any TPL until output is measured.
2026-08-12 | release/habr-article | OEM-Turbo safety baseline aborted automatically after 2.464s at CPU package 95C: max CPU clock 4619.99MHz, package power 71.08W, fans 5184/4285 RPM. This proves present 109/135W TPL unacceptable; target trial PL1/PL2/Tau=30/40/8. First GUI automation attempt only toggled temporary ThrottleStop controls; screenshot proves limits stayed 109/135/56, then Cancel and process stop restored read-only state. No TPL values were applied. Next: fresh temporary config and keyboard-message UI input test before any Apply.
2026-08-12 | release/habr-article | TPL automation stopped safely: fresh TS GUI could not reliably reopen/respond to automation; no TPL write occurred. Temp signed TS archive/process/dir removed. Current: Balanced PERFBOOSTMODE AC=1, DC=0; OEM TPL stays MSR 109/135/56 and MMIO 109/109/56. All-core safety baseline hits 95C in 2.464s at 71.08W; do not run sustained all-core Turbo without a supported cap. Next requires user decision: manually apply TS 30/40/8 under observation, or choose supported Windows max-frequency cap as alternative.
2026-08-12 | release/habr-article | user chose manual ThrottleStop TPL 30/40/8. Next: download verified portable TS, foreground UI; user changes fields under exact guardrails, applies but does not lock. Then agent screenshots MSR/MMIO and runs safety-gated baseline.
2026-08-12 | release/habr-article | user reports manual TPL Apply completed. Next: capture ThrottleStop MSR/MMIO screenshot to confirm 30/40/8, then run same 180s CPU-only test with automatic 95C abort. No other controls may be changed.
2026-08-12 | release/habr-article | TPL 30/40/8 baseline passed: 182 samples/180.97s/errors=[]/abort=null; CPU util 99.28%, package 29.95W mean/P95 30.00/max30.68, temp 75.55/76/78C; avg clock 2466MHz, P95=2498MHz, brief max=4519MHz. Thermal safe but PL1=30W does not beat nominal under all-core; next manual adjustment proposed 40/50/8, then same safety-gated 180s baseline. Turbo remains AC only, DC=0.
2026-08-12 | release/habr-article | user selected custom TPL. Parsed ThrottleStop profile raw RAPL: EAX0=0x001B8168 -> PL1=45W, EDX0=0x00428230 -> PL2=70W; same Tau=8s. SyncMMIO=1, all lock flags off. PP0 raw=0x230 without enable bit. Next: run 180s safety-gated all-core baseline (stop at package 95C).
2026-08-12 | release/habr-article | TPL 45/70/8 rejected by safety gate before workers: first telemetry sample CPU package=95C at 37.12W/4720.6MHz, CPU util17.1%, fan 3645/3131. No sustained benchmark result, workers terminated. Require immediate user choice: restore proven safe 30/40/8, or after cooldown trial intermediate 35/45/8. Do not leave 45/70/8 for routine all-core work.
2026-08-12 | release/habr-article | user changed PL2 to 55: TPL=45/55/8. 60s no-stress cooldown telemetry FAIL: 61 samples/59.98s/errors=[]; CPU util mean15.39%, package power mean/P95/max=35.12/46.38/49.33W; package temp=87.15/95/95C, core max=96C; fans 5162/4265 RPM. No all-core test started. Must immediately manually restore 30/40/8, the only verified safe TPL.
2026-08-12 | release/habr-article | user indicated restored but profile parses 35/45/8 (not 30/40/8). 60s no-stress preflight FAIL: 61 samples/59.98s/errors=[]; util mean20.32%, package power=35.15/41.64/43.47W, temp=83.70/93/95C, core max97C, fans=5180/4277. No all-core test. Must manually set exactly PL1 30, PL2 40, Time 8 and verify via raw EAX0=0x001B80F0/EDX0=0x00428140 before resuming.
2026-08-12 | release/habr-article | final TPL outcome: exact 30/40/8 raw confirmed, but 60s real-background preflight still 85.85/94/97C at only 15.4% CPU. AC Turbo reverted to PERFBOOSTMODE=0; post-revert 60s cooling=63.97/67/68C, 13.99/16.88/17.75W, locked 2611MHz. ThrottleStop process stopped and audit dir removed. Current safe state Turbo disabled AC/DC; no further TPL tuning until cooling/background root cause resolved.
2026-08-12 | release/habr-article | user explicitly requested Turbo back + ThrottleStop. Applied PERFBOOSTMODE AC=1/DC=0 and verified. Downloaded current stable TS 9.7 into temp with winget hash verification and Valid TechPowerUp signature; started PID 28796 and restored foreground. No TPL/voltage/lock changes made in this new run. Temp path C:\Users\ipetr\AppData\Local\Temp\ThrottleStop-open-12468.
2026-08-12 | release/habr-article | FIVR check: CPU Core selected, Unlock Adjustable Voltage checkbox cannot be enabled, offset stays 0.0mV. Native voltage interface is firmware-locked. Do not attempt BIOS downgrade/hidden UEFI variables/modified firmware; Intel XTU is unsupported for i9-11950H/WM590. Turbo stays AC=1/DC=0 by user's latest explicit request; TS remains open, no voltage/TPL write in current run.
2026-08-14 | master | Дизайн BOTkin: профиль исправлен по реальному портрету Крамского — смотрит вправо, очки, залысина, узкая борода; голова змеи слева от чаши и направлена вправо, тело послойно проходит спиралью и продолжается в B. Превью: proposals/direction-botkin.html; ruff чист; интеграция ждёт выбора пользователя.
2026-08-14 | master | Кобра доработана: открытая пасть с 3 клыками и языком, cobra_width() капюшон-горб smoothstep, слои тело-сзади→голова→чаша→B-спереди, первый виток за чашей. Грабли: пасть съедала собственная шея — решён S-изгибом затылка; угол головы задан явно (-24°). ruff чист.
2026-08-14 | master | Вариант 01 «наливная кобра-B» доведён: fmt 3 знака + 60 точек на сегмент, раздельные челюсти с прозрачной пастью, 3 клыка + 2 мелких зуба, зрачок-щель, 3 прорези на капюшоне, чаша с двойным ободом и бликом. Галерея перегенерирована; ruff чист. Превью: 127.0.0.1:55548/direction-botkin.html.
2026-08-14 | master | По фидбеку: убран внутренний обод чаши, убраны фронтальные сегменты змеи; чаша теперь рисуется последней и перекрывает тело кобры, которая проходит за неё. Голова остаётся видимой слева. ruff чист. Превью: 127.0.0.1:55548/direction-botkin.html.
2026-08-16 | master | Инструкция интернет-доступа завершена: docs/deploy-local-web.md (504 строки), рекомендуются Tailscale Serve или KeenDNS Password protected; прямой 8000 запрещён из-за legacy X-Telegram-User-Id. git diff --check целевого документа чист.
2026-08-17 | master | Контекст RTX Power Tray восстановлен: repo C:\Sandbox\rtx-power-tray clean, public origin ipetrovanton/rtx-power-tray, HEAD 7ef7b89. Installed v1.0.3 runs as expected two PyInstaller parent/child processes from Program Files with HKCU Run --minimized. Core: pinned RTX 3080 PnP ID; status poll 5s, UAC PnP Enable/Disable, blocks disable for active NVIDIA display or loaded Ollama. Current RTX enabled Code0 but P0 29.49W/56C, 0%/0MiB; no PnP action executed.
