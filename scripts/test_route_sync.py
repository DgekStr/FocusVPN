import json
import hashlib
import base64
import http.client
import sys
import tempfile
import types
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from subprocess import CompletedProcess
from urllib.parse import parse_qs, urlencode, urlparse

if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
import app
import panel_ui
from vless_monitor import DEFAULT_SETTINGS, VlessMonitor


def batch_import_fixture():
    public_key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
    shared_uuid = '00000000-0000-0000-0000-000000000001'

    def vless(network, port):
        stream = {
            'network': network, 'security': 'reality',
            'realitySettings': {'fingerprint': 'chrome', 'publicKey': public_key, 'serverName': 'vpn.example.com', 'shortId': 'aabbccdd'},
        }
        if network == 'grpc':
            stream['grpcSettings'] = {'serviceName': 'test-service'}
        return {'outbounds': [{'protocol': 'vless', 'tag': 'proxy', 'settings': {'address': '203.0.113.1', 'port': port, 'id': shared_uuid, 'flow': 'xtls-rprx-vision' if network == 'tcp' else ''}, 'streamSettings': stream}]}

    password = base64.b64encode(bytes(range(32))).decode()
    return [
        vless('tcp', 443),
        vless('xhttp', 8443),
        {'outbounds': [{'protocol': 'hysteria', 'tag': 'proxy', 'settings': {'address': '203.0.113.1', 'port': 443}, 'streamSettings': {'hysteriaSettings': {'auth': 'test-only'}, 'tlsSettings': {'alpn': ['h3']}}}]},
        {'outbounds': [{'protocol': 'trojan', 'tag': 'proxy', 'settings': {'servers': [{'address': '203.0.113.1', 'port': 2083, 'password': 'test-only'}]}, 'streamSettings': {'network': 'tcp', 'security': 'tls', 'tlsSettings': {'fingerprint': 'chrome'}}}]},
        {'outbounds': [{'protocol': 'shadowsocks', 'tag': 'proxy', 'settings': {'servers': [{'address': '203.0.113.1', 'port': 8388, 'password': password + ':' + password, 'method': '2022-blake3-aes-256-gcm'}]}}]},
        {'outbounds': [{'protocol': 'wireguard', 'tag': 'proxy', 'settings': {'address': ['10.0.0.2/32']}}]},
        vless('grpc', 2053),
    ]


class RouteSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.gateway_path = root / 'gateway.json'
        self.happ_path = root / 'happ.json'
        self.mode_path = root / 'mode.json'
        self.gateway = {'outbounds': [{'type': 'hysteria2', 'tag': 'auto-8'}], 'route': {'final': 'auto-8'}}
        self.happ = {
            'inbounds': [{'tag': 'happ-in'}],
            'outbounds': [{'type': 'vless', 'tag': 'auto-8'}, {'type': 'direct', 'tag': 'focusvpn-wg-direct', 'routing_mark': 2}],
            'route': {'final': 'auto-4'},
        }
        self.gateway_path.write_text(json.dumps({'route': {'final': 'auto-4'}}))
        self.happ_path.write_text(json.dumps(self.happ))
        self.mode_path.write_text(json.dumps({'mode': 'vless'}))
        self.patch('CONFIG_PATH', self.gateway_path)
        self.patch('HAPP_CONFIG_PATH', self.happ_path)
        self.patch('GATEWAY_MODE_PATH', self.mode_path)
        self.patch('check_candidate')
        self.patch('check_happ_candidate')
        self.gateway_restart = self.patch('restart_sing_box', return_value=True)
        self.happ_restart = self.patch('restart_happ_server')
        self.patch('write_atomic_bytes', side_effect=lambda data: self.gateway_path.write_bytes(data))
        self.patch('write_atomic_file', side_effect=lambda path, data, **kwargs: path.write_bytes(data))
        self.patch('backup_configuration', side_effect=lambda: self.backup(self.gateway_path))
        self.patch('backup_file', side_effect=lambda path, prefix: self.backup(path))

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(app, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def backup(self, path):
        destination = path.with_suffix('.backup')
        destination.write_bytes(path.read_bytes())
        return destination

    def test_switch_updates_happ_route_and_protocol(self):
        self.assertTrue(app.apply_configuration(self.gateway))
        result = json.loads(self.happ_path.read_text())
        self.assertEqual(result['route']['final'], 'auto-8')
        self.assertEqual(next(item for item in result['outbounds'] if item['tag'] == 'auto-8')['type'], 'hysteria2')
        self.assertEqual(result['inbounds'], self.happ['inbounds'])
        self.happ_restart.assert_called_once()

    def test_legacy_mode_uses_provider_without_client_route(self):
        self.mode_path.write_text(json.dumps({'mode': 'wireguard', 'happ_route': 'auto-4'}))
        self.assertTrue(app.apply_configuration(self.gateway))
        self.assertEqual(json.loads(self.happ_path.read_text())['route']['final'], 'auto-8')

    def test_gateway_default_mode_persists_and_blocks_tproxy_restart(self):
        self.mode_path.write_text(json.dumps({'mode': 'default'}))
        self.assertEqual(app.gateway_mode(), 'default')
        command = self.patch('command', return_value=CompletedProcess([], 0, ''))
        self.patch('VLESS_MONITOR', None)
        with patch.object(app, 'GATEWAY_MODE_UNIT', 'focusvpn-gateway-mode@{}.service'):
            app.control_gateway_mode('default')
        self.assertEqual(command.call_args.args[0], [app.SYSTEMCTL_BIN, 'start', 'focusvpn-gateway-mode@default.service'])
        with self.assertRaisesRegex(ValueError, 'режим шлюза на VLESS'):
            app.control_system_service('sing-box', 'start')

    def test_gateway_default_mode_does_not_require_external_peer_config(self):
        self.patch('GATEWAY_MODE_PATH', self.mode_path)
        command = self.patch('command', return_value=CompletedProcess([], 0, ''))
        self.patch('VLESS_MONITOR', None)
        with patch.object(app, 'gateway_mode', side_effect=['vless', 'default']):
            app.control_gateway_mode('default')
        self.assertEqual(command.call_args.args[0], [app.SYSTEMCTL_BIN, 'start', app.GATEWAY_MODE_UNIT.format('default')])
        self.assertFalse(hasattr(app, 'WIREGUARD_CLIENT_CONFIG_PATH'))

    def test_failed_restart_restores_both_configs_and_mode(self):
        previous = [path.read_bytes() for path in (self.gateway_path, self.happ_path, self.mode_path)]
        self.happ_restart.side_effect = [RuntimeError('failed restart'), None]
        with self.assertRaises(RuntimeError):
            app.apply_configuration(self.gateway)
        self.assertEqual(previous, [path.read_bytes() for path in (self.gateway_path, self.happ_path, self.mode_path)])

    def test_manager_owns_status_and_navigation_hides_vless(self):
        self.patch('service_state', return_value='active')
        page = app.render_outbounds_page(self.gateway, {})
        self.assertIn('sing-box: active', page)
        self.assertIn('/outbounds/check', page)
        self.assertNotIn('data-panel-nav="vless"', page)
        self.assertFalse(hasattr(app, 'update_hysteria2_auto8'))

    def test_manager_marks_only_default_route_with_green_indicator(self):
        self.patch('service_state', return_value='inactive')
        self.gateway['outbounds'].append({'type': 'vless', 'tag': 'auto-9'})
        for selected_tag in ('auto-8', 'auto-9'):
            with self.subTest(selected_tag=selected_tag):
                self.gateway['route']['final'] = selected_tag
                page = app.render_outbounds_page(self.gateway, {})
                self.assertRegex(page, r'href="/panel\.css\?v=[^"]+"')
                rows = page.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
                self.assertEqual(rows.count('class="status-dot route-active"'), 1)
                self.assertNotIn('<td>active</td>', rows)
                for row in rows.split('</tr>'):
                    if '<strong>' not in row:
                        continue
                    if f'<strong>{selected_tag}</strong>' in row:
                        self.assertIn('class="status-dot route-active"', row)
                        self.assertIn('aria-label="Маршрут по умолчанию"', row)
                        self.assertIn('title="Маршрут по умолчанию"', row)
                    else:
                        self.assertNotIn('route-active', row)

    def test_manager_renders_edit_modal_and_short_check_button(self):
        self.patch('service_state', return_value='inactive')
        page = app.render_outbounds_page(self.gateway, {})
        self.assertIn('data-outbound-edit=', page)
        self.assertIn('data-outbound-edit-dialog', page)
        self.assertIn('data-outbound-edit-form', page)
        self.assertIn('name="replace_tag" data-outbound-edit-tag', page)
        self.assertIn('>Проверить</button>', page)
        self.assertIn('>Редактировать</button>', page)
        self.assertNotIn('>Проверить соединение</button>', page)

    def test_server_actions_share_one_table_cell_in_order(self):
        self.patch('service_state', return_value='inactive')
        page = app.render_outbounds_page(self.gateway, {})
        rows = page.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
        self.assertEqual(rows.count('<td'), 9)
        self.assertEqual(rows.count('class="outbound-actions"'), 1)
        positions = [rows.index('>' + label + '</button>') for label in ('Использовать', 'Проверить', 'Редактировать', 'Удалить')]
        self.assertEqual(positions, sorted(positions))

    def test_sidebar_has_one_server_link_without_profile_items(self):
        page = app.render_shell('VPN-серверы', '<p>content</p>', 'outbounds', ['auto-1', 'auto-2'], 'auto-1')
        self.assertEqual(page.count('data-panel-nav="outbounds"'), 1)
        self.assertNotIn('class="profile-list"', page)
        self.assertNotIn('/outbounds?tag=', page)
        self.assertNotIn('auto-1', page)
        self.assertIn('data-panel-nav="wireguard"', page)
        self.assertEqual(page.count('class="nav-section"'), 1)

    def test_about_menu_item_is_pinned_below_main_navigation(self):
        page = app.render_shell('VPN-серверы', '<p>content</p>', 'outbounds', [])
        self.assertEqual(page.count('data-panel-nav="about"'), 1)
        self.assertIn('<a class="nav-link nav-about" data-panel-nav="about" href="/about"><span class="nav-dot"></span>О программе</a>', page)
        self.assertLess(page.index('href="/logout"'), page.index('data-panel-nav="about"'))
        self.assertLess(page.index('data-panel-nav="about"'), page.index('class="sidebar-foot"'))
        self.assertEqual(page.count('class="nav-section"'), 1)
        active_page = app.render_shell('О программе', '', 'about', [])
        self.assertIn('class="nav-link nav-about active" data-panel-nav="about"', active_page)
        self.assertEqual(active_page.count(' active"'), 1)

    def test_about_page_describes_program_with_version_license_and_external_links(self):
        page = app.render_about_page()
        self.assertIn('<h1>О программе</h1>', page)
        self.assertIn('<h2 id="about-title">FocusVPN</h2><span class="muted">Версия ' + panel_ui.PROJECT_VERSION + '</span>', page)
        self.assertIn('Русскоязычная self-hosted панель управления VPN-шлюзом на sing-box', page)
        self.assertIn('Программа распространяется бесплатно по лицензии MIT.', page)
        self.assertIn('клиентский биллинг и обязательные платежи не предусмотрены', page)
        self.assertIn('Это не создаёт подписки или платных обязательств', page)
        for label, url in (
            ('GitHub', 'https://github.com/DgekStr/FocusVPN'),
            ('Описание программы', 'https://github.com/DgekStr/FocusVPN/blob/master/README.md'),
            ('Развёртывание', 'https://github.com/DgekStr/FocusVPN/blob/master/docs/OPERATIONS.md'),
            ('Релизы', 'https://github.com/DgekStr/FocusVPN/releases'),
            ('MIT License', 'https://github.com/DgekStr/FocusVPN/blob/master/LICENSE'),
        ):
            self.assertIn(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>', page)
        self.assertIn('Copyright © 2026 <a href="https://github.com/DgekStr" target="_blank" rel="noopener noreferrer">DgekStr</a>', page)
        self.assertEqual(page.count('target="_blank"'), 6)
        self.assertEqual(page.count('rel="noopener noreferrer"'), 6)
        self.assertIn('class="nav-link nav-about active"', page)
        self.assertIn('/panel.css?v=2.2.0', page)

    def test_about_route_requires_login_and_renders_for_signed_in_admin(self):
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app.Handler, 'vpn_client_allowed', return_value=True), patch.object(app.Handler, 'crm_authenticated', return_value=False):
                with patch.object(app.Handler, 'session_authenticated', return_value=False):
                    connection.request('GET', '/about')
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 303)
                    self.assertEqual(response.getheader('Location'), '/login')
                with patch.object(app.Handler, 'session_authenticated', return_value=True):
                    connection.request('GET', '/about')
                    response = connection.getresponse()
                    page = response.read().decode('utf-8')
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                    self.assertIn('<h1>О программе</h1>', page)
                    self.assertIn('Версия ' + panel_ui.PROJECT_VERSION, page)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_editor_replacement_preserves_tag_route_and_nested_fields(self):
        config = {'outbounds': [{'type': 'vless', 'tag': 'auto-1', 'server': 'old.example.com'}], 'route': {'final': 'auto-1'}}
        replacement = {'type': 'vless', 'tag': 'ignored-new-tag', 'server': 'new.example.com', 'server_port': 443, 'uuid': '00000000-0000-4000-8000-000000000001', 'tls': {'enabled': True, 'server_name': 'example.com'}, 'transport': {'type': 'grpc', 'service_name': 'test-service'}}
        imported, skipped = app.import_server_batch(json.dumps(replacement), config, replace_tag='auto-1', validator=Mock())
        self.assertEqual(imported, ['auto-1'])
        self.assertEqual(skipped, [])
        self.assertEqual(config['route']['final'], 'auto-1')
        self.assertEqual(config['outbounds'][0], {**replacement, 'tag': 'auto-1'})

    def test_empty_install_direct_route_stays_valid_when_syncing_happ(self):
        config = {'outbounds': [{'type': 'direct', 'tag': 'direct'}], 'route': {'final': 'direct'}}
        result = app.synchronized_happ_config(config, self.happ, 'vless')
        self.assertEqual(result['route']['final'], 'direct')
        self.assertIn({'type': 'direct', 'tag': 'direct'}, result['outbounds'])
        self.assertEqual(result['inbounds'], self.happ['inbounds'])

    def test_settings_shows_gateway_modes_first_in_two_column_layout(self):
        api = types.SimpleNamespace(general=Mock(return_value={}), interface=Mock(return_value={}))
        self.patch('WG_ADMIN', types.SimpleNamespace(api=api))
        self.patch('managed_server_outbounds', return_value=[])
        self.patch('selectable_outbounds', return_value=[])
        self.patch('json_text', return_value='{}')
        self.patch('load_happ_state', return_value={'server': 'vpn.example.com'})
        self.patch('HAPP_USERS', None)
        self.patch('public_vless_link', return_value='vless://demo@vpn.example.com:9445')
        self.patch('load_service_control', return_value={'wireguard_fallback_gateway': '192.168.0.6'})
        self.patch('gateway_mode', return_value='default')
        self.patch('VLESS_MONITOR', None)
        self.patch('load_monitor_settings', return_value={**DEFAULT_SETTINGS})
        self.patch('HAPP_HISTORY', None)
        self.patch('wireguard_state', return_value='active')
        self.patch('service_state', return_value='active')
        self.patch('render_shell', side_effect=lambda title, body, *args, **kwargs: body)

        page = app.render_settings_page({}, {})
        self.assertIn('<div class="settings-layout">', page)
        self.assertEqual(page.count('class="panel gateway-mode-panel"'), 1)
        self.assertEqual(page.count('class="panel service-control-panel"'), 1)
        self.assertNotIn('settings-wide', page)
        self.assertLess(page.index('Режим работы VPN-шлюза'), page.index('Публичный URL подписки HAPP'))
        self.assertLess(page.index('VIP-ссылка HAPP'), page.index('Доступ к панели'))
        self.assertLess(page.index('happ_vip_link'), page.index('new_password'))
        self.assertEqual(page.count('action="/settings/password"'), 1)
        self.assertEqual(page.count('data-gateway-mode='), 2)
        self.assertNotIn('data-gateway-mode="wireguard"', page)
        self.assertNotIn('data-wg-client-live', page)
        self.assertNotIn('wireguard_client_config', page)
        self.assertNotIn('/settings/gateway/client', page)
        self.assertIn('action="/settings/service/wireguard"', page)
        self.assertIn('id="happ_public_key"', page)
        self.assertIn('id="happ_vip_uuid"', page)
        self.assertNotIn('Существующая общая ссылка', page)
        self.assertIn('data-gateway-mode="default"', page)
        self.assertIn('value="default"', page)
        self.assertIn('Шлюз по умолчанию', page)
        self.assertEqual(page.count('class="panel service-control-panel"'), 1)
        self.assertNotIn('settings-wide', page)
        self.assertIn('name="failover_enabled">', page)
        self.assertIn('Автопроверка доступности VLESS', page)
        self.assertLess(page.index('name="failover_enabled"'), page.index('name="auto_switch"'))
        self.assertIn('id="mattermost_message"', page)
        self.assertIn('📢 VPN-шлюз: обновление статуса\n📅 {date} | 🕐 {time}', page)
        self.assertIn('{date}, {time}, {old}, {new}, {source}, {latency}', page)
        self.assertIn('name="utc_offset" value="+03:00"', page)
        self.assertIn('data-monitor-status>', page)
        self.assertIn('<input id="happ_subscription_update_minutes" name="subscription_update_minutes" type="number" min="10" max="600" step="1" value="60" required>', page)
        self.assertLess(page.index('id="happ_subscription_announcement"'), page.index('id="happ_subscription_update_minutes"'))
        self.assertLess(page.index('id="happ_subscription_update_minutes"'), page.index('Сохранить блок HAPP'))
        self.assertIn('сейчас клиентам отдаётся 1 ч', page)
        self.patch('load_happ_state', return_value={'server': 'vpn.example.com', 'subscription_update_minutes': 150})
        page = app.render_settings_page({}, {})
        self.assertIn('step="1" value="150" required>', page)
        self.assertIn('сейчас клиентам отдаётся 3 ч', page)
        self.patch('load_happ_state', return_value={'server': 'vpn.example.com', 'subscription_update_minutes': 'broken'})
        self.assertIn('step="1" value="60" required>', app.render_settings_page({}, {}))
        self.patch('load_happ_state', return_value={'server': 'vpn.example.com'})

        self.patch('load_monitor_settings', return_value={**DEFAULT_SETTINGS, 'failover_enabled': True, 'message_template': '</textarea><script>alert(1)</script> "{old}"'})
        self.patch('VLESS_MONITOR', types.SimpleNamespace(status=lambda: {'last_checked_at': None, 'candidate': None, 'streak': 0, 'gateway_text': 'Шлюз по умолчанию auto-10: доступен. Проверено 10.10.2026 19:41:05 UTC.'}))
        page = app.render_settings_page({}, {})
        self.assertIn('name="failover_enabled" checked>', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt; &quot;{old}&quot;', page)
        self.assertIn('data-gateway-status>Шлюз по умолчанию auto-10: доступен. Проверено 10.10.2026 19:41:05 UTC.</p>', page)

    def test_connection_result_is_bound_to_profile(self):
        root = Path(self.directory.name)
        self.patch('APP_DIR', root)
        self.patch('command', return_value=CompletedProcess([], 0, ''))
        result_dir = root / 'outbound-checks'
        result_dir.mkdir()
        fingerprint = hashlib.sha256(json.dumps(self.gateway['outbounds'][0], sort_keys=True).encode('utf-8')).hexdigest()
        path = result_dir / 'auto-8.json'
        path.write_text(json.dumps({'ok': True, 'ip': '203.0.113.1', 'fingerprint': fingerprint}))
        kind, message = app.check_server_connection(self.gateway, 'auto-8')
        self.assertEqual(kind, 'success')
        self.assertIn('203.0.113.1', message)
        path.write_text(json.dumps({'ok': True, 'ip': '203.0.113.1', 'fingerprint': 'stale'}))
        self.assertEqual(app.check_server_connection(self.gateway, 'auto-8')[0], 'error')

    def test_json_import_preserves_explicit_tls_policy(self):
        source = {
            'protocol': 'hysteria2',
            'settings': {'address': '203.0.113.1', 'port': 443},
            'streamSettings': {'hysteriaSettings': {'auth': 'test-only'}, 'tlsSettings': {'serverName': 'vpn.example.com'}},
        }
        self.assertNotIn('insecure', app.import_xray_outbound(source, 'auto-8')['tls'])
        source['streamSettings']['tlsSettings']['allowInsecure'] = True
        self.assertTrue(app.import_xray_outbound(source, 'auto-8')['tls']['insecure'])

    def test_full_xray_reality_import_defaults_empty_fingerprint_to_chrome(self):
        source = batch_import_fixture()[0]['outbounds'][0]
        settings = source['settings']
        source['settings'] = {'vnext': [{'address': settings['address'], 'port': settings['port'], 'users': [{'id': settings['id'], 'flow': settings['flow'], 'encryption': 'none'}]}]}
        payload = {'outbounds': [source, {'protocol': 'freedom', 'tag': 'direct'}, {'protocol': 'blackhole', 'tag': 'block'}]}
        for fingerprint in ('', None, 'missing'):
            with self.subTest(fingerprint=fingerprint):
                reality = source['streamSettings']['realitySettings']
                if fingerprint == 'missing':
                    reality.pop('fingerprint', None)
                else:
                    reality['fingerprint'] = fingerprint
                config = json.loads(json.dumps(self.gateway))
                validator = Mock()
                tags, skipped = app.import_server_batch(json.dumps(payload), config, validator=validator)
                self.assertEqual(len(tags), 1)
                self.assertEqual(skipped, [])
                validator.assert_called_once()
                candidate = json.loads(validator.call_args.args[0])
                outbound = next(item for item in candidate['outbounds'] if item.get('tag') == tags[0])
                self.assertEqual(outbound['tls']['utls'], {'enabled': True, 'fingerprint': 'chrome'})
                self.assertEqual(outbound['tls']['reality']['public_key'], reality['publicKey'])
                self.assertEqual(outbound['tls']['reality']['short_id'], reality['shortId'])
                self.assertEqual(outbound['uuid'], settings['id'])
                self.assertEqual(outbound['server'], settings['address'])
                self.assertEqual(outbound['flow'], settings['flow'])
                self.assertEqual(config['route']['final'], self.gateway['route']['final'])

    def test_xray_reality_preserves_explicit_fingerprint_without_changing_plain_tls(self):
        source = batch_import_fixture()[0]['outbounds'][0]
        source['streamSettings']['realitySettings']['fingerprint'] = 'firefox'
        self.assertEqual(app.import_xray_outbound(source, 'auto-1')['tls']['utls'], {'enabled': True, 'fingerprint': 'firefox'})
        source['streamSettings'] = {'network': 'tcp', 'security': 'tls', 'tlsSettings': {'serverName': 'vpn.example.com'}}
        self.assertNotIn('utls', app.import_xray_outbound(source, 'auto-1')['tls'])

    def test_batch_import_flat_vless_and_mixed_protocols(self):
        config = {'outbounds': [{'type': 'urltest', 'tag': 'vless-auto', 'outbounds': []}], 'route': {'final': 'vless-auto'}}
        validator = self.patch('check_candidate')
        tags, skipped = app.import_server_batch(json.dumps(batch_import_fixture()), config, validator=validator)
        self.assertEqual(len(tags), 5)
        self.assertEqual(len(set(tags)), 5)
        self.assertEqual(len(skipped), 2)
        self.assertIn('XHTTP', skipped[0])
        self.assertIn('WireGuard', skipped[1])
        self.assertEqual(validator.call_count, 5)
        servers = app.managed_server_outbounds(config)
        self.assertEqual([item['type'] for item in servers], ['vless', 'hysteria2', 'trojan', 'shadowsocks', 'vless'])
        self.assertEqual(servers[0]['tls']['utls']['fingerprint'], 'chrome')
        self.assertEqual(servers[-1]['transport']['type'], 'grpc')
        self.assertEqual(config['route']['final'], 'vless-auto')
        self.assertEqual(config['outbounds'][0]['outbounds'], tags)

    def test_invalid_batch_leaves_config_unchanged(self):
        before = json.loads(json.dumps(self.gateway))
        with self.assertRaises(ValueError):
            app.import_server_batch(json.dumps([None, {'outbounds': [{'protocol': 'wireguard'}]}]), self.gateway)
        self.assertEqual(self.gateway, before)
        with self.assertRaises(ValueError):
            app.import_server_batch(json.dumps(batch_import_fixture()), self.gateway, replace_tag='auto-8')
        self.assertEqual(self.gateway, before)

    def test_background_check_returns_without_waiting_for_network(self):
        executor = ThreadPoolExecutor(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        self.patch('OUTBOUND_CHECK_EXECUTOR', executor)
        self.patch('OUTBOUND_CHECK_JOBS', {})

        def slow_check(config, tag):
            entered.set()
            release.wait(timeout=5)
            return 'success', 'test result'

        self.patch('check_server_connection', side_effect=slow_check)
        try:
            app.queue_server_checks(self.gateway, ['auto-8'])
            self.assertTrue(entered.wait(timeout=2))
            job = app.OUTBOUND_CHECK_JOBS['auto-8']
            self.assertFalse(job['future'].done())
            self.assertEqual(app.outbound_check_states(self.gateway)['checks'][0]['state'], 'running')
            self.assertTrue(app.CONFIG_LOCK.acquire(blocking=False))
            app.CONFIG_LOCK.release()
            app.queue_server_checks(self.gateway, ['auto-8'])
            self.assertIs(app.OUTBOUND_CHECK_JOBS['auto-8']['future'], job['future'])
            release.set()
            job['future'].result(timeout=2)
            self.assertEqual(app.outbound_check_states(self.gateway)['checks'][0]['state'], 'success')
        finally:
            release.set()
            executor.shutdown(wait=True)

    def test_background_check_failure_is_reported(self):
        self.patch('check_server_connection', side_effect=RuntimeError('internal details'))
        result = app.background_server_check(self.gateway, 'auto-8')
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('internal details', result['message'])

    def test_import_http_redirect_precedes_connection_result(self):
        executor = ThreadPoolExecutor(max_workers=1)
        release = threading.Event()
        entered = threading.Event()
        self.patch('OUTBOUND_CHECK_EXECUTOR', executor)
        self.patch('OUTBOUND_CHECK_JOBS', {})
        self.patch('load_config', return_value=self.gateway)
        self.patch('apply_configuration', return_value=True)
        access = patch.object(app.Handler, 'require_access', return_value=True)
        access.start()
        self.addCleanup(access.stop)

        def delayed_check(config, tag):
            entered.set()
            release.wait(timeout=5)
            return 'success', 'checked'

        self.patch('check_server_connection', side_effect=delayed_check)
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        try:
            body = urlencode({'csrf': app.CSRF_TOKEN, 'outbound_json': json.dumps({'type': 'hysteria2', 'server': '203.0.113.1', 'server_port': 443, 'password': 'test-only'})})
            connection.request('POST', '/outbounds/import', body, {'Content-Type': 'application/x-www-form-urlencoded'})
            response = connection.getresponse()
            self.assertEqual(response.status, 303)
            response.read()
            self.assertTrue(entered.wait(timeout=2))
            future = app.OUTBOUND_CHECK_JOBS['auto-1']['future']
            self.assertFalse(future.done())
            connection.request('GET', '/outbounds/checks')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
            self.assertEqual(next(item['state'] for item in payload['checks'] if item['tag'] == 'auto-1'), 'running')
            release.set()
            future.result(timeout=2)
        finally:
            release.set()
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            executor.shutdown(wait=True)

    def test_automatic_switch_rechecks_revision_and_syncs_happ(self):
        root = Path(self.directory.name)
        self.patch('APP_DIR', root)
        self.mode_path.write_text(json.dumps({'mode': 'vless'}))
        (root / 'vless-monitor-settings.json').write_text(json.dumps({'enabled': True, 'auto_switch': True, 'interval_minutes': 1}))
        config = {'outbounds': [{'type': 'vless', 'tag': 'auto-1'}, {'type': 'vless', 'tag': 'auto-2'}], 'route': {'final': 'auto-1'}}
        self.gateway_path.write_text(json.dumps(config))
        self.patch('load_config', side_effect=lambda: json.loads(self.gateway_path.read_text()))
        monitor = types.SimpleNamespace(lock=threading.Lock(), revision=2, record_switch=Mock(), append_event=Mock())
        self.patch('VLESS_MONITOR', monitor)
        servers = config['outbounds']
        self.assertFalse(app.switch_monitored_route('auto-2', 'auto-1', servers, 40, 1))
        self.assertEqual(json.loads(self.gateway_path.read_text())['route']['final'], 'auto-1')
        self.assertTrue(app.switch_monitored_route('auto-2', 'auto-1', servers, 40, 2))
        self.assertEqual(json.loads(self.happ_path.read_text())['route']['final'], 'auto-2')
        monitor.record_switch.assert_called_once_with('auto-1', 'auto-2', 'automatic', 40)

    def test_failover_switch_uses_its_own_flag_and_covers_every_server_type(self):
        root = Path(self.directory.name)
        self.patch('APP_DIR', root)
        self.mode_path.write_text(json.dumps({'mode': 'vless'}))
        config = {'outbounds': [{'type': 'trojan', 'tag': 'auto-1'}, {'type': 'vless', 'tag': 'auto-2'}, {'type': 'hysteria2', 'tag': 'auto-3'}], 'route': {'final': 'auto-1'}}
        self.gateway_path.write_text(json.dumps(config))
        self.patch('load_config', side_effect=lambda: json.loads(self.gateway_path.read_text()))
        monitor = types.SimpleNamespace(lock=threading.Lock(), revision=2, record_switch=Mock(), append_event=Mock())
        self.patch('VLESS_MONITOR', monitor)
        settings = root / 'vless-monitor-settings.json'
        servers = app.managed_server_outbounds(config)
        settings.write_text(json.dumps({'enabled': True, 'auto_switch': True, 'failover_enabled': False}))
        self.assertFalse(app.switch_monitored_route('auto-2', 'auto-1', servers, 40, 2, 'failover'))
        settings.write_text(json.dumps({'enabled': False, 'auto_switch': False, 'failover_enabled': True}))
        self.assertFalse(app.switch_monitored_route('auto-2', 'auto-1', servers, 40, 2))
        self.assertFalse(app.switch_monitored_route('auto-2', 'auto-1', servers, 40, 1, 'failover'))
        self.assertFalse(app.switch_monitored_route('auto-2', 'auto-1', servers[:2], 40, 2, 'failover'))
        self.assertEqual(json.loads(self.gateway_path.read_text())['route']['final'], 'auto-1')
        self.assertTrue(app.switch_monitored_route('auto-2', 'auto-1', servers, 40, 2, 'failover'))
        self.assertEqual(json.loads(self.gateway_path.read_text())['route']['final'], 'auto-2')
        self.assertEqual(json.loads(self.happ_path.read_text())['route']['final'], 'auto-2')
        monitor.record_switch.assert_called_once_with('auto-1', 'auto-2', 'failover', 40)
        self.mode_path.write_text(json.dumps({'mode': 'default'}))
        self.assertFalse(app.switch_monitored_route('auto-3', 'auto-2', app.managed_server_outbounds(json.loads(self.gateway_path.read_text())), 40, 2, 'failover'))

    def test_monitor_settings_post_saves_failover_message_and_offset_and_rejects_invalid(self):
        root = Path(self.directory.name)
        self.patch('APP_DIR', root)
        monitor = VlessMonitor(root, lambda: {}, Mock(), Mock(), Mock(), lambda: 'vless')
        self.addCleanup(monitor.stop)
        self.patch('VLESS_MONITOR', monitor)
        access = patch.object(app.Handler, 'require_access', return_value=True)
        access.start()
        self.addCleanup(access.stop)
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)

        def post(fields):
            connection.request('POST', '/settings/vless-monitor', urlencode({'csrf': app.CSRF_TOKEN, 'interval_minutes': '7', **fields}), {'Content-Type': 'application/x-www-form-urlencoded'})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 303)
            location = urlparse(response.getheader('Location'))
            self.assertEqual(location.path, '/settings')
            return parse_qs(location.query)

        try:
            result = post({'monitor_enabled': 'on', 'failover_enabled': 'on', 'message_template': '🔁 {old} → {new}\r\n{date} {time}', 'utc_offset': '+05:00'})
            self.assertEqual(result['kind'], ['success'])
            saved = json.loads((root / 'vless-monitor-settings.json').read_text(encoding='utf-8'))
            self.assertEqual((saved['enabled'], saved['failover_enabled'], saved['auto_switch'], saved['mattermost_enabled']), (True, True, False, False))
            self.assertEqual((saved['interval_minutes'], saved['utc_offset'], saved['message_template']), (7, '+05:00', '🔁 {old} → {new}\n{date} {time}'))

            for fields in ({'message_template': '{secret}'}, {'message_template': 'x' * 1001}, {'utc_offset': 'MSK'}, {'utc_offset': '+15:00'}):
                self.assertEqual(post({'failover_enabled': 'on', **fields})['kind'], ['error'])
                self.assertEqual(json.loads((root / 'vless-monitor-settings.json').read_text(encoding='utf-8')), saved)

            self.assertEqual(post({'failover_enabled': 'on'})['kind'], ['success'])
            kept = json.loads((root / 'vless-monitor-settings.json').read_text(encoding='utf-8'))
            self.assertEqual((kept['message_template'], kept['utc_offset'], kept['enabled']), (saved['message_template'], '+05:00', False))

            self.assertEqual(post({'message_template': '  ', 'utc_offset': '-03:30'})['kind'], ['success'])
            reset = json.loads((root / 'vless-monitor-settings.json').read_text(encoding='utf-8'))
            self.assertEqual((reset['message_template'], reset['utc_offset'], reset['failover_enabled']), (DEFAULT_SETTINGS['message_template'], '-03:30', False))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_completed_check_replaces_legacy_started_banner(self):
        self.patch('service_state', return_value='active')
        self.patch('outbound_check_states', return_value={'checks': [{'tag': 'auto-8', 'state': 'error', 'message': 'TLS certificate error'}]})
        page = app.render_outbounds_page(self.gateway, {}, 'auto-8: проверка соединения запущена в фоне.')
        self.assertIn('data-outbound-summary', page)
        self.assertIn('auto-8: TLS certificate error', page)
        self.assertNotIn('проверка соединения запущена в фоне', page)

    def test_check_summary_pending_and_success(self):
        checks = {
            'auto-1': {'tag': 'auto-1', 'state': 'success', 'message': 'Внешний IP: 203.0.113.1'},
            'auto-2': {'tag': 'auto-2', 'state': 'queued', 'message': 'В очереди'},
        }
        pending = app.check_summary_notice(checks, ['auto-1', 'auto-2'])
        self.assertIn('Проверено 1/2', pending)
        checks['auto-2'].update(state='success', message='Готово')
        finished = app.check_summary_notice(checks, ['auto-1', 'auto-2'])
        self.assertIn('Проверки завершены: 2/2', finished)


if __name__ == '__main__':
    unittest.main()