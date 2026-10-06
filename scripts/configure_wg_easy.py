import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from wg_easy_api import WgEasyApi
from wg_easy_api import WgEasyApiError


def prepare_credentials(path):
    path = Path(path)
    secret = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    values = (secret.get('username'), secret.get('password'))
    if all(isinstance(value, str) and value.strip() and not value.startswith('<') for value in values):
        return secret
    secret = {'username': 'focusvpn-service', 'password': secrets.token_urlsafe(32)}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.wg-easy-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(secret, handle)
            handle.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return secret


def write_init_environment(secret_path, environment_path, host, network):
    secret = prepare_credentials(secret_path)
    values = {
        'INIT_ENABLED': 'true',
        'INIT_USERNAME': secret['username'],
        'INIT_PASSWORD': secret['password'],
        'INIT_HOST': host,
        'INIT_PORT': '51820',
        'INIT_IPV4_CIDR': network,
        'INIT_IPV6_CIDR': 'fdcc:ad94:bacf:61a3::/64',
    }
    if any('\n' in value or '\r' in value for value in values.values()):
        raise ValueError('Invalid wg-easy initialization value')
    descriptor = os.open(environment_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    os.chmod(environment_path, 0o600)


def verify_api(secret_path, api_factory=WgEasyApi, attempts=60, delay=1):
    for attempt in range(attempts):
        try:
            api_factory(secret_path).clients()
            return
        except WgEasyApiError as error:
            if error.status_code is None or error.status_code < 500 or attempt + 1 == attempts:
                raise
            time.sleep(delay)
    raise WgEasyApiError('wg-easy API did not become ready in time.')


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == 'prepare':
        write_init_environment(*sys.argv[2:])
    elif len(sys.argv) == 3 and sys.argv[1] == 'verify':
        verify_api(sys.argv[2])
        print('wg-easy API authorization verified')
    else:
        raise SystemExit('Usage: configure_wg_easy.py prepare SECRET ENV HOST NETWORK | verify SECRET')