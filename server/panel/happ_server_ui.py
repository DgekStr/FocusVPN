import html
import datetime as dt

from happ_server import happ_add_link
from happ_stats import format_datetime
from panel_ui import render_shell


def protocol_buttons(user, links, esc):
    buttons = ''
    if links.get('trojan'):
        name = esc(user['name'])
        buttons += (
            f'<button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(links["trojan"])}" title="Ссылка Trojan; она уже входит в подписку HAPP">Trojan</button>'
            f'<button class="secondary" type="button" data-qr-open="trojan" data-qr-url="/happ-users/{esc(user["id"])}/qr?protocol=trojan" data-qr-label="{name}" data-qr-alt="QR Trojan: {name}" data-qr-note="Trojan: {name}. Отсканируйте QR-код в HAPP или другом Trojan-клиенте." title="QR-код ссылки Trojan">Trojan QR</button>'
        )
    if links.get('hysteria2'):
        name = esc(user['name'])
        buttons += (
            f'<button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(links["hysteria2"])}" title="Ссылка Hysteria2; она уже входит в подписку HAPP">Hysteria2</button>'
            f'<button class="secondary" type="button" data-qr-open="hysteria2" data-qr-url="/happ-users/{esc(user["id"])}/qr?protocol=hysteria2" data-qr-label="{name}" data-qr-alt="QR Hysteria2: {name}" data-qr-note="Hysteria2: {name}. Отсканируйте QR-код в HAPP или другом Hysteria2-клиенте." title="QR-код ссылки Hysteria2">Hysteria2 QR</button>'
        )
    if links.get('trusttunnel'):
        name = esc(user['name'])
        buttons += (
            f'<button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(links["trusttunnel"])}" title="Ссылка tt:// открывается в приложении TrustTunnel; HAPP этот протокол не поддерживает">TrustTunnel</button>'
            f'<button class="secondary" type="button" data-qr-open="trusttunnel" data-qr-url="/happ-users/{esc(user["id"])}/qr?protocol=trusttunnel" data-qr-label="{name}" data-qr-alt="QR TrustTunnel: {name}" data-qr-note="TrustTunnel: {name}. Отсканируйте QR-код в приложении TrustTunnel (HAPP его не поддерживает)." title="QR-код ссылки TrustTunnel">TrustTunnel QR</button>'
        )
    return buttons


