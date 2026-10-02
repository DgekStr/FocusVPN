import html
import json
from pathlib import Path

CONFIG_PATH = Path('/etc/sing-box/config.json')


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
      if outbound.get('type') in ('vless', 'hysteria2') and outbound.get('tag')
    ]


def render_shell(title, body, active, profile_tags=None, selected_profile=''):
    tags = load_profile_tags() if profile_tags is None else profile_tags
    vless_active = ' active' if active == 'vless' else ''
    wireguard_active = ' active' if active == 'wireguard' else ''
    happ_active = ' active' if active == 'happ-server' else ''
    settings_active = ' active' if active == 'settings' else ''
    outbounds_active = ' active' if active == 'outbounds' else ''
    profile_markup = ''.join(
        f'<a class="profile-link{" active" if tag == selected_profile else ""}" '
        f'href="/outbounds?tag={esc(tag)}"><span class="nav-dot"></span>{esc(tag)}</a>'
        for tag in tags
    ) or '<span class="nav-empty">Нет VLESS-профилей</span>'
    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} | FOCUSLENS.DEV</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="stylesheet" href="/panel.css?v=19">
</head>
<body>
<div class="app-shell">
  <aside class="app-sidebar">
    <a class="app-brand" href="/vless"><img src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VLESS GATEWAY</small></span></a>
    <nav class="app-nav" aria-label="Управление VPN">
      <section class="nav-section nav-vless">
        <a class="nav-link{vless_active}" data-panel-nav="vless" href="/vless"><span class="nav-dot"></span>VLESS</a>
        <a class="nav-link{outbounds_active}" data-panel-nav="outbounds" href="/outbounds"><span class="nav-dot"></span>VPN-серверы</a>
        <div class="profile-list">{profile_markup}</div>
      </section>
      <section class="nav-section">
        <a class="nav-link{wireguard_active}" data-panel-nav="wireguard" href="/wireguard"><span class="nav-dot"></span>WireGuard</a>
        <a class="nav-link{happ_active}" data-panel-nav="happ-server" href="/happ-server"><span class="nav-dot"></span>HAPP Server</a>
        <a class="nav-link{settings_active}" data-panel-nav="settings" href="/settings"><span class="nav-dot"></span>Настройки</a>
        <a class="nav-link nav-logout" href="/logout"><span class="nav-dot"></span>Выход</a>
      </section>
    </nav>
    <div class="sidebar-foot"><span>Private VPN control</span><small>FocusVPN @focuslens.dev v1.1</small></div>
  </aside>
  <div class="app-content"><main class="main">{body}</main></div>
</div>
<script src="/panel.js?v=10" defer></script>
<script src="/happ-actions.js?v=6" defer></script>
</body>
</html>'''
