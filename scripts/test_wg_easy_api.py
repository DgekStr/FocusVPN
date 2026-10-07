import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from wg_easy_api import WgEasyApi, WgEasyApiError
import wg_admin
sys.path.insert(0, str(Path(__file__).resolve().parent))
from configure_wg_easy import prepare_credentials, verify_api, write_init_environment


class WgEasyAuthorizationTests(unittest.TestCase):
    def test_initial_api_500_is_retried_until_wireguard_interface_is_ready(self):
        class FakeApi:
            attempts = 0

            def __init__(self, _secret_path):
                pass

            def clients(self):
                type(self).attempts += 1
                if type(self).attempts < 4:
                    raise WgEasyApiError('HTTP 500', status_code=500)

        verify_api('unused', FakeApi, attempts=5, delay=0)
        self.assertEqual(FakeApi.attempts, 4)

    def test_authentication_error_is_not_retried(self):
        class UnauthorizedApi:
            attempts = 0

            def __init__(self, _secret_path):
                pass

            def clients(self):
                type(self).attempts += 1
                raise WgEasyApiError('HTTP 401', status_code=401)

        with self.assertRaises(WgEasyApiError):
            verify_api('unused', UnauthorizedApi, attempts=5, delay=0)
        self.assertEqual(UnauthorizedApi.attempts, 1)

    def test_generated_credentials_are_preserved_across_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secret.json'
            first = prepare_credentials(path)
            self.assertGreaterEqual(len(first['password']), 32)
            self.assertEqual(prepare_credentials(path), first)

    def test_template_replaced_and_private_init_environment_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secret.json'
            path.write_text(json.dumps({'username': '<username>', 'password': '<password>'}), encoding='utf-8')
            environment = Path(directory) / 'init.env'
            write_init_environment(path, environment, '192.0.2.1', '10.8.0.0/24')
            content = environment.read_text(encoding='utf-8')
            self.assertIn('INIT_ENABLED=true\n', content)
            self.assertIn('INIT_HOST=192.0.2.1\n', content)
            self.assertNotIn('<password>', content)
            if sys.platform != 'win32':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(environment.stat().st_mode & 0o777, 0o600)

    def test_unconfigured_credentials_do_not_send_http_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / 'secret.json'
            for value in ('<wg-easy-admin-password>', '', None):
                with self.subTest(value=value):
                    secret_path.write_text(json.dumps({'username': 'admin', 'password': value}), encoding='utf-8')
                    with patch('wg_easy_api.request.urlopen') as urlopen:
                        with self.assertRaisesRegex(WgEasyApiError, 'первичную настройку'):
                            WgEasyApi(secret_path).clients()
                        urlopen.assert_not_called()

    def test_configured_credentials_keep_basic_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / 'secret.json'
            secret_path.write_text(json.dumps({'username': 'example', 'password': 'test-only-value'}), encoding='utf-8')
            expected = 'Basic ' + base64.b64encode(b'example:test-only-value').decode('ascii')
            self.assertEqual(WgEasyApi(secret_path)._authorization(), expected)


class WgClientDeletionTests(unittest.TestCase):
    def setUp(self):
        self.clients = [
            {'id': 7, 'name': 'Alice', 'ipv4Address': '10.8.0.7', 'enabled': True},
            {'id': 8, 'name': 'Bob', 'ipv4Address': '10.8.0.8', 'enabled': False},
        ]
        self.api = Mock(spec=WgEasyApi)
        self.api.clients.return_value = self.clients
        self.api.client.return_value = self.clients[0]
        self.api.general.return_value = {}
        self.api.interface.return_value = {'enabled': True}
        self.admin = wg_admin.WgAdmin(self.api, 'test-csrf')

    def test_table_delete_is_last_action_after_lan(self):
        for denies in (set(), {'10.8.0.7', '10.8.0.8'}):
            with self.subTest(denies=denies):
                with patch.object(wg_admin, 'load_lan_denies', return_value=denies), patch.object(wg_admin, 'current_wan_ip', return_value='192.0.2.1'), patch.object(wg_admin, 'service_uptime', return_value='1 hour'):
                    page = self.admin.render_dashboard()
                for client in self.clients:
                    identifier = client['id']
                    row = page.split(f'<tr data-wg-client-id="{identifier}">', 1)[1].split('</tr>', 1)[0]
                    delete_form = f'<form method="post" action="/wireguard/client/action"><input type="hidden" name="csrf" value="test-csrf"><input type="hidden" name="client_id" value="{identifier}"><input type="hidden" name="action" value="delete"><button class="danger" type="submit" aria-label="Удалить клиента {client["name"]}">Удалить</button></form>'
                    self.assertIn(delete_form, row)
                    label = 'Разрешить LAN' if client['ipv4Address'] in denies else 'Запретить LAN'
                    self.assertLess(row.index('>' + label + '</button>'), row.index(delete_form))
                    self.assertNotIn('<form', row.split(delete_form, 1)[1])
                    self.assertNotIn('<button', row.split(delete_form, 1)[1])
                    self.assertEqual(row.count('name="action" value="delete"'), 1)

    def test_delete_removes_client_and_only_its_lan_rule(self):
        def delete_client(identifier):
            self.clients[:] = [client for client in self.clients if client['id'] != int(identifier)]

        self.api.delete_client.side_effect = delete_client
        with patch.object(wg_admin, 'load_lan_denies', return_value={'10.8.0.7', '10.8.0.8'}), patch.object(wg_admin, 'save_lan_denies') as save, patch.object(wg_admin, 'restart_lan_firewall') as restart:
            message = self.admin.client_action({'client_id': ['7'], 'action': ['delete']})
        self.api.client.assert_called_once_with('7')
        self.api.delete_client.assert_called_once_with('7')
        self.assertEqual([client['id'] for client in self.clients], [8])
        self.assertEqual(message, 'Клиент удалён.')
        save.assert_called_once_with({'10.8.0.8'})
        restart.assert_called_once_with()

    def test_failed_delete_preserves_clients_and_lan_rules(self):
        self.api.delete_client.side_effect = WgEasyApiError('HTTP 500')
        with patch.object(wg_admin, 'load_lan_denies') as load, patch.object(wg_admin, 'save_lan_denies') as save, patch.object(wg_admin, 'restart_lan_firewall') as restart:
            with self.assertRaises(WgEasyApiError):
                self.admin.client_action({'client_id': ['7'], 'action': ['delete']})
        self.assertEqual([client['id'] for client in self.clients], [7, 8])
        load.assert_not_called()
        save.assert_not_called()
        restart.assert_not_called()

    def test_delete_api_method_and_id_validation(self):
        api = WgEasyApi('unused')
        with patch.object(api, '_request', return_value={}) as request:
            api.delete_client('7')
            request.assert_called_once_with('DELETE', '/api/client/7')
        for identifier in ('0', '-1', '7/enable', 'invalid'):
            with self.subTest(identifier=identifier), patch.object(api, '_request') as request:
                with self.assertRaises(WgEasyApiError):
                    api.delete_client(identifier)
                request.assert_not_called()


if __name__ == '__main__':
    unittest.main()