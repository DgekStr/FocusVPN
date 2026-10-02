# Project status

Дата фиксации: **2026-10-02**

## Вехи

| Веха | Статус | Что сделано |
|---|---|---|
| M1. Базовый sing-box | Done | sing-box 1.14.2, systemd unit, проверка конфигурации |
| M2. Исходящий VLESS | Done | VLESS Reality profiles, urltest и ручной route selector |
| M3. WireGuard gateway | Enabled; endpoint egress pending | Mode `wireguard`, source-policy routing `10.8.0.0/24` через `wg-client`, LAN bypass, scoped FORWARD/NAT; внешний peer `45.9.116.207` принимает handshake, но интернет-ответы endpoint-side не подтверждены |
| M4. Server-side split | Done | RU aggregated CIDR direct, LAN/private direct, daily updater |
| M5. Unified admin | Done | одна FocusLens Basic Auth для VLESS и WireGuard |
| M6. WireGuard operations | Done | CRUD, config/QR, activity, per-client LAN deny |
| M7. HAPP Direct | Retired | удалён из UI; server-side split-routing остаётся источником direct-маршрутизации |
| M8. HAPP Server | Done | VLESS Reality inbound `9445`; в WireGuard mode HAPP outbound помечается и идёт через `wg-client`, inbound остаётся активен |
| M9. UI consistency | Done | единый main-page shell, Settings, public-only HAPP QR и responsive WireGuard dashboard |
| M10. Publication package | Done | код, units, docs и sanitized previews перенесены; repository опубликован, новые UI-изменения ожидают отдельного review/commit |

## Проверенные runtime-факты

- OS: Ubuntu 22.04 LTS.
- sing-box: 1.14.2.
- HAPP inbound: `0.0.0.0:9445`.
- Admin panel: `0.0.0.0:9443`.
- WireGuard network: `10.8.0.0/24`.
- Gateway mode: `wireguard`; internet policy route via `wg-client`, LAN via main route.
- HAPP route in WireGuard mode: `focusvpn-wg-direct` with `routing_mark=2`.
- External endpoint `45.9.116.207:51820` completes WireGuard handshake; return traffic/NAT on the endpoint is not confirmed.
- `auto-8` Hysteria2 conversion is available in Settings; enter the complete Hysteria2 auth in the protected password field before applying. The provided message has a masked auth value, so production auto-8 remains unchanged until then.
- All production services were active at the last verification.

## Что сознательно не включено

- production secrets;
- actual VLESS links and user UUIDs;
- Basic Auth database;
- wg-easy API secret;
- SQLite backups and runtime state;
- generated `.pyc` files and historical `.before-*` backups.
