"""TrustTunnel upstream servers: profile import (JSON and tt:// links) and the official client run as an authenticated loopback SOCKS5 bridge for sing-box."""
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from happ_protocols import HOSTNAME_PATTERN
from vless_monitor import write_private_json

TT_TYPE = 'trusttunnel'
TYPE_ALIASES = frozenset({'trusttunnel', 'trust-tunnel', 'trust_tunnel'})
REGISTRY_FILE = 'trusttunnel-servers.json'
CLIENT_BIN = Path('/opt/trusttunnel/trusttunnel_client')
CLIENT_DIR = Path('/etc/sing-box/trusttunnel-clients')
UNIT_TEMPLATE = 'trusttunnel-client@{}.service'
SYSTEMCTL_BIN = '/usr/bin/systemctl'
SOCKS_HOST = '127.0.0.1'
SOCKS_USER = 'focusvpn'
PORT_RANGE = range(19500, 19600)
UPSTREAM_PROTOCOLS = ('http2', 'http3')
TLS_PROFILES = ('chrome', 'safari', 'firefox', 'okhttp', 'openssl', 'default')
TAG_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
CREDENTIAL_PATTERN = re.compile(r'[\x21-\x7e]{1,256}\Z')
CLIENT_RANDOM_PATTERN = re.compile(r'[0-9A-Fa-f]{1,64}(?:/[0-9A-Fa-f]{1,64})?\Z')
PEM_BLOCK = re.compile(r'-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----', re.S)
MAX_ADDRESSES = 8
MAX_CERTIFICATES = 8
CLIENT_MISSING = 'Клиент TrustTunnel не установлен (/opt/trusttunnel/trusttunnel_client): установите его установщиком и повторите импорт.'
OUTBOUND_KEYS = frozenset({
    'type', 'tag', 'name', 'server', 'server_port', 'addresses', 'username', 'password', 'tls', 'upstream_protocol',
    'tls_profile', 'anti_dpi', 'client_random', 'has_ipv6',
})
TLS_KEYS = frozenset({'server_name', 'insecure', 'certificate'})
ENDPOINT_KEYS = {
    'hostname': 'server_name', 'addresses': 'addresses', 'username': 'username', 'password': 'password', 'certificate': 'certificate',
    'skip_verification': 'skip_verification', 'upstream_protocol': 'upstream_protocol', 'tls_profile': 'tls_profile',
    'anti_dpi': 'anti_dpi', 'client_random': 'client_random', 'has_ipv6': 'has_ipv6',
}


def parse_port(value, label='Порт'):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f'{label}: укажите номер порта.')
    try:
        port = int(str(value).strip())
    except ValueError as error:
        raise ValueError(f'{label}: порт должен быть числом.') from error
    if not 1 <= port <= 65535:
        raise ValueError(f'{label}: порт должен быть в диапазоне 1-65535.')
    return port


def validate_host(value, label='Адрес сервера'):
    text = str(value or '').strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if not text or not HOSTNAME_PATTERN.fullmatch(text):
        raise ValueError(f'{label}: укажите IP-адрес или имя хоста.')
    return text.rstrip('.').lower()


def format_address(host, port):
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def split_address(value, default_port):
    text = str(value or '').strip()
    if not text:
        raise ValueError('Адрес сервера: значение пустое.')
    host, port = text, default_port
    if text.startswith('['):
        end = text.find(']')
        rest = text[end + 1:] if end > 0 else 'x'
        if end < 0 or (rest and not rest.startswith(':')):
            raise ValueError('Адрес сервера: некорректная запись IPv6.')
        host, port = text[1:end], (rest[1:] if rest else default_port)
    elif text.count(':') == 1:
        host, port = text.split(':', 1)
    return validate_host(host), parse_port(port, 'Порт сервера')


def strict_bool(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label}: допустимо только true или false.')
    return value


