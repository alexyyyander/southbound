"""Exercise UI contracts through the actual generic queue, policy and receipts."""
import unittest
from southbound.access.adapters.endpoint import EndpointAdapter
from southbound.access.hub import AccessHub
from southbound.runtime import SouthboundRuntime
from southbound.terminal_service import TerminalService
from southbound.terminals import TerminalStore
from southbound.terminals import TerminalConflict
from southbound.access.errors import PolicyDenied, ValidationError
from southbound.access.terminal_capabilities import UI_ACTIONS


class TerminalUiTests(unittest.TestCase):
    def setUp(self):
        self.store = TerminalStore()
        self.runtime = SouthboundRuntime(hub=AccessHub([EndpointAdapter(store=self.store)]))
        self.addCleanup(self.runtime.close)
        self.service = TerminalService(self.runtime)
        self.caps = ['notify', 'speak', 'read_battery']
        pairing = self.service.create_pairing({'device_id': 'phone', 'name': 'phone', 'capabilities': self.caps, 'confirm': True})
        self.token = self.service.pair({'protocol_version': 1, 'platform': 'android',
                                        'pairing_code': pairing['pairing_code'], 'capabilities': dict.fromkeys(self.caps, 1)})['token']
        self.ui_caps = self.caps + list(UI_ACTIONS)
        self.service.capabilities({'device_id': 'phone', 'capabilities': self.ui_caps, 'confirm': True})
        self.service.hello(self.token, {'protocol_version': 1, 'capabilities': dict.fromkeys(self.ui_caps, 1)})

    def test_ui_actions_use_generic_transport_and_device_scoped_receipts(self):
        actions = [
            ('app.launch', {'package': 'com.android.settings'}),
            ('ui.observe', {}),
            ('ui.tap', {'observation_id': 'screen1', 'element_id': '0.1'}),
            ('ui.input', {'observation_id': 'screen2', 'element_id': '0.2', 'text': '电池'}),
            ('ui.swipe', {'observation_id': 'screen3', 'start_x': 100, 'start_y': 800, 'end_x': 100, 'end_y': 200}),
            ('ui.back', {'observation_id': 'screen4'}),
        ]
        for cap, args in actions:
            cid = self.runtime.call('phone', cap, args)['result']['command_id']
            command = self.service.poll(self.token)['command']
            self.assertEqual((command['command_id'], command['capability'], command['arguments']), (cid, cap, args))
            result = {'performed': True} if cap != 'ui.observe' else {
                'observation_id': 'screen1', 'package': 'com.android.settings', 'nodes': [], 'truncated': False}
            self.service.receipt(self.token, {'command_id': cid, 'status': 'succeeded', 'result': result})
            self.assertEqual(self.store.command('phone', cid)['result'], result)

    def test_mutations_need_observation_and_bounded_coordinates(self):
        for cap, args in [('ui.tap', {'element_id': '0'}), ('ui.input', {'text': 'x'}),
                          ('ui.swipe', {'observation_id': 's', 'start_x': -1, 'start_y': 1, 'end_x': 1, 'end_y': 2}),
                          ('app.launch', {'package': 'com.android.settings', 'shell': 'anything'})]:
            with self.assertRaises(ValidationError):
                self.runtime.call('phone', cap, args)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM terminal_commands').fetchone()[0], 0)

    def test_observations_respect_screen_policy_and_writes_respect_read_only(self):
        self.runtime.hub.policy.config.allow_screen_capture = False
        with self.assertRaises(PolicyDenied):
            self.runtime.call('phone', 'ui.observe', {})
        self.runtime.hub.policy.config.allow_screen_capture = True
        self.runtime.hub.policy.config.mode = 'read_only'
        self.runtime.call('phone', 'ui.observe', {})
        with self.assertRaises(PolicyDenied):
            self.runtime.call('phone', 'ui.tap', {'observation_id': 'x', 'element_id': '0'})

    def test_invalid_receipt_does_not_complete_command_and_stale_failure_is_visible(self):
        cid = self.runtime.call('phone', 'ui.observe', {})['result']['command_id']
        self.service.poll(self.token)
        with self.assertRaises(ValueError):
            self.service.receipt(self.token, {'command_id': cid, 'status': 'succeeded', 'result': {'nodes': []}})
        self.assertEqual(self.store.command('phone', cid)['status'], 'leased')
        self.service.receipt(self.token, {'command_id': cid, 'status': 'failed', 'result': {'error': 'device_locked'}})
        cid = self.runtime.call('phone', 'ui.tap', {'observation_id': 'old', 'element_id': '0'})['result']['command_id']
        self.service.poll(self.token)
        with self.assertRaises(ValueError):
            self.service.receipt(self.token, {'command_id': cid, 'status': 'succeeded', 'result': {}})
        receipt = {'command_id': cid, 'status': 'failed', 'result': {'error': 'stale_observation'}}
        self.service.receipt(self.token, receipt)
        self.service.receipt(self.token, receipt)
        self.assertIsNone(self.service.poll(self.token)['command'])

    def test_ui_goal_is_delegated_to_agent_not_silently_replaced_by_battery_telemetry(self):
        result = self.runtime.intent('打开设置查看电池信息')
        self.assertEqual(result['execution'], 'planning_required')
        self.assertIn('ui.tap', result['available_capabilities'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM terminal_commands').fetchone()[0], 0)
        result = self.runtime.intent('让手机播报这句话：打开设置查看电池信息')
        self.assertEqual(self.store.command('phone', result['command_id'])['capability'], 'speak')

    def test_consent_progress_is_not_completion_and_cancel_blocks_resume(self):
        cid = self.runtime.call('phone', 'ui.observe', {})['result']['command_id']
        progress = {'command_id': cid, 'phase': 'waiting_for_user', 'message': 'Confirm application access'}
        with self.assertRaises(TerminalConflict):
            self.service.progress(self.token, progress)
        self.service.poll(self.token)
        self.service.progress(self.token, progress)
        command = self.store.command('phone', cid)
        self.assertEqual(command['status'], 'leased')
        self.assertEqual(command['result']['progress']['phase'], 'waiting_for_user')
        self.assertIsNone(command['completed_at'])
        with self.assertRaises(ValueError):
            self.service.progress(self.token, {**progress, 'phase': 'succeeded'})
        self.runtime.hub.policy.config.allow_screen_capture = False
        with self.assertRaises(PolicyDenied):
            self.service.progress(self.token, {**progress, 'phase': 'executing'})
        self.runtime.hub.policy.config.allow_screen_capture = True
        self.store.cancel('phone', cid)
        with self.assertRaises(TerminalConflict):
            self.service.progress(self.token, {**progress, 'phase': 'executing'})
