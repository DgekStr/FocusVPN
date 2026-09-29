import html

from happ_server import load_state, public_happ_link, public_vless_link
from panel_ui import render_shell


def page():
    data = load_state()
    vless_link = public_vless_link()
    happ_link = public_happ_link()
    body = f'''<section class="page-head">
  <div><p class="eyebrow">Public endpoint</p><h1>HAPP Server</h1><p class="subtitle">Отдельный VLESS Reality вход для HAPP и совместимых клиентов.</p></div>
  <div class="status"><span class="status-dot ok"></span>public</div>
</section>
<section class="panel-stack">
  <section class="panel">
    <h2>Public VLESS</h2>
    <div class="meta"><span>Endpoint: {html.escape(data.get('server', '—'))}:{html.escape(str(data.get('port', '—')))}</span><span>SNI: {html.escape(data.get('sni', '—'))}</span><span>Flow: xtls-rprx-vision</span></div>
    <div class="actions"><a class="button" href="{html.escape(happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(happ_link, quote=True)}">Открыть в HAPP</a><button class="button secondary" type="button" data-qr-open="public">Показать QR</button><button class="button secondary" type="button" data-happ-action="copy" data-happ-link="{html.escape(vless_link, quote=True)}">Скопировать vless://</button></div>
    <textarea class="public-link-field" aria-label="Public VLESS link" readonly rows="3" spellcheck="false">{html.escape(vless_link)}</textarea>
  </section>
  <section class="panel" data-happ-live>
    <div class="detail-head"><div><h2>Подключения HAPP</h2><p class="subtitle">Живые данные sing-box по входу HAPP Server.</p></div><span class="badge online" data-happ-online-count>0 онлайн</span></div>
    <div class="status-metrics happ-metrics"><article class="status-card cyan"><span>Онлайн</span><strong data-happ-online>0</strong><small>активных соединений</small></article><article class="status-card teal"><span>Скачано</span><strong data-happ-download>0 B</strong><small>по активным соединениям</small></article><article class="status-card violet"><span>Отправлено</span><strong data-happ-upload>0 B</strong><small>по активным соединениям</small></article></div>
    <div class="table-wrap"><table class="happ-connections"><thead><tr><th>IP клиента</th><th>Подключён</th><th>DL / UL</th><th>Протокол</th><th>Назначение</th></tr></thead><tbody data-happ-connections><tr><td colspan="5" class="empty">Нет активных подключений.</td></tr></tbody></table></div>
  </section>
</section>
<div class="qr-modal" data-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="qr-title"><button class="qr-close" type="button" data-qr-close aria-label="Закрыть">×</button><h2 id="qr-title">Public HAPP QR</h2><p data-qr-caption>Отсканируйте QR-код в HAPP.</p><img class="qr-image" data-qr-image alt="QR-код Public HAPP"></div></div>'''
    return render_shell('HAPP Server', body, 'happ-server')
