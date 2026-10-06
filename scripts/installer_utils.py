import json
import re
import sys
from pathlib import Path


SHA256_DIGEST = re.compile(r'^sha256:([0-9a-f]{64})$')


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
    if len(sys.argv) != 3:
        raise SystemExit('Usage: installer_utils.py RELEASE_JSON ASSET_NAME')
    release = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(release_asset_sha256(release, sys.argv[2]))


if __name__ == '__main__':
    main()
