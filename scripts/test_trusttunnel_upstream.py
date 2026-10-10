import base64
import html
import http.client
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlparse

if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
import app
import happ_protocols
import trusttunnel_upstream as tt
import vless_monitor
from test_happ_protocols import GOLDEN_DEEPLINK, TEST_CERTIFICATE, test_der

try:
    import tomllib
except ImportError:
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
PEM = tt.normalize_certificate(TEST_CERTIFICATE)


def sing_box_entry(**changes):
    return {'type': 'trusttunnel', 'server': 'tt.example.com', 'server_port': 8443, 'username': 'tt-user', 'password': 'tt-pass-0123456789', **changes}


class FakeSystem:
    def __init__(self):
        self.calls = []
        self.active = set()
        self.enabled = set()
        self.failing = set()

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        action, unit = args[1], args[-1]
        code, text = 0, ''
        if action == 'is-active':
            code, text = (0, 'active') if unit in self.active else (3, 'inactive')
        elif action == 'enable':
            self.enabled.add(unit)
        elif action == 'restart':
            if unit in self.failing:
                code, text = 1, 'failed'
            else:
                self.active.add(unit)
        elif action == 'stop':
            self.active.discard(unit)
        elif action == 'disable':
            self.enabled.discard(unit)
            self.active.discard(unit)
        return types.SimpleNamespace(returncode=code, stdout=text)

    def count(self, action, unit=None):
        return sum(1 for call in self.calls if call[1] == action and (unit is None or call[-1] == unit))


class ManagerFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.root / 'trusttunnel_client'
        self.binary.write_text('binary')
        self.system = FakeSystem()
        self.listening = True
        self.manager = tt.TrustTunnelClients(
            self.root, client_dir=self.root / 'clients', binary=self.binary, run=self.system, group=None,
            wait_listener=lambda port: self.listening, port_free=lambda port: True,
        )

    def unit(self, tag):
        return tt.UNIT_TEMPLATE.format(tag)

    def profile(self, **changes):
        return tt.parse_profile(sing_box_entry(**changes))

    def stage_and_prepare(self, tag, profile=None):
        outbound = self.manager.stage(tag, profile or self.profile())
        self.manager.prepare()
        return outbound


