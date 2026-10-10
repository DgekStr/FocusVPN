import datetime as dt
import json
import os
import sys
import tempfile
import types
import http.client
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
from happ_history import HappHistory
from happ_history_ui import render_history
from happ_stats import authenticated_peers
import app


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = HappHistory(self.directory.name)
        self.at = dt.datetime(2026, 10, 2, 18, tzinfo=dt.timezone.utc)

    def row(self, key='journal-1', user='personal-a', name='Alice', download=100, upload=50, started=None):
        return {'id': 'live-' + key, 'connection_key': key, 'user_key': user, 'user_name': name, 'started_at': (started or self.at).isoformat(timespec='microseconds'), 'ip': '203.0.113.1', 'source_port': 40001, 'destination': 'example.com:443', 'network': 'TCP', 'download_bytes': download, 'upload_bytes': upload}

    def test_default_retention_and_private_permissions(self):
        self.assertEqual(self.history.retention_days(), 60)
        if os.name == 'posix':
            self.assertEqual(Path(self.directory.name).stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.history.path.stat().st_mode & 0o777, 0o600)

    def rate_payload(self, amount, sampled_at, user='personal-a', identifier='rate-a'):
        return {'sampled_at': sampled_at.isoformat(), 'connections': [], 'users': [{'user_key': user, 'user_name': 'Alice', 'connections': 1}], 'traffic_samples': [{'id': identifier, 'user_key': user, 'download_bytes': amount}]}

    def test_download_chart_persists_maximum_not_bucket_average(self):
        self.history.ingest(self.rate_payload(0, self.at))
        self.history.ingest(self.rate_payload(2 * 1048576, self.at + dt.timedelta(seconds=2)))
        self.history.ingest(self.rate_payload(8 * 1048576, self.at + dt.timedelta(seconds=4)))
        self.history.ingest(self.rate_payload(8 * 1048576, self.at + dt.timedelta(seconds=6)))
        reopened = HappHistory(self.directory.name)
        chart = reopened.download_chart(90, self.at + dt.timedelta(seconds=6))
        self.assertEqual(chart['users'][0]['peak_bytes_per_second'], 3 * 1048576)
        self.assertEqual(max(point['y'] or 0 for point in chart['users'][0]['points']), 3)
        self.assertLessEqual(len(chart['users'][0]['points']), 301)
        self.assertEqual(chart['sampled_at'], int((self.at + dt.timedelta(seconds=6)).timestamp() * 1000))
        reopened.ingest(self.rate_payload(10 * 1048576, self.at + dt.timedelta(seconds=8)))
        self.assertEqual(reopened.download_chart(10, self.at + dt.timedelta(seconds=8))['users'][0]['peak_bytes_per_second'], 3 * 1048576)

    def test_activity_upload_peaks_lifetime_and_stale_data(self):
        for offset, download, upload in ((0, 0, 0), (1, 1000, 400), (2, 1100, 450)):
            observed = self.at + dt.timedelta(seconds=offset)
            payload = self.rate_payload(download, observed, identifier='live-journal-1')
            payload['traffic_samples'][0]['upload_bytes'] = upload
            payload['connections'] = [self.row(download=download, upload=upload)]
            self.history.ingest(payload, observed)
        reopened = HappHistory(self.directory.name)
        user = reopened.activity(60, self.at + dt.timedelta(seconds=2))['users'][0]
        self.assertEqual((user['download_bytes'], user['upload_bytes']), (1100, 450))
        self.assertEqual((user['download_rate'], user['upload_rate']), (100, 50))
        self.assertEqual((user['download_peak'], user['upload_peak']), (1000, 400))
        self.assertEqual((user['status'], user['connections'], user['ips']), ('active', 1, ['203.0.113.1']))
        self.assertEqual(reopened.activity(1, self.at + dt.timedelta(seconds=2))['users'][0]['download_peak'], 100)
        chart = reopened.download_chart(10, self.at + dt.timedelta(seconds=2), direction='upload')
        self.assertEqual(chart['users'][0]['peak_bytes_per_second'], 400)
        self.assertEqual(chart['direction'], 'upload')
        with self.assertRaises(ValueError):
            reopened.download_chart(direction='invalid')
        stale = reopened.activity(60, self.at + dt.timedelta(seconds=20))
        self.assertFalse(stale['fresh'])
        self.assertEqual(stale['users'][0]['status'], 'unknown')
        self.assertIsNone(stale['users'][0]['download_rate'])
        for invalid in (0, 61, 'bad'):
            with self.assertRaises(ValueError):
                reopened.activity(invalid, self.at)

    def test_activity_order_uses_lifetime_download_not_speed_or_status(self):
        bob = self.row(key='bob', user='personal-b', name='Bob', download=1048576, upload=0)
        tied = self.row(key='tied', user='personal-c', name='bob', download=1048576, upload=0)
        for offset, rows in ((0, [self.row(download=100, upload=2097152), tied, bob]), (1, [bob, self.row(download=1100, upload=2097152), tied]), (2, [self.row(download=2100, upload=2097152)])):
            observed = self.at + dt.timedelta(seconds=offset)
            payload = {'connections': rows, 'users': [{'user_key': row['user_key'], 'user_name': row['user_name'], 'connections': 1} for row in rows], 'traffic_samples': rows, 'sampled_at': observed.isoformat()}
            self.history.ingest(payload, observed)
            activity = self.history.activity(5, observed)
            self.assertEqual([user['user_key'] for user in activity['users']], ['personal-b', 'personal-c', 'personal-a'])
        self.assertEqual(activity['users'][0]['status'], 'offline')
        self.assertEqual(activity['users'][2]['status'], 'active')
        self.assertGreater(activity['users'][2]['download_rate'], activity['users'][0]['download_rate'])
        stale = self.history.activity(5, self.at + dt.timedelta(seconds=20))
        self.assertFalse(stale['fresh'])
        self.assertEqual([user['user_key'] for user in stale['users']], ['personal-b', 'personal-c', 'personal-a'])

    def test_download_chart_ranges_gaps_and_counter_reset(self):
        self.history.ingest(self.rate_payload(0, self.at))
        self.history.ingest(self.rate_payload(4 * 1048576, self.at + dt.timedelta(seconds=2)))
        self.history.ingest(self.rate_payload(900 * 1048576, self.at + dt.timedelta(minutes=20)))
        self.history.ingest(self.rate_payload(0, self.at + dt.timedelta(minutes=20, seconds=2)))
        for minutes in (10, 30, 60, 90):
            chart = self.history.download_chart(minutes, self.at + dt.timedelta(minutes=20, seconds=2))
            self.assertEqual(chart['until'] - chart['since'], minutes * 60000)
            self.assertEqual(chart['users'][0]['peak_bytes_per_second'], 0 if minutes == 10 else 2 * 1048576)
            if minutes > 10:
                self.assertTrue(any(point['y'] is None for point in chart['users'][0]['points']))
        for invalid in ('bad', 0, 60 * 24):
            with self.assertRaises(ValueError):
                self.history.download_chart(invalid, self.at)

    def test_activity_migrates_existing_database_and_keeps_unknown_traffic(self):
        self.history.ingest(self.rate_payload(1000, self.at), self.at)
        with self.history.connect() as database:
            database.execute('ALTER TABLE download_rate_samples DROP COLUMN upload_bytes_per_second')
            database.execute('ALTER TABLE download_rate_counters DROP COLUMN upload_bytes')
        migrated = HappHistory(self.directory.name)
        chart = migrated.download_chart(10, self.at, direction='upload')
        self.assertIsNone(chart['users'][0]['peak_bytes_per_second'])
        self.assertIsNone(chart['users'][0]['points'][0]['y'])
        migrated.ingest({'connections': [], 'visits': [self.row()]}, self.at)
        user = migrated.activity(5, self.at)['users'][0]
        self.assertEqual(user['unknown_traffic'], 1)
        self.assertIsNone(user['download_bytes'])
        migrated.record_collection_error()
        self.assertFalse(migrated.activity(5, self.at)['fresh'])

    def test_download_chart_limits_users_and_does_not_invent_old_history(self):
        self.history.ingest({'connections': [self.row()]}, self.at)
        self.assertEqual(self.history.download_chart(90, self.at)['users'], [])
        users = [{'user_key': 'user-' + str(index), 'user_name': 'User ' + str(index), 'connections': 1} for index in range(12)]
        samples = [{'id': 'connection-' + str(index), 'user_key': user['user_key'], 'download_bytes': 0} for index, user in enumerate(users)]
        payload = {'connections': [], 'users': users, 'traffic_samples': samples, 'sampled_at': self.at.isoformat()}
        self.history.ingest(payload)
        payload['sampled_at'] = (self.at + dt.timedelta(seconds=2)).isoformat()
        for index, sample in enumerate(samples):
            sample['download_bytes'] = (index + 1) * 1048576
        self.history.ingest(payload)
        chart = self.history.download_chart(30, self.at + dt.timedelta(seconds=2))
        self.assertEqual(chart['user_count'], 12)
        self.assertEqual(len(chart['users']), 10)
        self.assertEqual(chart['users'][0]['user_key'], 'user-11')
        before = chart
        self.history.ingest(payload)
        self.assertEqual(self.history.download_chart(30, self.at + dt.timedelta(seconds=2)), before)

    def test_samples_are_monotonic_and_not_double_counted(self):
        self.history.ingest({'connections': [self.row()]}, self.at)
        self.history.ingest({'connections': [self.row()]}, self.at)
        self.history.ingest({'connections': [self.row(download=200)]}, self.at)
        self.history.ingest({'connections': [self.row(download=150)]}, self.at)
        result = self.history.query()
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['download_bytes'], 200)
        self.assertEqual(result['upload_bytes'], 50)

    def test_user_totals_include_closed_sessions_without_double_counting(self):
        self.history.ingest({'connections': [self.row(), self.row(key='bob', user='personal-b', name='Bob', download=900, upload=90)]}, self.at)
        self.history.ingest({'connections': [self.row(download=200)]}, self.at)
        self.history.ingest({'connections': []}, self.at)
        totals = self.history.user_totals()
        self.assertEqual(totals['personal-a']['download_bytes'], 200)
        self.assertEqual(totals['personal-a']['upload_bytes'], 50)
        self.assertEqual(totals['personal-b']['download_bytes'], 900)
        self.assertEqual(totals['personal-b']['upload_bytes'], 90)
        self.assertTrue(totals['personal-a']['download'])
        self.assertNotIn('personal-missing', totals)

    def test_lifetime_totals_accumulate_reset_per_user_and_survive_retention_cleanup(self):
        bob = self.row(key='bob', user='personal-b', name='Bob', download=500, upload=60)
        self.history.ingest({'connections': [self.row(), bob]}, self.at)
        self.history.ingest({'connections': [self.row(), bob]}, self.at + dt.timedelta(seconds=2))
        self.history.ingest({'connections': [self.row(download=180, upload=80), bob]}, self.at + dt.timedelta(seconds=4))
        totals = self.history.user_totals()
        self.assertEqual((totals['personal-a']['download_bytes'], totals['personal-a']['upload_bytes']), (180, 80))
        self.assertEqual((totals['personal-b']['download_bytes'], totals['personal-b']['upload_bytes']), (500, 60))
        self.assertEqual(self.history.top_users()[0]['user_key'], 'personal-b')

        self.assertTrue(self.history.reset_user_totals('personal-a'))
        self.assertNotIn('personal-a', self.history.user_totals())
        self.assertEqual(self.history.user_totals()['personal-b']['download_bytes'], 500)
        self.history.ingest({'connections': [self.row(download=200, upload=100), bob]}, self.at + dt.timedelta(seconds=6))
        totals = self.history.user_totals()
        self.assertEqual((totals['personal-a']['download_bytes'], totals['personal-a']['upload_bytes']), (20, 20))

        self.history.ingest({'connections': []}, self.at + dt.timedelta(seconds=8))
        self.history.cleanup(at=self.at + dt.timedelta(days=61))
        totals = self.history.user_totals()
        self.assertEqual((totals['personal-a']['download_bytes'], totals['personal-a']['upload_bytes']), (20, 20))
        self.assertEqual((totals['personal-b']['download_bytes'], totals['personal-b']['upload_bytes']), (500, 60))

    def test_reset_clears_history_and_rate_sources_without_readding_old_active_bytes(self):
        payload = self.rate_payload(180, self.at)
        payload['connections'] = [self.row(download=180, upload=80)]
        self.history.ingest(payload, self.at)
        self.assertTrue(self.history.reset_user_totals('personal-a'))
        self.assertEqual(self.history.query('personal-a')['count'], 0)
        self.assertEqual(self.history.traffic_chart('personal-a'), [])
        with self.history.connect() as database:
            for table in ('user_traffic_totals', 'connections', 'download_rate_samples', 'download_rate_counters'):
                self.assertEqual(database.execute('SELECT COUNT(*) FROM ' + table + ' WHERE user_key=?', ('personal-a',)).fetchone()[0], 0)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM traffic_reset_baselines WHERE user_key=?', ('personal-a',)).fetchone()[0], 1)

        payload['sampled_at'] = (self.at + dt.timedelta(seconds=2)).isoformat()
        payload['traffic_samples'][0]['download_bytes'] = 200
        payload['connections'][0]['download_bytes'] = 200
        payload['connections'][0]['upload_bytes'] = 100
        self.history.ingest(payload, self.at + dt.timedelta(seconds=2))
        totals = self.history.user_totals()['personal-a']
        self.assertEqual((totals['download_bytes'], totals['upload_bytes']), (20, 20))
        self.assertEqual(self.history.query('personal-a')['download_bytes'], 20)

    def test_reset_baseline_persists_until_retention_cleanup(self):
        payload = self.rate_payload(180, self.at)
        payload['connections'] = [self.row(download=180, upload=80)]
        self.history.ingest(payload, self.at)
        self.assertTrue(self.history.reset_user_totals('personal-a'))
        for offset, download, upload, expected in ((2, 200, 100, (20, 20)), (4, 260, 130, (80, 50)), (6, 300, 150, (120, 70))):
            observed = self.at + dt.timedelta(seconds=offset)
            payload['sampled_at'] = observed.isoformat()
            payload['traffic_samples'][0]['download_bytes'] = download
            payload['connections'][0].update(download_bytes=download, upload_bytes=upload)
            self.history.ingest(payload, observed)
            totals = self.history.user_totals()['personal-a']
            self.assertEqual((totals['download_bytes'], totals['upload_bytes']), expected)
            self.assertEqual(self.history.query('personal-a')['download_bytes'], expected[0])
        self.history.cleanup(at=self.at + dt.timedelta(days=1))
        with self.history.connect() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM traffic_reset_baselines').fetchone()[0], 1)
        self.history.cleanup(at=self.at + dt.timedelta(days=62))
        with self.history.connect() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM traffic_reset_baselines').fetchone()[0], 0)

    def test_deleted_users_are_purged_and_delayed_samples_cannot_restore_them(self):
        rows = [self.row(), self.row(key='bob', user='personal-b', name='Bob', download=300), self.row(key='vip', user='VIP', name='VIP', download=700), self.row(key='unresolved', user='unknown', name='Не определён')]
        payload = {'connections': rows, 'users': [{'user_key': row['user_key'], 'user_name': row['user_name'], 'connections': 1} for row in rows], 'traffic_samples': [{'id': row['id'], 'user_key': row['user_key'], 'download_bytes': row['download_bytes'], 'upload_bytes': row['upload_bytes']} for row in rows], 'sampled_at': self.at.isoformat()}
        self.history.ingest(payload, self.at)
        reopened = HappHistory(self.directory.name)
        self.assertGreater(reopened.retain_registered_users([{'id': 'b'}]), 0)
        self.assertEqual(reopened.retain_registered_users([{'id': 'b'}]), 0)
        later = self.at + dt.timedelta(seconds=1)
        reopened.ingest({**payload, 'visits': [rows[0]], 'sampled_at': later.isoformat()}, later)
        self.assertEqual(set(reopened.user_totals()), {'personal-b', 'VIP'})
        self.assertEqual(reopened.user_totals()['personal-b']['download_bytes'], 300)
        self.assertEqual(reopened.user_totals()['VIP']['download_bytes'], 700)
        for collection in (reopened.activity(5, later)['users'], reopened.download_chart(10, later)['users'], reopened.top_users(), reopened.user_options(), reopened.traffic_chart(), reopened.query()['rows']):
            self.assertTrue(all(row['user_key'] in ('personal-b', 'VIP') for row in collection))
        with reopened.connect() as database:
            for table in ('connections', 'user_traffic_totals', 'download_rate_samples', 'download_rate_counters'):
                self.assertEqual(database.execute('SELECT COUNT(*) FROM ' + table + ' WHERE user_key=?', ('personal-a',)).fetchone()[0], 0)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM connections WHERE user_key='unknown'").fetchone()[0], 1)
        reopened.retain_registered_users([{'id': 'a'}, {'id': 'b'}])
        reopened.ingest({'connections': [self.row(download=200)]}, later + dt.timedelta(seconds=1))
        self.assertEqual(reopened.user_totals()['personal-a']['download_bytes'], 200)

    def test_lifetime_totals_backfill_existing_retained_connections_once(self):
        self.history.ingest({'connections': [self.row(download=321, upload=123), self.row(key='legacy-unknown', user='unknown', name='Не определён')]}, self.at)
        with self.history.connect() as database:
            database.execute('DROP TABLE user_traffic_totals')
            database.execute("DELETE FROM settings WHERE key='lifetime_totals_initialized'")
        migrated = HappHistory(self.directory.name)
        totals = migrated.user_totals()
        self.assertEqual((totals['personal-a']['download_bytes'], totals['personal-a']['upload_bytes']), (321, 123))
        with migrated.connect() as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM user_traffic_totals WHERE user_key='unknown'").fetchone()[0], 0)

    def test_journal_only_visit_keeps_unknown_bytes(self):
        visit = self.row()
        self.history.ingest({'connections': [], 'visits': [visit]}, self.at)
        result = self.history.query()
        self.assertIsNone(result['rows'][0]['download_bytes'])
        self.assertEqual(result['unknown_traffic'], 1)
        self.assertEqual(result['download_bytes'], 0)
        self.history.ingest({'connections': [self.row()], 'visits': [visit]}, self.at)
        self.assertEqual(self.history.query()['unknown_traffic'], 0)
        self.assertEqual(self.history.query()['count'], 1)

    def test_late_auth_identity_merges_live_id(self):
        unknown = self.row(key='live-key', user='unknown', name='Не определён')
        unknown['id'] = 'same-live-id'
        identified = self.row(key='journal-key', download=200)
        identified['id'] = 'same-live-id'
        self.history.ingest({'connections': [unknown]}, self.at)
        self.history.ingest({'connections': [identified], 'visits': [identified]}, self.at)
        result = self.history.query()
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['rows'][0]['user_name'], 'Alice')
        self.assertEqual(result['download_bytes'], 200)
        self.assertEqual(self.history.user_totals()['personal-a']['download_bytes'], 200)

    def test_unresolved_identity_never_appears_as_a_statistics_user(self):
        payload = self.rate_payload(1000, self.at)
        payload['connections'] = [self.row(download=1000)]
        for collection in ('connections', 'users', 'traffic_samples'):
            for item in payload[collection]:
                item.update({'user_key': 'unknown', 'user_name': 'Не определён'})
        self.history.ingest(payload, self.at)
        self.assertEqual(self.history.user_totals(), {})
        self.assertEqual(self.history.top_users(), [])
        self.assertEqual(self.history.activity(5, self.at)['users'], [])
        self.assertEqual(self.history.download_chart(10, self.at)['users'], [])
        self.assertEqual(self.history.user_options(), [])
        self.assertEqual(self.history.traffic_chart(), [])
        self.assertEqual(self.history.query()['count'], 0)
        with self.history.connect() as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM connections WHERE user_key='unknown'").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM user_traffic_totals WHERE user_key='unknown'").fetchone()[0], 0)
            database.execute("INSERT INTO user_traffic_totals VALUES ('unknown','Не определён',1000,50)")
            database.execute("INSERT INTO download_rate_samples VALUES (?,'unknown','Не определён',100,50)", (int(self.at.timestamp() * 1000),))
        reopened = HappHistory(self.directory.name)
        self.assertEqual(reopened.user_totals(), {})
        self.assertEqual(reopened.top_users(), [])
        self.assertEqual(reopened.activity(5, self.at)['users'], [])
        self.assertEqual(reopened.download_chart(10, self.at)['users'], [])
        self.assertEqual(reopened.download_chart(10, self.at)['user_count'], 0)
        self.assertEqual(reopened.user_options(), [])
        self.assertEqual(reopened.traffic_chart(), [])
        self.assertEqual(reopened.query()['count'], 0)

    def test_late_journal_identity_recovers_closed_connection_counters(self):
        pending = self.row(key='live-delayed', user='unknown', name='Не определён', download=123)
        pending.update({'source_port': 40001, 'id': 'delayed-live-id'})
        visit = {**pending, 'connection_key': 'authenticated-delayed', 'user_key': 'personal-a', 'user_name': 'Alice'}
        self.history.ingest({'connections': [pending]}, self.at)
        later = self.at + dt.timedelta(seconds=2)
        self.history.ingest({'connections': [], 'visits': [visit]}, later)
        result = self.history.query()
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['rows'][0]['user_name'], 'Alice')
        self.assertEqual(result['rows'][0]['active'], 0)
        self.assertEqual(result['download_bytes'], 123)
        self.assertEqual(self.history.user_totals()['personal-a']['download_bytes'], 123)
        self.history.ingest({'connections': [], 'visits': [visit]}, later + dt.timedelta(seconds=1))
        self.assertEqual(self.history.query()['count'], 1)
        self.assertEqual(self.history.user_totals()['personal-a']['download_bytes'], 123)

    def test_ambiguous_late_journal_never_assigns_unknown_connection(self):
        pending = self.row(key='live-ambiguous', user='unknown', name='Не определён')
        pending.update({'source_port': 40001, 'id': 'ambiguous-live-id'})
        first = {**pending, 'connection_key': 'authenticated-first', 'user_key': 'personal-a', 'user_name': 'Alice'}
        second = {**first, 'connection_key': 'authenticated-second', 'user_key': 'personal-b', 'user_name': 'Bob'}
        self.history.ingest({'connections': [pending]}, self.at)
        self.history.ingest({'connections': [], 'visits': [first, second]}, self.at + dt.timedelta(seconds=2))
        with self.history.connect() as database:
            row = database.execute('SELECT user_key FROM connections WHERE live_id=?', ('ambiguous-live-id',)).fetchone()
        self.assertEqual(row['user_key'], 'unknown')
        self.assertNotIn('personal-a', self.history.user_totals())
        self.assertNotIn('personal-b', self.history.user_totals())

    def test_restart_continues_counters_and_closed_rows_persist(self):
        self.history.ingest({'connections': [self.row()]}, self.at)
        reopened = HappHistory(self.directory.name)
        reopened.ingest({'connections': [self.row(download=200)]}, self.at)
        reopened.ingest({'connections': []}, self.at)
        result = reopened.query()
        self.assertEqual(result['download_bytes'], 200)
        self.assertEqual(result['rows'][0]['active'], 0)
        self.assertTrue(reopened.last_collected_at())

    def test_retention_purges_old_data_and_rejects_invalid_days(self):
        old = self.at - dt.timedelta(days=61)
        self.history.ingest({'connections': [self.row(started=old)]}, old)
        self.history.ingest({'connections': [self.row(key='new')]}, self.at)
        self.assertEqual(self.history.cleanup(self.at), 1)
        self.assertEqual(self.history.query()['count'], 1)
        for days in (0, 3651, 'invalid'):
            with self.assertRaises(ValueError):
                self.history.set_retention(days)

    def test_user_date_filters_and_sql_injection(self):
        self.history.ingest({'connections': [self.row(), self.row(key='second', user='personal-b', name='Bob')]}, self.at)
        result = self.history.query('personal-a', '2026-10-02', '2026-10-02')
        self.assertEqual(result['count'], 1)
        self.assertEqual(self.history.query("' OR 1=1 --")['count'], 0)
        self.assertEqual(self.history.query()['count'], 2)

    def test_traffic_chart_aggregates_all_matching_rows_per_user(self):
        self.history.ingest({'connections': [self.row(download=100, upload=20), self.row(key='alice-2', download=300, upload=40), self.row(key='bob', user='personal-b', name='Bob', download=50, upload=80, started=self.at + dt.timedelta(days=1))]}, self.at)
        chart = self.history.traffic_chart(since='2026-10-02', until='2026-10-02')
        self.assertEqual(len(chart), 1)
        self.assertEqual(chart[0]['user_key'], 'personal-a')
        self.assertEqual(chart[0]['download_bytes'], 400)
        self.assertEqual(chart[0]['upload_bytes'], 60)
        ranked = self.history.traffic_chart()
        self.assertEqual([item['user_key'] for item in ranked], ['personal-a', 'personal-b'])
        self.assertGreaterEqual(ranked[0]['download_bytes'] + ranked[0]['upload_bytes'], ranked[1]['download_bytes'] + ranked[1]['upload_bytes'])
        self.assertEqual(self.history.traffic_chart('personal-b')[0]['download_bytes'], 50)
        with self.assertRaises(ValueError):
            self.history.query(since='not-a-date')

    def test_real_xls_and_safe_html(self):
        self.history.ingest({'connections': [self.row(name='<script>Alice</script>')]}, self.at)
        output = self.history.export_xls()
        self.assertEqual(output[:8], bytes.fromhex('d0cf11e0a1b11ae1'))
        page = render_history(self.history, {})
        self.assertIn('&lt;script&gt;Alice&lt;/script&gt;', page)
        self.assertNotIn('<script>Alice</script>', page)
        self.assertIn('/happ-history.xls?', page)

    def test_collector_error_recovers(self):
        self.history.record_collection_error()
        self.assertTrue(self.history.query()['collector_error'])
        self.history.ingest({'connections': []}, self.at)
        self.assertIsNone(self.history.query()['collector_error'])

    def test_worker_cycle_collects_acknowledges_and_purges(self):
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        store = Mock()
        store.last_collected_at.return_value = None
        payload = {'connections': [], 'visits': []}
        with patch.object(app, 'HAPP_HISTORY_STOP', stop), patch.object(app, 'HAPP_HISTORY', store), patch.object(app, 'happ_live_connections', return_value=payload), patch.object(app, 'acknowledge_history_visits') as acknowledge:
            app.happ_history_worker()
        store.ingest.assert_called_once_with(payload)
        store.cleanup.assert_called_once()
        acknowledge.assert_called_once_with([])
        store.record_collection_error.assert_not_called()

    def test_worker_removes_previously_deleted_users_before_ingesting_delayed_payload(self):
        payload = {'connections': [self.row(), self.row(key='bob', user='personal-b', name='Bob')]}
        self.history.ingest(payload, self.at)
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        users = types.SimpleNamespace(registry=lambda: {'users': [{'id': 'b'}]})
        with patch.object(app, 'HAPP_HISTORY_STOP', stop), patch.object(app, 'HAPP_HISTORY', self.history), patch.object(app, 'HAPP_USERS', users), patch.object(app, 'happ_live_connections', return_value=payload), patch.object(app, 'acknowledge_history_visits'):
            app.happ_history_worker()
        self.assertEqual(set(self.history.user_totals()), {'personal-b'})
        self.assertEqual(self.history.query()['count'], 1)
        self.assertIsNone(self.history.query()['collector_error'])

    def test_delete_endpoint_clears_statistics_only_after_successful_user_removal(self):
        payload = {'connections': [self.row(), self.row(key='bob', user='personal-b', name='Bob', download=300)]}
        self.history.ingest(payload, self.at)
        registry = {'users': [{'id': 'a'}, {'id': 'b'}]}
        action = Mock(side_effect=ValueError('Configuration was not applied'))
        users = types.SimpleNamespace(registry=lambda: registry, action=action)
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'HAPP_HISTORY', self.history), patch.object(app, 'HAPP_USERS', users), patch.object(app, 'happ_setup_needed', return_value=False), patch.object(app.Handler, 'require_access', return_value=True):
                form = urlencode({'csrf': app.CSRF_TOKEN, 'id': 'a', 'operation': 'delete'})
                connection.request('POST', '/happ-users/action', form, {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertEqual(parse_qs(urlparse(response.getheader('Location')).query)['kind'], ['error'])
                response.read()
                self.assertIn('personal-a', self.history.user_totals())
                action.side_effect = lambda *_: registry.update(users=[{'id': 'b'}])
                connection.request('POST', '/happ-users/action', form, {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertEqual(parse_qs(urlparse(response.getheader('Location')).query)['kind'], ['success'])
                response.read()
                action.assert_called_with('a', 'delete', '', '')
                self.history.ingest(payload, self.at + dt.timedelta(seconds=1))
                self.assertEqual(set(self.history.user_totals()), {'personal-b'})
                self.assertEqual(self.history.user_totals()['personal-b']['download_bytes'], 300)
                self.assertEqual(self.history.query()['count'], 1)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_udp_journal_visit_has_no_invented_counters(self):
        timestamp = str(int(self.at.timestamp() * 1000000))
        events = [
            {'MESSAGE': 'INFO [1 0ms] inbound/vless[happ]: inbound connection from 203.0.113.1:40001', '_PID': '1', '__REALTIME_TIMESTAMP': timestamp},
            {'MESSAGE': 'INFO [1 10ms] inbound/vless[happ]: [personal-a] inbound packet connection to example.com:443', '_PID': '1'},
        ]
        records = {}
        authenticated_peers(events, {'personal-a': 'Alice'}, set(), 'happ', records)
        visit = next(iter(records['pending_visits'].values()))
        self.assertEqual(visit['network'], 'UDP')
        self.assertNotIn('download_bytes', visit)

    def test_history_http_and_xls_are_authenticated(self):
        self.history.ingest({'connections': [self.row()]}, self.at)
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'HAPP_HISTORY', self.history), patch.object(app, 'HAPP_USERS', None), patch.object(app.Handler, 'require_access', return_value=True):
                connection.request('GET', '/happ-history?user=personal-a')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn('Alice', response.read().decode('utf-8'))
                connection.request('GET', '/happ-history.xls?user=personal-a')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'application/vnd.ms-excel')
                self.assertEqual(response.read()[:8], bytes.fromhex('d0cf11e0a1b11ae1'))
                for minutes in (10, 30, 60, 90):
                    connection.request('GET', '/happ-server/traffic?minutes=' + str(minutes))
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    chart = json.loads(response.read())
                    self.assertEqual(chart['minutes'], minutes)
                    self.assertEqual(chart['until'] - chart['since'], minutes * 60000)
                connection.request('GET', '/happ-server/traffic?minutes=invalid')
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                with patch.object(app, 'happ_live_connections', return_value={'users': []}):
                    for seconds in (1, 5, 60):
                        connection.request('GET', '/happ-server/live?seconds=' + str(seconds))
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read())['activity']['seconds'], seconds)
                    connection.request('GET', '/happ-server/live?seconds=61')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                connection.request('GET', '/happ-server/traffic?direction=upload')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())['direction'], 'upload')

            def deny(handler):
                handler.send_empty(403)
                return False

            with patch.object(app.Handler, 'require_access', deny):
                for route in ('/happ-history', '/happ-history.xls', '/happ-server/traffic?minutes=90', '/happ-server/live?seconds=60'):
                    connection.request('GET', route)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_history_retention_settings_post_saves_and_redirects(self):
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'HAPP_HISTORY', self.history), patch.object(app, 'HAPP_USERS', None), patch.object(app.Handler, 'require_access', return_value=True):
                for days, csrf, kind in [('90', app.CSRF_TOKEN, 'success'), ('0', app.CSRF_TOKEN, 'error'), ('120', 'invalid-csrf', 'error')]:
                    connection.request('POST', '/settings/happ-history', urlencode({'csrf': csrf, 'retention_days': days}), {'Content-Type': 'application/x-www-form-urlencoded'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 303)
                    location = urlparse(response.getheader('Location'))
                    self.assertEqual(location.path, '/settings')
                    self.assertEqual(parse_qs(location.query)['kind'], [kind])
                    response.read()
                    self.assertEqual(self.history.retention_days(), 90)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_happ_profile_traffic_reset_endpoint_clears_only_selected_user(self):
        self.history.ingest({'connections': [self.row(), self.row(key='bob', user='personal-b', name='Bob', download=900, upload=90)]}, self.at)
        users = types.SimpleNamespace(registry=lambda: {'users': [{'id': 'a'}]})
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'HAPP_HISTORY', self.history), patch.object(app, 'HAPP_USERS', users), patch.object(app.Handler, 'require_access', return_value=True):
                connection.request('POST', '/happ-users/traffic/reset', urlencode({'csrf': app.CSRF_TOKEN, 'id': 'a'}), {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertEqual(urlparse(response.getheader('Location')).path, '/happ-server')
                response.read()
                self.assertNotIn('personal-a', self.history.user_totals())
                self.assertEqual(self.history.query('personal-a')['count'], 0)
                self.assertEqual(self.history.traffic_chart('personal-a'), [])
                self.assertEqual(self.history.user_totals()['personal-b']['download_bytes'], 900)

                connection.request('POST', '/happ-users/traffic/reset', urlencode({'csrf': app.CSRF_TOKEN, 'id': 'missing'}), {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertEqual(self.history.user_totals()['personal-b']['download_bytes'], 900)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_happ_live_endpoint_returns_lifetime_user_totals_and_top_five(self):
        self.history.ingest({'connections': [self.row(download=321, upload=123), self.row(key='bob', user='personal-b', name='Bob', download=900, upload=90)]}, self.at)
        live = {'online_count': 1, 'download': '10 B', 'upload': '5 B', 'connections': [], 'users': [{'user_key': 'personal-a', 'user_name': 'Renamed Alice', 'connections': 1, 'download_bytes': 10, 'upload_bytes': 5, 'download': '10 B', 'upload': '5 B'}], 'top_users': []}
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'HAPP_HISTORY', self.history), patch.object(app, 'happ_live_connections', return_value=live), patch.object(app.Handler, 'require_access', return_value=True):
                connection.request('GET', '/happ-server/live')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read())
                self.assertEqual(payload['account_traffic']['personal-a']['download_bytes'], 321)
                self.assertEqual(payload['top_users'][0]['user_key'], 'personal-b')
                self.assertEqual(payload['top_users'][1]['user_name'], 'Renamed Alice')
                self.assertEqual(payload['top_users'][1]['download_bytes'], 321)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_display_and_xls_dates_do_not_modify_storage(self):
        self.history.ingest({'connections': [self.row()]}, self.at)
        expected = '02.10.2026 18:00:00'
        page = render_history(self.history, {})
        self.assertIn('history-chart-panel', page)
        self.assertIn('history-chart-client', page)
        self.assertIn('history-chart-bar download', page)
        self.assertIn('history-chart-bar upload', page)
        self.assertIn('history-chart-values', page)
        self.assertIn('100 B', page)
        self.assertIn('50 B', page)
        self.assertLess(page.index('history-chart-panel'), page.index('Начало UTC'))
        self.assertIn(expected, page)
        self.assertNotIn('2026-10-02T18:00:00.000000+00:00', page)
        self.assertEqual(self.history.query()['rows'][0]['started_at'], self.at.isoformat(timespec='microseconds'))
        self.assertEqual(self.history.query(since='2026-10-02', until='2026-10-02')['count'], 1)
        workbook = Mock()
        sheet = Mock()
        workbook.add_sheet.return_value = sheet
        workbook.save.side_effect = lambda output: output.write(bytes.fromhex('d0cf11e0a1b11ae1'))
        with patch('xlwt.Workbook', return_value=workbook):
            self.history.export_xls()
        sheet.write.assert_any_call(1, 0, expected)
        sheet.write.assert_any_call(1, 1, expected)


if __name__ == '__main__':
    unittest.main()