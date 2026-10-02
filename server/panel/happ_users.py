import datetime as dt
import hashlib
import hmac
import json
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from vless_monitor import write_private_json


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def parse_expiry(value):
    value = str(value or '').strip()
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Укажите срок в формате даты/времени UTC.') from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def user_status(user, at=None):
    expiry = parse_expiry(user.get('expires_at'))
    if expiry is not None and expiry <= (at or now_utc()):
        return 'expired'
    return 'enabled' if user.get('enabled') else 'disabled'


def user_link(vip_link, user):
    parsed = urlsplit(vip_link)
    if parsed.scheme != 'vless' or not parsed.hostname or not parsed.port:
        raise ValueError('VIP-ссылка имеет неверный формат.')
    host = parsed.hostname
    if ':' in host:
        host = f'[{host}]'
    return urlunsplit(('vless', f'{uuid.UUID(user["uuid"])}@{host}:{parsed.port}', parsed.path, parsed.query, quote(user['name'], safe='')))


def build_user_config(config, registry, vip, at=None):
    candidate = json.loads(json.dumps(config))
    inbound = next((item for item in candidate.get('inbounds', []) if item.get('tag') == vip['inbound_tag'] and item.get('type') == 'vless'), None)
    if inbound is None:
        raise ValueError('VIP inbound не найден; применение отменено.')
    protected = json.loads(json.dumps(vip['users']))
    flow = next((item.get('flow', '') for item in protected), '')
    credentials = list(protected)
    existing = {str(item.get('uuid')).lower() for item in protected}
    for user in registry['users']:
        credential = str(uuid.UUID(user['uuid']))
        if credential in existing:
            raise ValueError('UUID персонального пользователя совпадает с VIP или другим пользователем.')
        existing.add(credential)
        if user_status(user, at) == 'enabled':
            record = {'name': 'personal-' + user['id'], 'uuid': credential}
            if flow:
                record['flow'] = flow
            credentials.append(record)
    inbound['users'] = credentials
    return candidate


