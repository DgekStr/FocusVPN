# Server package

Этот каталог содержит переносимые артефакты текущего production-развёртывания FocusVPN.

## Contents

- `panel/` — Python stdlib admin application and static UI.
- `libexec/` — nftables, policy-routing, RU zone updater and LAN policy scripts.
- `systemd/` — units and sing-box gateway drop-in.
- `config/` — safe rules and placeholders only.

## Deploy boundary

Файлы рассчитаны на установку в `/opt/sing-box-admin`, `/usr/local/libexec`, `/etc/systemd/system` и `/etc/sing-box`. Production secrets создаются отдельно на сервере. Не запускайте копирование всей папки поверх production без проверки placeholders, владельцев и прав.

Панель использует Python standard library; external Python dependencies не требуются. `qrencode` нужен для QR endpoint HAPP.
