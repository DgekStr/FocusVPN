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

            def deny(handler):
                handler.send_empty(403)
                return False

            with patch.object(app.Handler, 'require_access', deny):
                for route in ('/happ-history', '/happ-history.xls'):
                    connection.request('GET', route)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_display_and_xls_dates_do_not_modify_storage(self):
        self.history.ingest({'connections': [self.row()]}, self.at)
        expected = '02.10.2026 18:00:00'
        page = render_history(self.history, {})
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