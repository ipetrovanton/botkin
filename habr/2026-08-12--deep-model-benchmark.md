# Бенчмарк глубокого медицинского отчёта: Qwen, Gemma 4 и MedGemma

> Дата начала: 2026-08-12
> Статус: план одобрен, идёт подготовка телеметрии; прогоны не начаты
> Стек: Python 3.14, Ollama, SQLite, RTX 3080 Laptop 16 ГБ, Windows 11

## Постановка

Сравнить четыре локальные модели на комплексной суммаризации всей истории пациента:

1. `huihui_ai/Qwen3.6-abliterated:35b-a3b` (уже установлена, Q4_K_M);
2. `gemma4:31b-it-q4_K_M` (20 ГБ);
3. `gemma4:26b-a4b-it-qat` (16 ГБ, QAT-квант для практического запуска на 16 ГБ VRAM);
4. `medgemma:27b-it-q4_K_M` (17 ГБ).

Критерии: полнота и точность медицинских выводов, верность числам и источникам,
предсказуемость между повторами, время и токены, нагрузка CPU/GPU/RAM, температуры,
частоты, power draw/energy и доступные fan sensors.

## Контекст и ограничения

- Хост: Intel Core i9-11950H (8C/16T), 48 ГБ DDR4-3200 (16+32 ГБ),
  NVIDIA RTX 3080 Laptop 16 ГБ, Windows 11 Pro build 26200.
- Свободно на диске C: около 230 ГБ; три новых Q4/QAT-модели потребуют около 55 ГБ.
- `nvidia-smi` штатно отдаёт GPU utilization, VRAM, температуру, power draw,
  graphics/memory clocks и P-state; `fan.speed` на ноутбуке — `N/A`.
- Инвариантные WMI-классы отдают CPU utilization/effective frequency, RAM usage,
  paging и disk I/O. Стандартный ACPI CPU temperature sensor отсутствует.
- Точное полное потребление ноутбука от розетки программно не измеряется; без внешнего
  ваттметра можно посчитать GPU energy и, если сенсор доступен, CPU package energy.
- Raw-факты и полные ответы содержат медицинские данные: хранить локально в ignored
  benchmark-каталоге; в Хабр — только агрегаты и обезличенные цитаты.

## План

1. Установить и проверить аппаратную телеметрию до первого модельного прогона.
2. Реализовать детерминированный immutable fact package с ID источников и SHA-256.
3. Добавить TDD-проверки фактов, prompts, scorer и telemetry sampler.
4. Скачать новые модели; Qwen использовать первой, но официальный замер не совмещать
   с дисковой загрузкой моделей (параллельный запуск можно считать только pilot).
5. Гонять модели строго последовательно: stop всех моделей → cooldown → warmup →
   audit → три synthesis-повтора → stop → cooldown.
6. После каждого вызова атомарно сохранять raw output, metrics и telemetry summary.
7. Автоматически и вручную оценить factuality, coverage, hallucinations, citations,
   conflicts, Russian language, report structure и repeatability.
8. Опубликовать обезличенную сравнительную таблицу и выводы в фактуре.

## Ход работы

### Шаг 1: ревизия текущего бенчмарка

Существующий `scripts/bench/bench_health_report.py` уже последовательно выгружает модели,
стримит Ollama, фиксирует seed=42 и сохраняет output/thinking. Но вход сейчас строится
через `SELECT DISTINCT ... ORDER BY name`: теряется временная динамика, остаются OCR-дубли,
а старый `prompt_user.txt` содержит устаревшие ошибочные статусы референсов. Для нового
сравнения вход нужно сформировать заново детерминированным кодом и заморозить одним hash.

### Шаг 2: доступная телеметрия

Проверено без нагрузки:

- GPU: 0% utilization, 0 MiB VRAM, 70°C, 33.88 W, graphics 1245 MHz,
  memory 6001 MHz, P0; fan — N/A.
- CPU: i9-11950H, текущая частота WMI 2108 MHz, Processor Performance 161%,
  загрузка около 5% в момент снимка.
- RAM: 48 ГБ DDR4-3200, доступно около 29 ГБ, committed 34%.
- Disk: около 0% busy в момент снимка.

Высокие 70°C при 0% GPU и P0 означают, что абсолютный порог старта брать нельзя:
нужен baseline и условие термостабильности перед каждым прогоном.

### Шаг 3: выбор дополнительного сенсорного инструмента

Предложен LibreHardwareMonitor 0.9.6 (релиз 2026-02-14, MPL-2.0, активный проект):
CPU package/core temperature, package power, clocks, доступные motherboard/fan sensors.
Установка глобальная через winget требует подтверждения пользователя. Автоматизацию
строить через библиотеку/локальный сбор данных, не открывать web endpoint наружу.

Не выбраны:

- Intel Power Gadget — discontinued; Intel рекомендует прекратить использование из-за
  уязвимостей (INTEL-SA-01037).
