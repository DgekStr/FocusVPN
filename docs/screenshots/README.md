# Screenshots

Версия **v2.0**, дата **2026-10-03**. Изображения сняты браузером с актуальных серверных renderer и синтетических fixtures. Это не скриншоты реальных пользовательских данных и не доказательство доступности демонстрационных VPN-профилей.

- `project-overview.png` — статус и границы публикационного пакета.
- `wireguard-and-happ-preview.png` — актуальный HAPP Server: персональные пользователи, подписки, накопленные и активные счётчики. Историческое имя файла сохранено для совместимости ссылок.
- `vpn-servers.png` — импорт профилей, состояние проверки, задержка и UTC дата.
- `happ-history.png` — пользовательские фильтры, UTC даты, наблюдаемый трафик и экспорт XLS.

Production secrets, реальные VLESS links и клиентские данные в screenshots отсутствуют.

Для воспроизведения: `py -3 scripts/render_previews.py --serve --port 8788`. Preview слушает только `127.0.0.1`; POST запрещён, все значения фиктивные. Снимайте `/happ-server`, `/outbounds`, `/happ-history` и `/` через Playwright screenshot с нужным viewport. Без `--serve` команда обновляет только `docs/ui-preview.html`.

Если встроенный браузер сбрасывает viewport при screenshot, используйте `--capture`: явно включаемый loopback adapter принимает только PNG для четырёх фиксированных имён с заголовком `X-Preview-Capture: renderer-fixture-only`. В текущей серии renderer сняты через DevTools с capture-only desktop CSS preset из неизменённого panel.css; production CSS и содержимое не подменялись. Этот adapter не устанавливается как production API. Ширина desktop 1440 px.
