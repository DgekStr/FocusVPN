import base64
import json
from pathlib import Path

STATE_PATH = Path('/etc/sing-box-admin/happ-server.json')


def load_state():
    return json.loads(STATE_PATH.read_text(encoding='utf-8'))


def happ_add_link(link):
    payload = base64.b64encode(link.encode('utf-8')).decode('ascii')
    return 'happ://add/' + payload


def public_vless_link():
    return load_state()['link']


def public_happ_link():
    return happ_add_link(public_vless_link())
