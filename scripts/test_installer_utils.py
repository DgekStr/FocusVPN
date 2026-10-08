import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer_utils import release_asset_sha256, remove_placeholder_servers, migrate_placeholder_file


class InstallerUtilsTests(unittest.TestCase):
    def placeholder_config(self):
        tags = [f'auto-{index}' for index in range(1, 9)]
        return {'outbounds': [{'type': 'vless', 'tag': tag, 'server': '<provider-host>', 'uuid': '<vless-uuid>'} for tag in tags] + [{'type': 'urltest', 'tag': 'vless-auto', 'outbounds': tags}], 'route': {'final': 'auto-4'}}

    def test_new_install_has_no_empty_vless_profiles(self):
        path = Path(__file__).resolve().parents[1] / 'server' / 'config' / 'sing-box' / 'config.example.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(config['outbounds'], [{'type': 'direct', 'tag': 'direct'}])
        self.assertEqual(config['route']['final'], 'direct')

    def test_upgrade_cleans_only_complete_placeholder_set(self):
        config = self.placeholder_config()
        self.assertTrue(remove_placeholder_servers(config))
        self.assertEqual(config['outbounds'], [{'type': 'direct', 'tag': 'direct'}])
        self.assertEqual(config['route']['final'], 'direct')
        self.assertFalse(remove_placeholder_servers(config))

    def test_upgrade_keeps_config_if_any_profile_is_real(self):
        config = self.placeholder_config()
        config['outbounds'][0]['server'] = 'vpn.example.com'
        before = json.dumps(config)
        self.assertFalse(remove_placeholder_servers(config))
        self.assertEqual(json.dumps(config), before)

    def test_placeholder_file_migration_preserves_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            original = json.dumps(self.placeholder_config()).encode('utf-8')
            path.write_bytes(original)
            backups = Path(directory) / 'backups'
            self.assertTrue(migrate_placeholder_file(path, backups))
            self.assertEqual(next(backups.iterdir()).read_bytes(), original)
            self.assertFalse(migrate_placeholder_file(path, backups))

    def test_install_flow_confirms_before_changes_and_starts_panel_and_wg_easy(self):
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        main = installer.rsplit('\nmain() {', 1)[1]
        self.assertLess(main.index('validate_runtime_bundle'), main.index('confirm_install_plan'))
        self.assertLess(main.index('confirm_install_plan'), main.index('check_legacy_wireguard'))
        self.assertLess(main.index('check_legacy_wireguard'), main.index('install_packages'))
        self.assertIn('configure_nginx_proxy', main)
        self.assertIn('create_wg_easy_container', main)
        self.assertIn('systemctl enable --now sing-box-admin.service nginx.service', main)
        self.assertIn('https://$server_ip:7445', main)
        self.assertIn("grep -q '^INIT_PASSWORD='", installer)
        self.assertIn('removing one-time wg-easy initialization credentials', installer)
        self.assertIn('install -d -m 0750 -o root -g sing-box "$SING_BOX_ROOT" "$HAPP_ROOT"', installer)
        self.assertIn('python3 -m compileall -q "$APP_ROOT"', installer)

    def test_server_ipv4_detection_without_default_route_source(self):
        bash = str(Path('C:/Program Files/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is required for installer IPv4 validation')
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        commands = '''set -euo pipefail
ip() {
  case "$*" in
    "-4 route show default") printf '%s\\n' 'default via 192.168.56.1 dev enp0s3 proto static' ;;
    "-4 route get 1.1.1.1") printf '%s\\n' '1.1.1.1 via 192.168.56.1 dev enp0s3 src 192.168.56.10 uid 0' '    cache' ;;
    *) return 1 ;;
  esac
}
'''
        for name in ('configure_nginx_proxy', 'create_wg_easy_container', 'main'):
            with self.subTest(function=name):
                body = installer.split(name + '() {', 1)[1].split('\n}\n', 1)[0]
                assignment = next(line.strip() for line in body.splitlines() if line.strip().startswith('server_ip='))
                script = commands + assignment + '\nprintf "%s\\n" "$server_ip"\n'
                result = subprocess.run([bash], input=script, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), '192.168.56.10')

    def test_installer_configures_external_subscription_origin_without_overwriting_custom_url(self):
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        configure = installer.split('configure_nginx_proxy() {', 1)[1].split('\ncreate_wg_easy_container() {', 1)[0]
        self.assertIn('python3 "$REPO_ROOT/scripts/installer_utils.py" configure-proxy "$CONFIG_ROOT/focusvpn.env" "$server_ip"', configure)
        utility = Path(__file__).resolve().parent / 'installer_utils.py'
        automatic = 'FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL=https://192.0.2.5:7445'
        custom = 'FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL="https://vpn.example.com:8443/vpn/"'
        for configured, expected in (('', automatic), ('FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL=""\n', automatic), (custom + '\n', custom)):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'focusvpn.env'
                path.write_text('SING_BOX_ADMIN_HOST=0.0.0.0\nFOCUSVPN_TRUSTED_PROXY_NETWORKS=192.0.2.1/32\nUNRELATED=value\n' + configured, encoding='utf-8')
                result = subprocess.run([sys.executable, str(utility), 'configure-proxy', str(path), '192.0.2.5'], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = path.read_text(encoding='utf-8').splitlines()
                self.assertIn(expected, lines)
                self.assertEqual(sum(line.startswith('FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL=') for line in lines), 1)
                self.assertIn('SING_BOX_ADMIN_HOST=127.0.0.1', lines)
                self.assertIn('FOCUSVPN_TRUSTED_PROXY_NETWORKS=192.0.2.1/32,127.0.0.1/32', lines)
                self.assertIn('UNRELATED=value', lines)
                if os.name == 'posix':
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_runtime_bundle_preflight_rejects_missing_files(self):
        bash = str(Path('C:/Program Files/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is required for installer preflight validation')
        root = Path(__file__).resolve().parents[1]
        installer = (root / 'scripts' / 'install.sh').read_text(encoding='utf-8')
        function = 'validate_runtime_bundle() {' + installer.split('validate_runtime_bundle() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        script = 'fail() { printf "%s\\n" "$*" >&2; exit 1; }\n' + function + '\nvalidate_runtime_bundle\n'
        complete = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'REPO_ROOT': root.as_posix()})
        self.assertEqual(complete.returncode, 0, complete.stderr)
        with tempfile.TemporaryDirectory() as directory:
            incomplete = Path(directory)
            for source in ('VERSION', 'server/panel'):
                destination = incomplete / source
                if (root / source).is_dir():
                    shutil.copytree(root / source, destination, ignore=shutil.ignore_patterns('__pycache__'))
                else:
                    shutil.copyfile(root / source, destination)
            for missing in ('VERSION', 'server/panel/happ_server.py', 'server/panel/static/panel.css', 'server/panel/static/happ-actions.js', 'server/panel/static/chart.js', 'server/panel/static/chart.LICENSE.txt'):
                with self.subTest(missing=missing):
                    path = incomplete / missing
                    original = path.read_bytes()
                    path.unlink()
                    result = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'REPO_ROOT': incomplete.as_posix()})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('runtime bundle is incomplete: ' + missing, result.stderr)
                    path.write_bytes(original)

    def test_legacy_wireguard_configs_are_backed_up_only_after_confirmation(self):
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        legacy_check = installer.split('check_legacy_wireguard() {', 1)[1].split('\n}\n', 1)[0]
        self.assertIn('read -r -p "Move old WireGuard data to backup and continue? [y/N] "', legacy_check)
        self.assertIn('installation cancelled; existing WireGuard installation was not changed', legacy_check)
        self.assertIn('cp -a /etc/wireguard/wg-client.conf', legacy_check)
        self.assertIn('docker cp wg-easy:/etc/wireguard/.', legacy_check)

    def test_default_happ_template_uses_matching_valid_vip_uuid(self):
        config_root = Path(__file__).resolve().parents[1] / 'server' / 'config'
        public_state = json.loads((config_root / 'sing-box-admin' / 'happ-server.example.json').read_text(encoding='utf-8'))
        happ_config = json.loads((config_root / 'sing-box-happ-server' / 'config.example.json').read_text(encoding='utf-8'))
        vip_uuid = str(uuid.UUID(public_state['uuid']))
        inbound = next(item for item in happ_config['inbounds'] if item.get('type') == 'vless')
        self.assertEqual(inbound['users'][0]['uuid'], vip_uuid)
        self.assertEqual(urlsplit(public_state['link']).username, vip_uuid)

    def test_release_digest_is_selected_by_exact_asset_name(self):
        expected = 'a684484d7477d1437282ee411f4d131d0340aaad60a7868841ebd5d87dd8a0c6'
        release = {'assets': [
            {'name': 'other.tar.gz', 'digest': 'sha256:' + '0' * 64},
            {'name': 'sing-box-1.14.2-linux-amd64.tar.gz', 'digest': 'sha256:' + expected},
        ]}
        self.assertEqual(release_asset_sha256(release, 'sing-box-1.14.2-linux-amd64.tar.gz'), expected)

    def test_missing_or_invalid_release_digest_fails_closed(self):
        for release, asset_name in (
            ({'assets': []}, 'sing-box.tar.gz'),
            ({'assets': [{'name': 'sing-box.tar.gz'}]}, 'sing-box.tar.gz'),
            ({'assets': [{'name': 'sing-box.tar.gz', 'digest': 'md5:' + '0' * 32}]}, 'sing-box.tar.gz'),
            ({'assets': [{'name': 'sing-box.tar.gz', 'digest': 'sha256:' + 'A' * 64}]}, 'sing-box.tar.gz'),
            ({}, 'sing-box.tar.gz'),
        ):
            with self.subTest(release=release), self.assertRaises(ValueError):
                release_asset_sha256(release, asset_name)


if __name__ == '__main__':
    unittest.main()