class HappUsers:
    def __init__(self, directory, config_path, public_state_path, apply_config):
        self.directory = Path(directory)
        self.config_path = Path(config_path)
        self.public_state_path = Path(public_state_path)
        self.apply_config = apply_config
        self.registry_path = self.directory / 'happ-users.json'
        self.vip_path = self.directory / 'happ-vip.json'
        self.subscription_key_path = self.directory / 'happ-subscription-key.json'
        self.subscription_key = None

    def initialize(self):
        if not self.vip_path.exists():
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
            state = json.loads(self.public_state_path.read_text(encoding='utf-8'))
            link = state.get('link', '')
            parsed = urlsplit(link)
            if parsed.scheme != 'vless' or not parsed.username:
                raise ValueError('Не удалось сохранить существующую VIP-ссылку.')
            vip_uuid = str(uuid.UUID(parsed.username))
            inbound = next((item for item in config.get('inbounds', []) if item.get('type') == 'vless' and any(str(user.get('uuid')).lower() == vip_uuid for user in item.get('users', []))), None)
            if inbound is None:
                raise ValueError('UUID VIP-ссылки не совпадает с существующим inbound.')
            invariant_fields = {key: inbound.get(key) for key in ('listen', 'listen_port', 'tls', 'transport')}
            write_private_json(self.vip_path, {'link': link, 'inbound_tag': inbound['tag'], 'users': inbound['users'], 'inbound_fields': invariant_fields})
        if not self.registry_path.exists():
            write_private_json(self.registry_path, {'users': []})
        if not self.subscription_key_path.exists():
            write_private_json(self.subscription_key_path, {'key': secrets.token_hex(32)})
        self.subscription_key = bytes.fromhex(json.loads(self.subscription_key_path.read_text(encoding='utf-8'))['key'])
        if len(self.subscription_key) != 32:
            raise ValueError('Ключ подписок HAPP повреждён.')

    def subscription_token(self, user):
        payload = ('FocusVPN-HAPP:' + user['id'] + ':' + str(uuid.UUID(user['uuid']))).encode('utf-8')
        return hmac.new(self.subscription_key, payload, hashlib.sha256).hexdigest()

    def subscription_urls(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Некорректный адрес подписок HAPP.')
        vip = {'id': 'VIP', 'uuid': urlsplit(self.vip()['link']).username}
        return {user['id']: base_url.rstrip('/') + '/happ-subscription/' + self.subscription_token(user) for user in [vip, *self.registry()['users']]}

    def subscription_user(self, token):
        if len(token) != 64 or any(character not in '0123456789abcdef' for character in token):
            return None
        vip_link = self.vip()['link']
        vip = {'id': 'VIP', 'uuid': urlsplit(vip_link).username, 'name': 'VIP', 'enabled': True, 'link': vip_link}
        for user in [vip, *self.registry()['users']]:
            if hmac.compare_digest(self.subscription_token(user), token):
                if user_status(user) != 'enabled':
                    return None
                return {**user, 'status': 'enabled', 'link': user_link(vip_link, user) if user['id'] != 'VIP' else vip_link}
        return None

    def vip(self):
        return json.loads(self.vip_path.read_text(encoding='utf-8'))

    def registry(self):
        payload = json.loads(self.registry_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or not isinstance(payload.get('users'), list):
            raise ValueError('Реестр пользователей HAPP повреждён.')
        return payload

    def require_vip(self, config):
        vip = self.vip()
        inbound = next((item for item in config.get('inbounds', []) if item.get('tag') == vip['inbound_tag'] and item.get('type') == 'vless'), None)
        if inbound is None:
            raise ValueError('Нельзя удалить существующий VIP inbound.')
        for key, value in vip.get('inbound_fields', {}).items():
            if inbound.get(key) != value:
                raise ValueError('Нельзя изменить listener, TLS или транспорт сохранённого VIP-входа.')
        actual = {str(item.get('uuid')).lower(): item for item in inbound.get('users', [])}
        for protected in vip['users']:
            record = actual.get(str(protected['uuid']).lower())
            if record is None or record.get('flow', '') != protected.get('flow', ''):
                raise ValueError('Нельзя удалить или изменить VIP credentials.')

    def audit(self, operation, user):
        path = self.directory / 'happ-user-events.json'
        try:
            events = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            events = []
        events = events[-499:] + [{'at': now_utc().isoformat(), 'operation': operation, 'id': user['id'], 'name': user['name']}]
        write_private_json(path, events)

    def events(self):
        try:
            events = json.loads((self.directory / 'happ-user-events.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        return list(reversed(events[-100:]))

    def commit(self, registry):
        original = self.registry()
        config = json.loads(self.config_path.read_text(encoding='utf-8'))
        candidate = build_user_config(config, registry, self.vip())
        self.require_vip(candidate)
        write_private_json(self.registry_path, registry)
        try:
            self.apply_config(json.dumps(candidate, ensure_ascii=False))
        except Exception:
            write_private_json(self.registry_path, original)
            raise

    def validate_name_and_expiry(self, name, expires_at):
        name = str(name).strip()
        if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
            raise ValueError('Имя должно содержать от 1 до 80 символов без управляющих символов.')
        expiry = parse_expiry(expires_at)
        if expiry is not None and expiry <= now_utc():
            raise ValueError('Срок доступа должен быть в будущем; пустое поле означает бессрочный доступ.')
        return name, expiry.isoformat() if expiry else None

    def create(self, name, expires_at=''):
        name, expiry = self.validate_name_and_expiry(name, expires_at)
        registry = self.registry()
        if len(registry['users']) >= 200:
            raise ValueError('Достигнут предел 200 персональных пользователей.')
        if any(item['name'].casefold() == name.casefold() for item in registry['users']):
            raise ValueError('Пользователь с таким именем уже существует.')
        user = {'id': str(uuid.uuid4()), 'uuid': str(uuid.uuid4()), 'name': name, 'enabled': True, 'expires_at': expiry, 'created_at': now_utc().isoformat()}
        registry['users'].append(user)
        self.commit(registry)
        self.audit('create', user)
        return user['id']

    def action(self, user_id, operation, name='', expires_at=''):
        registry = self.registry()
        user = next((item for item in registry['users'] if item['id'] == user_id), None)
        if user is None:
            raise ValueError('Персональный пользователь не найден; VIP недоступен для этих операций.')
        if operation == 'delete':
            registry['users'] = [item for item in registry['users'] if item['id'] != user_id]
        elif operation == 'enable':
            if user_status({**user, 'enabled': True}) == 'expired':
                raise ValueError('Сначала обновите срок доступа пользователя.')
            user['enabled'] = True
        elif operation == 'disable':
            user['enabled'] = False
        elif operation == 'update':
            name, expiry = self.validate_name_and_expiry(name, expires_at)
            if any(item['id'] != user_id and item['name'].casefold() == name.casefold() for item in registry['users']):
                raise ValueError('Пользователь с таким именем уже существует.')
            user.update(name=name, expires_at=expiry)
        else:
            raise ValueError('Неизвестная операция пользователя HAPP.')
        self.commit(registry)
        self.audit(operation, user)

    def reconcile_expired(self):
        config = json.loads(self.config_path.read_text(encoding='utf-8'))
        candidate = build_user_config(config, self.registry(), self.vip())
        if candidate != config:
            self.apply_config(json.dumps(candidate, ensure_ascii=False))
            active = {str(user.get('uuid')).lower() for inbound in config.get('inbounds', []) if inbound.get('tag') == self.vip()['inbound_tag'] for user in inbound.get('users', [])}
            for user in self.registry()['users']:
                if user_status(user) == 'expired' and user['uuid'] in active:
                    self.audit('expired', user)
            return True
        return False

    def users(self):
        vip_link = self.vip()['link']
        return [{**item, 'status': user_status(item), 'link': user_link(vip_link, item)} for item in self.registry()['users']]