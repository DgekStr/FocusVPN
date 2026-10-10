import datetime as dt
import json
import math
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SERVER_TYPES = ('vless', 'hysteria2', 'trojan', 'shadowsocks', 'socks')
MESSAGE_FIELDS = ('date', 'time', 'old', 'new', 'source', 'latency')
MAX_TEMPLATE_LENGTH = 1000
FAILOVER_STRIKES = 3
FAILOVER_RECHECK_SECONDS = 60
DEFAULT_MESSAGE_TEMPLATE = (
    '📢 VPN-шлюз: обновление статуса\n'
    '📅 {date} | 🕐 {time}\n'
    '🔁 Произошла смена шлюза:\n'
    '➡️ Было: {old}\n'
    '✅ Стало: {new} ({source})'
)
SOURCE_LABELS = {'manual': 'вручную', 'automatic': 'автовыбор', 'failover': 'недоступность шлюза', 'manual_mode': 'смена режима', 'test': 'test'}
GATEWAY_LABELS = {'ok': 'доступен', 'suspect': 'проверка не пройдена, идёт подтверждение', 'down': 'недоступен', 'unknown': 'результат проверки неопределён', 'paused': 'проверка приостановлена вне режима VLESS', 'skipped': 'проверка не требуется'}
DEFAULT_SETTINGS = {
    'enabled': False,
    'interval_minutes': 5,
    'auto_switch': False,
    'failover_enabled': False,
    'mattermost_enabled': False,
    'webhook_url': '',
    'message_template': DEFAULT_MESSAGE_TEMPLATE,
    'utc_offset': '+03:00',
}


def write_private_json(path, payload):
    path.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.vless-monitor-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_settings(directory):
    path = Path(directory) / 'vless-monitor-settings.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)
    if not isinstance(payload, dict):
        raise ValueError('Настройки проверки VLESS повреждены.')
    return {**DEFAULT_SETTINGS, **payload}


def validate_template(value):
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return DEFAULT_MESSAGE_TEMPLATE
    if len(text) > MAX_TEMPLATE_LENGTH:
        raise ValueError(f'Сообщение не должно превышать {MAX_TEMPLATE_LENGTH} символов.')
    unknown = sorted({name for name in re.findall(r'\{(\w+)\}', text) if name not in MESSAGE_FIELDS})
    if unknown:
        raise ValueError('Неизвестные подстановки в сообщении: ' + ', '.join('{' + name + '}' for name in unknown) + '.')
    return text


def offset_minutes(value):
    match = re.fullmatch(r'([+-])(\d{2}):(\d{2})', str(value or '').strip())
    if not match:
        raise ValueError('Часовой пояс укажите смещением от UTC, например +03:00.')
    total = (int(match[2]) * 60 + int(match[3])) * (-1 if match[1] == '-' else 1)
    if int(match[3]) not in (0, 15, 30, 45) or not -720 <= total <= 840:
        raise ValueError('Смещение часового пояса должно быть от -12:00 до +14:00.')
    return total


def validate_utc_offset(value):
    total = offset_minutes(value)
    return f'{"-" if total < 0 else "+"}{abs(total) // 60:02d}:{abs(total) % 60:02d}'


