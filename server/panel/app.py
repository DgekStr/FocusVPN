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
import time
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse, urlsplit, urlunsplit

from wg_admin import WgAdmin
from wg_easy_api import WgEasyApi, WgEasyApiError
from happ_server import DEFAULT_SUBSCRIPTION_TITLE, SUBSCRIPTION_ANNOUNCEMENT
from happ_server import load_state as load_happ_state
from happ_server import public_vless_link, subscription_content, subscription_information_page, validate_subscription_content, vless_link_for_subscription
from happ_server_ui import page as happ_server_page
from happ_stats import HappStatsError, live_connections as happ_live_connections
from happ_stats import format_datetime
from panel_ui import render_shell, service_title
from happ_users import HappUsers, user_link, validate_subscription_base_url
from happ_history import HappHistory
from happ_history_ui import render_history
from server_metrics import ServerMetrics, collect_metrics, render_metrics_panel
from happ_stats import acknowledge_history_visits
from vless_monitor import VlessMonitor, load_settings as load_monitor_settings
from crm_bridge import authorized as crm_authorized, embed as crm_embed, panel_url as crm_panel_url

APP_DIR = Path('/etc/sing-box-admin')
SERVER_OUTBOUND_TYPES = ('vless', 'hysteria2', 'trojan', 'shadowsocks')
AUTH_PATH = APP_DIR / 'auth.json'
BACKUP_DIR = APP_DIR / 'backups'
FIRST_LOGIN_PATH = APP_DIR / 'first-login.txt'
WG_EASY_SECRET_PATH = APP_DIR / 'wg-easy-api.json'
CONFIG_PATH = Path('/etc/sing-box/config.json')
HAPP_CONFIG_PATH = Path('/etc/sing-box-happ-server/config.json')
HAPP_STATE_PATH = APP_DIR / 'happ-server.json'
SERVICE_CONTROL_PATH = APP_DIR / 'service-control.json'
GATEWAY_MODE_PATH = Path('/etc/focusvpn/gateway-mode.json')
WIREGUARD_CLIENT_CONFIG_PATH = Path('/etc/wireguard/wg-client.conf')
WIREGUARD_CLIENT_INPUT_PATH = Path('/etc/focusvpn/wireguard-client-input.conf')
FAVICON_PATH = Path('/opt/sing-box-admin/static/favicon.png')
FAVICON_SVG_PATH = Path('/opt/sing-box-admin/static/favicon.svg')
PANEL_CSS_PATH = Path('/opt/sing-box-admin/static/panel.css')
PANEL_JS_PATH = Path('/opt/sing-box-admin/static/panel.js')
CHART_JS_PATH = Path('/opt/sing-box-admin/static/chart.js')
HAPP_ACTIONS_PATH = Path('/opt/sing-box-admin/static/happ-actions.js')
QR_ENCODE_BIN = '/usr/bin/qrencode'
SING_BOX_BIN = '/usr/bin/sing-box'
SYSTEMCTL_BIN = '/usr/bin/systemctl'
DOCKER_BIN = '/usr/bin/docker'
SERVICE_CONTROL_UNIT = 'focusvpn-service-control@{}.service'
GATEWAY_MODE_UNIT = 'focusvpn-gateway-mode@{}.service'
DEFAULT_WIREGUARD_FALLBACK_GATEWAY = os.environ.get('FOCUSVPN_WG_FALLBACK_GATEWAY', '192.168.0.6')


def network_from_environment(name, default):
    try:
        return ipaddress.ip_network(os.environ.get(name, default), strict=False)
    except ValueError as error:
        raise RuntimeError(f'{name} must be a valid IP network') from error


def network_list_from_environment(name):
    value = os.environ.get(name, '').strip()
    if not value:
        return ()
    try:
        return tuple(ipaddress.ip_network(item.strip(), strict=False) for item in value.split(',') if item.strip())
    except ValueError as error:
        raise RuntimeError(f'{name} must contain valid IP networks') from error


VPN_NETWORK = network_from_environment('FOCUSVPN_WG_NETWORK', '10.8.0.0/24')
LAN_NETWORK = network_from_environment('FOCUSVPN_LAN_NETWORK', '192.168.0.0/24')
MANAGEMENT_NETWORK = network_from_environment('FOCUSVPN_MANAGEMENT_NETWORK', '10.1.17.0/24')
ACCESS_NETWORKS = (VPN_NETWORK, LAN_NETWORK, MANAGEMENT_NETWORK)
if os.environ.get('FOCUSVPN_ADMIN_NETWORK'):
    ACCESS_NETWORKS += (network_from_environment('FOCUSVPN_ADMIN_NETWORK', '0.0.0.0/0'),)
TRUSTED_PROXY_NETWORKS = network_list_from_environment('FOCUSVPN_TRUSTED_PROXY_NETWORKS')
HOST = os.environ.get('SING_BOX_ADMIN_HOST', '0.0.0.0')
PORT = int(os.environ.get('SING_BOX_ADMIN_PORT', '9443'))
HAPP_SUBSCRIPTION_BASE_URL = os.environ.get('FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL', '')
MAX_BODY_SIZE = 65536
SESSION_COOKIE_NAME = 'focusvpn_session'
SESSION_TTL_SECONDS = 12 * 60 * 60
CSRF_TOKEN = secrets.token_urlsafe(32)
CONFIG_LOCK = threading.Lock()
HAPP_LOCK = threading.Lock()
WG_LOCK = threading.Lock()
SERVICE_CONTROL_LOCK = threading.Lock()
SESSION_LOCK = threading.Lock()
OUTBOUND_CHECK_LOCK = threading.Lock()
OUTBOUND_CHECK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='outbound-check')
OUTBOUND_CHECK_JOBS = {}
VLESS_MONITOR = None
HAPP_USERS = None
HAPP_EXPIRY_STOP = threading.Event()
HAPP_HISTORY = None
HAPP_HISTORY_STOP = threading.Event()
SERVER_METRICS = None
SERVER_METRICS_STOP = threading.Event()
SESSIONS = {}
WG_ADMIN = WgAdmin(WgEasyApi(WG_EASY_SECRET_PATH, os.environ.get('FOCUSVPN_WG_EASY_API_URL', 'http://127.0.0.1:51821')), CSRF_TOKEN)
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


def wireguard_state():
    result = command([DOCKER_BIN, 'inspect', '--format', '{{.State.Status}}', 'wg-easy'], timeout=15)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else 'unknown'


def validate_wireguard_gateway(value):
    try:
        gateway = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise ValueError('Шлюз WireGuard должен быть IPv4-адресом.') from error
    if gateway.is_unspecified or gateway.is_loopback or gateway.is_multicast:
        raise ValueError('Шлюз WireGuard должен быть доступным IPv4-адресом.')
    return str(gateway)


def load_service_control():
    if not SERVICE_CONTROL_PATH.is_file():
        return {'wireguard_fallback_gateway': DEFAULT_WIREGUARD_FALLBACK_GATEWAY}
    try:
        payload = json.loads(SERVICE_CONTROL_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('Не удалось прочитать настройки сервисного управления.') from error
    if not isinstance(payload, dict):
        raise ValueError('Настройки сервисного управления повреждены.')
    return {
        'wireguard_fallback_gateway': validate_wireguard_gateway(
            str(payload.get('wireguard_fallback_gateway', DEFAULT_WIREGUARD_FALLBACK_GATEWAY))
        ),
    }


def save_wireguard_fallback_gateway(value):
    gateway = validate_wireguard_gateway(value)
    data = (json.dumps({'wireguard_fallback_gateway': gateway}, indent=2) + '\n').encode('utf-8')
    if SERVICE_CONTROL_PATH.is_file():
        backup_file(SERVICE_CONTROL_PATH, 'service-control')
    write_atomic_file(SERVICE_CONTROL_PATH, data, mode=0o600)
    return gateway


def validate_wireguard_client_config(value):
    sections = {}
    current = None
    for raw_line in value.splitlines():
        line = raw_line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1]
            if current in sections or current not in ('Interface', 'Peer'):
                raise ValueError('Конфигурация должна содержать секции [Interface] и один [Peer].')
            sections[current] = {}
            continue
        if current is None or '=' not in line:
            raise ValueError('Некорректная строка в конфигурации WireGuard.')
        key, item = (part.strip() for part in line.split('=', 1))
        if key in sections[current] or not item:
            raise ValueError('В конфигурации WireGuard есть пустое или повторяющееся поле.')
        sections[current][key] = item

    if set(sections) != {'Interface', 'Peer'}:
        raise ValueError('Конфигурация должна содержать секции [Interface] и один [Peer].')
    interface, peer = sections['Interface'], sections['Peer']
    if set(interface) - {'PrivateKey', 'Address', 'DNS', 'MTU'}:
        raise ValueError('В [Interface] разрешены только PrivateKey, Address, DNS и MTU.')
    if set(peer) - {'PublicKey', 'PresharedKey', 'Endpoint', 'AllowedIPs', 'PersistentKeepalive'}:
        raise ValueError('В [Peer] найдены неподдерживаемые параметры.')
    if not {'PrivateKey', 'Address'} <= set(interface) or not {'PublicKey', 'Endpoint', 'AllowedIPs'} <= set(peer):
        raise ValueError('Не заданы обязательные ключ, адрес, peer, endpoint или AllowedIPs.')

    def validate_key(name, secret=False):
        encoded = interface[name] if secret else peer[name]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError(f'{name}: ключ WireGuard должен быть base64-строкой.') from error
        if len(decoded) != 32:
            raise ValueError(f'{name}: ключ WireGuard должен содержать 32 байта.')

    validate_key('PrivateKey', secret=True)
    validate_key('PublicKey')
    if 'PresharedKey' in peer:
        validate_key('PresharedKey')
    try:
        addresses = [ipaddress.ip_interface(item.strip()) for item in interface['Address'].split(',')]
        allowed_networks = [ipaddress.ip_network(item.strip(), strict=False) for item in peer['AllowedIPs'].split(',')]
    except ValueError as error:
        raise ValueError('Address или AllowedIPs содержит некорректную сеть.') from error
    if not addresses or any(item.version != 4 for item in addresses):
        raise ValueError('Для клиентского шлюза требуется IPv4 Address.')
    if not allowed_networks or any(item.version != 4 for item in allowed_networks) or ipaddress.ip_network('0.0.0.0/0') not in allowed_networks:
        raise ValueError('AllowedIPs должен включать IPv4-маршрут 0.0.0.0/0.')
    endpoint = peer['Endpoint']
    if endpoint.startswith('[') and ']:' in endpoint:
        host, port = endpoint[1:].rsplit(']:', 1)
        try:
            if ipaddress.ip_address(host).version != 6:
                raise ValueError
        except ValueError as error:
            raise ValueError('Endpoint содержит некорректный IPv6-адрес.') from error
    else:
        host, separator, port = endpoint.rpartition(':')
        if not separator:
            raise ValueError('Endpoint должен иметь формат host:port.')
        try:
            validate_server(host, 'Endpoint')
        except ValueError as error:
            raise ValueError('Endpoint содержит некорректный адрес сервера.') from error
    require_port(port)
    if 'DNS' in interface:
        try:
            for address in interface['DNS'].split(','):
                ipaddress.ip_address(address.strip())
        except ValueError as error:
            raise ValueError('DNS должен содержать IP-адреса.') from error
    try:
        if 'MTU' in interface and not 576 <= int(interface['MTU']) <= 9000:
            raise ValueError('MTU должен быть в диапазоне 576-9000.')
        if 'PersistentKeepalive' in peer and not 0 <= int(peer['PersistentKeepalive']) <= 65535:
            raise ValueError('PersistentKeepalive должен быть в диапазоне 0-65535.')
    except ValueError as error:
        if str(error).startswith(('MTU', 'PersistentKeepalive')):
            raise
        raise ValueError('MTU и PersistentKeepalive должны быть числами.') from error

    lines = ['[Interface]', f"PrivateKey = {interface['PrivateKey']}", f"Address = {interface['Address']}", 'Table = off']
    for key in ('MTU',):
        if key in interface:
            lines.append(f'{key} = {interface[key]}')
    lines.extend(('', '[Peer]', f"PublicKey = {peer['PublicKey']}"))
    for key in ('PresharedKey', 'Endpoint', 'AllowedIPs', 'PersistentKeepalive'):
        if key in peer:
            lines.append(f'{key} = {peer[key]}')
    return '\n'.join(lines) + '\n'


def save_wireguard_client_config(value):
    if len(value.encode('utf-8')) > 32768:
        raise ValueError('Конфигурация WireGuard слишком большая.')
    original = value.replace('\r\n', '\n').replace('\r', '\n')
    config = validate_wireguard_client_config(value).encode('utf-8')
    WIREGUARD_CLIENT_CONFIG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    WIREGUARD_CLIENT_INPUT_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    write_atomic_file(WIREGUARD_CLIENT_INPUT_PATH, original.encode('utf-8'), mode=0o600)
    write_atomic_file(WIREGUARD_CLIENT_CONFIG_PATH, config, mode=0o600)


def load_wireguard_client_text():
    for path in (WIREGUARD_CLIENT_INPUT_PATH, WIREGUARD_CLIENT_CONFIG_PATH):
        try:
            return path.read_text(encoding='utf-8')
        except FileNotFoundError:
            continue
    return ''


def gateway_mode():
    try:
        payload = json.loads(GATEWAY_MODE_PATH.read_text(encoding='utf-8'))
        return payload.get('mode') if payload.get('mode') in ('vless', 'wireguard', 'default') else 'vless'
    except (OSError, json.JSONDecodeError, AttributeError):
        return 'vless'


