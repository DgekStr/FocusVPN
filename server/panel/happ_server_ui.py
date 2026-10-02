import html
import datetime as dt

from happ_server import load_state, happ_add_link, public_vless_link
from panel_ui import render_shell


def page(users=None, csrf='', message='', kind='success', events=None, traffic=None, subscriptions=None):
    data = load_state()
    vless_link = public_vless_link()
    subscriptions = subscriptions or {}
    traffic = traffic or {}
    happ_link = happ_add_link(subscriptions.get('VIP', vless_link))
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
        account_stats = f'<span class="muted" data-happ-account="personal-{user_id}" title="Наблюдаемый трафик за срок хранения истории">Скачано: <strong data-account-download>{esc(totals.get("download", "0 B"))}</strong> / Отправлено: <strong data-account-upload>{esc(totals.get("upload", "0 B"))}</strong></span>'
        rows.append(f'''<tr><td><strong>{esc(user['name'])}</strong><br><span class="badge {'ok' if enabled else 'bad'}">{labels[user['status']]}</span></td><td>{esc(expiry or 'Бессрочно')}</td><td><div class="inline-actions"><button class="secondary" type="button" data-happ-action="copy" data-happ-link="{esc(user['link'])}">Копировать ссылку</button><button class="secondary" type="button" data-qr-open="personal" data-qr-url="/happ-users/{user_id}/qr" data-qr-label="{esc(user['name'])}">QR</button>{subscription_button}<a class="button secondary" href="{esc(user_happ_link)}" data-happ-action="open" data-happ-link="{esc(user_happ_link)}">Открыть HAPP</a>{account_stats}</div></td><td><form method="post" action="/happ-users/action"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><div class="inline-actions"><button class="secondary" name="operation" value="{'disable' if enabled else 'enable'}">{'Отключить' if enabled else 'Включить'}</button><button class="danger" name="operation" value="delete" data-happ-user-delete>Удалить</button></div></form><details><summary>Изменить имя и срок</summary><form method="post" action="/happ-users/action"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="id" value="{user_id}"><input type="hidden" name="operation" value="update"><div class="field"><label>Имя</label><input name="name" value="{esc(user['name'])}" maxlength="80" required></div><div class="field"><label>Срок доступа UTC</label><input name="expires_at" type="datetime-local" value="{esc(expiry_field)}"></div><div class="actions"><button class="secondary" type="submit">Сохранить</button></div></form></details></td></tr>''')
    rows = ''.join(rows) or '<tr><td colspan="4" class="empty">Персональных пользователей пока нет.</td></tr>'
    banner = f'<div class="notice {"error" if kind == "error" else "success"}" role="status">{esc(message)}</div>' if message else ''
    event_labels = {'create': 'Создан', 'enable': 'Включён', 'disable': 'Отключён', 'delete': 'Удалён', 'update': 'Настройки изменены', 'expired': 'Срок истёк'}
    event_rows = ''.join(f'<tr><td>{esc(item.get("at", ""))}</td><td>{esc(item.get("name", ""))}</td><td>{esc(event_labels.get(item.get("operation"), item.get("operation", "")))}</td></tr>' for item in (events or []))
    body = f'''<section class="page-head">
  <div><p class="eyebrow">Public endpoint</p><h1>HAPP Server</h1><p class="subtitle">Отдельный VLESS Reality вход для HAPP и совместимых клиентов.</p></div>
  <div class="status"><span class="status-dot ok"></span>public</div>
</section>
{banner}
<section class="panel-stack">
  <section class="panel">
    <h2>VIP VLESS · существующая ссылка</h2>
    <div class="meta"><span>Endpoint: {html.escape(data.get('server', '—'))}:{html.escape(str(data.get('port', '—')))}</span><span>SNI: {html.escape(data.get('sni', '—'))}</span><span>Flow: xtls-rprx-vision</span></div>
    <div class="actions"><a class="button" href="{html.escape(happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(happ_link, quote=True)}">Открыть в HAPP</a><button class="button secondary" type="button" data-qr-open="public">Показать QR</button><button class="button secondary" type="button" data-happ-action="copy" data-happ-link="{html.escape(vless_link, quote=True)}">Скопировать vless://</button></div>
    <textarea class="public-link-field" aria-label="Public VLESS link" readonly rows="3" spellcheck="false">{html.escape(vless_link)}</textarea>
  </section>
  <section class="panel"><h2>Пользователи HAPP</h2><form method="post" action="/happ-users/create"><input type="hidden" name="csrf" value="{esc(csrf)}"><div class="form-grid"><div class="field"><label for="happ_user_name">Имя пользователя</label><input id="happ_user_name" name="name" maxlength="80" autocomplete="off" required></div><div class="field"><label for="happ_user_expiry">Срок UTC · пусто = бессрочно</label><input id="happ_user_expiry" name="expires_at" type="datetime-local"></div></div><div class="actions"><button type="submit">Создать пользователя</button></div></form><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Срок UTC</th><th>Персональная ссылка</th><th>Доступ</th></tr></thead><tbody>{rows}</tbody></table></div><p class="muted">Изменение доступа перезапускает HAPP-сервис и переподключает сессии. VIP UUID и ссылка сохраняются.</p></section>
  <section class="panel" data-happ-live>
    <div class="detail-head"><div><h2>Подключения HAPP</h2><p class="subtitle">Живые данные sing-box по входу HAPP Server.</p></div><span class="badge online" data-happ-online-count>0 онлайн</span></div>
    <div class="status-metrics happ-metrics"><article class="status-card cyan"><span>Онлайн</span><strong data-happ-online>0</strong><small>активных соединений</small></article><article class="status-card teal"><span>Скачано</span><strong data-happ-download>0 B</strong><small>по активным соединениям</small></article><article class="status-card violet"><span>Отправлено</span><strong data-happ-upload>0 B</strong><small>по активным соединениям</small></article></div>
    <div class="table-wrap"><table class="happ-connections"><thead><tr><th>Пользователь</th><th>IP клиента</th><th>Подключён</th><th>Скачано</th><th>Отправлено</th><th>Протокол</th><th>Назначение</th></tr></thead><tbody data-happ-connections><tr><td colspan="7" class="empty">Нет активных подключений.</td></tr></tbody></table></div>
    <h2>Трафик пользователей · активные соединения</h2><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Соединений</th><th>Скачано</th><th>Отправлено</th></tr></thead><tbody data-happ-user-traffic><tr><td colspan="4" class="empty">Нет активных подключений.</td></tr></tbody></table></div><p class="muted">Имя подтверждается журналом VLESS-аутентификации; без подтверждения отображается «Не определён». Счётчики относятся к активным соединениям, не являются накопленным итогом закрытых сессий.</p>
  </section>
  <section class="panel"><h2>Журнал персонального доступа</h2><div class="table-wrap"><table><thead><tr><th>Время UTC</th><th>Пользователь</th><th>Действие</th></tr></thead><tbody>{event_rows or '<tr><td colspan="3" class="empty">Событий пока нет.</td></tr>'}</tbody></table></div></section>
</section>
<div class="qr-modal" data-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="qr-title"><button class="qr-close" type="button" data-qr-close aria-label="Закрыть">×</button><h2 id="qr-title">HAPP QR</h2><p data-qr-caption>Отсканируйте QR-код в HAPP.</p><img class="qr-image" data-qr-image alt="QR-код HAPP"></div></div>
<dialog class="gateway-dialog" data-happ-user-delete-dialog><form method="dialog"><h2>Удалить персональный доступ?</h2><p>Персональная ссылка перестанет работать. VIP-ссылка сохранится.</p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button class="danger" type="button" data-happ-user-delete-confirm>Удалить</button></div></form></dialog>'''
    return render_shell('HAPP Server', body, 'happ-server')
