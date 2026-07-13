# Развёртывание веб-кабинета на локальном компьютере с доступом из интернета

Веб-кабинет — статика (`src/botkin/web/`), которую раздаёт тот же FastAPI-процесс,
что и API (`StaticFiles(html=True)` на `/`). Отдельного фронт-сервера нет: поднимаете
один процесс — получаете и API, и интерфейс.

## 1. Локальный запуск

```bash
cd botkin
# зависимости уже в .venv; если с нуля: python -m venv .venv && .venv/bin/pip install -e .

# минимальный запуск (кабинет на http://localhost:8000)
.venv/bin/python -m uvicorn botkin.api.app:app --host 127.0.0.1 --port 8000
```

Для распознавания загрузок нужен Ollama с моделью (`ollama pull qwen3-vl:8b` или что
задано в `VLM_MODEL`). Без Ollama кабинет работает в режиме просмотра уже накопленных
данных: загрузка примет файл, но обработка упадёт в `failed`.

### Дебаг-вход без идентификатора

По умолчанию каждый запрос требует заголовок `X-Telegram-User-Id` (фронт подставляет
его из экрана «Профиль»). Для локальной разработки можно назначить пользователя
по умолчанию — тогда кабинет открывается сразу, без настройки:

```bash
WEB_DEBUG_USER_ID=113521070 .venv/bin/python -m uvicorn botkin.api.app:app --port 8000
```

Запросы без заголовка пойдут от имени пользователя с `telegram_user_id=113521070`
(создастся автоматически). **Не задавайте флаг на публичном инстансе** — это
эквивалент входа без пароля.

## 2. Доступ из интернета

⚠️ **Сначала прочитайте раздел 3 (безопасность).** В кабинете медицинские данные,
а идентификация demo-уровня.

### Вариант A: Cloudflare Tunnel (рекомендуется — без белого IP и открытия портов)

```bash
# установка (Linux x86_64)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o ~/bin/cloudflared && chmod +x ~/bin/cloudflared

# сервис слушает только localhost
.venv/bin/python -m uvicorn botkin.api.app:app --host 127.0.0.1 --port 8000

# одноразовый публичный URL (для демо; меняется при каждом запуске)
cloudflared tunnel --url http://localhost:8000
# → выведет адрес вида https://<random>.trycloudflare.com
```

Постоянный домен: `cloudflared tunnel login` → `tunnel create botkin` →
маршрут на свой домен (нужен аккаунт Cloudflare, бесплатного тарифа достаточно).
Плюс: TLS и скрытие домашнего IP из коробки, порты наружу не открываются.

### Вариант B: ngrok (быстрая демонстрация)

```bash
ngrok http 8000
# → https://<random>.ngrok-free.app
```

Бесплатный тариф показывает interstitial-страницу и меняет URL при перезапуске.

### Вариант C: свой VPS + обратный прокси (постоянная установка)

На домашней машине ничего наружу не открывается; VPS терминирует TLS:

```bash
# туннель с домашней машины на VPS (reverse SSH)
ssh -N -R 127.0.0.1:8000:localhost:8000 user@vps
```

На VPS — Caddy (автоматический HTTPS через Let's Encrypt):

```
# /etc/caddy/Caddyfile
botkin.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

### Вариант D: проброс порта на роутере (не рекомендуется)

`--host 0.0.0.0` + port-forwarding 443→8000 на роутере. Требует белый IP,
самостоятельную настройку TLS и светит домашний адрес. Используйте туннели выше.

### Автозапуск через systemd (Linux)

```ini
# /etc/systemd/system/botkin-web.service
[Unit]
Description=Botkin web cabinet
After=network.target

[Service]
WorkingDirectory=/home/user/botkin
ExecStart=/home/user/botkin/.venv/bin/python -m uvicorn botkin.api.app:app --host 127.0.0.1 --port 8000
Restart=on-failure
# Environment=WEB_DEBUG_USER_ID=113521070   # только для приватных инстансов!

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now botkin-web
```

## 3. Безопасность — прочитать обязательно

1. **Идентификация demo-уровня.** Заголовок `X-Telegram-User-Id` принимается на веру:
   любой, кто знает/подберёт идентификатор, читает данные этого пользователя.
   Настоящей аутентификации пока нет (техдолг, журнал ит. 28). Публиковать инстанс
   в интернет можно только: для демо с тестовыми данными, либо закрыв его сверху
   (Cloudflare Access, HTTP Basic Auth на прокси, VPN/Tailscale).
2. **`WEB_DEBUG_USER_ID` на публичном инстансе не задавать** — см. выше.
3. **Только HTTPS.** Медицинские данные по HTTP недопустимы; туннели A/B/C дают TLS сами.
4. **Приватная альтернатива:** Tailscale (`tailscale up` на сервере и телефоне) — кабинет
   доступен с ваших устройств по внутреннему адресу сети, наружу не торчит вообще.

## 4. Проверка

```bash
curl https://<ваш-адрес>/health            # → {"status":"ok"}
curl -H "X-Telegram-User-Id: 113521070" https://<ваш-адрес>/api/stats
```

В браузере: `https://<ваш-адрес>/` → экран «Обзор»; идентификатор задаётся в
«Профиль» → «Идентификация (demo)».
