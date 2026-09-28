import html

from happ_routing import DIRECT_SITES, deeplink, json_text


def page():
    link = deeplink()
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HAPP Direct | FOCUSLENS.DEV</title><link rel="icon" href="/favicon.png"><link rel="stylesheet" href="/panel.css?v=2"></head>
<body><div class="standalone-shell"><aside class="standalone-sidebar"><a class="side-brand" href="/"><img src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VPN CONTROL</small></span></a><nav class="standalone-menu"><a data-panel-nav="vless" href="/">VLESS</a><a data-panel-nav="wireguard" href="/wireguard">WireGuard</a><a data-panel-nav="happ-routing" href="/happ-routing">HAPP Direct</a><a data-panel-nav="happ-server" href="/happ-server">HAPP Server</a></nav></aside><div class="standalone-content"><main class="main"><section class="panel"><p>FOCUSLENS.DEV / HAPP</p><h1>Direct Russian services</h1><p>Импортируйте routing-профиль в HAPP. Список доменов идёт напрямую с устройства, остальной трафик остаётся через текущий proxy-профиль.</p><div class="meta"><span>DirectSites: {len(DIRECT_SITES)}</span><span>GlobalProxy: true</span><span>LAN: direct</span></div><p><a class="button" href="{html.escape(link, quote=True)}" data-happ-action="open" data-happ-link="{html.escape(link, quote=True)}">Открыть в HAPP</a><button class="button secondary" type="button" data-happ-action="copy" data-happ-link="{html.escape(link, quote=True)}">Скопировать deeplink</button></p><small>Если приложение не открылось, скопируйте deeplink и вставьте его в HAPP вручную.</small><pre>{html.escape(link)}</pre><details><summary>JSON routing profile</summary><pre>{html.escape(json_text())}</pre></details></section></main></div></div><script src="/panel.js" defer></script><script src="/happ-actions.js" defer></script></body></html>'''