- HWiNFO CLI logging — требует HWiNFO Pro.
- CodeGreen — интересен для EMI/NVML energy, но на Windows нет готового wheel и требуется
  сборка из исходников; проект новый и добавляет риск перед основным бенчмарком.

## Архитектурные решения

### Два независимых prompts на одном fact package

- `FACT_AUDIT`: строго структурированная проверка фактов и противоречий без свободной
  клинической прозы; нужна для автоматической factuality-оценки.
- `CLINICAL_SYNTHESIS`: развёрнутый отчёт с разделением «факт / интерпретация /
  гипотеза», обязательными evidence IDs и явным перечислением недостающих данных.
- Выход audit не подмешивается в synthesis: все модели получают один и тот же вход,
  иначе вход второй стадии зависел бы от качества первой.

### Изолированный инференс

- Никогда не загружать две модели одновременно.
- Перед моделью: `ollama stop` всех, проверка `ollama ps`, cooldown и стабильный baseline.
- Warmup и cold-load измерять отдельно; quality-run выполнять на прогретой модели.
- Загрузки моделей не учитывать как официальный inference-run из-за disk/CPU шума.

### Сложность: CPU MSR-сенсоры требуют elevation

LibreHardwareMonitor 0.9.6 и PawnIO 2.2.0 установлены через winget; kernel driver
`PawnIO` находится в состоянии RUNNING. Прямое подключение DLL из PowerShell 7 не
сработало из-за несовместимого конструктора `Mutex(..., MutexSecurity)`; Windows
PowerShell 5.1 (.NET Framework 4) библиотеку загрузил успешно.

Без elevation доступны CPU load, RAM и все GPU-сенсоры (core/hotspot temperature,
power, clocks, load, VRAM), но CPU temperature/clock пусты, CPU package power = 0.
Fan sensors Lenovo EC не обнаружены. Пользователь выбрал перезапуск Devin от
администратора; перед паузой контекст сохранён в `.remember/remember.md` и `now.md`.
Модели по-прежнему не скачивались и не запускались.

### Шаг 4: elevated-сенсоры и два вентилятора ThinkPad

После перезапуска elevated-сессия подтверждена. LibreHardwareMonitor 0.9.6 через
Windows PowerShell 5.1 начал отдавать CPU core/package temperature, per-core clocks,
CPU package/core/platform power. Первый снимок: CPU package 94°C, CPU package 31.07 W,
CPU platform 94.97 W; GPU core 70°C, hotspot 76.09°C, GPU package 33.99 W. Перед
benchmark необходим cooldown и термостабильный baseline.

Стандартные `Win32_Fan`, Lenovo WMI и LibreHardwareMonitor fan sensors пусты. Для
ThinkPad P1 Gen 4i найден и проверен read-only путь через уже установленный PawnIO:

- LHM содержит подписанный модуль `LpcACPIEC.bin` и публичный
  `WindowsEmbeddedControllerIO` с mutex `Global\\Access_EC`;
- tachometer — 16-bit little-endian EC `0x84/0x85`;
- bit 0 регистра `0x31` выбирает вентилятор 1/2;
- регистр управления скоростью `0x2F` не читается и не изменяется;
- selector всегда возвращается к исходному значению в `finally`.

Проверенный снимок: исходный selector `0x01`, fan1 5183 RPM, fan2 4096 RPM.
Старые TPFanControl/TVicPort/WinRing0 не устанавливались, Memory Integrity не менялась.

### Шаг 5: clean idle-baseline и thermal gate

После оптимизации sampler (LHM+nvidia-smi+EC, без WMI в 1-Hz loop) получено 301 sample
за 300.17 с. Hot path стабильный: 1 Hz, errors пусты; transient RPM >8191 и 1–999
отбрасываются, median fan1/fan2 = 3631/3113 RPM.

| Метрика | Mean | P95 | Max |
|---|---:|---:|---:|
| CPU package temperature | 80.0°C | 93°C | 97°C |
| CPU package power | 28.7 W | 38.3 W | 46.4 W |
| GPU temperature | 65.7°C | 67°C | 68°C |
| GPU power | 32.5 W | 33.4 W | 33.9 W |
| GPU utilization / VRAM | 0% | 0% | 0 MiB |

Это baseline без LLM в VRAM, но с активной IDE/Devin. В этих условиях CPU уже почти
достигает thermal limit. Пользователь решил запускать benchmark в текущем thermal состоянии:
preflight не блокирует модель, но каждый результат получает `thermally_constrained=true`.
Температуры, power, clocks, fan RPM и throttle flags — обязательная часть сравнения;
Lenovo Thermal Mode/Windows power plan агент не меняет.

### Шаг 6: источник простаивающей нагрузки RTX и граница безопасного управления

Пользователь подтвердил, что систему охлаждения уже обслужили, и разрешил изменить
обратимые настройки Windows для работы от сети: исключить постоянный Turbo Boost,
а GPU без LLM перенести на iGPU. Прямое управление EC-регистром скорости вентилятора
`0x2F` не используется: в текущем исследовании для него нет подтверждённого контракта.

