import datetime as dt
import io
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from happ_stats import format_bytes, format_datetime
from pathlib import Path
from xml.sax.saxutils import quoteattr

XML_INVALID_CHARACTERS = re.compile('[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def utc_text(value=None):
    return (value or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat(timespec='microseconds')


def xml_attributes(items):
    return ''.join(' ' + name + '=' + quoteattr(XML_INVALID_CHARACTERS.sub('', str(value))) for name, value in items if value is not None and value != '')


class HappHistory:
    def __init__(self, directory='/mnt/stat'):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'happ-stat.sqlite3'
        self.lock = threading.Lock()
        self.registered_user_keys = None
        with self.connect() as database:
            database.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES ('retention_days', '60');
                CREATE TABLE IF NOT EXISTS connections (
                    connection_key TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    source_ip TEXT NOT NULL,
                    source_port INTEGER,
                    destination TEXT NOT NULL,
                    network TEXT NOT NULL,
                    live_id TEXT,
                    download_bytes INTEGER,
                    upload_bytes INTEGER,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS connection_user_time ON connections (user_key, started_at);
                CREATE INDEX IF NOT EXISTS connection_last_seen ON connections (last_seen_at);
                CREATE INDEX IF NOT EXISTS connection_live_id ON connections (live_id);
                CREATE TABLE IF NOT EXISTS user_traffic_totals (
                    user_key TEXT PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    download_bytes INTEGER NOT NULL DEFAULT 0,
                    upload_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS download_rate_samples (
                    sampled_at INTEGER NOT NULL,
                    user_key TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    bytes_per_second REAL,
                    PRIMARY KEY (sampled_at,user_key)
                );
                CREATE TABLE IF NOT EXISTS download_rate_counters (
                    live_id TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    download_bytes INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS traffic_reset_baselines (
                    connection_key TEXT PRIMARY KEY,
                    live_id TEXT,
                    user_key TEXT NOT NULL,
                    download_bytes INTEGER NOT NULL DEFAULT 0,
                    upload_bytes INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rate_user_time ON download_rate_samples (user_key,sampled_at);
                CREATE INDEX IF NOT EXISTS reset_baseline_live_id ON traffic_reset_baselines (live_id);
            ''')
            for table, column, definition in (
                ('download_rate_samples', 'upload_bytes_per_second', 'REAL'),
                ('download_rate_counters', 'upload_bytes', 'INTEGER'),
            ):
                columns = {row['name'] for row in database.execute('PRAGMA table_info(' + table + ')')}
                if column not in columns:
                    database.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + definition)
            initialized = database.execute("SELECT 1 FROM settings WHERE key='lifetime_totals_initialized'").fetchone()
            if not initialized:
                database.execute('''
                    INSERT OR IGNORE INTO user_traffic_totals (user_key,user_name,download_bytes,upload_bytes)
                    SELECT user_key,MAX(user_name),COALESCE(SUM(download_bytes),0),COALESCE(SUM(upload_bytes),0)
                    FROM connections WHERE user_key!='unknown' GROUP BY user_key
                ''')
                database.execute("INSERT OR REPLACE INTO settings VALUES ('lifetime_totals_initialized','1')")
        self.secure_files()

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.path, timeout=5)
        database.row_factory = sqlite3.Row
        database.execute('PRAGMA busy_timeout=5000')
        database.execute('PRAGMA secure_delete=ON')
        try:
            with database:
                yield database
        finally:
            database.close()

    def secure_files(self):
        for path in self.directory.glob('happ-stat.sqlite3*'):
            os.chmod(path, 0o600)

    def retention_days(self):
        with self.connect() as database:
            return int(database.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0])

    def set_retention(self, days):
        try:
            parsed = int(days)
        except (TypeError, ValueError) as error:
            raise ValueError('Срок хранения должен быть целым числом дней.') from error
        if not 1 <= parsed <= 3650:
            raise ValueError('Срок хранения должен быть от 1 до 3650 дней.')
        with self.lock, self.connect() as database:
            database.execute("UPDATE settings SET value=? WHERE key='retention_days'", (str(parsed),))
        return self.cleanup()

    def cleanup(self, at=None):
        cutoff = utc_text((at or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=self.retention_days()))
        with self.lock, self.connect() as database:
            cursor = database.execute('DELETE FROM connections WHERE last_seen_at < ?', (cutoff,))
            count = cursor.rowcount
            database.execute('DELETE FROM traffic_reset_baselines WHERE updated_at < ?', (cutoff,))
        if count:
            with self.connect() as database:
                database.execute('VACUUM')
        self.secure_files()
        return count

    @staticmethod
    def add_user_traffic(database, user_key, user_name, download_delta, upload_delta):
        if user_key == 'unknown':
            database.execute("UPDATE user_traffic_totals SET download_bytes=MAX(0,download_bytes+MIN(0,?)),upload_bytes=MAX(0,upload_bytes+MIN(0,?)) WHERE user_key='unknown'", (download_delta, upload_delta))
            return
        database.execute('''
            INSERT INTO user_traffic_totals (user_key,user_name,download_bytes,upload_bytes)
            VALUES (?,?,MAX(0,?),MAX(0,?))
            ON CONFLICT(user_key) DO UPDATE SET
                user_name=CASE WHEN excluded.user_name!='Не определён' THEN excluded.user_name ELSE user_traffic_totals.user_name END,
                download_bytes=MAX(0,user_traffic_totals.download_bytes+?),
                upload_bytes=MAX(0,user_traffic_totals.upload_bytes+?)
        ''', (user_key, user_name, download_delta, upload_delta, download_delta, upload_delta))

    def retain_registered_users(self, users):
        keys = frozenset({'VIP', 'unknown'} | {'personal-' + user['id'] for user in users})
        with self.lock:
            if keys == self.registered_user_keys:
                return 0
            removed = 0
            placeholders = ','.join('?' for key in keys)
            with self.connect() as database:
                for table in ('connections', 'user_traffic_totals', 'download_rate_samples', 'download_rate_counters', 'traffic_reset_baselines'):
                    removed += database.execute('DELETE FROM ' + table + ' WHERE user_key NOT IN (' + placeholders + ')', tuple(keys)).rowcount
            self.registered_user_keys = keys
        self.secure_files()
        return removed

    def reconcile_authenticated_visits(self, database, visits):
        authenticated = {}
        for visit in visits:
            if not visit.get('connection_key') or visit.get('user_key') in (None, '', 'unknown') or visit.get('source_port') is None:
                continue
            try:
                started = dt.datetime.fromisoformat(visit['started_at'])
            except (KeyError, TypeError, ValueError):
                continue
            peer = (visit.get('ip'), str(visit['source_port']), visit.get('destination'))
            authenticated.setdefault(peer, []).append((visit, started))
        if not authenticated:
            return
        for row in database.execute("SELECT * FROM connections WHERE user_key='unknown'").fetchall():
            try:
                started = dt.datetime.fromisoformat(row['started_at'])
            except (TypeError, ValueError):
                continue
            peer = (row['source_ip'], str(row['source_port']), row['destination'])
            candidates = [visit for visit, timestamp in authenticated.get(peer, []) if abs((timestamp - started).total_seconds()) <= 15]
            if len({(visit['connection_key'], visit['user_key']) for visit in candidates}) != 1:
                continue
            visit = candidates[0]
            known = database.execute('SELECT * FROM connections WHERE connection_key=?', (visit['connection_key'],)).fetchone()
            if known is not None and (known['download_bytes'] is not None or known['upload_bytes'] is not None):
                continue
            download, upload = row['download_bytes'] or 0, row['upload_bytes'] or 0
            self.add_user_traffic(database, 'unknown', row['user_name'], -download, -upload)
            self.add_user_traffic(database, visit['user_key'], visit['user_name'], download, upload)
            if known is not None:
                database.execute('DELETE FROM connections WHERE connection_key=?', (known['connection_key'],))
            database.execute('UPDATE connections SET connection_key=?,user_key=?,user_name=?,started_at=? WHERE connection_key=?', (visit['connection_key'], visit['user_key'], visit['user_name'], visit['started_at'], row['connection_key']))

    def ingest(self, payload, at=None):
        observed = utc_text(at)
        with self.lock, self.connect() as database:
            if self.registered_user_keys is not None:
                payload = {**payload, **{name: [item for item in payload.get(name, []) if item.get('user_key', 'unknown') in self.registered_user_keys] for name in ('connections', 'visits', 'users', 'traffic_samples')}}
            items = payload.get('connections', [])
            visits = payload.get('visits', [])
            self.record_download_rates(database, payload, observed)
            database.execute('UPDATE connections SET active=0 WHERE active=1')
            self.reconcile_authenticated_visits(database, visits)
            for visit in visits:
                key = visit.get('connection_key')
                if not key or not visit.get('started_at'):
                    continue
                if database.execute('SELECT 1 FROM traffic_reset_baselines WHERE connection_key=?', (key,)).fetchone() is not None:
                    continue
                database.execute('''
                    INSERT INTO connections (connection_key,user_key,user_name,started_at,last_seen_at,source_ip,source_port,destination,network,download_bytes,upload_bytes,active)
                    VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,0)
                    ON CONFLICT(connection_key) DO UPDATE SET
                        user_key=CASE WHEN excluded.user_key!='unknown' THEN excluded.user_key ELSE connections.user_key END,
                        user_name=CASE WHEN excluded.user_key!='unknown' THEN excluded.user_name ELSE connections.user_name END
                ''', (key, visit.get('user_key', 'unknown'), visit.get('user_name', 'Не определён'), visit['started_at'], visit['started_at'], visit.get('ip', '—'), visit.get('source_port'), visit.get('destination', '—'), visit.get('network', '')))
            for item in items:
                key = item.get('connection_key')
                if not key or not item.get('started_at'):
                    continue
                previous = database.execute('SELECT connection_key,user_key,user_name,download_bytes,upload_bytes FROM connections WHERE connection_key=?', (key,)).fetchone()
                live_id = str(item.get('id') or '')
                legacy = database.execute('SELECT connection_key,user_key,user_name,download_bytes,upload_bytes FROM connections WHERE live_id=?', (live_id,)).fetchone() if live_id else None
                reset_baseline = database.execute('SELECT * FROM traffic_reset_baselines WHERE connection_key=?', (key,)).fetchone()
                if reset_baseline is None and live_id:
                    reset_baseline = database.execute('SELECT * FROM traffic_reset_baselines WHERE live_id=? ORDER BY connection_key LIMIT 1', (live_id,)).fetchone()
                prior_rows = [row for row in (previous, legacy) if row is not None]
                previous_download = max((row['download_bytes'] or 0 for row in prior_rows), default=0)
                previous_upload = max((row['upload_bytes'] or 0 for row in prior_rows), default=0)
                baseline_download = reset_baseline['download_bytes'] if reset_baseline is not None else 0
                baseline_upload = reset_baseline['upload_bytes'] if reset_baseline is not None else 0
                raw_download = max(0, int(item.get('download_bytes', 0)))
                raw_upload = max(0, int(item.get('upload_bytes', 0)))
                download = max(previous_download, raw_download - baseline_download)
                upload = max(previous_upload, raw_upload - baseline_upload)
                counted_rows = [row for row in prior_rows if row['download_bytes'] is not None or row['upload_bytes'] is not None]
                old_identity = next((row for row in counted_rows if row['user_key'] != 'unknown'), counted_rows[0] if counted_rows else None)
                old_user_key = old_identity['user_key'] if old_identity else 'unknown'
                old_user_name = old_identity['user_name'] if old_identity else 'Не определён'
                user_key = str(item.get('user_key') or 'unknown')
                user_name = str(item.get('user_name') or 'Не определён')
                if user_key == 'unknown' and old_user_key != 'unknown':
                    user_key, user_name = old_user_key, old_user_name
                if old_user_key != user_key and (previous_download or previous_upload):
                    self.add_user_traffic(database, old_user_key, old_user_name, -previous_download, -previous_upload)
                    self.add_user_traffic(database, user_key, user_name, previous_download, previous_upload)
                self.add_user_traffic(database, user_key, user_name, download - previous_download, upload - previous_upload)
                if previous is not None:
                    download = max(download, previous['download_bytes'] or 0)
                    upload = max(upload, previous['upload_bytes'] or 0)
                if legacy is not None:
                    download = max(download, legacy['download_bytes'] or 0)
                    upload = max(upload, legacy['upload_bytes'] or 0)
                if legacy is not None and legacy['connection_key'] != key:
                    database.execute('DELETE FROM connections WHERE connection_key=?', (legacy['connection_key'],))
                database.execute('''
                    INSERT INTO connections (connection_key,user_key,user_name,started_at,last_seen_at,source_ip,source_port,destination,network,download_bytes,upload_bytes,active,live_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)
                    ON CONFLICT(connection_key) DO UPDATE SET
                        user_key=CASE WHEN excluded.user_key!='unknown' THEN excluded.user_key ELSE connections.user_key END,
                        user_name=CASE WHEN excluded.user_key!='unknown' THEN excluded.user_name ELSE connections.user_name END,
                        last_seen_at=excluded.last_seen_at,download_bytes=excluded.download_bytes,
                        upload_bytes=excluded.upload_bytes,active=1,live_id=excluded.live_id,
                        network=excluded.network,source_ip=excluded.source_ip,source_port=excluded.source_port
                    ''', (key, user_key, user_name, item['started_at'], observed, item.get('ip', '—'), item.get('source_port'), item.get('destination', '—'), item.get('network', ''), download, upload, live_id or None))
                if reset_baseline is not None:
                    database.execute('UPDATE traffic_reset_baselines SET updated_at=? WHERE connection_key=?', (observed, reset_baseline['connection_key']))
            database.execute("INSERT OR REPLACE INTO settings VALUES ('last_collected_at', ?)", (observed,))
            database.execute("DELETE FROM settings WHERE key='last_error'")
        self.secure_files()

    @staticmethod
    def record_download_rates(database, payload, observed):
        if not isinstance(payload.get('traffic_samples'), list):
            return
        timestamp = int(dt.datetime.fromisoformat(payload.get('sampled_at') or observed).timestamp() * 1000)
        previous_at = database.execute("SELECT value FROM settings WHERE key='last_rate_sample_at'").fetchone()
        previous_at = int(previous_at[0]) if previous_at else None
        if previous_at is not None and timestamp <= previous_at:
            return
        elapsed = (timestamp - previous_at) / 1000 if previous_at is not None else 0
        measured = 0 < elapsed <= 10
        users = {str(item['user_key']): str(item.get('user_name') or 'Не определён') for item in payload.get('users', []) if item.get('user_key') and item['user_key'] != 'unknown' and int(item.get('connections') or 0) > 0}
        rates = {key: 0.0 if measured else None for key in users}
        upload_rates = {key: 0.0 if measured else None for key in users}
        previous = {row['live_id']: row for row in database.execute('SELECT * FROM download_rate_counters')}
        counters = []
        for item in payload['traffic_samples']:
            if not item.get('id') or not item.get('user_key') or item['user_key'] == 'unknown':
                continue
            identifier, key = str(item['id']), str(item['user_key'])
            download = max(0, int(item.get('download_bytes') or 0))
            upload = max(0, int(item.get('upload_bytes') or 0))
            counters.append((identifier, key, download, upload))
            prior = previous.get(identifier)
            if measured and key in rates and prior is not None and prior['user_key'] == key:
                rates[key] += max(0, download - prior['download_bytes']) / elapsed
                if prior['upload_bytes'] is not None:
                    upload_rates[key] += max(0, upload - prior['upload_bytes']) / elapsed
        database.execute('DELETE FROM download_rate_counters')
        database.executemany('INSERT OR REPLACE INTO download_rate_counters (live_id,user_key,download_bytes,upload_bytes) VALUES (?,?,?,?)', counters)
        database.executemany('INSERT INTO download_rate_samples (sampled_at,user_key,user_name,bytes_per_second,upload_bytes_per_second) VALUES (?,?,?,?,?)', [(timestamp, key, users[key], rate, upload_rates[key]) for key, rate in rates.items()])
        database.execute("INSERT OR REPLACE INTO settings VALUES ('last_rate_sample_at', ?)", (str(timestamp),))
        database.execute('DELETE FROM download_rate_samples WHERE sampled_at < ?', (timestamp - 90 * 60000,))

    def activity(self, seconds=5, at=None):
        try:
            seconds = int(seconds)
        except (TypeError, ValueError) as error:
            raise ValueError('Интервал должен быть от 1 до 60 секунд.') from error
        if not 1 <= seconds <= 60:
            raise ValueError('Интервал должен быть от 1 до 60 секунд.')
        until = int((at or dt.datetime.now(dt.timezone.utc)).timestamp() * 1000)
        with self.connect() as database:
            database.execute('BEGIN')
            latest = database.execute("SELECT value FROM settings WHERE key='last_rate_sample_at'").fetchone()
            sampled_at = int(latest[0]) if latest else None
            error = database.execute("SELECT 1 FROM settings WHERE key='last_error'").fetchone()
            fresh = not error and sampled_at is not None and 0 <= until - sampled_at <= 10000
            totals = {row['user_key']: dict(row) for row in database.execute('SELECT * FROM user_traffic_totals')}
            logged = {row['user_key']: dict(row) for row in database.execute('SELECT user_key,MAX(user_name) AS user_name,SUM(download_bytes IS NULL OR upload_bytes IS NULL) AS unknown_traffic FROM connections GROUP BY user_key')}
            connected = {}
            for row in database.execute('SELECT user_key,user_name,source_ip FROM connections WHERE active=1'):
                user = connected.setdefault(row['user_key'], {'user_name': row['user_name'], 'ips': set(), 'connections': 0})
                user['ips'].add(row['source_ip'])
                user['connections'] += 1
            peaks = {row['user_key']: dict(row) for row in database.execute('''
                SELECT user_key,MAX(bytes_per_second) AS download,MAX(upload_bytes_per_second) AS upload
                FROM download_rate_samples WHERE sampled_at>? AND sampled_at<=? GROUP BY user_key
            ''', (until - seconds * 1000, until))}
            current = {row['user_key']: dict(row) for row in database.execute('SELECT * FROM download_rate_samples WHERE sampled_at=?', (sampled_at,))}
            users = []
            for key in totals.keys() | connected.keys() | current.keys() | logged.keys():
                if key == 'unknown':
                    continue
                total = totals.get(key, {})
                live = connected.get(key, {})
                sample = current.get(key, {})
                online = fresh and bool(live.get('connections'))
                download = sample.get('bytes_per_second') if online else (0 if fresh else None)
                upload = sample.get('upload_bytes_per_second') if online else (0 if fresh else None)
                users.append({
                    'user_key': key, 'user_name': live.get('user_name') or total.get('user_name') or sample.get('user_name') or logged.get(key, {}).get('user_name') or 'Не определён',
                    'status': ('active' if (download or 0) + (upload or 0) > 0 else 'connected') if online else ('offline' if fresh else 'unknown'),
                    'connections': live.get('connections', 0) if fresh else None,
                    'ips': sorted(live.get('ips', [])) if fresh else [],
                    'download_bytes': total.get('download_bytes'), 'upload_bytes': total.get('upload_bytes'),
                    'unknown_traffic': logged.get(key, {}).get('unknown_traffic', 0),
                    'download_rate': download, 'upload_rate': upload,
                    'download_peak': peaks.get(key, {}).get('download') if fresh else None,
                    'upload_peak': peaks.get(key, {}).get('upload') if fresh else None,
                })
        users.sort(key=lambda user: (-(user['download_bytes'] or 0), user['user_name'].casefold(), user['user_key']))
        return {'seconds': seconds, 'sampled_at': sampled_at, 'fresh': fresh, 'users': users}

    def download_chart(self, minutes=10, at=None, direction='download'):
        if direction not in ('download', 'upload'):
            raise ValueError('Неизвестное направление трафика.')
        rate_column = 'bytes_per_second' if direction == 'download' else 'upload_bytes_per_second'
        try:
            minutes = int(minutes)
        except (ValueError, TypeError) as error:
            raise ValueError('Отрезок истории указан некорректно.') from error
        if minutes not in (10, 30, 60, 90):
            raise ValueError('Доступны отрезки 10, 30, 60 и 90 минут.')
        until = int((at or dt.datetime.now(dt.timezone.utc)).timestamp() * 1000)
        since = until - minutes * 60000
        bucket = max(2000, math.ceil(minutes * 60000 / 300 / 1000) * 1000)
        with self.connect() as database:
            latest = database.execute("SELECT value FROM settings WHERE key='last_rate_sample_at'").fetchone()
            ranked = database.execute(f'''
                SELECT user_key,MAX({rate_column}) AS peak
                FROM download_rate_samples WHERE sampled_at>=? AND sampled_at<=?
                AND user_key!='unknown' GROUP BY user_key ORDER BY peak DESC,user_key LIMIT 10
            ''', (since, until)).fetchall()
            count = database.execute("SELECT COUNT(DISTINCT user_key) FROM download_rate_samples WHERE sampled_at>=? AND sampled_at<=? AND user_key!='unknown'", (since, until)).fetchone()[0]
            result = []
            for user in ranked:
                key = user['user_key']
                name = database.execute('SELECT user_name FROM download_rate_samples WHERE user_key=? AND sampled_at>=? AND sampled_at<=? ORDER BY sampled_at DESC LIMIT 1', (key, since, until)).fetchone()[0]
                rows = database.execute(f'''
                    SELECT sampled_at,bytes_per_second FROM (
                        SELECT sampled_at,{rate_column} AS bytes_per_second,
                            ROW_NUMBER() OVER (PARTITION BY sampled_at / ? ORDER BY {rate_column} DESC,sampled_at DESC) AS position
                        FROM download_rate_samples WHERE user_key=? AND sampled_at>=? AND sampled_at<=?
                    ) WHERE position=1 ORDER BY sampled_at
                ''', (bucket, key, since, until)).fetchall()
                points = []
                for row in rows:
                    if points and row['sampled_at'] - points[-1]['x'] > bucket * 2:
                        points.append({'x': points[-1]['x'] + bucket, 'y': None})
                    points.append({'x': row['sampled_at'], 'y': row['bytes_per_second'] / 1048576 if row['bytes_per_second'] is not None else None})
                result.append({'user_key': key, 'user_name': name, 'peak_bytes_per_second': user['peak'], 'points': points})
        return {'minutes': minutes, 'direction': direction, 'since': since, 'until': until, 'sampled_at': int(latest[0]) if latest else None, 'user_count': count, 'users': result}

    def filters(self, user_key='', since='', until=''):
        conditions, values = ["user_key!='unknown'"], []
        if user_key:
            conditions.append('user_key=?')
            values.append(user_key)
        for value, comparison in ((since, '>='), (until, '<')):
            if value:
                try:
                    date = dt.date.fromisoformat(value)
                except ValueError as error:
                    raise ValueError('Дата фильтра должна иметь формат ГГГГ-ММ-ДД.') from error
                if comparison == '<':
                    date += dt.timedelta(days=1)
                conditions.append('started_at ' + comparison + ' ?')
                values.append(utc_text(dt.datetime.combine(date, dt.time.min, tzinfo=dt.timezone.utc)))
        if since and until and since > until:
            raise ValueError('Начало периода не должно быть позже окончания.')
        return (' WHERE ' + ' AND '.join(conditions) if conditions else ''), values

    def query(self, user_key='', since='', until='', page=1, page_size=100):
        where, values = self.filters(user_key, since, until)
        page = max(1, int(page))
        with self.connect() as database:
            count = database.execute('SELECT COUNT(*) FROM connections' + where, values).fetchone()[0]
            rows = database.execute('SELECT * FROM connections' + where + ' ORDER BY started_at DESC,connection_key LIMIT ? OFFSET ?', values + [page_size, (page - 1) * page_size]).fetchall()
            totals = database.execute('SELECT COALESCE(SUM(download_bytes),0) AS download,COALESCE(SUM(upload_bytes),0) AS upload,SUM(download_bytes IS NULL) AS unknown FROM connections' + where, values).fetchone()
            collected = database.execute("SELECT value FROM settings WHERE key='last_collected_at'").fetchone()
            error = database.execute("SELECT value FROM settings WHERE key='last_error'").fetchone()
        return {'rows': [dict(row) for row in rows], 'count': count, 'page': page, 'download_bytes': totals['download'], 'upload_bytes': totals['upload'], 'unknown_traffic': totals['unknown'] or 0, 'last_collected_at': collected[0] if collected else None, 'collector_error': error[0] if error else None}

    def traffic_chart(self, user_key='', since='', until=''):
        where, values = self.filters(user_key, since, until)
        with self.connect() as database:
            rows = database.execute('''
                SELECT user_key,MAX(user_name) AS user_name,
                    COALESCE(SUM(download_bytes),0) AS download_bytes,
                    COALESCE(SUM(upload_bytes),0) AS upload_bytes
                FROM connections
            ''' + where + ''' GROUP BY user_key
                ORDER BY SUM(COALESCE(download_bytes,0)) + SUM(COALESCE(upload_bytes,0)) DESC,MAX(user_name) COLLATE NOCASE
            ''', values).fetchall()
        return [dict(row) for row in rows]

    def user_totals(self):
        with self.connect() as database:
            rows = database.execute('''
                SELECT user_key,user_name,download_bytes,upload_bytes
                FROM user_traffic_totals WHERE user_key!='unknown'
            ''').fetchall()
        return {row['user_key']: {**dict(row), 'download': format_bytes(row['download_bytes']), 'upload': format_bytes(row['upload_bytes'])} for row in rows}

    def top_users(self, live_users=(), limit=5):
        live = {item['user_key']: item for item in live_users}
        with self.connect() as database:
            rows = database.execute('''
                SELECT user_key,user_name,download_bytes,upload_bytes
                FROM user_traffic_totals
                WHERE user_key!='unknown' AND download_bytes + upload_bytes > 0
                ORDER BY download_bytes + upload_bytes DESC,user_name COLLATE NOCASE
                LIMIT ?
            ''', (max(0, min(5, int(limit))),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            active = live.get(item['user_key'], {})
            item['user_name'] = active.get('user_name') or item['user_name']
            item['connections'] = int(active.get('connections') or 0)
            item['download'] = format_bytes(item['download_bytes'])
            item['upload'] = format_bytes(item['upload_bytes'])
            result.append(item)
        return result

    @staticmethod
    def clear_statistics(database, user_key=None):
        scope = '' if user_key is None else ' WHERE user_key=?'
        values = () if user_key is None else (user_key,)
        live_rows = database.execute('SELECT connection_key,live_id,user_key,download_bytes,upload_bytes FROM connections WHERE active=1' + ('' if user_key is None else ' AND user_key=?'), values).fetchall()
        baselines = []
        for row in live_rows:
            if not row['connection_key']:
                continue
            # Raw sing-box counters = stored bytes + the baseline of an earlier reset, so repeated resets never re-count old bytes.
            prior = database.execute('SELECT download_bytes,upload_bytes FROM traffic_reset_baselines WHERE connection_key=?', (row['connection_key'],)).fetchone()
            if prior is None and row['live_id']:
                prior = database.execute('SELECT download_bytes,upload_bytes FROM traffic_reset_baselines WHERE live_id=? ORDER BY connection_key LIMIT 1', (row['live_id'],)).fetchone()
            baselines.append((row['connection_key'], row['live_id'], row['user_key'], (row['download_bytes'] or 0) + (prior['download_bytes'] if prior else 0), (row['upload_bytes'] or 0) + (prior['upload_bytes'] if prior else 0), utc_text()))
        database.executemany('INSERT OR REPLACE INTO traffic_reset_baselines (connection_key,live_id,user_key,download_bytes,upload_bytes,updated_at) VALUES (?,?,?,?,?,?)', baselines)
        history_rows = removed = 0
        for table in ('connections', 'user_traffic_totals', 'download_rate_samples', 'download_rate_counters'):
            count = database.execute('DELETE FROM ' + table + scope, values).rowcount
            removed += count
            if table == 'connections':
                history_rows = count
        return history_rows, removed

    def reset_user_totals(self, user_key):
        if not isinstance(user_key, str) or not user_key or len(user_key) > 128:
            raise ValueError('Пользователь статистики указан некорректно.')
        with self.lock, self.connect() as database:
            _, removed = self.clear_statistics(database, user_key)
        self.secure_files()
        return removed > 0

    def reset_all_statistics(self):
        with self.lock, self.connect() as database:
            history_rows, _ = self.clear_statistics(database)
        self.secure_files()
        self.compact()
        return history_rows

    def compact(self):
        with self.lock:
            try:
                with self.connect() as database:
                    database.execute('VACUUM')
            except sqlite3.OperationalError:
                return False
        self.secure_files()
        return True

    def user_options(self):
        with self.connect() as database:
            return [dict(row) for row in database.execute("SELECT user_key, MAX(user_name) AS user_name FROM connections WHERE user_key!='unknown' GROUP BY user_key ORDER BY user_name")]

    def last_collected_at(self):
        with self.connect() as database:
            row = database.execute("SELECT value FROM settings WHERE key='last_collected_at'").fetchone()
            return row[0] if row else None

    def record_collection_error(self):
        with self.connect() as database:
            database.execute("INSERT OR REPLACE INTO settings VALUES ('last_error', ?)", ('Последняя попытка сбора не удалась; старые данные сохранены.',))

    def export_xls(self, user_key='', since='', until=''):
        import xlwt

        where, values = self.filters(user_key, since, until)
        workbook = xlwt.Workbook(encoding='utf-8')
        headers = ['Начало UTC', 'Последнее наблюдение UTC', 'Пользователь', 'IP клиента', 'Порт клиента', 'Домен/IP назначения', 'Протокол', 'Скачано, байт', 'Отправлено, байт', 'Статус', 'Точность']
        sheet = None
        row_number = 65536
        sheet_number = 0
        with self.connect() as database:
            cursor = database.execute('SELECT * FROM connections' + where + ' ORDER BY started_at,connection_key', values)
            for row in cursor:
                if row_number >= 65536:
                    sheet_number += 1
                    sheet = workbook.add_sheet('HAPP ' + str(sheet_number))
                    for column, title in enumerate(headers):
                        sheet.write(0, column, title)
                        sheet.col(column).width = 6000 if column < 7 else 4200
                    row_number = 1
                data = [format_datetime(row['started_at']), format_datetime(row['last_seen_at']), row['user_name'], row['source_ip'], row['source_port'] or '', row['destination'], row['network'], row['download_bytes'] if row['download_bytes'] is not None else '', row['upload_bytes'] if row['upload_bytes'] is not None else '', 'Активно' if row['active'] else 'Закрыто/не наблюдается', 'Наблюдаемые счётчики' if row['download_bytes'] is not None else 'Финальные байты неизвестны']
                for column, value in enumerate(data):
                    sheet.write(row_number, column, value)
                row_number += 1
            if sheet is None:
                sheet = workbook.add_sheet('HAPP 1')
                for column, title in enumerate(headers):
                    sheet.write(0, column, title)
        output = io.BytesIO()
        workbook.save(output)
        return output.getvalue()

    def export_xml(self, user_key='', since='', until=''):
        where, values = self.filters(user_key, since, until)
        records = []
        with self.connect() as database:
            for row in database.execute('SELECT * FROM connections' + where + ' ORDER BY started_at,connection_key', values):
                records.append('  <connection' + xml_attributes((
                    ('started-at', row['started_at']), ('last-seen-at', row['last_seen_at']),
                    ('user-key', row['user_key']), ('user', row['user_name']),
                    ('source-ip', row['source_ip']), ('source-port', row['source_port']),
                    ('destination', row['destination']), ('network', row['network']),
                    ('download-bytes', row['download_bytes']), ('upload-bytes', row['upload_bytes']),
                    ('status', 'active' if row['active'] else 'closed'),
                    ('accuracy', 'observed' if row['download_bytes'] is not None else 'final-bytes-unknown'),
                )) + '/>\n')
        root = '<happ-statistics' + xml_attributes((
            ('exported-at', dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')), ('timezone', 'UTC'),
            ('records', len(records)), ('user', user_key), ('since', since), ('until', until),
        )) + '>\n'
        return ('<?xml version="1.0" encoding="UTF-8"?>\n' + root + ''.join(records) + '</happ-statistics>\n').encode('utf-8')