#!/usr/bin/env python3
import html
import ipaddress
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode

from panel_ui import render_shell
from wg_easy_api import WgEasyApiError

CLIENT_UPDATE_FIELDS = (
    'name', 'enabled', 'expiresAt', 'ipv4Address', 'ipv6Address',
    'preUp', 'postUp', 'preDown', 'postDown', 'allowedIps',
    'serverAllowedIps', 'firewallIps', 'mtu', 'jC', 'jMin', 'jMax',
    'i1', 'i2', 'i3', 'i4', 'i5', 'persistentKeepalive',
    'serverEndpoint', 'dns',
)
INTERFACE_UPDATE_FIELDS = (
    'ipv4Cidr', 'ipv6Cidr', 'mtu', 'routingTable', 'jC', 'jMin',
    'jMax', 's1', 's2', 's3', 's4', 'h1', 'h2', 'h3', 'h4',
    'i1', 'i2', 'i3', 'i4', 'i5', 'port', 'device', 'enabled',
    'firewallEnabled',
)
GENERAL_UPDATE_FIELDS = (
    'sessionTimeout', 'metricsPrometheus', 'metricsJson', 'metricsPassword',
)
ACTIVE_HANDSHAKE_SECONDS = 60
LAN_DENY_PATH = '/etc/sing-box-admin/wg-lan-deny.json'
WAN_IP_FALLBACK = '37.208.69.6'
_wan_ip_cache = ('', 0.0)


def esc(value):
    return html.escape(str(value), quote=True)


def form_value(values, name):
    return values.get(name, [''])[0].strip()


def field_subset(data, fields):
    return {field: data.get(field) for field in fields if field in data}


def json_text(data):
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def client_id(client):
    return client.get('clientId', client.get('id', ''))


def client_name(client):
    return client.get('name') or f'Client {client_id(client)}'


def client_status(client):
    return 'Включён' if client.get('enabled') else 'Отключён'


def client_address(client):
    return client.get('ipv4Address') or client.get('address') or '—'


def client_wan_ip(client):
    endpoint = str(client.get('endpoint') or '').strip()
    if not endpoint:
        return '—'
    if endpoint.startswith('['):
        host = endpoint[1:].partition(']')[0]
    else:
        host, separator, _ = endpoint.rpartition(':')
        if not separator:
            host = endpoint
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return '—'


def format_date(value):
    if not value:
        return 'Без срока'
    try:
        normalized = value.replace('Z', '+00:00')
        return datetime.fromisoformat(normalized).strftime('%d.%m.%Y %H:%M')
    except (TypeError, ValueError):
        return str(value)


def format_bytes(value):
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        return '0 B'
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f'{amount} {unit}' if unit == 'B' else f'{amount:.1f} {unit}'
        amount /= 1024
    return '0 B'


def format_megabytes(value):
    try:
        amount = max(0, int(value or 0))
    except (TypeError, ValueError):
        amount = 0
    return f'{amount / (1024 * 1024):.1f}'


def total_transfer(clients):
    download = 0
    upload = 0
    for client in clients:
        try:
            download += max(0, int(client.get('transferRx') or 0))
        except (TypeError, ValueError):
            pass
        try:
            upload += max(0, int(client.get('transferTx') or 0))
        except (TypeError, ValueError):
            pass
    return download, upload


def current_wan_ip():
    global _wan_ip_cache
    now = time.monotonic()
    if now - _wan_ip_cache[1] < 300 and _wan_ip_cache[0]:
        return _wan_ip_cache[0]
    candidate = ''
    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=2) as response:
            candidate = response.read().decode('ascii').strip()
        candidate = str(ipaddress.ip_address(candidate))
    except (OSError, ValueError, UnicodeDecodeError):
        candidate = WAN_IP_FALLBACK
    _wan_ip_cache = (candidate, now)
    return candidate


def parse_service_timestamp(value):
    raw = value.strip()
    if 'T' in raw and raw.endswith('Z'):
        base = raw[:-1]
        if '.' in base:
            prefix, fraction = base.split('.', 1)
            base = f'{prefix}.{fraction[:6].ljust(6, "0")}'
        return datetime.fromisoformat(base + '+00:00')
    return datetime.strptime(raw, '%a %Y-%m-%d %H:%M:%S %Z').replace(tzinfo=timezone.utc)