def pem_from_der(certificates):
    blocks = []
    for der in certificates:
        body = base64.b64encode(der).decode('ascii')
        blocks.append('-----BEGIN CERTIFICATE-----\n' + '\n'.join(body[index:index + 64] for index in range(0, len(body), 64)) + '\n-----END CERTIFICATE-----\n')
    return ''.join(blocks)


def split_der(blob):
    certificates, offset = [], 0
    while offset < len(blob):
        if blob[offset] != 0x30 or offset + 2 > len(blob):
            raise ValueError('Сертификат TrustTunnel повреждён.')
        marker = blob[offset + 1]
        if marker < 0x80:
            header, length = 2, marker
        else:
            count = marker & 0x7F
            header = 2 + count
            if not 1 <= count <= 4 or offset + header > len(blob):
                raise ValueError('Сертификат TrustTunnel повреждён.')
            length = int.from_bytes(blob[offset + 2:offset + header], 'big')
        end = offset + header + length
        if end > len(blob):
            raise ValueError('Сертификат TrustTunnel повреждён.')
        certificates.append(blob[offset:end])
        offset = end
    return certificates


def normalize_certificate(value):
    if value in (None, '', []):
        return ''
    text = '\n'.join(str(line) for line in value) if isinstance(value, list) else value
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError('Сертификат TrustTunnel: ожидается PEM-текст или список строк.')
    blocks = PEM_BLOCK.findall(text)
    if not blocks or len(blocks) > MAX_CERTIFICATES:
        raise ValueError('Сертификат TrustTunnel: не найден PEM-блок CERTIFICATE.')
    certificates = []
    for body in blocks:
        try:
            der = base64.b64decode(''.join(body.split()), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError('Сертификат TrustTunnel: некорректное содержимое PEM.') from error
        if not der or der[0] != 0x30:
            raise ValueError('Сертификат TrustTunnel: некорректное содержимое PEM.')
        certificates.append(der)
    return pem_from_der(certificates)


def build_profile(addresses, server_name='', username='', password='', skip_verification=False, certificate='', upstream_protocol='http2',
                  tls_profile='chrome', anti_dpi=False, client_random='', has_ipv6=True, name='', default_port=443):
    if isinstance(addresses, str):
        addresses = [addresses]
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= MAX_ADDRESSES:
        raise ValueError(f'Адреса сервера: укажите от 1 до {MAX_ADDRESSES} адресов.')
    parsed = [split_address(item, default_port) for item in addresses]
    primary_host = parsed[0][0]
    for label, value in (('Логин', username), ('Пароль', password)):
        if not isinstance(value, str) or not CREDENTIAL_PATTERN.fullmatch(value):
            raise ValueError(f'{label} TrustTunnel: до 256 печатных ASCII-символов без пробелов.')
    if upstream_protocol not in UPSTREAM_PROTOCOLS:
        raise ValueError('upstream_protocol должен быть http2 или http3.')
    if tls_profile not in TLS_PROFILES:
        raise ValueError('tls_profile должен быть одним из: ' + ', '.join(TLS_PROFILES) + '.')
    if not isinstance(client_random, str) or (client_random and not CLIENT_RANDOM_PATTERN.fullmatch(client_random)):
        raise ValueError('client_random: hex-значение вида prefix или prefix/mask.')
    display_name = str(name or '').strip()
    if len(display_name) > 80 or any(ord(char) < 32 for char in display_name):
        raise ValueError('Имя сервера TrustTunnel: до 80 символов без управляющих.')
    return {
        'addresses': [format_address(host, port) for host, port in parsed],
        'server_name': validate_host(server_name, 'Имя TLS') if str(server_name or '').strip() else primary_host,
        'username': username, 'password': password,
        'skip_verification': strict_bool(skip_verification, 'skip_verification / tls.insecure'),
        'certificate': normalize_certificate(certificate),
        'upstream_protocol': upstream_protocol, 'tls_profile': tls_profile,
        'anti_dpi': strict_bool(anti_dpi, 'anti_dpi'), 'client_random': client_random,
        'has_ipv6': strict_bool(has_ipv6, 'has_ipv6'), 'name': display_name,
    }


def primary_address(profile):
    return split_address(profile['addresses'][0], 443)


def read_varint(data, offset):
    if offset >= len(data):
        raise ValueError('Ссылка tt:// повреждена.')
    first = data[offset]
    size = 1 << (first >> 6)
    if offset + size > len(data):
        raise ValueError('Ссылка tt:// повреждена.')
    value = first & 0x3F
    for index in range(1, size):
        value = (value << 8) | data[offset + index]
    return value, offset + size


def decode_deeplink(link):
    text = str(link or '').strip()
    if not text.lower().startswith('tt://'):
        raise ValueError('Ссылка TrustTunnel должна начинаться с tt://.')
    payload = text[5:].lstrip('?')
    if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', payload):
        raise ValueError('Ссылка tt:// содержит недопустимые символы.')
    try:
        data = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
    except (binascii.Error, ValueError) as error:
        raise ValueError('Ссылка tt:// не является корректным base64.') from error
    fields, offset = {}, 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        length, offset = read_varint(data, offset)
        if offset + length > len(data):
            raise ValueError('Ссылка tt:// повреждена.')
        fields.setdefault(tag, []).append(data[offset:offset + length])
        offset += length
    return fields


def profile_from_deeplink(link):
    fields = decode_deeplink(link)

    def text(tag):
        try:
            return fields[tag][0].decode('utf-8') if tag in fields else ''
        except UnicodeDecodeError as error:
            raise ValueError('Ссылка tt:// содержит некорректный текст.') from error

    def flag(tag):
        return bool(fields.get(tag)) and fields[tag][0] not in (b'', b'\x00')

    addresses = [item.decode('utf-8', 'replace') for item in fields.get(2, [])]
    if not addresses or not text(1):
        raise ValueError('В ссылке tt:// отсутствует адрес или hostname сервера.')
    protocol = {b'\x01': 'http2', b'\x02': 'http3'}.get(fields.get(9, [b''])[0], 'http2')
    blob = b''.join(fields.get(8, []))
    return build_profile(
        addresses, server_name=text(1), username=text(5), password=text(6), skip_verification=flag(7),
        certificate=pem_from_der(split_der(blob)) if blob else '', upstream_protocol=protocol, anti_dpi=flag(10),
        client_random=text(11), has_ipv6=flag(4) if 4 in fields else True, name=text(12),
    )


def is_trusttunnel_entry(entry):
    if isinstance(entry, str):
        return entry.strip().lower().startswith('tt://')
    if not isinstance(entry, dict):
        return False
    kind = str(entry.get('type') or entry.get('protocol') or '').strip().lower()
    if kind in TYPE_ALIASES:
        return True
    endpoint = entry.get('endpoint')
    if isinstance(endpoint, dict) and 'hostname' in endpoint and 'addresses' in endpoint:
        return True
    link = entry.get('link') or entry.get('url') or entry.get('deeplink')
    return not kind and isinstance(link, str) and link.strip().lower().startswith('tt://')


def parse_profile(entry):
    if isinstance(entry, str):
        return profile_from_deeplink(entry)
    if not isinstance(entry, dict):
        raise ValueError('TrustTunnel: ожидается JSON-объект или ссылка tt://.')
    link = entry.get('link') or entry.get('url') or entry.get('deeplink')
    if isinstance(link, str) and link.strip().lower().startswith('tt://'):
        return profile_from_deeplink(link)
    endpoint = entry.get('endpoint')
    if isinstance(endpoint, dict):
        values = {ENDPOINT_KEYS[key]: value for key, value in endpoint.items() if key in ENDPOINT_KEYS}
        if 'addresses' not in values:
            raise ValueError('В секции endpoint отсутствуют addresses.')
        return build_profile(**values)
    unknown = sorted(str(key) for key in entry if key not in OUTBOUND_KEYS)
    if unknown:
        raise ValueError('TrustTunnel: неизвестные поля: ' + ', '.join(unknown[:5]) + '.')
    tls = entry.get('tls', {})
    if not isinstance(tls, dict) or any(key not in TLS_KEYS for key in tls):
        raise ValueError('TrustTunnel: tls допускает server_name, insecure и certificate.')
    addresses = list(entry['addresses']) if isinstance(entry.get('addresses'), list) else []
    if entry.get('server'):
        addresses.insert(0, format_address(validate_host(entry['server']), parse_port(entry.get('server_port', 443), 'server_port')))
    if not addresses:
        raise ValueError('TrustTunnel: укажите server и server_port.')
    optional = {key: entry[key] for key in ('upstream_protocol', 'tls_profile', 'anti_dpi', 'client_random', 'has_ipv6', 'name') if key in entry}
    return build_profile(
        addresses, server_name=tls.get('server_name', ''), username=entry.get('username'), password=entry.get('password'),
        skip_verification=tls.get('insecure', False), certificate=tls.get('certificate', ''), **optional,
    )


def export_outbound_json(tag, profile):
    host, port = primary_address(profile)
    result = {'type': TT_TYPE, 'tag': tag, 'server': host, 'server_port': port, 'username': profile['username'], 'password': profile['password']}
    extra = profile['addresses'][1:]
    if extra:
        result['addresses'] = extra
    tls = {}
    if profile['server_name'] != host:
        tls['server_name'] = profile['server_name']
    if profile['skip_verification']:
        tls['insecure'] = True
    if profile['certificate']:
        tls['certificate'] = profile['certificate'].strip().splitlines()
    if tls:
        result['tls'] = tls
    for key, default in (('upstream_protocol', 'http2'), ('tls_profile', 'chrome'), ('anti_dpi', False), ('client_random', ''), ('has_ipv6', True), ('name', '')):
        if profile[key] != default:
            result[key] = profile[key]
    return result


def toml_string(value):
    text = str(value)
    if not text.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError('Значение содержит недопустимые для конфигурации TrustTunnel символы.')
    return json.dumps(text)


def toml_bool(value):
    return 'true' if value else 'false'


def render_client_toml(profile, port, username, password):
    certificate = profile['certificate']
    lines = [
        'loglevel = "info"',
        'vpn_mode = "general"',
        # Fail closed: without the kill switch the client falls back to a direct connection while the endpoint is unreachable.
        'killswitch_enabled = true',
        'post_quantum_group_enabled = true',
        'exclusions = []',
        '',
        '[endpoint]',
        'hostname = ' + toml_string(profile['server_name']),
        'addresses = [' + ', '.join(toml_string(item) for item in profile['addresses']) + ']',
        'has_ipv6 = ' + toml_bool(profile['has_ipv6']),
        'username = ' + toml_string(profile['username']),
        'password = ' + toml_string(profile['password']),
        'client_random = ' + toml_string(profile['client_random']),
        'skip_verification = ' + toml_bool(profile['skip_verification']),
        "certificate = '''\n" + certificate + "'''" if certificate else 'certificate = ""',
        'upstream_protocol = ' + toml_string(profile['upstream_protocol']),
        'tls_profile = ' + toml_string(profile['tls_profile']),
        'anti_dpi = ' + toml_bool(profile['anti_dpi']),
        '',
        '[listener.socks]',
        'address = ' + toml_string(format_address(SOCKS_HOST, port)),
        'username = ' + toml_string(username),
        'password = ' + toml_string(password),
        '',
    ]
    return '\n'.join(lines)


def default_run(args, timeout=30):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)


