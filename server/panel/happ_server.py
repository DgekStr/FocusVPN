import base64
import html
import json
from pathlib import Path

STATE_PATH = Path('/etc/sing-box-admin/happ-server.json')


def happ_add_link(link):
    payload = base64.b64encode(link.encode('utf-8')).decode('ascii')
    return 'happ://add/' + payload


def happ_link(kind):
    data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    if kind == 'lan':
        return happ_add_link(data.get('local_link', data['link']))
    if kind == 'public':
        return happ_add_link(data['link'])
    raise ValueError('Unknown HAPP link kind')


def page():
    data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    link = data['link']
    local_link = data.get('local_link', link)
    public_happ_link = happ_add_link(link)
    local_happ_link = happ_add_link(local_link)
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HAPP Server | FOCUSLENS.DEV</title><link rel="icon" href="/favicon.png"><style>
body{{margin:0;background:#050816;color:#f5f7ff;font:16px/1.5 Inter,"Segoe UI",sans-serif}}.standalone-shell{{min-height:100vh;display:grid;grid-template-columns:240px minmax(0,1fr)}}.standalone-sidebar{{background:#0b1225;border-right:1px solid #ffffff1f;padding:24px 16px}}.side-brand{{align-items:center;color:#f5f7ff;display:flex;gap:10px;text-decoration:none}}.side-brand img{{height:38px;width:38px}}.side-brand span{{font-size:12px;font-weight:800;letter-spacing:.08em}}.side-brand small{{color:#8ea0be;display:block;font-size:9px;letter-spacing:.12em;margin-top:3px}}.standalone-menu{{display:grid;gap:6px;margin-top:34px}}.standalone-menu a{{border:1px solid transparent;color:#8ea0be;padding:10px 12px;text-decoration:none}}.standalone-menu a:hover,.standalone-menu a.active{{background:#111b34;border-color:#ffffff1f;color:#f5f7ff}}.standalone-content{{min-width:0;padding:32px}}main{{max-width:900px;margin:auto}}.panel{{background:#111b34e0;border:1px solid #ffffff1f;padding:24px;box-shadow:0 20px 40px #0006}}h1{{margin:0 0 8px}}p,small{{color:#8ea0be}}a.button,button.button{{display:inline-block;background:linear-gradient(135deg,#7c3aed,#22d3ee);border:0;color:#fff;cursor:pointer;padding:12px 16px;border-radius:4px;text-decoration:none;font:700 16px Inter,"Segoe UI",sans-serif;margin:0 8px 8px 0}}button.secondary{{background:transparent;border:1px solid #ffffff2f}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#050816;border:1px solid #ffffff1f;padding:16px;font:12px/1.45 Consolas,monospace}}.meta{{display:flex;gap:16px;flex-wrap:wrap;color:#8ea0be}}.qr-modal{{align-items:center;background:#050816d9;display:flex;inset:0;justify-content:center;padding:20px;position:fixed;z-index:20}}.qr-modal[hidden]{{display:none}}.qr-dialog{{background:#111b34;border:1px solid #ffffff2f;box-shadow:0 24px 80px #000b;max-width:420px;padding:24px;position:relative;text-align:center;width:100%}}.qr-dialog h2{{margin:0 0 6px}}.qr-dialog p{{margin:0}}.qr-image{{background:#fff;display:block;height:auto;margin:20px auto;max-width:300px;padding:12px;width:100%}}.qr-close{{background:transparent;border:1px solid #ffffff2f;color:#dce8ff;cursor:pointer;font-size:20px;line-height:1;position:absolute;right:12px;top:12px}}@media (max-width:760px){{.standalone-shell{{display:block}}.standalone-sidebar{{border-bottom:1px solid #ffffff1f;border-right:0}}.standalone-content{{padding:20px 14px}}}}
</style></head><body><div class="standalone-shell"><aside class="standalone-sidebar"><a class="side-brand" href="/"><img src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VPN CONTROL</small></span></a><nav class="standalone-menu"><a data-panel-nav="vless" href="/">VLESS</a><a data-panel-nav="wireguard" href="/wireguard">WireGuard</a><a data-panel-nav="happ-routing" href="/happ-routing">HAPP Direct</a><a data-panel-nav="happ-server" href="/happ-server">HAPP Server</a></nav></aside><div class="standalone-content"><main><section class="panel"><p>FOCUSLENS.DEV / HAPP SERVER</p><h1>VLESS Reality endpoint</h1><p>Отдельный HAPP-compatible сервер. Импортируйте ссылку в HAPP, не меняя текущий клиентский VLESS-шлюз.</p><div class="meta"><span>Public: {html.escape(data['server'])}:{html.escape(str(data['port']))}</span><span>LAN: 192.168.0.39:9445</span><span>SNI: {html.escape(data['sni'])}</span><span>Flow: xtls-rprx-vision</span></div><p><a class="button" href="{html.escape(local_happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(local_happ_link, quote=True)}">Открыть в HAPP (LAN)</a><button class="button secondary" type="button" data-qr-open="lan">Показать QR LAN</button></p><p><a class="button" href="{html.escape(public_happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(public_happ_link, quote=True)}">Открыть в HAPP (Public)</a><button class="button secondary" type="button" data-qr-open="public">Показать QR Public</button></p><p><button class="button" type="button" data-happ-action="copy" data-happ-link="{html.escape(local_happ_link, quote=True)}">Скопировать HAPP-ссылку</button></p><small>Для устройств в LAN используйте LAN-ссылку. Для внешних устройств нужен проброс TCP 9445 на 192.168.0.39.</small><pre>{html.escape(local_happ_link)}</pre><details><summary>Локальная VLESS-ссылка</summary><pre>{html.escape(local_link)}</pre></details><details><summary>Публичная HAPP-ссылка</summary><pre>{html.escape(public_happ_link)}</pre></details></section><div class="qr-modal" data-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="qr-title"><button class="qr-close" type="button" data-qr-close aria-label="Закрыть">×</button><h2 id="qr-title">HAPP QR</h2><p data-qr-caption>Отсканируйте код в HAPP.</p><img class="qr-image" data-qr-image alt="QR-код HAPP"></div></div></main></div></div><script src="/panel.js" defer></script><script src="/happ-actions.js" defer></script></body></html>'''
