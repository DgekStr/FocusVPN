#!/usr/bin/env python3
import ipaddress
import subprocess
from pathlib import Path

ZONE_PATH = Path('/etc/sing-box/ru.zone')
NFT = '/usr/sbin/nft'
CHUNK_SIZE = 250
MIN_NETWORKS = 1000
MAX_NETWORKS = 100000


def networks_from_zone():
    networks = []
    for line_number, raw_line in enumerate(ZONE_PATH.read_text(encoding='ascii').splitlines(), 1):
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


def run(args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError(result.stdout.strip() or 'nft command failed')


def main():
    networks = networks_from_zone()
    run([NFT, 'flush', 'set', 'inet', 'singbox_vless', 'ru_direct'])
    for offset in range(0, len(networks), CHUNK_SIZE):
        chunk = networks[offset:offset + CHUNK_SIZE]
        elements = '{ ' + ', '.join(str(network) for network in chunk) + ' }'
        run([NFT, 'add', 'element', 'inet', 'singbox_vless', 'ru_direct', elements])
    print(f'ru.zone loaded: {len(networks)} networks')


if __name__ == '__main__':
    main()
