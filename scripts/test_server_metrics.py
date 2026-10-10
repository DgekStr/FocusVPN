import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from server_metrics import ServerMetrics, format_decimal, format_storage_gb, format_uptime, parse_cpu_stat, parse_net_dev, read_disk_usage, render_metrics_panel, selected_interfaces


class ServerMetricsTests(unittest.TestCase):
    def test_panel_service_preserves_container_uptime_mount(self):
        path = Path(__file__).resolve().parents[1] / 'server' / 'systemd' / 'sing-box-admin.service'
        unit = path.read_text(encoding='utf-8')
        self.assertIn('BindReadOnlyPaths=/proc/uptime', unit)
        self.assertIn('ProtectKernelTunables=true', unit)

    def test_formats_uptime_and_localized_decimal(self):
        self.assertEqual(format_uptime(2 * 3600 + 41 * 60), '2ч 41м')
        self.assertEqual(format_uptime(3599), '0ч 59м')
        self.assertEqual(format_uptime(17 * 86400 + 18 * 3600), '17дн 18ч')
        self.assertEqual(format_decimal(53.1), '53,1')
        self.assertEqual(format_decimal(811274, 0), '811 274')
        self.assertEqual(format_storage_gb(64 * 1024 ** 3), '64,0')

    def test_reads_root_disk_usage(self):
        usage = type('Usage', (), {'total': 64 * 1024 ** 3, 'used': 12 * 1024 ** 3})()
        with patch('server_metrics.shutil.disk_usage', return_value=usage) as disk_usage:
            self.assertEqual(read_disk_usage(), {'total_bytes': 64 * 1024 ** 3, 'used_bytes': 12 * 1024 ** 3})
        disk_usage.assert_called_once_with('/')

    def test_metrics_panel_renders_server_identity_peaks_and_escaped_values(self):
        panel = render_metrics_panel({
            'current': {'uptime': 17 * 86400 + 18 * 3600, 'rx_bytes': 811_274_000_000, 'tx_bytes': 820_873_000_000},
            'peaks': {'cpu': 53.1, 'rx_rate': 54_612_500, 'tx_rate': 30_000_000, 'observed_since': 1_759_673_400},
            'identity': {'ip': '192.0.2.4', 'os': '<Ubuntu 24.10>'},
            'disk': {'total_bytes': 64 * 1024 ** 3, 'used_bytes': 12 * 1024 ** 3},
        })
        self.assertIn('192.0.2.4', panel)
        self.assertIn('&lt;Ubuntu 24.10&gt;', panel)
        self.assertIn('17дн 18ч', panel)
        self.assertIn('53,1%', panel)
        self.assertIn('436,9 Мбит/с', panel)
        self.assertIn('811 274', panel)
        self.assertIn('820 873', panel)
        self.assertIn('SSD-диск', panel)
        self.assertIn('64,0 / 12,0 ГБ', panel)
        self.assertLess(panel.index('Время работы'), panel.index('SSD-диск'))

    def test_parses_cpu_and_network_counters_and_selects_physical_interfaces(self):
        total, idle = parse_cpu_stat('cpu  100 2 30 800 20 3 4 1 0 0\ncpu0 1 0 0 9')
        self.assertEqual(total, 960)
        self.assertEqual(idle, 820)
        counters = parse_net_dev('Inter-| Receive | Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\nlo: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\nenp1s0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\ndocker0: 500 1 0 0 0 0 0 0 700 1 0 0 0 0 0 0')
        self.assertEqual(counters['enp1s0'], (1000, 2000))
        self.assertEqual(selected_interfaces(counters, net_root='/missing'), ['enp1s0'])

    def test_samples_peak_cpu_and_lan_rates_over_last_24_hours(self):
        with tempfile.TemporaryDirectory() as directory, patch('server_metrics.server_identity', return_value={'ip': '192.0.2.10', 'os': 'Ubuntu test', 'interface': 'eth0'}):
            metrics = ServerMetrics(Path(directory) / 'server.sqlite3')
            first = {'uptime': 1000, 'cpu_total': 100, 'cpu_idle': 90, 'rx_bytes': 1_000_000, 'tx_bytes': 2_000_000}
            second = {'uptime': 1005, 'cpu_total': 200, 'cpu_idle': 170, 'rx_bytes': 2_000_000, 'tx_bytes': 3_500_000}
            third = {'uptime': 1010, 'cpu_total': 300, 'cpu_idle': 260, 'rx_bytes': 2_500_000, 'tx_bytes': 4_000_000}
            metrics.sample(first, now=100)
            snapshot = metrics.sample(second, now=105)
            self.assertAlmostEqual(snapshot['current']['cpu_percent'], 20.0)
            self.assertAlmostEqual(snapshot['current']['rx_rate'], 200_000)
            self.assertAlmostEqual(snapshot['current']['tx_rate'], 300_000)
            snapshot = metrics.sample(third, now=110)
            self.assertAlmostEqual(snapshot['peaks']['cpu'], 20.0)
            self.assertAlmostEqual(snapshot['peaks']['rx_rate'], 200_000)
            self.assertAlmostEqual(snapshot['peaks']['tx_rate'], 300_000)
            self.assertEqual(snapshot['current']['rx_bytes'], 2_500_000)
            self.assertEqual(snapshot['identity']['ip'], '192.0.2.10')

    def test_reboot_starts_a_new_peak_window_and_old_samples_expire(self):
        with tempfile.TemporaryDirectory() as directory, patch('server_metrics.server_identity', return_value={'ip': '192.0.2.10', 'os': 'Ubuntu test', 'interface': 'eth0'}):
            metrics = ServerMetrics(Path(directory) / 'server.sqlite3')
            metrics.sample({'uptime': 1000, 'cpu_total': 100, 'cpu_idle': 50, 'rx_bytes': 100, 'tx_bytes': 100}, now=100)
            metrics.sample({'uptime': 1005, 'cpu_total': 200, 'cpu_idle': 170, 'rx_bytes': 1000, 'tx_bytes': 1000}, now=105)
            restarted = metrics.sample({'uptime': 3, 'cpu_total': 10, 'cpu_idle': 5, 'rx_bytes': 5, 'tx_bytes': 7}, now=110)
            self.assertEqual(restarted['peaks']['cpu'], 0)
            self.assertEqual(restarted['current']['rx_bytes'], 5)
            metrics.sample({'uptime': 86405, 'cpu_total': 100, 'cpu_idle': 90, 'rx_bytes': 105, 'tx_bytes': 207}, now=100 + 24 * 60 * 60 + 20)
            self.assertEqual(metrics.snapshot(now=100 + 24 * 60 * 60 + 20)['peaks']['observed_since'], 100 + 24 * 60 * 60 + 20)


if __name__ == '__main__':
    unittest.main()