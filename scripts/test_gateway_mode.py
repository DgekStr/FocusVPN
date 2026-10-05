import json
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'server' / 'libexec' / 'focusvpn-gateway-mode'
if sys.platform == 'win32':
    sys.modules.setdefault('grp', types.ModuleType('grp'))


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

    def test_default_mode_stops_tproxy_and_external_peer_and_restores_happ_route(self):
        with tempfile.TemporaryDirectory() as directory:
            happ_config = Path(directory) / 'happ.json'
            happ_config.write_text(json.dumps({'route': {'final': 'focusvpn-wg-direct'}}))
            systemctl = Mock()
            configure_happ = Mock(return_value='auto-10')
            save_mode = Mock()
            verify_route = Mock(return_value={'gateway': '192.168.0.6', 'dev': 'eth0'})
            cleanup = Mock()
            overrides = {
                'HAPP_CONFIG_PATH': happ_config,
                'load_mode_state': lambda: {'mode': 'wireguard', 'happ_route': 'auto-10'},
                'verify_default_gateway_route': verify_route,
                'cleanup_wireguard': cleanup,
                'systemctl': systemctl,
                'configure_happ_outbound': configure_happ,
                'save_mode': save_mode,
            }
            with patch.dict(self.namespace, overrides):
                self.namespace['apply_default']()

        cleanup.assert_called_once_with()
        systemctl.assert_has_calls([unittest.mock.call('stop', 'sing-box.service'), unittest.mock.call('start', 'sing-box-happ-server.service')])
        configure_happ.assert_called_once_with('vless', 'auto-10')
        verify_route.assert_has_calls([unittest.mock.call(require_selected=False), unittest.mock.call()])
        save_mode.assert_called_once_with('default')

    def test_default_mode_restarts_happ_when_restoring_provider_route(self):
        with tempfile.TemporaryDirectory() as directory:
            happ_config = Path(directory) / 'happ.json'
            happ_config.write_text(json.dumps({'route': {'final': 'focusvpn-wg-direct'}}))
            systemctl = Mock()

            def restore_provider(mode, original_route):
                self.assertEqual((mode, original_route), ('vless', 'auto-10'))
                happ_config.write_text(json.dumps({'route': {'final': 'auto-10'}}))

            overrides = {
                'HAPP_CONFIG_PATH': happ_config,
                'load_mode_state': lambda: {'mode': 'wireguard', 'happ_route': 'auto-10'},
                'verify_default_gateway_route': Mock(return_value={'gateway': '192.168.0.6', 'dev': 'eth0'}),
                'cleanup_wireguard': Mock(),
                'systemctl': systemctl,
                'configure_happ_outbound': restore_provider,
                'save_mode': Mock(),
            }
            with patch.dict(self.namespace, overrides):
                self.namespace['apply_default']()
            systemctl.assert_has_calls([unittest.mock.call('stop', 'sing-box.service'), unittest.mock.call('restart', 'sing-box-happ-server.service')])


if __name__ == '__main__':
    unittest.main()