Снимок через `Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine` показал
`pid_22652 ... phys_0 ... engtype_3D` с `UtilizationPercentage=29`. PID 22652 —
`C:\Users\ipetr\AppData\Local\Programs\Devin\Devin.exe`; на тот же момент
`nvidia-smi` показывал RTX 3080 в P0, 32.09 W, 64°C, 0% GPU utilization и
1245/6001 MHz. Поэтому нулевой CUDA-utilization не означает сон dGPU: UI-процесс
с 3D-очередью удерживает её активной.

Проверены Lenovo BIOS settings: `AdaptiveThermalManagementAC,Balanced`,
`AdaptiveThermalManagementBattery,Balanced`, `CoolQuietOnLap,Disable`; служба
`Lenovo Intelligent Thermal Solution Service` работает. Для P1 Gen 4 Lenovo
документирует связь Intelligent Cooling с Windows Power mode; Performance даёт
более высокую температуру и обороты, поэтому он не соответствует цели устранить
троттлинг без внешнего охлаждения.

### Шаг 7: применённый power cap и правило iGPU для IDE

После явного разрешения пользователя применены две обратимые политики текущей схемы
`Balanced` (`381b4222-f694-41f0-9685-ff5bb260df2e`):

```powershell
powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PERFBOOSTMODE 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR PERFBOOSTMODE 0
powercfg /setactive SCHEME_CURRENT
```

`powercfg /qh SCHEME_CURRENT SUB_PROCESSOR PERFBOOSTMODE` подтвердил `AC=0x00000000`
и `DC=0x00000000`: Turbo Boost отключён при обоих источниках питания, а
`PROCTHROTTLEMAX` сохранён на 100%, поэтому частота остаётся динамической, но не
должна подниматься выше номинального режима из-за boost. Microsoft документирует
значение `0` как Disabled.

Для `C:\Users\ipetr\AppData\Local\Programs\Devin\Devin.exe` записано пользовательское
Windows Graphics preference `GpuPreference=1;` (энергосбережение/iGPU). Настройка
подтверждена в `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`, но вступит в силу
только после перезапуска текущего Devin-процесса; сессию намеренно не завершали.

Проверка телеметрии после изменения была дважды прервана пользователем и результата
не дала. Поэтому нельзя заявлять численное снижение температуры до нового чистого
60–300-секундного baseline после перезапуска Devin. Прямой fan override не добавлен:
фирменный интерфейс допускает только выбранный BIOS режим Balanced, а не
подтверждённый контракт на запись EC `0x2F`.

### Шаг 8: распределение графических задач после перезапуска IDE

После перезапуска `Devin.exe` получил новый PID 8888 и использует GPU engine
`luid_0x00000000_0x0000FE21` только с shared memory: 25% в 3D-очереди,
0 bytes dedicated memory. Этот же адаптер обслуживает `dwm.exe`, `explorer.exe`
и другие desktop-процессы, поэтому правило `GpuPreference=1;` сработало — IDE
перенесена на iGPU.

У RTX (`luid_0x00000000_0x000102C0`) в snapshot остались `System`, `nvcontainer.exe`
и `ollama.exe`; `ollama ps` не показал ни одной загруженной модели, а у `ollama.exe`
было только 272 KiB shared memory. Активных игр и других пользовательских процессов
на RTX не найдено. Но `nvidia-smi` всё ещё показывает P0, 30.26 W, 59°C, 0 MiB VRAM
и 0% utilization: это нельзя приписать текущему inference или игре. Возможный
держатель — lifecycle CUDA/driver container, но причина 30 W не доказана.

Игровые launcher-процессы не переводятся на RTX: это разбудит dGPU без игры.
Для игры нужен отдельный `GpuPreference=2;` на исполняемый файл самой игры;
подходящий game `.exe` в текущих активных процессах отсутствует.

### Сложность: RTX остаётся в P0 без inference, UI и активного дисплея

- **Симптом:** RTX 3080 Laptop после 45 секунд без Ollama остаётся `P0`, 30.24 W,
  58°C, 0% utilization, 0 MiB VRAM и 1245/6001 MHz. `Display Active = Disabled`.
- **Гипотезы:** unloaded Ollama удерживает CUDA-контекст; IDE не применила iGPU;
  NVIDIA Display Container/overlay удерживает dGPU; BIOS принудил discrete graphics;
  завышен laptop Dynamic Boost или глобальный NVIDIA power profile.
- **Что пробовал:** остановлены оба `ollama` и `ollama app` процесса, ожидание 45 с,
  затем приложение восстановлено; `P0` не изменился. Перезапущена
  `NVDisplay.ContainerLocalSystem`; `P0` остался, 31.18 W. BIOS подтверждает
  `GraphicsDevice,SwitchableGfx`; PCIe ASPM уже `2` (Maximum Power Savings) для AC/DC.
  Попытка вернуть TGP к заводским 80 W окончилась:

  ```text
  Changing power management limit is not supported in current scope for GPU: 00000000:01:00.0.
  ```

  Текущий ceiling продолжает динамически меняться около 94.64–98.44 W при default 80 W;
  `nvidia-smi --power-limit=80` на WDDM-ноутбуке не может его записать.