def service_uptime():
    commands = (
        ['/usr/bin/docker', 'inspect', '--format', '{{.State.StartedAt}}', 'wg-easy'],
        ['/usr/bin/systemctl', 'show', 'sing-box-gateway', '-p', 'ActiveEnterTimestamp', '--value'],
    )
    for args in commands:
        try:
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        value = result.stdout.strip()
        if not value or value.startswith('0001-01-01'):
            continue
        try:
            started = parse_service_timestamp(value)
        except ValueError:
            continue
        seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        if days:
            return f'{days} дн. {hours} ч.'
        if hours:
            return f'{hours} ч. {minutes} мин.'
        return f'{minutes} мин.'
    return 'недоступно'


def relative_time(value):
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    seconds = max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds()))
    if seconds < 60:
        return 'только что', seconds
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} мин. назад', seconds
    hours = minutes // 60
    if hours < 24:
        return f'{hours} ч. назад', seconds
    return f'{hours // 24} дн. назад', seconds


def activity(client):
    handshake = relative_time(client.get('latestHandshakeAt'))
    if not handshake:
        return 'never', 'Не подключался', 'Нет handshake'
    label, seconds = handshake
    if seconds <= ACTIVE_HANDSHAKE_SECONDS:
        return 'online', 'Онлайн', label
    return 'stale', 'Был на связи', label


def transfer_summary(client):
    return f'RX {format_bytes(client.get("transferRx"))} · TX {format_bytes(client.get("transferTx"))}'


def keepalive_interval(client):
    try:
        seconds = int(client.get('persistentKeepalive') or 0)
    except (TypeError, ValueError):
        seconds = 0
    return f'{seconds} сек.' if seconds > 0 else '—'


def wireguard_qr_modal():
    return '''<div class="qr-modal" data-wg-qr-modal hidden aria-hidden="true"><div class="qr-dialog" role="dialog" aria-modal="true" aria-labelledby="wg-qr-title"><button class="qr-close" type="button" data-wg-qr-close aria-label="Закрыть">×</button><h2 id="wg-qr-title" data-wg-qr-title>QR WireGuard</h2><p data-wg-qr-caption>Отсканируйте код в приложении WireGuard.</p><img class="qr-image" data-wg-qr-image alt="QR-конфигурация WireGuard"></div></div>'''


def load_lan_denies():
    try:
        payload = json.loads(open(LAN_DENY_PATH, encoding='utf-8').read())
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, list):
        return set()
    result = set()
    for value in payload:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4:
            result.add(str(address))
    return result


