#!/usr/bin/env python3
import base64
import datetime as dt
import grp
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from wg_admin import WgAdmin
from wg_easy_api import WgEasyApi, WgEasyApiError
from happ_routing_ui import page as happ_routing_page
from happ_server import happ_link as happ_server_link
from happ_server_ui import page as happ_server_page

APP_DIR = Path('/etc/sing-box-admin')
AUTH_PATH = APP_DIR / 'auth.json'
BACKUP_DIR = APP_DIR / 'backups'
FIRST_LOGIN_PATH = APP_DIR / 'first-login.txt'
WG_EASY_SECRET_PATH = APP_DIR / 'wg-easy-api.json'
CONFIG_PATH = Path('/etc/sing-box/config.json')
FAVICON_PATH = Path('/opt/sing-box-admin/static/favicon.png')
PANEL_CSS_PATH = Path('/opt/sing-box-admin/static/panel.css')
PANEL_JS_PATH = Path('/opt/sing-box-admin/static/panel.js')
HAPP_ACTIONS_PATH = Path('/opt/sing-box-admin/static/happ-actions.js')
QR_ENCODE_BIN = '/usr/bin/qrencode'
SING_BOX_BIN = '/usr/bin/sing-box'
SYSTEMCTL_BIN = '/usr/bin/systemctl'
VPN_NETWORK = ipaddress.ip_network('10.8.0.0/24')
LAN_NETWORK = ipaddress.ip_network('192.168.0.0/24')
MANAGEMENT_NETWORK = ipaddress.ip_network('10.1.17.0/24')
ACCESS_NETWORKS = (VPN_NETWORK, LAN_NETWORK, MANAGEMENT_NETWORK)
HOST = os.environ.get('SING_BOX_ADMIN_HOST', '0.0.0.0')
PORT = int(os.environ.get('SING_BOX_ADMIN_PORT', '9443'))
MAX_BODY_SIZE = 65536
CSRF_TOKEN = secrets.token_urlsafe(32)
CONFIG_LOCK = threading.Lock()
WG_LOCK = threading.Lock()
WG_ADMIN = WgAdmin(WgEasyApi(WG_EASY_SECRET_PATH), CSRF_TOKEN)
HOSTNAME_PATTERN = re.compile(r'(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?\Z')
PUBLIC_KEY_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
SHORT_ID_PATTERN = re.compile(r'[0-9A-Fa-f]{0,16}\Z')
SERVICE_NAME_PATTERN = re.compile(r'[A-Za-z0-9._/-]{1,128}\Z')
FINGERPRINTS = ('chrome', 'firefox', 'edge', 'safari', 'ios', 'android', '360', 'qq', 'random', 'randomized')
FLOWS = ('', 'xtls-rprx-vision')
TRANSPORTS = ('tcp', 'grpc')


def command(args, timeout=30):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)


def service_state(name):
    result = command([SYSTEMCTL_BIN, 'is-active', name], timeout=10)
    return result.stdout.strip() or 'unknown'


def load_auth():
    data = json.loads(AUTH_PATH.read_text(encoding='utf-8'))
    return data['username'], base64.b64decode(data['salt']), base64.b64decode(data['hash']), data.get('realm', 'VLESS Gateway')


def auth_realm():
    _, _, _, realm = load_auth()
    sanitized = re.sub(r'[^A-Za-z0-9 ._-]', '', realm)[:64]
    return sanitized or 'VLESS Gateway'


def password_digest(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32)


def set_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError('Пароль должен содержать от 12 до 128 символов.')
    username, _, _, _ = load_auth()
    salt = secrets.token_bytes(16)
    data = {
        'username': username,
        'salt': base64.b64encode(salt).decode('ascii'),
        'hash': base64.b64encode(password_digest(password, salt)).decode('ascii'),
        'realm': f'VLESS Gateway {secrets.token_hex(4)}',
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    temporary = AUTH_PATH.with_suffix('.new')
    temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, AUTH_PATH)


def validate_server(value, label):
    if not value or len(value) > 253:
        raise ValueError(f'{label}: укажите IP-адрес или имя хоста.')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if not HOSTNAME_PATTERN.fullmatch(value):
            raise ValueError(f'{label}: некорректное имя хоста.')
        return value.rstrip('.').lower()


def require_port(value):
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError('Порт должен быть числом.') from error
    if not 1 <= port <= 65535:
        raise ValueError('Порт должен быть в диапазоне 1-65535.')
    return port


def form_value(values, name):
    return values.get(name, [''])[0].strip()


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


def profiles(config):
    return [outbound for outbound in config.get('outbounds', []) if outbound.get('type') == 'vless']


def profile_by_tag(config, tag):
    for outbound in profiles(config):
        if outbound.get('tag') == tag:
            return outbound
    raise ValueError('Профиль не найден.')


