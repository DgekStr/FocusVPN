import html
import json
from pathlib import Path

from happ_server import DEFAULT_SUBSCRIPTION_TITLE, load_state as load_happ_state, validate_subscription_content

CONFIG_PATH = Path('/etc/sing-box/config.json')
VERSION_PATH = Path(__file__).with_name('VERSION')
if not VERSION_PATH.is_file():
  VERSION_PATH = Path(__file__).resolve().parents[2] / 'VERSION'
PROJECT_VERSION = VERSION_PATH.read_text(encoding='utf-8').strip() if VERSION_PATH.is_file() else 'development'
ABOUT_REPOSITORY_URL = 'https://github.com/DgekStr/FocusVPN'
ABOUT_AUTHOR_URL = 'https://github.com/DgekStr'
ABOUT_LINKS = (
    ('GitHub', ABOUT_REPOSITORY_URL),
    ('Описание программы', ABOUT_REPOSITORY_URL + '/blob/master/README.md'),
    ('Развёртывание', ABOUT_REPOSITORY_URL + '/blob/master/docs/OPERATIONS.md'),
    ('Релизы', ABOUT_REPOSITORY_URL + '/releases'),
    ('MIT License', ABOUT_REPOSITORY_URL + '/blob/master/LICENSE'),
)


def esc(value):
    return html.escape(str(value), quote=True)


def service_title():
  try:
    title = load_happ_state().get('subscription_title', DEFAULT_SUBSCRIPTION_TITLE)
    return validate_subscription_content(title, '')[0]
  except (OSError, ValueError, AttributeError):
    return DEFAULT_SUBSCRIPTION_TITLE


def load_profile_tags():
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    return [
        outbound.get('tag', '')
        for outbound in config.get('outbounds', [])
        if outbound.get('type') in ('vless', 'hysteria2', 'trojan', 'shadowsocks', 'socks') and outbound.get('tag')
    ]


def render_shell(title, body, active, profile_tags=None, selected_profile=''):
    wireguard_active = ' active' if active == 'wireguard' else ''
    happ_active = ' active' if active == 'happ-server' else ''
    settings_active = ' active' if active == 'settings' else ''
    history_active = ' active' if active == 'happ-history' else ''
    outbounds_active = ' active' if active == 'outbounds' else ''
    about_active = ' active' if active == 'about' else ''
    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(service_title())}</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="stylesheet" href="/panel.css?v=2.2.0">
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
    <div class="sidebar-bottom">
      <a class="nav-link nav-about{about_active}" data-panel-nav="about" href="/about"><span class="nav-dot"></span>О программе</a>
      <div class="sidebar-foot"><span>VPN control</span><small>FocusVPN @focuslens.dev v{esc(PROJECT_VERSION)}</small></div>
    </div>
  </aside>
  <div class="app-content"><main class="main">{body}</main></div>
</div>
<script src="/chart.js?v=4.5.1" defer></script>
  <script src="/panel.js?v=2.2.0" defer></script>
<script src="/happ-actions.js?v=10" defer></script>
</body>
</html>'''


def render_about_page():
    links = ''.join(
        f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'
        for label, url in ABOUT_LINKS
    )
    body = f'''<section class="page-head">
    <div><p class="eyebrow">FocusVPN / About</p><h1>О программе</h1></div>
  </section>
  <section class="panel about-content" aria-labelledby="about-title">
    <div class="about-heading"><h2 id="about-title">FocusVPN</h2><span class="muted">Версия {esc(PROJECT_VERSION)}</span></div>
    <p class="about-summary">Русскоязычная self-hosted панель управления VPN-шлюзом на sing-box: клиенты WireGuard, внешние VPN-серверы (VLESS, Hysteria2, Trojan, Shadowsocks), HAPP Server с персональными подписками, история трафика, проверки доступности и автоматическое переключение шлюза с уведомлениями в Mattermost.</p>
    <nav class="about-links" aria-label="Ссылки о программе">{links}</nav>
    <div class="about-terms">
      <p>Программа распространяется бесплатно по лицензии MIT. В текущей версии клиентский биллинг и обязательные платежи не предусмотрены.</p>
      <p>Если в будущем появятся платные услуги, дополнительные модули или отдельный сервис клиентского биллинга, их функции, стоимость и условия будут опубликованы отдельно. Это не создаёт подписки или платных обязательств для пользователей бесплатной версии.</p>
    </div>
    <p class="about-meta">Copyright © 2026 <a href="{esc(ABOUT_AUTHOR_URL)}" target="_blank" rel="noopener noreferrer">DgekStr</a></p>
  </section>'''
    return render_shell('О программе', body, 'about')
