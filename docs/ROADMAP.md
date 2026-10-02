# Roadmap

Версия: **v2.0**. Дата: **2026-10-03**.

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

## Следующие вехи

- [ ] Включить HTTPS для панели и подписок перед использованием в недоверенных сетях.
- [ ] Подтвердить импорт, emoji, announce, infinity и автообновление на реальных Windows/Android/iOS клиентах HAPP.
- [ ] При необходимости отделить пожизненные traffic totals от retention подробной истории.

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
