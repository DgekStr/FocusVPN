import html
import json
from pathlib import Path

from happ_server import happ_link

STATE_PATH = Path('/etc/sing-box-admin/happ-server.json')


def page():
    data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    link = data['link']
    local_link = data.get('local_link', link)
    public_happ_link = happ_link('public')
    local_happ_link = happ_link('lan')
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HAPP Server | FOCUSLENS.DEV</title><link rel="icon" href="/favicon.png"><link rel="stylesheet" href="/panel.css?v=2"></head>
<body><div class="standalone-shell"><aside class="standalone-sidebar"><a class="side-brand" href="/"><img src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VPN CONTROL</small></span></a><nav class="standalone-menu"><a data-panel-nav="vless" href="/">VLESS</a><a data-panel-nav="wireguard" href="/wireguard">WireGuard</a><a data-panel-nav="happ-routing" href="/happ-routing">HAPP Direct</a><a data-panel-nav="happ-server" href="/happ-server">HAPP Server</a></nav></aside><div class="standalone-content"><main class="main"><section class="panel"><p>FOCUSLENS.DEV / HAPP SERVER</p><h1>VLESS Reality endpoint</h1><p>Отдельный HAPP-compatible сервер. Импортируйте ссылку в HAPP, не меняя текущий клиентский VLESS-шлюз.</p><div class="meta"><span>Public: {html.escape(data['server'])}:{html.escape(str(data['port']))}</span><span>LAN: 192.168.0.39:9445</span><span>SNI: {html.escape(data['sni'])}</span><span>Flow: xtls-rprx-vision</span></div><p><a class="button" href="{html.escape(local_happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(local_happ_link, quote=True)}">Открыть в HAPP (LAN)</a><button class="button secondary" type="button" data-qr-open="lan">Показать QR LAN</button></p><p><a class="button" href="{html.escape(public_happ_link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(public_happ_link, quote=True)}">Открыть в HAPP (Public)</a><button class="button secondary" type="button" data-qr-open="public">Показать QR Public</button></p><p><button class="button" type="button" data-happ-action="copy" data-happ-link="{html.escape(local_happ_link, quote=True)}">Скопировать HAPP-ссылку</button></p><small>Для устройств в LAN используйте LAN-ссылку. Для внешних устройств нужен проброс TCP 9445 на 192.168.0.39.</small><pre>{html.escape(local_happ_link)}</pre><details><summary>Локальная VLESS-ссылка</summary><pre>{html.escape(local_link)}</pre></details><details><summary>Публичная HAPP-ссылка</summary><pre>{html.escape(public_happ_link)}</pre></details></section><div class="qr-modal" data-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="qr-title"><button class="qr-close" type="button" data-qr-close aria-label="Закрыть">×</button><h2 id="qr-title">HAPP QR</h2><p data-qr-caption>Отсканируйте код в HAPP.</p><img class="qr-image" data-qr-image alt="QR-код HAPP"></div></div></main></div></div><script src="/panel.js" defer></script><script src="/happ-actions.js" defer></script></body></html>'''