def save_lan_denies(denies):
    directory = os.path.dirname(LAN_DENY_PATH)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.wg-lan-deny-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(sorted(denies, key=ipaddress.ip_address), handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, LAN_DENY_PATH)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def restart_lan_firewall():
    result = subprocess.run(
        ['/usr/bin/systemctl', 'restart', 'wg-easy-private-ui'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise WgEasyApiError('Не удалось применить LAN-политику.')


def notice(message, kind):
    if not message:
        return ''
    css_class = 'success' if kind == 'success' else 'error'
    return f'<div class="notice {css_class}" role="status">{esc(message)}</div>'


def page_shell(title, body, active='wireguard'):
    return render_shell(title, body, active)

    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} | FOCUSLENS.DEV</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="stylesheet" href="/panel.css?v=2">
<style>
:root {{
  --bg: #050816;
  --surface: rgba(17, 27, 52, .88);
  --surface-soft: rgba(11, 18, 38, .78);
  --text: #f5f7ff;
  --muted: #8ea0be;
  --accent: #7c3aed;
  --accent-2: #22d3ee;
  --danger: #fb7185;
  --ok: #5eead4;
  --border: rgba(255,255,255,.12);
  --shadow: 0 20px 40px rgba(0,0,0,.28);
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; background: radial-gradient(circle at 0 0, rgba(34,211,238,.16), transparent 26%), radial-gradient(circle at 88% 8%, rgba(124,58,237,.17), transparent 30%), var(--bg); color: var(--text); font-family: Inter, "Segoe UI", Roboto, Arial, sans-serif; line-height: 1.5; }}
a {{ color: inherit; }}
.standalone-shell {{ display: grid; grid-template-columns: 270px minmax(0, 1fr); min-height: 100vh; }}
.standalone-sidebar {{ background: rgba(5,8,22,.86); border-right: 1px solid var(--border); padding: 26px 18px; }}
.standalone-sidebar .side-brand {{ align-items: center; display: flex; gap: 11px; font-size: 15px; font-weight: 700; letter-spacing: .05em; margin-bottom: 32px; text-decoration: none; }}
.standalone-sidebar .side-brand img {{ height: 40px; width: 40px; }}
.standalone-sidebar .side-brand small {{ color: var(--accent-2); display: block; font-size: 10px; letter-spacing: .12em; margin-top: 3px; }}
.standalone-menu {{ display: grid; gap: 6px; }}
.standalone-menu a {{ border-left: 3px solid transparent; color: var(--muted); padding: 10px 11px; text-decoration: none; }}
.standalone-menu a:hover, .standalone-menu a.active {{ background: linear-gradient(90deg, rgba(124,58,237,.24), rgba(34,211,238,.08)); border-color: var(--accent-2); color: var(--text); }}
.standalone-content {{ min-width: 0; }}
.main {{ margin: 0 auto; max-width: 1440px; padding: 32px clamp(18px,4vw,60px) 52px; }}
.topline {{ align-items: start; border-bottom: 1px solid var(--border); display: flex; gap: 20px; justify-content: space-between; margin-bottom: 26px; padding-bottom: 22px; }}
.eyebrow {{ color: var(--accent-2); font-size: 11px; font-weight: 800; letter-spacing: .13em; margin: 0 0 7px; text-transform: uppercase; }}
h1 {{ font-size: clamp(25px,3vw,34px); letter-spacing: .01em; line-height: 1.1; margin: 0; }}
.subtitle {{ color: var(--muted); margin: 8px 0 0; }}
.status {{ align-items: center; background: var(--surface); border: 1px solid var(--border); box-shadow: 0 10px 26px rgba(0,0,0,.2); display: flex; font-size: 13px; gap: 8px; padding: 10px 12px; white-space: nowrap; }}
.status-dot {{ background: var(--danger); border-radius: 50%; height: 9px; width: 9px; }}
.status-dot.ok {{ background: var(--ok); box-shadow: 0 0 0 4px rgba(94,234,212,.12); }}
.layout {{ display: grid; gap: 18px; grid-template-columns: minmax(0,1fr) 332px; min-width: 0; }}
.layout > *, .stack, .panel {{ min-width: 0; }}
.stack {{ display: grid; gap: 18px; }}
.panel {{ background: var(--surface); border: 1px solid var(--border); box-shadow: var(--shadow); padding: 22px; }}
.panel h2 {{ font-size: 16px; letter-spacing: .02em; margin: 0 0 18px; }}
.panel h3 {{ color: var(--muted); font-size: 12px; letter-spacing: .08em; margin: 24px 0 10px; text-transform: uppercase; }}
.metrics {{ display: grid; gap: 0; }}
.status-metrics {{ display: grid; gap: 12px; grid-template-columns: repeat(5, minmax(0, 1fr)); }}
.status-card {{ border: 1px solid var(--border); box-shadow: var(--shadow); min-width: 0; padding: 16px; }}
.status-card.cyan {{ background: linear-gradient(145deg, rgba(34,211,238,.22), rgba(17,27,52,.9)); border-color: rgba(34,211,238,.42); }}
.status-card.violet {{ background: linear-gradient(145deg, rgba(124,58,237,.25), rgba(17,27,52,.9)); border-color: rgba(124,58,237,.45); }}
.status-card.teal {{ background: linear-gradient(145deg, rgba(45,212,191,.2), rgba(17,27,52,.9)); border-color: rgba(94,234,212,.42); }}
.status-card.orange {{ background: linear-gradient(145deg, rgba(251,146,60,.2), rgba(17,27,52,.9)); border-color: rgba(251,146,60,.42); }}
.status-card.pink {{ background: linear-gradient(145deg, rgba(244,114,182,.2), rgba(17,27,52,.9)); border-color: rgba(244,114,182,.42); }}
.status-card span {{ color: var(--muted); display: block; font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
.status-card strong {{ display: block; font-size: clamp(18px, 2vw, 24px); line-height: 1.2; margin-top: 8px; overflow-wrap: anywhere; }}
.status-card small {{ color: var(--muted); display: block; font-size: 11px; margin-top: 6px; }}
.metric {{ border-bottom: 1px solid var(--border); display: grid; gap: 4px; padding: 12px 0; }}
.metric:first-child {{ padding-top: 0; }}
.metric:last-child {{ border-bottom: 0; padding-bottom: 0; }}
.metric span {{ color: var(--muted); font-size: 12px; }}
.metric strong {{ font-size: 15px; overflow-wrap: anywhere; }}
.table-wrap {{ max-width: 100%; min-width: 0; overflow-x: auto; }}
table {{ border-collapse: collapse; min-width: 720px; width: 100%; }}
th {{ color: var(--muted); font-size: 11px; letter-spacing: .08em; padding: 0 10px 10px; text-align: left; text-transform: uppercase; }}
td {{ border-top: 1px solid var(--border); padding: 12px 10px; vertical-align: middle; }}
td:first-child, th:first-child {{ padding-left: 0; }}
td:last-child, th:last-child {{ padding-right: 0; }}
.client-name {{ display: grid; font-weight: 700; gap: 3px; }}
.client-name small {{ color: var(--muted); font-size: 11px; font-weight: 500; }}
.badge {{ border: 1px solid var(--border); color: var(--muted); display: inline-flex; font-size: 11px; font-weight: 700; padding: 3px 7px; white-space: nowrap; }}
.badge.ok {{ border-color: rgba(94,234,212,.45); color: var(--ok); }}
.badge.online {{ border-color: rgba(94,234,212,.5); color: var(--ok); }}
.badge.stale {{ border-color: rgba(34,211,238,.4); color: var(--accent-2); }}
.badge.never {{ border-color: rgba(142,160,190,.42); color: var(--muted); }}
.activity-cell {{ display: grid; gap: 4px; min-width: 135px; }}
.activity-cell small {{ color: var(--muted); font-size: 11px; }}
.actions {{ border-top: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; padding-top: 16px; }}
.inline-actions {{ align-items: center; display: flex; flex-wrap: wrap; gap: 6px; }}
button, .button {{ appearance: none; background: linear-gradient(135deg, var(--accent), var(--accent-2)); border: 0; border-radius: 4px; box-shadow: 0 10px 22px rgba(124,58,237,.22); color: #fff; cursor: pointer; font: inherit; font-size: 13px; font-weight: 700; min-height: 38px; padding: 8px 12px; text-decoration: none; }}
button:hover, .button:hover {{ filter: brightness(1.06); }}
button.secondary, .button.secondary {{ background: transparent; border: 1px solid var(--border); box-shadow: none; color: #dce8ff; }}
button.danger {{ background: transparent; border: 1px solid rgba(251,113,133,.48); box-shadow: none; color: #fda4af; }}
form {{ margin: 0; }}
.form-grid {{ display: grid; gap: 14px; grid-template-columns: repeat(2,minmax(0,1fr)); }}
.field {{ display: grid; gap: 7px; }}
.field.full {{ grid-column: 1 / -1; }}
label {{ color: #c1cee5; font-size: 12px; font-weight: 700; }}
input, textarea {{ background: rgba(5,8,22,.72); border: 1px solid rgba(142,160,190,.4); border-radius: 4px; color: var(--text); font: inherit; min-height: 40px; padding: 9px 10px; width: 100%; }}
textarea {{ font-family: "Cascadia Code", Consolas, monospace; font-size: 12px; min-height: 260px; resize: vertical; }}
input:focus, textarea:focus {{ border-color: var(--accent-2); box-shadow: 0 0 0 3px rgba(34,211,238,.14); outline: 0; }}
.notice {{ background: rgba(17,27,52,.82); border-left: 4px solid var(--accent-2); color: #d9f7ff; margin: 0 0 18px; padding: 12px 14px; }}
.notice.success {{ background: rgba(45,212,191,.12); border-color: var(--ok); color: #b8fff0; }}
.notice.error {{ background: rgba(251,113,133,.1); border-color: var(--danger); color: #fecdd3; }}
.empty {{ color: var(--muted); padding: 20px 0 4px; }}
.qr {{ background: #fff; max-width: 260px; padding: 12px; width: 100%; }}
.detail-head {{ align-items: center; display: flex; gap: 10px; justify-content: space-between; margin-bottom: 18px; }}
@media (max-width: 1100px) {{ .layout {{ grid-template-columns: minmax(0,1fr); }} .status-metrics {{ grid-template-columns: repeat(3, minmax(0,1fr)); }} }}
@media (max-width: 880px) {{ .topline {{ display: grid; }} }}
@media (max-width: 560px) {{ .main {{ padding: 24px 14px 36px; }} .form-grid {{ grid-template-columns: 1fr; }} .field.full {{ grid-column: auto; }} .actions {{ align-items: stretch; flex-direction: column; }} .button, button {{ text-align: center; width: 100%; }} .inline-actions {{ align-items: stretch; flex-direction: column; }} .status-metrics {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}
@media (max-width: 380px) {{ .status-metrics {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="standalone-shell"><aside class="standalone-sidebar"><a class="side-brand" href="/"><img src="/favicon.png" alt=""><span>FOCUSLENS.DEV<small>VPN CONTROL</small></span></a><nav class="standalone-menu"><a data-panel-nav="vless" href="/">VLESS</a><a data-panel-nav="wireguard" href="/wireguard">WireGuard</a><a data-panel-nav="happ-routing" href="/happ-routing">HAPP Direct</a><a data-panel-nav="happ-server" href="/happ-server">HAPP Server</a></nav></aside><div class="standalone-content">
<main class="main">{body}</main>
</div></div>
<script src="/panel.js" defer></script>
</body>
</html>'''


class WgAdmin:
    def __init__(self, api, csrf_token):
        self.api = api
        self.csrf_token = csrf_token

    def _csrf(self):
        return f'<input type="hidden" name="csrf" value="{esc(self.csrf_token)}">'

    def render(self, query, message='', kind='success'):
        selected_client = form_value(query, 'client')
        if selected_client:
            return self.render_client(selected_client, message, kind)
        return self.render_dashboard(message, kind)

    def live_state(self):
        clients = self.api.clients()
        download_bytes, upload_bytes = total_transfer(clients)
        client_states = []
        connections = []
        for client in clients:
            state, label, age = activity(client)
            client_states.append({
                'id': str(client_id(client)),
                'state': state,
                'label': label,
                'age': age,
            })
            if state == 'online':
                connections.append({
                    'id': str(client_id(client)),
                    'name': client_name(client),
                    'ip': client_address(client),
                    'wan_ip': client_wan_ip(client),
                    'handshake': age,
                    'download': format_bytes(client.get('transferRx')),
                    'upload': format_bytes(client.get('transferTx')),
                    'keepalive': keepalive_interval(client),
                })
        return {
            'download_mb': format_megabytes(download_bytes),
            'upload_mb': format_megabytes(upload_bytes),
            'online_count': sum(1 for item in client_states if item['state'] == 'online'),
            'clients': client_states,
            'connections': connections,
        }

    def render_dashboard(self, message='', kind='success'):
        clients = self.api.clients()
        general = self.api.general()
        interface = self.api.interface()
        active_count = sum(1 for client in clients if client.get('enabled'))
        online_count = sum(1 for client in clients if activity(client)[0] == 'online')
        download_bytes, upload_bytes = total_transfer(clients)
        download_mb = format_megabytes(download_bytes)
        upload_mb = format_megabytes(upload_bytes)
        wan_ip = current_wan_ip()
        uptime = service_uptime()
        interface_state = 'active' if interface.get('enabled') else 'inactive'
        lan_denies = load_lan_denies()
        rows = []
        live_rows = []
        for client in clients:
            identifier = client_id(client)
            state_class = 'ok' if client.get('enabled') else ''
            toggle = 'disable' if client.get('enabled') else 'enable'
            toggle_text = 'Отключить' if client.get('enabled') else 'Включить'
            activity_class, activity_label, activity_age = activity(client)
            address = client_address(client)
            lan_denied = address in lan_denies
            lan_action = 'allow-lan' if lan_denied else 'deny-lan'
            lan_label = 'Разрешить LAN' if lan_denied else 'Запретить LAN'
            lan_class = 'secondary' if lan_denied else 'danger'
            if activity_class == 'online':
                live_rows.append(f'''<tr>
    <td><div class="client-name">{esc(client_name(client))}<small>ID {esc(identifier)}</small></div></td>
    <td>{esc(address)}</td>
    <td>{esc(client_wan_ip(client))}</td>
    <td>{esc(activity_age)}</td>
    <td>{esc(format_bytes(client.get('transferRx')))} / {esc(format_bytes(client.get('transferTx')))}</td>
    <td>{esc(keepalive_interval(client))}</td>
</tr>''')
            rows.append(f'''<tr data-wg-client-id="{esc(identifier)}">
  <td><div class="client-name">{esc(client_name(client))}<small>ID {esc(identifier)}</small></div></td>
  <td>{esc(client_address(client))}</td>
  <td><span class="badge {state_class}">{esc(client_status(client))}</span></td>
  <td><div class="activity-cell"><span class="badge {activity_class}" data-wg-activity-badge>{esc(activity_label)}</span><small data-wg-activity-age>{esc(activity_age)}</small><small>{esc(transfer_summary(client))}</small></div></td>
  <td>{esc(format_date(client.get('expiresAt')))}</td>
  <td><div class="inline-actions">
    <a class="button secondary" href="/wireguard?client={esc(identifier)}">Настроить</a>
    <a class="button secondary" href="/wireguard/client/{esc(identifier)}/configuration">.conf</a>
    <button class="secondary" type="button" data-wg-qr-url="/wireguard/client/{esc(identifier)}/qr" data-wg-qr-label="{esc(client_name(client))}">QR</button>
    <form method="post" action="/wireguard/client/action">{self._csrf()}<input type="hidden" name="client_id" value="{esc(identifier)}"><input type="hidden" name="action" value="{toggle}"><button class="secondary" type="submit">{toggle_text}</button></form>
    <form method="post" action="/wireguard/client/action">{self._csrf()}<input type="hidden" name="client_id" value="{esc(identifier)}"><input type="hidden" name="action" value="{lan_action}"><button class="{lan_class}" type="submit">{lan_label}</button></form>
  </div></td>
</tr>''')
        client_rows = ''.join(rows) or '<tr><td colspan="6" class="empty">Клиенты пока не созданы.</td></tr>'
        live_client_rows = ''.join(live_rows) or '<tr><td colspan="6" class="empty">Нет активных подключений.</td></tr>'
        body = f'''<section class="topline">
  <div><p class="eyebrow">Private network</p><h1>WireGuard</h1><p class="subtitle">Клиенты, конфигурации и параметры интерфейса</p></div>
  <div class="status"><span class="status-dot {'ok' if interface_state == 'active' else ''}"></span>интерфейс: {esc(interface_state)}</div>
</section>
{notice(message, kind)}
<div class="stack wireguard-dashboard" data-wg-live>
    <section class="status-metrics" aria-label="Состояние WireGuard"><div class="status-card cyan"><span>Онлайн</span><strong data-wg-online-count>{online_count}</strong><small>WireGuard</small></div><div class="status-card violet"><span>Всего</span><strong>{len(clients)}</strong><small>WireGuard</small></div><div class="status-card teal"><span>DL / UL</span><strong data-wg-transfer><span class="wg-transfer-download" data-wg-download>{download_mb}</span><span> / </span><span data-wg-upload>{upload_mb}</span><span> Мб</span></strong><small>скачано / отправлено</small></div><div class="status-card orange"><span>WAN IP</span><strong>{esc(wan_ip)}</strong><small>внешний адрес</small></div><div class="status-card pink"><span>Сервис онлайн</span><strong>{esc(uptime)}</strong><small>wg-easy</small></div></section>
    <section class="panel">
        <div class="detail-head"><h2>Клиенты</h2><span class="badge {'online' if online_count else ''}" data-wg-dashboard-online data-wg-enabled-count="{active_count}">{online_count} онлайн · {active_count} включено</span></div>
        <div class="table-wrap"><table><thead><tr><th>Клиент</th><th>IPv4</th><th>Профиль</th><th>Активность</th><th>Срок</th><th>Действия</th></tr></thead><tbody>{client_rows}</tbody></table></div>
    </section>
    <section class="panel" data-wg-live-connections>
        <div class="detail-head"><div><h2>Подключения WireGuard</h2><p class="subtitle">Живые данные wg-easy по последнему handshake.</p></div><span class="badge online" data-wg-online-summary>{online_count} онлайн</span></div>
        <div class="table-wrap"><table class="wg-live-connections"><thead><tr><th>Клиент</th><th>VPN IP</th><th>WAN IP</th><th>Handshake</th><th>DL / UL</th><th>Keepalive</th></tr></thead><tbody data-wg-connections>{live_client_rows}</tbody></table></div>
    </section>
    <section class="panel">
        <h2>Новый клиент</h2>
        <form method="post" action="/wireguard/client/create"><input type="hidden" name="csrf" value="{esc(self.csrf_token)}"><div class="form-grid"><div class="field"><label for="client_name">Имя</label><input id="client_name" name="client_name" maxlength="128" required></div><div class="field"><label for="expires_at">Срок действия</label><input id="expires_at" name="expires_at" type="datetime-local"></div></div><div class="actions"><button type="submit">Создать клиента</button></div></form>
    </section>
    <section class="panel">
        <h2>Общие настройки</h2>
        <form method="post" action="/wireguard/general"><input type="hidden" name="csrf" value="{esc(self.csrf_token)}"><div class="field"><label for="general_json">General</label><textarea id="general_json" name="general_json" spellcheck="false">{esc(json_text(field_subset(general, GENERAL_UPDATE_FIELDS)))}</textarea></div><div class="actions"><button type="submit">Сохранить общие настройки</button></div></form>
    </section>
    <section class="panel">
        <h2>Интерфейс WireGuard</h2>
        <form method="post" action="/wireguard/interface"><input type="hidden" name="csrf" value="{esc(self.csrf_token)}"><div class="field"><label for="interface_json">Interface</label><textarea id="interface_json" name="interface_json" spellcheck="false">{esc(json_text(field_subset(interface, INTERFACE_UPDATE_FIELDS)))}</textarea></div><div class="actions"><button type="submit">Сохранить интерфейс</button></div></form>
        <form method="post" action="/wireguard/interface/restart">{self._csrf()}<div class="actions"><button class="secondary" type="submit">Перезапустить интерфейс</button></div></form>
    </section>
</div>
{wireguard_qr_modal()}'''
        return page_shell('WireGuard', body)

    def render_client(self, selected_client, message='', kind='success'):
        client = self.api.client(selected_client)
        identifier = client_id(client)
        payload = field_subset(client, CLIENT_UPDATE_FIELDS)
        activity_class, activity_label, activity_age = activity(client)
        lan_denied = client_address(client) in load_lan_denies()
        lan_action = 'allow-lan' if lan_denied else 'deny-lan'
        lan_label = 'Разрешить доступ в LAN' if lan_denied else 'Запретить доступ в LAN'
        lan_class = 'secondary' if lan_denied else 'danger'
        body = f'''<section class="topline">
  <div><p class="eyebrow">WireGuard client</p><h1>{esc(client_name(client))}</h1><p class="subtitle">ID {esc(identifier)} · {esc(client_address(client))}</p></div>
  <a class="button secondary" href="/wireguard">К списку клиентов</a>
</section>
{notice(message, kind)}
<div class="layout">
  <div class="stack">
    <section class="panel">
      <h2>Настройки клиента</h2>
      <form method="post" action="/wireguard/client/update"><input type="hidden" name="csrf" value="{esc(self.csrf_token)}"><input type="hidden" name="client_id" value="{esc(identifier)}"><div class="field"><label for="client_json">Client</label><textarea id="client_json" name="client_json" spellcheck="false">{esc(json_text(payload))}</textarea></div><div class="actions"><button type="submit">Сохранить клиента</button></div></form>
    </section>
    <section class="panel">
      <h2>Опасная операция</h2>
      <form method="post" action="/wireguard/client/action">{self._csrf()}<input type="hidden" name="client_id" value="{esc(identifier)}"><input type="hidden" name="action" value="delete"><div class="actions"><button class="danger" type="submit">Удалить клиента</button></div></form>
    </section>
  </div>
  <aside class="stack">
    <section class="panel"><h2>Подключение</h2><div class="metrics"><div class="metric"><span>Статус</span><strong>{esc(client_status(client))}</strong></div><div class="metric"><span>Активность</span><strong class="badge {activity_class}">{esc(activity_label)}</strong></div><div class="metric"><span>Последний handshake</span><strong>{esc(activity_age)}</strong></div><div class="metric"><span>Трафик</span><strong>{esc(transfer_summary(client))}</strong></div><div class="metric"><span>Срок</span><strong>{esc(format_date(client.get('expiresAt')))}</strong></div></div><div class="actions"><a class="button secondary" href="/wireguard/client/{esc(identifier)}/configuration">Скачать .conf</a></div></section>
    <section class="panel"><h2>Доступ к LAN</h2><p class="subtitle">Интернет остаётся доступен; ограничивается только сеть 192.168.0.0/24.</p><form method="post" action="/wireguard/client/action">{self._csrf()}<input type="hidden" name="client_id" value="{esc(identifier)}"><input type="hidden" name="action" value="{lan_action}"><div class="actions"><button class="{lan_class}" type="submit">{lan_label}</button></div></form></section>
        <section class="panel"><h2>QR</h2><div class="actions"><button class="secondary" type="button" data-wg-qr-url="/wireguard/client/{esc(identifier)}/qr" data-wg-qr-label="{esc(client_name(client))}">Показать QR</button></div></section>
  </aside>
</div>
{wireguard_qr_modal()}'''
        return page_shell(client_name(client), body)

    @staticmethod
    def _json_form(values, name, fields):
        try:
            payload = json.loads(form_value(values, name))
        except json.JSONDecodeError as exc:
            raise WgEasyApiError('JSON содержит синтаксическую ошибку.') from exc
        if not isinstance(payload, dict):
            raise WgEasyApiError('JSON должен быть объектом.')
        filtered = field_subset(payload, fields)
        if not filtered:
            raise WgEasyApiError('Не найдены редактируемые поля.')
        return filtered

    def create_client(self, values):
        name = form_value(values, 'client_name')
        if not name or len(name) > 128:
            raise WgEasyApiError('Укажите имя клиента до 128 символов.')
        result = self.api.create_client(name, form_value(values, 'expires_at'))
        return result.get('clientId') if isinstance(result, dict) else None

    def client_action(self, values):
        identifier = form_value(values, 'client_id')
        action = form_value(values, 'action')
        if action == 'enable':
            self.api.enable_client(identifier)
            return 'Клиент включён.'
        if action == 'disable':
            self.api.disable_client(identifier)
            return 'Клиент отключён.'
        if action == 'delete':
            client = self.api.client(identifier)
            address = client_address(client)
            self.api.delete_client(identifier)
            previous = load_lan_denies()
            if address in previous:
                previous.remove(address)
                save_lan_denies(previous)
                restart_lan_firewall()
            return 'Клиент удалён.'
        if action in ('deny-lan', 'allow-lan'):
            client = self.api.client(identifier)
            address = client_address(client)
            try:
                ipaddress.IPv4Address(address)
            except ipaddress.AddressValueError as error:
                raise WgEasyApiError('У клиента нет корректного IPv4-адреса.') from error
            previous = load_lan_denies()
            updated = set(previous)
            if action == 'deny-lan':
                updated.add(address)
            else:
                updated.discard(address)
            save_lan_denies(updated)
            try:
                restart_lan_firewall()
            except Exception:
                save_lan_denies(previous)
                restart_lan_firewall()
                raise
            return 'Доступ к LAN запрещён.' if action == 'deny-lan' else 'Доступ к LAN разрешён.'
        raise WgEasyApiError('Неизвестное действие клиента.')

    def update_client(self, values):
        identifier = form_value(values, 'client_id')
        self.api.update_client(identifier, self._json_form(values, 'client_json', CLIENT_UPDATE_FIELDS))
        return identifier

    def update_general(self, values):
        self.api.update_general(self._json_form(values, 'general_json', GENERAL_UPDATE_FIELDS))

    def update_interface(self, values):
        self.api.update_interface(self._json_form(values, 'interface_json', INTERFACE_UPDATE_FIELDS))
