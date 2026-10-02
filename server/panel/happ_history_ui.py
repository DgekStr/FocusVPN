import html
from urllib.parse import urlencode

from happ_stats import format_bytes, format_datetime
from panel_ui import render_shell


def render_history(history, query, message='', kind='success', registered_users=None):
    def value(key):
        return query.get(key, [''])[0].strip()

    user = value('user')
    since = value('since')
    until = value('until')
    page = max(1, int(value('page') or '1'))
    result = history.query(user, since, until, page)
    result = {**result, 'last_collected_at': format_datetime(result['last_collected_at'], 'ещё не выполнялся')}
    esc = html.escape
    options = {item['user_key']: item['user_name'] for item in history.user_options()}
    options.update({'VIP': 'VIP', 'unknown': 'Не определён'})
    options.update({'personal-' + item['id']: item['name'] for item in (registered_users or [])})
    option_markup = '<option value="">Все пользователи</option>' + ''.join(f'<option value="{esc(key)}"{" selected" if key == user else ""}>{esc(name)}</option>' for key, name in sorted(options.items(), key=lambda item: item[1].casefold()))
    rows = []
    for item in result['rows']:
        item = {**item, 'started_at': format_datetime(item['started_at']), 'last_seen_at': format_datetime(item['last_seen_at'])}
        download = format_bytes(item['download_bytes']) if item['download_bytes'] is not None else 'Неизвестно'
        upload = format_bytes(item['upload_bytes']) if item['upload_bytes'] is not None else 'Неизвестно'
        rows.append(f'<tr><td>{esc(item["started_at"])}</td><td>{esc(item["last_seen_at"])}</td><td>{esc(item["user_name"])}</td><td>{esc(item["source_ip"])}:{item["source_port"] or "—"}</td><td>{esc(item["destination"])}</td><td>{esc(item["network"])}</td><td>{download}</td><td>{upload}</td><td>{"Активно" if item["active"] else "Закрыто / не наблюдается"}</td></tr>')
    table_rows = ''.join(rows) or '<tr><td colspan="9" class="empty">По выбранным фильтрам записей нет.</td></tr>'
    params = {'user': user, 'since': since, 'until': until}
    export_url = '/happ-history.xls?' + urlencode(params)
    pages = max(1, (result['count'] + 99) // 100)
    previous_url = '/happ-history?' + urlencode({**params, 'page': max(1, page - 1)})
    next_url = '/happ-history?' + urlencode({**params, 'page': min(pages, page + 1)})
    banner = f'<div class="notice {"error" if kind == "error" else "success"}" role="status">{esc(message)}</div>' if message else ''
    if result['collector_error']:
        banner += f'<div class="notice error" role="status">{esc(result["collector_error"])}</div>'
    body = f'''<section class="page-head"><div><h1>История HAPP</h1><p class="subtitle">UTC · хранение {history.retention_days()} дней · /mnt/stat/</p></div><a class="button secondary" href="{esc(export_url)}" download>Экспорт XLS</a></section>
{banner}
<section class="panel"><form method="get" action="/happ-history"><div class="form-grid"><div class="field full"><label for="history_user">Пользователь</label><select id="history_user" name="user">{option_markup}</select></div><div class="field"><label for="history_since">От даты UTC</label><input id="history_since" name="since" type="date" value="{esc(since)}"></div><div class="field"><label for="history_until">До даты UTC включительно</label><input id="history_until" name="until" type="date" value="{esc(until)}"></div></div><div class="actions"><button type="submit">Применить фильтр</button><a class="button secondary" href="/happ-history">Сбросить</a></div></form><p class="muted">Записей: {result['count']}. Наблюдаемо скачано: {format_bytes(result['download_bytes'])}; отправлено: {format_bytes(result['upload_bytes'])}. Без финальных счётчиков: {result['unknown_traffic']}.</p><p class="muted">Последний сбор: {esc(result['last_collected_at'] or 'ещё не выполнялся')}. Доступны домен/IP и порт назначения; полный URL HTTPS не виден. Байты закрытых между опросами сессий могут быть неполными.</p><div class="table-wrap"><table><thead><tr><th>Начало UTC</th><th>Последнее наблюдение UTC</th><th>Пользователь</th><th>IP:порт клиента</th><th>Посещённый домен / IP:порт</th><th>Протокол</th><th>Скачано</th><th>Отправлено</th><th>Состояние</th></tr></thead><tbody>{table_rows}</tbody></table></div><div class="actions"><a class="button secondary" href="{esc(previous_url)}">Назад</a><span>Страница {page} из {pages}</span><a class="button secondary" href="{esc(next_url)}">Далее</a></div></section>'''
    return render_shell('История HAPP', body, 'happ-history')