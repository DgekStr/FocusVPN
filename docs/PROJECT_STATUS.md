# Project status

Версия: **v2.2.0** (развёрнута на `.39`, исходники отправлены в `master`; тег не создан, последний опубликованный релиз — v2.1.9). Дата фиксации: **2026-10-10**.

## Вехи

| Веха | Статус | Что сделано |
|---|---|---|
| M1. Базовый sing-box | Done | sing-box 1.14.2, systemd unit, проверка конфигурации |
| M2. Исходящий VLESS | Done | VLESS Reality profiles, urltest и ручной route selector |
| M3. External WireGuard client | Retired in 2.1.9 | Удалены внешний туннель, настройки, endpoints и policy routing; WireGuard-сервер сохранён |
| M4. Server-side split | Done | RU aggregated CIDR direct, LAN/private direct, daily updater |
| M5. Unified admin | Done | одна FocusLens Basic Auth для VLESS и WireGuard |
| M6. WireGuard operations | Done | CRUD, config/QR, activity, per-client LAN deny |
| M7. HAPP Direct | Retired | удалён из UI; server-side split-routing остаётся источником direct-маршрутизации |
| M8. HAPP Server | Done | VLESS Reality inbound `9445`, отдельный provider outbound без внешнего WG-клиента |
| M9. UI consistency | Done | Основной раздел VPN-серверы, пункт VLESS скрыт; live статусы, ping, Settings, журнал, public HAPP QR |
| M10. Publication package | Done | Код, units, документация и обезличенные fixtures; runtime-секреты не входят в пакет |
| M11. JSON server manager | Done | Объект/массив/полный config со всеми серверами sing-box/Xray, flat VLESS settings, VLESS TCP/gRPC, Hysteria2, Trojan, Shadowsocks и TrustTunnel (через локальный клиент-мост с killswitch); import/replace/delete синхронизируют selector и HAPP |
| M12. Background checks | Done | POST не ждёт сеть; последовательная очередь, fingerprint-bound результаты, timeout/retry polling, стартовый баннер заменяется итогом |
| M13. VLESS automation | Done, opt-in | Интервал 1-60 минут, HTTPS задержка, 3 последовательные победы, journal, фоновые Mattermost уведомления с редактируемым шаблоном и смещением UTC |
| M14. Regression coverage | Done | 246 Python-тестов и оба Node VM UI набора; регрессии удаления клиента, HTTP404, сохранения WG-сервера, failover-гистерезиса и сброса статистики HAPP |
| M15. Персональные HAPP аккаунты | Done | UUID/QR, включение, отзыв, удаление, срок UTC; VIP сохраняется |
| M16. Статистика и история | Done | Lifetime totals отдельно от 60-дневной SQLite history, TOP-5, per-user reset (итог, история и скорости, без возврата старых байтов), `/happ-history` chart, XLS/XML, полный сброс с подтверждением, движущийся график скоростей |
| M17. Подписки HAPP | Done server-side | Секретный токен аккаунта, трафик / безлимит, настраиваемое время обновления (10–600 мин, клиенту — целые часы), название с emoji и короткое announce |
| M18. Сетевой доступ | Done, explicit opt-in | `FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0` поддерживается конфигурацией; auth/token checks и защита 51821 сохранены |
| M19. Версия и публикация | v2.1.9 published | VERSION и документация обновлены; тег `v2.1.9`, GitHub Release и `master` опубликованы |
| M20. Default gateway mode | Done, opt-in | Прямой egress через основной gateway для WG-клиентов; route/FORWARD/NAT preflight, LAN deny сохранён |
| M21. Standalone bootstrap | Done | Git-clone bootstrap, checksum-verified sing-box, интерактивный scrypt auth, все systemd units |
| M22. HTTPS panel entry | Done | `vpn.focuslens.dev` Nginx proxy, trusted-proxy IP/session binding, Secure cookie, subscription origin |
| M23. Clean-clone verification | Done | Обновление `.41` из Git-тега v2.1.7 прошло; HAPP config/ключи/VIP/пользователи сохранены, API wg-easy и HTTPS работают. Строка четырёх кнопок проверена браузером на 1440/390 px и на установленном renderer; 130 тестов прошли |
| M24. Host metrics | Done | Settings показывает LAN IP/OS, uptime, SSD-диск (всего / занято), CPU/LAN peaks за 24 часа и boot RX/TX; local collector сохраняет samples в `/mnt/stat/` |
| M25. HAPP live/history UI | Done | Группировка IP/protocol, per-profile lifetime reset, history traffic chart, VIP endpoint из subscription URL |
| M26. Panel favicon | Done | Зелёная Canvas-анимация; сохраняется PNG fallback |
| M27. Gateway failover | Done, opt-in | Проверка шлюза по умолчанию любого типа; 3 неудачные проверки подряд (повтор 60 с), только свежие результаты, переход на самый быстрый сервер, общая gateway/HAPP-транзакция с откатом |
| M28. Mattermost template | Done | Редактируемый шаблон `{date} {time} {old} {new} {source} {latency}`, смещение от UTC, безопасная подстановка по белому списку; доставка подтверждена тестовой кнопкой на тестовом сервере |
| M29. Протоколы HAPP | Done, opt-in | Trojan и Hysteria2 (inbound-ы в HAPP sing-box, ссылки в подписке HAPP) и TrustTunnel v1.1.0 (отдельный unit, loopback SOCKS-мост, ссылка `tt://` и QR; HAPP его не поддерживает); пароли на пользователя, самоподписанный ECDSA-сертификат с закреплением или собственный, транзакция с откатом; Hysteria2 проверен официальным клиентом, в клиенте HAPP — проверяется вручную |
| M30. Версия 2.2.0 | Deployed, pushed, not tagged | `VERSION`, cache-bust CSS/JS и документация — `2.2.0`; развёрнута на `.39` и отправлена в `master`; тег `v2.2.0` и GitHub Release не создавались |

