"""Trojan, Hysteria2 and TrustTunnel access for HAPP personal users: sing-box inbounds, TrustTunnel endpoint files and client links."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from vless_monitor import write_private_json

TROJAN_TAG = 'happ-trojan-in'
HYSTERIA2_TAG = 'happ-hysteria2-in'
BRIDGE_TAG = 'happ-tt-socks-in'
BRIDGE_HOST = '127.0.0.1'
BRIDGE_PORT = 19448
SETTINGS_FILE = 'happ-protocols.json'
HAPP_DIR = Path('/etc/sing-box-happ-server')
TLS_DIR = HAPP_DIR / 'tls'
TRUSTTUNNEL_DIR = HAPP_DIR / 'trusttunnel'
TRUSTTUNNEL_BIN = Path('/opt/trusttunnel/trusttunnel_endpoint')
TRUSTTUNNEL_UNIT = 'trusttunnel.service'
SYSTEMCTL_BIN = '/usr/bin/systemctl'
OPENSSL_BIN = '/usr/bin/openssl'
CERTIFICATE_NAME = 'focusvpn-protocols'
RESERVED_PORTS = frozenset({22, 80, 9090, 9443, 9445, 12345, 51820, 51821, BRIDGE_PORT})
HOSTNAME_PATTERN = re.compile(r'(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?\Z')
PATH_PATTERN = re.compile(r'/[A-Za-z0-9_./@+-]{1,250}\Z')
SAFE_VALUE = re.compile(r'[A-Za-z0-9_.@:/+-]{1,300}\Z')
PEM_CERTIFICATE = re.compile(r'-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----', re.S)
TRUSTTUNNEL_VPN_TEMPLATE = '''listen_address = "0.0.0.0:{port}"
ipv6_available = false
allow_private_network_connections = false
tls_handshake_timeout_secs = 10
client_listener_timeout_secs = 600
connection_establishment_timeout_secs = 30
tcp_connections_timeout_secs = 604800
udp_connections_timeout_secs = 300
credentials_file = {credentials}

[listen_protocols]

[listen_protocols.http1]
upload_buffer_size = 32768

[listen_protocols.http2]
initial_connection_window_size = 8388608
initial_stream_window_size = 131072
max_concurrent_streams = 1000
max_frame_size = 16384
header_table_size = 65536

[listen_protocols.quic]
recv_udp_payload_size = 1350
send_udp_payload_size = 1350
initial_max_data = 104857600
initial_max_stream_data_bidi_local = 1048576
initial_max_stream_data_bidi_remote = 1048576
initial_max_stream_data_uni = 1048576
initial_max_streams_bidi = 4096
initial_max_streams_uni = 4096
max_connection_window = 25165824
max_stream_window = 16777216
disable_active_migration = true
enable_early_data = true
message_queue_capacity = 4096

[forward_protocol.socks5]
address = "{bridge_host}:{bridge_port}"
extended_auth = false
'''


PROTOCOL_LABELS = (('trojan', 'Trojan'), ('hysteria2', 'Hysteria2'), ('trusttunnel', 'TrustTunnel'))
CREDENTIAL_KEYS = ('trojan_password', 'tt_password', 'hy2_password')


def default_settings():
    return {
        'trojan': {'enabled': False, 'port': 9446},
        'hysteria2': {'enabled': False, 'port': 9448},
        'trusttunnel': {'enabled': False, 'port': 9447},
        'public_host': '',
        'server_name': '',
        'cert_path': '',
        'key_path': '',
        'generated_for': '',
    }


def validate_host(value, label):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not HOSTNAME_PATTERN.fullmatch(value):
        raise ValueError(f'{label}: укажите IP-адрес или имя хоста.')
    return value.rstrip('.').lower()


def validate_port(value, label, reserved=()):
    try:
        port = int(str(value).strip())
    except ValueError as error:
        raise ValueError(f'{label}: порт должен быть числом.') from error
    if not 1024 <= port <= 65535:
        raise ValueError(f'{label}: порт должен быть в диапазоне 1024-65535 (службы работают без привилегии для низких портов; перенаправьте внешний 443 на этот порт).')
    if port in RESERVED_PORTS or port in reserved:
        raise ValueError(f'{label}: порт {port} занят служебной функцией сервера.')
    return port


def normalize_settings(raw, reserved=()):
    base = default_settings()
    raw = raw if isinstance(raw, dict) else {}
    settings = {}
    for name, label in PROTOCOL_LABELS:
        item = raw.get(name) if isinstance(raw.get(name), dict) else {}
        settings[name] = {
            'enabled': bool(item.get('enabled', base[name]['enabled'])),
            'port': validate_port(item.get('port', base[name]['port']), label, reserved),
        }
    # Settings saved before Hysteria2 existed must stay loadable even if Trojan or TrustTunnel already use its default port.
    ports = [settings[name]['port'] for name, _ in PROTOCOL_LABELS if name != 'hysteria2' or isinstance(raw.get('hysteria2'), dict)]
    if len(set(ports)) != len(ports):
        raise ValueError('Trojan, Hysteria2 и TrustTunnel не могут использовать один порт.')
    settings['public_host'] = validate_host(raw.get('public_host'), 'Публичный адрес')
    settings['server_name'] = validate_host(raw.get('server_name'), 'Имя TLS')
    cert_path, key_path = str(raw.get('cert_path') or '').strip(), str(raw.get('key_path') or '').strip()
    if bool(cert_path) != bool(key_path):
        raise ValueError('Укажите оба пути (сертификат и ключ) или оставьте оба пустыми для самоподписанного сертификата.')
    for value in (cert_path, key_path):
        if value and not PATH_PATTERN.fullmatch(value):
            raise ValueError('Пути сертификата и ключа должны быть абсолютными и содержать только латиницу, цифры и ./_@+-.')
    settings['cert_path'], settings['key_path'] = cert_path, key_path
    settings['generated_for'] = str(raw.get('generated_for') or '') if not cert_path else ''
    return settings


def new_credentials():
    return {key: secrets.token_urlsafe(18) for key in CREDENTIAL_KEYS}


def ensure_credentials(registry):
    changed = False
    for user in registry.get('users', []):
        for key, value in new_credentials().items():
            if not isinstance(user.get(key), str) or len(user[key]) < 16:
                user[key] = value
                changed = True
    return changed


def protocol_user_name(user):
    return 'personal-' + user['id']


def certificate_der(path):
    match = PEM_CERTIFICATE.search(Path(path).read_text(encoding='ascii'))
    if match is None:
        raise ValueError('Файл сертификата не содержит PEM-сертификат.')
    return base64.b64decode(''.join(match.group(1).split()), validate=True)


def host_text(host):
    return f'[{host}]' if ':' in host else host


def trojan_link(user, host, port, server_name, pinned_sha256=None):
    query = [('security', 'tls'), ('sni', server_name), ('fp', 'chrome'), ('type', 'tcp')]
    if pinned_sha256:
        query += [('allowInsecure', '1'), ('pcs', pinned_sha256)]
    return f'trojan://{quote(user["trojan_password"], safe="")}@{host_text(host)}:{port}?{urlencode(query)}#{quote(user["name"] + " Trojan", safe="")}'


def hysteria2_link(user, host, port, server_name, pinned_sha256=None):
    query = [('sni', server_name)]
    if pinned_sha256:
        query += [('insecure', '1'), ('pinSHA256', pinned_sha256)]
    return f'hysteria2://{quote(user["hy2_password"], safe="")}@{host_text(host)}:{port}/?{urlencode(query)}#{quote(user["name"] + " Hysteria2", safe="")}'


def varint(value):
    if value < 1 << 6:
        return bytes([value])
    if value < 1 << 14:
        return (value | 0x4000).to_bytes(2, 'big')
    if value < 1 << 30:
        return (value | 0x80000000).to_bytes(4, 'big')
    return (value | 0xC000000000000000).to_bytes(8, 'big')


def tlv(tag, value):
    return varint(tag) + varint(len(value)) + value


def trusttunnel_deeplink(hostname, address, username, password, certificate=None, name=''):
    parts = [tlv(0, varint(1)), tlv(1, hostname.encode('utf-8')), tlv(5, username.encode('utf-8')), tlv(6, password.encode('utf-8')), tlv(2, address.encode('utf-8'))]
    if certificate:
        parts.append(tlv(8, certificate))
    if name:
        parts.append(tlv(12, name.encode('utf-8')))
    return 'tt://?' + base64.urlsafe_b64encode(b''.join(parts)).rstrip(b'=').decode('ascii')


def toml_string(value):
    value = str(value)
    if not SAFE_VALUE.fullmatch(value):
        raise ValueError('Значение содержит недопустимые для конфигурации TrustTunnel символы.')
    return json.dumps(value)


def render_trusttunnel(settings, users, cert_path, key_path, directory, server_name):
    credentials = ''.join(f'[[client]]\nusername = {toml_string(protocol_user_name(user))}\npassword = {toml_string(user["tt_password"])}\n\n' for user in users)
    return {
        'vpn.toml': TRUSTTUNNEL_VPN_TEMPLATE.format(
            port=int(settings['trusttunnel']['port']), credentials=toml_string((Path(directory) / 'credentials.toml').as_posix()),
            bridge_host=BRIDGE_HOST, bridge_port=BRIDGE_PORT,
        ),
        'hosts.toml': f'[[main_hosts]]\nhostname = {toml_string(server_name)}\ncert_chain_path = {toml_string(Path(cert_path).as_posix())}\nprivate_key_path = {toml_string(Path(key_path).as_posix())}\n',
        'credentials.toml': credentials,
    }


def default_run(args, timeout=30):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)


class HappProtocols:
    def __init__(self, directory, tls_dir=TLS_DIR, trusttunnel_dir=TRUSTTUNNEL_DIR, trusttunnel_bin=TRUSTTUNNEL_BIN, run=default_run, group='sing-box', fallback_host=None):
        self.path = Path(directory) / SETTINGS_FILE
        self.tls_dir = Path(tls_dir)
        self.trusttunnel_dir = Path(trusttunnel_dir)
        self.trusttunnel_bin = Path(trusttunnel_bin)
        self.run = run
        self.group = group
        self.fallback_host = fallback_host

    def settings(self):
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default_settings()
        except (OSError, ValueError) as error:
            raise ValueError('Файл настроек протоколов HAPP повреждён; исправьте или удалите happ-protocols.json.') from error
        return normalize_settings(payload)

    def save(self, settings):
        write_private_json(self.path, settings)

    def any_enabled(self, settings=None):
        settings = settings or self.settings()
        return settings['trojan']['enabled'] or settings['hysteria2']['enabled'] or settings['trusttunnel']['enabled']

    def host(self, settings):
        host = settings['public_host'] or (self.fallback_host() if callable(self.fallback_host) else self.fallback_host) or ''
        if not host:
            raise ValueError('Не удалось определить публичный адрес HAPP для ссылок.')
        return host

    def server_name(self, settings, host=None):
        return settings['server_name'] or host or self.host(settings)

    def group_id(self):
        if not self.group or not hasattr(os, 'chown'):
            return None
        try:
            import grp
            return grp.getgrnam(self.group).gr_gid
        except (ImportError, KeyError):
            return None

    def ensure_directory(self, path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o750)
        group = self.group_id()
        if group is not None:
            os.chown(path, 0, group)

    def install_bytes(self, path, data, mode, shared=True):
        path = Path(path)
        self.ensure_directory(path.parent)
        descriptor, temporary = tempfile.mkstemp(prefix='.focusvpn-protocols-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            group = self.group_id() if shared else None
            if group is not None:
                os.chown(temporary, 0, group)
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def tls_files(self, settings):
        if settings['cert_path']:
            return Path(settings['cert_path']), Path(settings['key_path']), False
        return self.tls_dir / (CERTIFICATE_NAME + '.crt'), self.tls_dir / (CERTIFICATE_NAME + '.key'), True

    def generate_certificate(self, server_name):
        self.ensure_directory(self.tls_dir)
        try:
            san = 'IP:' + str(ipaddress.ip_address(server_name))
        except ValueError:
            san = 'DNS:' + server_name
        with tempfile.TemporaryDirectory(prefix='.focusvpn-cert-', dir=self.tls_dir) as directory:
            certificate, key = Path(directory) / 'tls.crt', Path(directory) / 'tls.key'
            result = self.run([
                OPENSSL_BIN, 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes', '-days', '3650',
                '-subj', '/CN=' + server_name[:64], '-addext', 'subjectAltName=' + san, '-addext', 'basicConstraints=critical,CA:FALSE',
                '-keyout', str(key), '-out', str(certificate),
            ], 60)
            if result.returncode != 0 or not certificate.is_file() or not key.is_file():
                raise RuntimeError('Не удалось создать самоподписанный TLS-сертификат (проверьте OpenSSL).')
            self.install_bytes(self.tls_dir / (CERTIFICATE_NAME + '.crt'), certificate.read_bytes(), 0o644)
            self.install_bytes(self.tls_dir / (CERTIFICATE_NAME + '.key'), key.read_bytes(), 0o640)

    def prepare_tls(self, settings, force=False):
        settings = {**settings}
        name = self.server_name(settings)
        certificate, key, generated = self.tls_files(settings)
        if generated:
            if force or settings['generated_for'] != name or not certificate.is_file() or not key.is_file():
                self.generate_certificate(name)
            settings['generated_for'] = name
        else:
            try:
                certificate_der(certificate)
                key.read_bytes()
            except (OSError, ValueError) as error:
                raise ValueError('Сертификат или ключ по указанным путям недоступны или некорректны.') from error
            settings['generated_for'] = ''
        return settings

    def pinned_sha256(self, settings):
        certificate, _, generated = self.tls_files(settings)
        return hashlib.sha256(certificate_der(certificate)).hexdigest() if generated else None

    def credentialed(self, users):
        return [user for user in users if all(isinstance(user.get(key), str) for key in CREDENTIAL_KEYS)]

    def trojan_inbound(self, settings, users):
        certificate, key, _ = self.tls_files(settings)
        return {
            'type': 'trojan', 'tag': TROJAN_TAG, 'listen': '0.0.0.0', 'listen_port': settings['trojan']['port'],
            'users': [{'name': protocol_user_name(user), 'password': user['trojan_password']} for user in users],
            'tls': {'enabled': True, 'server_name': self.server_name(settings), 'certificate_path': str(certificate), 'key_path': str(key)},
        }

    def hysteria2_inbound(self, settings, users):
        certificate, key, _ = self.tls_files(settings)
        return {
            'type': 'hysteria2', 'tag': HYSTERIA2_TAG, 'listen': '0.0.0.0', 'listen_port': settings['hysteria2']['port'],
            'ignore_client_bandwidth': True,
            'users': [{'name': protocol_user_name(user), 'password': user['hy2_password']} for user in users],
            'tls': {'enabled': True, 'server_name': self.server_name(settings), 'certificate_path': str(certificate), 'key_path': str(key)},
        }

    def apply_to_config(self, candidate, users, settings=None):
        settings = settings or self.settings()
        users = self.credentialed(users)
        inbounds = [item for item in candidate.get('inbounds', []) if item.get('tag') not in (TROJAN_TAG, HYSTERIA2_TAG, BRIDGE_TAG)]
        if settings['trojan']['enabled'] and users:
            inbounds.append(self.trojan_inbound(settings, users))
        if settings['hysteria2']['enabled'] and users:
            inbounds.append(self.hysteria2_inbound(settings, users))
        if settings['trusttunnel']['enabled'] and users:
            inbounds.append({'type': 'socks', 'tag': BRIDGE_TAG, 'listen': BRIDGE_HOST, 'listen_port': BRIDGE_PORT})
        candidate['inbounds'] = inbounds
        return candidate

    def links(self, users, settings=None):
        settings = settings or self.settings()
        if not self.any_enabled(settings):
            return {}
        host = self.host(settings)
        name = self.server_name(settings, host)
        certificate, _, generated = self.tls_files(settings)
        result = {}
        pinned = embedded = None
        for user in self.credentialed(users):
            links = {}
            if (settings['trojan']['enabled'] or settings['hysteria2']['enabled']) and generated and pinned is None:
                pinned = hashlib.sha256(certificate_der(certificate)).hexdigest()
            if settings['trojan']['enabled']:
                links['trojan'] = trojan_link(user, host, settings['trojan']['port'], name, pinned if generated else None)
            if settings['hysteria2']['enabled']:
                links['hysteria2'] = hysteria2_link(user, host, settings['hysteria2']['port'], name, pinned if generated else None)
            if settings['trusttunnel']['enabled']:
                if generated and embedded is None:
                    embedded = certificate_der(certificate)
                links['trusttunnel'] = trusttunnel_deeplink(name, f'{host_text(host)}:{settings["trusttunnel"]["port"]}', protocol_user_name(user), user['tt_password'], embedded if generated else None, user['name'])
            result[user['id']] = links
        return result

    def subscription_links(self, user, settings=None):
        settings = settings or self.settings()
        if not self.credentialed([user]) or not (settings['trojan']['enabled'] or settings['hysteria2']['enabled']):
            return []
        host = self.host(settings)
        name = self.server_name(settings, host)
        pinned = self.pinned_sha256(settings)
        links = []
        if settings['trojan']['enabled']:
            links.append(trojan_link(user, host, settings['trojan']['port'], name, pinned))
        if settings['hysteria2']['enabled']:
            links.append(hysteria2_link(user, host, settings['hysteria2']['port'], name, pinned))
        return links

    def unit_state(self, args):
        try:
            return self.run([SYSTEMCTL_BIN, *args, TRUSTTUNNEL_UNIT], 15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 'unknown'

    def status(self, settings=None):
        settings = settings or self.settings()
        certificate, _, generated = self.tls_files(settings)
        fingerprint = ''
        try:
            fingerprint = hashlib.sha256(certificate_der(certificate)).hexdigest()
        except (OSError, ValueError):
            pass
        return {'binary': self.trusttunnel_bin.is_file(), 'service': self.unit_state(['is-active']), 'generated': generated, 'fingerprint': fingerprint}

    def render_files(self, settings, users):
        certificate, key, _ = self.tls_files(settings)
        return render_trusttunnel(settings, self.credentialed(users), certificate, key, self.trusttunnel_dir, self.server_name(settings))

    def stop_trusttunnel(self):
        credentials = self.trusttunnel_dir / 'credentials.toml'
        if not credentials.exists():
            return False
        if self.unit_state(['is-enabled']) == 'enabled' or self.unit_state(['is-active']) == 'active':
            self.run([SYSTEMCTL_BIN, 'disable', '--now', TRUSTTUNNEL_UNIT], 45)
        credentials.unlink(missing_ok=True)
        return True

    def sync_trusttunnel(self, settings, users, restart=False):
        users = self.credentialed(users)
        if not settings['trusttunnel']['enabled'] or not users:
            return self.stop_trusttunnel()
        if not self.trusttunnel_bin.is_file():
            raise RuntimeError('TrustTunnel endpoint не установлен (/opt/trusttunnel/trusttunnel_endpoint).')
        changed = False
        for name, text in self.render_files(settings, users).items():
            target = self.trusttunnel_dir / name
            data = text.encode('utf-8')
            if not target.is_file() or target.read_bytes() != data:
                self.install_bytes(target, data, 0o640)
                changed = True
        active = self.unit_state(['is-active']) == 'active'
        if changed or restart or not active:
            if self.run([SYSTEMCTL_BIN, 'enable', TRUSTTUNNEL_UNIT], 30).returncode != 0:
                raise RuntimeError('Не удалось включить автозапуск TrustTunnel (проверьте unit trusttunnel.service).')
            if self.run([SYSTEMCTL_BIN, 'restart', TRUSTTUNNEL_UNIT], 45).returncode != 0:
                raise RuntimeError('TrustTunnel не запустился.')
            for _ in range(10):
                time.sleep(0.5)
                if self.unit_state(['is-active']) == 'active':
                    break
            else:
                self.run([SYSTEMCTL_BIN, 'stop', TRUSTTUNNEL_UNIT], 30)
                raise RuntimeError('TrustTunnel не запустился с новой конфигурацией.')
            return True
        return False
