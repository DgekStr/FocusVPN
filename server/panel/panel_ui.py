import html
import json
from pathlib import Path

CONFIG_PATH = Path('/etc/sing-box/config.json')
VERSION_PATH = Path(__file__).with_name('VERSION')
if not VERSION_PATH.is_file():
  VERSION_PATH = Path(__file__).resolve().parents[2] / 'VERSION'
PROJECT_VERSION = VERSION_PATH.read_text(encoding='utf-8').strip() if VERSION_PATH.is_file() else 'development'


def esc(value):
    return html.escape(str(value), quote=True)


def load_profile_tags():
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    return [
        outbound.get('tag', '')
        for outbound in config.get('outbounds', [])
        if outbound.get('type') in ('vless', 'hysteria2', 'trojan', 'shadowsocks') and outbound.get('tag')
    ]


def render_shell(title, body, active, profile_tags=None, selected_profile=''):
    wireguard_active = ' active' if active == 'wireguard' else ''
    happ_active = ' active' if active == 'happ-server' else ''
    settings_active = ' active' if active == 'settings' else ''
    history_active = ' active' if active == 'happ-history' else ''
    outbounds_active = ' active' if active == 'outbounds' else ''
    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} | FOCUSLENS.DEV</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="stylesheet" href="/panel.css?v=2.1.7-happ-live-scroll-hidden">
</head>
<body>
<div class="app-shell">
  <aside class="app-sidebar">
    <a class="app-brand" href="/outbounds"><img src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VPN GATEWAY</small></span></a>
    <nav class="app-nav" aria-label="Управление VPN">
      <section class="nav-section">
        <a class="nav-link{outbounds_active}" data-panel-nav="outbounds" href="/outbounds"><span class="nav-dot"></span>VPN-серверы</a>
        <a class="nav-link{wireguard_active}" data-panel-nav="wireguard" href="/wireguard"><span class="nav-dot"></span>WireGuard</a>
        <a class="nav-link{happ_active}" data-panel-nav="happ-server" href="/happ-server"><span class="nav-dot"></span>HAPP Server</a>
        <a class="nav-link{history_active}" data-panel-nav="happ-history" href="/happ-history"><span class="nav-dot"></span>История HAPP</a>
        <a class="nav-link{settings_active}" data-panel-nav="settings" href="/settings"><span class="nav-dot"></span>Настройки</a>
        <a class="nav-link nav-logout" href="/logout"><span class="nav-dot"></span>Выход</a>
      </section>
    </nav>
    <div class="sidebar-foot"><span>VPN control</span><small>FocusVPN @focuslens.dev v{esc(PROJECT_VERSION)}</small></div>
  </aside>
  <div class="app-content"><main class="main">{body}</main></div>
</div>
  <script src="/panel.js?v=2.1.7" defer></script>
<script src="/happ-actions.js?v=10" defer></script>
</body>
</html>'''
