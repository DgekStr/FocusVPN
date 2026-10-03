import hmac
import html
import ipaddress
import os
from html.parser import HTMLParser
from pathlib import Path


BASE_PATH = '/admin/vpn/panel'


def authorized(headers, peer_address):
    token_file = os.environ.get('FOCUSVPN_CRM_TOKEN_FILE', '').strip()
    allowed_networks = os.environ.get('FOCUSVPN_CRM_NETWORKS', '').strip()
    authorization = headers.get('Authorization', '')
    if not token_file or not allowed_networks or not authorization.startswith('Bearer '):
        return False
    try:
        peer = ipaddress.ip_address(peer_address)
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
        networks = [ipaddress.ip_network(value.strip(), strict=False) for value in allowed_networks.split(',')]
        if not any(peer in network for network in networks):
            return False
        expected = Path(token_file).read_text(encoding='ascii').strip()
        if len(expected) < 32:
            return False
        supplied = authorization[7:]
        return hmac.compare_digest(supplied.encode('utf-8'), expected.encode('ascii'))
    except (OSError, ValueError, UnicodeError):
        return False


def panel_url(value):
    if value.startswith('/') and not value.startswith('//') and not value.startswith(BASE_PATH + '/'):
        return BASE_PATH + value
    return value


class EmbeddedPanel(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output = []

    def render_tag(self, tag, attrs, closed=False):
        values = dict(attrs)
        for name in ('href', 'src', 'action', 'data-qr-url', 'data-wg-qr-url'):
            if values.get(name):
                values[name] = panel_url(values[name])
        if tag == 'body':
            values['data-vpn-base'] = BASE_PATH
        if tag == 'a' and dict(attrs).get('href') == '/logout':
            values['href'] = '/admin'
            values['target'] = '_top'
        attributes = ''.join(
            ' ' + name if value is None else f' {name}="{html.escape(value, quote=True)}"'
            for name, value in values.items()
        )
        self.output.append(f'<{tag}{attributes}{" /" if closed else ""}>')

    def handle_starttag(self, tag, attrs):
        self.render_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self.render_tag(tag, attrs, True)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.output.append('<link rel="stylesheet" href="/vpn-embed.css?v=20261003">')
            self.output.append('<script src="/vpn-embed.js?v=20261003" defer></script>')
        self.output.append(f'</{tag}>')

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f'&{name};')

    def handle_charref(self, name):
        self.output.append(f'&#{name};')

    def handle_comment(self, data):
        self.output.append(f'<!--{data}-->')

    def handle_decl(self, declaration):
        self.output.append(f'<!{declaration}>')


def embed(content):
    parser = EmbeddedPanel()
    parser.feed(content)
    parser.close()
    return ''.join(parser.output)