def profile_values(profile):
    tls = profile.get('tls', {})
    reality = tls.get('reality', {})
    utls = tls.get('utls', {})
    transport = profile.get('transport', {})
    return {
        'tag': profile.get('tag', ''),
        'server': profile.get('server', ''),
        'server_port': str(profile.get('server_port', '')),
        'uuid': profile.get('uuid', ''),
        'flow': profile.get('flow', ''),
        'server_name': tls.get('server_name', ''),
        'fingerprint': utls.get('fingerprint', 'chrome'),
        'public_key': reality.get('public_key', ''),
        'short_id': reality.get('short_id', ''),
        'transport': transport.get('type', 'tcp'),
        'service_name': transport.get('service_name', ''),
    }


def update_profile(config, values):
    tag = form_value(values, 'profile_tag')
    profile = profile_by_tag(config, tag)
    server = validate_server(form_value(values, 'server'), 'Сервер')
    server_name = validate_server(form_value(values, 'server_name'), 'SNI')
    try:
        profile_uuid = str(uuid.UUID(form_value(values, 'uuid')))
    except ValueError as error:
        raise ValueError('UUID некорректен.') from error
    public_key = form_value(values, 'public_key')
    if not PUBLIC_KEY_PATTERN.fullmatch(public_key):
        raise ValueError('Reality public key некорректен.')
    short_id = form_value(values, 'short_id').lower()
    if not SHORT_ID_PATTERN.fullmatch(short_id):
        raise ValueError('Reality short ID должен быть шестнадцатеричной строкой до 16 символов.')
    fingerprint = form_value(values, 'fingerprint')
    if fingerprint not in FINGERPRINTS:
        raise ValueError('Выбран неподдерживаемый TLS fingerprint.')
    flow = form_value(values, 'flow')
    if flow not in FLOWS:
        raise ValueError('Выбран неподдерживаемый VLESS flow.')
    transport = form_value(values, 'transport')
    if transport not in TRANSPORTS:
        raise ValueError('Выбран неподдерживаемый транспорт.')
    service_name = form_value(values, 'service_name')
    if transport == 'grpc' and not SERVICE_NAME_PATTERN.fullmatch(service_name):
        raise ValueError('gRPC service name некорректен.')

    profile['server'] = server
    profile['server_port'] = require_port(form_value(values, 'server_port'))
    profile['uuid'] = profile_uuid
    if flow:
        profile['flow'] = flow
    else:
        profile.pop('flow', None)
    tls = profile.setdefault('tls', {})
    tls['enabled'] = True
    tls['server_name'] = server_name
    tls['utls'] = {'enabled': True, 'fingerprint': fingerprint}
    tls['reality'] = {'enabled': True, 'public_key': public_key, 'short_id': short_id}
    if transport == 'grpc':
        profile['transport'] = {'type': 'grpc', 'service_name': service_name}
    else:
        profile.pop('transport', None)
    return tag


def selectable_outbounds(config):
    return [
        outbound for outbound in config.get('outbounds', [])
        if outbound.get('type') in ('vless', 'urltest') and outbound.get('tag')
    ]


def set_default_outbound(config, tag):
    allowed_tags = {outbound['tag'] for outbound in selectable_outbounds(config)}
    if tag not in allowed_tags:
        raise ValueError('Выбранный исходящий профиль не существует.')
    config.setdefault('route', {})['final'] = tag
    return tag


