import base64
import hashlib
import http.client
import json
import re
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
import app
import happ_protocols as hp
import happ_server_ui
from happ_protocols_ui import render_protocols_panel
from happ_server import subscription_content
from happ_stats import authenticated_peers
from happ_users import HappUsers, now_utc, write_private_json

try:
    import tomllib
except ImportError:
    tomllib = None

TEST_CERTIFICATE = '''-----BEGIN CERTIFICATE-----
MIIBpjCCAUugAwIBAgIUdjuM4oYQ8awcTLX2OvPzWRO6VBcwCgYIKoZIzj0EAwIw
GzEZMBcGA1UEAwwQdnBuLmV4YW1wbGUudGVzdDAeFw0yNjEwMTAxMzAzMzFaFw0z
NjEwMDcxMzAzMzFaMBsxGTAXBgNVBAMMEHZwbi5leGFtcGxlLnRlc3QwWTATBgcq
hkjOPQIBBggqhkjOPQMBBwNCAAQBqpIaODz18qec3vCSaIla38IBRvxpaxVRh1Qb
vkqnKgBlc5qWv7U8EaMGr9/yzRql95yn/eGIpFJPMROXcIwbo20wazAdBgNVHQ4E
FgQUrhTlHFU3FAb4pwNZjn/CIEEI0sswHwYDVR0jBBgwFoAUrhTlHFU3FAb4pwNZ
jn/CIEEI0sswGwYDVR0RBBQwEoIQdnBuLmV4YW1wbGUudGVzdDAMBgNVHRMBAf8E
AjAAMAoGCCqGSM49BAMCA0kAMEYCIQCYA7Xt/YeneouQ8NiEhmhz+qZO43A8u6I3
zERP0tOhVQIhANCR3ZfSo5m20jSgJ99tdWECBsaoqAPfoly4aBxYHI9O
-----END CERTIFICATE-----
'''
TEST_KEY = '-----BEGIN PRIVATE KEY-----\nVEVTVA==\n-----END PRIVATE KEY-----\n'
# Produced by the real trusttunnel_endpoint 1.1.0 (`-c personal-test -a 203.0.113.10:9447 -n "Test User"`) for TEST_CERTIFICATE.
GOLDEN_DEEPLINK = 'tt://?AAEBARB2cG4uZXhhbXBsZS50ZXN0BQ1wZXJzb25hbC10ZXN0Bhh0ZXN0LXBhc3N3b3JkLTAxMjM0NTY3ODkCETIwMy4wLjExMy4xMDo5NDQ3CEGqMIIBpjCCAUugAwIBAgIUdjuM4oYQ8awcTLX2OvPzWRO6VBcwCgYIKoZIzj0EAwIwGzEZMBcGA1UEAwwQdnBuLmV4YW1wbGUudGVzdDAeFw0yNjEwMTAxMzAzMzFaFw0zNjEwMDcxMzAzMzFaMBsxGTAXBgNVBAMMEHZwbi5leGFtcGxlLnRlc3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQBqpIaODz18qec3vCSaIla38IBRvxpaxVRh1QbvkqnKgBlc5qWv7U8EaMGr9_yzRql95yn_eGIpFJPMROXcIwbo20wazAdBgNVHQ4EFgQUrhTlHFU3FAb4pwNZjn_CIEEI0sswHwYDVR0jBBgwFoAUrhTlHFU3FAb4pwNZjn_CIEEI0sswGwYDVR0RBBQwEoIQdnBuLmV4YW1wbGUudGVzdDAMBgNVHRMBAf8EAjAAMAoGCCqGSM49BAMCA0kAMEYCIQCYA7Xt_YeneouQ8NiEhmhz-qZO43A8u6I3zERP0tOhVQIhANCR3ZfSo5m20jSgJ99tdWECBsaoqAPfoly4aBxYHI9ODAlUZXN0IFVzZXI'


def decode_varint(data, offset):
    first = data[offset]
    size = 1 << (first >> 6)
    value = first & 0x3F
    for index in range(1, size):
        value = (value << 8) | data[offset + index]
    return value, offset + size


def decode_deeplink(link):
    assert link.startswith('tt://?')
    payload = link[len('tt://?'):]
    data = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
    fields, offset = [], 0
    while offset < len(data):
        tag, offset = decode_varint(data, offset)
        length, offset = decode_varint(data, offset)
        fields.append((tag, data[offset:offset + length]))
        offset += length
    return fields


def test_der():
    return base64.b64decode(''.join(TEST_CERTIFICATE.splitlines()[1:-1]))


class FakeSystem:
    def __init__(self):
        self.calls = []
        self.active = False
        self.enabled = False
        self.fail_restart = False

    def result(self, code, text=''):
        return types.SimpleNamespace(returncode=code, stdout=text)

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        if args[0] == hp.OPENSSL_BIN:
            Path(args[args.index('-out') + 1]).write_text(TEST_CERTIFICATE)
            Path(args[args.index('-keyout') + 1]).write_text(TEST_KEY)
            return self.result(0)
        action = args[1]
        if action == 'is-active':
            return self.result(0 if self.active else 3, 'active\n' if self.active else 'inactive\n')
        if action == 'is-enabled':
            return self.result(0 if self.enabled else 1, 'enabled\n' if self.enabled else 'disabled\n')
        if action == 'enable':
            self.enabled = True
        elif action == 'restart':
            if self.fail_restart:
                return self.result(1, 'failed')
            self.active = True
        elif action == 'stop':
            self.active = False
        elif action == 'disable':
            self.enabled = self.active = False
        return self.result(0)

    def count(self, action):
        return sum(1 for call in self.calls if call[0] == hp.SYSTEMCTL_BIN and call[1] == action)