def control_gateway_mode(mode):
    previous_mode = gateway_mode()
    if mode not in ('vless', 'wireguard', 'default'):
        raise ValueError('Неизвестный режим шлюза.')
    if mode == 'wireguard' and not WIREGUARD_CLIENT_CONFIG_PATH.is_file():
        raise ValueError('Сначала сохраните конфигурацию внешнего WireGuard-сервера.')
    result = command([SYSTEMCTL_BIN, 'start', GATEWAY_MODE_UNIT.format(mode)], timeout=120)
    if result.returncode != 0:
        detail = result.stdout.strip()
        raise RuntimeError(detail or f'Служба переключения шлюза завершилась с кодом {result.returncode}.')
    if gateway_mode() != mode:
        raise RuntimeError(f'Не удалось переключить шлюз в режим {mode}.')
    if VLESS_MONITOR is not None and previous_mode != mode:
        VLESS_MONITOR.record_switch(previous_mode, mode, 'manual_mode')


def control_system_service(name, action):
    if action not in ('start', 'restart', 'stop'):
        raise ValueError('Неизвестная операция сервиса.')
    if name == 'sing-box' and action in ('start', 'restart') and gateway_mode() in ('wireguard', 'default'):
        raise ValueError('Переключите режим шлюза на VLESS перед запуском sing-box/TPROXY.')
    result = command([SYSTEMCTL_BIN, action, name], timeout=75)
    expected = 'inactive' if action == 'stop' else 'active'
    if result.returncode != 0 or service_state(name) != expected:
        raise RuntimeError(f'Не удалось выполнить операцию {action} для {name}.')


def control_wireguard(action):
    if action not in ('start', 'restart', 'stop'):
        raise ValueError('Неизвестная операция WireGuard.')
    result = command([SYSTEMCTL_BIN, 'start', SERVICE_CONTROL_UNIT.format(f'wireguard-{action}')], timeout=90)
    expected = 'exited' if action == 'stop' else 'running'
    if result.returncode != 0 or wireguard_state() != expected:
        raise RuntimeError(f'Не удалось выполнить операцию {action} для WireGuard.')


def schedule_server_reboot():
    result = command([SYSTEMCTL_BIN, 'start', '--no-block', SERVICE_CONTROL_UNIT.format('reboot')], timeout=15)
    if result.returncode != 0:
        raise RuntimeError('Не удалось запланировать перезагрузку сервера.')


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
    with SESSION_LOCK:
        SESSIONS.clear()


def issue_session(client_ip):
    now = dt.datetime.now(dt.timezone.utc)
    token = secrets.token_urlsafe(32)
    with SESSION_LOCK:
        expired = [value for value, (expires_at, _) in SESSIONS.items() if expires_at <= now]
        for value in expired:
            del SESSIONS[value]
        SESSIONS[token] = (now + dt.timedelta(seconds=SESSION_TTL_SECONDS), client_ip)
    return token


def valid_session(token, client_ip):
    if not token:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    with SESSION_LOCK:
        record = SESSIONS.get(token)
        if record is None:
            return False
        expires_at, expected_client_ip = record
        if expires_at <= now:
            del SESSIONS[token]
            return False
        return hmac.compare_digest(expected_client_ip, client_ip)


def revoke_session(token):
    with SESSION_LOCK:
        SESSIONS.pop(token, None)


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
        if outbound.get('type') in SERVER_OUTBOUND_TYPES + ('urltest',) and outbound.get('tag')
    ]


def set_default_outbound(config, tag):
    allowed_tags = {outbound['tag'] for outbound in selectable_outbounds(config)}
    if tag not in allowed_tags:
        raise ValueError('Выбранный исходящий профиль не существует.')
    config.setdefault('route', {})['final'] = tag
    return tag


def managed_server_outbounds(config):
    return [item for item in config.get('outbounds', []) if item.get('tag') and item.get('type') in SERVER_OUTBOUND_TYPES]


def next_server_tag(config):
    tags = {item.get('tag') for item in config.get('outbounds', [])}
    for index in range(1, 1000):
        tag = f'auto-{index}'
        if tag not in tags:
            return tag
    raise ValueError('Нет свободного auto-N tag.')


def import_xray_outbound(source, tag):
    protocol = source.get('protocol', '')
    settings = source.get('settings', {})
    stream = source.get('streamSettings', {})
    if protocol == 'wireguard':
        raise ValueError('WireGuard не импортируется как outbound: используйте раздел клиента внешнего WireGuard.')
    if stream.get('network') == 'xhttp':
        raise ValueError('XHTTP не поддерживается используемым sing-box; этот профиль пропущен без замены транспорта.')
    if protocol in ('trojan', 'shadowsocks'):
        if stream.get('network', 'tcp') != 'tcp':
            raise ValueError('Для Trojan/Shadowsocks поддерживается импорт без дополнительного транспорта, network=tcp.')
        servers = settings.get('servers', [])
        server = servers[0] if servers else settings
        password = server.get('password')
        if not isinstance(password, str) or not password:
            raise ValueError('В профиле отсутствует пароль сервера.')
        outbound = {
            'type': protocol, 'tag': tag,
            'server': validate_server(server.get('address', ''), 'Server'),
            'server_port': require_port(str(server.get('port', ''))),
            'password': password,
        }
        if protocol == 'shadowsocks':
            if stream.get('security', 'none') != 'none':
                raise ValueError('Shadowsocks с дополнительным TLS требует отдельного транспорта.')
            outbound['method'] = server.get('method', '')
        else:
            if stream.get('security', 'tls') != 'tls':
                raise ValueError('Для Trojan требуется TLS.')
            tls_source = stream.get('tlsSettings', {})
            tls = {'enabled': True}
            if tls_source.get('serverName'):
                tls['server_name'] = validate_server(tls_source['serverName'], 'SNI')
            if tls_source.get('alpn'):
                tls['alpn'] = tls_source['alpn']
            if tls_source.get('fingerprint'):
                tls['utls'] = {'enabled': True, 'fingerprint': tls_source['fingerprint']}
            if 'allowInsecure' in tls_source:
                if not isinstance(tls_source['allowInsecure'], bool):
                    raise ValueError('Xray allowInsecure должен быть true или false.')
                tls['insecure'] = tls_source['allowInsecure']
            outbound['tls'] = tls
        return outbound
    if protocol in ('hysteria', 'hysteria2'):
        hysteria = stream.get('hysteriaSettings', {})
        password = hysteria.get('auth') or settings.get('auth') or source.get('password')
        if not password:
            raise ValueError('В Xray Hysteria2 JSON отсутствует полный auth.')
        tls_settings = stream.get('tlsSettings', {})
        tls = {'enabled': True, 'alpn': tls_settings.get('alpn') or ['h3']}
        if 'allowInsecure' in tls_settings:
            if not isinstance(tls_settings['allowInsecure'], bool):
                raise ValueError('Xray allowInsecure должен быть true или false.')
            tls['insecure'] = tls_settings['allowInsecure']
        if tls_settings.get('serverName'):
            tls['server_name'] = validate_server(tls_settings['serverName'], 'Hysteria2 SNI')
        server = settings.get('address') or settings.get('server')
        port = settings.get('port') or settings.get('server_port')
        return {
            'type': 'hysteria2', 'tag': tag,
            'server': validate_server(str(server or ''), 'Server'),
            'server_port': require_port(str(port or '')),
            'password': str(password), 'tls': tls,
        }
    if protocol != 'vless':
        raise ValueError('Поддерживаются VLESS, Hysteria2, Trojan и Shadowsocks.')
    servers = settings.get('vnext', [])
    server = servers[0] if servers else settings
    user = next((item for item in server.get('users', []) if item.get('id')), None) if servers else settings
    if user is None:
        raise ValueError('В Xray VLESS JSON отсутствует UUID пользователя.')
    network = stream.get('network', 'tcp')
    security = stream.get('security', '')
    tls_source = stream.get('realitySettings' if security == 'reality' else 'tlsSettings', {})
    tls = {'enabled': True}
    if tls_source.get('serverName'):
        tls['server_name'] = validate_server(tls_source['serverName'], 'SNI')
    if tls_source.get('fingerprint'):
        tls['utls'] = {'enabled': True, 'fingerprint': tls_source['fingerprint']}
    if security == 'reality':
        tls.setdefault('utls', {'enabled': True, 'fingerprint': 'chrome'})
        public_key = tls_source.get('publicKey', '')
        short_id = str(tls_source.get('shortId', '')).lower()
        if not PUBLIC_KEY_PATTERN.fullmatch(public_key) or not SHORT_ID_PATTERN.fullmatch(short_id):
            raise ValueError('Xray REALITY publicKey или shortId некорректен.')
        tls['reality'] = {'enabled': True, 'public_key': public_key, 'short_id': short_id}
    elif security not in ('tls', ''):
        raise ValueError('Для импорта поддерживается TLS или REALITY.')
    outbound = {
        'type': 'vless', 'tag': tag,
        'server': validate_server(server.get('address', ''), 'Server'),
        'server_port': require_port(str(server.get('port', ''))),
        'uuid': str(uuid.UUID(user['id'])), 'tls': tls,
    }
    flow = user.get('flow', '')
    if flow:
        if flow != 'xtls-rprx-vision':
            raise ValueError('Неподдерживаемый VLESS flow.')
        outbound['flow'] = flow
    if network == 'grpc':
        outbound['transport'] = {'type': 'grpc', 'service_name': stream.get('grpcSettings', {}).get('serviceName', 'grpc')}
    elif network != 'tcp':
        raise ValueError('Для Xray VLESS поддерживаются TCP и gRPC.')
    return outbound


def import_server_json(raw, config, replace_tag='', requested_tag=''):
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError('Импорт: некорректный JSON.') from error
    if not isinstance(payload, dict):
        raise ValueError('Импортируйте JSON-объект сервера или полный JSON config.')
    source = payload
    xray_protocol = payload.get('protocol')
    sing_box_type = payload.get('type')
    if not xray_protocol and sing_box_type not in SERVER_OUTBOUND_TYPES:
        source = next((item for item in payload.get('outbounds', []) if item.get('type') in SERVER_OUTBOUND_TYPES or item.get('protocol') in ('vless', 'hysteria', 'hysteria2', 'trojan', 'shadowsocks', 'wireguard')), None)
        if source is None:
            raise ValueError('В JSON не найден VPN outbound.')
        xray_protocol = source.get('protocol')
        sing_box_type = source.get('type')
    if replace_tag:
        tag = replace_tag
        if not any(item.get('tag') == tag for item in managed_server_outbounds(config)):
            raise ValueError('Можно заменять только существующие VPN-профили.')
    else:
        tag = requested_tag or next_server_tag(config)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', tag):
            raise ValueError('Tag содержит недопустимые символы.')
        if any(item.get('tag') == tag for item in config.get('outbounds', [])):
            raise ValueError('Такой tag уже существует; выберите его в списке замены.')
    if sing_box_type in SERVER_OUTBOUND_TYPES:
        outbound = json.loads(json.dumps(source))
        outbound['tag'] = tag
    elif xray_protocol in ('vless', 'hysteria', 'hysteria2', 'trojan', 'shadowsocks', 'wireguard'):
        outbound = import_xray_outbound(source, tag)
    else:
        raise ValueError('Поддерживается импорт VLESS, Hysteria2, Trojan и Shadowsocks.')
    if outbound.get('type') not in SERVER_OUTBOUND_TYPES:
        raise ValueError('Неподдерживаемый тип VPN outbound.')
    config['outbounds'] = [item for item in config.get('outbounds', []) if item.get('tag') != tag]
    config['outbounds'].append(outbound)
    selector = next((item for item in config['outbounds'] if item.get('type') == 'urltest' and item.get('tag') == 'vless-auto'), None)
    if selector is not None and tag not in selector.setdefault('outbounds', []):
        selector['outbounds'].append(tag)
    return tag


def import_server_batch(raw, config, replace_tag='', requested_tag='', validator=None):
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError('Импорт: некорректный JSON.') from error
    entries = payload if isinstance(payload, list) else [payload]
    if not entries or len(entries) > 16:
        raise ValueError('За один импорт допускается от 1 до 16 конфигураций.')
    if len(entries) > 1 and (replace_tag or requested_tag):
        raise ValueError('Для массива выберите добавление новых auto-N без указания общего tag.')
    working = json.loads(json.dumps(config))
    imported, skipped = [], []
    for index, entry in enumerate(entries, 1):
        candidate = json.loads(json.dumps(working))
        try:
            tag = import_server_json(json.dumps(entry), candidate, replace_tag, requested_tag)
            if validator is not None:
                validator((json.dumps(candidate, ensure_ascii=False) + '\n').encode('utf-8'))
        except (ValueError, RuntimeError) as error:
            skipped.append(f'Элемент {index}: {error}')
            continue
        except (TypeError, KeyError, AttributeError):
            skipped.append(f'Элемент {index}: неверная структура конфигурации.')
            continue
        working = candidate
        imported.append(tag)
    if not imported:
        raise ValueError('Ничего не импортировано. ' + ' '.join(skipped))
    config.clear()
    config.update(working)
    return imported, skipped


def remove_server_json(config, tag):
    managed = managed_server_outbounds(config)
    if not any(item.get('tag') == tag for item in managed):
        raise ValueError('Можно удалить только существующий VPN-сервер.')
    if len(managed) <= 1:
        raise ValueError('Нельзя удалить последний VPN-сервер.')
    selectors = [item for item in config.get('outbounds', []) if item.get('type') == 'urltest' and tag in item.get('outbounds', [])]
    if any(len(item.get('outbounds', [])) <= 1 for item in selectors):
        raise ValueError('Нельзя удалить последний outbound из urltest selector.')
    config['outbounds'] = [item for item in config.get('outbounds', []) if item.get('tag') != tag]
    for selector in config.get('outbounds', []):
        if selector.get('type') == 'urltest':
            selector['outbounds'] = [member for member in selector.get('outbounds', []) if member != tag]
    route_rules = config.get('route', {}).get('rules', [])
    config.setdefault('route', {})['rules'] = [
        rule for rule in route_rules
        if rule.get('outbound') != tag and rule.get('outboundTag') != tag
    ]
    if config.get('route', {}).get('final') == tag:
        urltest = next((item.get('tag') for item in config.get('outbounds', []) if item.get('type') == 'urltest'), None)
        config.setdefault('route', {})['final'] = urltest or managed_server_outbounds(config)[0]['tag']
    return tag


