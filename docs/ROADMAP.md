# Roadmap

Версия: **v2.1.4**. Дата среза: **2026-10-06**.

## Закрыто

- [x] Развернуть sing-box и VLESS outbound.
- [x] Ограничить TPROXY клиентами WireGuard.
- [x] Добавить RU direct routing и автообновление CIDR.
- [x] Объединить VLESS и WireGuard в одной панели.
- [x] Добавить live WireGuard activity и per-client LAN policy.
- [x] Добавить HAPP Server с public VLESS/QR.
- [x] Убрать HAPP Direct из UI; direct-маршрутизация остаётся server-side.
- [x] Выдержать единый shell главной страницы, CSS, SPA-навигацию, Settings и QR flows.
- [x] Подготовить публикационный пакет без секретов.
- [x] Добавить внешнего WireGuard-клиента с переключением режимов и LAN bypass.
- [x] Синхронизировать выбранный маршрут и серверные профили gateway/HAPP с совместным rollback.
- [x] Сделать VPN-серверы основным разделом; скрыть отдельный пункт VLESS и специальную форму auto-8.
- [x] Добавить импорт sing-box/Xray JSON и массивов, замену и удаление профилей, включая Trojan/Shadowsocks.
- [x] Выполнять HTTPS-проверки в фоне без блокировки POST и configuration lock.
- [x] Показывать очередь, ping, время и ошибку; исправить вечный стартовый баннер и добавить timeout/retry polling.
- [x] Добавить последовательную VLESS автопроверку 1-60 минут и переключение после 3 побед одного профиля.
- [x] Создать приватный журнал и асинхронную отправку Mattermost с кнопкой проверки webhook.
- [x] Добавить regression suites для маршрутов/rollback, import, scheduler, notifications и browser polling.
- [x] Подключить CRM admin bridge к существующей панели с server-side role/origin checks, service-token allowlist и encrypted password mode.
- [x] Добавить combined CRM/VPN preview renderer с synthetic API fixtures и role-aware embed.
- [x] Исправить Settings return flow после сохранения HAPP history retention; покрыть persistence, validation и CSRF regression.
- [x] Добавить default gateway mode с route/forward/NAT preflight; проверить WireGuard-to-default и VLESS-to-default transitions без смены production mode.
- [x] Выравнять Settings панели в равные responsive 2-column rows; вынести gateway selector наверх.
- [x] Добавить bootstrap с temporary Git clone, dependency downloads, secure password prompt и полным unit validation.
- [x] Настроить HTTPS reverse proxy `vpn.focuslens.dev` с real-client session binding и обновляемым subscription origin.
- [x] Добавить HAPP lifetime traffic totals отдельно от retention истории, TOP-5 и адресный reset конкретного профиля.
- [x] Сгруппировать live HAPP connections по IP/protocol с summed duration/bytes и destination последнего соединения.
- [x] Добавить `/happ-history` traffic chart с per-client download/upload, числами и descending order.
- [x] Добавить host OS/LAN IP, uptime, 24h CPU/LAN peaks и boot network totals в Settings.
- [x] Перенести VIP VLESS endpoint в конец `/happ-server` и связать его hostname с public subscription URL.
- [x] Добавить анимированный зелёный favicon, совместимый с Chromium через Canvas frames.
- [x] Проверить локальный Git-clone installer path: 110 Python tests, две Node UI suites, publication validation и installer help.
- [x] Исправить sing-box installer checksum: использовать SHA-256 asset digest из GitHub Release API с fail-closed проверкой.
- [x] Повторить Git bootstrap на Ubuntu 22.04 amd64, задать доступ панели `0.0.0.0/0`, поднять TLS proxy `:7445` с Basic Auth; VPN-службы оставить остановленными до заполнения реальных конфигураций.
- [x] Перед apt показывать список пакетов и служб и требовать подтверждение; запускать Nginx и wg-easy автоматически с защищённым setup-портом.
- [x] Обнаруживать legacy WireGuard и после отдельного согласия сохранять старые настройки в резервную копию.

## Следующие вехи

- [ ] Включить HTTPS для панели и подписок перед использованием в недоверенных сетях.
- [ ] Подтвердить импорт, emoji, announce, infinity и автообновление на реальных Windows/Android/iOS клиентах HAPP.
- [ ] Добавить автоматические smoke-тесты для всех systemd units.
- [ ] Добавить CI-проверку Python, JavaScript и конфигурационных примеров.
- [ ] Расширить текущие live результаты агрегированным health dashboard и метриками длительной доступности.
- [ ] Версионировать schema/config migration для панели.
- [ ] Добавить безопасный deploy script с dry-run и rollback.
- [x] Обновить screenshots v2.0 через актуальные renderer с синтетическими данными без production secrets.
- [ ] Проверить/исправить внешний WireGuard egress на стороне endpoint и подтвердить возвратный трафик.
- [ ] Подтвердить Mattermost delivery после настройки реального webhook.
- [ ] При необходимости добавить отдельный Xray backend для XHTTP; не заменять транспорт при импорте.

## Критерий готовности к публикации

Перед публикацией проверять `git diff`, отсутствие runtime-секретов, Python regression suites, browser Node VM scenario и publication checks. CI ещё предстоит настроить; локальный проход тестов не означает наличие CI. Коммиты и push выполняются только по явной команде владельца.

## Сверка milestones — 2026-10-04

### Закрыто в текущем срезе

- CRM bridge развёрнут на двух сторонах; root-only shared token и network allowlist настроены отдельно от Git.
- Panel renderer собирает embedded CRM/VPN preview; regression проверяет synthetic data и role boundary.
- HAPP history retention Settings save/validation flow исправлен и протестирован.

### Осталось

- Автоматические smoke-тесты systemd units и CI pipeline.
- Production browser acceptance на Windows/Android/iOS HAPP клиентах для subscribe metadata, emoji, announce и refresh.
- Проверка внешнего WireGuard egress и возвратного трафика.
- Безопасный deploy script с dry-run/rollback и schema/config migration versioning.
- Authenticated health/latency dashboard и ручная проверка Mattermost доставки после настройки действующего webhook.

Текущая версия FocusVPN — `v2.1.4`; CRM release `1.0.0.0.9` не меняет версию VPN. Production gateway оставлен в VLESS. Установочный поток подтверждает пакеты/службы, безопасно обрабатывает legacy WireGuard и автоматически запускает защищённый wg-easy setup и Nginx HTTPS endpoint. На Ubuntu 22.04 прошёл full install smoke; реальные VPN-конфигурации не установлены. Host metrics peaks покрывают период с запуска collector, network totals сбрасываются при reboot.