## Проверенные runtime-факты

- OS: Ubuntu 22.04 LTS.
- sing-box: 1.14.2.
- HAPP inbound: `0.0.0.0:9445`.
- Admin panel: `0.0.0.0:9443`.
- Test deployment `192.168.0.41`: Nginx HTTPS `:7445` использует self-signed сертификат; backend слушает `127.0.0.1:9443`, wg-easy API закрыт извне и работает. HAPP запущен/включён на `9445` после подтверждённой генерации ключей, JSON/ссылки согласованы, персональные пользователи создаются. Gateway sing-box отдельно не включался.
- Installer v2.1.5 перечисляет пакеты/службы до подтверждения, автоматически создаёт служебного администратора wg-easy и проверяет API панели. Credentials имеют права `0600 root:root`, пароль не остаётся в container environment и сохраняется при upgrade; HTTPS `:7445` отвечает, прямой `:51821` закрыт. Legacy backup/cancel ветка на `.41` не выполнялась.
- Server metrics use `/proc` counters; rolling peaks retain up to 24 hours, network byte totals reset at OS boot. Peaks start accumulating after the metrics collector is installed.
- Admin UI is reached externally at `https://vpn.focuslens.dev` via the TLS Nginx gateway. Direct WAN mapping to `37.208.69.6:9443` is closed; private HTTP remains the gateway upstream.
- WireGuard network: `10.8.0.0/24`.
- Active gateway mode remains `vless`. Default-gateway mode passed route/FORWARD/NAT preflight and is available in Settings; it was deliberately not enabled during deployment.
- Публичный `https://vpn.focuslens.dev` проверен: login отдаёт Basic challenge, `/settings` требует login, `/happ-info` доступна. Прямой WAN mapping к `37.208.69.6:9443` закрыт и снаружи timeout.
- Поддерживаются только VLESS/default; внешний WireGuard mode и WG outbound для HAPP удалены.
- Dedicated auto-8 settings are removed. Tags such as `auto-8` can be created again through JSON import; they are not reserved for a particular protocol.
- The sing-box status indicator is on `/outbounds`. Background HTTPS checks do not change the active route; saved errors are shown as errors, not permanent pending notifications.
- Автопроверка и автовыбор opt-in, default disabled, интервал 5 минут. Настройки и выбранный маршрут управляются владельцем; документация не фиксирует их как неизменный live-state.
- Admin, gateway sing-box and HAPP services were active at the snapshot. `active` and a valid config do not prove network connectivity.
- Подписки проверены реальными HTTP-запросами: аккаунтная изоляция, отзыв, UTF-8 metadata, total=0, интервал 1 час. Windows HAPP UI/import не проверялся автоматизированно.
- /happ-info содержит только информационный текст с justify; не выдаёт credentials. Нативное выравнивание announce управляется HAPP.

## Ограничения

- XHTTP не конвертируется в TCP/gRPC: пакетный импорт выводит явный пропуск.
- TrustTunnel: HAPP протокол не поддерживает (ссылка `tt://` открывается в приложении TrustTunnel); его трафик идёт через анонимный loopback SOCKS-мост и не попадает в статистику пользователей.
- TrustTunnel-серверы в списке VPN-серверов требуют клиента `/opt/trusttunnel/trusttunnel_client` (установщик ставит v1.1.11) и локальных портов `19500-19599`; нельзя импортировать профиль, который указывает на собственный HAPP TrustTunnel endpoint (петля маршрута). Самоподписанный сертификат Trojan может не приниматься клиентами, игнорирующими закрепление отпечатка.
- WireGuard outbound из Xray не поддерживается; отдельный внешний WG-client раздел удалён.
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
