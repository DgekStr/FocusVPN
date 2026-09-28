# Project status

Дата фиксации: **2026-09-29**

## Вехи

| Веха | Статус | Что сделано |
|---|---|---|
| M1. Базовый sing-box | Done | sing-box 1.14.2, systemd unit, проверка конфигурации |
| M2. Исходящий VLESS | Done | VLESS Reality profiles, urltest и ручной route selector |
| M3. WireGuard gateway | Done | TPROXY только для `10.8.0.0/24`, policy routing и автозапуск |
| M4. Server-side split | Done | RU aggregated CIDR direct, LAN/private direct, daily updater |
| M5. Unified admin | Done | одна FocusLens Basic Auth для VLESS и WireGuard |
| M6. WireGuard operations | Done | CRUD, config/QR, activity, per-client LAN deny |
| M7. HAPP Direct | Done | routing deeplink с direct Russian services |
| M8. HAPP Server | Done | VLESS Reality inbound `9445`, outbound через provider VLESS |
| M9. UI consistency | Done | SPA shell, shared CSS, CSP, QR modal, responsive WireGuard dashboard |
| M10. Publication package | Done (local) | код, units, docs и sanitized previews перенесены; remote настроен, push не выполнялся |

## Проверенные runtime-факты

- OS: Ubuntu 22.04 LTS.
- sing-box: 1.14.2.
- HAPP inbound: `0.0.0.0:9445`.
- Admin panel: `0.0.0.0:9443`.
- WireGuard network: `10.8.0.0/24`.
- Current provider route: managed by sing-box route selector.
- All production services were active at the last verification.

## Что сознательно не включено

- production secrets;
- actual VLESS links and user UUIDs;
- Basic Auth database;
- wg-easy API secret;
- SQLite backups and runtime state;
- generated `.pyc` files and historical `.before-*` backups.
