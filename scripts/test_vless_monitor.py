import json
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from vless_monitor import VlessMonitor, fastest_vless, validate_settings, notify_mattermost


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = {
            'outbounds': [{'tag': 'auto-1', 'type': 'vless'}, {'tag': 'auto-2', 'type': 'vless'}, {'tag': 'auto-3', 'type': 'hysteria2'}],
            'route': {'final': 'auto-1'},
        }
        self.checks = [
            {'tag': 'auto-1', 'state': 'success', 'latency_ms': 100},
            {'tag': 'auto-2', 'state': 'success', 'latency_ms': 40},
            {'tag': 'auto-3', 'state': 'success', 'latency_ms': 1},
        ]
        self.queued = []
        self.mode = 'vless'
        self.switches = []
        self.monitor = VlessMonitor(
            self.directory.name,
            lambda: json.loads(json.dumps(self.config)),
            self.queue,
            lambda config: {'checks': json.loads(json.dumps(self.checks))},
            self.switch,
            lambda: self.mode,
        )
        self.addCleanup(self.monitor.stop)
        self.monitor.save_settings({'enabled': True, 'auto_switch': True, 'interval_minutes': 1})

    def queue(self, config, tags):
        self.queued.append(list(tags))
        result = []
        for tag in tags:
            future = Future()
            future.set_result({'tag': tag})
            result.append(future)
        return result

    def switch(self, tag, old, servers, latency, revision):
        self.switches.append((old, tag, latency))
        self.config['route']['final'] = tag
        self.monitor.record_switch(old, tag, 'automatic', latency)
        return True

    def test_only_vless_and_three_consecutive_wins(self):
        self.monitor.run_cycle()
        self.monitor.run_cycle()
        self.assertEqual(self.switches, [])
        self.assertEqual(self.monitor.status()['streak'], 2)
        self.monitor.run_cycle()
        self.assertEqual(self.switches, [('auto-1', 'auto-2', 40)])
        self.assertEqual(self.queued, [['auto-1', 'auto-2']] * 3)
        self.assertEqual(self.monitor.status()['streak'], 0)
        events = self.monitor.journal()
        self.assertTrue(any(item['event'] == 'route_changed' and item['source'] == 'automatic' for item in events))

    def test_failure_and_changed_winner_reset_streak(self):
        self.monitor.run_cycle()
        self.checks[1]['state'] = 'error'
        self.monitor.run_cycle()
        self.assertEqual(self.monitor.status()['candidate'], 'auto-1')
        self.assertEqual(self.monitor.status()['streak'], 1)
        self.checks[0]['state'] = 'error'
        self.monitor.run_cycle()
        self.assertIsNone(self.monitor.status()['candidate'])
        self.assertEqual(self.monitor.status()['streak'], 0)
        self.assertEqual(self.switches, [])

    def test_wireguard_does_not_switch(self):
        self.mode = 'wireguard'
        for cycle in range(3):
            self.monitor.run_cycle()
        self.assertEqual(self.switches, [])
        self.assertEqual(self.monitor.status()['streak'], 0)

    def test_manual_change_resets_streak(self):
        self.monitor.run_cycle()
        self.monitor.run_cycle()
        self.monitor.record_switch('auto-1', 'auto-3', 'manual')
        self.config['route']['final'] = 'auto-3'
        self.monitor.run_cycle()
        self.assertEqual(self.switches, [])
        self.assertEqual(self.monitor.status()['streak'], 1)

    def test_disable_mid_cycle_prevents_switch(self):
        self.monitor.run_cycle()
        self.monitor.run_cycle()
        original_queue = self.monitor.queue_checks

        def disable(config, tags):
            self.monitor.save_settings({'enabled': False, 'auto_switch': False, 'interval_minutes': 1})
            return original_queue(config, tags)

        self.monitor.queue_checks = disable
        self.monitor.run_cycle()
        self.assertEqual(self.switches, [])

    def test_exception_resets_candidate(self):
        self.monitor.run_cycle()
        self.monitor.queue_checks = Mock(side_effect=RuntimeError('probe failed'))
        with self.assertRaises(RuntimeError):
            self.monitor.run_cycle()
        self.assertEqual(self.monitor.status()['streak'], 0)
        self.assertFalse(self.monitor.status()['running'])

    def test_failed_switch_is_logged(self):
        self.monitor.switch_route = Mock(side_effect=RuntimeError('apply failed'))
        for cycle in range(3):
            self.monitor.run_cycle()
        self.assertEqual(self.config['route']['final'], 'auto-1')
        self.assertTrue(any(item['event'] == 'switch_failed' for item in self.monitor.journal()))

    def test_interval_and_webhook_validation(self):
        for interval in (0, 61, 'invalid'):
            with self.assertRaises(ValueError):
                validate_settings({'interval_minutes': interval})
        self.assertEqual(validate_settings({'interval_minutes': 60})['interval_minutes'], 60)
        for url in ('file:///tmp/secret', 'https://user:password@example.com/hooks/path'):
            with self.assertRaises(ValueError):
                validate_settings({'mattermost_enabled': True, 'webhook_url': url})

    def test_notification_failure_never_undoes_switch_or_leaks_url(self):
        webhook = 'https://mattermost.example/hooks/test-only-token'
        self.monitor.save_settings({'enabled': True, 'auto_switch': True, 'mattermost_enabled': True, 'webhook_url': webhook, 'interval_minutes': 1})
        event = {'old_route': 'auto-1', 'new_route': 'auto-2', 'source': 'automatic', 'latency_ms': 40}
        with patch('vless_monitor.notify_mattermost', side_effect=RuntimeError(webhook)):
            self.monitor.deliver_notification(webhook, event)
        self.assertTrue(any(item['event'] == 'mattermost_failed' for item in self.monitor.journal()))
        self.assertNotIn('test-only-token', (Path(self.directory.name) / 'gateway-switches.jsonl').read_text())

    def test_webhook_payload_and_timeout(self):
        response = Mock(status=200)
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch('vless_monitor.urlopen', return_value=context) as send:
            notify_mattermost('https://mattermost.example/hooks/test-only', {'old_route': 'auto-1', 'new_route': 'auto-2', 'source': 'manual'})
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertIn('auto-1 -> auto-2', json.loads(request.data)['text'])
        self.assertEqual(send.call_args.kwargs['timeout'], 5)

    def test_tie_prefers_current_and_invalid_latency_excluded(self):
        checks = [{'tag': 'auto-1', 'state': 'success', 'latency_ms': 40}, {'tag': 'auto-2', 'state': 'success', 'latency_ms': 40}]
        self.assertEqual(fastest_vless(self.config, checks), 'auto-1')
        checks[0]['latency_ms'] = float('nan')
        self.assertEqual(fastest_vless(self.config, checks), 'auto-2')

    def test_scheduler_waits_configured_interval_after_cycle(self):
        self.monitor.next_cycle = 100
        self.monitor.wakeup = Mock()
        self.monitor.wakeup.wait.side_effect = [False, True]
        self.monitor.stop_event = Mock()
        self.monitor.stop_event.is_set.side_effect = [False, False, False, True]
        with patch('vless_monitor.time.monotonic', return_value=100):
            self.monitor.run()
        self.assertEqual(self.queued, [['auto-1', 'auto-2']])
        self.assertEqual(self.monitor.wakeup.wait.call_args_list[1].kwargs['timeout'], 60)
        self.assertFalse(self.monitor.status()['running'])


if __name__ == '__main__':
    unittest.main()