def page(users=None, csrf='', message='', kind='success', events=None, traffic=None, subscriptions=None, protocol_links=None):
    subscriptions = subscriptions or {}
    traffic = traffic or {}
    users = users or []
    protocol_links = protocol_links or {}
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
        rows.append(f'''<tr><td><strong>{esc(user['name'])}</strong><br><span class="badge {'ok' if enabled else 'bad'}">{labels[user['status']]}</span></td><td>{esc(expiry or 'Бессрочно')}</td><td><div class="inline-actions"><button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(user['link'])}">Копировать ссылку</button><button class="secondary" type="button" data-qr-open="personal" data-qr-url="/happ-users/{user_id}/qr" data-qr-label="{esc(user['name'])}">QR</button>{subscription_button}<a class="button secondary" href="{esc(user_happ_link)}" data-happ-action="open" data-happ-link="{esc(user_happ_link)}">Открыть HAPP</a>{protocol_buttons(user, protocol_links.get(user['id'], {}), esc)}{account_stats}</div></td><td><form method="post" action="/happ-users/action"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><div class="inline-actions"><button class="secondary" name="operation" value="{'disable' if enabled else 'enable'}">{'Отключить' if enabled else 'Включить'}</button><button class="danger" name="operation" value="delete" data-happ-user-delete>Удалить</button></div></form><details><summary>Изменить имя и срок</summary><form method="post" action="/happ-users/action"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><input type="hidden" name="operation" value="update"><div class="field"><label>Имя</label><input name="name" value="{esc(user['name'])}" maxlength="80" required></div><div class="field"><label>Срок доступа UTC</label><input name="expires_at" type="datetime-local" value="{esc(expiry_field)}"></div><div class="actions"><button class="secondary" type="submit">Сохранить</button></div></form></details></td></tr>''')
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
    <div class="detail-head"><div><h2>Подключения HAPP</h2><p class="subtitle">Активность пользователей · Download / Upload</p></div><span class="badge" data-happ-chart-status role="status">Ожидание данных</span></div>
    <div class="happ-chart-range"><label for="happ_chart_range">История<select id="happ_chart_range" data-happ-chart-range><option value="10">10 минут</option><option value="30">30 минут</option><option value="60">60 минут</option><option value="90">90 минут</option></select></label><span class="happ-chart-mode">Download + Upload</span><label for="happ_peak_seconds">Окно пика, сек<input id="happ_peak_seconds" data-happ-peak-seconds type="number" min="1" max="60" step="1" value="5"></label><span class="muted" data-happ-activity-status role="status">Ожидание замера</span></div>
    <div class="happ-live-totals"><div><span>Подключены / активны</span><strong data-happ-users-online>— / —</strong></div><div><span>Соединения</span><strong data-happ-online>—</strong></div><div><span>Download / Upload сейчас</span><strong data-happ-current>— / —</strong></div><div><span>Download · за всё время журнала</span><strong data-happ-lifetime-download>—</strong></div><div><span>Upload · за всё время журнала</span><strong data-happ-lifetime-upload>—</strong></div><div><span>На графике · топ по пику</span><strong data-happ-chart-count>0 / 10</strong></div></div>
    <div class="happ-chart-plot"><canvas data-happ-traffic-chart role="img" aria-label="Скорость Download и Upload пользователей HAPP, МБ в секунду"></canvas></div>
    <div class="happ-chart-legend" data-happ-chart-legend><p class="muted">Ожидание истории скоростей.</p></div>
    <div class="happ-activity-table" tabindex="0" role="region" aria-label="Активность пользователей HAPP"><table><thead><tr><th>Пользователь / статус</th><th>IP</th><th>Соед.</th><th>Download / Upload<br>за всё время журнала</th><th>Download / Upload<br>сейчас, МБ/с</th><th>Пик Download / Upload<br><span data-happ-peak-label>за 5 сек, МБ/с</span></th></tr></thead><tbody data-happ-activity-users><tr><td colspan="6" class="empty">Ожидание данных активности.</td></tr></tbody></table></div>
    <div class="happ-chart-footer"><span class="muted" data-happ-chart-overflow></span><a class="button secondary" href="/happ-history">История HAPP</a></div>
  </section>
  <section class="panel"><div class="detail-head happ-users-heading"><h2>Пользователи HAPP</h2><button type="button" data-happ-user-create-open>Добавить нового пользователя</button></div><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Срок UTC</th><th>Персональная ссылка</th><th>Доступ</th></tr></thead><tbody>{rows}</tbody></table></div><p class="muted">Изменение доступа перезапускает HAPP-сервис и переподключает сессии. VIP UUID и ссылка сохраняются.</p></section>
  <section class="panel happ-traffic-panel"><div class="detail-head"><div><h2>TOP-5 по трафику</h2><p class="subtitle">Скачано и отправлено по активным пользователям</p></div></div><div class="happ-traffic-top" data-happ-top-users aria-live="polite"><p class="muted">Загрузка live-метрик…</p></div></section>
  <section class="panel"><h2>Журнал персонального доступа</h2><div class="table-wrap"><table><thead><tr><th>Время UTC</th><th>Пользователь</th><th>Действие</th></tr></thead><tbody>{event_rows or '<tr><td colspan="3" class="empty">Событий пока нет.</td></tr>'}</tbody></table></div></section>
</section>
<div class="qr-modal" data-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="qr-title"><button class="qr-close" type="button" data-qr-close aria-label="Закрыть">×</button><h2 id="qr-title">HAPP QR</h2><p data-qr-caption>Отсканируйте QR-код в HAPP.</p><img class="qr-image" data-qr-image alt="QR-код HAPP"></div></div>
<dialog class="gateway-dialog" data-happ-user-create-dialog aria-labelledby="happ-user-create-title"><form method="post" action="/happ-users/create" data-happ-user-create-form><h2 id="happ-user-create-title">Добавить нового пользователя</h2><input type="hidden" name="csrf" value="{esc(csrf)}"><div class="form-grid"><div class="field"><label for="happ_user_name">Имя пользователя</label><input id="happ_user_name" name="name" maxlength="80" autocomplete="off" required autofocus></div><div class="field"><label for="happ_user_expiry">Срок UTC · пусто = бессрочно</label><input id="happ_user_expiry" name="expires_at" type="datetime-local"></div></div><div class="actions"><button class="secondary" type="button" data-happ-user-create-cancel>Отмена</button><button type="submit">Создать пользователя</button></div></form></dialog>
<dialog class="gateway-dialog" data-happ-user-delete-dialog><form method="dialog"><h2>Удалить персональный доступ?</h2><p>Персональная ссылка перестанет работать. VIP-ссылка сохранится.</p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button class="danger" type="button" data-happ-user-delete-confirm>Удалить</button></div></form></dialog>
<dialog class="gateway-dialog" data-happ-traffic-reset-dialog><form method="dialog"><h2>Сбросить накопительную статистику?</h2><p data-happ-traffic-reset-message>Скачано и отправлено обнулятся только для выбранного пользователя. Дальше будут учитываться новые байты.</p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button class="danger" type="button" data-happ-traffic-reset-confirm>Сбросить</button></div></form></dialog>'''
    return render_shell('HAPP Server', body, 'happ-server')
