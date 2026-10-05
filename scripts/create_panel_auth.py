#!/usr/bin/env python3
import base64
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path


def create_auth_file(path, prompt=None, read_secret=None):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f'{path} already exists')
    prompt = input if prompt is None else prompt
    read_secret = getpass.getpass if read_secret is None else read_secret
    username = (prompt('FocusVPN panel username [admin]: ') or 'admin').strip()
    if not username or ':' in username or any(character in username for character in '\r\n'):
        raise ValueError('Panel username must be non-empty and cannot contain colon or newline.')
    password = read_secret('FocusVPN panel password: ')
    confirmation = read_secret('Repeat panel password: ')
    if not hmac.compare_digest(password, confirmation):
        raise ValueError('Panel passwords do not match.')
    if len(password) < 12:
        raise ValueError('Panel password must contain at least 12 characters.')

    salt = secrets.token_bytes(16)
    payload = {
        'username': username,
        'salt': base64.b64encode(salt).decode('ascii'),
        'hash': base64.b64encode(hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32)).decode('ascii'),
        'realm': f'FocusVPN {secrets.token_hex(4)}',
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: create_panel_auth.py /etc/sing-box-admin/auth.json')
    try:
        create_auth_file(sys.argv[1])
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f'Panel credentials created at {sys.argv[1]} (mode 0600).')


if __name__ == '__main__':
    main()