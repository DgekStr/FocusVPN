import datetime as dt
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
import hashlib
from pathlib import Path
from urllib import error, request


CLASH_API_URL = os.environ.get('FOCUSVPN_HAPP_CLASH_API_URL', 'http://127.0.0.1:9090').rstrip('/')
USER_DIR = Path('/etc/sing-box-admin')
IDENTITY_LOCK = threading.Lock()
IDENTITY_CACHE = {'pid': None, 'cursor': None, 'records': {}, 'updated': 0.0}


class HappStatsError(RuntimeError):
    pass


def parse_connection_start(value):
    text = str(value).replace('Z', '+00:00')
    text = re.sub(r'(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$)', r'\1', text)
    started = dt.datetime.fromisoformat(text)
    if started.tzinfo is None:
        raise ValueError('Connection start must include timezone')
    return started


def format_datetime(value, missing='—'):
    if not value:
        return missing
    try:
        return parse_connection_start(value).astimezone(dt.timezone.utc).strftime('%d.%m.%Y %H:%M:%S')
    except (ValueError, TypeError):
        return missing


def peer_address(value):
    host, separator, port = value.rpartition(':')
    if not separator:
        raise ValueError('Missing peer port')
    return str(ipaddress.ip_address(host.strip('[]'))), int(port)


def journal_message(event):
    value = event.get('MESSAGE', '')
    if isinstance(value, list) and all(isinstance(item, int) and 0 <= item <= 255 for item in value):
        value = bytes(value).decode('utf-8', 'replace')
    if not isinstance(value, str):
        return ''
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)


def authenticated_peers(events, names, vip_names, inbound_tag, records=None):
    records = records if records is not None else {}
    sources = records.setdefault('sources', {})
    authenticated = records.setdefault('authenticated', {})
    pending_visits = records.setdefault('pending_visits', {})
    for event in events:
        message = journal_message(event)
        match = re.search(r'\[(\d+) [^\]]+\] inbound/vless\[' + re.escape(inbound_tag) + r'\]: (.*)', message)
        if not match:
            continue
        key = (str(event.get('_PID', '')), match.group(1))
        text = match.group(2)
        source_match = re.match(r'^inbound (?:packet )?connection from (.+)$', text)
        if source_match:
            try:
                host, port = peer_address(source_match.group(1))
                timestamp = int(event.get('__REALTIME_TIMESTAMP', 0)) / 1000000
            except (ValueError, TypeError):
                continue
            authenticated.pop(key, None)
            sources[key] = (host, port, timestamp)
        auth = re.match(r'^\[([^\]]+)\] inbound (?:packet )?connection to ', text)
        if auth:
            marker = auth.group(1)
            if marker in names:
                authenticated[key] = {'marker': marker, 'destination': text[auth.end():]}
            elif marker in vip_names:
                authenticated[key] = {'marker': marker, 'destination': text[auth.end():]}
            if key in authenticated and key in sources:
                host, port, timestamp = sources[key]
                destination_value = authenticated[key]['destination']
                connection_key = hashlib.sha256(json.dumps([key[0], key[1], timestamp, destination_value]).encode('utf-8')).hexdigest()
                pending_visits[connection_key] = {
                    'connection_key': connection_key,
                    'user_key': marker if marker in names else 'VIP',
                    'user_name': names.get(marker, 'VIP'),
                    'started_at': dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec='microseconds'),
                    'ip': host, 'source_port': port, 'destination': destination_value, 'network': 'UDP' if 'inbound packet connection to' in text else 'TCP',
                }
                while len(pending_visits) > 10000:
                    pending_visits.pop(next(iter(pending_visits)))
    while len(sources) > 10000:
        oldest = next(iter(sources))
        sources.pop(oldest)
        authenticated.pop(oldest, None)
    for key in list(authenticated):
        if key not in sources:
            authenticated.pop(key)
    peers = {}
    for key, auth_record in authenticated.items():
        if key not in sources:
            continue
        marker = auth_record['marker']
        if marker in names:
            identity = (marker, names[marker])
        elif marker in vip_names:
            identity = ('VIP', 'VIP')
        else:
            continue
        host, port, timestamp = sources[key]
        connection_key = hashlib.sha256(json.dumps([key[0], key[1], timestamp, auth_record['destination']]).encode('utf-8')).hexdigest()
        peers.setdefault((host, port), []).append({'user_key': identity[0], 'user_name': identity[1], 'started': timestamp, 'connection_key': connection_key, 'destination': auth_record['destination']})
    return peers


