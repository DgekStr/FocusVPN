import datetime as dt
import html
import ipaddress
import os
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path


VIRTUAL_PREFIXES = ('veth', 'docker', 'br-', 'virbr', 'wg', 'tun', 'tap', 'tailscale')


def format_uptime(seconds):
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours = remainder // 3600
    if days:
        return f'{days}дн {hours}ч'
    minutes = remainder // 60
    return f'{hours}ч {minutes}м'


def format_decimal(value, precision=1):
    return f'{float(value):,.{precision}f}'.replace(',', ' ').replace('.', ',')


def parse_cpu_stat(content):
    line = next((item for item in content.splitlines() if item.startswith('cpu ')), '')
    values = [int(value) for value in line.split()[1:]]
    if len(values) < 4:
        raise ValueError('Invalid /proc/stat CPU counters')
    total = sum(values[:8])
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def parse_net_dev(content):
    counters = {}
    for line in content.splitlines()[2:]:
        if ':' not in line:
            continue
        name, values = line.split(':', 1)
        numbers = values.split()
        if len(numbers) >= 9:
            counters[name.strip()] = (int(numbers[0]), int(numbers[8]))
    return counters


def selected_interfaces(counters, net_root='/sys/class/net'):
    candidates = [name for name in counters if name != 'lo' and not name.startswith(VIRTUAL_PREFIXES)]
    physical = [name for name in candidates if (Path(net_root) / name / 'device').exists()]
    return sorted(physical or candidates)


def read_os_version(path='/etc/os-release'):
    try:
        values = {}
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition('=')
            if separator:
                values[key] = value.strip().strip('"')
        return values.get('PRETTY_NAME') or values.get('NAME', 'Linux')
    except OSError:
        return 'Linux'


