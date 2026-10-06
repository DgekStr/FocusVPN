# Changelog

## v2.1.7 - 2026-10-06

- Модальный мастер HAPP после входа при шаблонных/отсутствующих Reality-ключах; запуск происходит по подтверждению пользователя, после генерации и проверки конфигурации.
- Повторная генерация в Settings согласованно обновляет private key, public state и защищённый VIP-шаблон; персональные UUID/токены подписок сохраняются. Добавлены backup/rollback и проверки CSRF/подтверждения.
- Исправлена группа каталогов sing-box/HAPP в installer: `root:sing-box`, `0750`. Устранено падение службы с `permission denied`.
- Пункты бокового меню объединены в одну секцию, интервалы VPN-серверы/WireGuard выровнены.
- Демонстрационный VIP UUID заменяется уникальным при генерации ключей; персональные UUID/токены сохраняются. В Settings добавлены отдельные поля публичного ключа/UUID и удалена устаревшая подпись о ссылке «без изменения».
- На `.41` проверены config validation, оба JSON/ссылка в Settings HTML, создание пользователя и сохранение персональных токенов при VIP миграции. 129 Python-тестов и Node suites прошли.
- Все четыре действия VPN-сервера размещены в одной ячейке и строке с gap 6 px. Раскладка проверена браузером на desktop/mobile; полный набор расширен до 130 Python-тестов.
- Проверено обновление `.41` из опубликованного Git-тега v2.1.7: HAPP config, ключи, VIP и персональные пользователи не изменились; службы активны, wg-easy healthy, HTTPS login отвечает Basic challenge.

## v2.1.6 - 2026-10-06

- Кнопка «Проверить соединение» сокращена до «Проверить»; рядом добавлена «Редактировать» с модальным JSON-редактором существующего профиля. Сохранение использует валидируемый replace-import, сохраняет tag и обновляет shared gateway/HAPP-конфигурацию.
- Из шаблона новой установки удалены восемь пустых VLESS-серверов и пустой urltest selector. Администратор импортирует свои профили после развёртывания.
- Добавлена ограниченная миграция старого полного набора placeholders с приватным backup; реальные или смешанные настройки сохраняются.
- Добавлены Python-проверки миграции/редактора и Node-проверки открытия, отмены и JSON validation модалки; JS/CSS URL обновлены для сброса кэша.
- Удалены отдельные профили из бокового меню: остался единственный пункт «VPN-серверы» с общей страницей управления.
- Обновление из опубликованного Git-тега проверено на `192.168.0.41`: восемь placeholders очищены с backup, config check и wg-easy API проходят, HTTPS-панель отвечает. Рендер редактора/меню проверен на установленном коде; взаимодействия модалки проверены Node-тестом.

## v2.1.5 - 2026-10-06

- Автоматическое создание служебного администратора wg-easy через официальный unattended setup без ввода логина/пароля. Пароль уникален для установки и сохраняется при обновлениях в root-only API-файле панели.
- После проверки `/api/client` контейнер пересоздаётся без INIT-переменных. Существующая база сохраняется; настроенный администратор не сбрасывается.
- При шаблонных credentials панель больше не отправляет фиктивный логин в API, а сообщает о незавершённой настройке.
- Добавлены четыре теста авторизации, замены шаблонов и сохранения пароля при повторном запуске.
- На `192.168.0.41` проверены auto-admin создание и повторная установка из опубликованного тега: API клиентов доступен, пароль сохранён, INIT_PASSWORD удалён из окружения Docker, приватный API-файл имеет `0600 root:root`, внешний порт `51821` закрыт.

## v2.1.4 - 2026-10-06

- Legacy detector дополнительно замечает остановленные, но enabled `wg-quick@...` units и после отдельного подтверждения отключает их. Конфиги до очистки перемещаются в root-only backup.
- Проверен fresh clone/upgrade на Ubuntu 22.04 amd64: Nginx и панель активны, wg-easy `15.4.0` healthy с restart policy `unless-stopped`; setup API доступен на localhost, внешние порты `:51821` и `:80` закрыты, `:7445` требует Basic Auth.
- Старых WireGuard конфигураций на тестовом хосте не было; destructive backup path там не запускался.

