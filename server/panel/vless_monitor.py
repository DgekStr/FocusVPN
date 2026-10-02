import datetime as dt
import json
import math
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_SETTINGS = {
    'enabled': False,
    'interval_minutes': 5,
    'auto_switch': False,
    'mattermost_enabled': False,
    'webhook_url': '',
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


def validate_settings(payload):
    try:
        interval = int(payload.get('interval_minutes', 5))
    except (ValueError, TypeError) as error:
        raise ValueError('Интервал проверки должен быть целым числом от 1 до 60 минут.') from error
    if not 1 <= interval <= 60:
        raise ValueError('Интервал проверки должен быть от 1 до 60 минут.')
    result = {**DEFAULT_SETTINGS, **payload, 'interval_minutes': interval}
    for key in ('enabled', 'auto_switch', 'mattermost_enabled'):
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
    return result


def fastest_vless(config, checks):
    available = {item['tag'] for item in config.get('outbounds', []) if item.get('type') == 'vless'}
    candidates = []
    for check in checks:
        latency = check.get('latency_ms')
        if check.get('tag') in available and check.get('state') == 'success' and isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency) and latency >= 0:
            candidates.append((latency, check['tag']))
    if not candidates:
        return None
    candidates.sort()
    minimum = candidates[0][0]
    tied = [tag for latency, tag in candidates if latency == minimum]
    current = config.get('route', {}).get('final')
    return current if current in tied else tied[0]


def notify_mattermost(url, event):
    text = f'FocusVPN: шлюз изменён {event["old_route"]} -> {event["new_route"]} ({event["source"]}).'
    if event.get('latency_ms') is not None:
        text += f' Задержка: {event["latency_ms"]} мс.'
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
        return {**saved, 'running': False, 'candidate': None, 'streak': 0, 'next_check_at': None}

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def save_settings(self, payload):
        settings = validate_settings(payload)
        with self.lock:
            write_private_json(self.directory / 'vless-monitor-settings.json', settings)
            self.revision += 1
            self.state.update(candidate=None, streak=0)
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
            notify_mattermost(url, event)
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
        if not settings['enabled']:
            return
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
            wait_seconds = max(0, self.next_cycle - time.monotonic()) if settings['enabled'] else None
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
                    self.state['running'] = False
                    self.next_cycle = time.monotonic() + settings['interval_minutes'] * 60
                    self.state['next_check_at'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=settings['interval_minutes'])).isoformat() if settings['enabled'] else None
                    write_private_json(self.directory / 'vless-monitor-state.json', self.state)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='vless-monitor', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup.set()
        self.notifications.shutdown(wait=False, cancel_futures=True)