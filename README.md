# FocusVPN v2.1

Единый VPN-шлюз и административная панель для WireGuard, sing-box и HAPP. Документ описывает релиз `v2.1`; базовый релиз `v1.0` закреплён коммитом `3f48b16`, история Git не переписывается.

## Новое в v2.1

- В Settings добавлены метрики VPN-сервера: LAN IP и версия ОС, время работы, пиковая загрузка CPU и LAN за последние 24 часа, а также принятый/отправленный трафик со времени загрузки ОС.
- Collector считывает локальные `/proc` counters каждые 5 секунд и хранит скользящие метрики в `/mnt/stat/server-metrics.sqlite3`. Пики начинают накапливаться после запуска collector; счётчики сети сбрасываются при перезагрузке ОС.
- `/happ-history` показывает график по клиентам: скачано — розовым, отправлено — синим. Под столбцами постоянно видны числа; клиенты отсортированы по сумме трафика, выбранные фильтры пользователя и дат применяются к графику.
- HAPP lifetime totals скачивания и отправки хранятся независимо от срока хранения подробной истории. В `/happ-server` доступны TOP-5 и отдельный сброс накопительной статистики каждого профиля.
- Активные HAPP соединения объединяются по IP клиента и протоколу. Длительность и байты суммируются, назначение показывается по последнему соединению.
- В VIP-блоке `/happ-server` Endpoint использует hostname из настроенного публичного URL подписки; порт Reality остаётся `9445`.
- Favicon панели — зелёный пульсирующий индикатор; анимация работает в Chromium через Canvas.

## Возможности

- VLESS Reality и `urltest`, ручной выбор исходящего маршрута, TPROXY только для клиентов WireGuard, прямой маршрут для частных сетей и обновляемого списка RU IPv4.
- Режимы шлюза: VLESS/TPROXY, обычный основной маршрут сервера или внешний WireGuard-клиент. Перед переключением default gateway проверяются маршрут, IPv4 forwarding и правила FORWARD/NAT. Режим внешнего WireGuard ограничен клиентской сетью; индивидуальные LAN-запреты сохраняются.
- Управление клиентами WireGuard: создание, включение, удаление, конфигурация, QR-код, активность и запрет LAN для отдельного клиента.
- Импорт sing-box/Xray JSON и массивов: VLESS, Hysteria2, Trojan и Shadowsocks. XHTTP и WireGuard outbound автоматически не преобразуются.
- Последовательные фоновые проверки VLESS с задержкой, состоянием очереди и временем проверки. Автовыбор выключен по умолчанию и переключает маршрут только после трёх побед одного профиля подряд; в режиме WireGuard автоматического переключения нет.
- HAPP Server на VLESS Reality `9445`, персональные пользователи, отдельные UUID/QR, срок действия, включение/отзыв доступа и сохранение общей VIP-ссылки.
- Персональные HTTP-подписки с секретным токеном, изоляцией по аккаунту, безлимитным `total=0`, сроком действия и запрашиваемым часовым обновлением.
- Журнал HAPP в приватной SQLite базе `/mnt/stat/happ-stat.sqlite3`: фоновые наблюдения, фильтры, пагинация и экспорт XLS. Подробная история очищается по сроку из Settings, по умолчанию через 60 дней.
- Интеграционный шлюз CRM к существующей панели с проверкой роли и источника запроса. Отдельный VPN-сервер для этой интеграции не устанавливается.
- Standalone Git bootstrap устанавливает системные зависимости, панель и systemd units, проверяет SHA-256 sing-box и безопасно запрашивает пароль панели.

## HAPP: точность статистики

Live-сопоставление имени пользователя использует журнал аутентификации VLESS, а не один IP-адрес. Lifetime totals прибавляют только новые приросты счётчиков соединений и переживают очистку подробной истории. При первом запуске они заполняются из записей, которые ещё есть в SQLite; уже удалённые по retention данные восстановить нельзя. Кнопка сброса удаляет итог только выбранного профиля; активные счётчики остаются базой, и после сброса прибавляются лишь новые байты.

Исторический график и totals — наблюдаемые оценки, а не точный биллинг. При разрыве или перезапуске соединения могут быть потеряны финальные байты, которые collector не успел снять. Полные HTTPS-пути и содержимое страниц недоступны.

## Адреса