def check_server_connection(config, tag):
    selected = next((item for item in managed_server_outbounds(config) if item.get('tag') == tag), None)
    if selected is None or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', tag):
        raise ValueError('Сервер для проверки не найден.')
    expected = hashlib.sha256(json.dumps(selected, sort_keys=True).encode('utf-8')).hexdigest()
    result = command([SYSTEMCTL_BIN, 'start', f'focusvpn-outbound-test@{tag}.service'], timeout=70)
    if result.returncode:
        return 'error', 'Не удалось запустить проверку соединения; сохранённая конфигурация не изменена.'
    try:
        outcome = json.loads((APP_DIR / 'outbound-checks' / f'{tag}.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return 'error', 'Результат проверки соединения недоступен.'
    if outcome.get('fingerprint') != expected:
        return 'error', 'Профиль изменился во время проверки. Повторите тест.'
    if outcome.get('ok'):
        return 'success', f'Соединение {tag} проверено: внешний IP {outcome.get("ip", "")}. Маршрут не изменён.'
    return 'error', f'{tag}: {outcome.get("error", "HTTPS-проверка не прошла.")}'


def background_server_check(config, tag):
    try:
        kind, message = check_server_connection(config, tag)
        result = {'state': kind, 'message': message}
        try:
            outcome = json.loads((APP_DIR / 'outbound-checks' / f'{tag}.json').read_text(encoding='utf-8'))
            selected = next(item for item in managed_server_outbounds(config) if item['tag'] == tag)
            fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True).encode('utf-8')).hexdigest()
            if outcome.get('fingerprint') == fingerprint:
                result['latency_ms'] = outcome.get('latency_ms') if kind == 'success' else None
                result['checked_at'] = outcome.get('checked_at')
        except (OSError, ValueError, StopIteration):
            pass
        return result
    except Exception:
        return {'state': 'error', 'message': 'Проверка соединения завершилась с ошибкой. Повторите тест.'}


def queue_server_checks(config, tags):
    snapshot = json.loads(json.dumps(config))
    profiles_by_tag = {item['tag']: item for item in managed_server_outbounds(snapshot)}
    if any(tag not in profiles_by_tag for tag in tags):
        raise ValueError('Сервер для проверки не найден.')
    with OUTBOUND_CHECK_LOCK:
        for tag in tags:
            fingerprint = hashlib.sha256(json.dumps(profiles_by_tag[tag], sort_keys=True).encode('utf-8')).hexdigest()
            previous = OUTBOUND_CHECK_JOBS.get(tag)
            if previous is not None and previous['fingerprint'] == fingerprint and not previous['future'].done():
                continue
            future = OUTBOUND_CHECK_EXECUTOR.submit(background_server_check, snapshot, tag)
            OUTBOUND_CHECK_JOBS[tag] = {'fingerprint': fingerprint, 'future': future}
        return [OUTBOUND_CHECK_JOBS[tag]['future'] for tag in tags]


def outbound_check_states(config):
    checks = []
    with OUTBOUND_CHECK_LOCK:
        jobs = dict(OUTBOUND_CHECK_JOBS)
    for selected in managed_server_outbounds(config):
        tag = selected['tag']
        fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True).encode('utf-8')).hexdigest()
        job = jobs.get(tag)
        result = {'state': 'unchecked', 'message': 'Не проверен'}
        if job is not None and job['fingerprint'] == fingerprint:
            future = job['future']
            if future.done():
                result = future.result()
            elif future.running():
                result = {'state': 'running', 'message': 'Проверяется соединение…'}
            else:
                result = {'state': 'queued', 'message': 'В очереди на проверку'}
        else:
            try:
                outcome = json.loads((APP_DIR / 'outbound-checks' / f'{tag}.json').read_text(encoding='utf-8'))
                if outcome.get('fingerprint') == fingerprint:
                    result = {'state': 'success', 'message': 'Внешний IP: ' + str(outcome.get('ip', ''))} if outcome.get('ok') else {'state': 'error', 'message': str(outcome.get('error', 'Проверка не прошла.'))}
                    result['latency_ms'] = outcome.get('latency_ms') if outcome.get('ok') else None
                    result['checked_at'] = outcome.get('checked_at')
            except (OSError, json.JSONDecodeError):
                pass
        checks.append({'tag': tag, **result})
    return {'checks': checks, 'automation': VLESS_MONITOR.status() if VLESS_MONITOR is not None else {}}


def switch_monitored_route(tag, expected_route, expected_servers, latency_ms, revision):
    with SERVICE_CONTROL_LOCK:
        with CONFIG_LOCK:
            settings = load_monitor_settings(APP_DIR)
            if VLESS_MONITOR is None or not settings['enabled'] or not settings['auto_switch'] or gateway_mode() != 'vless':
                return False
            with VLESS_MONITOR.lock:
                if VLESS_MONITOR.revision != revision:
                    return False
            config = load_config()
            current_servers = [item for item in config.get('outbounds', []) if item.get('type') == 'vless']
            if config.get('route', {}).get('final') != expected_route or current_servers != expected_servers:
                return False
            set_default_outbound(config, tag)
            return apply_configuration(config, source='automatic', latency_ms=latency_ms)


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
    if gateway_mode() in ('wireguard', 'default'):
        return False
    result = command([SYSTEMCTL_BIN, 'restart', 'sing-box'], timeout=45)
    if result.returncode != 0 or service_state('sing-box') != 'active':
        raise RuntimeError('sing-box не запустился с новой конфигурацией.')
    return True


def synchronized_happ_config(config, happ_config, mode):
    candidate = json.loads(json.dumps(happ_config))
    shared_types = SERVER_OUTBOUND_TYPES + ('urltest',)
    shared = [item for item in config.get('outbounds', []) if item.get('type') in shared_types]
    preserved = [item for item in candidate.get('outbounds', []) if item.get('type') not in shared_types]
    shared_tags = {item.get('tag') for item in shared}
    if any(item.get('tag') in shared_tags for item in preserved):
        raise ValueError('Tag VPN-сервера конфликтует со служебным outbound HAPP.')
    candidate['outbounds'] = preserved + json.loads(json.dumps(shared))
    final = 'focusvpn-wg-direct' if mode == 'wireguard' else config.get('route', {}).get('final')
    if not any(item.get('tag') == final for item in candidate['outbounds']):
        direct = next((item for item in config.get('outbounds', []) if item.get('tag') == final and item.get('type') == 'direct'), None)
        if direct is not None:
            candidate['outbounds'].append(json.loads(json.dumps(direct)))
    candidate.setdefault('route', {})['final'] = final
    return candidate


def apply_configuration(config, source='manual', latency_ms=None):
    with HAPP_LOCK:
        return apply_shared_configuration(config, source, latency_ms)


