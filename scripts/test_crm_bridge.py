import base64
import http.client
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
from crm_bridge import BASE_PATH, authorized, embed
import app


class CrmBridgeTests(unittest.TestCase):
    def test_bridge_requires_both_service_token_and_source_network(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / 'token'
            token_file.write_text('test-bridge-token-' + 'a' * 48, encoding='ascii')
            settings = {
                'FOCUSVPN_CRM_TOKEN_FILE': str(token_file),
                'FOCUSVPN_CRM_NETWORKS': '192.168.0.101/32',
            }
            headers = {'Authorization': 'Bearer ' + token_file.read_text(encoding='ascii')}
            with patch.dict(os.environ, settings):
                self.assertTrue(authorized(headers, '192.168.0.101'))
                self.assertTrue(authorized(headers, '::ffff:192.168.0.101'))
                self.assertFalse(authorized(headers, '192.168.0.102'))
                self.assertFalse(authorized({'Authorization': 'Bearer invalid'}, '192.168.0.101'))
                self.assertFalse(authorized({}, '192.168.0.101'))

    def test_bridge_is_disabled_without_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(authorized({'Authorization': 'Bearer ' + 'a' * 64}, '192.168.0.101'))

    def test_embedded_panel_preserves_csrf_and_rewrites_local_routes_only(self):
        markup = '<!doctype html><html><head><script src="/panel.js"></script></head><body><form action="/outbounds/route" method="post"><input name="csrf" value="test&amp;token"></form><a href="https://vpn.example/happ-subscription/test">Subscription</a><a href="/logout">Exit</a><img data-qr-url="/happ-qr" src="/favicon.png"><button data-wg-qr-url="/wireguard/client/7/qr">QR</button></body></html>'
        result = embed(markup)
        self.assertIn(f'src="{BASE_PATH}/panel.js"', result)
        self.assertIn(f'action="{BASE_PATH}/outbounds/route"', result)
        self.assertIn(f'data-wg-qr-url="{BASE_PATH}/wireguard/client/7/qr"', result)
        self.assertIn(f'data-vpn-base="{BASE_PATH}"', result)
        self.assertIn('name="csrf" value="test&amp;token"', result)
        self.assertIn('href="https://vpn.example/happ-subscription/test"', result)
        self.assertIn('href="/admin" target="_top"', result)
        self.assertIn('/vpn-embed.css', result)
        self.assertIn('/vpn-embed.js', result)

    def test_admin_credentials_require_explicit_embed_header_and_valid_password(self):
        password = ' vpn-admin-password:with spaces '
        salt = b'crm-bridge-test!'
        auth = ('vpn-admin', salt, app.password_digest(password, salt), 'FocusVPN')
        markup = '<html><head></head><body><form method="post" action="/outbounds/check"></form></body></html>'
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        valid = 'Basic ' + base64.b64encode(('vpn-admin:' + password).encode()).decode('ascii')
        invalid = 'Basic ' + base64.b64encode(b'vpn-admin:wrong-password').decode('ascii')
        try:
            with patch.dict(os.environ, {}, clear=True), patch.object(app, 'load_auth', return_value=auth), patch.object(app, 'load_config', return_value={}), patch.object(app, 'render_outbounds_page', return_value=markup), patch.object(app.Handler, 'vpn_client_allowed', return_value=True), patch.object(app.Handler, 'session_authenticated', return_value=False):
                for headers, status in [({}, 303), ({'Authorization': valid}, 303), ({'Authorization': invalid, 'X-FocusVPN-CRM': '1'}, 303), ({'Authorization': valid, 'X-FocusVPN-CRM': '1'}, 200)]:
                    connection.request('GET', '/outbounds', headers=headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, status)
                    content = response.read().decode('utf-8')
                    if status == 200:
                        self.assertEqual(response.getheader('X-Frame-Options'), 'SAMEORIGIN')
                        self.assertIn(f'data-vpn-base="{BASE_PATH}"', content)
                        self.assertIn(f'action="{BASE_PATH}/outbounds/check"', content)
                    else:
                        self.assertEqual(response.getheader('Location'), '/login')
                        self.assertEqual(response.getheader('X-Frame-Options'), 'DENY')
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()