import http.client
import json
import runpy
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode


SCRIPT = Path(__file__).resolve().parents[1] / 'server' / 'libexec' / 'focusvpn-gateway-mode'
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))
sys.path.insert(0, str(SCRIPT.parents[1] / 'panel'))
import app


class GatewayPanelModeTests(unittest.TestCase):
    def test_removed_mode_cannot_start_gateway_unit(self):
        with patch.object(app, 'command') as command:
            with self.assertRaises(ValueError):
                app.control_gateway_mode('wireguard')
            command.assert_not_called()

    def test_legacy_mode_is_not_exposed_as_supported_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'GATEWAY_MODE_PATH', Path(directory) / 'gateway.json'):
            for stored, expected in (('vless', 'vless'), ('default', 'default'), ('wireguard', 'vless')):
                app.GATEWAY_MODE_PATH.write_text(json.dumps({'mode': stored}))
                self.assertEqual(app.gateway_mode(), expected)

    def test_supported_modes_preserve_monitor_events(self):
        monitor = Mock()
        with patch.object(app, 'gateway_mode', side_effect=['vless', 'default']), patch.object(app, 'command', return_value=subprocess.CompletedProcess([], 0, '')) as command, patch.object(app, 'VLESS_MONITOR', monitor):
            app.control_gateway_mode('default')
        command.assert_called_once_with([app.SYSTEMCTL_BIN, 'start', app.GATEWAY_MODE_UNIT.format('default')], timeout=120)
        monitor.record_switch.assert_called_once_with('vless', 'default', 'manual_mode')


