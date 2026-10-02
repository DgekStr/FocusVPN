import base64
import datetime as dt
import html
import http.client
import ipaddress
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs, quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from happ_users import HappUsers, build_user_config, user_link, now_utc, write_private_json
from happ_server import happ_add_link, subscription_content, SUBSCRIPTION_ANNOUNCEMENT, subscription_information_page
from happ_history import HappHistory
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
import app
import happ_server_ui


class HappUserTests(unittest.TestCase):
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
        self.original_public = self.public_path.read_bytes()
        self.applied = []
        self.manager = HappUsers(self.root, self.config_path, self.public_path, self.apply)
        self.manager.initialize()

    def apply(self, raw):
        candidate = json.loads(raw)
        self.manager.require_vip(candidate)
        self.applied.append(candidate)
        self.config_path.write_text(raw)

    def vip_intact(self):
        config = json.loads(self.config_path.read_text())
        self.assertEqual(config['inbounds'][0]['users'][0], self.config['inbounds'][0]['users'][0])
        self.assertEqual(self.manager.vip()['link'], self.link)
        self.assertEqual(self.public_path.read_bytes(), self.original_public)
        self.assertEqual(config['inbounds'][0]['listen_port'], 9445)

    def test_initialize_preserves_vip_without_apply(self):
        self.assertEqual(self.applied, [])
        self.vip_intact()
        if os.name == 'posix':
            self.assertEqual(self.manager.vip_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.manager.registry_path.stat().st_mode & 0o777, 0o600)

    def test_happ_deep_link_preserves_raw_configuration_uri(self):
        link = self.link.replace('#VIP', '#Alice%20%26%20Bob')
        wrapped = urlsplit(happ_add_link(link))
        self.assertEqual(wrapped.scheme, 'happ')
        self.assertEqual(wrapped.netloc, 'add')
        self.assertEqual(wrapped.query, '')
        self.assertEqual(wrapped.fragment, '')
        self.assertEqual(unquote(wrapped.path[1:]), link)
        self.assertIn('security%3Dreality', wrapped.path)
        self.assertIn('%2526', wrapped.path)

    def test_subscription_token_stable_private_and_revoked(self):
        user_id = self.manager.create('Alice')
        user = self.manager.users()[0]
        token = self.manager.subscription_token(user)
        self.assertEqual(self.manager.subscription_user(token)['id'], user_id)
        self.assertIsNone(self.manager.subscription_user('0' * 64))
        self.assertIsNone(self.manager.subscription_user(token[:-1]))
        reopened = HappUsers(self.root, self.config_path, self.public_path, self.apply)
        reopened.initialize()
        self.assertEqual(reopened.subscription_token(user), token)
        if os.name == 'posix':
            self.assertEqual(self.manager.subscription_key_path.stat().st_mode & 0o777, 0o600)
        self.manager.action(user_id, 'disable')
        self.assertIsNone(self.manager.subscription_user(token))
        self.manager.action(user_id, 'enable')
        self.assertEqual(self.manager.subscription_user(token)['id'], user_id)
        registry = self.manager.registry()
        registry['users'][0]['expires_at'] = (now_utc() - dt.timedelta(minutes=1)).isoformat()
        write_private_json(self.manager.registry_path, registry)
        self.assertIsNone(self.manager.subscription_user(token))
        self.manager.action(user_id, 'delete')
        self.assertIsNone(self.manager.subscription_user(token))
        self.vip_intact()

    def test_subscription_content_has_real_traffic_and_unlimited_total(self):
        user = {'name': 'Alice\r\ninvalid-header', 'link': self.link, 'expires_at': '2030-01-01T00:00:00+00:00'}
        content, headers = subscription_content(user, {'download_bytes': 120, 'upload_bytes': 30})
        self.assertEqual(headers['subscription-userinfo'], 'upload=30; download=120; total=0; expire=1893456000')
        self.assertEqual(headers['profile-update-interval'], '1')
        self.assertNotIn('\r', headers['profile-title'])
        title = base64.b64decode(headers['profile-title'].removeprefix('base64:')).decode('utf-8')
        self.assertTrue(title.startswith('\U0001f5a7 FocusVPN Alice'))
        self.assertLessEqual(len(title), 25)
        self.assertIn(('#profile-title: ' + headers['profile-title']).encode('ascii'), content)
        self.assertIn(b'#subscription-userinfo: upload=30; download=120; total=0', content)
        self.assertTrue(content.decode('utf-8').endswith(self.link + '\n'))

    def test_open_ipv4_network_keeps_admin_session_requirement(self):
        handler = object.__new__(app.Handler)
        handler.client_address = ('203.0.113.42', 40000)
        with patch.object(app, 'ACCESS_NETWORKS', (ipaddress.ip_network('0.0.0.0/0'),)):
            self.assertTrue(handler.vpn_client_allowed())
            with patch.object(handler, 'session_authenticated', return_value=False), patch.object(handler, 'redirect_to') as redirect:
                self.assertFalse(handler.require_access())
                redirect.assert_called_once_with('/login')
        with patch.dict(os.environ, {'FOCUSVPN_ADMIN_NETWORK': '0.0.0.0/0'}):
            self.assertIn(ipaddress.ip_address('203.0.113.42'), app.network_from_environment('FOCUSVPN_ADMIN_NETWORK', '192.168.0.0/24'))

    def test_subscription_announcement_preserves_short_text_and_emojis(self):
        content, headers = subscription_content({'name': 'Alice', 'link': self.link}, {}, 'http://127.0.0.1:9443/happ-info')
        text = base64.b64decode(headers['announce'].removeprefix('base64:')).decode('utf-8')
        self.assertEqual(text, SUBSCRIPTION_ANNOUNCEMENT)
        self.assertTrue(text.startswith('🔒Частный VPN'))
        self.assertTrue(text.endswith('исключительно для Вас ❤️'))
        self.assertIn('Не делитесь ссылкой🤬', text)
        self.assertIn('focuslens.dev', text)
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 200)
        headers['announce'].encode('ascii')
        self.assertNotIn('\n', headers['announce'])
        self.assertIn(('#announce: ' + headers['announce']).encode('ascii'), content)
        self.assertEqual(headers['profile-web-page-url'], 'http://127.0.0.1:9443/happ-info')
        page = subscription_information_page()
        self.assertIn('text-align:justify', page)
        for paragraph in SUBSCRIPTION_ANNOUNCEMENT.split('\n'):
            self.assertIn(html.escape(paragraph), page)
        self.assertNotIn(self.link, page)

    def test_subscription_buttons_and_user_counters(self):
        user_id = self.manager.create('Alice')
        urls = self.manager.subscription_urls('http://127.0.0.1:9443')
        self.assertEqual(happ_add_link(urls[user_id]), 'happ://add/' + urls[user_id])
        with patch.object(happ_server_ui, 'load_state', return_value={'server': 'vpn.example.com'}), patch.object(happ_server_ui, 'public_vless_link', return_value=self.link):
            page = happ_server_ui.page(self.manager.users(), traffic={'personal-' + user_id: {'download': '64.0 MB', 'upload': '8.0 MB'}}, subscriptions=urls)
        self.assertIn('data-happ-link="happ://add/' + urls[user_id] + '"', page)
        self.assertIn('data-happ-link="happ://add/' + urls['VIP'] + '"', page)
        self.assertIn('Открыть HAPP</a><span', page)
        self.assertIn('data-account-download>64.0 MB', page)
        self.assertIn('data-account-upload>8.0 MB', page)

    def test_subscription_http_isolated_updates_and_preserves_admin_auth(self):
        self.manager.create('Alice')
        self.manager.create('Bob')
        users = self.manager.users()
        history = HappHistory(self.root / 'stats')
        started = now_utc().isoformat()
        record = {'connection_key': 'alice-visit', 'started_at': started, 'user_key': 'personal-' + users[0]['id'], 'download_bytes': 100, 'upload_bytes': 20}
        other = {**record, 'connection_key': 'bob-visit', 'user_key': 'personal-' + users[1]['id'], 'download_bytes': 9999}
        history.ingest({'connections': [record, other]})
        token = self.manager.subscription_token(users[0])
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app, 'HAPP_HISTORY', history), patch.object(app.Handler, 'vpn_client_allowed', return_value=True):
                connection.request('GET', '/happ-subscription/' + token)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('subscription-userinfo'), 'upload=20; download=100; total=0')
                self.assertEqual(response.getheader('profile-update-interval'), '1')
                self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                announcement = response.getheader('announce')
                self.assertEqual(base64.b64decode(announcement.removeprefix('base64:')).decode('utf-8'), SUBSCRIPTION_ANNOUNCEMENT)
                self.assertTrue(response.getheader('profile-web-page-url').endswith('/happ-info'))
                body = response.read().decode('utf-8')
                self.assertIn('#announce: ' + announcement, body)
                self.assertIn(users[0]['link'], body)
                self.assertNotIn(users[1]['link'], body)
                connection.request('GET', '/happ-info')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                information = response.read().decode('utf-8')
                self.assertIn('text-align:justify', information)
                self.assertIn('focuslens.dev', information)
                self.assertNotIn(users[0]['link'], information)
                history.ingest({'connections': [{**record, 'download_bytes': 200}]})
                connection.request('GET', '/happ-subscription/' + token)
                response = connection.getresponse()
                self.assertEqual(response.getheader('subscription-userinfo'), 'upload=20; download=200; total=0')
                response.read()
                for route in ('/happ-server', '/happ-server/live'):
                    connection.request('GET', route)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 303)
                    response.read()
                self.manager.action(users[0]['id'], 'disable')
                for bad_token in (token, '0' * 64, token + '/extra'):
                    connection.request('GET', '/happ-subscription/' + bad_token)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 404)
                    self.assertEqual(response.read(), b'')
            with patch.object(app.Handler, 'vpn_client_allowed', return_value=False):
                connection.request('GET', '/happ-subscription/' + token)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_vip_and_personal_open_buttons_use_configuration_deep_links(self):
        self.manager.create('Alice & Bob')
        users = self.manager.users()
        with patch.object(happ_server_ui, 'load_state', return_value={'server': 'vpn.example.com', 'port': 9445}), patch.object(happ_server_ui, 'public_vless_link', return_value=self.link):
            page = happ_server_ui.page(users, 'test-csrf')
        for link in (self.link, users[0]['link']):
            expected = html.escape('happ://add/' + quote(link, safe=''), quote=True)
            self.assertIn('href="' + expected + '" data-happ-action="open" data-happ-link="' + expected + '"', page)
        self.vip_intact()

    def test_create_disable_enable_delete_preserve_vip(self):
        user_id = self.manager.create('Alice')
        self.assertEqual(len(json.loads(self.config_path.read_text())['inbounds'][0]['users']), 2)
        original_uuid = self.manager.registry()['users'][0]['uuid']
        self.manager.action(user_id, 'disable')
        self.assertEqual(len(json.loads(self.config_path.read_text())['inbounds'][0]['users']), 1)
        self.manager.action(user_id, 'enable')
        self.assertEqual(self.manager.registry()['users'][0]['uuid'], original_uuid)
        self.manager.action(user_id, 'delete')
        self.assertEqual(self.manager.registry()['users'], [])
        self.vip_intact()

    def test_expiry_revokes_personal_only_once(self):
        self.manager.create('Alice', (now_utc() + dt.timedelta(days=1)).isoformat())
        registry = self.manager.registry()
        registry['users'][0]['expires_at'] = (now_utc() - dt.timedelta(minutes=1)).isoformat()
        write_private_json(self.manager.registry_path, registry)
        self.assertTrue(self.manager.reconcile_expired())
        self.assertFalse(self.manager.reconcile_expired())
        self.assertEqual(self.manager.users()[0]['status'], 'expired')
        self.assertTrue(any(item['operation'] == 'expired' for item in self.manager.events()))
        self.vip_intact()

    def test_failed_apply_restores_registry(self):
        previous = self.manager.registry_path.read_bytes()
        with patch.object(self.manager, 'apply_config', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.manager.create('Alice')
        self.assertEqual(json.loads(self.manager.registry_path.read_bytes()), json.loads(previous))
        self.vip_intact()

    def test_link_reuses_vip_transport_and_endpoint(self):
        user_id = self.manager.create('Alice & Bob')
        user = self.manager.users()[0]
        link = urlsplit(user['link'])
        original = urlsplit(self.link)
        self.assertNotEqual(link.username, original.username)
        self.assertEqual(link.hostname, original.hostname)
        self.assertEqual(link.port, 9445)
        self.assertEqual(parse_qs(link.query), parse_qs(original.query))
        self.assertIn('%26', link.fragment)
        self.assertEqual(user['id'], user_id)

    def test_vip_cannot_be_deleted_via_personal_api(self):
        with self.assertRaises(ValueError):
            self.manager.action(self.vip_uuid, 'delete')
        self.vip_intact()

    def test_duplicate_name_and_past_expiry_rejected(self):
        self.manager.create('Alice')
        with self.assertRaises(ValueError):
            self.manager.create('alice')
        with self.assertRaises(ValueError):
            self.manager.create('Bob', (now_utc() - dt.timedelta(minutes=1)).isoformat())

    def test_raw_config_cannot_remove_or_change_vip(self):
        for field, value in [('users', []), ('listen_port', 9446), ('tls', {'enabled': False})]:
            candidate = json.loads(json.dumps(self.config))
            candidate['inbounds'][0][field] = value
            with self.assertRaises(ValueError):
                self.manager.require_vip(candidate)

    def test_duplicate_vip_uuid_rejected(self):
        registry = {'users': [{'id': 'test', 'uuid': self.vip_uuid, 'name': 'Alice', 'enabled': True}]}
        with self.assertRaises(ValueError):
            build_user_config(self.config, registry, self.manager.vip())

    def test_edit_keeps_uuid_and_renders_safe_labels(self):
        user_id = self.manager.create('Alice')
        original_uuid = self.manager.registry()['users'][0]['uuid']
        self.manager.action(user_id, 'update', '<script>unsafe</script>', (now_utc() + dt.timedelta(days=1)).isoformat())
        self.assertEqual(self.manager.registry()['users'][0]['uuid'], original_uuid)
        with patch.object(happ_server_ui, 'load_state', return_value={'server': 'vpn.example.com', 'port': 9445}), patch.object(happ_server_ui, 'public_vless_link', return_value=self.link):
            page = happ_server_ui.page(self.manager.users(), 'test-csrf', events=self.manager.events())
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>unsafe</script>', page)
        self.assertIn('/happ-users/' + user_id + '/qr', page)
        self.assertIn('VIP VLESS', page)


if __name__ == '__main__':
    unittest.main()