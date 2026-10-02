import datetime as dt
import io
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from happ_stats import format_bytes, format_datetime
from pathlib import Path


def utc_text(value=None):
    return (value or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat(timespec='microseconds')


class HappHistory:
    def __init__(self, directory='/mnt/stat'):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'happ-stat.sqlite3'
        self.lock = threading.Lock()
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
            ''')
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
        if count:
            with self.connect() as database:
                database.execute('VACUUM')
        self.secure_files()
        return count

    def ingest(self, payload, at=None):
        observed = utc_text(at)
        items = payload.get('connections', [])
        visits = payload.get('visits', [])
        with self.lock, self.connect() as database:
            database.execute('UPDATE connections SET active=0 WHERE active=1')
            for visit in visits:
                key = visit.get('connection_key')
                if not key or not visit.get('started_at'):
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
                previous = database.execute('SELECT download_bytes,upload_bytes FROM connections WHERE connection_key=?', (key,)).fetchone()
                live_id = str(item.get('id') or '')
                legacy = database.execute('SELECT connection_key,download_bytes,upload_bytes FROM connections WHERE live_id=?', (live_id,)).fetchone() if live_id else None
                download = max(0, int(item.get('download_bytes', 0)))
                upload = max(0, int(item.get('upload_bytes', 0)))
                if previous is not None:
                    download = max(download, previous['download_bytes'] or 0)
                    upload = max(upload, previous['upload_bytes'] or 0)
                if legacy is not None and legacy['connection_key'] != key:
                    download = max(download, legacy['download_bytes'] or 0)
                    upload = max(upload, legacy['upload_bytes'] or 0)
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
                    ''', (key, item.get('user_key', 'unknown'), item.get('user_name', 'Не определён'), item['started_at'], observed, item.get('ip', '—'), item.get('source_port'), item.get('destination', '—'), item.get('network', ''), download, upload, live_id or None))
            database.execute("INSERT OR REPLACE INTO settings VALUES ('last_collected_at', ?)", (observed,))
            database.execute("DELETE FROM settings WHERE key='last_error'")
        self.secure_files()

    def filters(self, user_key='', since='', until=''):
        conditions, values = [], []
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

    def user_totals(self):
        with self.connect() as database:
            rows = database.execute('''
                SELECT user_key, COALESCE(SUM(download_bytes),0) AS download_bytes,
                    COALESCE(SUM(upload_bytes),0) AS upload_bytes
                FROM connections GROUP BY user_key
            ''').fetchall()
        return {row['user_key']: {**dict(row), 'download': format_bytes(row['download_bytes']), 'upload': format_bytes(row['upload_bytes'])} for row in rows}

    def user_options(self):
        with self.connect() as database:
            return [dict(row) for row in database.execute('SELECT user_key, MAX(user_name) AS user_name FROM connections GROUP BY user_key ORDER BY user_name')]

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