class RemovedExternalClientTests(unittest.TestCase):
    def test_client_backend_symbols_are_absent(self):
        for name in ('WIREGUARD_CLIENT_CONFIG_PATH', 'validate_wireguard_client_config', 'save_wireguard_client_config', 'load_wireguard_client_text', 'collect_wireguard_client_state', 'wireguard_client_state', 'control_wireguard_client'):
            self.assertFalse(hasattr(app, name), name)

    def test_privileged_status_action_is_rejected(self):
        namespace = runpy.run_path(str(SCRIPT.with_name('focusvpn-service-control')), run_name='focusvpn_service_control_test')
        with patch.object(sys, 'argv', ['focusvpn-service-control', 'wireguard-client-status']), self.assertRaises(SystemExit):
            namespace['main']()

    def test_removed_routes_return_not_found_without_system_commands(self):
        server = app.VpnOnlyServer(('127.0.0.1', 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(app.Handler, 'require_access', return_value=True), patch.object(app, 'command') as command:
                for method, path in (('GET', '/settings/gateway/client/live'), ('POST', '/settings/gateway/client'), ('POST', '/settings/gateway/config')):
                    # A GET body would stay unread and the server's close could reset the socket before the 404 is read.
                    body = urlencode({'csrf': app.CSRF_TOKEN, 'operation': 'connect'}) if method == 'POST' else None
                    connection.request(method, path, body, {'Content-Type': 'application/x-www-form-urlencoded'} if body else {})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 404)
                    response.read()
                command.assert_not_called()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


class GatewayModeTests(unittest.TestCase):
    def setUp(self):
        namespace = runpy.run_path(str(SCRIPT), run_name='focusvpn_gateway_mode_test')
        self.namespace = namespace['apply_default'].__globals__

    def run_for(self, args, optional=False, input_text=None):
        if args[:4] == ['/usr/sbin/ip', '-j', '-4', 'route'] and args[4] == 'show':
            return subprocess.CompletedProcess(args, 0, '[{"gateway":"192.168.0.6","dev":"eth0"}]')
        if args[:4] == ['/usr/sbin/ip', '-j', '-4', 'route'] and args[4] == 'get':
            if args[args.index('from') + 1] == '10.8.0.1':
                return subprocess.CompletedProcess(args, 2, 'RTNETLINK answers: Invalid argument')
            return subprocess.CompletedProcess(args, 0, '[{"gateway":"192.168.0.6","dev":"eth0"}]')
        if args[0] == '/usr/sbin/sysctl':
            return subprocess.CompletedProcess(args, 0, '1\n')
        return subprocess.CompletedProcess(args, 0, '')

    def test_default_route_requires_existing_forward_nat_and_ip_forward(self):
        with patch.dict(self.namespace, {'run': self.run_for}):
            route = self.namespace['verify_default_gateway_route']()
        self.assertEqual(route['gateway'], '192.168.0.6')
        self.assertEqual(route['dev'], 'eth0')

    def test_default_preflight_installs_missing_subnet_scoped_rules_once(self):
        checks = set()
        insertions = []

        def run(args, optional=False, input_text=None):
            if args[0] == '/usr/sbin/iptables':
                if '-C' in args:
                    return subprocess.CompletedProcess(args, 0 if tuple(args) in checks else 1, '')
                if '-I' in args:
                    check = list(args)
                    operation = check.index('-I')
                    check[operation] = '-C'
                    del check[operation + 2]
                    checks.add(tuple(check))
                    insertions.append(args)
                    return subprocess.CompletedProcess(args, 0, '')
            return self.run_for(args, optional, input_text)

        with patch.dict(self.namespace, {'run': run}):
            result = self.namespace['verify_default_gateway_route'](require_selected=False, install_missing=True)
            self.assertEqual(len(result['_added_forwarding']), 3)
            repeated = self.namespace['verify_default_gateway_route'](require_selected=False, install_missing=True)
            self.assertEqual(repeated['_added_forwarding'], [])
        self.assertEqual(len(insertions), 3)
        self.assertTrue(all('10.8.0.0/24' in rule for rule in insertions))
        self.assertTrue(all('eth0' in rule for rule in insertions))

    def test_failed_nat_creation_rolls_back_only_added_rules(self):
        removed = []

        def run(args, optional=False, input_text=None):
            if args[0] == '/usr/sbin/iptables':
                if '-C' in args:
                    if '-d' in args:
                        return subprocess.CompletedProcess(args, 0, '')
                    if '-s' in args and 'FORWARD' in args and inserted:
                        return subprocess.CompletedProcess(args, 0, '')
                    return subprocess.CompletedProcess(args, 1, '')
                if '-I' in args:
                    if 'POSTROUTING' in args:
                        raise RuntimeError('NAT insertion failed')
                    inserted.append(args)
                    return subprocess.CompletedProcess(args, 0, '')
                if '-D' in args:
                    removed.append(args)
                    return subprocess.CompletedProcess(args, 0, '')
            return self.run_for(args, optional, input_text)

        inserted = []
        with patch.dict(self.namespace, {'run': run}):
            with self.assertRaisesRegex(RuntimeError, 'NAT insertion failed'):
                self.namespace['verify_default_gateway_route'](require_selected=False, install_missing=True)
        self.assertEqual(len(removed), 1)
        self.assertIn('-s', removed[0])
        self.assertNotIn('-d', removed[0])

    def test_disabled_forwarding_does_not_install_firewall_rules(self):
        commands = []

        def run(args, optional=False, input_text=None):
            commands.append(args)
            if args[0] == '/usr/sbin/sysctl':
                return subprocess.CompletedProcess(args, 0, '0\n')
            return self.run_for(args, optional, input_text)

        with patch.dict(self.namespace, {'run': run}):
            with self.assertRaisesRegex(RuntimeError, 'forwarding выключен'):
                self.namespace['verify_default_gateway_route'](install_missing=True)
        self.assertFalse(any('-I' in command for command in commands))

    def test_default_route_rejects_a_different_client_egress(self):
        def different_route(args, optional=False, input_text=None):
            if args[:4] == ['/usr/sbin/ip', '-j', '-4', 'route'] and args[4] == 'show':
                return subprocess.CompletedProcess(args, 0, '[{"gateway":"192.168.0.6","dev":"eth0"}]')
            if args[:4] == ['/usr/sbin/ip', '-j', '-4', 'route'] and args[4] == 'get':
                if args[args.index('from') + 1] == '10.8.0.1':
                    return subprocess.CompletedProcess(args, 2, 'RTNETLINK answers: Invalid argument')
                return subprocess.CompletedProcess(args, 0, '[{"gateway":"198.51.100.1","dev":"wg-client"}]')
            return self.run_for(args, optional, input_text)

        with patch.dict(self.namespace, {'run': different_route}):
            with self.assertRaisesRegex(RuntimeError, 'основной шлюз сервера'):
                self.namespace['verify_default_gateway_route']()

    def test_default_mode_preflight_allows_current_external_policy_route(self):
        def external_wg_route(args, optional=False, input_text=None):
            if args[:4] == ['/usr/sbin/ip', '-j', '-4', 'route'] and args[4] == 'show':
                return subprocess.CompletedProcess(args, 0, '[{"gateway":"192.168.0.6","dev":"eth0"}]')
            if args[:4] == ['/usr/sbin/ip', '-j', '-4', 'route'] and args[4] == 'get':
                if args[args.index('from') + 1] == '10.8.0.1':
                    return subprocess.CompletedProcess(args, 2, 'RTNETLINK answers: Invalid argument')
                return subprocess.CompletedProcess(args, 0, '[{"gateway":"198.51.100.1","dev":"wg-client"}]')
            return self.run_for(args, optional, input_text)

        with patch.dict(self.namespace, {'run': external_wg_route}):
            route = self.namespace['verify_default_gateway_route'](require_selected=False)
        self.assertEqual(route['dev'], 'eth0')
        with patch.dict(self.namespace, {'run': external_wg_route}):
            with self.assertRaisesRegex(RuntimeError, 'основной шлюз сервера'):
                self.namespace['verify_default_gateway_route']()

    def test_default_mode_stops_tproxy_without_restarting_happ(self):
        systemctl = Mock()
        save_mode = Mock()
        verify_route = Mock(return_value={'gateway': '192.168.0.6', 'dev': 'eth0'})
        with patch.dict(self.namespace, {'load_mode_state': lambda: {'mode': 'vless'}, 'verify_default_gateway_route': verify_route, 'systemctl': systemctl, 'save_mode': save_mode}):
            self.namespace['apply_default']()
        systemctl.assert_has_calls([unittest.mock.call('stop', 'sing-box.service'), unittest.mock.call('start', 'sing-box-happ-server.service')])
        verify_route.assert_has_calls([unittest.mock.call(require_selected=False, install_missing=True), unittest.mock.call()])
        save_mode.assert_called_once_with('default')

    def test_vless_mode_starts_only_existing_vpn_services(self):
        systemctl = Mock()
        save_mode = Mock()
        with patch.dict(self.namespace, {'systemctl': systemctl, 'save_mode': save_mode}):
            self.namespace['apply_vless']()
        systemctl.assert_has_calls([unittest.mock.call('start', 'sing-box.service'), unittest.mock.call('start', 'sing-box-happ-server.service')])
        save_mode.assert_called_once_with('vless')

    def test_system_gateway_rejects_removed_mode_without_commands(self):
        command = Mock()
        with patch.dict(self.namespace, {'run': command}), patch.object(sys, 'argv', ['focusvpn-gateway-mode', 'wireguard']), self.assertRaises(SystemExit):
            self.namespace['main']()
        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()