import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
import happ_stats
from happ_stats import aggregate_connections, authenticated_peers, connection_identity, connection_item, summarize_users, rank_top_users, journal_message, parse_connection_start, format_datetime


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
        self.assertEqual(alice['user_key'], 'personal-a')
        self.assertEqual(alice['connections'], 2)
        self.assertEqual(alice['download_bytes'], 3072)
        self.assertEqual(alice['upload_bytes'], 192)
        self.assertEqual(alice['download'], '3.0 KB')

    def test_live_traffic_samples_preserve_per_connection_counters(self):
        peers = authenticated_peers([self.source(1, 40001), self.auth(1, 'personal-a'), self.source(2, 40002), self.auth(2, 'personal-a')], self.names, set(), 'happ')
        parallel = {**self.live(40001, download=512), 'id': '40001-second'}
        connections = [self.live(40001, download=2048), self.live(40002, download=1024), parallel]
        with patch.object(happ_stats.request, 'urlopen') as urlopen, patch.object(happ_stats, 'journal_identities', return_value=peers):
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = json.dumps({'connections': connections}).encode()
            payload = happ_stats.live_connections()
            again = happ_stats.live_connections()
            collector = happ_stats.live_connections(include_visits=True)
            response.read.return_value = b'{"connections": []}'
            empty = happ_stats.live_connections()
        samples = payload['traffic_samples']
        self.assertEqual([sample['download_bytes'] for sample in samples], [2048, 512, 1024])
        self.assertEqual([sample['user_key'] for sample in samples], ['personal-a', 'personal-a', 'personal-a'])
        self.assertEqual(len({sample['id'] for sample in samples}), 3)
        self.assertEqual(samples, again['traffic_samples'])
        self.assertEqual(set(samples[0]), {'id', 'user_key', 'download_bytes', 'upload_bytes'})
        self.assertEqual(payload['users'][0]['download_bytes'], 3584)
        self.assertEqual(payload['online_count'], 3)
        self.assertEqual(len(collector['connections']), 3)
        self.assertTrue(all(item.get('connection_key') and item.get('started_at') for item in collector['connections']))
        self.assertEqual(dt.datetime.fromisoformat(payload['sampled_at']).utcoffset(), dt.timedelta())
        self.assertEqual(empty['traffic_samples'], [])
        self.assertEqual(empty['users'], [])

    def test_connections_group_by_ip_and_protocol_and_sum_live_fields(self):
        items = [
            {'ip': '203.0.113.1', 'network': 'TCP', 'user_name': 'Alice', 'started_at': '2026-10-02T18:00:00+00:00', 'destination': 'old.example:443', 'duration_seconds': 120, 'download_bytes': 100, 'upload_bytes': 10},
            {'ip': '203.0.113.1', 'network': 'tcp', 'user_name': 'Alice', 'started_at': '2026-10-02T18:01:00+00:00', 'destination': 'new.example:443', 'duration_seconds': 30, 'download_bytes': 200, 'upload_bytes': 20},
            {'ip': '203.0.113.1', 'network': 'udp', 'user_name': 'Bob', 'started_at': '2026-10-02T18:02:00+00:00', 'destination': 'dns.example:53', 'duration_seconds': 40, 'download_bytes': 50, 'upload_bytes': 5},
        ]
        grouped = aggregate_connections(items)
        self.assertEqual(len(grouped), 2)
        tcp = next(item for item in grouped if item['network'] == 'TCP')
        self.assertEqual(tcp['user_name'], 'Alice')
        self.assertEqual(tcp['connections'], 2)
        self.assertEqual(tcp['destination'], 'new.example:443')
        self.assertEqual(tcp['duration_seconds'], 150)
        self.assertEqual(tcp['download_bytes'], 300)
        self.assertEqual(tcp['upload_bytes'], 30)
        udp = next(item for item in grouped if item['network'] == 'UDP')
        self.assertEqual(udp['user_name'], 'Bob')
        self.assertEqual(udp['download_bytes'], 50)

    def test_top_users_ranks_by_combined_download_and_upload(self):
        users = [
            {'user_name': f'User {index}', 'download_bytes': download, 'upload_bytes': upload}
            for index, (download, upload) in enumerate([(10, 0), (10, 50), (30, 30), (5, 0), (20, 0), (1, 0)], 1)
        ]
        ranked = rank_top_users(users)
        self.assertEqual([item['user_name'] for item in ranked], ['User 2', 'User 3', 'User 5', 'User 1', 'User 4'])

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