def write_atomic_bytes(data):
    group_id = grp.getgrnam('sing-box').gr_gid
    descriptor, temporary_name = tempfile.mkstemp(prefix='.config-admin-', dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary_name, 0, group_id)
        os.chmod(temporary_name, 0o640)
        os.replace(temporary_name, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def backup_configuration():
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = BACKUP_DIR / f'config-{timestamp}.json'
    shutil.copy2(CONFIG_PATH, backup)
    os.chmod(backup, 0o600)
    stored = sorted(BACKUP_DIR.glob('config-*.json'), reverse=True)
    for stale in stored[10:]:
        stale.unlink(missing_ok=True)
    return backup


def check_candidate(data):
    with tempfile.TemporaryDirectory(prefix='.sing-box-admin-check-', dir=CONFIG_PATH.parent) as directory:
        candidate = Path(directory) / 'config.json'
        candidate.write_bytes(data)
        os.chmod(candidate, 0o640)
        result = command([SING_BOX_BIN, 'check', '-C', directory], timeout=30)
        if result.returncode != 0:
            raise RuntimeError('sing-box отклонил конфигурацию. Текущая версия сохранена без изменений.')


def restart_sing_box():
    result = command([SYSTEMCTL_BIN, 'restart', 'sing-box'], timeout=45)
    if result.returncode != 0 or service_state('sing-box') != 'active':
        raise RuntimeError('sing-box не запустился с новой конфигурацией.')


def apply_configuration(config):
    data = (json.dumps(config, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    check_candidate(data)
    backup = backup_configuration()
    write_atomic_bytes(data)
    try:
        restart_sing_box()
    except RuntimeError:
        write_atomic_bytes(backup.read_bytes())
        restart_sing_box()
        raise RuntimeError('Новая конфигурация не запустилась; предыдущая версия восстановлена.')


def dashboard_state():
    try:
        config = load_config()
        profile_count = len(profiles(config))
        active_tag = config.get('route', {}).get('final', 'не задан')
        modified = dt.datetime.fromtimestamp(CONFIG_PATH.stat().st_mtime, tz=dt.timezone.utc).astimezone().strftime('%d.%m.%Y %H:%M')
    except Exception:
        profile_count, active_tag, modified = 0, 'ошибка чтения', 'недоступно'
    return {
        'sing_box': service_state('sing-box'),
        'gateway': service_state('sing-box-gateway'),
        'profiles': profile_count,
        'route': active_tag,
        'modified': modified,
    }


def esc(value):
    return html.escape(str(value), quote=True)


def select_options(items, selected, blank_label=None):
    options = []
    if blank_label is not None:
        options.append(f'<option value=""{" selected" if not selected else ""}>{esc(blank_label)}</option>')
    for item in items:
        options.append(f'<option value="{esc(item)}"{" selected" if item == selected else ""}>{esc(item or "не использовать")}</option>')
    return ''.join(options)


def message_banner(message, kind):
    if not message:
        return ''
    style = 'success' if kind == 'success' else 'error'
    return f'<div class="notice {style}" role="status">{esc(message)}</div>'


def render_page(config, selected_tag, message='', kind='success'):
    all_profiles = profiles(config)
    if not all_profiles:
        raise RuntimeError('VLESS-профили не найдены.')
    selected = next((item for item in all_profiles if item.get('tag') == selected_tag), all_profiles[0])
    values = profile_values(selected)
    state = dashboard_state()
    current_route = config.get('route', {}).get('final', '')
    sidebar = []
    for item in all_profiles:
        tag = item.get('tag', '')
        active = ' active' if tag == values['tag'] else ''
        sidebar.append(f'<a class="profile-link{active}" href="/?profile={esc(tag)}"><span class="profile-dot"></span><span>{esc(tag)}</span></a>')
    fingerprint_options = select_options(FINGERPRINTS, values['fingerprint'])
    flow_options = select_options(FLOWS, values['flow'], 'без Vision')
    transport_options = select_options(TRANSPORTS, values['transport'])
    route_options = []
    for outbound in selectable_outbounds(config):
        tag = outbound['tag']
        label = 'Авто: самый быстрый доступный' if outbound['type'] == 'urltest' else f'Профиль: {tag}'
        selected_attribute = ' selected' if tag == current_route else ''
        route_options.append(f'<option value="{esc(tag)}"{selected_attribute}>{esc(label)}</option>')
    status_class = 'ok' if state['sing_box'] == 'active' and state['gateway'] == 'active' else 'bad'
    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FOCUSLENS.DEV | VLESS Gateway</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-icon" href="/favicon.png">
<link rel="stylesheet" href="/panel.css?v=2">
<style>
:root {{
  --paper: #f4f2ea;
  --surface: #fffdf8;
  --ink: #182127;
  --muted: #637078;
  --line: #d7d8ce;
  --teal: #0b716c;
  --teal-dark: #07524f;
  --orange: #bd641f;
  --red: #b53f32;
  --green: #176b42;
  --shadow: 0 14px 36px rgba(22, 32, 39, .08);
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: var(--ink); background: var(--paper); font-family: "Segoe UI Variable", "Noto Sans", sans-serif; letter-spacing: 0; }}
.shell {{ min-height: 100vh; display: grid; grid-template-columns: 248px minmax(0, 1fr); }}
.sidebar {{ background: #17262b; color: #e9f1ef; padding: 28px 18px; display: flex; flex-direction: column; gap: 26px; }}
.brand {{ font-size: 18px; font-weight: 700; line-height: 1.15; }}
.brand small {{ display: block; margin-top: 6px; color: #9bb3af; font-size: 12px; font-weight: 600; }}
.label {{ color: #94b0ab; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
.profile-list {{ display: grid; gap: 4px; }}
.profile-link {{ align-items: center; border-left: 3px solid transparent; color: #c9d7d4; display: flex; gap: 10px; min-height: 38px; padding: 8px 10px; text-decoration: none; }}
.profile-link:hover, .profile-link.active {{ background: #22393d; border-color: #e09a3d; color: #fff; }}
.profile-dot {{ width: 7px; height: 7px; border-radius: 50%; background: #5f8580; flex: 0 0 auto; }}
.profile-link.active .profile-dot {{ background: #e09a3d; }}
.sidebar-foot {{ margin-top: auto; color: #9bb3af; font-size: 12px; line-height: 1.45; }}
.main {{ padding: 32px clamp(20px, 4vw, 64px) 48px; }}
.topline {{ display: flex; align-items: start; justify-content: space-between; gap: 20px; margin-bottom: 24px; }}
h1 {{ margin: 0; font-size: 28px; line-height: 1.1; }}
.subtitle {{ margin: 8px 0 0; color: var(--muted); font-size: 14px; }}
.status {{ align-items: center; background: var(--surface); border: 1px solid var(--line); display: flex; gap: 8px; padding: 10px 12px; font-size: 13px; white-space: nowrap; }}
.status-dot {{ border-radius: 50%; height: 9px; width: 9px; background: var(--red); }}
.status-dot.ok {{ background: #2a9d62; }}
.grid {{ display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; align-items: start; }}
.panel {{ background: var(--surface); border: 1px solid var(--line); box-shadow: var(--shadow); padding: 24px; }}
.panel h2 {{ font-size: 17px; margin: 0 0 20px; }}
.form-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
.field {{ display: grid; gap: 7px; }}
.field.full {{ grid-column: 1 / -1; }}
.field label {{ color: #3e4a50; font-size: 12px; font-weight: 700; }}
input, select {{ appearance: none; background: #fff; border: 1px solid #bfc7c3; border-radius: 5px; color: var(--ink); font: inherit; min-height: 40px; padding: 8px 10px; width: 100%; }}
input:focus, select:focus {{ border-color: var(--teal); box-shadow: 0 0 0 3px rgba(11, 113, 108, .14); outline: 0; }}
.actions {{ border-top: 1px solid var(--line); display: flex; gap: 10px; margin-top: 24px; padding-top: 18px; }}
button {{ background: var(--teal); border: 1px solid var(--teal); border-radius: 5px; color: #fff; cursor: pointer; font: inherit; font-weight: 700; min-height: 40px; padding: 8px 14px; }}
button:hover {{ background: var(--teal-dark); }}
button.secondary {{ background: transparent; border-color: #9aa6a2; color: var(--ink); }}
button.danger {{ background: transparent; border-color: #d6a09a; color: var(--red); }}
.meta {{ display: grid; gap: 12px; }}
.metric {{ border-bottom: 1px solid var(--line); display: grid; gap: 4px; padding-bottom: 12px; }}
.metric:last-child {{ border-bottom: 0; padding-bottom: 0; }}
.metric span {{ color: var(--muted); font-size: 12px; }}
.metric strong {{ font-size: 15px; overflow-wrap: anywhere; }}
.notice {{ border-left: 4px solid; margin: 0 0 20px; padding: 12px 14px; }}
.notice.success {{ background: #eaf5ed; border-color: var(--green); color: #145137; }}
.notice.error {{ background: #fff0ed; border-color: var(--red); color: #8d2e25; }}
.password-form {{ display: grid; gap: 12px; margin-top: 20px; }}
.password-form .actions {{ margin-top: 4px; }}
@media (max-width: 820px) {{
  .shell {{ grid-template-columns: 1fr; }}
  .sidebar {{ gap: 18px; padding: 20px; }}
  .profile-list {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .sidebar-foot {{ display: none; }}
  .main {{ padding: 24px 16px 36px; }}
  .topline, .grid {{ grid-template-columns: 1fr; display: grid; }}
  .status {{ width: fit-content; }}
}}
@media (max-width: 560px) {{ .form-grid {{ grid-template-columns: 1fr; }} .field.full {{ grid-column: auto; }} .profile-list {{ grid-template-columns: 1fr; }} .actions {{ align-items: stretch; flex-direction: column; }} }}

/* FocusLens visual language */
:root {{
    --paper: #050816;
    --surface: rgba(17, 27, 52, .88);
    --surface-soft: rgba(11, 18, 38, .76);
    --ink: #f5f7ff;
    --muted: #8ea0be;
    --line: rgba(255, 255, 255, .12);
    --teal: #7c3aed;
    --teal-dark: #6330c8;
    --orange: #22d3ee;
    --red: #fb7185;
    --green: #5eead4;
    --shadow: 0 20px 40px rgba(0, 0, 0, .28);
}}
body {{
    background: radial-gradient(circle at 0 0, rgba(34, 211, 238, .16), transparent 26%), radial-gradient(circle at 88% 8%, rgba(124, 58, 237, .17), transparent 30%), var(--paper);
    font-family: Inter, "Segoe UI", Roboto, Arial, sans-serif;
    line-height: 1.5;
}}
.shell {{ grid-template-columns: 270px minmax(0, 1fr); }}
.sidebar {{ background: rgba(5, 8, 22, .86); border-right: 1px solid var(--line); backdrop-filter: blur(14px); padding: 26px 18px; }}
.brand {{ align-items: center; display: flex; font-size: 15px; gap: 11px; letter-spacing: .05em; }}
.brand-mark {{ box-shadow: 0 0 0 1px var(--line), 0 12px 24px rgba(0, 0, 0, .32); height: 42px; object-fit: contain; width: 42px; }}
.brand small {{ color: var(--accent-2, #22d3ee); font-size: 10px; letter-spacing: .12em; margin-top: 4px; text-transform: uppercase; }}
.label {{ color: #8ea0be; letter-spacing: .12em; }}
.profile-list {{ gap: 6px; margin-top: 10px; }}
.profile-link {{ border-left-color: transparent; color: #b8c8e5; min-height: 42px; padding: 10px 11px; }}
.profile-link:hover, .profile-link.active {{ background: linear-gradient(90deg, rgba(124, 58, 237, .24), rgba(34, 211, 238, .08)); border-color: #22d3ee; color: #fff; }}
.profile-dot {{ background: #52657f; box-shadow: 0 0 0 3px rgba(82, 101, 127, .16); }}
.profile-link.active .profile-dot {{ background: #22d3ee; box-shadow: 0 0 0 3px rgba(34, 211, 238, .14); }}
.sidebar-foot {{ border-top: 1px solid var(--line); color: #8ea0be; padding-top: 16px; }}
.main {{ max-width: 1480px; padding: 34px clamp(20px, 4vw, 62px) 52px; width: 100%; }}
.topline {{ border-bottom: 1px solid var(--line); margin-bottom: 26px; padding-bottom: 22px; }}
.topline h1 {{ font-size: clamp(25px, 3vw, 34px); letter-spacing: .01em; }}
.subtitle {{ color: #8ea0be; }}
.status {{ background: rgba(17, 27, 52, .72); border-color: var(--line); box-shadow: 0 10px 26px rgba(0, 0, 0, .2); color: #dfe9ff; }}
.status-dot.ok {{ background: #5eead4; box-shadow: 0 0 0 4px rgba(94, 234, 212, .12); }}
.grid {{ grid-template-columns: minmax(0, 1fr) 322px; gap: 18px; }}
.panel {{ background: var(--surface); border-color: var(--line); box-shadow: var(--shadow); padding: 22px; }}
.panel h2 {{ color: #f5f7ff; font-size: 16px; letter-spacing: .02em; }}
.field label {{ color: #c1cee5; letter-spacing: .02em; }}
input, select {{ background: rgba(5, 8, 22, .72); border-color: rgba(142, 160, 190, .4); border-radius: 4px; color: #f5f7ff; }}
input:focus, select:focus {{ border-color: #22d3ee; box-shadow: 0 0 0 3px rgba(34, 211, 238, .14); }}
option {{ background: #111b34; color: #f5f7ff; }}
.actions {{ border-top-color: var(--line); }}
button {{ background: linear-gradient(135deg, #7c3aed 0%, #22d3ee 100%); border-color: transparent; border-radius: 4px; box-shadow: 0 10px 22px rgba(124, 58, 237, .22); color: #fff; }}
button:hover {{ background: linear-gradient(135deg, #6833d7 0%, #16b8d5 100%); }}
button.secondary {{ border-color: var(--line); color: #dce8ff; }}
button.danger {{ border-color: rgba(251, 113, 133, .48); color: #fda4af; }}
.metric {{ border-bottom-color: var(--line); }}
.metric span {{ color: #8ea0be; }}
.metric strong {{ color: #f5f7ff; }}
.notice {{ background: rgba(17, 27, 52, .82); border-left-color: #22d3ee; color: #d9f7ff; }}
.notice.success {{ background: rgba(45, 212, 191, .12); border-color: #5eead4; color: #b8fff0; }}
.notice.error {{ background: rgba(251, 113, 133, .1); border-color: #fb7185; color: #fecdd3; }}
@media (max-width: 820px) {{
    .sidebar {{ border-bottom: 1px solid var(--line); border-right: 0; }}
    .brand-mark {{ height: 36px; width: 36px; }}
}}
</style>
</head>
<body>
<div class="shell">
<aside class="sidebar">
    <div><div class="brand"><img class="brand-mark" src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VLESS Gateway</small></span></div></div>
  <div><div class="label">Профили</div><nav class="profile-list">{''.join(sidebar)}</nav></div>
    <div><div class="label">Управление</div><nav class="profile-list"><a class="profile-link" data-panel-nav="wireguard" href="/wireguard"><span class="profile-dot"></span><span>WireGuard</span></a><a class="profile-link" data-panel-nav="happ-routing" href="/happ-routing"><span class="profile-dot"></span><span>HAPP Direct</span></a><a class="profile-link" data-panel-nav="happ-server" href="/happ-server"><span class="profile-dot"></span><span>HAPP Server</span></a></nav></div>
  <div class="sidebar-foot">Доступен только из WireGuard</div>
</aside>
<main class="main">
  <div class="topline">
    <div><h1>{esc(values['tag'])}</h1><p class="subtitle">Исходящий VLESS-профиль</p></div>
    <div class="status"><span class="status-dot {status_class}"></span>sing-box: {esc(state['sing_box'])}</div>
  </div>
  {message_banner(message, kind)}
  <div class="grid">
    <section class="panel">
      <h2>Подключение</h2>
      <form method="post" action="/save" autocomplete="off">
        <input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}">
        <input type="hidden" name="profile_tag" value="{esc(values['tag'])}">
        <div class="form-grid">
          <div class="field"><label for="server">Сервер</label><input id="server" name="server" value="{esc(values['server'])}" required></div>
          <div class="field"><label for="server_port">Порт</label><input id="server_port" name="server_port" inputmode="numeric" value="{esc(values['server_port'])}" required></div>
          <div class="field full"><label for="uuid">VLESS UUID</label><input id="uuid" name="uuid" value="{esc(values['uuid'])}" required></div>
          <div class="field"><label for="flow">Flow</label><select id="flow" name="flow">{flow_options}</select></div>
          <div class="field"><label for="fingerprint">TLS fingerprint</label><select id="fingerprint" name="fingerprint">{fingerprint_options}</select></div>
          <div class="field full"><label for="server_name">Reality SNI</label><input id="server_name" name="server_name" value="{esc(values['server_name'])}" required></div>
          <div class="field full"><label for="public_key">Reality public key</label><input id="public_key" name="public_key" value="{esc(values['public_key'])}" required></div>
          <div class="field"><label for="short_id">Reality short ID</label><input id="short_id" name="short_id" value="{esc(values['short_id'])}"></div>
          <div class="field"><label for="transport">Транспорт</label><select id="transport" name="transport">{transport_options}</select></div>
          <div class="field full"><label for="service_name">gRPC service name</label><input id="service_name" name="service_name" value="{esc(values['service_name'])}"></div>
        </div>
        <div class="actions"><button type="submit">Сохранить и применить</button></div>
      </form>
    </section>
    <aside class="meta">
      <section class="panel">
        <h2>Состояние</h2>
        <div class="metric"><span>VLESS-профилей</span><strong>{state['profiles']}</strong></div>
        <div class="metric"><span>Маршрут по умолчанию</span><strong>{esc(state['route'])}</strong></div>
        <div class="metric"><span>TPROXY gateway</span><strong>{esc(state['gateway'])}</strong></div>
        <div class="metric"><span>Изменено</span><strong>{esc(state['modified'])}</strong></div>
        <form method="post" action="/restart"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="actions"><button class="secondary" type="submit">Перезапустить sing-box</button></div></form>
      </section>
            <section class="panel">
                <h2>Исходящий профиль</h2>
                <form method="post" action="/route">
                    <input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}">
                    <div class="field"><label for="route_final">Маршрут по умолчанию</label><select id="route_final" name="route_final">{''.join(route_options)}</select></div>
                    <div class="actions"><button type="submit">Применить профиль</button></div>
                </form>
            </section>
      <section class="panel">
        <h2>Пароль панели</h2>
        <form class="password-form" method="post" action="/password" autocomplete="new-password">
          <input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}">
          <div class="field"><label for="new_password">Новый пароль</label><input id="new_password" name="new_password" type="password" minlength="12" required></div>
          <div class="field"><label for="confirm_password">Повторите пароль</label><input id="confirm_password" name="confirm_password" type="password" minlength="12" required></div>
          <div class="actions"><button class="danger" type="submit">Обновить пароль</button></div>
        </form>
      </section>
    </aside>
  </div>
</main>
</div>
<script src="/panel.js" defer></script>
<script src="/happ-actions.js" defer></script>
</body>
</html>'''


class VpnOnlyServer(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = 'VLESSAdmin'
    sys_version = ''

    def log_message(self, format_string, *args):
        return

    def send_common_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

    def send_html(self, status, content):
        payload = content.encode('utf-8')
        self.send_response(status)
        self.send_common_headers()
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_empty(self, status):
        self.send_response(status)
        self.send_common_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def send_favicon(self):
        try:
            payload = FAVICON_PATH.read_bytes()
        except OSError:
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_panel_css(self):
        try:
            payload = PANEL_CSS_PATH.read_bytes()
        except OSError:
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'text/css; charset=utf-8')
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_panel_js(self):
        try:
            payload = PANEL_JS_PATH.read_bytes()
        except OSError:
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'application/javascript; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_happ_actions_js(self):
        try:
            payload = HAPP_ACTIONS_PATH.read_bytes()
        except OSError:
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'application/javascript; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_binary(self, payload, content_type, disposition=None):
        self.send_response(HTTPStatus.OK)
        self.send_common_headers()
        self.send_header('Content-Type', content_type)
        if disposition:
            self.send_header('Content-Disposition', disposition)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_happ_qr(self, kind):
        try:
            link = happ_server_link(kind)
        except (OSError, ValueError, json.JSONDecodeError):
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        try:
            result = subprocess.run(
                [QR_ENCODE_BIN, '-t', 'SVG', '-o', '-', '-m', '2', '-s', '8', link],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self.send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if result.returncode != 0 or not result.stdout:
            self.send_empty(HTTPStatus.BAD_GATEWAY)
            return
        self.send_binary(result.stdout, 'image/svg+xml; charset=utf-8')

    def vpn_client_allowed(self):
        try:
            client_ip = ipaddress.ip_address(self.client_address[0])
            return any(client_ip in network for network in ACCESS_NETWORKS)
        except ValueError:
            return False

    def authenticated(self):
        header = self.headers.get('Authorization', '')
        if not header.startswith('Basic '):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode('utf-8')
            username, password = decoded.split(':', 1)
            expected_username, salt, expected_digest, _ = load_auth()
            actual_digest = password_digest(password, salt)
            return hmac.compare_digest(username, expected_username) and hmac.compare_digest(actual_digest, expected_digest)
        except (ValueError, UnicodeDecodeError, OSError, json.JSONDecodeError):
            return False

    def require_access(self):
        if not self.vpn_client_allowed():
            self.send_empty(HTTPStatus.FORBIDDEN)
            return False
        if not self.authenticated():
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_common_headers()
            self.send_header('WWW-Authenticate', f'Basic realm="{auth_realm()}", charset="UTF-8"')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return False
        FIRST_LOGIN_PATH.unlink(missing_ok=True)
        return True

    def parse_form(self):
        length_header = self.headers.get('Content-Length', '')
        try:
            length = int(length_header)
        except ValueError:
            raise ValueError('Некорректный запрос.')
        if length < 1 or length > MAX_BODY_SIZE:
            raise ValueError('Размер запроса недопустим.')
        raw = self.rfile.read(length).decode('utf-8')
        values = parse_qs(raw, keep_blank_values=True)
        if not hmac.compare_digest(form_value(values, 'csrf'), CSRF_TOKEN):
            raise ValueError('Проверка запроса не пройдена. Обновите страницу.')
        return values

    def redirect(self, profile='', message='', kind='success'):
        params = {'message': message, 'kind': kind}
        if profile:
            params['profile'] = profile
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        self.send_header('Location', '/?' + urlencode(params))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def redirect_wireguard(self, client='', message='', kind='success'):
        params = {'message': message, 'kind': kind}
        if client:
            params['client'] = client
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        self.send_header('Location', '/wireguard?' + urlencode(params))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def serve_wireguard_file(self, path):
        parts = path.strip('/').split('/')
        if len(parts) != 4 or parts[:2] != ['wireguard', 'client'] or not parts[2].isdigit():
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        try:
            if parts[3] == 'configuration':
                payload, headers = WG_ADMIN.api.client_config(parts[2])
                self.send_binary(
                    payload,
                    headers.get('Content-Type', 'application/octet-stream'),
                    headers.get('Content-Disposition', 'attachment; filename="wireguard.conf"'),
                )
                return
            if parts[3] == 'qr':
                payload, headers = WG_ADMIN.api.client_qr(parts[2])
                self.send_binary(payload, headers.get('Content-Type', 'image/svg+xml'))
                return
        except WgEasyApiError as error:
            self.send_html(HTTPStatus.BAD_GATEWAY, f'<h1>WireGuard временно недоступен</h1><p>{esc(error)}</p>')
            return
        self.send_empty(HTTPStatus.NOT_FOUND)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/favicon.png':
            self.send_favicon()
            return
        if parsed.path == '/panel.css':
            self.send_panel_css()
            return
        if parsed.path == '/panel.js':
            self.send_panel_js()
            return
        if parsed.path == '/happ-actions.js':
            self.send_happ_actions_js()
            return
        if not self.require_access():
            return
        if parsed.path == '/happ-qr':
            query = parse_qs(parsed.query)
            self.send_happ_qr(form_value(query, 'kind'))
            return
        if parsed.path.startswith('/wireguard/client/'):
            self.serve_wireguard_file(parsed.path)
            return
        if parsed.path == '/wireguard':
            query = parse_qs(parsed.query)
            try:
                content = WG_ADMIN.render(query, form_value(query, 'message'), form_value(query, 'kind') or 'success')
                self.send_html(HTTPStatus.OK, content)
            except WgEasyApiError as error:
                self.send_html(HTTPStatus.BAD_GATEWAY, f'<h1>WireGuard временно недоступен</h1><p>{esc(error)}</p>')
            return
        if parsed.path == '/happ-routing':
            self.send_html(HTTPStatus.OK, happ_routing_page())
            return
        if parsed.path == '/happ-server':
            self.send_html(HTTPStatus.OK, happ_server_page())
            return
        if parsed.path != '/':
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        query = parse_qs(parsed.query)
        try:
            config = load_config()
            selected = form_value(query, 'profile')
            content = render_page(config, selected, form_value(query, 'message'), form_value(query, 'kind') or 'success')
            self.send_html(HTTPStatus.OK, content)
        except Exception:
            self.send_html(HTTPStatus.INTERNAL_SERVER_ERROR, '<h1>Панель временно недоступна</h1>')

    def do_POST(self):
        if not self.require_access():
            return
        path = urlparse(self.path).path
        if path.startswith('/wireguard/'):
            try:
                values = self.parse_form()
                with WG_LOCK:
                    if path == '/wireguard/client/create':
                        identifier = WG_ADMIN.create_client(values)
                        self.redirect_wireguard(str(identifier or ''), 'Клиент создан.')
                        return
                    if path == '/wireguard/client/action':
                        message = WG_ADMIN.client_action(values)
                        self.redirect_wireguard('', message)
                        return
                    if path == '/wireguard/client/update':
                        identifier = WG_ADMIN.update_client(values)
                        self.redirect_wireguard(str(identifier), 'Настройки клиента сохранены.')
                        return
                    if path == '/wireguard/general':
                        WG_ADMIN.update_general(values)
                        self.redirect_wireguard('', 'Общие настройки сохранены.')
                        return
                    if path == '/wireguard/interface':
                        WG_ADMIN.update_interface(values)
                        self.redirect_wireguard('', 'Настройки интерфейса сохранены.')
                        return
                    if path == '/wireguard/interface/restart':
                        WG_ADMIN.api.restart_interface()
                        self.redirect_wireguard('', 'Интерфейс WireGuard перезапущен.')
                        return
                self.send_empty(HTTPStatus.NOT_FOUND)
            except WgEasyApiError as error:
                self.redirect_wireguard(form_value(locals().get('values', {}), 'client_id'), str(error), 'error')
            except (ValueError, RuntimeError) as error:
                self.redirect_wireguard(form_value(locals().get('values', {}), 'client_id'), str(error), 'error')
            except Exception:
                self.redirect_wireguard('', 'Операция WireGuard не выполнена.', 'error')
            return
        try:
            values = self.parse_form()
            if path == '/save':
                with CONFIG_LOCK:
                    config = load_config()
                    tag = update_profile(config, values)
                    apply_configuration(config)
                self.redirect(tag, 'Конфигурация проверена и применена.')
                return
            if path == '/route':
                with CONFIG_LOCK:
                    config = load_config()
                    tag = set_default_outbound(config, form_value(values, 'route_final'))
                    apply_configuration(config)
                self.redirect('', f'Исходящий профиль {tag} применён.')
                return
            if path == '/restart':
                with CONFIG_LOCK:
                    restart_sing_box()
                self.redirect('', 'sing-box перезапущен.')
                return
            if path == '/password':
                password = form_value(values, 'new_password')
                if password != form_value(values, 'confirm_password'):
                    raise ValueError('Пароли не совпадают.')
                set_password(password)
                self.send_html(HTTPStatus.OK, '<!doctype html><meta charset="utf-8"><p>Пароль обновлён. Обновите страницу и войдите с новым паролем.</p>')
                return
            self.send_empty(HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError) as error:
            profile = form_value(locals().get('values', {}), 'profile_tag')
            self.redirect(profile, str(error), 'error')
        except Exception:
            self.redirect('', 'Операция не выполнена. Текущая конфигурация сохранена.', 'error')


def main():
    if not AUTH_PATH.is_file():
        raise SystemExit(f'Authentication file missing: {AUTH_PATH}')
    try:
        ipaddress.IPv4Address(HOST)
    except ipaddress.AddressValueError as error:
        raise SystemExit('SING_BOX_ADMIN_HOST must be an IPv4 address') from error
    server = VpnOnlyServer((HOST, PORT), Handler)
    server.serve_forever(poll_interval=0.5)


if __name__ == '__main__':
    main()