def route_interface():
    try:
        result = subprocess.run(['/usr/sbin/ip', '-o', '-4', 'route', 'show', 'default'], capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    match = re.search(r'\bdev\s+(\S+)', result.stdout)
    return match.group(1) if match else ''


def interface_ipv4(interface):
    if not interface:
        return ''
    try:
        result = subprocess.run(['/usr/sbin/ip', '-o', '-4', 'addr', 'show', 'dev', interface, 'scope', 'global'], capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    match = re.search(r'\binet\s+(\d+(?:\.\d+){3})/', result.stdout)
    if not match:
        return ''
    try:
        return str(ipaddress.IPv4Address(match.group(1)))
    except ipaddress.AddressValueError:
        return ''


def server_identity(counters=None):
    counters = parse_net_dev(Path('/proc/net/dev').read_text()) if counters is None else counters
    interfaces = selected_interfaces(counters)
    preferred = route_interface()
    address = interface_ipv4(preferred) if preferred in interfaces else ''
    if not address:
        address = next((value for name in interfaces if (value := interface_ipv4(name))), '')
    return {'ip': address or '—', 'os': read_os_version(), 'interface': preferred if preferred in interfaces else (interfaces[0] if interfaces else '—')}


def read_raw():
    uptime = float(Path('/proc/uptime').read_text().split()[0])
    cpu_total, cpu_idle = parse_cpu_stat(Path('/proc/stat').read_text())
    counters = parse_net_dev(Path('/proc/net/dev').read_text())
    interfaces = selected_interfaces(counters)
    rx_bytes = sum(counters[name][0] for name in interfaces)
    tx_bytes = sum(counters[name][1] for name in interfaces)
    return {'uptime': uptime, 'cpu_total': cpu_total, 'cpu_idle': cpu_idle, 'rx_bytes': rx_bytes, 'tx_bytes': tx_bytes}


class ServerMetrics:
    def __init__(self, path='/mnt/stat/server-metrics.sqlite3', clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.clock = clock
        self.lock = threading.Lock()
        with self.connect() as database:
            database.execute('''
                CREATE TABLE IF NOT EXISTS samples (
                    timestamp REAL PRIMARY KEY,
                    uptime REAL NOT NULL,
                    cpu_total INTEGER NOT NULL,
                    cpu_idle INTEGER NOT NULL,
                    cpu_percent REAL NOT NULL,
                    rx_bytes INTEGER NOT NULL,
                    tx_bytes INTEGER NOT NULL,
                    rx_rate REAL NOT NULL,
                    tx_rate REAL NOT NULL
                )
            ''')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.path, timeout=5)
        database.row_factory = sqlite3.Row
        database.execute('PRAGMA busy_timeout=5000')
        try:
            with database:
                yield database
        finally:
            database.close()

    def sample(self, raw=None, now=None):
        raw = read_raw() if raw is None else raw
        timestamp = self.clock() if now is None else float(now)
        with self.lock, self.connect() as database:
            previous = database.execute('SELECT * FROM samples ORDER BY timestamp DESC LIMIT 1').fetchone()
            if previous and raw['uptime'] < previous['uptime']:
                database.execute('DELETE FROM samples')
                previous = None
            cpu = rx_rate = tx_rate = 0.0
            if previous and raw['uptime'] >= previous['uptime']:
                elapsed = max(timestamp - previous['timestamp'], 0.01)
                total_delta = raw['cpu_total'] - previous['cpu_total']
                idle_delta = raw['cpu_idle'] - previous['cpu_idle']
                if total_delta > 0:
                    cpu = max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100))
                rx_rate = max(0, raw['rx_bytes'] - previous['rx_bytes']) / elapsed
                tx_rate = max(0, raw['tx_bytes'] - previous['tx_bytes']) / elapsed
            database.execute('''
                INSERT OR REPLACE INTO samples
                (timestamp,uptime,cpu_total,cpu_idle,cpu_percent,rx_bytes,tx_bytes,rx_rate,tx_rate)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (timestamp, raw['uptime'], raw['cpu_total'], raw['cpu_idle'], cpu, raw['rx_bytes'], raw['tx_bytes'], rx_rate, tx_rate))
            database.execute('DELETE FROM samples WHERE timestamp < ?', (timestamp - 24 * 60 * 60,))
        return self.snapshot(now=timestamp)

    def snapshot(self, now=None):
        timestamp = self.clock() if now is None else float(now)
        with self.connect() as database:
            latest = database.execute('SELECT * FROM samples ORDER BY timestamp DESC LIMIT 1').fetchone()
            peaks = database.execute('''
                SELECT MAX(cpu_percent) AS cpu,MAX(rx_rate) AS rx_rate,MAX(tx_rate) AS tx_rate,MIN(timestamp) AS observed_since
                FROM samples WHERE timestamp >= ?
            ''', (timestamp - 24 * 60 * 60,)).fetchone()
        if latest is None:
            current = None
        else:
            current = dict(latest)
        identity = server_identity()
        return {'current': current, 'peaks': dict(peaks) if peaks else {}, 'identity': identity}


def collect_metrics(stop, metrics, interval=5):
    while not stop.is_set():
        try:
            metrics.sample()
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
            pass
        stop.wait(interval)


def render_metrics_panel(snapshot):
        snapshot = snapshot or {}
        current = snapshot.get('current') or {}
        peaks = snapshot.get('peaks') or {}
        identity = snapshot.get('identity') or {}
        uptime = format_uptime(current.get('uptime', 0)) if current else 'Сбор…'
        cpu = peaks.get('cpu')
        cpu_value = format_decimal(cpu) + '%' if cpu is not None else '—'
        since = peaks.get('observed_since')
        since_label = dt.datetime.fromtimestamp(since).astimezone().strftime('%d.%m, %H:%M') if since else 'Сбор начат'
        rx_peak = max(0, float(peaks.get('rx_rate') or 0))
        tx_peak = max(0, float(peaks.get('tx_rate') or 0))
        lan_peak = max(rx_peak, tx_peak) * 8 / 1_000_000
        lan_value = format_decimal(lan_peak) + ' Мбит/с' if current else '—'
        rx_mb = format_decimal((current.get('rx_bytes') or 0) / 1_000_000, 0) if current else '—'
        tx_mb = format_decimal((current.get('tx_bytes') or 0) / 1_000_000, 0) if current else '—'
        ip = html.escape(str(identity.get('ip') or '—'))
        os_name = html.escape(str(identity.get('os') or 'Linux'))
        return f'''<section class="server-metrics-overview" aria-label="Обзор сервера">
    <div class="server-metrics-heading"><h2>Обзор сервера</h2><p>{ip}<span class="dot-separator">·</span>{os_name}</p></div>
    <div class="server-metrics-grid">
        <article class="server-metric-card"><div class="server-metric-label">Время работы</div><strong>{uptime}</strong><small>С момента загрузки ОС</small></article>
        <article class="server-metric-card"><div class="server-metric-label">Пиковая загрузка CPU</div><strong>{cpu_value}</strong><small>Наблюдение с {since_label}</small></article>
        <article class="server-metric-card"><div class="server-metric-label">Пиковая нагрузка LAN</div><strong>{lan_value}</strong><small>Максимум RX / TX · до 24 ч</small></article>
        <article class="server-metric-card server-network-total"><div class="server-metric-label">Сетевой трафик</div><div><strong>{rx_mb}</strong><span>МБ</span></div><div><strong>{tx_mb}</strong><span>МБ</span></div><small>Получено / отправлено · счётчики ОС</small></article>
    </div>
</section>'''