def render_message(template, event, utc_offset=DEFAULT_SETTINGS['utc_offset']):
    try:
        moment = dt.datetime.fromisoformat(event['at'])
    except (KeyError, TypeError, ValueError):
        moment = dt.datetime.now(dt.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    try:
        minutes = offset_minutes(utc_offset)
    except ValueError:
        minutes = offset_minutes(DEFAULT_SETTINGS['utc_offset'])
    moment = moment.astimezone(dt.timezone(dt.timedelta(minutes=minutes)))
    latency = event.get('latency_ms')
    values = {
        'date': moment.strftime('%d.%m.%Y'),
        'time': moment.strftime('%H:%M'),
        'old': str(event.get('old_route') or '—'),
        'new': str(event.get('new_route') or '—'),
        'source': SOURCE_LABELS.get(event.get('source'), str(event.get('source') or '—')),
        'latency': f'{latency:g} мс' if isinstance(latency, (int, float)) and not isinstance(latency, bool) else '—',
    }
    return re.sub(r'\{(\w+)\}', lambda match: values.get(match.group(1), match.group(0)), template or DEFAULT_MESSAGE_TEMPLATE)


def validate_settings(payload):
    try:
        interval = int(payload.get('interval_minutes', 5))
    except (ValueError, TypeError) as error:
        raise ValueError('Интервал проверки должен быть целым числом от 1 до 60 минут.') from error
    if not 1 <= interval <= 60:
        raise ValueError('Интервал проверки должен быть от 1 до 60 минут.')
    result = {**DEFAULT_SETTINGS, **payload, 'interval_minutes': interval}
    for key in ('enabled', 'auto_switch', 'failover_enabled', 'mattermost_enabled'):
        if not isinstance(result[key], bool):
            raise ValueError('Некорректное значение переключателя проверки VLESS.')
    webhook = str(result.get('webhook_url', '')).strip()
    if webhook:
        parsed = urlsplit(webhook)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Webhook должен быть HTTP(S) URL без логина, пароля и фрагмента.')
    if result['mattermost_enabled'] and not webhook:
        raise ValueError('Для уведомлений Mattermost укажите webhook URL.')
    result['webhook_url'] = webhook
    result['message_template'] = validate_template(result['message_template'])
    result['utc_offset'] = validate_utc_offset(result['utc_offset'])
    return result


def usable_latency(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def managed_servers(config):
    return [item for item in config.get('outbounds', []) if item.get('type') in SERVER_TYPES and item.get('tag')]


def fresh_probe(check, started):
    if check.get('state') not in ('success', 'error'):
        return False
    try:
        return dt.datetime.fromisoformat(check['checked_at']) >= started
    except (KeyError, TypeError, ValueError):
        return False


def fastest_available(checks, excluded):
    candidates = sorted((check['latency_ms'], check['tag']) for check in checks if check.get('tag') != excluded and check.get('state') == 'success' and usable_latency(check.get('latency_ms')))
    return (candidates[0][1], candidates[0][0]) if candidates else None


def fastest_vless(config, checks):
    available = {item['tag'] for item in config.get('outbounds', []) if item.get('type') == 'vless'}
    candidates = []
    for check in checks:
        latency = check.get('latency_ms')
        if check.get('tag') in available and check.get('state') == 'success' and usable_latency(latency):
            candidates.append((latency, check['tag']))
    if not candidates:
        return None
    candidates.sort()
    minimum = candidates[0][0]
    tied = [tag for latency, tag in candidates if latency == minimum]
    current = config.get('route', {}).get('final')
    return current if current in tied else tied[0]


def gateway_text(state, settings):
    if not settings.get('failover_enabled'):
        return 'Автопроверка доступности выключена.'
    label = GATEWAY_LABELS.get(state.get('gateway_state'))
    if label is None:
        return 'Автопроверка доступности включена; первая проверка ещё не выполнена.'
    try:
        checked = dt.datetime.fromisoformat(state['gateway_checked_at']).astimezone(dt.timezone.utc).strftime('%d.%m.%Y %H:%M:%S')
    except (KeyError, TypeError, ValueError):
        checked = '—'
    suffix = f' ({state.get("gateway_strikes", 0)}/{FAILOVER_STRIKES})' if state.get('gateway_state') == 'suspect' else ''
    return f'Шлюз по умолчанию {state.get("gateway_route") or "—"}: {label}{suffix}. Проверено {checked} UTC.'


def notify_mattermost(url, event, template=None, utc_offset=None):
    text = render_message(template, event, utc_offset or DEFAULT_SETTINGS['utc_offset'])
    request = Request(url, data=json.dumps({'text': text}, ensure_ascii=False).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=5) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('Mattermost returned a non-success status')


class VlessMonitor:
    def __init__(self, directory, load_config, queue_checks, check_states, switch_route, mode):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o750, exist_ok=True)
        settings_path = self.directory / 'vless-monitor-settings.json'
        if not settings_path.exists():
            write_private_json(settings_path, DEFAULT_SETTINGS)
        descriptor = os.open(self.directory / 'gateway-switches.jsonl', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.load_config = load_config
        self.queue_checks = queue_checks
        self.check_states = check_states
        self.switch_route = switch_route
        self.mode = mode
        self.lock = threading.Lock()
        self.wakeup = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None
        self.notifications = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mattermost')
        self.revision = 0
        self.state = self.read_state()
        write_private_json(self.directory / 'vless-monitor-state.json', self.state)
        self.next_cycle = time.monotonic()

    def read_state(self):
        try:
            saved = json.loads((self.directory / 'vless-monitor-state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        return {**saved, 'running': False, 'candidate': None, 'streak': 0, 'next_check_at': None, 'gateway_route': None, 'gateway_state': None, 'gateway_strikes': 0, 'gateway_checked_at': None}

    def status(self):
        with self.lock:
            state = json.loads(json.dumps(self.state))
        try:
            settings = load_settings(self.directory)
        except (OSError, ValueError):
            settings = DEFAULT_SETTINGS
        state['gateway_text'] = gateway_text(state, settings)
        return state

    def save_settings(self, payload):
        settings = validate_settings(payload)
        with self.lock:
            write_private_json(self.directory / 'vless-monitor-settings.json', settings)
            self.revision += 1
            self.state.update(candidate=None, streak=0)
            if not settings['failover_enabled']:
                self.state.update(gateway_route=None, gateway_state=None, gateway_strikes=0, gateway_checked_at=None)
            self.next_cycle = time.monotonic()
        self.wakeup.set()
        return settings

    def journal(self):
        try:
            lines = (self.directory / 'gateway-switches.jsonl').read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            return []
        result = []
        for line in lines[-100:]:
            try:
                result.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(result))

    def append_event(self, event):
        event = {'at': dt.datetime.now(dt.timezone.utc).isoformat(), **event}
        with self.lock:
            self.directory.mkdir(parents=True, mode=0o750, exist_ok=True)
            path = self.directory / 'gateway-switches.jsonl'
            try:
                lines = path.read_text(encoding='utf-8').splitlines()[-499:]
            except FileNotFoundError:
                lines = []
            descriptor, temporary = tempfile.mkstemp(prefix='.gateway-log-', dir=self.directory)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write('\n'.join(lines + [json.dumps(event, ensure_ascii=False)]) + '\n')
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        return event

    def record_switch(self, old_route, new_route, source, latency_ms=None):
        if old_route == new_route:
            return
        self.reset_candidate()

        event = self.append_event({'event': 'route_changed', 'old_route': old_route, 'new_route': new_route, 'source': source, 'latency_ms': latency_ms})
        settings = load_settings(self.directory)
        if settings['mattermost_enabled']:
            self.notifications.submit(self.deliver_notification, settings['webhook_url'], event)

    def reset_candidate(self):
        with self.lock:
            self.revision += 1
            self.state.update(candidate=None, streak=0)

    def deliver_notification(self, url, event):
        try:
            settings = load_settings(self.directory)
            notify_mattermost(url, event, settings['message_template'], settings['utc_offset'])
            self.append_event({'event': 'mattermost_sent', 'new_route': event['new_route']})
        except Exception:
            self.append_event({'event': 'mattermost_failed', 'new_route': event['new_route'], 'message': 'Уведомление не доставлено; смена маршрута не отменена.'})

    def test_notification(self, current_route):
        settings = load_settings(self.directory)
        if not settings['webhook_url']:
            raise ValueError('Сначала сохраните webhook Mattermost.')
        event = self.append_event({'event': 'mattermost_test_queued', 'old_route': current_route, 'new_route': current_route, 'source': 'test'})
        self.notifications.submit(self.deliver_notification, settings['webhook_url'], event)

    def run_cycle(self):
        try:
            return self.check_cycle()
        except Exception:
            with self.lock:
                self.state.update(candidate=None, streak=0)
            raise
        finally:
            with self.lock:
                self.state['running'] = False

    def check_cycle(self):
        settings = load_settings(self.directory)
        if settings['failover_enabled']:
            try:
                self.failover_cycle()
            except Exception:
                with self.lock:
                    route = self.state.get('gateway_route')
                self.set_gateway(route, 'unknown')
                self.append_event({'event': 'monitor_error', 'message': 'Ошибка проверки доступности шлюза; повтор будет выполнен по расписанию.'})
            with self.lock:
                confirming = self.state.get('gateway_state') == 'suspect'
            if confirming:
                return
        if settings['enabled']:
            self.selection_cycle()

    def set_gateway(self, route, state, strikes=0):
        with self.lock:
            self.state.update(gateway_route=route, gateway_state=state, gateway_strikes=strikes, gateway_checked_at=dt.datetime.now(dt.timezone.utc).isoformat())

    def probe(self, tags):
        started = dt.datetime.now(dt.timezone.utc)
        for future in self.queue_checks(self.load_config(), tags):
            try:
                future.result(timeout=80)
            except Exception:
                continue
        return {item['tag']: item for item in self.check_states(self.load_config())['checks'] if item.get('tag') in tags and fresh_probe(item, started)}

    def failover_cycle(self):
        config = self.load_config()
        route = config.get('route', {}).get('final')
        tags = [item['tag'] for item in managed_servers(config)]
        with self.lock:
            revision = self.revision
            self.state['running'] = True
        if route not in tags:
            self.set_gateway(route, 'skipped')
            return
        if self.mode() != 'vless':
            self.set_gateway(route, 'paused')
            return
        state = self.probe([route]).get(route, {}).get('state')
        if state == 'success':
            self.set_gateway(route, 'ok')
            return
        if state != 'error':
            self.set_gateway(route, 'unknown')
            return
        with self.lock:
            known = self.state.get('gateway_route') == route
            was_down = known and self.state.get('gateway_state') == 'down'
            strikes = (self.state.get('gateway_strikes', 0) if known else 0) + 1
        if not was_down and strikes < FAILOVER_STRIKES:
            self.set_gateway(route, 'suspect', strikes)
            self.append_event({'event': 'gateway_check_failed', 'old_route': route, 'message': f'Проверка шлюза по умолчанию не прошла ({strikes}/{FAILOVER_STRIKES}); повтор через {FAILOVER_RECHECK_SECONDS} с.'})
            return
        self.set_gateway(route, 'down', strikes)
        if not was_down:
            self.append_event({'event': 'gateway_down', 'old_route': route, 'message': f'Шлюз по умолчанию недоступен: {FAILOVER_STRIKES} проверки подряд не прошли.'})
        others = [tag for tag in tags if tag != route]
        winner = fastest_available(list(self.probe(others).values()), route) if others else None
        if winner is None:
            if not was_down:
                self.append_event({'event': 'failover_no_candidate', 'old_route': route, 'message': 'Рабочий сервер для перехода не найден; прежний шлюз сохранён.'})
            return
        tag, latency = winner
        servers = managed_servers(self.load_config())
        try:
            switched = self.switch_route(tag, route, servers, latency, revision, 'failover')
        except Exception:
            self.append_event({'event': 'switch_failed', 'old_route': route, 'new_route': tag, 'message': 'Применение маршрута не удалось; прежний маршрут сохранён.', 'latency_ms': latency})
            return
        if switched:
            self.set_gateway(tag, 'ok')

    def selection_cycle(self):
        settings = load_settings(self.directory)
        snapshot = self.load_config()
        tags = [item['tag'] for item in snapshot.get('outbounds', []) if item.get('type') == 'vless']
        with self.lock:
            revision = self.revision
            self.state['running'] = True
        futures = self.queue_checks(snapshot, tags)
        for future in futures:
            future.result(timeout=80)
        current = self.load_config()
        snapshot_servers = [item for item in snapshot.get('outbounds', []) if item.get('type') == 'vless']
        current_servers = [item for item in current.get('outbounds', []) if item.get('type') == 'vless']
        checks = self.check_states(current)['checks']
        winner = fastest_vless(current, checks)
        can_select = settings['auto_switch'] and self.mode() == 'vless'
        with self.lock:
            settings_changed = revision != self.revision
            stale = snapshot_servers != current_servers or snapshot.get('route', {}).get('final') != current.get('route', {}).get('final') or settings_changed
            if stale or not can_select or winner is None:
                self.state.update(candidate=None, streak=0)
            elif self.state.get('candidate') == winner:
                self.state['streak'] += 1
            else:
                self.state.update(candidate=winner, streak=1)
            self.state['last_checked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            ready = self.state.get('streak', 0) >= 3 and winner != current.get('route', {}).get('final')
        self.append_event({'event': 'check_cycle', 'winner': winner, 'streak': self.status().get('streak', 0), 'checks': [{'tag': item['tag'], 'state': item['state'], 'latency_ms': item.get('latency_ms')} for item in checks if item['tag'] in tags]})
        if ready:
            winner_latency = next(item['latency_ms'] for item in checks if item['tag'] == winner)
            try:
                switched = self.switch_route(winner, current['route']['final'], current_servers, winner_latency, revision)
                with self.lock:
                    self.state.update(candidate=None, streak=0)
            except Exception:
                with self.lock:
                    self.state.update(candidate=None, streak=0)
                self.append_event({'event': 'switch_failed', 'new_route': winner, 'message': 'Применение маршрута не удалось; прежний маршрут сохранён.', 'latency_ms': winner_latency})

    def run(self):
        while not self.stop_event.is_set():
            settings = load_settings(self.directory)
            active = settings['enabled'] or settings['failover_enabled']
            wait_seconds = max(0, self.next_cycle - time.monotonic()) if active else None
            if self.wakeup.wait(timeout=wait_seconds):
                self.wakeup.clear()
                continue
            if self.stop_event.is_set():
                break
            try:
                self.run_cycle()
            except Exception:
                with self.lock:
                    self.state.update(candidate=None, streak=0)
                self.append_event({'event': 'monitor_error', 'message': 'Ошибка цикла проверки VLESS; повтор будет выполнен по расписанию.'})
            finally:
                settings = load_settings(self.directory)
                with self.lock:
                    delay = settings['interval_minutes'] * 60
                    if settings['failover_enabled'] and self.state.get('gateway_state') == 'suspect':
                        delay = min(delay, FAILOVER_RECHECK_SECONDS)
                    self.state['running'] = False
                    self.next_cycle = time.monotonic() + delay
                    self.state['next_check_at'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)).isoformat() if settings['enabled'] or settings['failover_enabled'] else None
                    write_private_json(self.directory / 'vless-monitor-state.json', self.state)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='vless-monitor', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup.set()
        self.notifications.shutdown(wait=False, cancel_futures=True)