- **Решение:** приложений-держателей RTX не найдено; прямой CLI power cap недоступен.
  Оставлены безопасные изменения: `GpuPreference=1` для Devin и `PERFBOOSTMODE=0`
  для CPU. Для дальнейшего устранения P0 требуется посмотреть глобальный NVIDIA
  Power management mode через NVIDIA Control Panel либо откатиться на OEM-драйвер Lenovo;
  оба действия не выполнялись без отдельного решения пользователя.
- **Урок:** `nvidia-smi` с нулевыми VRAM/utilization не доказывает спящий dGPU;
  GPU Engine, WMI process memory и изолирующая остановка сервиса нужны вместе.

### Шаг 10: подготовлен и проверен Lenovo OEM-драйвер

Пользователь выбрал переход с Game Ready 610.88 на Lenovo OEM-пакет. По официальному
`DS551306` получено имя пакета `n40da26w.exe`; CDN HEAD подтвердил `200 OK`, размер
1,862,234,424 bytes и Last-Modified `2026-03-30`. После загрузки проверены:

```text
FileVersion:     32.0.15.7391 (N40DA26W)
CompanyName:     Lenovo Group Limited
SignatureStatus: Valid
Signer:          CN=Lenovo, O=Lenovo
SHA-256:         256161E40648D86BA5B63ABFC66532FD3A8A4ADAB444BBAA94E7054896CF14AD
```

Это поддерживаемый пакет для P1 Gen 4 / RTX 3080 Laptop GPU; текущий драйвер был
32.0.16.1088 (Game Ready 610.88). Официальная инструкция Lenovo требует сохранить
и закрыть прочие приложения и запускает unattended install как
`n40da26w.exe /verysilent /norestart`; успешные возвратные коды с обязательной
перезагрузкой — 259 или 3010. Запуск отложен до подтверждения пользователя, что
несохранённых данных в открытых приложениях нет.

### Шаг 11: OEM-драйвер зарегистрирован, перезагрузка ожидается

Тихая установка запущена с `/verysilent /norestart` после остановки Ollama. Wrapper
не передал код завершения, потому что self-extractor оставил дочерние процессы
`n40da26w.tmp` и `setup.exe`; поэтому результат проверялся не по exit code, а через
PnP. В промежуточной фазе устройство временно было `Display`, Code 28. Затем
зарегистрирован новый OEM-INF:

```text
DeviceName:     NVIDIA GeForce RTX 3080 Laptop GPU
DriverVersion:  32.0.15.7391
DriverProvider: NVIDIA
InfName:        oem35.inf
Status:         OK
Code:           0
```

Таким образом, замена с Game Ready 32.0.16.1088 завершилась успешно. `setup.exe`
ещё выполняет финальную очистку; `PendingFileRenameOperations` непуст, а Lenovo
требует restart после установки. P0 нельзя измерять до перезагрузки: текущая
сессия содержит заменённые runtime-компоненты и невалидный baseline.

### Шаг 12: восстановлена NVIDIA Control Panel после DCH OEM-драйвера

После reboot PnP подтвердил OEM-драйвер `32.0.15.7391`, `oem35.inf`, GPU Status=OK,
Code=0. При этом пользователь увидел NVIDIA-окно «панель управления не найдена».
`Get-AppxPackage` у текущего пользователя не нашел `NVIDIACorp.NVIDIAControlPanel`,
то есть проблема была не в драйвере, а в отсутствующем DCH Store-приложении.

Проверен и установлен Microsoft Store package `NVIDIA Control Panel`:

```text
winget install --id 9NF8H0H7WMLT --exact --source msstore \
  --accept-source-agreements --accept-package-agreements --silent
Successfully installed
```

Пакет `NVIDIACorp.NVIDIAControlPanel`, version `8.1.969.0`, зарегистрирован и
запущен как `nvcplui.exe`. Это устраняет окно об отсутствии панели. После reboot
RTX ещё P0, 29.16 W, 57°C при 0% / 0 MiB; следующий контролируемый шаг — через
панель установить Global `Power management mode` в `Normal` / `Optimal Power`,
а не `Prefer maximum performance`, и повторить чистый baseline.

### Сложность: Power management mode недоступен в DCH панели P1 Gen 4

Пользователь подтвердил, что пункта Global `Power management mode` в открытой
NVIDIA Control Panel нет. UI Automation панели подтвердил текст о том, что Windows
теперь управляет выбором графического процессора, со ссылкой на Windows Graphics
settings. Следовательно, гипотеза о доступном глобальном NVIDIA профиле была
неверной для этой конфигурации; закрепление приложений через
`UserGpuPreferences` — правильный поддерживаемый путь.

