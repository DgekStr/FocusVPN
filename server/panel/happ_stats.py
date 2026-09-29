import datetime as dt
import ipaddress
import json
import os
from urllib import error, request


CLASH_API_URL = os.environ.get('FOCUSVPN_HAPP_CLASH_API_URL', 'http://127.0.0.1:9090').rstrip('/')


class HappStatsError(RuntimeError):
    pass


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


def duration(value):
    if not value:
        return '—'
    try:
        started = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return '—'
    seconds = max(0, int((dt.datetime.now(dt.timezone.utc) - started).total_seconds()))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f'{hours} ч. {minutes} мин.'
    if minutes:
        return f'{minutes} мин. {seconds} сек.'
    return f'{seconds} сек.'


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


def connection_item(value):
    metadata = value.get('metadata') if isinstance(value.get('metadata'), dict) else {}
    return {
        'id': str(value.get('id') or ''),
        'ip': source_ip(metadata),
        'destination': destination(metadata),
        'network': str(metadata.get('network') or value.get('network') or '—').upper(),
        'duration': duration(value.get('start')),
        'download': format_bytes(value.get('download')),
        'upload': format_bytes(value.get('upload')),
        'download_bytes': max(0, int(value.get('download') or 0)),
        'upload_bytes': max(0, int(value.get('upload') or 0)),
    }


def live_connections():
    try:
        api_request = request.Request(f'{CLASH_API_URL}/connections', headers={'Accept': 'application/json'})
        with request.urlopen(api_request, timeout=3) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (error.HTTPError, error.URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HappStatsError('Статистика HAPP временно недоступна.') from exc
    connections = payload.get('connections') if isinstance(payload, dict) else None
    if not isinstance(connections, list):
        raise HappStatsError('Clash API вернул некорректные данные подключений.')
    items = [connection_item(item) for item in connections if isinstance(item, dict)]
    items.sort(key=lambda item: item['id'])
    return {
        'online_count': len(items),
        'download': format_bytes(sum(item['download_bytes'] for item in items)),
        'upload': format_bytes(sum(item['upload_bytes'] for item in items)),
        'connections': items,
    }