## v2.1.3 - 2026-10-06

- Installer до изменений показывает полный список пакетов и служб и требует явного подтверждения.
- Nginx и wg-easy устанавливаются/запускаются автоматически; панель публикуется по HTTPS на `:7445`, setup wg-easy доступен через SSH-туннель к localhost.
- При обнаружении legacy WireGuard установщик предлагает отмену или подтверждённый перенос старых конфигов в приватную резервную копию; managed `wg-client.conf` сохраняется.
- После установки печатается перечень запущенных компонентов и фактический URL панели.

## v2.1.2 - 2026-10-06

- Исправлены чистые HAPP runtime-шаблоны: VIP UUID синтаксически корректен и совпадает в панели, VLESS-ссылке и inbound-конфигурации. Панель может запуститься до замены тестовых значений реальными ключами; сами VPN-службы с этими шаблонами не включаются.
- Добавлен regression test, проверяющий UUID и VLESS-ссылку в двух HAPP-примерах.
- Проверено на `192.168.0.41`: панель работает за HTTPS proxy `:7445`, backend доступен только на loopback `:9443`, Basic Auth включён. Используется самоподписанный сертификат, поэтому браузер покажет предупреждение; VPN-службы оставлены выключенными.

## v2.1.1 - 2026-10-06

- Исправлена установка sing-box: installer получает SHA-256 digest нужного архива из GitHub Release API и сверяет файл перед распаковкой. Если asset или digest отсутствует/некорректен, установка завершается ошибкой; проверка checksum не отключается.
- Первая установка по Git clone на Ubuntu 22.04 amd64 остановилась на скачивании отсутствующего upstream-файла `checksums.txt`; до создания приложения, runtime-конфигурации и пароля она не дошла. Этот релиз заменяет удалённый источник контрольных сумм официальным digest точного release asset из GitHub API.
- Повторная установка и проверка доступа на `192.168.0.41` выполняются отдельно. VPN-службы нельзя запускать до замены шаблонов реальными конфигурациями; backend по умолчанию работает по незашифрованному HTTP и требует Basic Auth.
- Installer checksum helper покрыт positive/negative unit tests. Полный privileged `--enable` и запуск VPN-туннелей не выполнялись.

## v2.1 - 2026-10-06

- `/happ-server`: live-подключения группируются по IP и протоколу; duration, download/upload суммируются, destination берётся из последнего соединения.
- HAPP lifetime traffic totals хранятся отдельно от retention истории. Добавлены TOP-5 и подтверждаемый per-profile reset; первоначальные totals backfill-ятся из ещё сохранённых rows.
- `/happ-history`: верхний график per-client download/upload с числовыми значениями, фильтрами страницы и сортировкой по убыванию общего трафика.
- VIP VLESS block перенесён вниз `/happ-server`; Endpoint host берётся из настроенного публичного URL подписки, порт Reality остаётся `9445`.
- Settings показывает IP и ОС VPN-хоста, uptime, CPU/LAN peaks за rolling 24 часа и сетевые RX/TX counters. Локальный collector опрашивает `/proc` раз в 5 секунд и хранит samples в `/mnt/stat/server-metrics.sqlite3`.
- Добавлен анимированный пульсирующий зелёный favicon для панели.
- Installer copies all packaged panel Python files, including `server_metrics.py`; bootstrap runs it from the selected Git ref. Clean-clone/source validation: 105 Python tests, two Node UI regression suites, `scripts/validate.ps1` and installer `--help`.

### Ограничения проверки

- Полный privileged apt/systemd first boot не запускался в disposable Debian/Ubuntu VM.
- Host traffic totals являются системными счётчиками с загрузки ОС; CPU/LAN peaks начинают наблюдаться с момента запуска collector и хранятся до 24 часов.
- HAPP traffic является наблюдаемой оценкой; пропущенные финальные байты соединения восстановить нельзя. Мобильный импорт/refresh HAPP отдельно не сертифицировался этим набором тестов.

## v2.0 - 2026-10-03