Панель закрыта и спустя 60 с получен чистый снимок: RTX P0, 27.54 W, 49°C,
0% / 0 MiB, 1245/6000 MHz. GPU Engine показывает только `Devin.exe` на iGPU
(`phys_0`, shared memory); RTX-mapped user-processes отсутствуют. Переустановка
OEM-драйвера, закрытие Control Panel, остановка Ollama и restart NVIDIA service
не перевели dGPU в low-power state. Проблема лежит ниже прикладных профилей —
в driver/firmware runtime power state, без безопасной user-mode настройки для
принудительного сна dGPU.

### Шаг 13: чистый 300-секундный baseline после OEM-драйвера и power cap CPU

Elevated-сессия подтверждена; `oem35.inf` / 32.0.15.7391 активен, `ollama ps` пуст,
`PERFBOOSTMODE` равен 0 на AC/DC. TelemetrySession отработала 302 sample за 300.96 с,
errors пусты. В идентичном фоне с IDE/Devin результат CPU радикально лучше исходного
baseline до cap:

| Метрика | До: mean / P95 / max | После: mean / P95 / max |
|---|---:|---:|
| CPU package temperature | 80 / 93 / 97°C | **58.8 / 61 / 62°C** |
| CPU package power | 28.7 / 38.3 / 46.4 W | **12.9 / 14.6 / 23.0 W** |
| Fan1 RPM | median 3631 | median 2491, P95 3177 |
| Fan2 RPM | median 3113 | median 2164, P95 2789 |

Средняя частота CPU 2606.7 MHz, P95/max 2611.2/2611.3 MHz: процессор удерживается
около номинальных 2.6 GHz, а не Turbo Boost. Это решает конкретную проблему
термотроттлинга при обычной работе от сети без внешней подставки.

RTX остаётся отдельным нерешённым потребителем: P0, 1245/6000 MHz, mean power
29.32 W (P95 30.88 W), mean temp 57.0°C, utilization P95=0%, VRAM P95=0 MiB;
за 300.96 с интегрировано 2.451 Wh. OEM-драйвер уменьшил GPU mean лишь с 32.5 до
29.3 W, но не перевёл её в low-power state. Следующий выбор пользователя — оставить
совместимость и этот idle draw либо временно отключать dGPU через supported PnP
control вне игр/Ollama.

### Шаг 14: обратимый PnP-тест полного отключения RTX

Preflight: elevated=True, единственный активный внутренний экран 3840×2400 на Intel
UHD, Ollama без модели; RTX P0 26.80 W, 41°C, 0 MiB/0%. Выполнен 90-секундный тест
`Disable-PnpDevice` для точного NVIDIA instance ID с безусловным `Enable-PnpDevice`
в `finally`.

Во время disable устройство ожидаемо получило `CM_PROB_DISABLED`; `nvidia-smi`
не смог связаться с NVIDIA driver, а Intel продолжил выводить 3840×2400. После
включения RTX вернулась в `Status=OK`, `CM_PROB_NONE`, а `nvidia-smi` снова показал
P0, 28.76 W, 57°C, 0%/0 MiB. Следовательно, PnP-disable — работающий обратимый
обходной путь для idle draw; это не удаление драйвера и не изменение BIOS/EC.

Автоматическое отключение по одному только utilization отвергнуто: старт игры или
Ollama может кратко иметь нулевой GPU load, и watcher отключит RTX в гонке. Для
автоматизации нужен явный пользовательский триггер или список доверенных GPU-задач.

### Шаг 15: явный переключатель RTX с PnP safety gate

По решению пользователя добавлен `scripts/rtx_power.ps1` с обязательным `-Action`
`Status|Disable|Enable`. Скрипт фиксирован на instance ID RTX 3080 данного P1 Gen 4,
показывает PnP/driver/Ollama/telemetry status и перед disable отказывается работать,
если NVIDIA обслуживает активный дисплей или Ollama держит модель. `-Force` оставлен
только как явный обход этого safety gate.

Первый вариант скрипта с русскими строками не парсился Windows PowerShell 5.1:
UTF-8 без BOM трактовался в legacy code page. Интерфейс переведён в ASCII; `Status`
и `Disable -WhatIf` прошли. Реальный scripted cycle подтвердил:

```text
Disable: DeviceStatus=Error, DeviceProblem=CM_PROB_DISABLED,
         nvidia-smi unavailable
Enable:  DeviceStatus=OK, DeviceProblem=CM_PROB_NONE,
         DriverVersion=32.0.15.7391, P0=27.35 W
```

