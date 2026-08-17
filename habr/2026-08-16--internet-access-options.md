# Публикация локального веб-кабинета Botkin в интернет

> Дата начала: 2026-08-16  
> Теги: deployment, networking, keenetic, openwrt, security

## Постановка

Изучить текущую архитектуру BOTkin и подготовить подробную инструкцию доступа к веб-кабинету с произвольных устройств через интернет, оставив FastAPI, SQLite, Ollama и модели на ноутбуке. Сравнить варианты без платного хостинга, включая Keenetic и Cudy TR3000 с OpenWrt.

## Контекст и ограничения

- Фронтенд — SPA из `src/botkin/web/`, которую раздаёт тот же FastAPI-процесс, что и API.
- На ноутбуке Windows работает FastAPI, Ollama запущена в WSL2; внешний доступ нужен к FastAPI, но не к Ollama, SQLite или MinIO.
- Система хранит медицинские документы и данные, поэтому открытый HTTP и прямой проброс порта 8000 недопустимы.
- В репозитории уже есть `docs/deploy-local-web.md`, но часть текста устарела после появления email/password-аутентификации.
- Реализация в этой задаче не меняется: результат — проверенная инструкция и архитектурные рекомендации.

## План

1. Проверить сетевую архитектуру, аутентификацию и клиентские запросы BOTkin.
2. Сверить актуальные возможности KeenDNS, Keenetic VPN, OpenWrt/WireGuard/Tailscale и TLS reverse proxy по первичным источникам.
3. Составить матрицу выбора и пошаговые схемы для приватного и публичного доступа.
4. Зафиксировать риски текущей реализации и критерии готовности к публичной эксплуатации.

## Ход работы

### Шаг 1: Аудит текущей схемы приложения

- FastAPI раздаёт SPA и API с одного origin; клиент использует относительные URL и cookie, поэтому отдельный хостинг фронта не требуется.
- Локальный процесс уже рассчитан на `0.0.0.0:8000`; наружу достаточно публиковать один HTTPS endpoint reverse proxy/VPN.
- Загрузка использует обычный `POST /upload`, прогресс — HTTP polling; WebSocket-проксирование не требуется.
- Email/password-аутентификация и хешированные session tokens уже есть, но cookie установлена с `secure=False`, публичная регистрация открыта, rate limiting и `TrustedHostMiddleware` отсутствуют.
- Ollama, SQLite и порты MinIO не должны публиковаться в WAN.

### Шаг 2: Сверка вариантов внешнего доступа

- KeenDNS поддерживает домены 4-го уровня для локальных web-приложений даже без публичного IP. В Cloud access поддерживаются HTTP/HTTPS, чего достаточно для Botkin.
- С KeeneticOS 3.7 доступ к проксируемому приложению можно закрыть отдельной авторизацией Keenetic; для текущего API это обязательнее режима `Unrestricted`.
- Tailscale Serve публикует localhost только внутри tailnet, выдаёт HTTPS и не требует port forwarding; Tailscale Funnel, напротив, делает endpoint публичным.
- WireGuard на Keenetic/OpenWrt требует достижимого WAN-адреса: CGNAT нельзя обойти локальным DNAT/firewall.
- Cloudflare Tunnel обходит CGNAT исходящим соединением, но без заранее созданного Cloudflare Access public hostname открыт всему интернету.
- Caddy автоматически выпускает TLS-сертификат при доступных 80/443 и поддерживает `basic_auth` с хешированным паролем.
- Для Cudy TR3000 подтверждена аппаратная достаточность (MT7981, 512 МБ RAM), но Cudy предупреждает о несовместимости старых OpenWrt intermediate images с новым NAND в устройствах конца 2025 года.

### Шаг 3: Подробная инструкция

Полностью переписан `docs/deploy-local-web.md`: добавлены общая подготовка Windows, диагностика CGNAT, пошаговые схемы Tailscale Serve, KeenDNS, WireGuard на Keenetic/OpenWrt, Caddy и Cloudflare Access, матрица выбора, внешний чек-лист и перечень production-hardening задач. Проверка `git diff --check -- docs/deploy-local-web.md` прошла без ошибок после нормализации окончаний строк; итоговый документ — 504 строки.

## Архитектурные решения

### Решение: приватный overlay VPN как вариант по умолчанию

- **Альтернативы:** прямой DNAT порта 8000 — публикует необработанный FastAPI и требует белого IP; Caddy/Cloudflare/KeenDNS без внешней авторизации — оставляют доступным legacy-контракт `X-Telegram-User-Id`; отдельный хостинг фронта — не работает без отдельной публикации API и добавляет CORS/deployment-контур.
- **Выбрано:** Tailscale Serve для доверенных устройств; KeenDNS с `Password protected` — когда нужен обычный браузер без VPN-клиента. Критерий — отсутствие публичного доступа к API либо обязательный барьер перед всеми маршрутами при сохранении локальных моделей и БД.
- **Компромисс:** Tailscale требует клиент на каждом устройстве; KeenDNS зависит от облачной инфраструктуры и даёт двойной вход.
- **Когда пересмотреть:** после отключения/криптографической защиты `X-Telegram-User-Id`, включения Secure cookie, rate limiting, invite-only регистрации и Trusted Hosts можно рассматривать обычный Caddy HTTPS без второго login-barrier.

### Решение: не переносить FastAPI/Ollama на роутер

- **Альтернативы:** запуск proxy и VPN на OpenWrt; отдельная раздача SPA; перенос inference на внешний сервер.
- **Выбрано:** на роутере оставить только маршрутизацию/VPN, а FastAPI, SQLite, файлы и Ollama — на ноутбуке. Критерий — единый origin уже реализован, модели требуют GPU ноутбука, а Tailscale на ноутбуке исключает зависимость от firmware/packages роутера.
- **Компромисс:** кабинет недоступен, когда ноутбук выключен или спит.
- **Когда пересмотреть:** при появлении постоянно включённого домашнего сервера с достаточной GPU/RAM или требования SLA выше доступности ноутбука.

## Итог

Подтверждено, что платный хостинг не требуется. Для текущего Botkin лучший безопасный путь — Tailscale Serve на ноутбуке. Для Keenetic есть вариант без белого IP и без клиентского VPN: KeenDNS domain 4-го уровня, но только с `Password protected`. Cudy TR3000/OpenWrt полезен как WireGuard-router при белом IP; при CGNAT сам OpenWrt входящую доступность не создаёт. Прямой проброс 8000 и DMZ исключены.

## Материалы

- Keenetic, remote access to home network resources with KeenDNS — https://support.keenetic.com/starter/kn-1112/en/15884-remote-access-to-home-network-resources-with-keendns.html, обращение 2026-08-16.
- Keenetic, password protected access — https://support.keenetic.com/skipper-dsl/kn-2112/en/19558-password-protected-remote-access-to-a-device-with-open-web-ui-via-dns.html, обращение 2026-08-16.
- Keenetic router security — https://help.keenetic.com/hc/en-us/articles/360001839240-Keenetic-router-security, обращение 2026-08-16.
- Tailscale Serve — https://tailscale.com/docs/features/tailscale-serve, обращение 2026-08-16.
- OpenWrt WireGuard — https://openwrt.org/docs/guide-user/services/vpn/wireguard/start, обращение 2026-08-16.
- Cudy OpenWrt revision warning — https://www.cudy.com/blogs/faq/openwrt-software-download, обращение 2026-08-16.
- Caddy reverse proxy — https://caddyserver.com/docs/quick-starts/reverse-proxy, обращение 2026-08-16.
- Cloudflare Access — https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/, обращение 2026-08-16.