def apply_shared_configuration(config, source='manual', latency_ms=None):
    data = (json.dumps(config, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    mode = gateway_mode()
    happ_config = synchronized_happ_config(config, json.loads(HAPP_CONFIG_PATH.read_text(encoding='utf-8')), mode)
    happ_data = (json.dumps(happ_config, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    check_candidate(data)
    check_happ_candidate(happ_data)
    backup = backup_configuration()
    previous_config = json.loads(backup.read_bytes())
    previous_route = previous_config.get('route', {}).get('final')
    happ_backup = backup_file(HAPP_CONFIG_PATH, 'happ-server-config')
    previous_mode_data = GATEWAY_MODE_PATH.read_bytes() if GATEWAY_MODE_PATH.exists() else None
    try:
        write_atomic_bytes(data)
        write_atomic_file(HAPP_CONFIG_PATH, happ_data, mode=0o640, group_name='sing-box')
        if mode == 'wireguard':
            state = json.loads(previous_mode_data) if previous_mode_data else {'mode': mode}
            state['happ_route'] = config['route']['final']
            write_atomic_file(GATEWAY_MODE_PATH, (json.dumps(state) + '\n').encode('utf-8'), mode=0o600)
        applied = restart_sing_box()
        restart_happ_server()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        write_atomic_bytes(backup.read_bytes())
        write_atomic_file(HAPP_CONFIG_PATH, happ_backup.read_bytes(), mode=0o640, group_name='sing-box')
        if previous_mode_data is not None:
            write_atomic_file(GATEWAY_MODE_PATH, previous_mode_data, mode=0o600)
        restart_sing_box()
        restart_happ_server()
        raise RuntimeError('Новая конфигурация не запустилась; конфиги шлюза и HAPP восстановлены.')
    if VLESS_MONITOR is not None:
        try:
            route_changed = previous_route != config.get('route', {}).get('final')
            if route_changed and applied:
                VLESS_MONITOR.record_switch(previous_route, config['route']['final'], source, latency_ms)
            elif route_changed:
                VLESS_MONITOR.reset_candidate()
                VLESS_MONITOR.append_event({'event': 'route_selected_deferred', 'old_route': previous_route, 'new_route': config['route']['final'], 'source': source})
            elif profiles(previous_config) != profiles(config):
                VLESS_MONITOR.reset_candidate()
                VLESS_MONITOR.append_event({'event': 'profiles_changed', 'source': source, 'message': 'Настройки VLESS изменены; серия кандидата сброшена.'})
        except OSError:
            pass
    return applied


def write_atomic_file(path, data, mode=0o640, group_name=None):
    descriptor, temporary_name = tempfile.mkstemp(prefix='.focusvpn-admin-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if group_name:
            os.chown(temporary_name, 0, grp.getgrnam(group_name).gr_gid)
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def backup_file(path, prefix):
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = BACKUP_DIR / f'{prefix}-{timestamp}.json'
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    stored = sorted(BACKUP_DIR.glob(f'{prefix}-*.json'), reverse=True)
    for stale in stored[10:]:
        stale.unlink(missing_ok=True)
    return backup


def json_text(path):
    return json.dumps(json.loads(path.read_text(encoding='utf-8')), indent=2, ensure_ascii=False)


def parse_json_object(raw, label):
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f'{label}: некорректный JSON.') from error
    if not isinstance(payload, dict):
        raise ValueError(f'{label}: ожидается JSON-объект.')
    return payload


def check_happ_candidate(data):
    with tempfile.TemporaryDirectory(prefix='.happ-server-check-', dir=HAPP_CONFIG_PATH.parent) as directory:
        candidate = Path(directory) / 'config.json'
        candidate.write_bytes(data)
        os.chmod(candidate, 0o640)
        result = command([SING_BOX_BIN, 'check', '-C', directory], timeout=30)
        if result.returncode != 0:
            raise RuntimeError('sing-box отклонил HAPP Server конфигурацию.')


def restart_happ_server():
    result = command([SYSTEMCTL_BIN, 'restart', 'sing-box-happ-server'], timeout=45)
    if result.returncode != 0 or service_state('sing-box-happ-server') != 'active':
        raise RuntimeError('HAPP Server не запустился с новой конфигурацией.')


def apply_happ_configuration(raw):
    payload = parse_json_object(raw, 'HAPP Server')
    if HAPP_USERS is not None:
        HAPP_USERS.require_vip(payload)
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    check_happ_candidate(data)
    backup = backup_file(HAPP_CONFIG_PATH, 'happ-server-config')
    write_atomic_file(HAPP_CONFIG_PATH, data, mode=0o640, group_name='sing-box')
    try:
        restart_happ_server()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        write_atomic_file(HAPP_CONFIG_PATH, backup.read_bytes(), mode=0o640, group_name='sing-box')
        restart_happ_server()
        raise RuntimeError('Новая HAPP Server конфигурация не запустилась; предыдущая версия восстановлена.')


def generate_and_apply_happ_keys(server, sni, port):
    if HAPP_USERS is None:
        raise RuntimeError('Управление пользователями HAPP недоступно.')
    server = validate_server(str(server), 'Адрес HAPP')
    sni = validate_server(str(sni), 'HAPP SNI')
    port = require_port(str(port))
    original_config = json.loads(HAPP_CONFIG_PATH.read_text(encoding='utf-8'))
    HAPP_USERS.require_vip(original_config)
    vip = HAPP_USERS.vip()
    state = json.loads(HAPP_STATE_PATH.read_text(encoding='utf-8'))
    generated = command([SING_BOX_BIN, 'generate', 'reality-keypair'], timeout=30)
    private_match = re.search(r'^PrivateKey:\s*(\S+)', generated.stdout or '', re.MULTILINE)
    public_match = re.search(r'^PublicKey:\s*(\S+)', generated.stdout or '', re.MULTILINE)
    if generated.returncode != 0 or not private_match or not public_match:
        raise RuntimeError('Не удалось сгенерировать Reality-ключи.')
    private_key, public_key = private_match.group(1), public_match.group(1)
    if not PUBLIC_KEY_PATTERN.fullmatch(private_key) or not PUBLIC_KEY_PATTERN.fullmatch(public_key):
        raise RuntimeError('sing-box вернул некорректную пару Reality-ключей.')
    short_id = secrets.token_hex(4)
    candidate = json.loads(json.dumps(original_config))
    inbound = next(item for item in candidate['inbounds'] if item.get('tag') == vip['inbound_tag'])
    inbound['listen_port'] = port
    tls = inbound.setdefault('tls', {})
    tls.update(enabled=True, server_name=sni)
    reality = tls.setdefault('reality', {})
    reality.update(enabled=True, private_key=private_key, short_id=[short_id])
    reality['handshake'] = {'server': sni, 'server_port': 443}
    outbounds = candidate.get('outbounds', [])
    if len(outbounds) == 1 and outbounds[0].get('server') == '<provider-host>' and outbounds[0].get('uuid') == '<provider-vless-uuid>':
        candidate = synchronized_happ_config(load_config(), candidate, gateway_mode())
    parsed = urlsplit(vip['link'])
    vip_uuid = parsed.username
    if vip_uuid == '00000000-0000-4000-8000-000000000001':
        vip_uuid = str(uuid.uuid4())
        for credential in inbound.get('users', []):
            if credential.get('uuid') == parsed.username:
                credential['uuid'] = vip_uuid
        for credential in vip['users']:
            if credential.get('uuid') == parsed.username:
                credential['uuid'] = vip_uuid
        for candidate_inbound in candidate.get('inbounds', []):
            if candidate_inbound.get('tag') == vip['inbound_tag']:
                for credential in candidate_inbound.get('users', []):
                    if credential.get('uuid') == parsed.username:
                        credential['uuid'] = vip_uuid
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(security='reality', sni=sni, pbk=public_key, sid=short_id)
    host = f'[{server}]' if ':' in server else server
    link = urlunsplit(('vless', f'{vip_uuid}@{host}:{port}', '', urlencode(query, safe='-_'), parsed.fragment))
    state.update(server=server, port=port, sni=sni, uuid=vip_uuid, public_key=public_key, short_id=short_id, link=link)
    state.pop('local_link', None)
    vip.update(link=link, inbound_fields={key: inbound.get(key) for key in ('listen', 'listen_port', 'tls', 'transport')})
    config_data = (json.dumps(candidate, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    check_happ_candidate(config_data)
    paths = [(HAPP_CONFIG_PATH, 'happ-server-config', 0o640, 'sing-box'), (HAPP_STATE_PATH, 'happ-server-state', 0o600, None), (HAPP_USERS.vip_path, 'happ-vip', 0o600, None)]
    backups = [(path, backup_file(path, prefix), mode, group) for path, prefix, mode, group in paths]
    was_active = service_state('sing-box-happ-server') == 'active'
    was_enabled = command([SYSTEMCTL_BIN, 'is-enabled', 'sing-box-happ-server'], timeout=10).returncode == 0
    try:
        write_atomic_file(HAPP_CONFIG_PATH, config_data, mode=0o640, group_name='sing-box')
        write_atomic_file(HAPP_STATE_PATH, (json.dumps(state, indent=2, ensure_ascii=False) + '\n').encode('utf-8'), mode=0o600)
        write_atomic_file(HAPP_USERS.vip_path, (json.dumps(vip, indent=2, ensure_ascii=False) + '\n').encode('utf-8'), mode=0o600)
        restart_happ_server()
        if command([SYSTEMCTL_BIN, 'enable', 'sing-box-happ-server'], timeout=30).returncode != 0:
            raise RuntimeError('Не удалось включить автозапуск HAPP Server.')
        if command([SYSTEMCTL_BIN, 'enable', 'sing-box-happ-server'], timeout=30).returncode != 0:
            raise RuntimeError('Не удалось включить автозапуск HAPP Server.')
    except (RuntimeError, OSError, subprocess.SubprocessError):
        for path, backup, mode, group in backups:
            write_atomic_file(path, backup.read_bytes(), mode=mode, group_name=group)
        if not was_enabled:
            command([SYSTEMCTL_BIN, 'disable', 'sing-box-happ-server'], timeout=30)
        if was_active:
            restart_happ_server()
        else:
            command([SYSTEMCTL_BIN, 'stop', 'sing-box-happ-server'], timeout=30)
        raise RuntimeError('Настройка HAPP не применена; конфигурация и ссылки восстановлены.')


def happ_setup_needed():
    try:
        config = json.loads(HAPP_CONFIG_PATH.read_text(encoding='utf-8'))
        state = json.loads(HAPP_STATE_PATH.read_text(encoding='utf-8'))
        inbound = next(item for item in config.get('inbounds', []) if item.get('type') == 'vless')
        private_key = inbound.get('tls', {}).get('reality', {}).get('private_key', '')
        return not (PUBLIC_KEY_PATTERN.fullmatch(str(private_key)) and PUBLIC_KEY_PATTERN.fullmatch(str(state.get('public_key', ''))))
    except (OSError, ValueError, StopIteration, TypeError, AttributeError):
        return True


def happ_key_form(server='', sni='www.cloudflare.com', port=9445, initial=False):
    if '<' in str(server):
        server = ''
    if '<' in str(sni) or sni == 'localhost':
        sni = 'www.cloudflare.com'
    label = 'Сгенерировать и запустить HAPP' if initial else 'Сгенерировать и применить ключи'
    return f'''<form method="post" action="/settings/happ/keys"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="form-grid"><div class="field"><label>Адрес HAPP-сервера<input name="happ_server" value="{esc(server)}" required></label></div><div class="field"><label>Порт<input name="happ_port" type="number" min="1" max="65535" value="{esc(port)}" required></label></div><div class="field full"><label>Reality SNI<input name="happ_sni" value="{esc(sni)}" required></label></div></div><label><input class="inline-checkbox" name="confirm_happ_keys" type="checkbox" value="generate" required> Применить ключи ко всем ссылкам и запустить HAPP. Старые ссылки потребуют обновления.</label><div class="actions">{'<button class="secondary" type="button" data-happ-setup-close>Позже</button>' if initial else ''}<button type="submit">{label}</button></div></form>'''


def add_happ_setup_dialog(content, request_host=''):
    if 'class="app-shell"' not in content or not happ_setup_needed():
        return content
    try:
        state = json.loads(HAPP_STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    server = str(state.get('server', ''))
    if not server or '<' in server:
        server = urlsplit('//' + request_host).hostname or ''
    sni = str(state.get('sni', 'www.cloudflare.com'))
    if not sni or '<' in sni or sni == 'localhost':
        sni = 'www.cloudflare.com'
    modal = f'<dialog class="gateway-dialog" data-happ-setup-dialog aria-labelledby="happ-setup-title"><h2 id="happ-setup-title">Первоначальная настройка HAPP</h2>{happ_key_form(server, sni, state.get("port", 9445), initial=True)}</dialog>'
    return content.replace('</main>', modal + '</main>', 1)


def resolve_happ_subscription_base_url(state=None):
    state = load_happ_state() if state is None else state
    explicit = state.get('subscription_base_url') or HAPP_SUBSCRIPTION_BASE_URL
    if explicit:
        return validate_subscription_base_url(str(explicit))
    host = validate_server(str(state.get('server') or urlsplit(state.get('link', '')).hostname or ''), 'Public HAPP server')
    if ':' in host:
        host = '[' + host + ']'
    return validate_subscription_base_url(f'https://{host}:7445')


def save_happ_subscription_base_url(value):
    value = str(value or '').strip()
    if value:
        value = validate_subscription_base_url(value)
    state = load_happ_state()
    if value:
        state['subscription_base_url'] = value
    else:
        state.pop('subscription_base_url', None)
    resolve_happ_subscription_base_url(state)
    backup_file(HAPP_STATE_PATH, 'happ-subscription-origin')
    write_atomic_file(HAPP_STATE_PATH, (json.dumps(state, indent=2, ensure_ascii=False) + '\n').encode('utf-8'), mode=0o600)


def save_happ_subscription_content(title, announcement):
    title, announcement = validate_subscription_content(title, announcement)
    state = load_happ_state()
    state.update(subscription_title=title, subscription_announcement=announcement)
    backup_file(HAPP_STATE_PATH, 'happ-subscription-content')
    write_atomic_file(HAPP_STATE_PATH, (json.dumps(state, indent=2, ensure_ascii=False) + '\n').encode('utf-8'), mode=0o600)


def apply_happ_state(raw):
    payload = parse_json_object(raw, 'HAPP Public link')
    resolve_happ_subscription_base_url(payload)
    server = validate_server(str(payload.get('server', '')), 'Public server')
    port = require_port(str(payload.get('port', '')))
    try:
        state_uuid = str(uuid.UUID(str(payload.get('uuid', ''))))
    except ValueError as error:
        raise ValueError('HAPP Public link: UUID некорректен.') from error
    sni = validate_server(str(payload.get('sni', '')), 'Public SNI')
    public_key = str(payload.get('public_key', ''))
    short_id = str(payload.get('short_id', '')).lower()
    link = str(payload.get('link', ''))
    if not PUBLIC_KEY_PATTERN.fullmatch(public_key):
        raise ValueError('HAPP Public link: Reality public key некорректен.')
    if not SHORT_ID_PATTERN.fullmatch(short_id):
        raise ValueError('HAPP Public link: Reality short ID некорректен.')
    if not link.startswith('vless://'):
        raise ValueError('HAPP Public link: ожидается ссылка vless://.')
    parsed_link = urlsplit(link)
    if parsed_link.scheme != 'vless':
        raise ValueError('HAPP Public link: ожидается ссылка vless://.')
    query = dict(parse_qsl(parsed_link.query, keep_blank_values=True))
    query.update({'sni': sni, 'pbk': public_key, 'sid': short_id})
    rebuilt_link = urlunsplit((
        'vless',
        f'{quote(state_uuid)}@{server}:{port}',
        '',
        urlencode(query, safe='-_'),
        parsed_link.fragment,
    ))
    if HAPP_USERS is not None and rebuilt_link != HAPP_USERS.vip()['link']:
        raise ValueError('Сохранённая VIP-ссылка защищена. Персональные ссылки создаются в разделе HAPP Server.')
    payload.update({
        'server': server,
        'port': port,
        'uuid': state_uuid,
        'sni': sni,
        'public_key': public_key,
        'short_id': short_id,
        'link': rebuilt_link,
    })
    payload.pop('local_link', None)
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    backup_file(HAPP_STATE_PATH, 'happ-server-state')
    write_atomic_file(HAPP_STATE_PATH, data, mode=0o600)


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
    all_servers = managed_server_outbounds(config)
    if not all_profiles:
        raise RuntimeError('VLESS-профили не найдены.')
    selected = next((item for item in all_profiles if item.get('tag') == selected_tag), all_profiles[0])
    values = profile_values(selected)
    state = dashboard_state()
    current_route = config.get('route', {}).get('final', '')
    sidebar = []
    for item in all_servers:
        tag = item.get('tag', '')
        active = ' active' if tag == values['tag'] else ''
        href = f'/?profile={esc(tag)}' if item.get('type') == 'vless' else f'/outbounds?tag={esc(tag)}'
        sidebar.append(f'<a class="profile-link{active}" href="{href}"><span class="profile-dot"></span><span>{esc(tag)}</span></a>')
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
<title>{esc(service_title())}</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-icon" href="/favicon.png">
<link rel="stylesheet" href="/panel.css?v=2.1.7">
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
    <div><div class="label">Управление</div><nav class="profile-list"><a class="profile-link" data-panel-nav="outbounds" href="/outbounds"><span class="profile-dot"></span><span>VPN-серверы</span></a><a class="profile-link" data-panel-nav="wireguard" href="/wireguard"><span class="profile-dot"></span><span>WireGuard</span></a><a class="profile-link" data-panel-nav="happ-routing" href="/happ-routing"><span class="profile-dot"></span><span>HAPP Direct</span></a><a class="profile-link" data-panel-nav="happ-server" href="/happ-server"><span class="profile-dot"></span><span>HAPP Server</span></a></nav></div>
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
<script src="/panel.js?v=2.1.7" defer></script>
<script src="/happ-actions.js" defer></script>
</body>
</html>'''


def render_vless_profile_form(config, selected_tag, action, submit_label):
        selected = profile_by_tag(config, selected_tag)
        values = profile_values(selected)
        fingerprint_options = select_options(FINGERPRINTS, values['fingerprint'])
        flow_options = select_options(FLOWS, values['flow'], 'без Vision')
        transport_options = select_options(TRANSPORTS, values['transport'])
        return f'''<form method="post" action="{esc(action)}" autocomplete="off">
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
    <div class="actions"><button type="submit">{esc(submit_label)}</button></div>
</form>'''


def render_vless_page(config, selected_tag, message='', kind='success'):
        all_profiles = profiles(config)
        if not all_profiles:
                raise RuntimeError('VLESS-профили не найдены.')
        selected = next((item for item in all_profiles if item.get('tag') == selected_tag), all_profiles[0])
        tag = selected.get('tag', '')
        state = dashboard_state()
        current_route = config.get('route', {}).get('final', '')
        route_options = []
        for outbound in selectable_outbounds(config):
                outbound_tag = outbound['tag']
                label = 'Авто: самый быстрый доступный' if outbound['type'] == 'urltest' else f'Профиль: {outbound_tag}'
                selected_attribute = ' selected' if outbound_tag == current_route else ''
                route_options.append(f'<option value="{esc(outbound_tag)}"{selected_attribute}>{esc(label)}</option>')
        status_class = 'ok' if state['sing_box'] == 'active' and state['gateway'] == 'active' else ''
        body = f'''<section class="page-head">
    <div><p class="eyebrow">Outbound tunnel</p><h1>VLESS</h1><p class="subtitle">Профиль {esc(tag)} и исходящий маршрут шлюза.</p></div>
    <div class="status"><span class="status-dot {status_class}"></span>sing-box: {esc(state['sing_box'])}</div>
</section>
{message_banner(message, kind)}
<div class="panel-grid">
    <section class="panel"><h2>{esc(tag)}</h2>{render_vless_profile_form(config, tag, '/vless/profile', 'Сохранить и применить')}</section>
    <aside class="panel-stack">
        <section class="panel"><h2>Состояние</h2><div class="metrics"><div class="metric"><span>VLESS-профилей</span><strong>{state['profiles']}</strong></div><div class="metric"><span>Маршрут по умолчанию</span><strong>{esc(state['route'])}</strong></div><div class="metric"><span>TPROXY gateway</span><strong>{esc(state['gateway'])}</strong></div><div class="metric"><span>Изменено</span><strong>{esc(state['modified'])}</strong></div></div><form method="post" action="/vless/restart"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="actions"><button class="secondary" type="submit">Перезапустить sing-box</button></div></form></section>
        <section class="panel"><h2>Исходящий профиль</h2><form method="post" action="/vless/route"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="route_final">Маршрут по умолчанию</label><select id="route_final" name="route_final">{''.join(route_options)}</select></div><div class="actions"><button type="submit">Применить профиль</button></div></form></section>
    </aside>
</div>'''
        return render_shell('VLESS', body, 'vless', [item.get('tag', '') for item in managed_server_outbounds(config)], tag)


def render_settings_page(config, query, message='', kind='success'):
        all_profiles = managed_server_outbounds(config)
        selected_tag = form_value(query, 'profile') or (all_profiles[0].get('tag', '') if all_profiles else '')
        if selected_tag not in {item.get('tag', '') for item in all_profiles}:
                selected_tag = all_profiles[0].get('tag', '') if all_profiles else ''
        route_options = []
        current_route = config.get('route', {}).get('final', '')
        for outbound in selectable_outbounds(config):
                outbound_tag = outbound['tag']
                label = 'Авто: самый быстрый доступный' if outbound['type'] == 'urltest' else f'Профиль: {outbound_tag}'
                route_options.append(f'<option value="{esc(outbound_tag)}{" selected" if outbound_tag == current_route else ""}>{esc(label)}</option>')
        profile_options = ''.join(f'<option value="{esc(item.get("tag", ""))}{" selected" if item.get("tag", "") == selected_tag else ""}>{esc(item.get("tag", ""))}</option>' for item in all_profiles)
        try:
                wg_general = json.dumps(WG_ADMIN.api.general(), indent=2, ensure_ascii=False)
                wg_interface = json.dumps(WG_ADMIN.api.interface(), indent=2, ensure_ascii=False)
                wg_error = ''
        except WgEasyApiError as error:
                wg_general = '{}'
                wg_interface = '{}'
                wg_error = message_banner(f'WireGuard API: {error}', 'error')
        try:
                happ_config = json_text(HAPP_CONFIG_PATH)
                happ_state_payload = load_happ_state()
                happ_state_payload.pop('local_link', None)
                happ_state = json.dumps(happ_state_payload, indent=2, ensure_ascii=False)
                happ_subscription_setting = str(happ_state_payload.get('subscription_base_url', ''))
                happ_subscription_title = str(happ_state_payload.get('subscription_title', DEFAULT_SUBSCRIPTION_TITLE))
                happ_subscription_announcement = str(happ_state_payload.get('subscription_announcement', SUBSCRIPTION_ANNOUNCEMENT))
                happ_subscription_origin = resolve_happ_subscription_base_url(happ_state_payload)
                happ_error = ''
        except (OSError, ValueError) as error:
                happ_config = '{}'
                happ_state = '{}'
                happ_state_payload = {}
                happ_subscription_setting = ''
                happ_subscription_title = DEFAULT_SUBSCRIPTION_TITLE
                happ_subscription_announcement = SUBSCRIPTION_ANNOUNCEMENT
                happ_subscription_origin = ''
                happ_error = message_banner(f'HAPP Server: {error}', 'error')
        service_control = load_service_control()
        active_gateway_mode = gateway_mode()
        client_configured = WIREGUARD_CLIENT_CONFIG_PATH.is_file()
        wireguard_client_text = load_wireguard_client_text()
        vip_link = HAPP_USERS.vip()['link'] if HAPP_USERS is not None else public_vless_link()
        vip_link = vless_link_for_subscription(vip_link, happ_subscription_origin)
        monitor_settings = load_monitor_settings(APP_DIR)
        monitor_state = VLESS_MONITOR.status() if VLESS_MONITOR is not None else {}
        monitor_state = {**monitor_state, 'last_checked_at': format_datetime(monitor_state.get('last_checked_at'), 'ещё не выполнялась')}
        history_days = HAPP_HISTORY.retention_days() if HAPP_HISTORY is not None else 60
        service_states = {
            'wireguard': wireguard_state(),
            'vless': service_state('sing-box'),
            'happ': service_state('sing-box-happ-server'),
        }
        status_class = lambda value: 'ok' if value in ('active', 'running') else 'bad'
        try:
            server_metrics_markup = render_metrics_panel(SERVER_METRICS.snapshot() if SERVER_METRICS is not None else {})
        except (OSError, sqlite3.Error, ValueError):
            server_metrics_markup = render_metrics_panel({})
        gateway_mode_labels = {'vless': 'VLESS', 'wireguard': 'Внешний WireGuard', 'default': 'Шлюз по умолчанию'}
        gateway_mode_panel = f'''<section class="panel gateway-mode-panel"><h2>Режим работы VPN-шлюза</h2><p class="subtitle">Активный режим: <span class="badge {'ok' if active_gateway_mode == 'vless' else 'online'}">{esc(gateway_mode_labels[active_gateway_mode])}</span></p><div class="gateway-mode-actions">
        <form method="post" action="/settings/gateway/mode" data-gateway-mode="vless"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="mode" value="vless"><button class="{'secondary' if active_gateway_mode != 'vless' else 'mode-active'}" type="submit"{' disabled' if active_gateway_mode == 'vless' else ''}>VLESS-шлюз</button></form>
        <form method="post" action="/settings/gateway/mode" data-gateway-mode="default"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="mode" value="default"><button class="{'secondary' if active_gateway_mode != 'default' else 'mode-active'}" type="submit"{' disabled' if active_gateway_mode == 'default' else ''}>Шлюз по умолчанию</button></form>
        <form method="post" action="/settings/gateway/mode" data-gateway-mode="wireguard"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="mode" value="wireguard"><button class="{'secondary' if active_gateway_mode != 'wireguard' else 'mode-active'}" type="submit"{' disabled' if active_gateway_mode == 'wireguard' or not client_configured else ''}>Внешний WireGuard{' · активен' if active_gateway_mode == 'wireguard' else ''}</button></form>
    </div><p class="muted">«Шлюз по умолчанию» отключает VLESS/TPROXY и внешний WireGuard: интернет-трафик клиентов WireGuard идёт через основной шлюз сервера. Локальные сети маршрутизируются напрямую; индивидуальные LAN-запреты сохраняются. Основной маршрут сервера не меняется.</p></section>'''
        body = f'''<section class="page-head">
    <div><p class="eyebrow">Service control</p><h1>Настройки</h1><p class="subtitle">VLESS, WireGuard, HAPP Server и доступ к панели.</p></div>
</section>
{server_metrics_markup}
{message_banner(message, kind)}
{wg_error}{happ_error}
<div class="settings-layout">
    {gateway_mode_panel}
    <section class="panel"><h2>Публичный URL подписки HAPP</h2><form method="post" action="/settings/happ/subscription"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="happ_subscription_base_url">WAN / DNS URL · пусто = публичный адрес HAPP</label><input id="happ_subscription_base_url" name="subscription_base_url" type="url" value="{esc(happ_subscription_setting)}" placeholder="{esc(happ_subscription_origin)}"></div><p class="muted">Текущий адрес: {esc(happ_subscription_origin)}. Внешний порт должен быть доступен клиенту; HTTPS задаётся только для настроенного TLS endpoint.</p><div class="actions"><button type="submit">Сохранить URL подписки</button></div></form></section>
    <section class="panel"><h2>Заголовок и объявление HAPP</h2><form method="post" action="/settings/happ/announcement"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="happ_subscription_title">Заголовок сервера</label><input id="happ_subscription_title" name="subscription_title" maxlength="80" value="{esc(happ_subscription_title)}" required></div><div class="field"><label for="happ_subscription_announcement">Текст объявления</label><textarea id="happ_subscription_announcement" name="subscription_announcement" rows="5" maxlength="2000">{esc(happ_subscription_announcement)}</textarea></div><p class="muted">Заголовок отображается перед именем пользователя; итоговое имя HAPP ограничено 25 символами. Объявление обновится при следующем обновлении подписки; строка «Скачано» формируется автоматически.</p><div class="actions"><button type="submit">Сохранить блок HAPP</button></div></form></section>
    <section class="panel"><h2>Хранение статистики HAPP</h2><form method="post" action="/settings/happ-history"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="happ_retention_days">Хранить дней · старые записи очищаются автоматически</label><input id="happ_retention_days" name="retention_days" type="number" min="1" max="3650" step="1" value="{history_days}" required></div><div class="actions"><button type="submit">Сохранить срок хранения</button><a class="button secondary" href="/happ-history">История HAPP</a></div></form><p class="muted">База хранится в /mnt/stat/. По умолчанию 60 дней; уменьшение срока сразу удалит записи старше выбранного периода.</p></section>
    <section class="panel"><h2>VIP-ссылка HAPP</h2><div class="field"><label for="happ_vip_link">Ссылка подключения</label><textarea id="happ_vip_link" class="public-link-field" readonly spellcheck="false">{esc(vip_link)}</textarea></div></section>
    <section class="panel"><h2>Автопроверка VLESS</h2><form method="post" action="/settings/vless-monitor"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="form-grid"><div class="field"><label><input class="inline-checkbox" type="checkbox" name="monitor_enabled"{' checked' if monitor_settings['enabled'] else ''}> Проверять VLESS по расписанию</label></div><div class="field"><label for="monitor_interval_minutes">Интервал между циклами, минуты</label><input id="monitor_interval_minutes" name="interval_minutes" type="number" min="1" max="60" step="1" value="{monitor_settings['interval_minutes']}" required></div><div class="field full"><label><input class="inline-checkbox" type="checkbox" name="auto_switch"{' checked' if monitor_settings['auto_switch'] else ''}> Автовыбор: минимальная задержка в трёх циклах подряд</label></div><div class="field"><label><input class="inline-checkbox" type="checkbox" name="mattermost_enabled"{' checked' if monitor_settings['mattermost_enabled'] else ''}> Уведомлять Mattermost о смене шлюза</label></div><div class="field"><label for="mattermost_webhook">Webhook Mattermost{' · сохранён' if monitor_settings['webhook_url'] else ''}</label><input id="mattermost_webhook" name="webhook_url" type="password" autocomplete="new-password" placeholder="{'Оставьте пустым для сохранения webhook' if monitor_settings['webhook_url'] else 'https://mattermost.example/hooks/...'}"></div><div class="field full"><label><input class="inline-checkbox" type="checkbox" name="clear_webhook"> Удалить сохранённый webhook</label></div></div><div class="actions"><button type="submit">Сохранить автоматизацию</button><a class="button secondary" href="/gateway-journal">Журнал переключений</a></div></form><form method="post" action="/settings/vless-monitor/test-webhook"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="actions"><button class="secondary" type="submit"{' disabled' if not monitor_settings['webhook_url'] else ''}>Проверить webhook</button></div></form><p class="muted">Последняя проверка: {esc(monitor_state.get('last_checked_at', 'ещё не выполнялась'))}. Кандидат: {esc(monitor_state.get('candidate') or 'нет')} · {monitor_state.get('streak', 0)}/3.</p></section>
    <section class="panel"><h2>Клиент внешнего WireGuard</h2><p class="muted">Последняя сохранённая конфигурация показывается только в этой авторизованной панели. На диске исходный текст и рабочий конфиг хранятся с правами 0600.</p><form method="post" action="/settings/gateway/config"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="wireguard_client_config">Конфигурация клиента</label><textarea id="wireguard_client_config" name="wireguard_client_config" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="[Interface]&#10;PrivateKey = ...&#10;Address = 10.0.0.2/32&#10;&#10;[Peer]&#10;PublicKey = ...&#10;Endpoint = vpn.example.com:51820&#10;AllowedIPs = 0.0.0.0/0" required>{esc(wireguard_client_text)}</textarea></div><div class="actions"><button type="submit">{'Обновить конфигурацию' if client_configured else 'Сохранить конфигурацию'}</button><span class="service-status">{'Конфигурация сохранена' if client_configured else 'Конфигурация ещё не задана'}</span></div></form></section>
    <dialog class="gateway-dialog" data-gateway-dialog aria-labelledby="gateway-dialog-title"><form method="dialog"><h2 id="gateway-dialog-title" data-gateway-dialog-title>Сменить шлюз?</h2><p data-gateway-dialog-message></p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button type="button" data-gateway-dialog-confirm>Переключить</button></div></form></dialog>
    <section class="panel service-control-panel"><h2>Управление сервисами</h2><p class="subtitle">При остановке WireGuard-моста маршрут сервера переключается на LAN-шлюз.</p><div class="service-control-grid">
        <div class="service-control-item"><h3>WireGuard</h3><p class="service-status">Состояние: <span class="badge {status_class(service_states['wireguard'])}">{esc(service_states['wireguard'])}</span></p><form method="post" action="/settings/wireguard/gateway"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="wireguard_fallback_gateway">Шлюз при остановке WireGuard</label><input id="wireguard_fallback_gateway" name="wireguard_fallback_gateway" value="{esc(service_control['wireguard_fallback_gateway'])}" inputmode="decimal" required></div><div class="actions"><button class="secondary" type="submit">Сохранить шлюз</button></div></form><form method="post" action="/settings/service/wireguard"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="inline-actions"><button class="secondary" name="operation" value="start" type="submit">Запустить</button><button class="secondary" name="operation" value="restart" type="submit">Перезапустить</button><button class="danger" name="operation" value="stop" type="submit">Остановить</button></div></form></div>
        <div class="service-control-item"><h3>VLESS</h3><p class="service-status">Состояние: <span class="badge {status_class(service_states['vless'])}">{esc(service_states['vless'])}</span></p><p>Состояние определяется выбранным режимом шлюза выше.</p></div>
        <div class="service-control-item"><h3>HAPP Server</h3><p class="service-status">Состояние: <span class="badge {status_class(service_states['happ'])}">{esc(service_states['happ'])}</span></p><form method="post" action="/settings/service/happ"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="inline-actions"><button class="secondary" name="operation" value="start" type="submit">Запустить</button><button class="secondary" name="operation" value="restart" type="submit">Перезапустить</button><button class="danger" name="operation" value="stop" type="submit">Остановить</button></div></form></div>
        <div class="service-control-item"><h3>Сервер</h3><p class="service-status">Перезагрузка отключит панель и сервисы на короткое время.</p><form method="post" action="/settings/system/reboot" autocomplete="off"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="reboot_confirmation">Введите REBOOT для подтверждения</label><input id="reboot_confirmation" name="reboot_confirmation" pattern="REBOOT" required></div><div class="actions"><button class="danger" type="submit">Перезагрузить сервер</button></div></form></div>
    </div></section>
    <section class="panel"><h2>WireGuard</h2><div class="settings-grid"><form method="post" action="/settings/wireguard/general"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="general_json">General JSON</label><textarea id="general_json" name="general_json" spellcheck="false">{esc(wg_general)}</textarea></div><div class="actions"><button type="submit">Сохранить General</button></div></form><form method="post" action="/settings/wireguard/interface"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="interface_json">Interface JSON</label><textarea id="interface_json" name="interface_json" spellcheck="false">{esc(wg_interface)}</textarea></div><div class="actions"><button type="submit">Сохранить Interface</button></div></form></div><form method="post" action="/settings/wireguard/restart"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="actions"><button class="secondary" type="submit">Перезапустить интерфейс WireGuard</button></div></form></section>
    <section class="panel"><h2>Reality-ключи HAPP</h2><div class="form-grid"><div class="field"><label for="happ_vip_uuid">UUID VIP</label><input id="happ_vip_uuid" value="{esc(happ_state_payload.get('uuid', ''))}" readonly></div><div class="field"><label for="happ_public_key">Публичный Reality-ключ</label><input id="happ_public_key" value="{esc(happ_state_payload.get('public_key', '')) if PUBLIC_KEY_PATTERN.fullmatch(str(happ_state_payload.get('public_key', ''))) else ''}" readonly placeholder="Не сгенерирован"></div></div>{happ_key_form(happ_state_payload.get('server', ''), happ_state_payload.get('sni', 'www.cloudflare.com'), happ_state_payload.get('port', 9445))}</section>
    <section class="panel"><h2>HAPP Server</h2><div class="settings-grid"><form method="post" action="/settings/happ/state"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="happ_state_json">JSON публичной ссылки</label><textarea id="happ_state_json" name="happ_state_json" spellcheck="false">{esc(happ_state)}</textarea></div><div class="actions"><button type="submit">Сохранить публичную ссылку</button></div></form><form method="post" action="/settings/happ/config"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="field"><label for="happ_config_json">sing-box HAPP Server JSON</label><textarea id="happ_config_json" name="happ_config_json" spellcheck="false">{esc(happ_config)}</textarea></div><div class="actions"><button class="danger" type="submit">Проверить и применить HAPP config</button></div></form></div><form method="post" action="/settings/happ/restart"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="actions"><button class="secondary" type="submit">Перезапустить HAPP Server</button></div></form></section>
    <section class="panel"><h2>Доступ к панели</h2><form method="post" action="/settings/password" autocomplete="new-password"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="form-grid"><div class="field"><label for="new_password">Новый пароль</label><input id="new_password" name="new_password" type="password" minlength="12" required></div><div class="field"><label for="confirm_password">Повторите пароль</label><input id="confirm_password" name="confirm_password" type="password" minlength="12" required></div></div><div class="actions"><button class="danger" type="submit">Обновить пароль</button></div></form></section>
</div>'''
        return render_shell('Настройки', body, 'settings', [item.get('tag', '') for item in managed_server_outbounds(config)], selected_tag)


def check_summary_notice(checks, tags, prefix=''):
    selected = [checks[tag] for tag in tags if tag in checks]
    pending = sum(item['state'] in ('running', 'queued') for item in selected)
    finished = sum(item['state'] in ('success', 'error') for item in selected)
    if pending:
        text = f'Проверено {finished}/{len(selected)}. Остальные проверки выполняются в фоне.'
        style = ''
    elif len(selected) == 1:
        text = f'{selected[0]["tag"]}: {selected[0]["message"]}'
        style = 'success' if selected[0]['state'] == 'success' else 'error'
    else:
        failed = [item['tag'] for item in selected if item['state'] != 'success']
        text = f'Проверки завершены: {finished}/{len(selected)}.'
        if failed:
            text += ' Ошибка или нет результата: ' + ', '.join(failed) + '.'
        style = 'error' if failed else 'success'
    return f'<div class="notice {style}" data-outbound-summary data-check-tags="{esc(json.dumps(tags))}" data-check-prefix="{esc(prefix)}" role="status">{esc(prefix + text)}</div>'


def render_outbounds_page(config, query, message='', kind='success'):
    servers = managed_server_outbounds(config)
    current_route = config.get('route', {}).get('final', '')
    sing_box_state = service_state('sing-box')
    status_class = 'ok' if sing_box_state == 'active' else ''
    server_options = ''.join(f'<option value="{esc(item["tag"])}">{esc(item["tag"])} · {esc(item["type"])}</option>' for item in servers)
    checks = {item['tag']: item for item in outbound_check_states(config)['checks']}
    tracked = [tag for tag in form_value(query, 'checks').split(',') if tag in checks]
    if not tracked:
        tracked = [tag for tag in checks if message == f'{tag}: проверка соединения запущена в фоне.']
    notice = check_summary_notice(checks, tracked, '' if len(tracked) == 1 else message + ' ') if tracked else message_banner(message, kind)
    rows = []
    for outbound in servers:
        tag = outbound['tag']
        route_indicator = (
            '<span class="status-dot route-active" role="img" aria-label="Маршрут по умолчанию" title="Маршрут по умолчанию"></span>'
            if current_route == tag else ''
        )
        latency = checks[tag].get('latency_ms')
        latency_text = f'{latency:.2f}' if isinstance(latency, (int, float)) else '—'
        failed_class = ' class="outbound-failed"' if checks[tag]['state'] == 'error' else ''
        rows.append(
            f'<tr{failed_class}>'
            f'<td><strong>{esc(tag)}</strong></td>'
            f'<td>{esc(outbound["type"])}</td>'
            f'<td>{esc(outbound.get("server", ""))}</td>'
            f'<td>{esc(outbound.get("server_port", ""))}</td>'
            f'<td>{route_indicator}</td>'
            f'<td data-outbound-check-tag="{esc(tag)}" role="status">{esc(checks[tag]["message"])}</td>'
            f'<td data-outbound-latency>{latency_text}</td>'
            f'<td data-outbound-checked-at>{esc(format_datetime(checks[tag].get("checked_at")))}</td>'
            f'<td><div class="outbound-actions"><form method="post" action="/outbounds/route"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="tag" value="{esc(tag)}"><button class="secondary" type="submit"{ " disabled" if current_route == tag else ""}>Использовать</button></form>'
            f'<form method="post" action="/outbounds/check"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="tag" value="{esc(tag)}"><button class="secondary" type="submit">Проверить</button></form><button class="secondary" type="button" data-outbound-edit="{esc(json.dumps(outbound, ensure_ascii=False))}">Редактировать</button>'
            f'<form method="post" action="/outbounds/delete" data-outbound-delete><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="tag" value="{esc(tag)}"><button class="danger" type="submit"{ " disabled" if len(servers) <= 1 else ""}>Удалить</button></form></div></td>'
            '</tr>'
        )
    if not rows:
        rows.append('<tr><td class="empty" colspan="9">Нет импортированных серверов.</td></tr>')
    body = f'''<section class="page-head"><div><p class="eyebrow">Outbound manager</p><h1>VPN-серверы</h1></div><div class="status" role="status"><span class="status-dot {status_class}"></span>sing-box: {esc(sing_box_state)}</div></section>
{notice}
<div class="panel-stack">
    <section class="panel" data-outbound-checks><h2>Настроенные серверы</h2><div class="table-wrap"><table><thead><tr><th>Tag</th><th>Тип</th><th>Сервер</th><th>Порт</th><th>Маршрут</th><th>Проверка</th><th>Пинг, мс</th><th>Проверен UTC</th><th>Действия</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p class="muted">Текущий маршрут: <strong>{esc(current_route)}</strong></p></section>
    <section class="panel"><h2>Импорт JSON</h2><form method="post" action="/outbounds/import"><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><div class="form-grid"><div class="field"><label for="import_replace_tag">Заменить один существующий сервер</label><select id="import_replace_tag" name="replace_tag"><option value="">Добавить новые auto-N</option>{server_options}</select></div><div class="field"><label for="import_tag">Tag для одного профиля</label><input id="import_tag" name="import_tag" placeholder="Для массива оставьте пустым"></div><div class="field full"><label for="outbound_json">JSON sing-box / Xray: объект или массив конфигураций</label><textarea id="outbound_json" name="outbound_json" spellcheck="false" required placeholder="Вставьте JSON VPN-профиля или массив профилей"></textarea></div></div><div class="actions"><button type="submit">Проверить и импортировать</button></div></form></section>
</div><dialog class="gateway-dialog" data-outbound-edit-dialog aria-labelledby="outbound-edit-title"><form method="post" action="/outbounds/import" data-outbound-edit-form><h2 id="outbound-edit-title">Редактировать VPN-сервер</h2><input type="hidden" name="csrf" value="{esc(CSRF_TOKEN)}"><input type="hidden" name="replace_tag" data-outbound-edit-tag><div class="field"><label for="outbound_edit_json">Конфигурация сервера</label><textarea id="outbound_edit_json" name="outbound_json" spellcheck="false" required data-outbound-edit-json></textarea></div><div class="actions"><button class="secondary" type="button" data-outbound-edit-cancel>Отмена</button><button type="submit">Сохранить</button></div></form></dialog><dialog class="gateway-dialog" data-outbound-delete-dialog><form method="dialog"><h2>Удалить VPN-сервер?</h2><p data-outbound-delete-message></p><div class="actions"><button class="secondary" value="cancel">Отмена</button><button type="button" data-outbound-delete-confirm>Удалить</button></div></form></dialog>'''
    return render_shell('VPN-серверы', body, 'outbounds', [item.get('tag', '') for item in servers], '')


class VpnOnlyServer(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = 'VLESSAdmin'
    sys_version = ''

    def log_message(self, format_string, *args):
        return

    def crm_authenticated(self):
        headers = getattr(self, 'headers', {})
        if headers.get('X-FocusVPN-CRM') == '1' and headers.get('Authorization', '').startswith('Basic '):
            return self.authenticated()
        if not os.environ.get('FOCUSVPN_CRM_TOKEN_FILE') or not os.environ.get('FOCUSVPN_CRM_NETWORKS'):
            return False
        return crm_authorized(self.headers, self.client_address[0])

    def send_header(self, keyword, value):
        if keyword.lower() == 'location' and self.crm_authenticated():
            value = crm_panel_url(value)
        super().send_header(keyword, value)

    def send_common_headers(self):
        embedded = self.crm_authenticated()
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'SAMEORIGIN' if embedded else 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        ancestors = "'self'" if embedded else "'none'"
        self.send_header('Content-Security-Policy', f"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors {ancestors}")

    def send_html(self, status, content):
        if status == HTTPStatus.OK:
            content = add_happ_setup_dialog(content, self.headers.get('Host', ''))
        if self.crm_authenticated():
            content = crm_embed(content)
        payload = content.encode('utf-8')
        self.send_response(status)
        self.send_common_headers()
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status, payload):
        content = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_common_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

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

    def send_favicon_svg(self):
        try:
            payload = FAVICON_SVG_PATH.read_bytes()
        except OSError:
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_common_headers()
        self.send_header('Content-Type', 'image/svg+xml; charset=utf-8')
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

    def send_panel_js(self, script_path=None):
        try:
            payload = (script_path or PANEL_JS_PATH).read_bytes()
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

    def send_happ_qr(self, user_id=None):
        try:
            if HAPP_USERS is None:
                self.send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            with HAPP_LOCK:
                subscriptions = HAPP_USERS.subscription_urls(resolve_happ_subscription_base_url())
                link = subscriptions.get('VIP' if user_id is None else user_id)
            if link is None:
                self.send_empty(HTTPStatus.NOT_FOUND)
                return
        except (OSError, ValueError, json.JSONDecodeError):
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        try:
            result = subprocess.run(
                [QR_ENCODE_BIN, '-t', 'SVG', '-o', '-', '-m', '2', '-s', '8'],
                input=link.encode('utf-8'),
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
            client_ip = self.client_ip_address()
            return any(client_ip in network for network in ACCESS_NETWORKS)
        except ValueError:
            return False

    def client_ip_address(self):
        peer_ip = ipaddress.ip_address(self.client_address[0])
        if any(peer_ip in network for network in TRUSTED_PROXY_NETWORKS):
            forwarded_ip = self.headers.get('X-Real-IP', '').strip()
            if forwarded_ip:
                try:
                    return ipaddress.ip_address(forwarded_ip)
                except ValueError:
                    pass
        return peer_ip

    def request_is_secure(self):
        try:
            peer_ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(peer_ip in network for network in TRUSTED_PROXY_NETWORKS) and self.headers.get('X-Forwarded-Proto', '').lower() == 'https'

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

    def session_token(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            morsel = cookies.get(SESSION_COOKIE_NAME)
            return morsel.value if morsel else ''
        except CookieError:
            return ''

    def session_authenticated(self):
        return valid_session(self.session_token(), str(self.client_ip_address()))

    def session_cookie(self, token, max_age, secure=False):
        secure_flag = '; Secure' if secure else ''
        return f'{SESSION_COOKIE_NAME}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict{secure_flag}'

    def send_login_challenge(self):
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_common_headers()
        self.send_header('WWW-Authenticate', f'Basic realm="{auth_realm()}", charset="UTF-8"')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def login(self):
        if not self.vpn_client_allowed():
            self.send_empty(HTTPStatus.FORBIDDEN)
            return
        if not self.authenticated():
            self.send_login_challenge()
            return
        FIRST_LOGIN_PATH.unlink(missing_ok=True)
        token = issue_session(str(self.client_ip_address()))
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        self.send_header('Set-Cookie', self.session_cookie(token, SESSION_TTL_SECONDS, self.request_is_secure()))
        self.send_header('Location', '/wireguard')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def logout(self):
        revoke_session(self.session_token())
        self.send_response(HTTPStatus.OK)
        self.send_common_headers()
        self.send_header('Set-Cookie', self.session_cookie('', 0, self.request_is_secure()))
        content = ('<!doctype html><meta charset="utf-8"><title>' + esc(service_title()) + '</title><p>Сессия завершена.</p><p><a href="/login">Войти</a></p>').encode('utf-8')
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def require_access(self):
        if not self.vpn_client_allowed():
            self.send_empty(HTTPStatus.FORBIDDEN)
            return False
        if not self.session_authenticated() and not self.crm_authenticated():
            self.redirect_to('/login')
            return False
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
        self.redirect_vless(profile, message, kind)

    def redirect_vless(self, profile='', message='', kind='success'):
        params = {'message': message, 'kind': kind}
        if profile:
            params['profile'] = profile
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        self.send_header('Location', '/vless?' + urlencode(params))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def redirect_settings(self, profile='', message='', kind='success'):
        params = {'message': message, 'kind': kind}
        if profile:
            params['profile'] = profile
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        self.send_header('Location', '/settings?' + urlencode(params))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def redirect_outbounds(self, message='', kind='success', checks=None):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        params = {'message': message, 'kind': kind}
        if checks:
            params['checks'] = ','.join(checks)
        self.send_header('Location', '/outbounds?' + urlencode(params))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def redirect_to(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_common_headers()
        self.send_header('Location', location)
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

    def send_happ_subscription(self, path):
        if not self.vpn_client_allowed():
            self.send_empty(HTTPStatus.FORBIDDEN)
            return
        parts = path.strip('/').split('/')
        if len(parts) != 2:
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        try:
            if HAPP_USERS is None or HAPP_HISTORY is None:
                self.send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            with HAPP_LOCK:
                user = HAPP_USERS.subscription_user(parts[1])
            if user is None:
                self.send_empty(HTTPStatus.NOT_FOUND)
                return
            user_key = 'VIP' if user['id'] == 'VIP' else 'personal-' + user['id']
            traffic = HAPP_HISTORY.user_totals().get(user_key, {})
            subscription_state = load_happ_state()
            subscription_origin = resolve_happ_subscription_base_url(subscription_state)
            user = {**user, 'link': vless_link_for_subscription(user['link'], subscription_origin)}
            content, headers = subscription_content(
                user,
                traffic,
                subscription_origin + '/happ-info',
                subscription_state.get('subscription_title', DEFAULT_SUBSCRIPTION_TITLE),
                subscription_state.get('subscription_announcement', SUBSCRIPTION_ANNOUNCEMENT),
            )
        except (ValueError, OSError, sqlite3.Error):
            self.send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.send_response(HTTPStatus.OK)
        self.send_common_headers()
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/happ-info':
            if self.vpn_client_allowed():
                try:
                    state = load_happ_state()
                    content = subscription_information_page(
                        state.get('subscription_title', DEFAULT_SUBSCRIPTION_TITLE),
                        state.get('subscription_announcement', SUBSCRIPTION_ANNOUNCEMENT),
                    )
                except (OSError, ValueError):
                    self.send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                self.send_html(HTTPStatus.OK, content)
            else:
                self.send_empty(HTTPStatus.FORBIDDEN)
            return
        if parsed.path.startswith('/happ-subscription/'):
            self.send_happ_subscription(parsed.path)
            return
        if parsed.path == '/login':
            self.login()
            return
        if parsed.path == '/logout':
            self.logout()
            return
        if parsed.path == '/favicon.png':
            self.send_favicon()
            return
        if parsed.path == '/favicon.svg':
            self.send_favicon_svg()
            return
        if parsed.path == '/panel.css':
            self.send_panel_css()
            return
        if parsed.path == '/panel.js':
            self.send_panel_js()
            return
        if parsed.path == '/chart.js':
            self.send_panel_js(CHART_JS_PATH)
            return
        if parsed.path == '/happ-actions.js':
            self.send_happ_actions_js()
            return
        if not self.require_access():
            return
        if parsed.path == '/happ-qr':
            self.send_happ_qr()
            return
        if parsed.path.startswith('/happ-users/'):
            parts = parsed.path.strip('/').split('/')
            if len(parts) == 3 and parts[2] == 'qr':
                self.send_happ_qr(parts[1])
            else:
                self.send_empty(HTTPStatus.NOT_FOUND)
            return
        if parsed.path == '/wireguard/live':
            try:
                self.send_json(HTTPStatus.OK, WG_ADMIN.live_state())
            except WgEasyApiError:
                self.send_json(HTTPStatus.BAD_GATEWAY, {'error': 'WireGuard временно недоступен.'})
            return
        if parsed.path == '/happ-server/live':
            try:
                seconds = form_value(parse_qs(parsed.query), 'seconds') or '5'
                activity = HAPP_HISTORY.activity(seconds) if HAPP_HISTORY is not None else None
                payload = happ_live_connections()
                payload['activity'] = activity
                payload['account_traffic'] = HAPP_HISTORY.user_totals() if HAPP_HISTORY is not None else {}
                if HAPP_HISTORY is not None:
                    payload['top_users'] = HAPP_HISTORY.top_users(payload.get('users', []))
                self.send_json(HTTPStatus.OK, payload)
            except ValueError as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {'error': str(error)})
            except HappStatsError as error:
                self.send_json(HTTPStatus.BAD_GATEWAY, {'error': str(error)})
            except (OSError, sqlite3.Error):
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'error': 'Статистика аккаунтов временно недоступна.'})
            return
        if parsed.path == '/happ-server/traffic':
            try:
                if HAPP_HISTORY is None:
                    raise RuntimeError('Хранилище статистики недоступно.')
                query = parse_qs(parsed.query)
                self.send_json(HTTPStatus.OK, HAPP_HISTORY.download_chart(form_value(query, 'minutes') or '10', direction=form_value(query, 'direction') or 'download'))
            except ValueError as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {'error': str(error)})
            except (RuntimeError, OSError, sqlite3.Error):
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'error': 'История скоростей временно недоступна.'})
            return
        if parsed.path in ('/happ-history', '/happ-history.xls'):
            try:
                if HAPP_HISTORY is None:
                    raise RuntimeError('Хранилище статистики недоступно.')
                query = parse_qs(parsed.query)
                if parsed.path.endswith('.xls'):
                    data = HAPP_HISTORY.export_xls(form_value(query, 'user'), form_value(query, 'since'), form_value(query, 'until'))
                    self.send_binary(data, 'application/vnd.ms-excel', 'attachment; filename="happ-statistics.xls"')
                else:
                    with HAPP_LOCK:
                        users = HAPP_USERS.registry()['users'] if HAPP_USERS is not None else []
                    self.send_html(HTTPStatus.OK, render_history(HAPP_HISTORY, query, form_value(query, 'message'), form_value(query, 'kind') or 'success', users))
            except ValueError:
                self.send_html(HTTPStatus.BAD_REQUEST, '<h1>Некорректные фильтры истории</h1><p>Проверьте даты и номер страницы.</p>')
            except (RuntimeError, OSError, sqlite3.Error, ImportError):
                self.send_html(HTTPStatus.SERVICE_UNAVAILABLE, '<h1>История HAPP временно недоступна</h1><p>Проверьте хранилище и зависимость XLS или повторите запрос.</p>')
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
            self.redirect_to('/happ-server')
            return
        if parsed.path == '/happ-server':
            query = parse_qs(parsed.query)
            with HAPP_LOCK:
                users = HAPP_USERS.users() if HAPP_USERS is not None else []
                events = HAPP_USERS.events() if HAPP_USERS is not None else []
                subscription_origin = resolve_happ_subscription_base_url()
                subscriptions = HAPP_USERS.subscription_urls(subscription_origin) if HAPP_USERS is not None else {}
                vip_vless_link = vless_link_for_subscription(HAPP_USERS.vip()['link'], subscription_origin) if HAPP_USERS is not None else public_vless_link()
                users = [{**user, 'link': vless_link_for_subscription(user['link'], subscription_origin)} for user in users]
            traffic = HAPP_HISTORY.user_totals() if HAPP_HISTORY is not None else {}
            subscription_host = urlsplit(subscription_origin).hostname or '—'
            self.send_html(HTTPStatus.OK, happ_server_page(users, CSRF_TOKEN, form_value(query, 'message'), form_value(query, 'kind') or 'success', events, traffic=traffic, subscriptions=subscriptions, vip_vless_link=vip_vless_link, endpoint_host=subscription_host))
            return
        if parsed.path == '/settings':
            query = parse_qs(parsed.query)
            try:
                config = load_config()
                self.send_html(HTTPStatus.OK, render_settings_page(config, query, form_value(query, 'message'), form_value(query, 'kind') or 'success'))
            except (WgEasyApiError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                self.send_html(HTTPStatus.BAD_GATEWAY, f'<h1>Настройки временно недоступны</h1><p>{esc(error)}</p>')
            return
        if parsed.path == '/outbounds/checks':
            try:
                self.send_json(HTTPStatus.OK, outbound_check_states(load_config()))
            except (OSError, ValueError):
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'error': 'Результаты проверки временно недоступны.'})
            return
        if parsed.path == '/gateway-journal':
            if VLESS_MONITOR is None:
                self.send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            events = VLESS_MONITOR.journal()
            labels = {'route_changed': 'Шлюз переключён', 'route_selected_deferred': 'Маршрут сохранён, применение отложено', 'check_cycle': 'Цикл проверки', 'monitor_error': 'Ошибка проверки', 'switch_failed': 'Ошибка переключения', 'mattermost_sent': 'Mattermost: доставлено', 'mattermost_failed': 'Mattermost: ошибка', 'mattermost_test_queued': 'Mattermost: тест в очереди'}
            sources = {'manual': 'Вручную', 'automatic': 'Автоматически', 'manual_mode': 'Смена режима', 'test': 'Тест'}
            rows = []
            for event in events:
                details = event.get('message', '')
                if event.get('event') == 'check_cycle':
                    cycle_checks = event.get('checks', [])
                    details = f'Ответили: {sum(item.get("state") == "success" for item in cycle_checks)}/{len(cycle_checks)}; серия {event.get("streak", 0)}/3.'
                rows.append(f'<tr><td>{esc(event.get("at", ""))}</td><td>{esc(labels.get(event.get("event"), event.get("event", "")))}</td><td>{esc(event.get("old_route", ""))}</td><td>{esc(event.get("new_route", "") or event.get("winner", ""))}</td><td>{esc(sources.get(event.get("source"), event.get("source", "")))}</td><td>{esc(event.get("latency_ms", ""))}</td><td>{esc(details)}</td></tr>')
            rows = ''.join(rows)
            body = f'<section class="page-head"><h1>Журнал переключений</h1></section><section class="panel"><div class="table-wrap"><table><thead><tr><th>Время UTC</th><th>Событие</th><th>Было</th><th>Стало / кандидат</th><th>Источник</th><th>мс</th><th>Результат</th></tr></thead><tbody>{rows or "<tr><td colspan=7>Событий пока нет.</td></tr>"}</tbody></table></div></section>'
            self.send_html(HTTPStatus.OK, render_shell('Журнал переключений', body, 'settings'))
            return
        if parsed.path == '/outbounds':
            query = parse_qs(parsed.query)
            try:
                self.send_html(HTTPStatus.OK, render_outbounds_page(load_config(), query, form_value(query, 'message'), form_value(query, 'kind') or 'success'))
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                self.send_html(HTTPStatus.BAD_GATEWAY, f'<h1>VPN-серверы временно недоступны</h1><p>{esc(error)}</p>')
            return
        if parsed.path not in ('/', '/vless'):
            self.send_empty(HTTPStatus.NOT_FOUND)
            return
        self.redirect_to('/outbounds')
        return

    def do_POST(self):
        if not self.require_access():
            return
        path = urlparse(self.path).path
        if path.startswith('/happ-users/'):
            try:
                values = self.parse_form()
                if HAPP_USERS is None:
                    raise RuntimeError('Управление пользователями HAPP недоступно.')
                with HAPP_LOCK:
                    if path == '/happ-users/create':
                        HAPP_USERS.create(form_value(values, 'name'), form_value(values, 'expires_at'))
                        sync_happ_history_users()
                        message = 'Персональный пользователь создан. VIP credentials сохранены; при применении HAPP переподключает сессии.'
                    elif path == '/happ-users/action':
                        HAPP_USERS.action(form_value(values, 'id'), form_value(values, 'operation'), form_value(values, 'name'), form_value(values, 'expires_at'))
                        sync_happ_history_users()
                        message = 'Изменение пользователя применено; VIP-ссылка сохранена.'
                    elif path == '/happ-users/traffic/reset':
                        user_id = form_value(values, 'id')
                        if not any(user.get('id') == user_id for user in HAPP_USERS.registry().get('users', [])):
                            raise ValueError('Пользователь не найден.')
                        if HAPP_HISTORY is None:
                            raise RuntimeError('Хранилище статистики HAPP недоступно.')
                        HAPP_HISTORY.reset_user_totals('personal-' + user_id)
                        message = 'Накопительная статистика пользователя сброшена.'
                    else:
                        self.send_empty(HTTPStatus.NOT_FOUND)
                        return
                self.redirect_to('/happ-server?' + urlencode({'message': message, 'kind': 'success'}))
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError, subprocess.SubprocessError):
                message = 'Сначала завершите первоначальную настройку HAPP: сгенерируйте и примените Reality-ключи в мастере или Настройках.' if happ_setup_needed() else 'Пользователь не изменён. Проверьте имя, срок доступа и конфигурацию HAPP.'
                self.redirect_to('/happ-server?' + urlencode({'message': message, 'kind': 'error'}))
            return
        if path.startswith('/settings/'):
            try:
                values = self.parse_form()
                if path == '/settings/happ-history':
                    if HAPP_HISTORY is None:
                        raise RuntimeError('Хранилище статистики недоступно.')
                    deleted = HAPP_HISTORY.set_retention(form_value(values, 'retention_days'))
                    self.redirect_settings('', f'Срок хранения сохранён. Удалено старых записей: {deleted}.')
                    return
                if path == '/settings/vless-monitor':
                    if VLESS_MONITOR is None:
                        raise RuntimeError('Монитор проверки VLESS недоступен.')
                    with CONFIG_LOCK:
                        settings = load_monitor_settings(APP_DIR)
                        webhook = form_value(values, 'webhook_url') or settings['webhook_url']
                        if form_value(values, 'clear_webhook') == 'on':
                            webhook = ''
                        VLESS_MONITOR.save_settings({
                            'enabled': form_value(values, 'monitor_enabled') == 'on',
                            'interval_minutes': form_value(values, 'interval_minutes'),
                            'auto_switch': form_value(values, 'auto_switch') == 'on',
                            'mattermost_enabled': form_value(values, 'mattermost_enabled') == 'on',
                            'webhook_url': webhook,
                        })
                    self.redirect_settings('', 'Настройки автоматизации сохранены. Проверки выполняются последовательно в фоне.')
                    return
                if path == '/settings/vless-monitor/test-webhook':
                    if VLESS_MONITOR is None:
                        raise RuntimeError('Монитор проверки VLESS недоступен.')
                    VLESS_MONITOR.test_notification(load_config().get('route', {}).get('final', ''))
                    self.redirect_settings('', 'Тест Mattermost отправлен в очередь. Результат появится в журнале переключений.')
                    return
                if path == '/settings/vless':
                    with CONFIG_LOCK:
                        config = load_config()
                        tag = update_profile(config, values)
                        applied = apply_configuration(config)
                    message = 'VLESS профиль проверен и применён.' if applied else 'VLESS профиль сохранён; он применится после возврата в режим VLESS.'
                    self.redirect_settings(tag, message)
                    return
                if path == '/settings/vless-route':
                    with CONFIG_LOCK:
                        config = load_config()
                        tag = set_default_outbound(config, form_value(values, 'route_final'))
                        applied = apply_configuration(config)
                    message = f'Исходящий профиль {tag} применён.' if applied else f'Исходящий профиль {tag} сохранён и применится в режиме VLESS.'
                    self.redirect_settings('', message)
                    return
                if path == '/settings/vless-restart':
                    with CONFIG_LOCK:
                        restarted = restart_sing_box()
                    message = 'sing-box перезапущен.' if restarted else 'VLESS приостановлен; для перезапуска переключите шлюз на VLESS.'
                    self.redirect_settings('', message)
                    return
                if path == '/settings/wireguard/gateway':
                    with SERVICE_CONTROL_LOCK:
                        gateway = save_wireguard_fallback_gateway(form_value(values, 'wireguard_fallback_gateway'))
                    self.redirect_settings('', f'Шлюз WireGuard сохранён: {gateway}.')
                    return
                if path == '/settings/gateway/config':
                    with SERVICE_CONTROL_LOCK:
                        save_wireguard_client_config(form_value(values, 'wireguard_client_config'))
                        if gateway_mode() == 'wireguard':
                            control_gateway_mode('wireguard')
                    self.redirect_settings('', 'Конфигурация внешнего WireGuard сохранена.')
                    return
                if path == '/settings/gateway/mode':
                    mode = form_value(values, 'mode')
                    with SERVICE_CONTROL_LOCK:
                        control_gateway_mode(mode)
                    messages = {
                        'vless': 'Включён VLESS-шлюз. Внешний WireGuard остановлен.',
                        'wireguard': 'Включён внешний WireGuard-шлюз. VLESS приостановлен.',
                        'default': 'Включён шлюз по умолчанию. Весь внешний трафик клиентов идёт через основной шлюз сервера.',
                    }
                    self.redirect_settings('', messages[mode])
                    return
                if path == '/settings/service/wireguard':
                    action = form_value(values, 'operation')
                    with SERVICE_CONTROL_LOCK:
                        control_wireguard(action)
                    messages = {'start': 'WireGuard запущен.', 'restart': 'WireGuard перезапущен.', 'stop': 'WireGuard остановлен; маршрут направлен через LAN-шлюз.'}
                    self.redirect_settings('', messages[action])
                    return
                if path == '/settings/service/vless':
                    action = form_value(values, 'operation')
                    with CONFIG_LOCK:
                        control_system_service('sing-box', action)
                    messages = {'start': 'VLESS запущен.', 'restart': 'VLESS перезапущен.', 'stop': 'VLESS остановлен.'}
                    self.redirect_settings('', messages[action])
                    return
                if path == '/settings/service/happ':
                    action = form_value(values, 'operation')
                    with HAPP_LOCK:
                        control_system_service('sing-box-happ-server', action)
                    messages = {'start': 'HAPP Server запущен.', 'restart': 'HAPP Server перезапущен.', 'stop': 'HAPP Server остановлен.'}
                    self.redirect_settings('', messages[action])
                    return
                if path == '/settings/system/reboot':
                    if form_value(values, 'reboot_confirmation') != 'REBOOT':
                        raise ValueError('Для перезагрузки введите REBOOT.')
                    with SERVICE_CONTROL_LOCK:
                        schedule_server_reboot()
                    self.redirect_settings('', 'Перезагрузка сервера запланирована.')
                    return
                if path == '/settings/wireguard/general':
                    with WG_LOCK:
                        WG_ADMIN.update_general(values)
                    self.redirect_settings('', 'WireGuard General сохранён.')
                    return
                if path == '/settings/wireguard/interface':
                    with WG_LOCK:
                        WG_ADMIN.update_interface(values)
                    self.redirect_settings('', 'WireGuard Interface сохранён.')
                    return
                if path == '/settings/wireguard/restart':
                    with WG_LOCK:
                        WG_ADMIN.api.restart_interface()
                    self.redirect_settings('', 'Интерфейс WireGuard перезапущен.')
                    return
                if path == '/settings/happ/subscription':
                    with HAPP_LOCK:
                        save_happ_subscription_base_url(form_value(values, 'subscription_base_url'))
                    self.redirect_settings('', 'Публичный адрес подписки сохранён; VIP-ссылка не изменена.')
                    return
                if path == '/settings/happ/announcement':
                    with HAPP_LOCK:
                        save_happ_subscription_content(
                            form_value(values, 'subscription_title'),
                            form_value(values, 'subscription_announcement'),
                        )
                    self.redirect_settings('', 'Заголовок и объявление HAPP сохранены; новые данные появятся при обновлении подписки.')
                    return
                if path == '/settings/happ/keys':
                    if form_value(values, 'confirm_happ_keys') != 'generate':
                        raise ValueError('Подтвердите применение ключей и обновление всех ссылок.')
                    with CONFIG_LOCK, HAPP_LOCK:
                        generate_and_apply_happ_keys(form_value(values, 'happ_server'), form_value(values, 'happ_sni'), form_value(values, 'happ_port'))
                    self.redirect_settings('', 'Reality-ключи применены ко всем ссылкам; HAPP Server запущен. Обновите подписки клиентов.')
                    return
                if path == '/settings/happ/state':
                    with HAPP_LOCK:
                        apply_happ_state(form_value(values, 'happ_state_json'))
                    self.redirect_settings('', 'Public HAPP link сохранён.')
                    return
                if path == '/settings/happ/config':
                    with HAPP_LOCK:
                        apply_happ_configuration(form_value(values, 'happ_config_json'))
                    self.redirect_settings('', 'HAPP Server конфигурация проверена и применена.')
                    return
                if path == '/settings/happ/restart':
                    with HAPP_LOCK:
                        restart_happ_server()
                    self.redirect_settings('', 'HAPP Server перезапущен.')
                    return
                if path == '/settings/password':
                    password = form_value(values, 'new_password')
                    if password != form_value(values, 'confirm_password'):
                        raise ValueError('Пароли не совпадают.')
                    set_password(password)
                    self.send_html(HTTPStatus.OK, '<!doctype html><meta charset="utf-8"><p>Пароль обновлён. Обновите страницу и войдите с новым паролем.</p>')
                    return
                self.send_empty(HTTPStatus.NOT_FOUND)
            except WgEasyApiError as error:
                self.redirect_settings(form_value(locals().get('values', {}), 'profile_tag'), str(error), 'error')
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                self.redirect_settings(form_value(locals().get('values', {}), 'profile_tag'), str(error), 'error')
            except Exception:
                self.redirect_settings('', 'Настройки не применены. Текущая конфигурация сохранена.', 'error')
            return
        if path.startswith('/outbounds/'):
            try:
                values = self.parse_form()
                with CONFIG_LOCK:
                    config = load_config()
                    if path == '/outbounds/import':
                        imported, skipped = import_server_batch(
                            form_value(values, 'outbound_json'),
                            config,
                            form_value(values, 'replace_tag'),
                            form_value(values, 'import_tag'),
                            validator=check_candidate,
                        )
                        applied = apply_configuration(config)
                        kind = 'error' if skipped else 'success'
                        queue_server_checks(config, imported)
                        message = f'Импортировано: {", ".join(imported)}. '
                        if not applied:
                            message += 'Применение маршрута отложено до режима VLESS. '
                        message += ' '.join(skipped)
                        self.redirect_outbounds(message, kind, checks=imported)
                        return
                    if path == '/outbounds/delete':
                        tag = remove_server_json(config, form_value(values, 'tag'))
                        applied = apply_configuration(config)
                        message = f'Сервер {tag} удалён.' if applied else f'Сервер {tag} удалён из конфигурации; изменение применится в режиме VLESS.'
                        self.redirect_outbounds(message)
                        return
                    if path == '/outbounds/route':
                        tag = set_default_outbound(config, form_value(values, 'tag'))
                        applied = apply_configuration(config)
                        message = f'Маршрут переключён на {tag}.' if applied else f'Маршрут {tag} сохранён и применится в режиме VLESS.'
                        self.redirect_outbounds(message)
                        return
                    if path == '/outbounds/check':
                        tag = form_value(values, 'tag')
                        queue_server_checks(config, [tag])
                        self.redirect_outbounds(checks=[tag])
                        return
                self.send_empty(HTTPStatus.NOT_FOUND)
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                self.redirect_outbounds(str(error), 'error')
            except Exception:
                self.redirect_outbounds('Операция не выполнена; текущая конфигурация сохранена.', 'error')
            return
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
            if path in ('/vless/profile', '/save'):
                with CONFIG_LOCK:
                    config = load_config()
                    tag = update_profile(config, values)
                    applied = apply_configuration(config)
                message = 'Конфигурация проверена и применена.' if applied else 'Конфигурация сохранена; она применится после возврата в режим VLESS.'
                self.redirect_vless(tag, message)
                return
            if path in ('/vless/route', '/route'):
                with CONFIG_LOCK:
                    config = load_config()
                    tag = set_default_outbound(config, form_value(values, 'route_final'))
                    applied = apply_configuration(config)
                message = f'Исходящий профиль {tag} применён.' if applied else f'Исходящий профиль {tag} сохранён и применится в режиме VLESS.'
                self.redirect_vless('', message)
                return
            if path in ('/vless/restart', '/restart'):
                with CONFIG_LOCK:
                    restarted = restart_sing_box()
                message = 'sing-box перезапущен.' if restarted else 'VLESS приостановлен; для перезапуска переключите шлюз на VLESS.'
                self.redirect_vless('', message)
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
            self.redirect_vless(profile, str(error), 'error')
        except Exception:
            self.redirect_vless('', 'Операция не выполнена. Текущая конфигурация сохранена.', 'error')


def happ_expiry_worker():
    while not HAPP_EXPIRY_STOP.wait(60):
        try:
            with HAPP_LOCK:
                if HAPP_USERS is not None:
                    HAPP_USERS.reconcile_expired()
        except Exception:
            if VLESS_MONITOR is not None:
                VLESS_MONITOR.append_event({'event': 'happ_expiry_failed', 'message': 'Не удалось применить истечение персонального доступа HAPP; повтор через минуту.'})


def sync_happ_history_users():
    if HAPP_HISTORY is not None and HAPP_USERS is not None:
        HAPP_HISTORY.retain_registered_users(HAPP_USERS.registry()['users'])


def happ_history_worker():
    next_cleanup = 0.0
    while not HAPP_HISTORY_STOP.is_set():
        cycle_started = time.monotonic()
        try:
            if HAPP_HISTORY is not None:
                with HAPP_LOCK:
                    sync_happ_history_users()
                payload = happ_live_connections(include_visits=True, history_since=HAPP_HISTORY.last_collected_at())
                HAPP_HISTORY.ingest(payload)
                acknowledge_history_visits(payload.get('visits', []))
                if time.monotonic() >= next_cleanup:
                    HAPP_HISTORY.cleanup()
                    next_cleanup = time.monotonic() + 3600
        except Exception:
            try:
                if HAPP_HISTORY is not None:
                    HAPP_HISTORY.record_collection_error()
            except Exception:
                pass
        HAPP_HISTORY_STOP.wait(max(0.05, 1 - (time.monotonic() - cycle_started)))


def main():
    global VLESS_MONITOR, HAPP_USERS, HAPP_HISTORY, SERVER_METRICS
    if not AUTH_PATH.is_file():
        raise SystemExit(f'Authentication file missing: {AUTH_PATH}')
    try:
        ipaddress.IPv4Address(HOST)
    except ipaddress.AddressValueError as error:
        raise SystemExit('SING_BOX_ADMIN_HOST must be an IPv4 address') from error
    server = VpnOnlyServer((HOST, PORT), Handler)
    HAPP_USERS = HappUsers(APP_DIR, HAPP_CONFIG_PATH, HAPP_STATE_PATH, apply_happ_configuration)
    HAPP_USERS.initialize()
    HAPP_HISTORY = HappHistory('/mnt/stat')
    sync_happ_history_users()
    SERVER_METRICS = ServerMetrics('/mnt/stat/server-metrics.sqlite3')
    SERVER_METRICS.sample()
    threading.Thread(target=collect_metrics, args=(SERVER_METRICS_STOP, SERVER_METRICS), name='server-metrics', daemon=True).start()
    threading.Thread(target=happ_history_worker, name='happ-history', daemon=True).start()
    threading.Thread(target=happ_expiry_worker, name='happ-expiry', daemon=True).start()
    VLESS_MONITOR = VlessMonitor(APP_DIR, load_config, queue_server_checks, outbound_check_states, switch_monitored_route, gateway_mode)
    VLESS_MONITOR.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        SERVER_METRICS_STOP.set()
        HAPP_HISTORY_STOP.set()
        HAPP_EXPIRY_STOP.set()
        VLESS_MONITOR.stop()
        server.server_close()


if __name__ == '__main__':
    main()
