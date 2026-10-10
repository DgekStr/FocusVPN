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

    def test_wg_easy_host_modules_are_loaded_and_persisted_before_container_start(self):
        bash = str(Path('C:/Program Files/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is required for wg-easy kernel module validation')
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        function = 'prepare_wg_easy_host() {' + installer.split('prepare_wg_easy_host() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        modules = ['wireguard', 'iptable_filter', 'iptable_nat', 'ip6table_filter', 'ip6table_nat']
        for failed_module in ('', 'wireguard', 'ip6table_nat'):
            with self.subTest(failed_module=failed_module), tempfile.TemporaryDirectory() as directory:
                modules_file = Path(directory) / 'modules-load.d' / 'focusvpn-wg-easy.conf'
                script = '''set -euo pipefail
fail() { printf '%s\\n' "$*" >&2; exit 1; }
modprobe() { printf '%s\\n' "$1"; [[ "$1" != "$FAILED_MODULE" ]]; }
''' + function + '\nprepare_wg_easy_host "$MODULES_FILE"\n'
                result = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'MODULES_FILE': modules_file.as_posix(), 'FAILED_MODULE': failed_module})
                if failed_module:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout.splitlines(), modules[:modules.index(failed_module) + 1])
                    self.assertIn('required wg-easy kernel module is unavailable: ' + failed_module, result.stderr)
                    self.assertFalse(modules_file.exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.splitlines(), modules)
                    self.assertEqual(modules_file.read_text(encoding='utf-8').splitlines(), modules)
                    if os.name == 'posix':
                        self.assertEqual(modules_file.stat().st_mode & 0o777, 0o644)
        create = installer.split('create_wg_easy_container() {', 1)[1].split('\n}\n', 1)[0]
        self.assertLess(create.index('prepare_wg_easy_host'), create.index('docker inspect'))
        self.assertIn('docker.io kmod nftables', installer)

    def test_existing_unhealthy_wg_easy_is_restarted_after_host_preparation(self):
        bash = str(Path('C:/Program Files/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is required for wg-easy container recovery validation')
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        function = 'create_wg_easy_container() {' + installer.split('create_wg_easy_container() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        for health in ('healthy', 'unhealthy', ''):
            with self.subTest(health=health), tempfile.TemporaryDirectory() as directory:
                commands_file = Path(directory) / 'commands.txt'
                script = '''set -euo pipefail
prepare_wg_easy_host() { printf '%s\\n' prepare_wg_easy_host >> "$COMMANDS_FILE"; }
docker() {
  printf '%s\\n' "docker $*" >> "$COMMANDS_FILE"
  if [[ "$*" == *'.State.Health'* ]]; then printf '%s\\n' "$WG_HEALTH"; fi
}
curl() { return 0; }
python3() { printf '%s\\n' verify_api >> "$COMMANDS_FILE"; }
log() { :; }
fail() { printf '%s\\n' "$*" >&2; exit 1; }
''' + function + '\ncreate_wg_easy_container\n'
                result = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'COMMANDS_FILE': commands_file.as_posix(), 'WG_HEALTH': health, 'ADMIN_ROOT': directory, 'REPO_ROOT': directory})
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = commands_file.read_text(encoding='utf-8').splitlines()
                self.assertEqual(commands[0], 'prepare_wg_easy_host')
                self.assertEqual(commands.count('docker restart wg-easy'), int(health == 'unhealthy'))
                self.assertIn('verify_api', commands)
                self.assertFalse(any(command.startswith('docker rm ') for command in commands))
                if health == 'unhealthy':
                    self.assertLess(commands.index('docker restart wg-easy'), commands.index('verify_api'))

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

    def test_installer_opens_public_ipv4_panel_by_default_and_preserves_explicit_acl(self):
        utility = Path(__file__).resolve().parent / 'installer_utils.py'
        public = 'FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0'
        restricted = 'FOCUSVPN_ADMIN_NETWORK="192.0.2.10/32"'
        for configured, expected in (('', public), ('FOCUSVPN_ADMIN_NETWORK=\n', public), ('FOCUSVPN_ADMIN_NETWORK=""\n', public), (restricted + '\n', restricted)):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'focusvpn.env'
                path.write_text('SING_BOX_ADMIN_HOST=0.0.0.0\nUNRELATED=value\n' + configured, encoding='utf-8')
                for attempt in range(2):
                    with self.subTest(attempt=attempt):
                        result = subprocess.run([sys.executable, str(utility), 'configure-proxy', str(path), '192.0.2.5'], text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        lines = path.read_text(encoding='utf-8').splitlines()
                        self.assertIn(expected, lines)
                        self.assertEqual(sum(line.startswith('FOCUSVPN_ADMIN_NETWORK=') for line in lines), 1)
                        self.assertIn('SING_BOX_ADMIN_HOST=127.0.0.1', lines)
                        self.assertIn('FOCUSVPN_TRUSTED_PROXY_NETWORKS=127.0.0.1/32', lines)
                        self.assertIn('UNRELATED=value', lines)
                        if os.name == 'posix':
                            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_fresh_install_public_acl_and_firewall_template_are_consistent(self):
        bash = str(Path('C:/Program Files/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is required for fresh installer ACL validation')
        root = Path(__file__).resolve().parents[1]
        environment = (root / 'server/config/focusvpn/focusvpn.env.example').read_text(encoding='utf-8')
        self.assertIn('SING_BOX_ADMIN_HOST=127.0.0.1\n', environment)
        self.assertIn('FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0\n', environment)
        installer = (root / 'scripts/install.sh').read_text(encoding='utf-8')
        function = 'render_template() {' + installer.split('render_template() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        for admin_network in ('', '0.0.0.0/0', '192.0.2.10/32'):
            with self.subTest(admin_network=admin_network), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / 'wg-easy-private-ui.nft'
                script = 'set -euo pipefail\nset -a\n' + environment + '\nset +a\n' + '''python3() { "$PYTHON_EXECUTABLE" "$@"; }
FOCUSVPN_ADMIN_NETWORK="$TEST_ADMIN_NETWORK"
export FOCUSVPN_ADMIN_NETWORK
''' + function + '\nrender_template "$NFT_TEMPLATE" "$NFT_DESTINATION"\n'
                result = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'PYTHON_EXECUTABLE': Path(sys.executable).as_posix(), 'TEST_ADMIN_NETWORK': admin_network, 'NFT_TEMPLATE': (root / 'server/config/sing-box-admin/wg-easy-private-ui.nft.template').as_posix(), 'NFT_DESTINATION': destination.as_posix()})
                self.assertEqual(result.returncode, 0, result.stderr)
                rendered = destination.read_text(encoding='utf-8')
                self.assertNotIn('__FOCUSVPN_', rendered)
                self.assertIn(', ' + (admin_network or '0.0.0.0/0') + ' } counter drop', rendered)
                self.assertIn('iifname != "lo" tcp dport 51821 counter drop', rendered)
                self.assertNotIn('7445', rendered)

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
            for source in ('VERSION', 'server/panel', 'server/systemd/trusttunnel.service', 'server/systemd/trusttunnel-client@.service'):
                destination = incomplete / source
                destination.parent.mkdir(parents=True, exist_ok=True)
                if (root / source).is_dir():
                    shutil.copytree(root / source, destination, ignore=shutil.ignore_patterns('__pycache__'))
                else:
                    shutil.copyfile(root / source, destination)
            for missing in ('VERSION', 'server/panel/happ_server.py', 'server/panel/happ_protocols.py', 'server/panel/trusttunnel_upstream.py', 'server/systemd/trusttunnel.service', 'server/systemd/trusttunnel-client@.service', 'server/panel/static/panel.css', 'server/panel/static/happ-actions.js', 'server/panel/static/chart.js', 'server/panel/static/chart.LICENSE.txt'):
                with self.subTest(missing=missing):
                    path = incomplete / missing
                    original = path.read_bytes()
                    path.unlink()
                    result = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'REPO_ROOT': incomplete.as_posix()})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('runtime bundle is incomplete: ' + missing, result.stderr)
                    path.write_bytes(original)

    def test_runtime_install_copies_happ_meter_and_matching_asset_revision(self):
        bash = str(Path('C:/Program Files/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is required for runtime installation validation')
        root = Path(__file__).resolve().parents[1]
        installer = (root / 'scripts' / 'install.sh').read_text(encoding='utf-8')
        body = installer.split('install_tree() {', 1)[1].split('\n}\n', 1)[0]
        prefixes = ('find "$REPO_ROOT/server/panel"', 'find "$REPO_ROOT/server/panel/static"', 'install -m 0644 "$REPO_ROOT/VERSION"')
        commands = [line.strip() for line in body.splitlines() if line.strip().startswith(prefixes)]
        self.assertEqual(len(commands), 3)
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / 'runtime'
            script = 'set -euo pipefail\nmkdir -p "$APP_ROOT/static"\n' + '\n'.join(commands) + '\n'
            result = subprocess.run([bash], input=script, text=True, capture_output=True, env={**os.environ, 'REPO_ROOT': root.as_posix(), 'APP_ROOT': installed.as_posix()})
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('panel_ui.py', 'static/panel.css', 'static/panel.js', 'static/chart.js', 'static/chart.LICENSE.txt'):
                self.assertEqual((installed / name).read_bytes(), (root / 'server/panel' / name).read_bytes())
                if os.name == 'posix':
                    self.assertEqual((installed / name).stat().st_mode & 0o777, 0o644)
            self.assertEqual((installed / 'VERSION').read_bytes(), (root / 'VERSION').read_bytes())
            shell = (installed / 'panel_ui.py').read_text(encoding='utf-8')
            self.assertIn('/panel.css?v=2.2.0', shell)
            self.assertIn('/panel.js?v=2.2.0', shell)
            self.assertIn('repeat(25, minmax(0, 1fr))', (installed / 'static/panel.css').read_text(encoding='utf-8'))
            self.assertIn('index < 25', (installed / 'static/panel.js').read_text(encoding='utf-8'))

    def test_legacy_wireguard_configs_are_backed_up_only_after_confirmation(self):
        installer = (Path(__file__).resolve().parent / 'install.sh').read_text(encoding='utf-8')
        legacy_check = installer.split('check_legacy_wireguard() {', 1)[1].split('\n}\n', 1)[0]
        self.assertIn('read -r -p "Move old WireGuard data to backup and continue? [y/N] "', legacy_check)
        self.assertIn('installation cancelled; existing WireGuard installation was not changed', legacy_check)
        self.assertNotIn('wg-client', legacy_check)
        self.assertIn('legacy_configs+=("$answer")', legacy_check)
        self.assertIn('if (( managed_container )) && [[ "$path" == "/etc/wg-easy" ]]', legacy_check)
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

    def test_installer_pins_and_verifies_trusttunnel_without_making_it_mandatory(self):
        installer = (Path(__file__).resolve().parents[1] / 'scripts' / 'install.sh').read_text(encoding='utf-8')
        function = 'install_trusttunnel() {' + installer.split('install_trusttunnel() {', 1)[1].split('\n}\n', 1)[0]
        self.assertIn('readonly TRUSTTUNNEL_VERSION="1.1.0"', installer)
        self.assertIn('trusttunnel-v${TRUSTTUNNEL_VERSION}-linux-${machine}.tar.gz', function)
        self.assertIn('releases/tags/v${TRUSTTUNNEL_VERSION}', function)
        self.assertIn('installer_utils.py" "$release_metadata" "$asset"', function)
        self.assertIn('sha256sum --check --status', function)
        self.assertNotIn(' fail "', function)
        main = installer.split('\nmain() {', 1)[1]
        self.assertIn('install_trusttunnel || log "warning:', main)
        self.assertLess(main.index('install_sing_box'), main.index('install_trusttunnel'))
        unit = (Path(__file__).resolve().parents[1] / 'server' / 'systemd' / 'trusttunnel.service').read_text(encoding='utf-8')
        for line in ('User=sing-box', 'ProtectSystem=strict', 'NoNewPrivileges=yes', 'CapabilityBoundingSet=', 'ExecStart=/opt/trusttunnel/trusttunnel_endpoint /etc/sing-box-happ-server/trusttunnel/vpn.toml /etc/sing-box-happ-server/trusttunnel/hosts.toml'):
            self.assertIn(line, unit)

    def test_installer_pins_and_verifies_the_trusttunnel_client_without_making_it_mandatory(self):
        installer = (Path(__file__).resolve().parents[1] / 'scripts' / 'install.sh').read_text(encoding='utf-8')
        function = 'install_trusttunnel_client() {' + installer.split('install_trusttunnel_client() {', 1)[1].split('\n}\n', 1)[0]
        self.assertIn('readonly TRUSTTUNNEL_CLIENT_VERSION="1.1.11"', installer)
        self.assertIn('trusttunnel_client-v${TRUSTTUNNEL_CLIENT_VERSION}-linux-${machine}.tar.gz', function)
        self.assertIn('TrustTunnel/TrustTunnelClient/releases/download/v${TRUSTTUNNEL_CLIENT_VERSION}', function)
        self.assertIn('releases/tags/v${TRUSTTUNNEL_CLIENT_VERSION}', function)
        self.assertIn('installer_utils.py" "$release_metadata" "$asset"', function)
        self.assertIn('sha256sum --check --status', function)
        self.assertIn('trusttunnel_client" --version', function)
        self.assertNotIn(' fail "', function)
        main = installer.split('\nmain() {', 1)[1]
        self.assertIn('install_trusttunnel_client || log "warning:', main)
        self.assertLess(main.index('install_trusttunnel ||'), main.index('install_trusttunnel_client ||'))
        self.assertIn('server/panel/trusttunnel_upstream.py server/systemd/trusttunnel-client@.service', installer)
        self.assertIn('TrustTunnel endpoint and client (/opt/trusttunnel)', installer)


if __name__ == '__main__':
    unittest.main()