RTX оставлена включённой после проверки. Использование:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\rtx_power.ps1 -Action Status
powershell -ExecutionPolicy Bypass -File .\scripts\rtx_power.ps1 -Action Disable -Confirm:$false
powershell -ExecutionPolicy Bypass -File .\scripts\rtx_power.ps1 -Action Enable -Confirm:$false
```

### Шаг 6: загрузка моделей и проверка Ollama tags

Последовательно выполнены `ollama pull` с успешной проверкой sha256 digest:

| Модель | Ollama tag | Runtime-архитектура | Квант | Context | Thinking |
|---|---|---|---|---:|---|
| Qwen3.6-35B-A3B | `huihui_ai/Qwen3.6-abliterated:35b-a3b` | qwen35moe, 36.0B | Q4_K_M, 23 ГБ | 262K | да |
| Gemma 4 31B | `gemma4:31b-it-q4_K_M` | gemma4, 31.3B | Q4_K_M, 19 ГБ | 262K | да |
| Gemma 4 26B A4B | `gemma4:26b-a4b-it-qat` | gemma4, 25.2B | Q4_0, 15 ГБ | 262K | да |
| MedGemma 27B | `medgemma:27b-it-q4_K_M` | gemma3, 27.4B | Q4_K_M, 17 ГБ | 131K | нет |

`ollama ps` после загрузок пуст: pull не загрузил модели в VRAM. MedGemma пойдёт с
`think=false`, остальные модели — с `think=high`.

### Шаг 7: immutable fact package и harness

Реальная БД user_id=1 успешно преобразована в fact package SHA-256
`c04efae696a780716ba8500fd697fe285974e6fae42fc7ae0655ea271f3081bf`:
318 lab facts, 87 time series, 9 reports, 29 medication records, 209 daily health
aggregates, 16 activities, 20 frozen RAG sources. Пакет создаётся до первого
инференса и одинаков для всех моделей.

TDD: 20 unit tests для fact package, telemetry и harness прошли; ruff clean.

### Шаг 8: calibration после q8 KV-cache

Временный Ollama server `127.0.0.1:11435` запущен с `OLLAMA_KV_CACHE_TYPE=q8_0`,
`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`.
Permanent environment не менялся.

На коротком одинаковом prompt получено:

| Модель | off/4K | off/8K | think/8K | GPU VRAM peak |
|---|---:|---:|---:|---:|
| Qwen35 MoE | 29.3 tok/s | 28.7 tok/s | 25.2 tok/s | ~14.7 ГБ |
| Gemma31 Q4_K_M | 2.15 tok/s | 2.18 tok/s | 1.96 tok/s | ~14.4 ГБ |
| Gemma26 QAT | 47.95 tok/s | 48.19 tok/s | 47.37 tok/s | ~14.5 ГБ |
| MedGemma27 Q4_K_M | 3.89 tok/s | — | 4.17 tok/s | ~14.4 ГБ |

Gemma26 QAT — практический speed-лидер. Gemma31 не ускорилась от уменьшения
context 4K→8K: bottleneck — offload весов, а не KV-cache. Полный quality-run
выполняется отдельно для каждой модели; calibration output сохранён локально.

## Итог

План выполнен: четыре модели скачаны и прогнаны последовательно в отдельном Ollama
server с `q8_0` KV-cache, Flash Attention, одной загруженной моделью и live heartbeat.

### Calibration

| Модель | off/4K | off/8K | think/8K |
|---|---:|---:|---:|
| Qwen35 MoE | 29.3 | 28.7 | 25.2 tok/s |
| Gemma31 Q4_K_M | 2.15 | 2.18 | 1.96 tok/s |
| Gemma26 QAT | 47.95 | 48.19 | 47.37 tok/s |
| MedGemma27 Q4_K_M | 3.89 | — | 4.17 tok/s |

### Full quality-run: synthesis

| Модель | Wall, 3 seed | Output chars | Token-set Jaccard | Number Jaccard | GPU energy |
|---|---:|---:|---:|---:|---:|
| Qwen35 MoE | 621.5s | 19 824 | 0.250 | 0.644 | ~8.27 Wh |
| Gemma31 Q4_K_M | 3665.9s | 10 599 | 0.385 | 0.644 | ~32.56 Wh |
| Gemma26 QAT | 270.3s | 12 974 | 0.306 | 0.648 | ~5.86 Wh |
| MedGemma27 Q4_K_M | 3192.4s | 24 117 | 0.484 | 0.885 | ~30.73 Wh |

`exact_hashes=3/3` у всех моделей: все seed дали разные тексты. Token-set Jaccard показывает
согласованность лексики, number Jaccard — устойчивость чисел; это не клиническая точность.
Все четыре модели отвечали по-русски, без refusal. Markdown-структура: Qwen `[12,12,12]`,
Gemma31 `[12,12,12]`, Gemma26 `[11,11,12]`, MedGemma `[1,1,1]` — MedGemma выдала
длинный текст, но почти без заголовочной структуры.
Evidence ID coverage оказался `0/0`: модели не использовали требуемый формат `[LAB:...]/[REP...]`,
поэтому трассируемость к исходным фактам пока не засчитывается. Topic markers `4–5/15`
для Qwen/Gemma и `2/15` для MedGemma — грубая regex-эвристика, не финальная clinical accuracy.

Главный технический вывод: Gemma26 QAT — практический лидер по скорости и энергии;
Qwen35 — лучший компромисс глубины/скорости среди крупных reasoning-моделей;
Gemma31 и MedGemma27 на 16 ГБ VRAM слишком медленны из-за CPU-offload. Все full runs
помечены `thermally_constrained=true` согласно решению пользователя.

### Исправление harness

Первый Qwen/Gemma запуск был остановлен после обнаружения ошибки агрегации streaming:
`setdefault()` сохранял пустой финальный `content`, хотя данные уже приходили в предыдущих
chunks. Исправление проверено TDD; после него Qwen output стал непустым (до 8069 chars в audit),
а все четыре модели прогнаны повторно. Live heartbeat оставлен в runner с interval 30s
для следующих запусков; текущий full-run показывал heartbeat каждые 5s.

### Шаг 9: structured FACT_AUDIT и golden-set scorer

Добавлена строгая Pydantic-схема `FactAudit` с полями `lab_assertions`, `date_assertions`,
`medication_assertions`, `contradictions`, `findings`, `missing_data`. Ollama получает
JSON Schema через `format`; scorer проверяет существование evidence IDs, значения/единицы/
status лабораторных фактов, даты, raw/canonical/schedule лекарств, группы противоречий и
наличие provenance у важных findings.

Синтетический RED→GREEN: **24 passed**, ruff clean. Тесты включают правильный audit,
wrong lab value и unknown evidence ID.

Реальный audit на том же fact package:

| Модель | Wall | Результат |
|---|---:|---|
| Qwen35 | 445.6s | JSON валиден, но golden FAIL: почти все обязательные факты пропущены, 2 invalid external IDs |
| Gemma31 | 626.5s | JSON валиден, но assertions пусты; golden FAIL |
| Gemma26 QAT | 160.0s | JSON валиден, но assertions пусты; golden FAIL |
| MedGemma27 | остановлен на 42 мин | structured format проигнорирован, свободный content >13k chars; `structured_output_failed` |

Это отдельный результат от обычного quality synthesis: JSON Schema синтаксически
соблюдается у первых трёх моделей, но полнота аудита недостаточна. Следующий инженерный
шаг — декомпозировать audit по доменам (лаборатории/даты/лекарства) и запускать отдельные
короткие structured-запросы, вместо требования обработать 318 фактов одним ответом.

Пользователь решил остановить Gemma31: после двух больших lab batches (988 с и 1226.7 с)
и date batch (141 с) стоимость полного domain audit стала непрактичной. Основное сравнение
продолжается на Qwen35 и Gemma26 QAT; Gemma31 остаётся в benchmark как calibration/full-synthesis
reference, но новые domain batches для неё не запускаются.

### Критический quality finding: Qwen hallucinated clinical domain

При просмотре полного `qwen_full_synthesis_report.md` обнаружено, что Qwen описывает
«системную экспозицию синтетических каннабиноидов», URB-597, PRE-084, JP104, JWH/FUB,
CB1/CB2, CYP2C9/CYP3A4 и предлагает токсикологический мониторинг.

В fact package нет подтверждённой patient-фактуры для такого вывода. Модель использовала
`SRC:*` из frozen RAG chunks как будто они доказывают факт о пациенте. В отчёте появились
рекомендации повторить токсикологический скрининг через 2–4 недели, контролировать ALT/AST,
липидограмму, ECG/QTc и коррелировать это с Garmin.

Это не медицинский вывод и не рекомендация пользователю — это зафиксированная ошибка
модели. Наличие существующего `SRC:id` не означает, что источник подтверждает утверждение.
Следующий scorer обязан проверять тип источника: patient-specific claims допускают
`LAB/REP/MED/HLT/ACT`, а `SRC` может только дополнить общую интерпретацию после явной
привязки к patient fact. Нужен отдельный entailment/claim-to-fact слой, иначе valid ID
создаёт ложное ощущение трассируемости.

### Шаг 10: per-patient e2e benchmark

Реальные e2e sidecars разделены на три независимых patient packages:

| Patient package | Documents | Labs | Reports | Medications | Garmin |
|---|---:|---:|---:|---:|---|
| Петров Антон Игоревич, 24.02.1993 | 20 | 79 | 11 | 8 | да |
| Петрова Инна Игоревна, 25.11.1991 | 2 | 13 | 0 | 0 | нет |
| Саулина Инна Игоревна, 25.11.1991 | 12 | 347 | 0 | 0 | нет |

Garmin attached only to the first package. Weather facts are absent in all e2e fixtures;
model is instructed to say that weather is unavailable. Analysis dates absent in sidecars
remain missing and are not invented. Each package has a separate SHA-256 and document list.

Qwen35 and Gemma26 were run once per patient, sequentially, with patient-scoped prompt.
Saved outputs: `benchmarks/e2e_patient_reports/`. Rescored guards after correcting the
absence detector: all six outputs have `garmin_leak=false`, `weather_leak=false`,
`passed_guards=true`. Evidence citation count remains `0` in these free-form reports;
this is tracked separately from the no-cross-patient/no-invented-weather guards.

### Шаг 11: strict Garmin audit с числовым provenance

В connector Garmin подтверждены источники данных: resting heart rate, heart rate, steps,
sleep total, deep/light/REM/awake phases, stress, Body Battery, HRV, blood pressure,
weight и activities. При сборке e2e package ранее терялся `value_json` с фазами сна; loader
исправлен и теперь сохраняет его.

Добавлен отдельный structured Garmin audit: `HLT/ACT evidence_ids`, дата, metric, value,
unit; для sleep обязательны фазы; для activities обязательны type, duration, distance,
heart rate и calories. Batch уменьшен с 40 до 20 после обнаружения JSON truncation на
`num_predict=8192`.

Реальный strict результат на Петрове:

| Модель | Passed | Evidence | Metrics | Sleep phases | Activities | Invalid IDs |
|---|---:|---:|---:|---:|---:|---:|
| Qwen35 | да | 225 | 209/209 | 29/29 | 16/16 | 0 |
| Gemma26 QAT | да | 225 | 209/209 | 29/29 | 16/16 | 0 |

Примеры фактических значений сохранены в `benchmarks/garmin_audit_strict3/*/garmin_audit.md`.
Обе модели вернули конкретные sleep phases и activity fields с валидными HLT/ACT IDs.

### Шаг 12: детерминированный Garmin summary

После verified Garmin audit добавлен второй шаг без LLM: post-processor агрегирует только
прошедшие scorer факты и сохраняет `verified_garmin_summary.json/.md`. В summary нет
медицинских интерпретаций или новых рекомендаций — только периоды, N, average/min/max,
sleep phases, activity totals и evidence IDs.

Обе модели дали одинаковый проверенный summary: средний sleep=`7.48 h` за 29 дней,
HRV=`32.72 ms` за 29 точек, resting HR=`60.2 уд/мин` за 30 точек, steps=`5609.93`
за 30 дней, stress=`36.1`, Body Battery max=`63.17`. Активности: 45,738.17 s,
67,658.72 m, 10,556 calories. Все строки сохраняют HLT/ACT IDs.

Артефакты: `benchmarks/garmin_audit_strict3/*/verified_garmin_summary.md`.
Этот summary предназначен как компактный проверенный контекст для следующего clinical
synthesis prompt; он не заменяет врачебную интерпретацию.

## Материалы

- https://ollama.com/library/gemma4/tags — точные теги и размеры Gemma 4, обращение 2026-08-12.
- https://ollama.com/library/medgemma/tags — MedGemma 27B Q4_K_M, обращение 2026-08-12.
- https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6 — релиз 0.9.6, обращение 2026-08-12.
- https://github.com/LibreHardwareMonitor/LibreHardwareMonitor — MPL-2.0 и поддерживаемые сенсоры, обращение 2026-08-12.
- https://www.intel.com/content/www/us/en/security-center/advisory/intel-sa-01037.html — прекращение Intel Power Gadget, обращение 2026-08-12.
- https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/options-for-perf-state-engine-perfboostmode — значения `PERFBOOSTMODE`; обращение 2026-08-12.
- https://download.lenovo.com/pccbbs/pubs/x1_extreme_gen4_p1_gen4/index_en.html — руководство ThinkPad P1 Gen 4: связь Intelligent Cooling с Windows Power mode; обращение 2026-08-12.
- https://www.nvidia.com/content/Control-Panel-Help/vLatest/en-us/mergedProjects/nv3d/Manage_3D_Settings_%28reference%29.htm — смысл NVIDIA Power management mode; обращение 2026-08-12.
- https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/pci-express-settings-link-state-power-management — значения PCIe ASPM; обращение 2026-08-12.
- https://www.nvidia.com/en-gb/geforce/drivers/details/274424/ — GeForce Game Ready Driver 610.88, WHQL, 28.07.2026; обращение 2026-08-12.
- https://download.lenovo.com/pccbbs/mobiles/n40da26w.html — DS551306: версия, поддерживаемые модели, подпись и unattended install; обращение 2026-08-12.
- https://learn.microsoft.com/en-us/windows/package-manager/winget/ — штатная установка пакетов из Microsoft Store через WinGet; обращение 2026-08-12.
- https://docs.nvidia.com/vgpu/latest/known-issues/bug-3999308-nvidia-control-panel-not-available-in-multiuser-environments.html — NVIDIA Control Panel распространяется через Microsoft Store; обращение 2026-08-12.
- https://learn.microsoft.com/en-us/powershell/module/pnpdevice/disable-pnpdevice?view=windowsserver2025-ps — штатный PnP Disable/Enable device control; обращение 2026-08-12.
- https://docs.ollama.com/faq — `OLLAMA_KV_CACHE_TYPE=q8_0`, Flash Attention и server environment; обращение 2026-08-12.
- https://docs.ollama.com/context-length — связь context length, VRAM и CPU offload; обращение 2026-08-12.
