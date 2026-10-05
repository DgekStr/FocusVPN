# Changelog

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
- 68 Python-тестов, два Node VM UI-сценария и локальные publication checks.

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