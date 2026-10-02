import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from happ_stats import authenticated_peers, connection_identity, connection_item, summarize_users, journal_message, parse_connection_start, format_datetime


class HappStatsTests(unittest.TestCase):
    def setUp(self):
        self.started = '2026-10-02T18:00:00+00:00'
        self.timestamp = str(int(dt.datetime.fromisoformat(self.started).timestamp() * 1000000))
        self.names = {'personal-a': 'Alice', 'personal-b': 'Bob'}

    def source(self, cid, port, host='203.0.113.1', pid='1'):
        return {'MESSAGE': f'INFO [{cid} 0ms] inbound/vless[happ]: inbound connection from {host}:{port}', '__REALTIME_TIMESTAMP': self.timestamp, '_PID': pid}

    def auth(self, cid, marker, pid='1'):
        return {'MESSAGE': f'INFO [{cid} 10ms] inbound/vless[happ]: [{marker}] inbound connection to example.com:443', '_PID': pid}

    def live(self, port, host='203.0.113.1', started=None, download=100, upload=50):
        return {'id': str(port), 'metadata': {'sourceIP': host, 'sourcePort': port, 'network': 'tcp'}, 'start': started or self.started, 'download': download, 'upload': upload}

    def test_users_sharing_ip_remain_distinct(self):
        peers = authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a'), self.source(2, 40002), self.auth(2, 'personal-b')], self.names, {'0'}, 'happ')
        self.assertEqual(connection_identity(self.live(40001), peers)['user_name'], 'Alice')
        self.assertEqual(connection_identity(self.live(40002), peers)['user_name'], 'Bob')
        self.assertEqual(connection_identity(self.live(40003), peers)['user_name'], 'Не определён')

    def test_binary_message_and_ansi(self):
        source = self.source(1, 40001)
        auth = self.auth(1, 'personal-a')
        for event in (source, auth):
            event['MESSAGE'] = list(('\x1b[36m' + event['MESSAGE'] + '\x1b[0m').encode('utf-8'))
        peers = authenticated_peers([source, auth], self.names, set(), 'happ')
        self.assertEqual(connection_identity(self.live(40001), peers)['user_name'], 'Alice')
        self.assertNotIn('\x1b', journal_message(auth))

    def test_source_without_auth_is_not_identified(self):
        peers = authenticated_peers([self.source(1, 40001)], self.names, set(), 'happ')
        self.assertEqual(connection_identity(self.live(40001), peers)['user_key'], 'unknown')

    def test_stale_or_ambiguous_connection_is_not_identified(self):
        peers = authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a')], self.names, set(), 'happ')
        self.assertEqual(connection_identity(self.live(40001, started='2026-10-02T19:00:00+00:00'), peers)['user_key'], 'unknown')
        peers = authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a'), self.source(2, 40001), self.auth(2, 'personal-b')], self.names, set(), 'happ')
        self.assertEqual(connection_identity(self.live(40001), peers)['user_key'], 'unknown')

    def test_cid_reuse_and_process_identity(self):
        records = {}
        authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a')], self.names, set(), 'happ', records)
        peers = authenticated_peers([self.source(1, 40002)], self.names, set(), 'happ', records)
        self.assertEqual(connection_identity(self.live(40002), peers)['user_key'], 'unknown')
        peers = authenticated_peers([self.auth(1, 'personal-b', pid='2')], self.names, set(), 'happ', records)
        self.assertEqual(connection_identity(self.live(40002), peers)['user_key'], 'unknown')

    def test_vip_and_ipv6(self):
        peers = authenticated_peers([self.source(1, 40001, host='[2001:db8::1]'), self.auth(1, '0')], self.names, {'0'}, 'happ')
        self.assertEqual(connection_identity(self.live(40001, host='2001:db8::1'), peers)['user_name'], 'VIP')

    def test_user_traffic_totals(self):
        peers = authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a'), self.source(2, 40002), self.auth(2, 'personal-a'), self.source(3, 40003), self.auth(3, 'personal-b')], self.names, set(), 'happ')
        items = [connection_item(self.live(40001, download=2048, upload=128), peers), connection_item(self.live(40002, download=1024, upload=64), peers), connection_item(self.live(40003, download=100, upload=50), peers)]
        groups = summarize_users(items)
        alice = next(item for item in groups if item['user_name'] == 'Alice')
        self.assertEqual(alice['connections'], 2)
        self.assertEqual(alice['download_bytes'], 3072)
        self.assertEqual(alice['upload_bytes'], 192)
        self.assertEqual(alice['download'], '3.0 KB')

    def test_rename_uses_current_registry_name(self):
        records = {}
        authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a')], self.names, set(), 'happ', records)
        peers = authenticated_peers([], {'personal-a': 'Renamed'}, set(), 'happ', records)
        self.assertEqual(connection_identity(self.live(40001), peers)['user_name'], 'Renamed')

    def test_nanosecond_api_timestamp(self):
        started = parse_connection_start('2026-10-02T18:00:00.123456789Z')
        self.assertEqual(started.microsecond, 123456)
        peers = authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a')], self.names, set(), 'happ')
        self.assertEqual(connection_identity(self.live(40001, started='2026-10-02T18:00:00.123456789Z'), peers)['user_name'], 'Alice')

    def test_display_datetime_preserves_utc_and_drops_fraction(self):
        self.assertEqual(format_datetime('2026-10-02T21:05:23.914301+00:00'), '02.10.2026 21:05:23')
        self.assertEqual(format_datetime('2026-10-03T00:05:23+03:00'), '02.10.2026 21:05:23')
        self.assertEqual(format_datetime(None), '—')
        self.assertEqual(format_datetime('invalid'), '—')


if __name__ == '__main__':
    unittest.main()