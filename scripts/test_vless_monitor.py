import datetime as dt
import json
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'panel'))
from vless_monitor import DEFAULT_MESSAGE_TEMPLATE, VlessMonitor, fastest_available, fastest_vless, gateway_text, notify_mattermost, render_message, validate_settings, validate_template


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
        self.sources = []
        self.monitor = VlessMonitor(
            self.directory.name,
            lambda: json.loads(json.dumps(self.config)),
            self.queue,
            self.states,
            self.switch,
            lambda: self.mode,
        )
        self.addCleanup(self.monitor.stop)
        self.monitor.save_settings({'enabled': True, 'auto_switch': True, 'interval_minutes': 1})

    def states(self, config):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        return {'checks': [{'checked_at': now, **item} for item in json.loads(json.dumps(self.checks))]}

    def enable_failover(self, **overrides):
        self.monitor.save_settings({'enabled': False, 'auto_switch': False, 'failover_enabled': True, 'interval_minutes': 1, **overrides})

    def queue(self, config, tags):
        self.queued.append(list(tags))
        result = []
        for tag in tags:
            future = Future()
            future.set_result({'tag': tag})
            result.append(future)
        return result

    def switch(self, tag, old, servers, latency, revision, source='automatic'):
        self.switches.append((old, tag, latency))
        self.sources.append(source)
        self.config['route']['final'] = tag
        self.monitor.record_switch(old, tag, source, latency)
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
        event = {'at': '2026-10-10T19:41:30+00:00', 'old_route': 'auto-1', 'new_route': 'auto-2', 'source': 'automatic'}
        with patch('vless_monitor.urlopen', return_value=context) as send:
            notify_mattermost('https://mattermost.example/hooks/test-only', event, 'Было {old} -> Стало {new} в {time}', '+03:00')
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data)['text'], 'Было auto-1 -> Стало auto-2 в 22:41')
        self.assertEqual(send.call_args.kwargs['timeout'], 5)

    def test_default_message_matches_requested_layout(self):
        event = {'at': '2026-10-10T19:41:30+00:00', 'old_route': 'auto-10', 'new_route': 'auto-10', 'source': 'test'}
        self.assertEqual(
            render_message(DEFAULT_MESSAGE_TEMPLATE, event),
            '📢 VPN-шлюз: обновление статуса\n📅 10.10.2026 | 🕐 22:41\n🔁 Произошла смена шлюза:\n➡️ Было: auto-10\n✅ Стало: auto-10 (test)',
        )
        self.assertEqual(render_message(DEFAULT_MESSAGE_TEMPLATE, event, '-05:30').splitlines()[1], '📅 10.10.2026 | 🕐 14:11')
        failover = {**event, 'old_route': 'auto-10', 'new_route': 'auto-2', 'source': 'failover', 'latency_ms': 40.5}
        self.assertIn('✅ Стало: auto-2 (недоступность шлюза)', render_message(DEFAULT_MESSAGE_TEMPLATE, failover))
        self.assertEqual(render_message('{latency}|{latency}', failover), '40.5 мс|40.5 мс')
        self.assertEqual(render_message('{latency}', event), '—')

    def test_template_is_whitelisted_and_never_evaluated(self):
        event = {'at': '2026-10-10T19:41:30+00:00', 'old_route': '{new}', 'new_route': 'auto-2', 'source': 'manual'}
        self.assertEqual(render_message('{old} {unknown} {old.__class__} {} %s', event), '{new} {unknown} {old.__class__} {} %s')
        self.assertEqual(validate_template('  {old} -> {new}  \r\n'), '{old} -> {new}')
        self.assertEqual(validate_template('  \n '), DEFAULT_MESSAGE_TEMPLATE)
        self.assertEqual(validate_template(None), DEFAULT_MESSAGE_TEMPLATE)
        for invalid in ('{secret}', 'ok {old} {webhook_url}', 'x' * 1001):
            with self.assertRaises(ValueError):
                validate_template(invalid)
        self.assertEqual(validate_template('{old.__class__}'), '{old.__class__}')

    def test_utc_offset_validation_and_settings_defaults(self):
        for value, expected in (('+03:00', '+03:00'), ('-05:30', '-05:30'), ('+14:00', '+14:00'), ('-12:00', '-12:00'), ('+05:45', '+05:45')):
            self.assertEqual(validate_settings({'utc_offset': value})['utc_offset'], expected)
        for invalid in ('3', '+3:00', '03:00', '+15:00', '-13:00', '+03:07', 'Europe/Moscow', '', '+03:00x'):
            with self.assertRaises(ValueError):
                validate_settings({'utc_offset': invalid})
        defaults = validate_settings({})
        self.assertEqual((defaults['utc_offset'], defaults['failover_enabled'], defaults['message_template']), ('+03:00', False, DEFAULT_MESSAGE_TEMPLATE))
        with self.assertRaises(ValueError):
            validate_settings({'failover_enabled': 'on'})
        self.assertEqual(validate_settings({'message_template': ''})['message_template'], DEFAULT_MESSAGE_TEMPLATE)

    def test_saved_template_and_offset_are_used_for_delivery(self):
        webhook = 'https://mattermost.example/hooks/test-only-token'
        self.monitor.save_settings({'enabled': True, 'auto_switch': True, 'mattermost_enabled': True, 'webhook_url': webhook, 'interval_minutes': 1, 'message_template': '{old}>{new}@{time}', 'utc_offset': '-05:00'})
        event = {'at': '2026-10-10T19:41:30+00:00', 'old_route': 'auto-1', 'new_route': 'auto-2', 'source': 'automatic'}
        with patch('vless_monitor.notify_mattermost') as send:
            self.monitor.deliver_notification(webhook, event)
        send.assert_called_once_with(webhook, event, '{old}>{new}@{time}', '-05:00')
        self.assertTrue(any(item['event'] == 'mattermost_sent' for item in self.monitor.journal()))

    def fail_gateway(self, cycles):
        for cycle in range(cycles):
            self.monitor.run_cycle()

    def test_failover_switches_to_fastest_server_of_any_type_only_after_three_failed_checks(self):
        self.enable_failover()
        self.checks[0].update(state='error', latency_ms=None)
        self.fail_gateway(1)
        status = self.monitor.status()
        self.assertEqual((status['gateway_state'], status['gateway_strikes'], self.switches), ('suspect', 1, []))
        self.assertIn('(1/3)', status['gateway_text'])
        self.fail_gateway(1)
        self.assertEqual((self.monitor.status()['gateway_strikes'], self.switches), (2, []))
        self.assertFalse(any(item['event'] == 'gateway_down' for item in self.monitor.journal()))
        self.fail_gateway(1)
        self.assertEqual(self.switches, [('auto-1', 'auto-3', 1)])
        self.assertEqual(self.sources, ['failover'])
        self.assertEqual(self.queued, [['auto-1'], ['auto-1'], ['auto-1'], ['auto-2', 'auto-3']])
        events = [item['event'] for item in self.monitor.journal()]
        self.assertEqual((events.count('gateway_check_failed'), events.count('gateway_down'), events.count('route_changed')), (2, 1, 1))
        self.assertEqual((self.monitor.status()['gateway_state'], self.monitor.status()['gateway_route'], self.monitor.status()['gateway_strikes']), ('ok', 'auto-3', 0))

    def test_failover_ignores_healthy_gateway(self):
        self.enable_failover()
        self.monitor.run_cycle()
        self.assertEqual(self.switches, [])
        self.assertEqual(self.queued, [['auto-1']])
        status = self.monitor.status()
        self.assertEqual((status['gateway_state'], status['gateway_route']), ('ok', 'auto-1'))
        self.assertIn('auto-1: доступен.', status['gateway_text'])

    def test_failover_success_between_failures_resets_the_counter(self):
        self.enable_failover()
        for state, expected in (('error', ('suspect', 1)), ('error', ('suspect', 2)), ('success', ('ok', 0)), ('error', ('suspect', 1)), ('error', ('suspect', 2))):
            self.checks[0]['state'] = state
            self.monitor.run_cycle()
            status = self.monitor.status()
            self.assertEqual((status['gateway_state'], status['gateway_strikes']), expected)
        self.assertEqual(self.switches, [])
        self.assertFalse(any(item['event'] == 'gateway_down' for item in self.monitor.journal()))

    def test_failover_ignores_stale_or_missing_probe_results_and_clears_strikes(self):
        self.enable_failover()
        self.checks[0].update(state='error')
        self.fail_gateway(2)
        self.assertEqual(self.monitor.status()['gateway_strikes'], 2)
        for checked_at in ('2020-01-01T00:00:00+00:00', None, 'not-a-date'):
            self.checks[0].update(state='error', checked_at=checked_at)
            self.monitor.run_cycle()
            status = self.monitor.status()
            self.assertEqual((self.switches, status['gateway_state'], status['gateway_strikes']), ([], 'unknown', 0))
        self.assertFalse(any(item['event'] == 'gateway_down' for item in self.monitor.journal()))
        self.checks[0].pop('checked_at')
        self.checks[0]['state'] = 'running'
        self.monitor.run_cycle()
        self.assertEqual(self.switches, [])
        self.checks[0]['state'] = 'error'
        self.monitor.run_cycle()
        self.assertEqual(self.monitor.status()['gateway_strikes'], 1)

    def test_failover_route_change_starts_a_new_count(self):
        self.enable_failover()
        self.checks[0].update(state='error')
        self.checks[1].update(state='error')
        self.fail_gateway(2)
        self.assertEqual(self.monitor.status()['gateway_strikes'], 2)
        self.config['route']['final'] = 'auto-2'
        self.monitor.run_cycle()
        status = self.monitor.status()
        self.assertEqual((status['gateway_route'], status['gateway_state'], status['gateway_strikes']), ('auto-2', 'suspect', 1))
        self.assertEqual(self.switches, [])

    def test_failover_without_candidate_keeps_gateway_and_logs_once(self):
        self.enable_failover()
        for check in self.checks:
            check.update(state='error', latency_ms=None)
        self.fail_gateway(5)
        events = [item['event'] for item in self.monitor.journal()]
        self.assertEqual((events.count('gateway_check_failed'), events.count('gateway_down'), events.count('failover_no_candidate')), (2, 1, 1))
        self.assertEqual(self.switches, [])
        self.assertEqual(self.config['route']['final'], 'auto-1')
        self.assertEqual(self.monitor.status()['gateway_state'], 'down')

    def test_down_gateway_retries_candidates_every_cycle_without_new_strikes(self):
        self.enable_failover()
        for check in self.checks:
            check.update(state='error', latency_ms=None)
        self.fail_gateway(4)
        self.assertEqual((self.monitor.status()['gateway_state'], self.switches), ('down', []))
        self.checks[1].update(state='success', latency_ms=40)
        self.fail_gateway(1)
        self.assertEqual(self.switches, [('auto-1', 'auto-2', 40)])
        self.assertEqual(self.monitor.status()['gateway_state'], 'ok')

    def test_down_gateway_that_recovers_returns_to_ok_without_switching(self):
        self.enable_failover()
        for check in self.checks:
            check.update(state='error', latency_ms=None)
        self.fail_gateway(3)
        self.assertEqual(self.monitor.status()['gateway_state'], 'down')
        self.checks[0].update(state='success', latency_ms=100)
        self.fail_gateway(1)
        self.assertEqual((self.monitor.status()['gateway_state'], self.switches), ('ok', []))

    def test_failover_candidates_exclude_failed_slow_invalid_and_current(self):
        checks = [
            {'tag': 'auto-1', 'state': 'success', 'latency_ms': 1},
            {'tag': 'auto-2', 'state': 'error', 'latency_ms': 2},
            {'tag': 'auto-3', 'state': 'success', 'latency_ms': float('nan')},
            {'tag': 'auto-4', 'state': 'success', 'latency_ms': 90},
            {'tag': 'auto-5', 'state': 'success', 'latency_ms': 30},
            {'tag': 'auto-6', 'state': 'success', 'latency_ms': True},
        ]
        self.assertEqual(fastest_available(checks, 'auto-1'), ('auto-5', 30))
        self.assertIsNone(fastest_available(checks[:3], 'auto-1'))

    def test_failover_is_paused_outside_vless_mode_and_skips_selector_routes(self):
        self.enable_failover()
        self.mode = 'default'
        self.monitor.run_cycle()
        self.assertEqual((self.queued, self.monitor.status()['gateway_state']), ([], 'paused'))
        self.mode = 'vless'
        self.config['outbounds'].append({'tag': 'vless-auto', 'type': 'urltest'})
        self.config['route']['final'] = 'vless-auto'
        self.monitor.run_cycle()
        self.assertEqual((self.queued, self.monitor.status()['gateway_state']), ([], 'skipped'))
        self.assertEqual(self.switches, [])

    def test_failover_error_does_not_block_scheduled_selection(self):
        self.monitor.save_settings({'enabled': True, 'auto_switch': True, 'failover_enabled': True, 'interval_minutes': 1})
        self.monitor.failover_cycle = Mock(side_effect=RuntimeError('probe infrastructure failed'))
        self.monitor.run_cycle()
        self.assertEqual(self.queued, [['auto-1', 'auto-2']])
        self.assertTrue(any(item['event'] == 'monitor_error' for item in self.monitor.journal()))
        self.assertFalse(self.monitor.status()['running'])

    def test_failover_exception_leaves_fast_recheck_mode(self):
        self.enable_failover()
        self.checks[0].update(state='error')
        self.fail_gateway(1)
        self.assertEqual(self.monitor.status()['gateway_state'], 'suspect')
        self.monitor.failover_cycle = Mock(side_effect=RuntimeError('probe infrastructure failed'))
        self.monitor.run_cycle()
        status = self.monitor.status()
        self.assertEqual((status['gateway_state'], status['gateway_strikes'], status['gateway_route']), ('unknown', 0, 'auto-1'))

    def test_failed_failover_switch_is_logged_and_route_is_kept(self):
        self.enable_failover()
        self.checks[0].update(state='error', latency_ms=None)
        self.monitor.switch_route = Mock(side_effect=RuntimeError('apply failed'))
        self.fail_gateway(3)
        self.assertEqual(self.config['route']['final'], 'auto-1')
        failed = [item for item in self.monitor.journal() if item['event'] == 'switch_failed']
        self.assertEqual((failed[0]['old_route'], failed[0]['new_route']), ('auto-1', 'auto-3'))

    def test_failover_flag_alone_schedules_cycles_and_disabled_flag_never_probes(self):
        self.enable_failover()
        self.monitor.next_cycle = 100
        self.monitor.wakeup = Mock()
        self.monitor.wakeup.wait.side_effect = [False, True]
        self.monitor.stop_event = Mock()
        self.monitor.stop_event.is_set.side_effect = [False, False, False, True]
        with patch('vless_monitor.time.monotonic', return_value=100):
            self.monitor.run()
        self.assertEqual(self.queued, [['auto-1']])
        self.assertEqual(self.monitor.wakeup.wait.call_args_list[1].kwargs['timeout'], 60)
        self.queued.clear()
        self.monitor.save_settings({'enabled': True, 'auto_switch': False, 'failover_enabled': False, 'interval_minutes': 1})
        self.monitor.run_cycle()
        self.assertEqual(self.queued, [['auto-1', 'auto-2']])
        self.assertIsNone(self.monitor.status()['gateway_state'])

    def schedule_one_cycle(self):
        self.monitor.next_cycle = 100
        self.monitor.wakeup = Mock()
        self.monitor.wakeup.wait.side_effect = [False, True]
        self.monitor.stop_event = Mock()
        self.monitor.stop_event.is_set.side_effect = [False, False, False, True]
        with patch('vless_monitor.time.monotonic', return_value=100):
            self.monitor.run()
        return self.monitor.wakeup.wait.call_args_list[1].kwargs['timeout']

    def test_suspect_gateway_is_rechecked_every_minute_without_running_selection(self):
        self.enable_failover(enabled=True, auto_switch=True, interval_minutes=10)
        self.checks[0].update(state='error')
        self.assertEqual(self.schedule_one_cycle(), 60)
        self.assertEqual(self.queued, [['auto-1']])
        self.assertEqual((self.monitor.status()['gateway_state'], self.monitor.status()['streak']), ('suspect', 0))
        self.assertFalse(any(item['event'] == 'check_cycle' for item in self.monitor.journal()))

    def test_healthy_or_confirmed_gateway_uses_the_configured_interval(self):
        self.enable_failover(enabled=True, auto_switch=False, interval_minutes=10)
        self.assertEqual(self.schedule_one_cycle(), 600)
        self.assertEqual(self.queued, [['auto-1'], ['auto-1', 'auto-2']])
        self.assertEqual(self.monitor.status()['gateway_state'], 'ok')

    def test_disabling_failover_clears_gateway_status_text(self):
        self.enable_failover()
        self.monitor.run_cycle()
        self.assertEqual(self.monitor.status()['gateway_state'], 'ok')
        self.monitor.save_settings({'enabled': True, 'auto_switch': False, 'failover_enabled': False, 'interval_minutes': 1})
        status = self.monitor.status()
        self.assertEqual((status['gateway_state'], status['gateway_route'], status['gateway_strikes']), (None, None, 0))
        self.assertEqual(status['gateway_text'], 'Автопроверка доступности выключена.')

    def test_gateway_text_states(self):
        self.assertIn('выключена', gateway_text({}, {'failover_enabled': False}))
        self.assertIn('ещё не выполнена', gateway_text({}, {'failover_enabled': True}))
        state = {'gateway_route': 'auto-10', 'gateway_state': 'down', 'gateway_checked_at': '2026-10-10T19:41:05+00:00'}
        self.assertEqual(gateway_text(state, {'failover_enabled': True}), 'Шлюз по умолчанию auto-10: недоступен. Проверено 10.10.2026 19:41:05 UTC.')
        suspect = {**state, 'gateway_state': 'suspect', 'gateway_strikes': 2}
        self.assertEqual(gateway_text(suspect, {'failover_enabled': True}), 'Шлюз по умолчанию auto-10: проверка не пройдена, идёт подтверждение (2/3). Проверено 10.10.2026 19:41:05 UTC.')
        self.assertIn('—', gateway_text({'gateway_state': 'ok'}, {'failover_enabled': True}))

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