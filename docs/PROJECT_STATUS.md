# Project status

Версия: **v2.0**. Дата фиксации: **2026-10-03**.

## Вехи

| Веха | Статус | Что сделано |
|---|---|---|
| M1. Базовый sing-box | Done | sing-box 1.14.2, systemd unit, проверка конфигурации |
| M2. Исходящий VLESS | Done | VLESS Reality profiles, urltest и ручной route selector |
| M3. WireGuard gateway | Done; endpoint egress pending | Переключение режимов, source-policy routing через `wg-client`, LAN bypass, scoped FORWARD/NAT; доступность внешнего выхода проверяется отдельно |
| M4. Server-side split | Done | RU aggregated CIDR direct, LAN/private direct, daily updater |
| M5. Unified admin | Done | одна FocusLens Basic Auth для VLESS и WireGuard |
| M6. WireGuard operations | Done | CRUD, config/QR, activity, per-client LAN deny |
| M7. HAPP Direct | Retired | удалён из UI; server-side split-routing остаётся источником direct-маршрутизации |
| M8. HAPP Server | Done | VLESS Reality inbound `9445`; в WireGuard mode HAPP outbound помечается и идёт через `wg-client`, inbound остаётся активен |
| M9. UI consistency | Done | Основной раздел VPN-серверы, пункт VLESS скрыт; live статусы, ping, Settings, журнал, public HAPP QR |
| M10. Publication package | Done | Код, units, документация и обезличенные fixtures; runtime-секреты не входят в пакет |
| M11. JSON server manager | Done | Объект/массив sing-box/Xray, flat VLESS settings, VLESS TCP/gRPC, Hysteria2, Trojan и Shadowsocks; import/replace/delete синхронизируют selector и HAPP |
| M12. Background checks | Done | POST не ждёт сеть; последовательная очередь, fingerprint-bound результаты, timeout/retry polling, стартовый баннер заменяется итогом |
| M13. VLESS automation | Done, opt-in | Интервал 1-60 минут, HTTPS задержка, 3 последовательные победы, journal, фоновые Mattermost уведомления |
| M14. Regression coverage | Done | 68 Python-тестов и 2 Node VM UI-сценария; локальные publication gates |
| M15. Персональные HAPP аккаунты | Done | UUID/QR, включение, отзыв, удаление, срок UTC; VIP сохраняется |
| M16. Статистика и история | Done | Аутентифицированные имена, live и сохранённые суммы, SQLite в /mnt/stat, retention 60 дней, настоящий XLS |
| M17. Подписки HAPP | Done server-side | Секретный токен аккаунта, трафик / безлимит, часовое обновление, название с emoji и короткое announce |
| M18. Сетевой доступ | Done, explicit opt-in | FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0 на текущем сервере; auth/token checks и защита 51821 сохранены |
| M19. Версия и публикация | v2.0 | VERSION, актуальные документы, безопасные screenshots; v1.0 закрепляет ранее опубликованную базу |

## Проверенные runtime-факты

- OS: Ubuntu 22.04 LTS.
- sing-box: 1.14.2.
- HAPP inbound: `0.0.0.0:9445`.
- Admin panel: `0.0.0.0:9443`.
- WireGuard network: `10.8.0.0/24`.
- Доступ из источника вне прежнего allowlist проверен через временный namespace: панель возвращает 303 на вход, не анонимный доступ в админку. NAT и HTTPS не настраивались.
- When WireGuard mode is selected, HAPP route is `focusvpn-wg-direct` with `routing_mark=2`.
- Внешний WireGuard egress требует отдельного подтверждения возвратного трафика и NAT на стороне endpoint; активный сервис не доказывает доступность выхода.
- Dedicated auto-8 settings are removed. Tags such as `auto-8` can be created again through JSON import; they are not reserved for a particular protocol.
- The sing-box status indicator is on `/outbounds`. Background HTTPS checks do not change the active route; saved errors are shown as errors, not permanent pending notifications.
- Автопроверка и автовыбор opt-in, default disabled, интервал 5 минут. Настройки и выбранный маршрут управляются владельцем; документация не фиксирует их как неизменный live-state.
- Admin, gateway sing-box and HAPP services were active at the snapshot. `active` and a valid config do not prove network connectivity.
- Подписки проверены реальными HTTP-запросами: аккаунтная изоляция, отзыв, UTF-8 metadata, total=0, интервал 1 час. Windows HAPP UI/import не проверялся автоматизированно.
- /happ-info содержит только информационный текст с justify; не выдаёт credentials. Нативное выравнивание announce управляется HAPP.

## Ограничения

- XHTTP не конвертируется в TCP/gRPC: пакетный импорт выводит явный пропуск.
- WireGuard outbound из Xray настраивается через отдельный WG-client раздел, не через общий server selector.
- Метрика ping - время до первого HTTPS-байта через туннель, а не ICMP. Внешний endpoint, TLS, auth и upstream connectivity могут быть причиной провала.
- Автовыбор действует только для VLESS и только в режиме VLESS. Перезапуск, ручная смена, ошибка цикла или изменение профилей/настроек сбрасывают серию побед.
- Трафик является наблюдаемой нижней границей за срок хранения, не пожизненным биллингом; HAPP показывает upload + download / безлимит. HTTPS paths и неизвестные финальные байты не восстанавливаются.
- Широкий IPv4-доступ к HTTP 9443 не шифрует admin credentials и токены: для недоверенных сетей требуется HTTPS.

## Что сознательно не включено

- production secrets;
- actual VLESS links and user UUIDs;
- Basic Auth database;
- wg-easy API secret;
- SQLite backups and runtime state;
- generated `.pyc` files and historical `.before-*` backups.
