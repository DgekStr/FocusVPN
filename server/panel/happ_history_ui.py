import html
from urllib.parse import urlencode

from happ_stats import format_bytes, format_datetime
from panel_ui import render_shell


def render_history(history, query, message='', kind='success', registered_users=None, csrf=''):
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
    xml_export_url = '/happ-history.xml?' + urlencode(params)
    pages = max(1, (result['count'] + 99) // 100)
    previous_url = '/happ-history?' + urlencode({**params, 'page': max(1, page - 1)})
    next_url = '/happ-history?' + urlencode({**params, 'page': min(pages, page + 1)})
    banner = f'<div class="notice {"error" if kind == "error" else "success"}" role="status">{esc(message)}</div>' if message else ''
    if result['collector_error']:
        banner += f'<div class="notice error" role="status">{esc(result["collector_error"])}</div>'
    chart_rows = history.traffic_chart(user, since, until)
    chart_max = max((max(int(item['download_bytes']), int(item['upload_bytes'])) for item in chart_rows), default=0)
    chart_groups = []
    for item in chart_rows:
        download_bytes = max(0, int(item['download_bytes']))
        upload_bytes = max(0, int(item['upload_bytes']))
        download_height = download_bytes / chart_max * 100 if chart_max else 0
        upload_height = upload_bytes / chart_max * 100 if chart_max else 0
        chart_groups.append(
            f'<div class="history-chart-client" title="{esc(item["user_name"])}">'
            '<div class="history-chart-bars">'
            f'<span class="history-chart-bar download" style="height:{download_height:.4f}%" title="Скачано: {esc(format_bytes(download_bytes))}" aria-label="Скачано {esc(format_bytes(download_bytes))}"></span>'
            f'<span class="history-chart-bar upload" style="height:{upload_height:.4f}%" title="Отправлено: {esc(format_bytes(upload_bytes))}" aria-label="Отправлено {esc(format_bytes(upload_bytes))}"></span>'
            '</div>'
            f'<span class="history-chart-name">{esc(item["user_name"])}</span>'
            f'<span class="history-chart-values"><span class="download">{esc(format_bytes(download_bytes))}</span><span class="upload">{esc(format_bytes(upload_bytes))}</span></span>'
            '</div>'
        )
    chart_content = ''.join(chart_groups) or '<p class="muted history-chart-empty">Нет данных трафика за выбранный период.</p>'
    chart = (
        '<section class="panel history-chart-panel" aria-label="График трафика пользователей">'
        '<div class="detail-head"><div><h2>Трафик по клиентам</h2>'
        '<p class="subtitle">Сумма скачанного и отправленного трафика за выбранный период</p></div>'
        '<div class="history-chart-legend"><span><i class="download"></i>Скачано</span><span><i class="upload"></i>Отправлено</span></div></div>'
        f'<div class="history-chart-scroll"><div class="history-chart-plot" style="--client-count:{max(1, len(chart_rows))}">{chart_content}</div></div>'
        '</section>'
    )
    body = f'''<section class="page-head"><div><h1>История HAPP</h1><p class="subtitle">UTC · хранение {history.retention_days()} дней · /mnt/stat/</p></div><div class="inline-actions"><a class="button secondary" href="{esc(export_url)}" download>Экспорт XLS</a><a class="button secondary" href="{esc(xml_export_url)}" download>Экспорт XML</a><button class="danger" type="button" data-happ-history-reset-open>Полный сброс статистики</button></div></section>
{banner}
{chart}
<section class="panel"><form method="get" action="/happ-history"><div class="form-grid"><div class="field full"><label for="history_user">Пользователь</label><select id="history_user" name="user">{option_markup}</select></div><div class="field"><label for="history_since">От даты UTC</label><input id="history_since" name="since" type="date" value="{esc(since)}"></div><div class="field"><label for="history_until">До даты UTC включительно</label><input id="history_until" name="until" type="date" value="{esc(until)}"></div></div><div class="actions"><button type="submit">Применить фильтр</button><a class="button secondary" href="/happ-history">Сбросить</a></div></form><p class="muted">Записей: {result['count']}. Наблюдаемо скачано: {format_bytes(result['download_bytes'])}; отправлено: {format_bytes(result['upload_bytes'])}. Без финальных счётчиков: {result['unknown_traffic']}.</p><p class="muted">Последний сбор: {esc(result['last_collected_at'] or 'ещё не выполнялся')}. Доступны домен/IP и порт назначения; полный URL HTTPS не виден. Байты закрытых между опросами сессий могут быть неполными.</p><div class="table-wrap"><table><thead><tr><th>Начало UTC</th><th>Последнее наблюдение UTC</th><th>Пользователь</th><th>IP:порт клиента</th><th>Посещённый домен / IP:порт</th><th>Протокол</th><th>Скачано</th><th>Отправлено</th><th>Состояние</th></tr></thead><tbody>{table_rows}</tbody></table></div><div class="actions"><a class="button secondary" href="{esc(previous_url)}">Назад</a><span>Страница {page} из {pages}</span><a class="button secondary" href="{esc(next_url)}">Далее</a></div></section>
<dialog class="gateway-dialog" data-happ-history-reset-dialog aria-labelledby="happ-history-reset-title"><form method="post" action="/happ-history/reset" data-happ-history-reset-form><h2 id="happ-history-reset-title">Полный сброс статистики HAPP</h2><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="confirm" value="reset-all"><p class="dialog-warning">Внимание: действие необратимо.</p><p>Будут безвозвратно удалены история подключений, накопительные Download/Upload всех пользователей, графики скоростей и пики. Пользователи, ключи, подписки и настройки не изменятся.</p><p>Подключения, активные в момент сброса, будут учитываться заново с нуля. Перед сбросом можно сохранить все данные: <a href="/happ-history.xls" download>XLS</a> · <a href="/happ-history.xml" download>XML</a>.</p><div class="actions"><button class="secondary" type="button" data-happ-history-reset-cancel>Отмена</button><button class="danger" type="submit">Сбросить всю статистику</button></div></form></dialog>'''
    return render_shell('История HAPP', body, 'happ-history')