| Назначение | Адрес |
|---|---|
| Административная панель | `https://vpn.focuslens.dev` |
| VPN-серверы | `/outbounds` |
| WireGuard | `/wireguard` |
| HAPP Server | `/happ-server` |
| Настройки | `/settings` |
| Журнал переключений | `/gateway-journal` |
| История HAPP | `/happ-history` |
| Информация о подписке | `/happ-info` |
| HAPP VLESS inbound | публичный адрес, TCP `9445` |

Backend панели на `192.168.0.39:9443` использует HTTP внутри LAN; внешний доступ проходит через HTTPS reverse proxy на `192.168.0.15`. WAN-доступ к `37.208.69.6:9443` закрыт. На уровне приложения настроен `FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0`, поэтому прямой доступ к backend из разрешённой маршрутизируемой сети обходит TLS proxy; авторизация панели остаётся обязательной. Не публикуйте backend HTTP в недоверенную сеть и не меняйте firewall/NAT без отдельной проверки.

## Установка

Для новой установки на Debian 12+ или Ubuntu 22.04+ используйте опубликованный тег `v2.1`:

```bash
curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/v2.1/scripts/bootstrap.sh | sudo bash -s -- v2.1
```

Bootstrap клонирует выбранную ветку или тег во временный каталог с правами `0700`, запускает `scripts/install.sh` и удаляет clone после завершения. Для интерактивного ввода пароля требуется терминал. Пароль не передаётся в argv или environment; в `/etc/sing-box-admin/auth.json` сохраняется salted scrypt hash с правами `0600`.

Дополнительные параметры:

- `--start-wg-easy` создаёт контейнер wg-easy для первичной настройки. Сам контейнер не настраивает его администратора/API.
- `--enable` проверяет реальные конфигурации и включает VPN-службы. Не используйте этот параметр до заполнения gateway, HAPP и wg-easy конфигураций.

Installer из уже полученного clone:

```bash
git clone --branch v2.1 https://github.com/DgekStr/FocusVPN.git
cd FocusVPN
sudo ./scripts/install.sh
```

Затем настройте `/etc/focusvpn/focusvpn.env`, заполните реальные файлы `/etc/sing-box/config.json`, `/etc/sing-box-happ-server/config.json`, `/etc/sing-box-admin/happ-server.json` и `/etc/sing-box-admin/wg-easy-api.json`; запустите wg-easy initial setup и только после этого выполните `sudo ./scripts/install.sh --enable`. Установщик не должен запускать VPN-службы с placeholders.

## Проверка Git-клона

PowerShell-проверка опубликованного тега без привилегированной установки:

```powershell
$clone = Join-Path $env:TEMP ('FocusVPN-v2.1-' + [guid]::NewGuid().ToString('N'))
git clone --depth 1 --branch v2.1 https://github.com/DgekStr/FocusVPN.git $clone
Push-Location $clone
try {
    .\scripts\validate.ps1
    py -3 -m unittest discover -s scripts -p 'test_*.py' -q
    node scripts\test_panel_checks.js
    node scripts\test_happ_stats_ui.js
    & 'C:\Program Files\Git\bin\bash.exe' scripts/bootstrap.sh --help
    & 'C:\Program Files\Git\bin\bash.exe' scripts/install.sh --help
} finally {
    Pop-Location
}
```

На Linux вместо `C:\Program Files\Git\bin\bash.exe` вызывайте `bash`. Опубликованный v2.1 проверен из чистого clone: 105 Python-тестов, две Node UI suites, `scripts/validate.ps1`, а также справка bootstrap и installer. Полную установку с apt, systemd и изменениями сети в этой проверке не выполняли; для неё нужна отдельная disposable Debian/Ubuntu VM.

## Репозиторий

- `server/panel/` — Python-код панели и обработчики HAPP/WireGuard.
- `server/panel/static/` — CSS и JavaScript.
- `server/libexec/` и `server/systemd/` — системные команды и службы.
- `server/config/` — безопасные шаблоны конфигураций.
- `scripts/` — installer, bootstrap, тесты и preview renderer.
- `docs/OPERATIONS.md` — эксплуатация и развертывание.
- `docs/PROJECT_STATUS.md` и `docs/ROADMAP.md` — статус и следующие задачи.
- `docs/CHANGELOG.md` — изменения релизов.
- `VERSION` — версия runtime.

Секреты, реальные UUID и VLESS-ссылки, ключи Reality, Basic Auth, wg-easy API secret, runtime базы и введённые администраторами конфигурации в Git не включаются. Скриншоты в `docs/screenshots/` сделаны на синтетических данных и отражают интерфейс v2.0, а не новые карточки метрик/график v2.1.