class ProtocolFunctionTests(unittest.TestCase):
    def test_settings_defaults_and_validation(self):
        settings = hp.normalize_settings({})
        self.assertEqual(settings['trojan'], {'enabled': False, 'port': 9446})
        self.assertEqual(settings['hysteria2'], {'enabled': False, 'port': 9448})
        self.assertEqual(settings['trusttunnel'], {'enabled': False, 'port': 9447})
        self.assertEqual((settings['public_host'], settings['server_name'], settings['cert_path'], settings['key_path']), ('', '', '', ''))
        good = hp.normalize_settings({'trojan': {'enabled': True, 'port': '8443'}, 'hysteria2': {'enabled': True, 'port': '9555'}, 'trusttunnel': {'enabled': 1, 'port': 1024}, 'public_host': ' VPN.Example.COM. ', 'server_name': '2001:db8::1', 'cert_path': '/etc/ssl/a.pem', 'key_path': '/etc/ssl/a.key'})
        self.assertEqual((good['trojan']['port'], good['hysteria2'], good['trusttunnel']['enabled'], good['public_host'], good['server_name']), (8443, {'enabled': True, 'port': 9555}, True, 'vpn.example.com', '2001:db8::1'))
        for invalid in (
            {'trojan': {'port': 0}}, {'trojan': {'port': 443}}, {'trusttunnel': {'port': 1023}}, {'trojan': {'port': 65536}}, {'trojan': {'port': 'abc'}}, {'trojan': {'port': 9445}}, {'trusttunnel': {'port': hp.BRIDGE_PORT}},
            {'trojan': {'port': 9500}, 'trusttunnel': {'port': 9500}}, {'hysteria2': {'port': 1023}}, {'hysteria2': {'port': 9445}}, {'hysteria2': {'port': 'abc'}}, {'hysteria2': {'port': hp.BRIDGE_PORT}},
            {'trojan': {'port': 9500}, 'hysteria2': {'port': 9500}}, {'hysteria2': {'port': 9500}, 'trusttunnel': {'port': 9500}}, {'hysteria2': {'port': 9446}},
            {'public_host': 'bad host'}, {'server_name': 'a_b.example'}, {'cert_path': '/a.pem'}, {'key_path': '/a.key'},
            {'cert_path': 'relative.pem', 'key_path': 'relative.key'}, {'cert_path': '/tmp/a;b.pem', 'key_path': '/tmp/a.key'},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                hp.normalize_settings(invalid)
        with self.assertRaises(ValueError):
            hp.normalize_settings({'trojan': {'port': 9777}}, reserved=(9777,))
        with self.assertRaises(ValueError):
            hp.normalize_settings({'hysteria2': {'port': 9777}}, reserved=(9777,))
        self.assertEqual(hp.normalize_settings({'cert_path': '/a.pem', 'key_path': '/a.key', 'generated_for': 'old'})['generated_for'], '')

    def test_settings_saved_before_hysteria2_stay_loadable(self):
        legacy = hp.normalize_settings({'trojan': {'enabled': True, 'port': 9448}, 'trusttunnel': {'enabled': True, 'port': 9447}})
        self.assertEqual((legacy['trojan']['port'], legacy['hysteria2']), (9448, {'enabled': False, 'port': 9448}))
        with self.assertRaises(ValueError):
            hp.normalize_settings({'trojan': {'port': 9448}, 'hysteria2': {'enabled': True, 'port': 9448}})

    def test_varint_boundaries_follow_rfc9000(self):
        for value, encoded in ((0, b'\x00'), (63, b'\x3f'), (64, b'\x40\x40'), (16383, b'\x7f\xff'), (16384, b'\x80\x00\x40\x00'), ((1 << 30) - 1, b'\xbf\xff\xff\xff'), (1 << 30, b'\xc0\x00\x00\x00\x40\x00\x00\x00')):
            with self.subTest(value=value):
                self.assertEqual(hp.varint(value), encoded)
                self.assertEqual(decode_varint(encoded, 0), (value, len(encoded)))

    def test_deeplink_is_byte_identical_to_the_real_endpoint_export(self):
        link = hp.trusttunnel_deeplink('vpn.example.test', '203.0.113.10:9447', 'personal-test', 'test-password-0123456789', test_der(), 'Test User')
        self.assertEqual(link, GOLDEN_DEEPLINK)
        fields = decode_deeplink(link)
        self.assertEqual([tag for tag, _ in fields], [0, 1, 5, 6, 2, 8, 12])
        self.assertEqual(dict(fields)[0], b'\x01')
        self.assertEqual(dict(fields)[8], test_der())
        self.assertEqual(dict(fields)[12], b'Test User')
        self.assertNotIn(8, [tag for tag, _ in decode_deeplink(hp.trusttunnel_deeplink('vpn.example.test', '203.0.113.10:9447', 'u', 'p'))])

    def test_trojan_link_uses_standard_parameters_and_escapes_secrets(self):
        user = {'name': 'Алиса & Боб', 'trojan_password': 'a/b+c=d'}
        link = hp.trojan_link(user, '2001:db8::10', 9446, 'vpn.example.com', 'ab' * 32)
        parts = urlsplit(link)
        self.assertEqual((parts.scheme, parts.hostname, parts.port, unquote(parts.username)), ('trojan', '2001:db8::10', 9446, 'a/b+c=d'))
        self.assertEqual(parse_qs(parts.query), {'security': ['tls'], 'sni': ['vpn.example.com'], 'fp': ['chrome'], 'type': ['tcp'], 'allowInsecure': ['1'], 'pcs': ['ab' * 32]})
        self.assertEqual(unquote(parts.fragment), 'Алиса & Боб Trojan')
        trusted = parse_qs(urlsplit(hp.trojan_link(user, 'vpn.example.com', 443, 'vpn.example.com')).query)
        self.assertNotIn('allowInsecure', trusted)
        self.assertNotIn('pcs', trusted)

    def test_hysteria2_link_uses_the_official_uri_scheme_and_escapes_secrets(self):
        user = {'name': 'Алиса & Боб', 'hy2_password': 'a/b+c=d'}
        link = hp.hysteria2_link(user, '2001:db8::10', 9448, 'vpn.example.com', 'ab' * 32)
        parts = urlsplit(link)
        self.assertEqual((parts.scheme, parts.hostname, parts.port, unquote(parts.username), parts.path), ('hysteria2', '2001:db8::10', 9448, 'a/b+c=d', '/'))
        self.assertEqual(parse_qs(parts.query), {'sni': ['vpn.example.com'], 'insecure': ['1'], 'pinSHA256': ['ab' * 32]})
        self.assertEqual(unquote(parts.fragment), 'Алиса & Боб Hysteria2')
        self.assertEqual(parse_qs(urlsplit(hp.hysteria2_link(user, 'vpn.example.com', 443, 'vpn.example.com')).query), {'sni': ['vpn.example.com']})

    def test_trusttunnel_files_are_valid_toml_and_reject_unsafe_values(self):
        settings = {**hp.default_settings(), 'trusttunnel': {'enabled': True, 'port': 9447}}
        users = [{'id': 'abc', 'name': 'Alice', 'tt_password': 'pass-1_x'}, {'id': 'def', 'name': 'Bob', 'tt_password': 'pass-2_y'}]
        files = hp.render_trusttunnel(settings, users, '/etc/tls/a.crt', '/etc/tls/a.key', '/etc/tt', 'vpn.example.com')
        self.assertEqual(sorted(files), ['credentials.toml', 'hosts.toml', 'vpn.toml'])
        if tomllib is not None:
            vpn = tomllib.loads(files['vpn.toml'])
            self.assertEqual((vpn['listen_address'], vpn['credentials_file'], vpn['forward_protocol']['socks5']['address']), ('0.0.0.0:9447', '/etc/tt/credentials.toml', '127.0.0.1:19448'))
            self.assertEqual({'http1', 'http2', 'quic'}, set(vpn['listen_protocols']))
            self.assertEqual(tomllib.loads(files['hosts.toml'])['main_hosts'], [{'hostname': 'vpn.example.com', 'cert_chain_path': '/etc/tls/a.crt', 'private_key_path': '/etc/tls/a.key'}])
            self.assertEqual(tomllib.loads(files['credentials.toml'])['client'], [{'username': 'personal-abc', 'password': 'pass-1_x'}, {'username': 'personal-def', 'password': 'pass-2_y'}])
        else:
            self.assertIn('username = "personal-abc"', files['credentials.toml'])
        for unsafe in ('x"\ny', 'tab\there', 'quote"', 'back\\slash', 'ключ'):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                hp.render_trusttunnel(settings, [{'id': 'abc', 'name': 'A', 'tt_password': unsafe}], '/c', '/k', '/d', 'vpn.example.com')


class ProtocolFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config_path = self.root / 'config.json'
        self.public_path = self.root / 'happ-server.json'
        self.vip_uuid = '00000000-0000-0000-0000-000000000001'
        self.link = 'vless://' + self.vip_uuid + '@vpn.example.com:9445?security=reality&sni=example.com&pbk=test-only&sid=aabb&flow=xtls-rprx-vision#VIP'
        self.config = {
            'inbounds': [{'type': 'vless', 'tag': 'happ-vless-in', 'listen': '0.0.0.0', 'listen_port': 9445, 'users': [{'uuid': self.vip_uuid, 'flow': 'xtls-rprx-vision'}], 'tls': {'enabled': True}}],
            'outbounds': [{'type': 'direct', 'tag': 'direct'}],
            'route': {'final': 'direct'},
        }
        self.config_path.write_text(json.dumps(self.config))
        self.public_path.write_text(json.dumps({'link': self.link, 'server': 'vpn.example.com', 'port': 9445}))
        self.applied = []
        self.fail_apply = False
        self.system = FakeSystem()
        self.binary = self.root / 'trusttunnel_endpoint'
        self.binary.write_text('binary')
        sleeper = patch.object(hp.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.protocols = hp.HappProtocols(self.root, tls_dir=self.root / 'tls', trusttunnel_dir=self.root / 'trusttunnel', trusttunnel_bin=self.binary, run=self.system, group=None, fallback_host=lambda: 'vpn.example.com')
        self.manager = HappUsers(self.root, self.config_path, self.public_path, self.apply, protocols=self.protocols)
        self.manager.initialize()

    def apply(self, raw):
        if self.fail_apply:
            raise RuntimeError('sing-box rejected the configuration')
        candidate = json.loads(raw)
        self.manager.require_vip(candidate)
        self.applied.append(candidate)
        self.config_path.write_text(raw)

    def settings_form(self, trojan=False, trusttunnel=False, hysteria2=False, **extra):
        return {'trojan': {'enabled': trojan, 'port': 9446}, 'hysteria2': {'enabled': hysteria2, 'port': 9448}, 'trusttunnel': {'enabled': trusttunnel, 'port': 9447}, 'public_host': '', 'server_name': '', 'cert_path': '', 'key_path': '', **extra}

    def enable(self, trojan=True, trusttunnel=False, hysteria2=False, **extra):
        self.manager.apply_protocols(self.settings_form(trojan, trusttunnel, hysteria2, **extra))

    def live_config(self):
        return json.loads(self.config_path.read_text())

    def inbound(self, tag):
        return next((item for item in self.live_config()['inbounds'] if item['tag'] == tag), None)

    def registry_user(self, user_id):
        return next(item for item in self.manager.registry()['users'] if item['id'] == user_id)


class ProtocolManagerTests(ProtocolFixture):
    def test_disabled_protocols_leave_the_vless_config_untouched(self):
        self.manager.create('Alice')
        self.assertEqual([item['tag'] for item in self.live_config()['inbounds']], ['happ-vless-in'])
        before = len(self.applied)
        self.assertFalse(self.manager.reconcile_expired())
        self.assertEqual(len(self.applied), before)
        self.assertEqual(self.system.calls, [])

    def test_trojan_inbound_contains_active_users_only_and_reconcile_is_idempotent(self):
        alice, bob = self.manager.create('Alice'), self.manager.create('Bob')
        self.manager.action(bob, 'disable')
        self.enable()
        trojan = self.inbound(hp.TROJAN_TAG)
        self.assertEqual((trojan['type'], trojan['listen'], trojan['listen_port']), ('trojan', '0.0.0.0', 9446))
        self.assertEqual(trojan['users'], [{'name': 'personal-' + alice, 'password': self.registry_user(alice)['trojan_password']}])
        self.assertEqual(trojan['tls'], {'enabled': True, 'server_name': 'vpn.example.com', 'certificate_path': str(self.protocols.tls_dir / 'focusvpn-protocols.crt'), 'key_path': str(self.protocols.tls_dir / 'focusvpn-protocols.key')})
        self.assertEqual(self.live_config()['inbounds'][0]['tag'], 'happ-vless-in')
        self.assertEqual(self.live_config()['inbounds'][0]['users'][0], self.config['inbounds'][0]['users'][0])
        applied = len(self.applied)
        self.assertFalse(self.manager.reconcile_expired())
        self.assertEqual(len(self.applied), applied)
        self.assertIsNone(self.inbound(hp.BRIDGE_TAG))
        self.enable(trojan=False)
        self.assertIsNone(self.inbound(hp.TROJAN_TAG))
        self.assertFalse(self.manager.reconcile_expired())

    def test_trojan_is_not_listening_without_users(self):
        self.enable()
        self.assertIsNone(self.inbound(hp.TROJAN_TAG))
        self.manager.create('Alice')
        self.assertIsNotNone(self.inbound(hp.TROJAN_TAG))

    def test_hysteria2_inbound_contains_active_users_only_and_reconcile_is_idempotent(self):
        alice, bob = self.manager.create('Alice'), self.manager.create('Bob')
        self.manager.action(bob, 'disable')
        self.enable(trojan=False, hysteria2=True)
        inbound = self.inbound(hp.HYSTERIA2_TAG)
        self.assertEqual((inbound['type'], inbound['listen'], inbound['listen_port'], inbound['ignore_client_bandwidth']), ('hysteria2', '0.0.0.0', 9448, True))
        self.assertEqual(inbound['users'], [{'name': 'personal-' + alice, 'password': self.registry_user(alice)['hy2_password']}])
        self.assertEqual(inbound['tls'], {'enabled': True, 'server_name': 'vpn.example.com', 'certificate_path': str(self.protocols.tls_dir / 'focusvpn-protocols.crt'), 'key_path': str(self.protocols.tls_dir / 'focusvpn-protocols.key')})
        self.assertEqual([item['tag'] for item in self.live_config()['inbounds']], ['happ-vless-in', hp.HYSTERIA2_TAG])
        applied = len(self.applied)
        self.assertFalse(self.manager.reconcile_expired())
        self.assertEqual((len(self.applied), self.system.count('restart')), (applied, 0))
        self.enable(trojan=True, hysteria2=True)
        self.assertEqual([item['tag'] for item in self.live_config()['inbounds']], ['happ-vless-in', hp.TROJAN_TAG, hp.HYSTERIA2_TAG])
        self.enable(trojan=False, hysteria2=False)
        self.assertEqual([item['tag'] for item in self.live_config()['inbounds']], ['happ-vless-in'])
        self.assertFalse(self.manager.reconcile_expired())

    def test_hysteria2_is_not_listening_without_users_and_follows_user_changes(self):
        self.enable(trojan=False, hysteria2=True)
        self.assertIsNone(self.inbound(hp.HYSTERIA2_TAG))
        alice = self.manager.create('Alice')
        names = lambda: [item['name'] for item in self.inbound(hp.HYSTERIA2_TAG)['users']] if self.inbound(hp.HYSTERIA2_TAG) else []
        self.assertEqual(names(), ['personal-' + alice])
        self.manager.action(alice, 'disable')
        self.assertEqual(names(), [])
        self.manager.action(alice, 'enable')
        self.assertEqual(names(), ['personal-' + alice])
        self.manager.action(alice, 'delete')
        self.assertEqual(names(), [])

    def test_trusttunnel_uses_loopback_bridge_and_manages_the_service(self):
        alice = self.manager.create('Alice')
        self.enable(trojan=False, trusttunnel=True)
        bridge = self.inbound(hp.BRIDGE_TAG)
        self.assertEqual(bridge, {'type': 'socks', 'tag': hp.BRIDGE_TAG, 'listen': '127.0.0.1', 'listen_port': hp.BRIDGE_PORT})
        directory = self.protocols.trusttunnel_dir
        self.assertIn('listen_address = "0.0.0.0:9447"', (directory / 'vpn.toml').read_text())
        self.assertIn('address = "127.0.0.1:19448"', (directory / 'vpn.toml').read_text())
        self.assertIn('hostname = "vpn.example.com"', (directory / 'hosts.toml').read_text())
        credentials = (directory / 'credentials.toml').read_text()
        self.assertIn('username = "personal-' + alice + '"', credentials)
        self.assertIn(self.registry_user(alice)['tt_password'], credentials)
        self.assertTrue(self.system.active and self.system.enabled)
        self.assertEqual((self.system.count('enable'), self.system.count('restart')), (1, 1))
        self.manager.create('Bob')
        self.assertEqual(self.system.count('restart'), 2)
        self.assertEqual(credentials.count('[[client]]') + 1, (directory / 'credentials.toml').read_text().count('[[client]]'))
        self.assertFalse(self.manager.reconcile_expired())
        self.assertEqual(self.system.count('restart'), 2)
        self.enable(trojan=False, trusttunnel=False)
        self.assertIsNone(self.inbound(hp.BRIDGE_TAG))
        self.assertFalse((directory / 'credentials.toml').exists())
        self.assertFalse(self.system.active or self.system.enabled)
        self.assertEqual(self.manager.protocol_error, '')

    def test_user_lifecycle_keeps_both_protocols_in_sync(self):
        alice = self.manager.create('Alice')
        self.enable(trojan=True, trusttunnel=True)
        names = lambda: [item['name'] for item in self.inbound(hp.TROJAN_TAG)['users']] if self.inbound(hp.TROJAN_TAG) else []
        credentials = lambda: (self.protocols.trusttunnel_dir / 'credentials.toml').read_text() if (self.protocols.trusttunnel_dir / 'credentials.toml').exists() else ''
        self.assertEqual(names(), ['personal-' + alice])
        self.manager.action(alice, 'disable')
        self.assertEqual((names(), credentials()), ([], ''))
        self.assertIsNone(self.inbound(hp.BRIDGE_TAG))
        self.manager.action(alice, 'enable')
        self.assertEqual(names(), ['personal-' + alice])
        self.assertIn('personal-' + alice, credentials())
        user = self.registry_user(alice)
        registry = self.manager.registry()
        registry['users'][0]['expires_at'] = (now_utc().replace(microsecond=0)).isoformat()
        write_private_json(self.manager.registry_path, registry)
        self.assertTrue(self.manager.reconcile_expired())
        self.assertEqual((names(), credentials()), ([], ''))
        self.assertEqual(user['trojan_password'], self.registry_user(alice)['trojan_password'])
        self.manager.action(alice, 'update', 'Alice', '')
        self.manager.action(alice, 'delete')
        self.assertEqual(self.manager.registry()['users'], [])

    def test_registry_backfill_gives_existing_users_credentials_once(self):
        legacy = {'users': [{'id': 'legacy-1', 'uuid': '11111111-1111-4111-8111-111111111111', 'name': 'Legacy', 'enabled': True, 'expires_at': None, 'created_at': now_utc().isoformat()}]}
        write_private_json(self.manager.registry_path, legacy)
        self.manager.initialize()
        user = self.registry_user('legacy-1')
        for key in ('trojan_password', 'tt_password', 'hy2_password'):
            self.assertRegex(user[key], r'^[A-Za-z0-9_-]{24,}$')
        self.assertEqual(len({user['trojan_password'], user['tt_password'], user['hy2_password']}), 3)
        before = self.manager.registry_path.read_bytes()
        self.manager.initialize()
        self.assertEqual(self.manager.registry_path.read_bytes(), before)
        created = self.registry_user(self.manager.create('Fresh'))
        self.assertRegex(created['trojan_password'], r'^[A-Za-z0-9_-]{24,}$')
        self.assertRegex(created['hy2_password'], r'^[A-Za-z0-9_-]{24,}$')

    def test_registry_written_before_hysteria2_only_gains_the_missing_password(self):
        self.manager.create('Alice')
        registry = self.manager.registry()
        old_trojan, old_tt = registry['users'][0]['trojan_password'], registry['users'][0]['tt_password']
        del registry['users'][0]['hy2_password']
        write_private_json(self.manager.registry_path, registry)
        self.manager.initialize()
        user = self.manager.registry()['users'][0]
        self.assertEqual((user['trojan_password'], user['tt_password']), (old_trojan, old_tt))
        self.assertRegex(user['hy2_password'], r'^[A-Za-z0-9_-]{24,}$')

    def test_links_embed_or_pin_only_the_generated_certificate(self):
        alice = self.manager.create('Alice')
        self.enable(trojan=True, trusttunnel=True, hysteria2=True)
        links = self.manager.protocol_links()[alice]
        user = self.registry_user(alice)
        trojan = urlsplit(links['trojan'])
        query = parse_qs(trojan.query)
        self.assertEqual((trojan.scheme, trojan.hostname, trojan.port, unquote(trojan.username), unquote(trojan.fragment)), ('trojan', 'vpn.example.com', 9446, user['trojan_password'], 'Alice Trojan'))
        self.assertEqual((query['allowInsecure'], query['pcs'], query['sni']), (['1'], [hashlib.sha256(test_der()).hexdigest()], ['vpn.example.com']))
        hysteria2 = urlsplit(links['hysteria2'])
        self.assertEqual((hysteria2.scheme, hysteria2.hostname, hysteria2.port, unquote(hysteria2.username), unquote(hysteria2.fragment)), ('hysteria2', 'vpn.example.com', 9448, user['hy2_password'], 'Alice Hysteria2'))
        self.assertEqual(parse_qs(hysteria2.query), {'sni': ['vpn.example.com'], 'insecure': ['1'], 'pinSHA256': [hashlib.sha256(test_der()).hexdigest()]})
        fields = dict(decode_deeplink(links['trusttunnel']))
        self.assertEqual((fields[1], fields[2], fields[5], fields[6], fields[8], fields[12]), (b'vpn.example.com', b'vpn.example.com:9447', ('personal-' + alice).encode(), user['tt_password'].encode(), test_der(), b'Alice'))
        certificate, key = self.root / 'real.pem', self.root / 'real.key'
        certificate.write_text(TEST_CERTIFICATE)
        key.write_text(TEST_KEY)
        pattern = patch.object(hp, 'PATH_PATTERN', re.compile(r'.+'))
        pattern.start()
        self.addCleanup(pattern.stop)
        self.enable(trojan=True, trusttunnel=True, hysteria2=True, cert_path=str(certificate), key_path=str(key), public_host='198.51.100.7', server_name='vpn.example.org')
        trusted = self.manager.protocol_links()[alice]
        query = parse_qs(urlsplit(trusted['trojan']).query)
        self.assertEqual((urlsplit(trusted['trojan']).hostname, query['sni']), ('198.51.100.7', ['vpn.example.org']))
        self.assertNotIn('allowInsecure', query)
        self.assertNotIn('pcs', query)
        trusted_hysteria2 = urlsplit(trusted['hysteria2'])
        self.assertEqual((trusted_hysteria2.hostname, parse_qs(trusted_hysteria2.query)), ('198.51.100.7', {'sni': ['vpn.example.org']}))
        self.assertEqual(self.inbound(hp.HYSTERIA2_TAG)['tls']['certificate_path'], str(certificate))
        trusted_fields = dict(decode_deeplink(trusted['trusttunnel']))
        self.assertNotIn(8, trusted_fields)
        self.assertEqual((trusted_fields[1], trusted_fields[2]), (b'vpn.example.org', b'198.51.100.7:9447'))
        self.assertEqual(self.inbound(hp.TROJAN_TAG)['tls']['certificate_path'], str(certificate))
        self.assertEqual(self.protocols.settings()['generated_for'], '')
        self.assertIn('cert_chain_path = "' + certificate.as_posix() + '"', (self.protocols.trusttunnel_dir / 'hosts.toml').read_text())

    def test_certificate_is_generated_once_and_regenerated_on_request(self):
        self.manager.create('Alice')
        self.enable()
        generations = sum(1 for call in self.system.calls if call[0] == hp.OPENSSL_BIN)
        command = next(call for call in self.system.calls if call[0] == hp.OPENSSL_BIN)
        self.assertEqual((generations, command[command.index('-pkeyopt') + 1], command[command.index('-addext') + 1]), (1, 'ec_paramgen_curve:prime256v1', 'subjectAltName=DNS:vpn.example.com'))
        self.assertIn('basicConstraints=critical,CA:FALSE', command)
        self.assertEqual(self.protocols.settings()['generated_for'], 'vpn.example.com')
        self.enable(trojan=True, trusttunnel=True)
        self.assertEqual(sum(1 for call in self.system.calls if call[0] == hp.OPENSSL_BIN), 1)
        self.enable(trojan=True, regenerate=True)
        self.assertEqual(sum(1 for call in self.system.calls if call[0] == hp.OPENSSL_BIN), 2)
        self.enable(trojan=True, server_name='other.example.com')
        self.assertEqual(sum(1 for call in self.system.calls if call[0] == hp.OPENSSL_BIN), 3)
        latest = next(call for call in reversed(self.system.calls) if call[0] == hp.OPENSSL_BIN)
        self.assertEqual(latest[latest.index('-addext') + 1], 'subjectAltName=DNS:other.example.com')

    def test_failed_config_apply_restores_the_previous_settings(self):
        self.manager.create('Alice')
        original = self.config_path.read_text()
        self.fail_apply = True
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            self.enable(trojan=True, trusttunnel=True, hysteria2=True)
        self.assertFalse(self.protocols.settings()['trojan']['enabled'])
        self.assertFalse(self.protocols.settings()['hysteria2']['enabled'])
        self.assertEqual(self.config_path.read_text(), original)
        self.assertFalse((self.protocols.trusttunnel_dir / 'credentials.toml').exists())

    def test_failed_trusttunnel_start_reverts_sing_box_and_settings(self):
        self.manager.create('Alice')
        original = json.loads(self.config_path.read_text())
        self.system.fail_restart = True
        with self.assertRaisesRegex(RuntimeError, 'TrustTunnel не запустился'):
            self.enable(trojan=True, trusttunnel=True)
        self.assertEqual(self.live_config(), original)
        self.assertEqual(self.protocols.settings()['trojan'], {'enabled': False, 'port': 9446})
        self.assertFalse((self.protocols.trusttunnel_dir / 'credentials.toml').exists())
        self.assertFalse(self.system.enabled or self.system.active)

    def test_invalid_form_values_change_nothing(self):
        self.manager.create('Alice')
        before = self.config_path.read_text()
        for form in (self.settings_form(trojan=True, public_host='bad host'), {**self.settings_form(), 'trojan': {'enabled': True, 'port': 9445}}, self.settings_form(cert_path='/only/one.pem')):
            with self.subTest(form=form), self.assertRaises(ValueError):
                self.manager.apply_protocols(form)
        self.assertEqual(self.config_path.read_text(), before)
        self.assertFalse(self.protocols.path.exists())

    def test_vip_port_is_reserved_for_protocols(self):
        config = self.live_config()
        config['inbounds'][0]['listen_port'] = 9555
        self.config_path.write_text(json.dumps(config))
        self.manager.vip_path.unlink()
        state = json.loads(self.public_path.read_text())
        state['link'] = self.link.replace(':9445', ':9555')
        self.public_path.write_text(json.dumps(state))
        self.manager.initialize()
        with self.assertRaisesRegex(ValueError, '9555'):
            self.manager.apply_protocols({**self.settings_form(trojan=True), 'trojan': {'enabled': True, 'port': 9555}})

    def test_corrupt_settings_never_disable_protocols_silently(self):
        self.manager.create('Alice')
        self.enable()
        applied = len(self.applied)
        self.protocols.path.write_text('{broken')
        with self.assertRaises(ValueError):
            self.manager.reconcile_expired()
        self.assertEqual(len(self.applied), applied)
        self.assertIsNotNone(self.inbound(hp.TROJAN_TAG))
        self.protocols.path.unlink()
        self.assertEqual(self.protocols.settings(), hp.default_settings())

    def test_missing_endpoint_is_reported_without_breaking_user_changes(self):
        self.manager.create('Alice')
        self.enable(trojan=False, trusttunnel=True)
        self.binary.unlink()
        bob = self.manager.create('Bob')
        self.assertIn(bob, [item['id'] for item in self.manager.registry()['users']])
        self.assertIn('не установлен', self.manager.protocol_error)
        self.assertIn(bob, self.manager.protocol_links())
        self.binary.write_text('binary')
        self.assertFalse(self.manager.reconcile_expired())
        self.assertEqual(self.manager.protocol_error, '')
        self.assertIn('personal-' + bob, (self.protocols.trusttunnel_dir / 'credentials.toml').read_text())
        self.binary.unlink()
        with self.assertRaisesRegex(RuntimeError, 'не установлен'):
            self.enable(trojan=False, trusttunnel=True)

    def test_status_reports_endpoint_service_and_certificate(self):
        self.manager.create('Alice')
        self.enable(trojan=True, trusttunnel=True)
        status = self.protocols.status()
        self.assertEqual((status['binary'], status['service'], status['generated'], status['fingerprint']), (True, 'active', True, hashlib.sha256(test_der()).hexdigest()))
        self.binary.unlink()
        self.assertFalse(self.protocols.status()['binary'])


class ProtocolIntegrationTests(ProtocolFixture):
    def test_subscription_lists_trojan_and_hysteria2_after_vless_and_never_trusttunnel(self):
        alice = self.manager.create('Alice')
        self.enable(trojan=True, trusttunnel=True, hysteria2=True)
        user = self.manager.subscription_user(self.manager.subscription_token(self.registry_user(alice)))
        extra = self.manager.subscription_extra_links(user)
        links = self.manager.protocol_links()[alice]
        self.assertEqual(extra, [links['trojan'], links['hysteria2']])
        content, _ = subscription_content({**user, 'extra_links': extra}, {}, None)
        lines = content.decode('utf-8').splitlines()
        body = [line for line in lines if not line.startswith('#')]
        self.assertEqual((body[0].split(':', 1)[0], body[1:]), ('happ', [user['link'], extra[0], extra[1]]))
        self.assertEqual([line.split('://', 1)[0] for line in body[1:]], ['vless', 'trojan', 'hysteria2'])
        self.assertFalse(any(line.startswith('tt://') for line in lines))
        vip = self.manager.subscription_user(self.manager.subscription_token({'id': 'VIP', 'uuid': self.vip_uuid}))
        self.assertEqual(self.manager.subscription_extra_links(vip), [])
        self.assertEqual(subscription_content(vip, {}, None)[0].decode('utf-8').splitlines()[-1], self.link)
        self.enable(trojan=False, trusttunnel=True, hysteria2=True)
        self.assertEqual(self.manager.subscription_extra_links(user), [links['hysteria2']])
        self.enable(trojan=True, trusttunnel=True)
        self.assertEqual(self.manager.subscription_extra_links(user), [links['trojan']])
        self.enable(trojan=False, trusttunnel=True)
        self.assertEqual(self.manager.subscription_extra_links(user), [])

    def test_qr_endpoint_encodes_only_existing_protocol_links(self):
        alice = self.manager.create('Alice')
        self.enable(trojan=True, trusttunnel=True, hysteria2=True)
        links = self.manager.protocol_links()[alice]
        handler = object.__new__(app.Handler)
        for protocol in ('trojan', 'hysteria2', 'trusttunnel'):
            result = types.SimpleNamespace(returncode=0, stdout=b'<svg xmlns="http://www.w3.org/2000/svg"/>')
            with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app.subprocess, 'run', return_value=result) as encoder, patch.object(handler, 'send_binary') as send:
                handler.send_happ_qr(alice, protocol)
                self.assertEqual(encoder.call_args.kwargs['input'].decode('utf-8'), links[protocol])
                send.assert_called_once_with(result.stdout, 'image/svg+xml; charset=utf-8')
        for user_id, protocol in ((alice, 'wireguard'), ('missing', 'trojan'), (alice, '../etc/passwd')):
            with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app.subprocess, 'run') as encoder, patch.object(handler, 'send_empty') as send:
                handler.send_happ_qr(user_id, protocol)
                send.assert_called_once_with(404)
                encoder.assert_not_called()
        self.enable(trojan=False, trusttunnel=False)
        with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app.subprocess, 'run') as encoder, patch.object(handler, 'send_empty') as send:
            handler.send_happ_qr(alice, 'trojan')
            send.assert_called_once_with(404)
            encoder.assert_not_called()
        with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app.subprocess, 'run') as encoder, patch.object(handler, 'send_empty') as send:
            handler.send_happ_qr(alice, 'hysteria2')
            send.assert_called_once_with(404)
            encoder.assert_not_called()

    def test_happ_users_page_shows_protocol_buttons_only_for_available_links(self):
        alice = self.manager.create('Alice')
        self.enable(trojan=True, trusttunnel=True, hysteria2=True)
        users = [{**item, 'status': 'enabled', 'link': 'vless://example'} for item in self.manager.registry()['users']]
        links = self.manager.protocol_links()
        page = happ_server_ui.page(users, 'csrf-token', protocol_links=links)
        for protocol in ('trojan', 'hysteria2', 'trusttunnel'):
            self.assertIn(f'data-qr-url="/happ-users/{alice}/qr?protocol={protocol}"', page)
            self.assertIn('data-happ-link="' + links[alice][protocol].replace('&', '&amp;') + '"', page)
        self.assertIn('>Trojan</button>', page)
        self.assertIn('>Hysteria2 QR</button>', page)
        self.assertIn('>TrustTunnel QR</button>', page)
        self.assertIn('HAPP этот протокол не поддерживает', page)
        plain = happ_server_ui.page(users, 'csrf-token')
        self.assertNotIn('protocol=trojan', plain)
        self.assertNotIn('protocol=hysteria2', plain)
        self.assertNotIn('TrustTunnel', plain)
        only_trojan = happ_server_ui.page(users, 'csrf-token', protocol_links={alice: {'trojan': links[alice]['trojan']}})
        self.assertIn('protocol=trojan', only_trojan)
        self.assertNotIn('protocol=hysteria2', only_trojan)
        self.assertNotIn('protocol=trusttunnel', only_trojan)
        only_hysteria2 = happ_server_ui.page(users, 'csrf-token', protocol_links={alice: {'hysteria2': links[alice]['hysteria2']}})
        self.assertIn('protocol=hysteria2', only_hysteria2)
        self.assertNotIn('protocol=trojan', only_hysteria2)

    def test_settings_panel_renders_form_without_secrets(self):
        self.manager.create('Alice')
        self.enable(trojan=True)
        settings = self.protocols.settings()
        page = render_protocols_panel(settings, {'binary': True, 'service': 'active', 'fingerprint': 'ab' * 32}, 'csrf-token', 'ошибка <b>', 'vpn.example.com', 3)
        for fragment in ('action="/settings/happ/protocols"', 'name="csrf" value="csrf-token"', 'name="trojan_enabled" value="on" checked', 'name="hysteria2_enabled" value="on">', 'name="trusttunnel_enabled" value="on">', 'name="trojan_port"', 'name="hysteria2_port"', 'name="trusttunnel_port"', 'name="public_host"', 'name="server_name"', 'name="cert_path"', 'name="key_path"', 'name="regenerate_cert"', 'placeholder="vpn.example.com"', 'abababababababab…', '<span class="badge ok">active</span>', 'TCP 9446 (Trojan)', 'UDP 9448 (Hysteria2)', 'TCP+UDP 9447 (TrustTunnel)', 'HAPP этот протокол не поддерживает'):
            self.assertIn(fragment, page)
        self.assertIn('&lt;b&gt;', page)
        self.assertNotIn('<b>', page)
        self.assertNotIn(self.manager.registry()['users'][0]['trojan_password'], page)
        self.assertNotIn(self.manager.registry()['users'][0]['hy2_password'], page)
        missing = render_protocols_panel(settings, {'binary': False, 'service': 'inactive', 'fingerprint': ''}, 'csrf-token')
        self.assertIn('не установлен', missing)
        self.assertIn('Сертификат будет создан при сохранении', missing)

    def test_settings_post_requires_csrf_and_passes_only_form_fields(self):
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        manager = Mock()
        try:
            with patch.object(app.Handler, 'require_access', return_value=True), patch.object(app, 'HAPP_USERS', manager):
                fields = {'trojan_enabled': 'on', 'trojan_port': '9446', 'hysteria2_enabled': 'on', 'hysteria2_port': '9448', 'trusttunnel_port': '9447', 'public_host': 'vpn.example.com', 'regenerate_cert': 'on'}
                connection.request('POST', '/settings/happ/protocols', urlencode({'csrf': 'invalid', **fields}), {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 303)
                self.assertEqual(parse_qs(urlsplit(response.getheader('Location')).query)['kind'], ['error'])
                manager.apply_protocols.assert_not_called()
                connection.request('POST', '/settings/happ/protocols', urlencode({'csrf': app.CSRF_TOKEN, **fields}), {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                response.read()
                self.assertEqual(urlsplit(response.getheader('Location')).path, '/settings')
                self.assertIn('kind', parse_qs(urlsplit(response.getheader('Location')).query))
                manager.apply_protocols.assert_called_once_with({
                    'trojan': {'enabled': True, 'port': '9446'}, 'hysteria2': {'enabled': True, 'port': '9448'}, 'trusttunnel': {'enabled': False, 'port': '9447'},
                    'public_host': 'vpn.example.com', 'server_name': '', 'cert_path': '', 'key_path': '', 'regenerate': True,
                })
                manager.apply_protocols.side_effect = ValueError('Trojan: порт 9445 занят служебной функцией сервера.')
                connection.request('POST', '/settings/happ/protocols', urlencode({'csrf': app.CSRF_TOKEN, 'trojan_port': '9445', 'hysteria2_port': '9448', 'trusttunnel_port': '9447'}), {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                response.read()
                query = parse_qs(urlsplit(response.getheader('Location')).query)
                self.assertEqual((query['kind'], query['message']), (['error'], ['Trojan: порт 9445 занят служебной функцией сервера.']))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_statistics_attribute_trojan_and_hysteria2_connections_to_personal_users(self):
        def journal(message, pid='42'):
            return {'MESSAGE': message, '_PID': pid, '__REALTIME_TIMESTAMP': str(1790000000 * 1000000)}

        events = [
            journal('[111 0ms] inbound/trojan[happ-trojan-in]: inbound connection from 203.0.113.7:40000'),
            journal('[111 1ms] inbound/trojan[happ-trojan-in]: [personal-abc] inbound connection to example.com:443'),
            journal('[222 0ms] inbound/trojan[other-trojan]: inbound connection from 203.0.113.8:40001'),
            journal('[222 1ms] inbound/trojan[other-trojan]: [personal-abc] inbound connection to example.com:443'),
            journal('[333 0ms] inbound/vless[happ-vless-in]: inbound connection from 203.0.113.9:40002'),
            journal('[333 1ms] inbound/vless[happ-vless-in]: [personal-abc] inbound connection to example.org:443'),
            journal('[444 0ms] inbound/hysteria2[happ-hysteria2-in]: inbound connection from 203.0.113.10:40003'),
            journal('[444 1ms] inbound/hysteria2[happ-hysteria2-in]: [personal-abc] inbound connection to example.net:443'),
            journal('[555 0ms] inbound/hysteria2[other-hysteria2]: inbound connection from 203.0.113.11:40004'),
            journal('[555 1ms] inbound/hysteria2[other-hysteria2]: [personal-abc] inbound connection to example.net:443'),
        ]
        peers = authenticated_peers(events, {'personal-abc': 'Alice'}, set(), 'happ-vless-in', {})
        self.assertEqual(sorted(peers), [('203.0.113.10', 40003), ('203.0.113.7', 40000), ('203.0.113.9', 40002)])
        self.assertEqual((peers[('203.0.113.7', 40000)][0]['user_key'], peers[('203.0.113.7', 40000)][0]['destination']), ('personal-abc', 'example.com:443'))
        self.assertEqual((peers[('203.0.113.10', 40003)][0]['user_key'], peers[('203.0.113.10', 40003)][0]['destination']), ('personal-abc', 'example.net:443'))
        self.assertEqual((hp.TROJAN_TAG, hp.HYSTERIA2_TAG), ('happ-trojan-in', 'happ-hysteria2-in'))


if __name__ == '__main__':
    unittest.main()