def connection_identity(value, peers):
    metadata = value.get('metadata') if isinstance(value.get('metadata'), dict) else {}
    try:
        host = str(ipaddress.ip_address(source_ip(metadata)))
        port = int(metadata.get('sourcePort'))
        started = parse_connection_start(value.get('start')).timestamp()
    except (ValueError, TypeError):
        return {'user_key': 'unknown', 'user_name': 'Не определён'}
    candidates = [item for item in peers.get((host, port), []) if abs(item['started'] - started) <= 15]
    identities = {(item['user_key'], item['user_name']) for item in candidates}
    if len(identities) != 1:
        return {'user_key': 'unknown', 'user_name': 'Не определён'}
    user_key, user_name = next(iter(identities))
    matching = [item for item in candidates if item['destination'] == destination(metadata)]
    if len(matching) == 1:
        nearest = matching[0]
    elif len(candidates) == 1:
        nearest = candidates[0]
    else:
        return {'user_key': user_key, 'user_name': user_name}
    return {'user_key': user_key, 'user_name': user_name, 'connection_key': nearest['connection_key'], 'started_at': dt.datetime.fromtimestamp(nearest['started'], dt.timezone.utc).isoformat(timespec='microseconds')}


def load_user_identities():
    try:
        registry = json.loads((USER_DIR / 'happ-users.json').read_text(encoding='utf-8'))
        vip = json.loads((USER_DIR / 'happ-vip.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}, set(), 'happ-vless-in'
    names = {'personal-' + item['id']: item['name'] for item in registry.get('users', [])}
    vip_names = {str(item.get('name') or index) for index, item in enumerate(vip.get('users', []))}
    return names, vip_names, vip.get('inbound_tag', 'happ-vless-in')


def journal_identities(connections, history_since=None):
    names, vip_names, inbound_tag = load_user_identities()
    with IDENTITY_LOCK:
        if time.monotonic() - IDENTITY_CACHE['updated'] < 2:
            return authenticated_peers([], names, vip_names, inbound_tag, IDENTITY_CACHE['records'])
        try:
            result = subprocess.run(['/usr/bin/systemctl', 'show', 'sing-box-happ-server', '-p', 'MainPID', '--value'], capture_output=True, text=True, timeout=2, check=False)
            pid = result.stdout.strip()
            if result.returncode or not pid or pid == '0':
                return {}
            if pid != IDENTITY_CACHE['pid']:
                IDENTITY_CACHE.update(pid=pid, cursor=None, records={})
            args = ['/usr/bin/journalctl', '_PID=' + pid, '-o', 'json', '--no-pager', '-n', '20000']
            if IDENTITY_CACHE['cursor']:
                args.append('--after-cursor=' + IDENTITY_CACHE['cursor'])
            else:
                timestamps = []
                for item in connections:
                    try:
                        timestamps.append(parse_connection_start(item.get('start')).timestamp())
                    except (ValueError, TypeError):
                        continue
                if history_since:
                    try:
                        timestamps.append(parse_connection_start(history_since).timestamp() - 300)
                    except ValueError:
                        pass
                earliest = min(timestamps, default=time.time() - 300) - 15
                args.extend(['--since', dt.datetime.fromtimestamp(earliest, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')])
            result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
            if result.returncode:
                IDENTITY_CACHE['cursor'] = None
                return {}
            events = []
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('__CURSOR'):
                    IDENTITY_CACHE['cursor'] = event['__CURSOR']
                if re.search(r'inbound (?:packet )?connection', journal_message(event)):
                    events.append(event)
            IDENTITY_CACHE['updated'] = time.monotonic()
            return authenticated_peers(events, names, vip_names, inbound_tag, IDENTITY_CACHE['records'])
        except (OSError, subprocess.SubprocessError):
            return {}


def summarize_users(items):
    grouped = {}
    for item in items:
        key = item['user_key']
        group = grouped.setdefault(key, {'user_name': item['user_name'], 'connections': 0, 'download_bytes': 0, 'upload_bytes': 0})
        group['connections'] += 1
        group['download_bytes'] += item['download_bytes']
        group['upload_bytes'] += item['upload_bytes']
    for group in grouped.values():
        group['download'] = format_bytes(group['download_bytes'])
        group['upload'] = format_bytes(group['upload_bytes'])
    return sorted(grouped.values(), key=lambda item: item['user_name'].casefold())


def rank_top_users(users, limit=5):
    ranked = sorted(
        users,
        key=lambda item: (-(item['download_bytes'] + item['upload_bytes']), item['user_name'].casefold()),
    )
    return ranked[:max(0, min(5, int(limit)))]


def acknowledge_history_visits(visits):
    with IDENTITY_LOCK:
        pending = IDENTITY_CACHE['records'].get('pending_visits', {})
        for visit in visits:
            if pending.get(visit['connection_key']) == visit:
                pending.pop(visit['connection_key'], None)


def format_bytes(value):
    try:
        amount = max(0, int(value or 0))
    except (TypeError, ValueError):
        amount = 0
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f'{amount} {unit}' if unit == 'B' else f'{amount:.1f} {unit}'
        amount /= 1024
    return '0 B'


def duration_seconds(value):
    if not value:
        return 0
    try:
        started = parse_connection_start(value)
    except ValueError:
        return 0
    return max(0, int((dt.datetime.now(dt.timezone.utc) - started).total_seconds()))


def format_duration_seconds(seconds):
    seconds = max(0, int(seconds or 0))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f'{hours} ч. {minutes} мин.'
    if minutes:
        return f'{minutes} мин. {seconds} сек.'
    return f'{seconds} сек.'


def duration(value):
    return format_duration_seconds(duration_seconds(value))


def source_ip(metadata):
    candidate = str(metadata.get('sourceIP') or '').strip()
    if candidate:
        return candidate
    source = str(metadata.get('source') or '').strip()
    if not source:
        return '—'
    try:
        return str(ipaddress.ip_address(source.rsplit(':', 1)[0].strip('[]')))
    except ValueError:
        return source


def destination(metadata):
    host = str(metadata.get('host') or metadata.get('destinationIP') or '').strip()
    port = metadata.get('destinationPort')
    if host and port:
        return f'{host}:{port}'
    return host or '—'


def connection_item(value, peers=None):
    metadata = value.get('metadata') if isinstance(value.get('metadata'), dict) else {}
    identity = connection_identity(value, peers or {})
    if 'connection_key' not in identity:
        try:
            started_at = parse_connection_start(value.get('start')).isoformat(timespec='microseconds')
        except ValueError:
            started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')
        identity['connection_key'] = 'live-' + str(value.get('id') or hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest())
        identity['started_at'] = started_at
    return {
        **identity,
        'id': str(value.get('id') or ''),
        'ip': source_ip(metadata),
        'source_port': metadata.get('sourcePort'),
        'destination': destination(metadata),
        'network': str(metadata.get('network') or value.get('network') or '—').upper(),
        'duration': duration(value.get('start')),
        'duration_seconds': duration_seconds(value.get('start')),
        'download': format_bytes(value.get('download')),
        'upload': format_bytes(value.get('upload')),
        'download_bytes': max(0, int(value.get('download') or 0)),
        'upload_bytes': max(0, int(value.get('upload') or 0)),
    }


def aggregate_connections(items):
    grouped = {}
    for item in items:
        ip = str(item.get('ip') or '—')
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            pass
        protocol = str(item.get('network') or '—').upper()
        key = (ip, protocol)
        group = grouped.setdefault(key, {
            'ip': ip, 'network': protocol, 'user_names': [], 'connections': 0,
            'duration_seconds': 0, 'download_bytes': 0, 'upload_bytes': 0,
            'latest_started': None, 'destination': '—',
        })
        name = str(item.get('user_name') or 'Не определён')
        if name not in group['user_names']:
            group['user_names'].append(name)
        group['connections'] += 1
        group['duration_seconds'] += max(0, int(item.get('duration_seconds') or 0))
        group['download_bytes'] += max(0, int(item.get('download_bytes') or 0))
        group['upload_bytes'] += max(0, int(item.get('upload_bytes') or 0))
        try:
            started = parse_connection_start(item.get('started_at'))
        except (ValueError, TypeError):
            started = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        if group['latest_started'] is None or started >= group['latest_started']:
            group['latest_started'] = started
            group['destination'] = str(item.get('destination') or '—')

    result = []
    for group in grouped.values():
        group['user_name'] = ', '.join(group.pop('user_names')) or 'Не определён'
        group.pop('latest_started')
        group['duration'] = format_duration_seconds(group['duration_seconds'])
        group['download'] = format_bytes(group['download_bytes'])
        group['upload'] = format_bytes(group['upload_bytes'])
        result.append(group)
    return sorted(result, key=lambda item: (item['ip'], item['network']))


def live_connections(include_visits=False, history_since=None):
    try:
        api_request = request.Request(f'{CLASH_API_URL}/connections', headers={'Accept': 'application/json'})
        with request.urlopen(api_request, timeout=3) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (error.HTTPError, error.URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HappStatsError('Статистика HAPP временно недоступна.') from exc
    connections = payload.get('connections') if isinstance(payload, dict) else None
    if not isinstance(connections, list):
        raise HappStatsError('Clash API вернул некорректные данные подключений.')
    connections = [item for item in connections if isinstance(item, dict)]
    peers = journal_identities(connections, history_since)
    items = [connection_item(item, peers) for item in connections]
    items.sort(key=lambda item: item['id'])
    users = summarize_users(items)
    payload = {
        'online_count': len(items),
        'download': format_bytes(sum(item['download_bytes'] for item in items)),
        'upload': format_bytes(sum(item['upload_bytes'] for item in items)),
        'connections': aggregate_connections(items),
        'users': users,
        'top_users': rank_top_users(users),
    }
    if include_visits:
        with IDENTITY_LOCK:
            payload['visits'] = list(IDENTITY_CACHE['records'].get('pending_visits', {}).values())
    return payload