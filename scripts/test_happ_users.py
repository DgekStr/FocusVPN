import base64
import datetime as dt
from email.message import Message
import html
import http.client
from http.cookies import SimpleCookie
import ipaddress
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs, quote, unquote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from happ_users import HappUsers, build_user_config, user_link, now_utc, write_private_json
from happ_server import happ_add_link, subscription_content, HAPP_DIRECT_SITES, SUBSCRIPTION_ANNOUNCEMENT, subscription_information_page, update_interval_hours, validate_subscription_content, validate_update_minutes, vless_link_for_subscription
from happ_history import HappHistory
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
import app
import happ_server_ui
import panel_ui


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

    def run_key_setup(self, restart_error=False, validation_error=False):
        def command(arguments, **kwargs):
            if arguments[1:3] == ['generate', 'reality-keypair']:
                return CompletedProcess(arguments, 0, 'PrivateKey: ' + 'A' * 43 + '\nPublicKey: ' + 'B' * 43 + '\n')
            return CompletedProcess(arguments, 0, '')

        def backup(path, prefix):
            destination = self.root / (prefix + '.backup')
            destination.write_bytes(path.read_bytes())
            return destination

        with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app, 'HAPP_CONFIG_PATH', self.config_path), patch.object(app, 'HAPP_STATE_PATH', self.public_path), patch.object(app, 'command', side_effect=command) as service_command, patch.object(app, 'check_happ_candidate', side_effect=RuntimeError('invalid config') if validation_error else None) as check, patch.object(app, 'backup_file', side_effect=backup), patch.object(app, 'write_atomic_file', side_effect=lambda path, data, **kwargs: path.write_bytes(data)), patch.object(app, 'service_state', return_value='inactive'), patch.object(app, 'restart_happ_server', side_effect=RuntimeError('failed') if restart_error else None):
            app.generate_and_apply_happ_keys('vpn.example.com', 'www.cloudflare.com', '9445')
            check.assert_called_once()
            service_command.assert_any_call([app.SYSTEMCTL_BIN, 'enable', 'sing-box-happ-server'], timeout=30)

    def test_key_generation_updates_all_links_without_changing_user_ids(self):
        self.manager.create('Alice')
        before = self.manager.registry_path.read_bytes()
        token = self.manager.subscription_token(self.manager.users()[0])
        self.run_key_setup()
        state = json.loads(self.public_path.read_text())
        config = json.loads(self.config_path.read_text())
        self.assertEqual(state['public_key'], 'B' * 43)
        self.assertEqual(config['inbounds'][0]['tls']['reality']['private_key'], 'A' * 43)
        self.manager.require_vip(config)
        self.assertEqual(self.manager.registry_path.read_bytes(), before)
        self.assertEqual(self.manager.subscription_token(self.manager.users()[0]), token)
        self.assertEqual(parse_qs(urlsplit(self.manager.users()[0]['link']).query)['pbk'], ['B' * 43])

    def test_failed_key_setup_restores_config_state_and_vip(self):
        paths = [self.config_path, self.public_path, self.manager.vip_path]
        original = [path.read_bytes() for path in paths]
        with self.assertRaisesRegex(RuntimeError, 'восстановлены'):
            self.run_key_setup(restart_error=True)
        self.assertEqual([path.read_bytes() for path in paths], original)

    def test_key_setup_replaces_demo_vip_uuid_and_keeps_personal_users(self):
        demo_uuid = '00000000-0000-4000-8000-000000000001'
        config = json.loads(self.config_path.read_text())
        config['inbounds'][0]['users'][0]['uuid'] = demo_uuid
        self.config_path.write_text(json.dumps(config))
        state = json.loads(self.public_path.read_text())
        state['link'] = self.link.replace(self.vip_uuid, demo_uuid)
        self.public_path.write_text(json.dumps(state))
        self.manager.vip_path.unlink()
        self.manager.initialize()
        self.manager.create('Alice')
        registry_before = self.manager.registry_path.read_bytes()
        token_before = self.manager.subscription_token(self.manager.users()[0])
        self.run_key_setup()
        state = json.loads(self.public_path.read_text())
        self.assertNotEqual(state['uuid'], demo_uuid)
        self.assertEqual(urlsplit(self.manager.vip()['link']).username, state['uuid'])
        updated = json.loads(self.config_path.read_text())
        self.assertEqual(updated['inbounds'][0]['users'][0]['uuid'], state['uuid'])
        self.manager.require_vip(updated)
        self.assertEqual(self.manager.registry_path.read_bytes(), registry_before)
        self.assertEqual(self.manager.subscription_token(self.manager.users()[0]), token_before)

    def test_invalid_generated_candidate_does_not_write_files(self):
        paths = [self.config_path, self.public_path, self.manager.vip_path]
        original = [path.read_bytes() for path in paths]
        with self.assertRaisesRegex(RuntimeError, 'invalid config'):
            self.run_key_setup(validation_error=True)
        self.assertEqual([path.read_bytes() for path in paths], original)

    def test_first_login_wizard_disappears_after_key_setup(self):
        markup = app.render_shell('VPN', '<p>page</p>', 'outbounds', [])
        with patch.object(app, 'HAPP_CONFIG_PATH', self.config_path), patch.object(app, 'HAPP_STATE_PATH', self.public_path):
            self.assertTrue(app.happ_setup_needed())
            page = app.add_happ_setup_dialog(markup, '192.0.2.1:7445')
            self.assertIn('data-happ-setup-dialog', page)
            self.assertIn('name="csrf"', page)
            self.assertIn('action="/settings/happ/keys"', page)
            self.run_key_setup()
            self.assertFalse(app.happ_setup_needed())
            self.assertEqual(app.add_happ_setup_dialog(markup), markup)

    def test_key_form_does_not_include_private_key(self):
        form = app.happ_key_form('vpn.example.com', 'www.cloudflare.com', 9445, initial=True)
        self.assertIn('data-happ-setup-close', form)
        self.assertIn('name="confirm_happ_keys"', form)
        self.assertNotIn('private_key', form)

    def test_key_setup_post_requires_confirmation_and_csrf(self):
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app.Handler, 'require_access', return_value=True), patch.object(app, 'generate_and_apply_happ_keys') as generate:
                for fields, expected in (({'csrf': 'invalid', 'confirm_happ_keys': 'generate'}, 0), ({'csrf': app.CSRF_TOKEN}, 0), ({'csrf': app.CSRF_TOKEN, 'confirm_happ_keys': 'generate', 'happ_server': 'vpn.example.com', 'happ_sni': 'www.cloudflare.com', 'happ_port': '9445'}, 1)):
                    connection.request('POST', '/settings/happ/keys', urlencode(fields), {'Content-Type': 'application/x-www-form-urlencoded'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 303)
                    response.read()
                    self.assertEqual(generate.call_count, expected)
                generate.assert_called_once_with('vpn.example.com', 'www.cloudflare.com', '9445')
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

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

    def test_vless_public_host_uses_subscription_origin_and_preserves_transport(self):
        rewritten = vless_link_for_subscription(self.link, 'https://vpn.focuslens.dev:443/happ-subscription/demo-token')
        self.assertEqual(urlsplit(rewritten).hostname, 'vpn.focuslens.dev')
        self.assertEqual(urlsplit(rewritten).port, 9445)
        self.assertEqual(urlsplit(rewritten).query, urlsplit(self.link).query)
        self.assertEqual(urlsplit(rewritten).fragment, urlsplit(self.link).fragment)
        self.assertEqual(urlsplit(rewritten).username, urlsplit(self.link).username)
        self.assertEqual(vless_link_for_subscription(self.link, 'http://[2001:db8::10]:9443').split('@', 1)[1].split('?', 1)[0], '[2001:db8::10]:9445')

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
        self.assertEqual(
            base64.b64decode(headers['announce'].removeprefix('base64:')).decode('utf-8'),
            SUBSCRIPTION_ANNOUNCEMENT + '\nDL: 120 B / UL: 30 B',
        )
        self.assertIn(('#profile-title: ' + headers['profile-title']).encode('ascii'), content)
        self.assertIn(b'#subscription-userinfo: upload=30; download=120; total=0', content)
        self.assertTrue(content.decode('utf-8').endswith(self.link + '\n'))

    def test_subscription_content_uses_custom_title_and_announcement(self):
        user = {'name': 'Alice', 'link': self.link}
        content, headers = subscription_content(
            user,
            {'download_bytes': 120, 'upload_bytes': 30},
            title='Focus VPN',
            announcement='Тестовое объявление\nВторая строка',
        )
        title = base64.b64decode(headers['profile-title'].removeprefix('base64:')).decode('utf-8')
        announcement = base64.b64decode(headers['announce'].removeprefix('base64:')).decode('utf-8')
        self.assertEqual(title, 'Focus VPN Alice')
        self.assertEqual(announcement, 'Тестовое объявление\nВторая строка\nDL: 120 B / UL: 30 B')
        self.assertIn(('#profile-title: ' + headers['profile-title']).encode('ascii'), content)
        self.assertEqual(
            base64.b64decode(subscription_content(user, {'download_bytes': 0}, announcement='')[1]['announce'].removeprefix('base64:')).decode('utf-8'),
            'DL: 0 B / UL: 0 B',
        )
        with self.assertRaises(ValueError):
            validate_subscription_content('Название\nHeader: injected', 'Текст')
        with self.assertRaises(ValueError):
            validate_subscription_content('OK', 'x' * 2001)

    def test_update_minutes_accept_only_ten_to_six_hundred_whole_minutes(self):
        for value, expected in ((10, 10), ('10', 10), (' 60 ', 60), ('600', 600), (359, 359)):
            with self.subTest(value=value):
                self.assertEqual(validate_update_minutes(value), expected)
        for value in (9, 601, 0, -10, '', ' ', None, True, False, 'abc', '60.5', '1e2', '+60', '-10', '٣٠', '1' * 5, 60.0, [60]):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'от 10 до 600'):
                validate_update_minutes(value)

    def test_subscription_update_interval_is_whole_hours_rounded_from_minutes(self):
        user = {'name': 'Alice', 'link': self.link}
        expected = {10: '1', 29: '1', 30: '1', 60: '1', 89: '1', 90: '2', 119: '2', 120: '2', 150: '3', 359: '6', 600: '10'}
        for minutes, hours in expected.items():
            with self.subTest(minutes=minutes):
                content, headers = subscription_content(user, {}, update_minutes=minutes)
                self.assertEqual(headers['profile-update-interval'], hours)
                self.assertIn(('#profile-update-interval: ' + hours + '\n').encode('ascii'), content)
        self.assertEqual(subscription_content(user, {})[1]['profile-update-interval'], '1')
        for broken in (None, '', 'abc', 5, 601, -60, '60.5', {}):
            with self.subTest(broken=broken):
                self.assertEqual(update_interval_hours(broken), 1)
                self.assertEqual(subscription_content(user, {}, update_minutes=broken)[1]['profile-update-interval'], '1')

    def test_subscription_information_page_uses_and_escapes_custom_content(self):
        page = subscription_information_page('<b>Private VPN</b>', '<script>alert(1)</script>')
        self.assertIn('&lt;b&gt;Private VPN&lt;/b&gt;', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)

    def test_subscription_routing_preserves_existing_access(self):
        self.manager.create('Alice')
        paths = (self.config_path, self.public_path, self.manager.registry_path, self.manager.subscription_key_path)
        originals = {path: path.read_bytes() for path in paths}
        applied_count = len(self.applied)
        routing_links = []
        vip_url = self.manager.subscription_urls('https://vpn.example.com')['VIP']
        vip = self.manager.subscription_user(vip_url.rsplit('/', 1)[1])
        for user in (vip, *self.manager.users()):
            with self.subTest(user=user['id']):
                token = self.manager.subscription_token(user)
                for iteration in range(2):
                    content, headers = subscription_content(user, {'download_bytes': iteration})
                    lines = content.decode('utf-8').splitlines()
                    self.assertEqual(lines[-1], user['link'])
                    self.assertEqual(lines.count(user['link']), 1)
                    routing = [line for line in lines if line.startswith('happ://routing/onadd/')]
                    self.assertEqual(len(routing), 1)
                    profile = json.loads(base64.b64decode(routing[0].removeprefix('happ://routing/onadd/'), validate=True))
                    self.assertEqual(profile['Name'], 'FocusVPN Direct')
                    self.assertEqual(profile['GlobalProxy'], 'true')
                    self.assertEqual(dt.datetime.fromtimestamp(int(profile['LastUpdated']), dt.timezone.utc), dt.datetime(2026, 10, 8, second=1, tzinfo=dt.timezone.utc))
                    self.assertEqual(profile['DirectSites'], list(HAPP_DIRECT_SITES))
                    self.assertEqual(len(profile['DirectSites']), 201)
                    self.assertEqual(profile['DirectSites'].count('domain:focuslens.dev'), 1)
                    self.assertEqual(len(profile['DirectSites']), len(set(profile['DirectSites'])))
                    self.assertTrue(all(site.startswith('domain:') for site in profile['DirectSites']))
                    for site in ('mtalk.google.com', 'push.apple.com', 'max.ru', 'sberbank.ru', 'vk.com', 'api.ipify.org', 'ifconfig.me', 'zvuk.com'):
                        self.assertIn('domain:' + site, profile['DirectSites'])
                    self.assertNotIn('routing', headers)
                    routing_links.append(routing[0])
                    self.assertEqual(self.manager.subscription_token(user), token)
                    self.assertEqual(self.manager.subscription_user(token)['link'], user['link'])
        self.assertEqual(len(set(routing_links)), 1)
        self.assertEqual(len(self.applied), applied_count)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        self.vip_intact()

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

    def test_session_cookie_allows_cross_site_portal_navigation(self):
        handler = object.__new__(app.Handler)
        cookies = SimpleCookie()
        cookies.load(handler.session_cookie('test-session', 300, secure=True))
        cookie = cookies[app.SESSION_COOKIE_NAME]
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['path'], '/')
        self.assertEqual(cookie['max-age'], '300')
        self.assertTrue(cookie['httponly'])
        self.assertTrue(cookie['secure'])

    def test_trusted_proxy_preserves_client_ip_and_https_cookie(self):
        handler = object.__new__(app.Handler)
        handler.client_address = ('192.168.0.15', 41000)
        handler.headers = Message()
        handler.headers['X-Real-IP'] = '203.0.113.42'
        handler.headers['X-Forwarded-Proto'] = 'https'
        trusted = (ipaddress.ip_network('192.168.0.15/32'),)
        with patch.object(app, 'TRUSTED_PROXY_NETWORKS', trusted), patch.object(app, 'ACCESS_NETWORKS', (ipaddress.ip_network('203.0.113.0/24'),)):
            self.assertEqual(str(handler.client_ip_address()), '203.0.113.42')
            self.assertTrue(handler.vpn_client_allowed())
            self.assertTrue(handler.request_is_secure())
            self.assertIn('; Secure', handler.session_cookie('session', 300, handler.request_is_secure()))
            with patch.object(handler, 'session_token', return_value='session'), patch.object(app, 'valid_session', return_value=True) as valid:
                self.assertTrue(handler.session_authenticated())
                valid.assert_called_once_with('session', '203.0.113.42')
        handler.headers.replace_header('X-Real-IP', 'not-an-ip')
        with patch.object(app, 'TRUSTED_PROXY_NETWORKS', trusted):
            self.assertEqual(str(handler.client_ip_address()), '192.168.0.15')
        handler.client_address = ('203.0.113.80', 41000)
        handler.headers.replace_header('X-Real-IP', '203.0.113.42')
        with patch.object(app, 'TRUSTED_PROXY_NETWORKS', trusted):
            self.assertEqual(str(handler.client_ip_address()), '203.0.113.80')
            self.assertFalse(handler.request_is_secure())

    def test_trusted_proxy_environment_accepts_only_valid_networks(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FOCUSVPN_TEST_PROXY', None)
            self.assertEqual(app.network_list_from_environment('FOCUSVPN_TEST_PROXY'), ())
        with patch.dict(os.environ, {'FOCUSVPN_TEST_PROXY': '192.168.0.15,10.1.17.0/24'}):
            self.assertEqual(app.network_list_from_environment('FOCUSVPN_TEST_PROXY'), (ipaddress.ip_network('192.168.0.15/32'), ipaddress.ip_network('10.1.17.0/24')))
        with patch.dict(os.environ, {'FOCUSVPN_TEST_PROXY': 'not-a-network'}):
            with self.assertRaises(RuntimeError):
                app.network_list_from_environment('FOCUSVPN_TEST_PROXY')

    def test_subscription_announcement_preserves_short_text_and_emojis(self):
        content, headers = subscription_content({'name': 'Alice', 'link': self.link}, {}, 'http://127.0.0.1:9443/happ-info')
        text = base64.b64decode(headers['announce'].removeprefix('base64:')).decode('utf-8')
        self.assertEqual(text, SUBSCRIPTION_ANNOUNCEMENT + '\nDL: 0 B / UL: 0 B')
        self.assertTrue(text.startswith('🔒Это частный VPN сервер, для работы команды разработчиков focuslens.dev.'))
        self.assertIn('Если вы здесь оказались - это не случайно ❤️', text)
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

    def test_mobile_download_metric_units_and_refresh(self):
        user = {'name': 'Alice', 'link': self.link}
        for download, expected in ((0, '0 B'), (-10, '0 B'), (1024, '1.0 KB'), (1024 ** 2, '1.0 MB'), (512 * 1024 ** 2, '512.0 MB'), (1024 ** 3, '1.0 GB'), (int(1.5 * 1024 ** 3), '1.5 GB'), (2 ** 63 - 1, '8388608.0 TB')):
            content, headers = subscription_content(user, {'download_bytes': download, 'upload_bytes': 1024 ** 3})
            announcement = base64.b64decode(headers['announce'].removeprefix('base64:')).decode('utf-8')
            self.assertEqual(announcement, SUBSCRIPTION_ANNOUNCEMENT + '\nDL: ' + expected + ' / UL: 1.0 GB')
            self.assertLessEqual(len(announcement.encode('utf-16-le')) // 2, 200)
            self.assertIn('upload=1073741824; download=' + str(max(0, download)) + '; total=0', headers['subscription-userinfo'])
            self.assertIn(('#announce: ' + headers['announce']).encode('ascii'), content)

    def test_subscription_buttons_and_user_counters(self):
        user_id = self.manager.create('Alice')
        urls = self.manager.subscription_urls('http://127.0.0.1:9443')
        self.assertEqual(happ_add_link(urls[user_id]), 'happ://add/' + urls[user_id])
        page = happ_server_ui.page(self.manager.users(), traffic={'personal-' + user_id: {'download': '64.0 MB', 'upload': '8.0 MB'}}, subscriptions=urls)
        self.assertIn('data-happ-link="happ://add/' + urls[user_id] + '"', page)
        self.assertNotIn('data-happ-link="happ://add/' + urls['VIP'] + '"', page)
        self.assertIn('Открыть HAPP</a><div class="happ-account-traffic">', page)
        self.assertIn('data-account-download>64.0 MB', page)
        self.assertIn('data-account-upload>8.0 MB', page)
        self.assertIn('action="/happ-users/traffic/reset"', page)
        self.assertIn('data-happ-traffic-reset', page)
        self.assertIn('Нарастающий итог с момента включения накопительной статистики', page)

    def test_vip_link_block_is_not_duplicated_on_happ_server_page(self):
        urls = self.manager.subscription_urls('http://127.0.0.1:9443')
        page = happ_server_ui.page(self.manager.users(), 'test-csrf', subscriptions=urls)
        for marker in ('VIP VLESS', 'существующая ссылка', 'data-qr-open="public"', 'public-link-field', 'Public VLESS link', 'Endpoint:', self.link):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, page)
        self.assertIn('data-qr-modal', page)

    def test_personal_access_log_formats_russian_utc(self):
        cases = (
            ('2026-10-07T21:36:46.369264+00:00', '07.10.2026 21:36:46', 'dgektv', 'create', 'Создан'),
            ('2026-10-07T07:20:10.181860+00:00', '07.10.2026 07:20:10', 'marchenko', 'update', 'Настройки изменены'),
            ('2026-10-08T00:36:46+03:00', '07.10.2026 21:36:46', 'Alice', 'enable', 'Включён'),
            (None, '—', 'Alice', 'disable', 'Отключён'),
            ('', '—', 'Alice', 'delete', 'Удалён'),
            ('invalid<script>', '—', 'Alice', 'expired', 'Срок истёк'),
        )
        for timestamp, expected, name, operation, label in cases:
            with self.subTest(timestamp=timestamp):
                events = [{'at': timestamp, 'name': name, 'operation': operation}]
                original = [item.copy() for item in events]
                page = happ_server_ui.page([], events=events)
                self.assertIn('<th>Время UTC</th>', page)
                self.assertIn(f'<tr><td>{expected}</td><td>{name}</td><td>{label}</td></tr>', page)
                if timestamp:
                    self.assertNotIn(timestamp, page)
                self.assertEqual(events, original)

    def test_live_connections_precede_user_management(self):
        page = happ_server_ui.page([])
        connections_position = page.index('Подключения HAPP')
        users_position = page.index('Пользователи HAPP')
        self.assertLess(connections_position, users_position)
        self.assertLess(page.index('data-happ-traffic-chart'), page.index('id="happ_user_name"'))
        live_panel = page.split('data-happ-live>', 1)[1].split('</section>', 1)[0]
        self.assertIn('data-happ-chart-legend', live_panel)
        self.assertIn('data-happ-chart-range', live_panel)
        for minutes in (10, 30, 60, 90):
            self.assertIn(f'<option value="{minutes}">{minutes} минут</option>', live_panel)
        self.assertIn('href="/happ-history">История HAPP', live_panel)
        self.assertLess(live_panel.index('data-happ-chart-legend'), live_panel.index('href="/happ-history"'))
        self.assertIn('data-happ-activity-users', live_panel)
        self.assertIn('data-happ-chart-direction', live_panel)
        self.assertIn('data-happ-peak-seconds type="number" min="1" max="60"', live_panel)
        self.assertIn('data-happ-lifetime-download', live_panel)
        self.assertIn('data-happ-lifetime-upload', live_panel)
        self.assertIn('/chart.js?v=4.5.1', page)
        self.assertLess(page.index('/chart.js?v='), page.index('/panel.js?v='))
        self.assertLess(users_position, page.index('TOP-5 по трафику'))
        self.assertNotIn('Имя подтверждается журналом VLESS-аутентификации;', page)
        self.assertNotIn('не являются накопленным итогом закрытых сессий.', page)

    def test_user_creation_is_in_styled_modal(self):
        page = happ_server_ui.page([], 'test-csrf')
        users_panel = page.split('<h2>Пользователи HAPP</h2>', 1)[1].split('TOP-5 по трафику', 1)[0]
        self.assertIn('type="button" data-happ-user-create-open>Добавить нового пользователя', users_panel)
        self.assertNotIn('action="/happ-users/create"', users_panel)
        self.assertNotIn('id="happ_user_name"', users_panel)
        modal = page.split('<dialog class="gateway-dialog" data-happ-user-create-dialog', 1)[1].split('</dialog>', 1)[0]
        self.assertIn('aria-labelledby="happ-user-create-title"', modal)
        self.assertIn('method="post" action="/happ-users/create" data-happ-user-create-form', modal)
        self.assertIn('name="csrf" value="test-csrf"', modal)
        self.assertIn('name="name" maxlength="80" autocomplete="off" required autofocus', modal)
        self.assertIn('name="expires_at" type="datetime-local"', modal)
        self.assertIn('type="button" data-happ-user-create-cancel>Отмена', modal)
        self.assertIn('type="submit">Создать пользователя', modal)
        self.assertEqual(page.count('action="/happ-users/create"'), 1)
        self.assertIn('/panel.css?v=2.2.0', page)
        self.assertIn('/panel.js?v=2.2.0', page)
        self.assertIn('/happ-actions.js?v=10', page)

    def test_subscription_origin_prefers_settings_then_env_then_public_host(self):
        with patch.object(app, 'HAPP_SUBSCRIPTION_BASE_URL', ''), patch.object(app, 'PORT', 9443):
            self.assertEqual(app.resolve_happ_subscription_base_url({'server': '203.0.113.10'}), 'https://203.0.113.10:7445')
            self.assertEqual(app.resolve_happ_subscription_base_url({'server': 'vpn.example.com'}), 'https://vpn.example.com:7445')
            self.assertEqual(app.resolve_happ_subscription_base_url({'server': '2001:db8::10'}), 'https://[2001:db8::10]:7445')
            self.assertEqual(app.resolve_happ_subscription_base_url({'link': self.link}), 'https://vpn.example.com:7445')
            state = {'server': '203.0.113.10', 'subscription_base_url': 'https://vpn.example.com:8443/vpn/'}
            self.assertEqual(app.resolve_happ_subscription_base_url(state), 'https://vpn.example.com:8443/vpn')
        with patch.object(app, 'HAPP_SUBSCRIPTION_BASE_URL', 'https://env.example.com'):
            self.assertEqual(app.resolve_happ_subscription_base_url({'server': '203.0.113.10'}), 'https://env.example.com')
            self.assertEqual(app.resolve_happ_subscription_base_url(state), 'https://vpn.example.com:8443/vpn')
        for invalid in ('ftp://vpn.example.com', 'http://user:secret@vpn.example.com', 'http://vpn.example.com:99999', 'https://vpn.example.com?token=bad', 'https://vpn.example.com\r\nHeader:bad'):
            with self.assertRaises(ValueError):
                self.manager.subscription_urls(invalid)

    def test_vip_and_personal_qr_encode_public_mobile_subscription(self):
        self.manager.create('Alice')
        user = self.manager.users()[0]
        handler = object.__new__(app.Handler)
        urls = self.manager.subscription_urls('https://vpn.example.com:7445')
        for user_id, expected in ((None, urls['VIP']), (user['id'], urls[user['id']])):
            result = types.SimpleNamespace(returncode=0, stdout=b'<svg xmlns="http://www.w3.org/2000/svg"/>')
            with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app, 'load_happ_state', return_value={'server': 'vpn.example.com'}), patch.object(app, 'HAPP_SUBSCRIPTION_BASE_URL', ''), patch.object(app, 'PORT', 9443), patch.object(app.subprocess, 'run', return_value=result) as encoder, patch.object(handler, 'send_binary') as send:
                handler.send_happ_qr(user_id)
                self.assertEqual(encoder.call_args.kwargs['input'].decode('utf-8'), expected)
                self.assertEqual(urlsplit(expected).hostname, 'vpn.example.com')
                self.assertEqual(urlsplit(expected).port, 7445)
                self.assertTrue(urlsplit(expected).path.startswith('/happ-subscription/'))
                resolved = self.manager.subscription_user(urlsplit(expected).path.rsplit('/', 1)[1])
                self.assertEqual(resolved['link'], self.link if user_id is None else user['link'])
                send.assert_called_once_with(result.stdout, 'image/svg+xml; charset=utf-8')

    def test_mobile_qr_does_not_fall_back_to_raw_vless(self):
        handler = object.__new__(app.Handler)
        with patch.object(app, 'HAPP_USERS', None), patch.object(handler, 'send_empty') as send, patch.object(app.subprocess, 'run') as encoder:
            handler.send_happ_qr()
            send.assert_called_once_with(503)
            encoder.assert_not_called()
        with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app, 'load_happ_state', return_value={'server': 'vpn.example.com'}), patch.object(app, 'HAPP_SUBSCRIPTION_BASE_URL', ''), patch.object(handler, 'send_empty') as send, patch.object(app.subprocess, 'run') as encoder:
            handler.send_happ_qr('missing-account')
            send.assert_called_once_with(404)
            encoder.assert_not_called()

    def test_subscription_setting_save_and_clear_preserves_original_vip(self):
        with patch.object(app, 'HAPP_STATE_PATH', self.public_path), patch.object(app, 'load_happ_state', side_effect=lambda: json.loads(self.public_path.read_text())), patch.object(app, 'HAPP_SUBSCRIPTION_BASE_URL', ''), patch.object(app, 'backup_file'), patch.object(app, 'write_atomic_file', side_effect=lambda path, content, **kwargs: path.write_bytes(content)):
            original = json.loads(self.original_public)
            app.save_happ_subscription_base_url('https://sub.example.com:8443/')
            self.assertEqual(json.loads(self.public_path.read_text()), {**original, 'subscription_base_url': 'https://sub.example.com:8443'})
            app.save_happ_subscription_base_url('')
            self.assertEqual(json.loads(self.public_path.read_text()), original)
            with self.assertRaises(ValueError):
                app.save_happ_subscription_base_url('ftp://sub.example.com')
            self.assertEqual(json.loads(self.public_path.read_text()), original)
        self.assertEqual(self.manager.vip()['link'], self.link)
        self.assertEqual(json.loads(self.config_path.read_text())['inbounds'][0]['users'], self.config['inbounds'][0]['users'])

    def test_subscription_announcement_save_preserves_other_settings_and_vip(self):
        with patch.object(app, 'HAPP_STATE_PATH', self.public_path), patch.object(app, 'load_happ_state', side_effect=lambda: json.loads(self.public_path.read_text(encoding='utf-8'))), patch.object(panel_ui, 'load_happ_state', side_effect=lambda: json.loads(self.public_path.read_text(encoding='utf-8'))), patch.object(app, 'backup_file'), patch.object(app, 'write_atomic_file', side_effect=lambda path, content, **kwargs: path.write_bytes(content)):
            self.assertIn('<title>' + html.escape(panel_ui.DEFAULT_SUBSCRIPTION_TITLE) + '</title>', app.render_shell('Настройки', '', 'settings'))
            app.save_happ_subscription_content('  Focus VPN  ', 'Первая строка\r\nВторая строка')
            saved = json.loads(self.public_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['subscription_title'], 'Focus VPN')
            self.assertEqual(saved['subscription_announcement'], 'Первая строка\nВторая строка')
            self.assertEqual(saved['link'], self.link)
            self.assertIn('<title>Focus VPN</title>', app.render_shell('Настройки', '', 'settings'))
            app.save_happ_subscription_content('Focus VPN', '')
            saved = json.loads(self.public_path.read_text(encoding='utf-8'))
            self.assertEqual((saved['subscription_announcement'], saved['subscription_update_minutes']), ('', 60))
            app.save_happ_subscription_content('Focus VPN', '', '180')
            self.assertEqual(json.loads(self.public_path.read_text(encoding='utf-8'))['subscription_update_minutes'], 180)
            app.save_happ_subscription_content('Focus VPN', 'Текст', '')
            self.assertEqual(json.loads(self.public_path.read_text(encoding='utf-8'))['subscription_update_minutes'], 180)
            for invalid in ('9', '601', 'abc', '60.5', '-10'):
                with self.subTest(minutes=invalid), self.assertRaisesRegex(ValueError, 'от 10 до 600'):
                    app.save_happ_subscription_content('Other title', 'Другой текст', invalid)
            with self.assertRaises(ValueError):
                app.save_happ_subscription_content('Bad\nTitle', 'Текст')
            saved = json.loads(self.public_path.read_text(encoding='utf-8'))
            self.assertEqual((saved['subscription_title'], saved['subscription_announcement'], saved['subscription_update_minutes']), ('Focus VPN', 'Текст', 180))
        self.assertEqual(self.manager.vip()['link'], self.link)
        self.assertEqual(json.loads(self.config_path.read_text())['inbounds'][0]['users'], self.config['inbounds'][0]['users'])

    def test_service_title_escapes_markup_and_falls_back_without_valid_state(self):
        with patch.object(panel_ui, 'load_happ_state', return_value={'subscription_title': 'VPN <Team> & Friends'}):
            self.assertIn('<title>VPN &lt;Team&gt; &amp; Friends</title>', app.render_shell('Настройки', '', 'settings'))
        for state in ({}, {'subscription_title': ''}, {'subscription_title': 'Bad\nTitle'}, []):
            with self.subTest(state=state), patch.object(panel_ui, 'load_happ_state', return_value=state):
                self.assertEqual(panel_ui.service_title(), panel_ui.DEFAULT_SUBSCRIPTION_TITLE)
        for error in (FileNotFoundError(), json.JSONDecodeError('invalid', '', 0)):
            with self.subTest(error=type(error).__name__), patch.object(panel_ui, 'load_happ_state', side_effect=error):
                self.assertEqual(panel_ui.service_title(), panel_ui.DEFAULT_SUBSCRIPTION_TITLE)

    def test_subscription_announcement_settings_form_saves_through_authenticated_post(self):
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            form = urlencode({
                'csrf': app.CSRF_TOKEN,
                'subscription_title': 'Focus VPN',
                'subscription_announcement': 'Обновлённый текст HAPP',
                'subscription_update_minutes': '120',
            })
            with patch.object(app, 'HAPP_STATE_PATH', self.public_path), patch.object(app, 'load_happ_state', side_effect=lambda: json.loads(self.public_path.read_text(encoding='utf-8'))), patch.object(panel_ui, 'load_happ_state', side_effect=lambda: json.loads(self.public_path.read_text(encoding='utf-8'))), patch.object(app, 'backup_file'), patch.object(app, 'write_atomic_file', side_effect=lambda path, content, **kwargs: path.write_bytes(content)), patch.object(app.Handler, 'vpn_client_allowed', return_value=True), patch.object(app.Handler, 'session_authenticated', return_value=True):
                connection.request('POST', '/settings/happ/announcement', body=form, headers={'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertIn('/settings?', response.getheader('Location'))
                response.read()
                connection.request('GET', '/logout')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn('<title>Focus VPN</title>', response.read().decode('utf-8'))
            saved = json.loads(self.public_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['subscription_title'], 'Focus VPN')
            self.assertEqual(saved['subscription_announcement'], 'Обновлённый текст HAPP')
            self.assertEqual(saved['subscription_update_minutes'], 120)
            self.assertEqual(saved['link'], self.link)
            rejected = urlencode({'csrf': app.CSRF_TOKEN, 'subscription_title': 'Changed', 'subscription_announcement': 'Changed', 'subscription_update_minutes': '5'})
            with patch.object(app, 'HAPP_STATE_PATH', self.public_path), patch.object(app, 'load_happ_state', side_effect=lambda: json.loads(self.public_path.read_text(encoding='utf-8'))), patch.object(app, 'backup_file'), patch.object(app, 'write_atomic_file', side_effect=lambda path, content, **kwargs: path.write_bytes(content)), patch.object(app.Handler, 'vpn_client_allowed', return_value=True), patch.object(app.Handler, 'session_authenticated', return_value=True):
                connection.request('POST', '/settings/happ/announcement', body=rejected, headers={'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                response.read()
                query = parse_qs(urlsplit(response.getheader('Location')).query)
                self.assertEqual((response.status, query['kind']), (303, ['error']))
                self.assertIn('от 10 до 600', query['message'][0])
            self.assertEqual(json.loads(self.public_path.read_text(encoding='utf-8')), saved)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_chart_library_is_served_before_admin_auth(self):
        chart_path = Path(__file__).resolve().parents[1] / 'server' / 'panel' / 'static' / 'chart.js'
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app, 'CHART_JS_PATH', chart_path), patch.object(app.Handler, 'require_access', return_value=False) as access:
                connection.request('GET', '/chart.js?v=4.5.1')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'application/javascript; charset=utf-8')
                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                self.assertEqual(response.read(), chart_path.read_bytes())
                access.assert_not_called()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

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
        subscription_state = {'server': 'vpn.example.com'}
        try:
            with patch.object(app, 'HAPP_USERS', self.manager), patch.object(app, 'HAPP_HISTORY', history), patch.object(app, 'load_happ_state', side_effect=lambda: dict(subscription_state)), patch.object(app, 'HAPP_SUBSCRIPTION_BASE_URL', ''), patch.object(app.Handler, 'vpn_client_allowed', return_value=True):
                connection.request('GET', '/happ-subscription/' + token)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('subscription-userinfo'), 'upload=20; download=100; total=0')
                self.assertEqual(response.getheader('profile-update-interval'), '1')
                self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                announcement = response.getheader('announce')
                self.assertEqual(base64.b64decode(announcement.removeprefix('base64:')).decode('utf-8'), SUBSCRIPTION_ANNOUNCEMENT + '\nDL: 100 B / UL: 20 B')
                self.assertEqual(response.getheader('profile-web-page-url'), 'https://vpn.example.com:7445/happ-info')
                body = response.read().decode('utf-8')
                self.assertIn('#announce: ' + announcement, body)
                self.assertIn(users[0]['link'], body)
                self.assertNotIn(users[1]['link'], body)
                routing = [line for line in body.splitlines() if line.startswith('happ://routing/onadd/')]
                self.assertEqual(len(routing), 1)
                profile = json.loads(base64.b64decode(routing[0].removeprefix('happ://routing/onadd/'), validate=True))
                self.assertEqual(profile['DirectSites'], list(HAPP_DIRECT_SITES))
                self.assertEqual(profile['LastUpdated'], '1791417601')
                self.assertIn('domain:focuslens.dev', profile['DirectSites'])
                self.assertIsNone(response.getheader('routing'))
                connection.request('GET', '/happ-info')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                information = response.read().decode('utf-8')
                self.assertIn('text-align:justify', information)
                self.assertIn('focuslens.dev', information)
                self.assertNotIn(users[0]['link'], information)
                subscription_state.update(subscription_title='Private VPN', subscription_announcement='Новое объявление HAPP', subscription_update_minutes=600)
                connection.request('GET', '/happ-subscription/' + token)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('profile-update-interval'), '10')
                self.assertEqual(
                    base64.b64decode(response.getheader('profile-title').removeprefix('base64:')).decode('utf-8'),
                    'Private VPN Alice',
                )
                self.assertEqual(
                    base64.b64decode(response.getheader('announce').removeprefix('base64:')).decode('utf-8'),
                    'Новое объявление HAPP\nDL: 100 B / UL: 20 B',
                )
                response.read()
                connection.request('GET', '/happ-info')
                response = connection.getresponse()
                self.assertIn('Private VPN', response.read().decode('utf-8'))
                subscription_state.clear()
                subscription_state['server'] = 'vpn.example.com'
                history.ingest({'connections': [{**record, 'download_bytes': 200}]})
                connection.request('GET', '/happ-subscription/' + token)
                response = connection.getresponse()
                self.assertEqual(response.getheader('subscription-userinfo'), 'upload=20; download=200; total=0')
                response.read()
                for amount, label in ((512 * 1024 ** 2, '512.0 MB'), (int(1.5 * 1024 ** 3), '1.5 GB')):
                    history.ingest({'connections': [{**record, 'download_bytes': amount}]})
                    connection.request('GET', '/happ-subscription/' + token)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    refreshed = base64.b64decode(response.getheader('announce').removeprefix('base64:')).decode('utf-8')
                    self.assertEqual(refreshed, SUBSCRIPTION_ANNOUNCEMENT + '\nDL: ' + label + ' / UL: 20 B')
                    self.assertEqual(response.getheader('subscription-userinfo'), 'upload=20; download=' + str(amount) + '; total=0')
                    self.assertIn(routing[0], response.read().decode('utf-8'))
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
        page = happ_server_ui.page(users, 'test-csrf')
        expected = html.escape('happ://add/' + quote(users[0]['link'], safe=''), quote=True)
        self.assertIn('href="' + expected + '" data-happ-action="open" data-happ-link="' + expected + '"', page)
        vip_expected = html.escape('happ://add/' + quote(self.link, safe=''), quote=True)
        self.assertNotIn(vip_expected, page)
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
        page = happ_server_ui.page(self.manager.users(), 'test-csrf', events=self.manager.events())
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>unsafe</script>', page)
        self.assertIn('/happ-users/' + user_id + '/qr', page)
        self.assertNotIn('VIP VLESS', page)


if __name__ == '__main__':
    unittest.main()