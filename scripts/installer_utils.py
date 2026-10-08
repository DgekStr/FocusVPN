import ipaddress
import json
import os
import re
import sys
import tempfile
from pathlib import Path


SHA256_DIGEST = re.compile(r'^sha256:([0-9a-f]{64})$')


def configure_proxy_environment(path, server_ip):
    server_ip = str(ipaddress.IPv4Address(server_ip))
    path = Path(path)
    subscription_origin = f'https://{server_ip}:7445'
    lines = path.read_text(encoding='utf-8').splitlines()
    updated = []
    found_host = False
    found_proxy = False
    found_subscription = False
    for line in lines:
        if line.startswith('SING_BOX_ADMIN_HOST='):
            updated.append('SING_BOX_ADMIN_HOST=127.0.0.1')
            found_host = True
        elif line.startswith('FOCUSVPN_TRUSTED_PROXY_NETWORKS='):
            current = line.split('=', 1)[1].strip().strip('"\'')
            networks = [item.strip() for item in current.split(',') if item.strip()]
            if '127.0.0.1/32' not in networks:
                networks.append('127.0.0.1/32')
            updated.append('FOCUSVPN_TRUSTED_PROXY_NETWORKS=' + ','.join(networks))
            found_proxy = True
        elif line.startswith('FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL='):
            current = line.split('=', 1)[1].strip().strip('"\'')
            updated.append(line if current else 'FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL=' + subscription_origin)
            found_subscription = True
        else:
            updated.append(line)
    if not found_host:
        updated.append('SING_BOX_ADMIN_HOST=127.0.0.1')
    if not found_proxy:
        updated.append('FOCUSVPN_TRUSTED_PROXY_NETWORKS=127.0.0.1/32')
    if not found_subscription:
        updated.append('FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL=' + subscription_origin)
    path.write_text('\n'.join(updated) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)


def remove_placeholder_servers(config):
    outbounds = config.get('outbounds', [])
    profiles = [item for item in outbounds if item.get('type') == 'vless']
    expected_tags = {f'auto-{index}' for index in range(1, 9)}
    if len(profiles) != 8 or {item.get('tag') for item in profiles} != expected_tags:
        return False
    if any(item.get('server') != '<provider-host>' or item.get('uuid') != '<vless-uuid>' for item in profiles):
        return False
    selectors = [item for item in outbounds if item not in profiles]
    if len(selectors) != 1 or selectors[0].get('type') != 'urltest' or selectors[0].get('tag') != 'vless-auto':
        return False
    if set(selectors[0].get('outbounds', [])) != expected_tags:
        return False
    if config.get('route', {}).get('final') not in expected_tags | {'vless-auto'}:
        return False
    config['outbounds'] = [{'type': 'direct', 'tag': 'direct'}]
    config['route']['final'] = 'direct'
    return True


def migrate_placeholder_file(path, backup_directory):
    path = Path(path)
    original = path.read_bytes()
    config = json.loads(original)
    if not remove_placeholder_servers(config):
        return False
    backup_directory = Path(backup_directory)
    backup_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, backup = tempfile.mkstemp(prefix='empty-vless-', suffix='.json', dir=backup_directory)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(original)
    os.chmod(backup, 0o600)
    previous = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.focusvpn-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(config, handle, indent=2)
            handle.write('\n')
        os.chmod(temporary, previous.st_mode & 0o777)
        if hasattr(os, 'chown'):
            os.chown(temporary, previous.st_uid, previous.st_gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Removed eight unused template profiles; original configuration backed up')
    return True


def release_asset_sha256(release, asset_name):
    if not isinstance(release, dict) or not isinstance(release.get('assets'), list):
        raise ValueError('Release metadata does not contain an assets list.')
    asset = next((item for item in release['assets'] if isinstance(item, dict) and item.get('name') == asset_name), None)
    if asset is None:
        raise ValueError(f'Release metadata does not contain asset {asset_name}.')
    match = SHA256_DIGEST.fullmatch(str(asset.get('digest') or ''))
    if match is None:
        raise ValueError(f'Release metadata does not contain a valid SHA-256 digest for {asset_name}.')
    return match.group(1)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == 'remove-placeholders':
        migrate_placeholder_file(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) == 4 and sys.argv[1] == 'configure-proxy':
        configure_proxy_environment(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) != 3:
        raise SystemExit('Usage: installer_utils.py RELEASE_JSON ASSET_NAME')
    release = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(release_asset_sha256(release, sys.argv[2]))


if __name__ == '__main__':
    main()
