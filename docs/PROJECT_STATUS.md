# Project status

Версия: **v2.1.3**. Дата фиксации: **2026-10-06**.

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
| M14. Regression coverage | Done | 110 Python-тестов и 2 Node VM UI-сценария; local/clean-clone publication gates |
| M15. Персональные HAPP аккаунты | Done | UUID/QR, включение, отзыв, удаление, срок UTC; VIP сохраняется |
| M16. Статистика и история | Done | Lifetime totals отдельно от 60-дневной SQLite history, TOP-5, per-user reset, `/happ-history` chart, XLS |
| M17. Подписки HAPP | Done server-side | Секретный токен аккаунта, трафик / безлимит, часовое обновление, название с emoji и короткое announce |
| M18. Сетевой доступ | Done, explicit opt-in | `FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0` поддерживается конфигурацией; auth/token checks и защита 51821 сохранены |
| M19. Версия и публикация | v2.1.3 | VERSION, README/changelog/status/roadmap обновлены; v1.0 закрепляет ранее опубликованную базу |
| M20. Default gateway mode | Done, opt-in | Прямой egress через основной gateway для WG-клиентов; route/FORWARD/NAT preflight, LAN deny сохранён |
| M21. Standalone bootstrap | Done | Git-clone bootstrap, checksum-verified sing-box, интерактивный scrypt auth, все systemd units |
| M22. HTTPS panel entry | Done | `vpn.focuslens.dev` Nginx proxy, trusted-proxy IP/session binding, Secure cookie, subscription origin |
| M23. Clean-clone verification | In progress | 110 Python-тестов, 2 Node UI suites и publication checks ожидают финального прогона; auto-start wg-easy/Nginx и install-plan confirmation v2.1.3 ожидают серверной проверки |
| M24. Host metrics | Done | Settings показывает LAN IP/OS, uptime, CPU/LAN peaks за 24 часа и boot RX/TX; local collector сохраняет samples в `/mnt/stat/` |
| M25. HAPP live/history UI | Done | Группировка IP/protocol, per-profile lifetime reset, history traffic chart, VIP endpoint из subscription URL |
| M26. Panel favicon | Done | Зелёная Canvas-анимация; сохраняется PNG fallback |

## Проверенные runtime-факты

- OS: Ubuntu 22.04 LTS.
- sing-box: 1.14.2.
- HAPP inbound: `0.0.0.0:9445`.
- Admin panel: `0.0.0.0:9443`.
- Test deployment `192.168.0.41`: Nginx HTTPS proxy `:7445` использует self-signed сертификат; backend `sing-box-admin` слушает только `127.0.0.1:9443`, `FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0`, trusted proxy `127.0.0.1/32`; login возвращает Basic Auth challenge. HAPP содержит только тестовые значения, VPN/HAPP services не включались.
- Installer v2.1.3 после подтверждения перечисляет пакеты/службы, запускает Nginx и wg-easy с автоперезапуском, ограничивает setup API localhost и выводит URL панели/SSH-туннель.
- Server metrics use `/proc` counters; rolling peaks retain up to 24 hours, network byte totals reset at OS boot. Peaks start accumulating after the metrics collector is installed.
- Admin UI is reached externally at `https://vpn.focuslens.dev` via the TLS Nginx gateway. Direct WAN mapping to `37.208.69.6:9443` is closed; private HTTP remains the gateway upstream.
- WireGuard network: `10.8.0.0/24`.
- Active gateway mode remains `vless`. Default-gateway mode passed route/FORWARD/NAT preflight and is available in Settings; it was deliberately not enabled during deployment.
- Публичный `https://vpn.focuslens.dev` проверен: login отдаёт Basic challenge, `/settings` требует login, `/happ-info` доступна. Прямой WAN mapping к `37.208.69.6:9443` закрыт и снаружи timeout.
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
- Default-gateway mode is direct, unencrypted egress through the server's ordinary uplink, not a VPN tunnel; it is scoped to the WireGuard client subnet and leaves HAPP on its separate outbound.
- Трафик является наблюдаемой нижней границей за срок хранения, не пожизненным биллингом; HAPP показывает upload + download / безлимит. HTTPS paths и неизвестные финальные байты не восстанавливаются.
- Внутренний HTTP backend `192.168.0.39:9443` остаётся доступен по LAN и используется Nginx; приложение по прежнему запросу настроено на `0.0.0.0/0`. LAN-клиенты с прямым доступом могут обходить TLS, если host firewall не ограничивает порт proxy-адресом `.15`.

## Что сознательно не включено

- production secrets;
- actual VLESS links and user UUIDs;
- Basic Auth database;
- wg-easy API secret;
- SQLite backups and runtime state;
- generated `.pyc` files and historical `.before-*` backups.
- live VPN/HAPP/Basic Auth secrets and administrator-entered configs.
