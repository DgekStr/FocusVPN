import base64
import html
import json
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlsplit, urlunsplit

from happ_users import parse_expiry

STATE_PATH = Path('/etc/sing-box-admin/happ-server.json')
SUBSCRIPTION_ANNOUNCEMENT = (
    '🔒Это частный VPN сервер, для работы команды разработчиков focuslens.dev. '
    'Если вы здесь оказались - это не случайно ❤️'
)


def load_state():
    return json.loads(STATE_PATH.read_text(encoding='utf-8'))


def happ_add_link(link):
    return 'happ://add/' + (link if link.startswith(('https://', 'http://')) else quote(link, safe=''))


def public_vless_link():
    return load_state()['link']


def public_happ_link():
    return happ_add_link(public_vless_link())


def vless_link_for_subscription(link, subscription_url):
    parsed_link = urlsplit(link)
    public_host = urlsplit(subscription_url).hostname
    if parsed_link.scheme != 'vless' or not parsed_link.hostname or not public_host:
        raise ValueError('Не удалось определить публичный VLESS host.')
    if ':' in public_host:
        public_host = '[' + public_host + ']'
    userinfo, separator, _ = parsed_link.netloc.rpartition('@')
    authority = (userinfo + separator if separator else '') + public_host
    if parsed_link.port:
        authority += ':' + str(parsed_link.port)
    return urlunsplit((parsed_link.scheme, authority, parsed_link.path, parsed_link.query, parsed_link.fragment))


def subscription_content(user, traffic, information_url=None):
    download = max(0, int(traffic.get('download_bytes', 0)))
    upload = max(0, int(traffic.get('upload_bytes', 0)))
    userinfo = f'upload={upload}; download={download}; total=0'
    expiry = parse_expiry(user.get('expires_at'))
    if expiry is not None:
        userinfo += '; expire=' + str(int(expiry.timestamp()))
    title = base64.b64encode(('\U0001f5a7 FocusVPN ' + user['name'])[:25].encode('utf-8')).decode('ascii')
    headers = {'subscription-userinfo': userinfo, 'profile-update-interval': '1', 'profile-title': 'base64:' + title}
    downloaded = f'{download / (1024 ** 3):.2f} ГБ' if download >= 1024 ** 3 else f'{download / (1024 ** 2):.2f} МБ'
    announcement = SUBSCRIPTION_ANNOUNCEMENT + '\nСкачано: ' + downloaded + ' / ∞'
    headers['announce'] = 'base64:' + base64.b64encode(announcement.encode('utf-8')).decode('ascii')
    if information_url:
        headers['profile-web-page-url'] = information_url
    body = ''.join(f'#{key}: {value}\n' for key, value in headers.items()) + user['link'] + '\n'
    return body.encode('utf-8'), headers


def subscription_information_page():
    paragraphs = ''.join('<p>' + html.escape(paragraph) + '</p>' for paragraph in SUBSCRIPTION_ANNOUNCEMENT.split('\n'))
    return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>FocusVPN</title><style>body{margin:0;background:#f4f7fa;color:#17252e;font:18px/1.65 Georgia,serif}main{max-width:680px;margin:48px auto;padding:0 24px}h1{font-size:28px}p{text-align:justify;overflow-wrap:anywhere;hyphens:auto}@media(max-width:480px){main{margin:24px auto;padding:0 18px}}</style></head><body><main><h1>FocusVPN</h1>' + paragraphs + '</main></body></html>'
