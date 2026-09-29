# Roadmap

Дата: **2026-09-29**

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

## Следующие вехи

- [ ] Добавить автоматические smoke-тесты для всех systemd units.
- [ ] Добавить CI-проверку Python, JavaScript и конфигурационных примеров.
- [ ] Добавить health dashboard с последним успешным upstream check.
- [ ] Версионировать schema/config migration для панели.
- [ ] Добавить безопасный deploy script с dry-run и rollback.
- [ ] Подготовить production screenshots после авторизованного browser smoke.

## Критерий готовности к публикации

Проект можно публиковать после проверки, что git status содержит только ожидаемые файлы, секретные шаблоны отсутствуют, а CI проходит локальные syntax checks. Push намеренно оставлен ручным действием владельца проекта.