- Персональные HAPP аккаунты: независимые UUID/QR, срок UTC, включение, отзыв и удаление без ротации VIP.
- Подтверждённые аутентификацией имена пользователей, отдельные скачано/отправлено, суммы активного и сохранённого трафика.
- Приватная SQLite история в `/mnt/stat/`, независимый фоновый collector, retention 60 дней по умолчанию, фильтры, pagination и настоящий BIFF XLS.
- Персональные HTTP-подписки с непрозрачными токенами, изоляцией аккаунта и отзывом доступа. Метаданные HAPP: upload/download, безлимит, срок, обновление раз в час, название `🖧 FocusVPN`.
- Короткое уведомление о частном VPN для focuslens.dev через UTF-8 Base64 `announce`; `/happ-info` с выравниванием по ширине. Нативное выравнивание контролирует HAPP.
- Отображение дат отчётов, XLS и проверки отклика в UTC `dd.mm.yyyy HH:MM:SS`; исходный ISO storage/API сохранён.
- Настраиваемый IPv4-допуск `FOCUSVPN_ADMIN_NETWORK`, согласованный с nftables. На текущем сервере явно включён `0.0.0.0/0`; auth/token checks и защита wg-easy 51821 сохранены.
- Default VPN gateway mode: direct egress через текущий server default route, с предварительной проверкой route/forward/NAT и сохранением per-client LAN deny; активируется явно из Settings.
- HTTPS reverse proxy `vpn.focuslens.dev`, trusted-proxy IP/session binding, обновление Subscription origin из публичного DNS URL.
- Standalone Git-clone bootstrap, автоматическая установка системных зависимостей и интерактивное безопасное создание panel auth hash; systemd units ставятся при bootstrap, VPN services стартуют только после заполнения реальных конфигов.
- Runtime VERSION, актуальные README/status/roadmap/security/operations и обезличенные screenshots.
- 88 Python-тестов, два Node VM UI-сценария и локальные publication checks.

### Post-release updates — 2026-10-05

- HAPP `/happ-server`: lifetime per-user traffic totals, TOP-5 ranking, and per-profile reset. Totals survive history retention and initialize from retained records.
- Live connections aggregate by client IP and protocol; duration and byte counters sum, while destination follows the latest connection.

- Добавлен opt-in default gateway mode для WireGuard-клиентов с проверкой main route, forwarding, FORWARD и NAT. Он оставляет системный route и HAPP inbound незатронутыми; активный production mode не меняется автоматически.
- Settings перестроен в равные responsive две колонки; selector gateway вынесен наверх.
- Добавлен однофайловый Git bootstrap: скачивание выбранного ref, зависимостей и checksum-проверенного sing-box; password prompt не передаёт пароль через argv/environment. Пустые/placeholder configs блокируют старт VPN-служб.
- Проверен clean clone `master`; installer tests/unit tests/publication gates прошли. Полный privileged apt/systemd first boot требует отдельной Debian/Ubuntu VM.

### Ограничения

- HTTP 9443 не шифруется; для недоверенных сетей требуется HTTPS. Релиз не настраивает NAT или TLS.
- Трафик является нижней наблюдаемой границей за срок хранения истории; финальные байты коротких сессий могут быть неизвестны.
- Windows HAPP UI/import/auto-update, внешний WireGuard egress и реальная доставка Mattermost не объявляются подтверждёнными локальными тестами.
- Реальные ссылки, токены, UUID, ключи, базы и пользовательские screenshots не входят в релиз.

## v1.0 - Базовая Публикация

Базовая версия закрепляет commit `3f48b1652c2847507ea951274e377fa45594f6f6`, опубликованный до изменений v2.0. Git-история не переписывается.

- WireGuard gateway, VLESS TPROXY, RU/private split и общий интерфейс управления.
- Внешний WireGuard mode и совместная маршрутизация gateway/HAPP.
- Импорт sing-box/Xray объектов и массивов, управление профилями, неблокирующие HTTPS-проверки.
- VLESS monitoring, автоматический выбор после трёх побед, журнал и Mattermost queue.
- Installer, systemd units, безопасные конфигурационные шаблоны и публикационная документация.