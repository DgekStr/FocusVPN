import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from wg_easy_api import WgEasyApi, WgEasyApiError
sys.path.insert(0, str(Path(__file__).resolve().parent))
from configure_wg_easy import prepare_credentials, write_init_environment


class WgEasyAuthorizationTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()