def wait_for_listener(port, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((SOCKS_HOST, port)) == 0:
                return True
        time.sleep(0.25)
    return False


def port_is_free(port):
    with socket.socket() as probe:
        try:
            probe.bind((SOCKS_HOST, port))
        except OSError:
            return False
    return True


class TrustTunnelClients:
    def __init__(self, directory, client_dir=CLIENT_DIR, binary=CLIENT_BIN, run=default_run, group='sing-box', ports=PORT_RANGE,
                 wait_listener=wait_for_listener, port_free=port_is_free):
        self.path = Path(directory) / REGISTRY_FILE
        self.client_dir = Path(client_dir)
        self.binary = Path(binary)
        self.run = run
        self.group = group
        self.ports = ports
        self.wait_listener = wait_listener
        self.port_free = port_free
        self.pending = {}
        self.snapshot = None
        self.salt = None
        self.error = ''

    def available(self):
        return self.binary.is_file()

    def load(self):
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            payload = {}
        except (OSError, ValueError) as error:
            raise ValueError('Файл trusttunnel-servers.json повреждён; исправьте или удалите его.') from error
        salt = payload.get('salt') if isinstance(payload.get('salt'), str) and re.fullmatch(r'[0-9a-f]{64}', payload.get('salt', '')) else None
        self.salt = salt or self.salt or secrets.token_hex(32)
        servers = {}
        for tag, record in (payload.get('servers') if isinstance(payload.get('servers'), dict) else {}).items():
            if not TAG_PATTERN.fullmatch(str(tag)) or not isinstance(record, dict) or not isinstance(record.get('profile'), dict):
                continue
            port = record.get('port')
            if isinstance(port, bool) or not isinstance(port, int) or port not in self.ports:
                continue
            servers[tag] = {'port': port, 'profile': build_profile(**record['profile'])}
        return {'salt': self.salt, 'servers': servers}

    def save(self, registry):
        write_private_json(self.path, {'version': 1, 'salt': registry['salt'], 'servers': registry['servers']})

    def owns(self, tag):
        return tag in self.pending or tag in self.load()['servers']

    def record(self, tag):
        return self.pending.get(tag) or self.load()['servers'].get(tag)

    def socks_password(self, registry, tag, profile):
        message = (tag + '\0' + json.dumps(profile, sort_keys=True, separators=(',', ':'))).encode('utf-8')
        return hmac.new(bytes.fromhex(registry['salt']), message, hashlib.sha256).hexdigest()[:40]

    def outbound(self, registry, tag):
        record = registry['servers'][tag]
        return {
            'type': 'socks', 'tag': tag, 'server': SOCKS_HOST, 'server_port': record['port'], 'version': '5',
            'username': SOCKS_USER, 'password': self.socks_password(registry, tag, record['profile']),
        }

    def export(self, tag):
        record = self.record(tag)
        return export_outbound_json(tag, record['profile']) if record else None

    def display(self, tag):
        record = self.record(tag)
        return primary_address(record['profile']) if record else (SOCKS_HOST, 0)

    def allocate_port(self, used):
        for port in self.ports:
            if port not in used and self.port_free(port):
                return port
        raise ValueError(f'Нет свободных портов для клиентов TrustTunnel ({self.ports[0]}-{self.ports[-1]}).')

    def pending_tags(self):
        return set(self.pending)

    def stage(self, tag, profile):
        if not TAG_PATTERN.fullmatch(tag):
            raise ValueError('Tag содержит недопустимые символы.')
        if not self.available():
            raise ValueError(CLIENT_MISSING)
        registry = self.load()
        used = {item['port'] for name, item in registry['servers'].items() if name != tag}
        used |= {item['port'] for name, item in self.pending.items() if name != tag}
        current = self.pending.get(tag) or registry['servers'].get(tag)
        self.pending[tag] = {'port': current['port'] if current else self.allocate_port(used), 'profile': profile}
        return self.outbound({'salt': registry['salt'], 'servers': {**registry['servers'], **self.pending}}, tag)

    def discard(self, tags):
        for tag in tags:
            self.pending.pop(tag, None)

    def group_id(self):
        if not self.group or not hasattr(os, 'chown'):
            return None
        try:
            import grp
            return grp.getgrnam(self.group).gr_gid
        except (ImportError, KeyError):
            return None

    def ensure_directory(self):
        self.client_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.client_dir, 0o750)
        group = self.group_id()
        if group is not None:
            os.chown(self.client_dir, 0, group)

    def install_bytes(self, path, data):
        self.ensure_directory()
        descriptor, temporary = tempfile.mkstemp(prefix='.focusvpn-tt-', dir=self.client_dir)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            group = self.group_id()
            if group is not None:
                os.chown(temporary, 0, group)
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def config_path(self, tag):
        return self.client_dir / (tag + '.toml')

    def read_config(self, tag):
        try:
            return self.config_path(tag).read_bytes()
        except FileNotFoundError:
            return None

    def unit_state(self, tag, action='is-active'):
        try:
            return self.run([SYSTEMCTL_BIN, action, UNIT_TEMPLATE.format(tag)], 15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 'unknown'

    def start_client(self, tag, registry):
        record = registry['servers'][tag]
        data = render_client_toml(record['profile'], record['port'], SOCKS_USER, self.socks_password(registry, tag, record['profile'])).encode('utf-8')
        changed = self.read_config(tag) != data
        if changed:
            self.install_bytes(self.config_path(tag), data)
        unit = UNIT_TEMPLATE.format(tag)
        if self.run([SYSTEMCTL_BIN, 'enable', unit], 30).returncode != 0:
            raise RuntimeError('Не удалось включить автозапуск клиента TrustTunnel.')
        if changed or self.unit_state(tag) != 'active':
            if self.run([SYSTEMCTL_BIN, 'restart', unit], 45).returncode != 0:
                raise RuntimeError('Клиент TrustTunnel не запустился.')
            if not self.wait_listener(record['port']):
                self.run([SYSTEMCTL_BIN, 'stop', unit], 30)
                raise RuntimeError('Клиент TrustTunnel не начал слушать локальный порт; проверьте конфигурацию.')

    def remove_client(self, tag):
        try:
            self.run([SYSTEMCTL_BIN, 'disable', '--now', UNIT_TEMPLATE.format(tag)], 45)
        except (OSError, subprocess.SubprocessError):
            pass
        self.config_path(tag).unlink(missing_ok=True)

    def prepare(self):
        if not self.pending:
            return
        registry = self.load()
        self.snapshot = {
            'existed': self.path.exists(), 'registry': json.loads(json.dumps(registry)),
            'files': {tag: self.read_config(tag) for tag in self.pending},
            'active': {tag: self.unit_state(tag) == 'active' for tag in self.pending},
        }
        merged = {'salt': registry['salt'], 'servers': {**registry['servers'], **self.pending}}
        try:
            for tag in self.pending:
                self.start_client(tag, merged)
            self.save(merged)
        except Exception:
            self.rollback()
            raise
        self.pending = {}

    def rollback(self):
        snapshot, self.snapshot = self.snapshot, None
        self.pending = {}
        if snapshot is None:
            return
        for tag, data in snapshot['files'].items():
            try:
                if data is None:
                    self.remove_client(tag)
                else:
                    self.install_bytes(self.config_path(tag), data)
                    self.run([SYSTEMCTL_BIN, 'restart' if snapshot['active'][tag] else 'stop', UNIT_TEMPLATE.format(tag)], 45)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue
        try:
            if snapshot['existed']:
                self.save(snapshot['registry'])
            else:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def finalize(self, config):
        self.snapshot = None
        self.remove_orphans(config)

    def remove_orphans(self, config):
        registry = self.load()
        present = {item.get('tag') for item in config.get('outbounds', []) if item.get('type') == 'socks'}
        orphans = [tag for tag in registry['servers'] if tag not in present]
        for tag in orphans:
            self.remove_client(tag)
        if orphans:
            self.save({'salt': registry['salt'], 'servers': {tag: item for tag, item in registry['servers'].items() if tag in present}})
        return orphans

    def reconcile(self, config):
        try:
            self.remove_orphans(config)
            registry = self.load()
            if registry['servers'] and not self.available():
                raise RuntimeError(CLIENT_MISSING)
            for tag in registry['servers']:
                self.start_client(tag, registry)
            self.error = ''
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            self.error = str(error)
