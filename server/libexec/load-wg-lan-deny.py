#!/usr/bin/env python3
import ipaddress
import json
import os
import subprocess
from pathlib import Path

STATE_PATH = Path('/etc/sing-box-admin/wg-lan-deny.json')
NFT = '/usr/sbin/nft'
TABLE = 'wg_easy_private_ui'
WG_INTERFACE = os.environ.get('FOCUSVPN_WG_INTERFACE', 'wg0')
LAN_NETWORK = ipaddress.ip_network(os.environ.get('FOCUSVPN_LAN_NETWORK', '192.168.0.0/24'), strict=False)


def run(args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30, check=False)
    if result.returncode:
        raise SystemExit(result.stdout.strip() or 'nft command failed')


def denied_addresses():
    try:
        values = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    addresses = []
    for value in values if isinstance(values, list) else []:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4:
            addresses.append(str(address))
    return sorted(set(addresses), key=ipaddress.ip_address)


def main():
    run([NFT, 'flush', 'chain', 'inet', TABLE, 'wg_lan_deny'])
    for address in denied_addresses():
        run([
            NFT, 'add', 'rule', 'inet', TABLE, 'wg_lan_deny',
            'iifname', WG_INTERFACE, 'ip', 'saddr', address,
            'ip', 'daddr', str(LAN_NETWORK), 'counter', 'drop',
        ])
    print(f'LAN deny rules loaded: {len(denied_addresses())} clients')


if __name__ == '__main__':
    main()
