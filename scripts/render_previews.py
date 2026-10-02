import argparse
import datetime as dt
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server' / 'panel'))
if sys.platform == 'win32':
    import types
    sys.modules.setdefault('grp', types.ModuleType('grp'))
import app
import happ_server_ui
import panel_ui
from happ_history import HappHistory
from happ_history_ui import render_history

LIVE = {
    'online_count': 1, 'download': '3.0 MB', 'upload': '192.0 KB',
    'connections': [{'user_name': 'demo-dev-01', 'ip': '203.0.113.24', 'duration': '18 мин.', 'download': '3.0 MB', 'upload': '192.0 KB', 'network': 'TCP', 'destination': 'example.com:443'}],
    'users': [{'user_name': 'demo-dev-01', 'connections': 1, 'download': '3.0 MB', 'upload': '192.0 KB'}],
    'account_traffic': {'personal-demo-a': {'download': '64.0 MB', 'upload': '8.0 MB'}, 'personal-demo-b': {'download': '12.0 MB', 'upload': '1.0 MB'}},
}
CHECKS = {'checks': [
    {'tag': 'demo-vless', 'state': 'success', 'message': 'Внешний IP: 203.0.113.10', 'latency_ms': 83.4, 'checked_at': '2026-10-03T09:15:23+00:00'},
    {'tag': 'demo-hy2', 'state': 'error', 'message': 'Нет HTTPS-ответа', 'checked_at': '2026-10-03T09:15:25+00:00'},
]}


def render_pages():
    users = [
        {'id': 'demo-a', 'name': 'demo-dev-01', 'status': 'enabled', 'link': 'vless://<demo-only-user>@vpn.example.com:9445#demo-dev-01'},
        {'id': 'demo-b', 'name': 'demo-dev-02', 'status': 'disabled', 'link': 'vless://<demo-only-user>@vpn.example.com:9445#demo-dev-02'},
    ]
    subscriptions = {user['id']: 'http://127.0.0.1:8788/happ-subscription/demo-only-' + user['id'] for user in users}
    subscriptions['VIP'] = 'http://127.0.0.1:8788/happ-subscription/demo-only-vip'
    config = {'outbounds': [{'tag': 'demo-vless', 'type': 'vless', 'server': 'vpn.example.com', 'server_port': 443}, {'tag': 'demo-hy2', 'type': 'hysteria2', 'server': 'hy2.example.com', 'server_port': 443}], 'route': {'final': 'demo-vless'}}
    with patch.object(panel_ui, 'load_profile_tags', return_value=['demo-vless', 'demo-hy2']), patch.object(happ_server_ui, 'load_state', return_value={'server': 'vpn.example.com', 'port': 9445, 'sni': 'example.org'}), patch.object(happ_server_ui, 'public_vless_link', return_value='vless://<demo-only-vip>@vpn.example.com:9445#VIP'), patch.object(app, 'CSRF_TOKEN', 'demo-only-csrf'), patch.object(app, 'service_state', return_value='active'), patch.object(app, 'outbound_check_states', return_value=CHECKS):
        happ = happ_server_ui.page(users, 'demo-only-csrf', traffic=LIVE['account_traffic'], subscriptions=subscriptions)
        outbounds = app.render_outbounds_page(config, {})
        with tempfile.TemporaryDirectory(prefix='focusvpn-preview-') as directory:
            history = HappHistory(directory)
            at = dt.datetime(2026, 10, 3, 9, 15, 23, tzinfo=dt.timezone.utc)
            records = [{'id': 'demo-live-' + str(index), 'connection_key': 'demo-visit-' + str(index), 'user_key': 'personal-demo-a', 'user_name': 'demo-dev-01', 'started_at': (at - dt.timedelta(minutes=index * 3)).isoformat(), 'ip': '203.0.113.24', 'source_port': 40000 + index, 'destination': destination, 'network': 'TCP', 'download_bytes': 33554432, 'upload_bytes': 4194304} for index, destination in enumerate(('example.com:443', 'docs.example.org:443'))]
            history.ingest({'connections': records}, at)
            history.ingest({'connections': []}, at)
            history_page = render_history(history, {'since': ['2026-10-03'], 'until': ['2026-10-03']}, registered_users=users)
    return {'/happ-server': happ, '/outbounds': outbounds, '/happ-history': history_page}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--port', type=int, default=8788)
    arguments = parser.parse_args()
    pages = render_pages()
    preview = pages['/happ-server'].replace('/panel.css?v=20', '../server/panel/static/panel.css').replace('/favicon.png', '../server/panel/static/favicon.png').replace('/panel.js?v=17', '../server/panel/static/panel.js').replace('/happ-actions.js?v=7', '../server/panel/static/happ-actions.js')
    (ROOT / 'docs' / 'ui-preview.html').write_text(preview, encoding='utf-8')
    assert 'v2.0' in preview and 'data-happ-account="personal-demo-a"' in preview
    assert '03.10.2026 09:15:23' in pages['/happ-history']
    print('safe renderer previews generated', flush=True)
    if not arguments.serve:
        return

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_POST(self):
            allowed = {'project-overview.png', 'wireguard-and-happ-preview.png', 'vpn-servers.png', 'happ-history.png', 'happ-mobile.png'}
            filename = self.path.removeprefix('/capture/')
            if not arguments.capture or not self.path.startswith('/capture/') or filename not in allowed or self.headers.get('X-Preview-Capture') != 'renderer-fixture-only':
                self.send_error(403)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 8 <= length <= 20000000:
                self.send_error(400)
                return
            content = self.rfile.read(length)
            if not content.startswith(bytes.fromhex('89504e470d0a1a0a')):
                self.send_error(400)
                return
            (ROOT / 'docs' / 'screenshots' / filename).write_bytes(content)
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in pages:
                content, kind = pages[path].encode('utf-8'), 'text/html; charset=utf-8'
            elif path in ('/happ-server/live', '/outbounds/checks'):
                content, kind = json.dumps(LIVE if path == '/happ-server/live' else CHECKS, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8'
            else:
                assets = {'/panel.css': ('server/panel/static/panel.css', 'text/css'), '/panel.js': ('server/panel/static/panel.js', 'text/javascript'), '/happ-actions.js': ('server/panel/static/happ-actions.js', 'text/javascript'), '/favicon.png': ('server/panel/static/favicon.png', 'image/png'), '/': ('index.html', 'text/html; charset=utf-8')}
                if path in assets:
                    filename, kind = assets[path]
                elif path.startswith('/docs/screenshots/') and '..' not in path:
                    filename, kind = path[1:], 'image/png'
                else:
                    self.send_error(404)
                    return
                content = (ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', arguments.port), Handler)
    print(f'preview=http://127.0.0.1:{arguments.port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()