class ProfileParsingTests(unittest.TestCase):
    def test_sing_box_style_profile_applies_defaults(self):
        profile = tt.parse_profile({'type': 'trusttunnel', 'server': '203.0.113.5', 'username': 'u', 'password': 'p'})
        self.assertEqual(profile, {
            'addresses': ['203.0.113.5:443'], 'server_name': '203.0.113.5', 'username': 'u', 'password': 'p', 'skip_verification': False,
            'certificate': '', 'upstream_protocol': 'http2', 'tls_profile': 'chrome', 'anti_dpi': False, 'client_random': '',
            'has_ipv6': True, 'name': '',
        })
        self.assertEqual(tt.primary_address(profile), ('203.0.113.5', 443))

    def test_full_profile_round_trips_through_the_exported_json(self):
        entry = sing_box_entry(
            addresses=['203.0.113.9:443', '[2001:db8::1]:8443'], tls={'server_name': 'VPN.Example.com.', 'insecure': True, 'certificate': TEST_CERTIFICATE.strip().splitlines()},
            upstream_protocol='http3', tls_profile='firefox', anti_dpi=True, client_random='aabbccdd/ff00ff00', has_ipv6=False, name='Office',
        )
        profile = tt.parse_profile(entry)
        self.assertEqual(profile['addresses'], ['tt.example.com:8443', '203.0.113.9:443', '[2001:db8::1]:8443'])
        self.assertEqual((profile['server_name'], profile['skip_verification'], profile['certificate']), ('vpn.example.com', True, PEM))
        exported = tt.export_outbound_json('auto-5', profile)
        self.assertEqual((exported['type'], exported['tag'], exported['server'], exported['server_port']), ('trusttunnel', 'auto-5', 'tt.example.com', 8443))
        self.assertEqual(exported['addresses'], ['203.0.113.9:443', '[2001:db8::1]:8443'])
        self.assertEqual(tt.parse_profile(exported), profile)
        minimal = tt.export_outbound_json('auto-6', tt.parse_profile(sing_box_entry()))
        self.assertEqual(set(minimal), {'type', 'tag', 'server', 'server_port', 'username', 'password'})

    def test_invalid_values_are_rejected_with_specific_errors(self):
        cases = (
            ({'username': ''}, 'Логин'), ({'password': 'with space'}, 'Пароль'), ({'password': 'пароль'}, 'Пароль'), ({'username': 5}, 'Логин'),
            ({'server_port': 0}, 'порт'), ({'server_port': 70000}, 'порт'), ({'server_port': 'x'}, 'порт'), ({'server_port': True}, 'порт'),
            ({'server': 'bad host'}, 'Адрес сервера'), ({'server': ''}, 'server и server_port'), ({'tls': {'insecure': 'yes'}}, 'true или false'),
            ({'tls': {'unknown': 1}}, 'tls допускает'), ({'tls': 'x'}, 'tls допускает'), ({'upstream_protocol': 'quic'}, 'http2 или http3'),
            ({'tls_profile': 'curl'}, 'tls_profile'), ({'client_random': 'zz'}, 'client_random'), ({'anti_dpi': 1}, 'anti_dpi'),
            ({'has_ipv6': 'no'}, 'has_ipv6'), ({'tls': {'certificate': 'not a certificate'}}, 'PEM'), ({'tls': {'certificate': 5}}, 'PEM'),
            ({'addresses': ['[2001:db8::1']}, 'IPv6'), ({'addresses': ['a.example:99999']}, 'порт'), ({'unknown_field': 1}, 'неизвестные поля'),
            ({'name': 'x' * 81}, 'Имя'), ({'addresses': ['h%d.example' % index for index in range(9)]}, 'от 1 до 8'),
        )
        for changes, fragment in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, fragment):
                    tt.parse_profile(sing_box_entry(**changes))
        with self.assertRaises(ValueError):
            tt.parse_profile(None)
        with self.assertRaises(ValueError):
            tt.parse_profile({'type': 'trusttunnel', 'username': 'u', 'password': 'p'})

    def test_official_client_config_shape_is_accepted_and_foreign_sections_are_ignored(self):
        entry = {
            'loglevel': 'info', 'vpn_mode': 'selective', 'killswitch_enabled': False, 'exclusions': ['example.com'],
            'endpoint': {
                'hostname': 'vpn.example.com', 'addresses': ['203.0.113.5:443'], 'username': 'u', 'password': 'p', 'certificate': TEST_CERTIFICATE,
                'skip_verification': False, 'upstream_protocol': 'http3', 'tls_profile': 'safari', 'dns_upstreams': ['tls://1.1.1.1'], 'has_ipv6': False,
            },
            'listener': {'tun': {'mtu_size': 1350}},
        }
        self.assertTrue(tt.is_trusttunnel_entry(entry))
        profile = tt.parse_profile(entry)
        self.assertEqual((profile['server_name'], profile['addresses'], profile['upstream_protocol'], profile['tls_profile'], profile['has_ipv6']), ('vpn.example.com', ['203.0.113.5:443'], 'http3', 'safari', False))
        self.assertEqual(profile['certificate'], PEM)
        rendered = tt.render_client_toml(profile, 19500, tt.SOCKS_USER, 'x' * 40)
        self.assertIn('killswitch_enabled = true', rendered)
        self.assertNotIn('example.com"]', rendered.split('exclusions', 1)[1].split('\n', 1)[0])
        with self.assertRaisesRegex(ValueError, 'Адреса сервера'):
            tt.parse_profile({'endpoint': {'hostname': 'vpn.example.com', 'addresses': None, 'username': 'u', 'password': 'p'}})
        with self.assertRaisesRegex(ValueError, 'addresses'):
            tt.parse_profile({'endpoint': {'hostname': 'vpn.example.com', 'username': 'u', 'password': 'p'}})

    def test_deeplink_from_the_real_endpoint_is_imported(self):
        profile = tt.parse_profile(GOLDEN_DEEPLINK)
        self.assertEqual((profile['addresses'], profile['server_name'], profile['username'], profile['password'], profile['name']), (['203.0.113.10:9447'], 'vpn.example.test', 'personal-test', 'test-password-0123456789', 'Test User'))
        self.assertEqual(profile['certificate'], tt.pem_from_der([test_der()]))
        self.assertEqual((profile['upstream_protocol'], profile['skip_verification'], profile['anti_dpi'], profile['has_ipv6']), ('http2', False, False, True))
        self.assertEqual(tt.parse_profile({'link': GOLDEN_DEEPLINK}), profile)
        self.assertEqual(tt.parse_profile({'type': 'trusttunnel', 'url': GOLDEN_DEEPLINK}), profile)
        self.assertTrue(tt.is_trusttunnel_entry(' ' + GOLDEN_DEEPLINK.upper()[:5] + GOLDEN_DEEPLINK[5:]))

    def test_deeplink_protocol_flags_and_certificate_chains(self):
        der = test_der()
        fields = (
            happ_protocols.tlv(0, happ_protocols.varint(2)) + happ_protocols.tlv(1, b'vpn.example.com') + happ_protocols.tlv(2, b'203.0.113.5:443') + happ_protocols.tlv(2, b'203.0.113.6')
            + happ_protocols.tlv(4, b'\x00') + happ_protocols.tlv(5, b'u') + happ_protocols.tlv(6, b'p') + happ_protocols.tlv(7, b'\x01') + happ_protocols.tlv(8, der) + happ_protocols.tlv(8, der)
            + happ_protocols.tlv(9, b'\x02') + happ_protocols.tlv(10, b'\x01') + happ_protocols.tlv(11, b'aabb/ff00') + happ_protocols.tlv(99, b'future')
        )
        link = 'tt://?' + base64.urlsafe_b64encode(fields).rstrip(b'=').decode('ascii')
        profile = tt.parse_profile(link)
        self.assertEqual(profile['addresses'], ['203.0.113.5:443', '203.0.113.6:443'])
        self.assertEqual((profile['upstream_protocol'], profile['skip_verification'], profile['anti_dpi'], profile['has_ipv6'], profile['client_random']), ('http3', True, True, False, 'aabb/ff00'))
        self.assertEqual(profile['certificate'].count('BEGIN CERTIFICATE'), 2)
        for invalid in ('tt:/x', 'tt://?***', 'tt://?AAAA', 'tt://?' + base64.urlsafe_b64encode(happ_protocols.tlv(5, b'u')).decode().rstrip('='), 'http://example.com', 'tt://?' + base64.urlsafe_b64encode(b'\x01\x7f').decode()):
            with self.subTest(link=invalid), self.assertRaises(ValueError):
                tt.parse_profile(invalid)
        broken = (
            happ_protocols.tlv(0, happ_protocols.varint(2)) + happ_protocols.tlv(1, b'vpn.example.com') + happ_protocols.tlv(2, b'203.0.113.5:443')
            + happ_protocols.tlv(5, b'u') + happ_protocols.tlv(6, b'p') + happ_protocols.tlv(8, b'\x30\x82\x40\x00broken')
        )
        with self.assertRaisesRegex(ValueError, 'Сертификат'):
            tt.parse_profile('tt://?' + base64.urlsafe_b64encode(broken).rstrip(b'=').decode('ascii'))

    def test_entry_detection_does_not_claim_other_protocols(self):
        for entry in ({'type': 'vless'}, {'protocol': 'hysteria2'}, {'type': 'trojan', 'link': 'tt://?x'}, {'outbounds': []}, None, 'vless://x', 5):
            self.assertFalse(tt.is_trusttunnel_entry(entry), entry)
        for entry in ({'type': 'TrustTunnel'}, {'protocol': 'trust-tunnel'}, {'type': 'trust_tunnel'}, {'endpoint': {'hostname': 'h', 'addresses': []}}, {'link': 'tt://?x'}, 'tt://?x'):
            self.assertTrue(tt.is_trusttunnel_entry(entry), entry)

    def test_client_toml_is_fail_closed_private_and_valid(self):
        profile = self.profile_with_certificate()
        text = tt.render_client_toml(profile, 19507, tt.SOCKS_USER, 'p' * 40)
        for line in ('killswitch_enabled = true', 'vpn_mode = "general"', 'exclusions = []', '[endpoint]', 'hostname = "tt.example.com"', 'addresses = ["tt.example.com:8443"]',
                     'skip_verification = false', 'upstream_protocol = "http2"', '[listener.socks]', 'address = "127.0.0.1:19507"', 'username = "focusvpn"', 'password = "' + 'p' * 40 + '"'):
            self.assertIn(line, text)
        self.assertNotIn('listener.tun', text)
        self.assertNotIn('killswitch_enabled = false', text)
        if tomllib is not None:
            data = tomllib.loads(text)
            self.assertEqual((data['killswitch_enabled'], data['exclusions'], data['vpn_mode']), (True, [], 'general'))
            self.assertEqual(data['endpoint']['certificate'], PEM)
            self.assertEqual(data['listener']['socks'], {'address': '127.0.0.1:19507', 'username': 'focusvpn', 'password': 'p' * 40})
            tricky = tt.parse_profile(sing_box_entry(password='a"b\\c\'d#e', username='x]y'))
            parsed = tomllib.loads(tt.render_client_toml(tricky, 19500, 'focusvpn', 'q' * 40))
            self.assertEqual((parsed['endpoint']['username'], parsed['endpoint']['password']), ('x]y', 'a"b\\c\'d#e'))
        with self.assertRaises(ValueError):
            tt.render_client_toml({**profile, 'password': 'line\nbreak'}, 19500, 'focusvpn', 'x')
        self.assertEqual(tt.render_client_toml(tt.parse_profile(sing_box_entry()), 19500, 'u', 'p').count('certificate = ""'), 1)

    def profile_with_certificate(self):
        return tt.parse_profile(sing_box_entry(tls={'certificate': TEST_CERTIFICATE}))


