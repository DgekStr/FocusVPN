#!/usr/bin/env python3
import ipaddress
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

SOURCE_URL = 'https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone'
ZONE_PATH = Path('/etc/sing-box/ru.zone')
GATEWAY_SCRIPT = '/usr/local/libexec/sing-box-gateway'
MAX_BYTES = 5 * 1024 * 1024
MIN_NETWORKS = 1000
MAX_NETWORKS = 100000


def parse_zone(payload):
    networks = []
    for line_number, raw_line in enumerate(payload.decode('ascii').splitlines(), 1):
        value = raw_line.strip()
        if not value or value.startswith('#'):
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as error:
            raise RuntimeError(f'invalid ru.zone CIDR at line {line_number}') from error
        if network.version != 4:
            raise RuntimeError('ru.zone contains a non-IPv4 network')
        networks.append(network)
    collapsed = list(ipaddress.collapse_addresses(networks))
    if not MIN_NETWORKS <= len(collapsed) <= MAX_NETWORKS:
        raise RuntimeError(f'unexpected ru.zone network count: {len(collapsed)}')
    return collapsed


def atomic_write(path, payload):
    descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o640)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def run_gateway():
    result = subprocess.run([GATEWAY_SCRIPT, 'start'], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(result.stdout.strip() or 'gateway reload failed')


def main():
    request = urllib.request.Request(SOURCE_URL, headers={'User-Agent': 'sing-box-ru-zone-updater/1.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise RuntimeError('ru.zone is unexpectedly large')
    networks = parse_zone(payload)
    old_zone = ZONE_PATH.read_bytes() if ZONE_PATH.exists() else None
    if old_zone == payload:
        print(f'ru.zone unchanged: {len(networks)} networks')
        return
    if old_zone is not None:
        shutil.copy2(ZONE_PATH, ZONE_PATH.with_suffix('.zone.previous'))
    atomic_write(ZONE_PATH, payload)
    try:
        run_gateway()
    except Exception:
        if old_zone is not None:
            atomic_write(ZONE_PATH, old_zone)
            run_gateway()
        raise
    print(f'ru.zone updated: {len(networks)} networks')


if __name__ == '__main__':
    main()
