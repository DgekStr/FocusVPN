import html
import datetime as dt

from happ_server import load_state, happ_add_link, public_vless_link
from happ_stats import format_datetime
from panel_ui import render_shell


def page(users=None, csrf='', message='', kind='success', events=None, traffic=None, subscriptions=None, vip_vless_link=None, endpoint_host=None):
    data = load_state()
    vless_link = vip_vless_link or public_vless_link()
    subscriptions = subscriptions or {}
    traffic = traffic or {}
    happ_link = happ_add_link(subscriptions.get('VIP', vless_link))
    endpoint_host = str(endpoint_host or data.get('server') or '—')
    if ':' in endpoint_host and not endpoint_host.startswith('['):
      endpoint_host = '[' + endpoint_host + ']'
    users = users or []
    rows = []
    labels = {'enabled': 'Включён', 'disabled': 'Отключён', 'expired': 'Срок истёк'}
    esc = html.escape
    for user in users:
        user_id = esc(user['id'])
        enabled = user['status'] == 'enabled'
        expiry = user.get('expires_at')
        expiry_field = dt.datetime.fromisoformat(expiry).strftime('%Y-%m-%dT%H:%M') if expiry else ''
        subscription = subscriptions.get(user['id'])
        subscription_button = f'<button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(subscription)}">Копировать подписку</button>' if subscription else ''
        user_happ_link = happ_add_link(subscription or user['link'])
        totals = traffic.get('personal-' + user['id'], {})
        account_stats = f'<div class="happ-account-traffic"><span class="muted" data-happ-account="personal-{user_id}" title="Нарастающий итог с момента включения накопительной статистики">Скачано: <strong data-account-download>{esc(totals.get("download", "0 B"))}</strong> / Отправлено: <strong data-account-upload>{esc(totals.get("upload", "0 B"))}</strong></span><form method="post" action="/happ-users/traffic/reset" data-happ-traffic-reset><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><button class="secondary happ-stat-reset" type="submit" aria-label="Сбросить статистику {esc(user["name"])}">Сбросить</button></form></div>'
        rows.append(f'''<tr><td><strong>{esc(user['name'])}</strong><br><span class="badge {'ok' if enabled else 'bad'}">{labels[user['status']]}</span></td><td>{esc(expiry or 'Бессрочно')}</td><td><div class="inline-actions"><button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(user['link'])}">Копировать ссылку</button><button class="secondary" type="button" data-qr-open="personal" data-qr-url="/happ-users/{user_id}/qr" data-qr-label="{esc(user['name'])}">QR</button>{subscription_button}<a class="button secondary" href="{esc(user_happ_link)}" data-happ-action="open" data-happ-link="{esc(user_happ_link)}">Открыть HAPP</a>{account_stats}</div></td><td><form method="post" action="/happ-users/action"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><div class="inline-actions"><button class="secondary" name="operation" value="{'disable' if enabled else 'enable'}">{'Отключить' if enabled else 'Включить'}</button><button class="danger" name="operation" value="delete" data-happ-user-delete>Удалить</button></div></form><details><summary>Изменить имя и срок</summary><form method="post" action="/happ-users/action"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><input type="hidden" name="operation" value="update"><div class="field"><label>Имя</label><input name="name" value="{esc(user['name'])}" maxlength="80" required></div><div class="field"><label>Срок доступа UTC</label><input name="expires_at" type="datetime-local" value="{esc(expiry_field)}"></div><div class="actions"><button class="secondary" type="submit">Сохранить</button></div></form></details></td></tr>''')
    rows = ''.join(rows) or '<tr><td colspan="4" class="empty">Персональных пользователей пока нет.</td></tr>'
    banner = f'<div class="notice {"error" if kind == "error" else "success"}" role="status">{esc(message)}</div>' if message else ''
    event_labels = {'create': 'Создан', 'enable': 'Включён', 'disable': 'Отключён', 'delete': 'Удалён', 'update': 'Настройки изменены', 'expired': 'Срок истёк'}
    event_rows = ''.join(f'<tr><td>{esc(format_datetime(item.get("at")))}</td><td>{esc(item.get("name", ""))}</td><td>{esc(event_labels.get(item.get("operation"), item.get("operation", "")))}</td></tr>' for item in (events or []))
    body = f'''<section class="page-head">
  <div><p class="eyebrow">Public endpoint</p><h1>HAPP Server</h1><p class="subtitle">Отдельный VLESS Reality вход для HAPP и совместимых клиентов.</p></div>
  <div class="status"><span class="status-dot ok"></span>public</div>
</section>
{banner}
<section class="panel-stack">
  <section class="panel" data-happ-live>
    <div class="detail-head"><div><h2>Подключения HAPP</h2><p class="subtitle">Скачивание · максимальные пики</p></div><span class="badge" data-happ-chart-status role="status">Ожидание данных</span></div>
    <div class="happ-chart-range"><label for="happ_chart_range">Отрезок истории</label><select id="happ_chart_range" data-happ-chart-range><option value="10">10 минут</option><option value="30">30 минут</option><option value="60">60 минут</option><option value="90">90 минут</option></select></div>
    <div class="happ-live-totals"><div><span>Соединения</span><strong data-happ-online>0</strong></div><div><span>Скачивание</span><strong data-happ-speed>0.00 МБ/с</strong></div><div><span>На графике</span><strong data-happ-chart-count>0 / 10</strong></div></div>
    <div class="happ-chart-plot"><canvas data-happ-traffic-chart role="img" aria-label="Скорость скачивания пользователей HAPP, МБ в секунду"></canvas></div>
    <div class="happ-chart-legend" data-happ-chart-legend><p class="muted">Ожидание истории скоростей.</p></div>
    <div class="happ-chart-footer"><span class="muted" data-happ-chart-overflow></span><a class="button secondary" href="/happ-history">История HAPP</a></div>
  </section>
  <section class="panel"><div class="detail-head happ-users-heading"><h2>Пользователи HAPP</h2><button type="button" data-happ-user-create-open>Добавить нового пользователя</button></div><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Срок UTC</th><th>Персональная ссылка</th><th>Доступ</th></tr></thead><tbody>{rows}</tbody></table></div><p class="muted">Изменение доступа перезапускает HAPP-сервис и переподключает сессии. VIP UUID и ссылка сохраняются.</p></section>
  <section class="panel happ-traffic-panel"><div class="detail-head"><div><h2>TOP-5 по трафику</h2><p class="subtitle">Скачано и отправлено по активным пользователям</p></div></div><div class="happ-traffic-top" data-happ-top-users aria-live="polite"><p class="muted">Загрузка live-метрик…</p></div></section>
  <section class="panel"><h2>Журнал персонального доступа</h2><div class="table-wrap"><table><thead><tr><th>Время UTC</th><th>Пользователь</th><th>Действие</th></tr></thead><tbody>{event_rows or '<tr><td colspan="3" class="empty">Событий пока нет.</td></tr>'}</tbody></table></div></section>
  <section class="panel"><h2>VIP VLESS · существующая ссылка</h2>
    <div class="meta"><span>Endpoint: {html.escape(endpoint_host)}:9445</span><span>SNI: {html.escape(data.get('sni', '—'))}</span><span>Flow: xtls-rprx-vision</span></div>
    <div class="actions"><a class="button" href="{html.escape(happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(happ_link, quote=True)}">Открыть в HAPP</a><button class="button secondary" type="button" data-qr-open="public">Показать QR</button><button class="button secondary" type="button" data-happ-action="copy" data-happ-link="{html.escape(vless_link, quote=True)}">Скопировать vless://</button></div>
    <textarea class="public-link-field" aria-label="Public VLESS link" readonly rows="3" spellcheck="false">{html.escape(vless_link)}</textarea>
  </section>
</section>
<div class="qr-modal" data-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="qr-title"><button class="qr-close" type="button" data-qr-close aria-label="Закрыть">×</button><h2 id="qr-title">HAPP QR</h2><p data-qr-caption>Отсканируйте QR-код в HAPP.</p><img class="qr-image" data-qr-image alt="QR-код HAPP"></div></div>
<dialog class="gateway-dialog" data-happ-user-create-dialog aria-labelledby="happ-user-create-title"><form method="post" action="/happ-users/create" data-happ-user-create-form><h2 id="happ-user-create-title">Добавить нового пользователя</h2><input type="hidden" name="csrf" value="{esc(csrf)}"><div class="form-grid"><div class="field"><label for="happ_user_name">Имя пользователя</label><input id="happ_user_name" name="name" maxlength="80" autocomplete="off" required autofocus></div><div class="field"><label for="happ_user_expiry">Срок UTC · пусто = бессрочно</label><input id="happ_user_expiry" name="expires_at" type="datetime-local"></div></div><div class="actions"><button class="secondary" type="button" data-happ-user-create-cancel>Отмена</button><button type="submit">Создать пользователя</button></div></form></dialog>
<dialog class="gateway-dialog" data-happ-user-delete-dialog><form method="dialog"><h2>Удалить персональный доступ?</h2><p>Персональная ссылка перестанет работать. VIP-ссылка сохранится.</p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button class="danger" type="button" data-happ-user-delete-confirm>Удалить</button></div></form></dialog>
<dialog class="gateway-dialog" data-happ-traffic-reset-dialog><form method="dialog"><h2>Сбросить накопительную статистику?</h2><p data-happ-traffic-reset-message>Скачано и отправлено обнулятся только для выбранного пользователя. Дальше будут учитываться новые байты.</p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button class="danger" type="button" data-happ-traffic-reset-confirm>Сбросить</button></div></form></dialog>'''
    return render_shell('HAPP Server', body, 'happ-server')