class ManagerTests(ManagerFixture):
    def test_stage_alone_never_touches_disk_or_services(self):
        outbound = self.manager.stage('auto-11', self.profile())
        self.assertEqual((outbound['type'], outbound['server'], outbound['version'], outbound['username']), ('socks', '127.0.0.1', '5', 'focusvpn'))
        self.assertEqual(outbound['server_port'], 19500)
        self.assertEqual(len(outbound['password']), 40)
        self.assertFalse((self.root / 'trusttunnel-servers.json').exists())
        self.assertFalse((self.root / 'clients').exists())
        self.assertEqual(self.system.calls, [])
        self.assertTrue(self.manager.owns('auto-11'))
        self.manager.discard(['auto-11'])
        self.assertFalse(self.manager.owns('auto-11'))

    def test_prepare_writes_private_files_starts_the_unit_and_saves_the_registry(self):
        outbound = self.stage_and_prepare('auto-11')
        unit = self.unit('auto-11')
        self.assertEqual((self.system.count('enable', unit), self.system.count('restart', unit)), (1, 1))
        self.assertIn(unit, self.system.active)
        config = (self.root / 'clients' / 'auto-11.toml').read_text(encoding='utf-8')
        self.assertIn('password = "' + outbound['password'] + '"', config)
        self.assertIn('address = "127.0.0.1:19500"', config)
        registry = json.loads((self.root / 'trusttunnel-servers.json').read_text(encoding='utf-8'))
        self.assertEqual(registry['servers']['auto-11']['port'], 19500)
        self.assertEqual(registry['servers']['auto-11']['profile']['username'], 'tt-user')
        self.assertEqual(self.manager.load()['servers']['auto-11']['profile'], self.profile())
        self.assertEqual(self.manager.outbound(self.manager.load(), 'auto-11'), outbound)
        self.assertEqual(self.manager.pending, {})
        if os.name == 'posix':
            self.assertEqual((self.root / 'trusttunnel-servers.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root / 'clients' / 'auto-11.toml').stat().st_mode & 0o777, 0o640)
            self.assertEqual((self.root / 'clients').stat().st_mode & 0o777, 0o750)

    def test_replacement_keeps_the_port_but_changes_the_socks_password_with_the_profile(self):
        first = self.stage_and_prepare('auto-11')
        second = self.stage_and_prepare('auto-11', self.profile(password='another-pass-9876543210'))
        self.assertEqual(first['server_port'], second['server_port'])
        self.assertNotEqual(first['password'], second['password'])
        self.assertEqual(self.system.count('restart', self.unit('auto-11')), 2)
        same = self.stage_and_prepare('auto-11', self.profile(password='another-pass-9876543210'))
        self.assertEqual(same, second)
        self.assertEqual(self.system.count('restart', self.unit('auto-11')), 2)

    def test_servers_get_distinct_ports_and_exhaustion_is_reported(self):
        for tag in ('auto-11', 'auto-12', 'auto-13'):
            self.manager.stage(tag, self.profile())
        self.assertEqual([self.manager.pending[tag]['port'] for tag in ('auto-11', 'auto-12', 'auto-13')], [19500, 19501, 19502])
        manager = tt.TrustTunnelClients(self.root / 'small', client_dir=self.root / 'small-clients', binary=self.binary, run=self.system, group=None, ports=range(19500, 19502), wait_listener=lambda port: True, port_free=lambda port: True)
        manager.stage('a', self.profile())
        manager.stage('b', self.profile())
        with self.assertRaisesRegex(ValueError, 'Нет свободных портов'):
            manager.stage('c', self.profile())
        busy = tt.TrustTunnelClients(self.root / 'busy', client_dir=self.root / 'busy-clients', binary=self.binary, run=self.system, group=None, wait_listener=lambda port: True, port_free=lambda port: port != 19500)
        self.assertEqual(busy.stage('a', self.profile())['server_port'], 19501)

    def test_failed_start_rolls_back_new_servers_and_restores_changed_ones(self):
        first = self.stage_and_prepare('auto-11')
        before = (self.root / 'clients' / 'auto-11.toml').read_bytes()
        self.manager.stage('auto-11', self.profile(password='changed-pass-0000000000'))
        self.manager.stage('auto-12', self.profile())
        self.system.failing.add(self.unit('auto-12'))
        with self.assertRaisesRegex(RuntimeError, 'не запустился'):
            self.manager.prepare()
        self.assertEqual((self.root / 'clients' / 'auto-11.toml').read_bytes(), before)
        self.assertFalse((self.root / 'clients' / 'auto-12.toml').exists())
        self.assertEqual(set(self.manager.load()['servers']), {'auto-11'})
        self.assertEqual(self.manager.outbound(self.manager.load(), 'auto-11'), first)
        self.assertEqual(self.manager.pending, {})
        self.assertIn(self.unit('auto-11'), self.system.active)
        self.assertNotIn(self.unit('auto-12'), self.system.enabled)

    def test_listener_that_never_opens_stops_the_unit_and_aborts(self):
        self.listening = False
        self.manager.stage('auto-11', self.profile())
        with self.assertRaisesRegex(RuntimeError, 'не начал слушать'):
            self.manager.prepare()
        self.assertNotIn(self.unit('auto-11'), self.system.active)
        self.assertFalse((self.root / 'trusttunnel-servers.json').exists())
        self.assertFalse((self.root / 'clients' / 'auto-11.toml').exists())

    def test_rollback_without_a_snapshot_is_harmless(self):
        self.manager.rollback()
        self.manager.stage('auto-11', self.profile())
        self.manager.rollback()
        self.assertEqual(self.manager.pending, {})

    def test_missing_client_binary_blocks_staging_with_a_clear_message(self):
        self.binary.unlink()
        self.assertFalse(self.manager.available())
        with self.assertRaisesRegex(ValueError, 'не установлен'):
            self.manager.stage('auto-11', self.profile())
        with self.assertRaises(ValueError):
            self.manager.stage('bad tag', self.profile())

    def test_finalize_removes_only_servers_the_config_no_longer_lists(self):
        for tag in ('auto-11', 'auto-12'):
            self.stage_and_prepare(tag)
        config = {'outbounds': [{'type': 'socks', 'tag': 'auto-11'}, {'type': 'vless', 'tag': 'auto-12'}, {'type': 'direct', 'tag': 'direct'}]}
        self.manager.finalize(config)
        self.assertEqual(set(self.manager.load()['servers']), {'auto-11'})
        self.assertTrue((self.root / 'clients' / 'auto-11.toml').exists())
        self.assertFalse((self.root / 'clients' / 'auto-12.toml').exists())
        self.assertEqual(self.system.count('disable', self.unit('auto-12')), 1)
        self.assertEqual(self.system.count('disable', self.unit('auto-11')), 0)

    def test_reconcile_starts_registered_clients_and_reports_a_missing_binary(self):
        self.stage_and_prepare('auto-11')
        self.system.active.clear()
        self.manager.reconcile({'outbounds': [{'type': 'socks', 'tag': 'auto-11'}]})
        self.assertIn(self.unit('auto-11'), self.system.active)
        self.assertEqual(self.manager.error, '')
        restarts = self.system.count('restart')
        self.manager.reconcile({'outbounds': [{'type': 'socks', 'tag': 'auto-11'}]})
        self.assertEqual(self.system.count('restart'), restarts)
        self.manager.reconcile({'outbounds': []})
        self.assertEqual(self.manager.load()['servers'], {})
        self.stage_and_prepare('auto-12')
        self.binary.unlink()
        self.manager.reconcile({'outbounds': [{'type': 'socks', 'tag': 'auto-12'}]})
        self.assertIn('не установлен', self.manager.error)

    def test_registry_survives_restart_and_corruption_is_reported(self):
        outbound = self.stage_and_prepare('auto-11')
        reopened = tt.TrustTunnelClients(self.root, client_dir=self.root / 'clients', binary=self.binary, run=self.system, group=None)
        self.assertEqual(reopened.outbound(reopened.load(), 'auto-11'), outbound)
        self.assertEqual(reopened.export('auto-11')['tag'], 'auto-11')
        self.assertEqual(reopened.display('auto-11'), ('tt.example.com', 8443))
        (self.root / 'trusttunnel-servers.json').write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'повреждён'):
            reopened.load()
        (self.root / 'trusttunnel-servers.json').write_text(json.dumps({'servers': {'auto-11': {'port': 5, 'profile': {}}, '../x': {}, 'ok': 'no'}}), encoding='utf-8')
        self.assertEqual(reopened.load()['servers'], {})

    def test_unit_template_is_hardened_and_runs_the_unprivileged_client(self):
        unit = (ROOT / 'server' / 'systemd' / 'trusttunnel-client@.service').read_text(encoding='utf-8')
        for line in ('User=sing-box', 'Group=sing-box', 'ExecStart=/opt/trusttunnel/trusttunnel_client --config /etc/sing-box/trusttunnel-clients/%i.toml', 'NoNewPrivileges=yes',
                     'ProtectSystem=strict', 'PrivateDevices=yes', 'CapabilityBoundingSet=', 'AmbientCapabilities=', 'Restart=on-failure', 'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK'):
            self.assertIn(line, unit)
        self.assertEqual(tt.UNIT_TEMPLATE, 'trusttunnel-client@{}.service')
        self.assertEqual(tt.CLIENT_DIR.as_posix(), '/etc/sing-box/trusttunnel-clients')
        self.assertEqual(tt.CLIENT_BIN.as_posix(), '/opt/trusttunnel/trusttunnel_client')


class ImportFixture(ManagerFixture):
    def setUp(self):
        super().setUp()
        self.config = {
            'outbounds': [{'type': 'urltest', 'tag': 'vless-auto', 'outbounds': ['auto-1']}, {'type': 'hysteria2', 'tag': 'auto-1', 'server': '203.0.113.1', 'server_port': 443, 'password': 'x'}, {'type': 'direct', 'tag': 'direct'}],
            'route': {'final': 'vless-auto'},
        }
        patcher = patch.object(app, 'TRUSTTUNNEL_CLIENTS', self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)

    def vless(self, port=443):
        return {'type': 'vless', 'server': '203.0.113.2', 'server_port': port, 'uuid': '00000000-0000-4000-8000-000000000002', 'tls': {'enabled': True, 'server_name': 'example.com'}}

    def all_protocols(self):
        return [
            self.vless(),
            {'type': 'hysteria2', 'server': '203.0.113.3', 'server_port': 443, 'password': 'hy2-secret', 'tls': {'enabled': True}},
            {'type': 'trojan', 'server': '203.0.113.4', 'server_port': 443, 'password': 'trojan-secret', 'tls': {'enabled': True}},
            {'type': 'shadowsocks', 'server': '203.0.113.5', 'server_port': 8388, 'method': 'aes-128-gcm', 'password': 'ss-secret'},
            sing_box_entry(),
        ]


class ImportTests(ImportFixture):
    def test_one_full_config_imports_every_protocol_and_ignores_service_outbounds(self):
        payload = {'log': {'level': 'info'}, 'outbounds': [{'type': 'direct', 'tag': 'direct'}, *self.all_protocols(), {'type': 'selector', 'tag': 'pick', 'outbounds': ['a']}, {'type': 'block', 'tag': 'block'}], 'route': {'final': 'pick'}}
        validator = Mock()
        tags, skipped = app.import_server_batch(json.dumps(payload), self.config, validator=validator)
        self.assertEqual((tags, skipped), (['auto-2', 'auto-3', 'auto-4', 'auto-5', 'auto-6'], []))
        self.assertEqual(validator.call_count, 5)
        servers = {item['tag']: item for item in app.managed_server_outbounds(self.config)}
        self.assertEqual([servers[tag]['type'] for tag in tags], ['vless', 'hysteria2', 'trojan', 'shadowsocks', 'socks'])
        self.assertEqual(self.config['outbounds'][0]['outbounds'], ['auto-1', *tags])
        self.assertEqual(self.config['route']['final'], 'vless-auto')
        socks = servers['auto-6']
        self.assertEqual((socks['server'], socks['server_port'], socks['version'], socks['username']), ('127.0.0.1', 19500, '5', 'focusvpn'))
        self.assertEqual(set(self.manager.pending), {'auto-6'})
        self.assertNotIn('tt-pass-0123456789', json.dumps(self.config))

    def test_array_mixes_objects_nested_configs_links_and_plain_tt_text(self):
        payload = [self.vless(), {'outbounds': [{'type': 'trojan', 'server': '203.0.113.4', 'server_port': 443, 'password': 'p', 'tls': {'enabled': True}}, {'type': 'direct', 'tag': 'direct'}]}, GOLDEN_DEEPLINK, sing_box_entry(server='tt2.example.com')]
        tags, skipped = app.import_server_batch(json.dumps(payload), self.config, validator=Mock())
        self.assertEqual((len(tags), skipped), (4, []))
        self.assertEqual(set(self.manager.pending), {tags[2], tags[3]})
        self.assertEqual(self.manager.pending[tags[2]]['profile']['username'], 'personal-test')
        self.manager.pending.clear()
        text = GOLDEN_DEEPLINK + '\n\n  ' + GOLDEN_DEEPLINK + '\n'
        config = json.loads(json.dumps(self.config))
        tags, skipped = app.import_server_batch(text, config, validator=Mock())
        self.assertEqual((len(tags), skipped), (2, []))
        self.assertEqual(sorted(self.manager.pending), sorted(tags))

    def test_labels_name_the_failing_element_of_nested_configs(self):
        payload = [{'outbounds': [self.vless(), sing_box_entry(password='bad pass'), {'protocol': 'wireguard'}]}, 'tt://?broken']
        tags, skipped = app.import_server_batch(json.dumps(payload), self.config, validator=Mock())
        self.assertEqual(tags, ['auto-2'])
        self.assertEqual(len(skipped), 3)
        self.assertTrue(skipped[0].startswith('Элемент 1.2: ') and 'Пароль' in skipped[0], skipped[0])
        self.assertTrue(skipped[1].startswith('Элемент 1.3: ') and 'WireGuard' in skipped[1], skipped[1])
        self.assertTrue(skipped[2].startswith('Элемент 2: '), skipped[2])
        self.assertEqual(self.manager.pending, {})

    def test_failed_validation_discards_the_staged_client_but_keeps_the_rest(self):
        calls = []

        def validator(data):
            calls.append(json.loads(data)['outbounds'][-1]['type'])
            if len(calls) == 2:
                raise RuntimeError('sing-box отклонил конфигурацию')

        tags, skipped = app.import_server_batch(json.dumps([sing_box_entry(), sing_box_entry(server='second.example.com'), self.vless()]), self.config, validator=validator)
        self.assertEqual(tags, ['auto-2', 'auto-3'])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(set(self.manager.pending), {'auto-2'})
        self.assertEqual(self.manager.pending['auto-2']['profile']['addresses'], ['tt.example.com:8443'])

    def test_batch_limit_counts_servers_not_service_outbounds(self):
        servers = [self.vless(port) for port in range(1000, 1016)]
        padded = {'outbounds': [{'type': 'direct', 'tag': f'd{index}'} for index in range(10)] + servers}
        tags, _ = app.import_server_batch(json.dumps(padded), json.loads(json.dumps(self.config)), validator=Mock())
        self.assertEqual(len(tags), 16)
        with self.assertRaisesRegex(ValueError, 'от 1 до 16'):
            app.import_server_batch(json.dumps(servers + [self.vless(2000)]), json.loads(json.dumps(self.config)))
        with self.assertRaisesRegex(ValueError, 'без указания общего tag'):
            app.import_server_batch(json.dumps(self.all_protocols()), self.config, requested_tag='x')

    def test_nothing_to_import_reports_every_reason_and_changes_nothing(self):
        before = json.loads(json.dumps(self.config))
        for payload in ('{broken', json.dumps({'outbounds': [{'type': 'direct', 'tag': 'direct'}]}), json.dumps([None, 5, 'vless://x', {'type': 'tuic'}]), json.dumps({'type': 'socks', 'server': '127.0.0.1', 'server_port': 1080})):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                app.import_server_batch(payload, self.config, validator=Mock())
            self.assertEqual(self.config, before)
        with self.assertRaisesRegex(ValueError, 'SOCKS-outbound не импортируется напрямую'):
            app.import_server_json(json.dumps({'type': 'socks'}), self.config)
        self.assertEqual(self.manager.pending, {})

    def test_trusttunnel_import_is_refused_without_the_manager_or_the_client(self):
        with patch.object(app, 'TRUSTTUNNEL_CLIENTS', None):
            with self.assertRaisesRegex(ValueError, 'TrustTunnel недоступен'):
                app.import_server_json(json.dumps(sing_box_entry()), self.config)
            tags, skipped = app.import_server_batch(json.dumps([self.vless(), sing_box_entry()]), self.config, validator=Mock())
            self.assertEqual((tags, len(skipped)), (['auto-2'], 1))
        self.binary.unlink()
        with self.assertRaisesRegex(ValueError, 'не установлен'):
            app.import_server_batch(json.dumps(sing_box_entry()), self.config, validator=Mock())

    def test_replacing_with_trusttunnel_json_keeps_tag_and_port(self):
        self.config['outbounds'].append({'type': 'vless', 'tag': 'auto-2', 'server': 'old.example.com'})
        tags, skipped = app.import_server_batch(json.dumps(sing_box_entry()), self.config, replace_tag='auto-2', validator=Mock())
        self.assertEqual((tags, skipped), (['auto-2'], []))
        replaced = next(item for item in self.config['outbounds'] if item['tag'] == 'auto-2')
        self.assertEqual((replaced['type'], replaced['server_port']), ('socks', 19500))
        self.manager.prepare()
        again = json.loads(json.dumps(self.config))
        app.import_server_batch(json.dumps(sing_box_entry(password='different-pass-111111')), again, replace_tag='auto-2', validator=Mock())
        self.assertEqual(next(item for item in again['outbounds'] if item['tag'] == 'auto-2')['server_port'], 19500)


class TransactionTests(ImportFixture):
    def run_import(self, payload, apply=None, **kwargs):
        events = []
        original_prepare, original_rollback, original_finalize = self.manager.prepare, self.manager.rollback, self.manager.finalize
        self.manager.prepare = lambda: (events.append('prepare'), original_prepare())[1]
        self.manager.rollback = lambda: (events.append('rollback'), original_rollback())[1]
        self.manager.finalize = lambda config: (events.append('finalize'), original_finalize(config))[1]

        def fake_apply(config):
            events.append('apply')
            return apply(config) if apply else True

        with patch.object(app, 'check_candidate'), patch.object(app, 'apply_configuration', side_effect=fake_apply):
            try:
                return events, app.import_and_apply(json.dumps(payload), self.config, **kwargs)
            except Exception as error:
                return events, error

    def test_clients_start_before_the_configuration_is_applied_and_orphans_go_after(self):
        events, result = self.run_import([sing_box_entry(), self.vless()])
        self.assertEqual(events, ['prepare', 'apply', 'finalize'])
        self.assertEqual((result[0], result[1], result[2]), (['auto-2', 'auto-3'], [], True))
        self.assertIn(self.unit('auto-2'), self.system.active)
        self.assertEqual(set(self.manager.load()['servers']), {'auto-2'})

    def test_failed_apply_rolls_the_clients_back_and_leaves_no_registry(self):
        def refuse(config):
            raise RuntimeError('sing-box не запустился с новой конфигурацией.')

        events, error = self.run_import(sing_box_entry(), apply=refuse)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(events, ['prepare', 'apply', 'rollback'])
        self.assertEqual(self.manager.load()['servers'], {})
        self.assertFalse((self.root / 'clients' / 'auto-2.toml').exists())
        self.assertEqual(self.manager.pending, {})
        self.assertNotIn(self.unit('auto-2'), self.system.active)

    def test_failed_client_start_aborts_before_the_configuration_is_touched(self):
        self.system.failing.add(self.unit('auto-2'))
        events, error = self.run_import(sing_box_entry())
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(events, ['prepare', 'rollback'])
        self.assertEqual(self.manager.pending, {})
        self.assertEqual(self.manager.load()['servers'], {})

    def test_invalid_payload_leaves_no_staged_state_and_import_without_trusttunnel_skips_the_manager(self):
        events, error = self.run_import('{broken')
        self.assertIsInstance(error, ValueError)
        self.assertEqual(events, [])
        events, result = self.run_import(self.vless())
        self.assertEqual(events, ['prepare', 'apply', 'finalize'])
        self.assertEqual(self.system.calls, [])

    def test_converting_a_trusttunnel_server_to_another_protocol_removes_its_client(self):
        events, result = self.run_import(sing_box_entry())
        self.assertEqual(result[0], ['auto-2'])
        self.assertTrue(self.manager.load()['servers'])
        events, result = self.run_import(self.vless(), replace_tag='auto-2')
        self.assertEqual(result[0], ['auto-2'])
        self.assertEqual(next(item for item in self.config['outbounds'] if item['tag'] == 'auto-2')['type'], 'vless')
        self.assertEqual(self.manager.load()['servers'], {})
        self.assertFalse((self.root / 'clients' / 'auto-2.toml').exists())
        self.assertEqual(self.system.count('disable', self.unit('auto-2')), 1)

    def test_deleting_a_trusttunnel_server_removes_the_client_after_apply(self):
        self.run_import(sing_box_entry())
        self.run_import(self.vless())
        tag = app.remove_server_json(self.config, 'auto-2')
        self.assertEqual(tag, 'auto-2')
        self.assertTrue(self.manager.owns('auto-2'))
        app.finalize_trusttunnel(self.config)
        self.assertFalse(self.manager.owns('auto-2'))
        self.assertEqual(self.system.count('disable', self.unit('auto-2')), 1)
        with patch.object(self.manager, 'finalize', side_effect=OSError('boom')):
            app.finalize_trusttunnel(self.config)
        with patch.object(app, 'TRUSTTUNNEL_CLIENTS', None):
            app.finalize_trusttunnel(self.config)

    def test_http_import_runs_the_transaction_and_reports_the_imported_tags(self):
        applied = []
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app.Handler, 'require_access', return_value=True), patch.object(app, 'load_config', return_value=self.config), patch.object(app, 'check_candidate'), \
                    patch.object(app, 'apply_configuration', side_effect=lambda config: applied.append(json.loads(json.dumps(config))) or True), patch.object(app, 'queue_server_checks'):
                body = urlencode({'csrf': app.CSRF_TOKEN, 'outbound_json': json.dumps({'outbounds': self.all_protocols()})})
                connection.request('POST', '/outbounds/import', body, {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 303)
                query = parse_qs(urlparse(response.getheader('Location')).query)
                self.assertIn('auto-2, auto-3, auto-4, auto-5, auto-6', query['message'][0])
                self.assertEqual(query['kind'], ['success'])
                self.assertEqual(len(applied), 1)
                self.assertEqual(len([item for item in applied[0]['outbounds'] if item['type'] != 'urltest' and item['type'] != 'direct']), 6)
                self.assertIn(self.unit('auto-6'), self.system.active)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


class PanelIntegrationTests(ImportFixture):
    def test_table_shows_the_trusttunnel_endpoint_and_an_editable_profile_instead_of_the_loopback_bridge(self):
        tags, _ = app.import_server_batch(json.dumps([sing_box_entry(tls={'server_name': 'vpn.example.com'}), self.vless()]), self.config, validator=Mock())
        self.manager.prepare()
        with patch.object(app, 'service_state', return_value='active'), patch.object(app, 'outbound_check_states', return_value={'checks': [{'tag': tag, 'state': 'unchecked', 'message': 'Не проверен'} for tag in ('auto-1', 'auto-2', 'auto-3')]}):
            page = app.render_outbounds_page(self.config, {})
        rows = page.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
        row = next(item for item in rows.split('</tr>') if '<strong>auto-2</strong>' in item)
        self.assertIn('<td>trusttunnel</td><td>tt.example.com</td><td>8443</td>', row)
        self.assertNotIn('19500', row.split('data-outbound-edit=')[0])
        edit = json.loads(html.unescape(row.split('data-outbound-edit="', 1)[1].split('"', 1)[0]))
        self.assertEqual((edit['type'], edit['tag'], edit['server'], edit['server_port'], edit['password']), ('trusttunnel', 'auto-2', 'tt.example.com', 8443, 'tt-pass-0123456789'))
        self.assertEqual(edit['tls'], {'server_name': 'vpn.example.com'})
        self.assertNotIn('"socks"', row)
        self.assertIn('<option value="auto-2">auto-2 · trusttunnel</option>', page)
        self.assertIn('<td>vless</td>', rows)
        self.assertIn('TrustTunnel', page.split('<h2>Импорт JSON</h2>', 1)[1].split('</form>', 1)[0])
        self.assertIn('tt://', page.split('<h2>Импорт JSON</h2>', 1)[1].split('</form>', 1)[0])

    def test_plain_socks_outbounds_and_a_broken_registry_are_rendered_without_failing(self):
        self.config['outbounds'].append({'type': 'socks', 'tag': 'manual', 'server': '198.51.100.7', 'server_port': 1080})
        view = app.server_view(self.config['outbounds'][-1])
        self.assertEqual((view['type'], view['server'], view['server_port']), ('socks', '198.51.100.7', 1080))
        (self.root / 'trusttunnel-servers.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(app.server_view(self.config['outbounds'][-1])['type'], 'socks')
        with patch.object(app, 'TRUSTTUNNEL_CLIENTS', None):
            self.assertEqual(app.server_view(self.config['outbounds'][-1])['edit'], self.config['outbounds'][-1])

    def test_bridge_outbounds_are_shared_with_happ_selected_as_route_and_checked_like_any_server(self):
        tags, _ = app.import_server_batch(json.dumps(sing_box_entry()), self.config, validator=Mock())
        self.assertEqual(tags, ['auto-2'])
        self.assertIn('auto-2', {item['tag'] for item in app.selectable_outbounds(self.config)})
        self.assertEqual(app.set_default_outbound(self.config, 'auto-2'), 'auto-2')
        happ = {'inbounds': [{'tag': 'happ-vless-in'}], 'outbounds': [{'type': 'direct', 'tag': 'focusvpn-wg-direct'}], 'route': {'final': 'auto-1'}}
        synchronized = app.synchronized_happ_config(self.config, happ, 'vless')
        self.assertIn(next(item for item in self.config['outbounds'] if item['tag'] == 'auto-2'), synchronized['outbounds'])
        self.assertEqual(synchronized['route']['final'], 'auto-2')
        self.assertIn('socks', vless_monitor.SERVER_TYPES)
        self.assertIn('auto-2', [item['tag'] for item in vless_monitor.managed_servers(self.config)])
        helper = (ROOT / 'server' / 'libexec' / 'focusvpn-outbound-test').read_text(encoding='utf-8')
        self.assertIn("('vless', 'hysteria2', 'trojan', 'shadowsocks', 'socks')", helper)
        import panel_ui
        with patch.object(panel_ui, 'CONFIG_PATH', self.root / 'config.json'):
            (self.root / 'config.json').write_text(json.dumps(self.config), encoding='utf-8')
            self.assertIn('auto-2', panel_ui.load_profile_tags())

    def test_check_fingerprint_changes_when_the_trusttunnel_profile_changes(self):
        tags, _ = app.import_server_batch(json.dumps(sing_box_entry()), self.config, validator=Mock())
        first = json.loads(json.dumps(next(item for item in self.config['outbounds'] if item['tag'] == 'auto-2')))
        self.manager.prepare()
        app.import_server_batch(json.dumps(sing_box_entry(password='rotated-pass-22222222')), self.config, replace_tag='auto-2', validator=Mock())
        second = next(item for item in self.config['outbounds'] if item['tag'] == 'auto-2')
        self.assertEqual(first['server_port'], second['server_port'])
        self.assertNotEqual(first['password'], second['password'])

    def test_startup_reconcile_is_best_effort(self):
        self.manager.stage('auto-9', self.profile())
        self.manager.prepare()
        self.system.active.clear()
        self.manager.reconcile(self.config)
        self.assertEqual(self.manager.load()['servers'], {})


if __name__ == '__main__':
    unittest.main()
