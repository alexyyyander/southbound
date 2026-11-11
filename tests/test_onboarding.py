from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from southbound.access.adapters.endpoint import EndpointAdapter
from southbound.access.errors import PolicyDenied
from southbound.access.hub import AccessHub
from southbound.missions import MissionService, MissionStore
from southbound.onboarding import setup_link
from southbound.runtime import SouthboundRuntime
from southbound.terminal_service import TerminalService
from southbound.terminals import TerminalConflict, TerminalStore, TerminalUnauthorized


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.store = TerminalStore(clock=lambda: self.now)
        self.runtime = SouthboundRuntime(hub=AccessHub([EndpointAdapter(store=self.store)]))
        self.addCleanup(self.runtime.close)
        self.missions = MissionService(self.runtime, store=MissionStore(Path(self.tmp.name)/'missions.db'))
        self.missions.worker.clock = lambda: self.now
        self.addCleanup(self.missions.close)
        self.service = TerminalService(self.runtime, self.missions)
        self.request = {'request_id': 'a'*32}

    def pair(self, device='phone', caps=None):
        caps = caps or ['notify', 'speak']
        grant = self.service.create_pairing({'device_id': device, 'capabilities': caps, 'confirm': True})
        client = self.service.pair({'protocol_version': 1, 'pairing_code': grant['pairing_code'], 'capabilities': dict.fromkeys(caps, 1)})
        return grant, client['token']

    def test_setup_secret_stays_in_fragment_and_url_is_validated(self):
        self.assertEqual(setup_link('http://192.168.1.2:8765/', 'a+/b'), 'http://192.168.1.2:8765/connect#code=a%2B%2Fb')
        for url in ('file:///tmp/a', 'https://u:p@host', 'https://host/path', 'https://host#x', 'https://host?q=1'):
            with self.assertRaises(ValueError):
                setup_link(url, 'code')

    def test_setup_status_expires_and_is_not_a_terminal_credential(self):
        grant, token = self.pair()
        status = self.service.setup_status(grant)
        self.assertTrue(status['paired'])
        self.assertNotIn('token_hash', status)
        self.assertNotIn(token, str(status))
        with self.assertRaises(TerminalUnauthorized):
            self.service.poll(grant['pairing_code'])
        self.now += 601
        with self.assertRaises(TerminalUnauthorized):
            self.service.setup_status(grant)
        self.service.poll(token)  # expiry of the installation link does not revoke the phone

    def test_new_setup_link_invalidates_old_and_revoke_removes_status_access(self):
        first = self.service.create_pairing({'device_id': 'phone', 'capabilities': ['notify'], 'confirm': True})
        second, _ = self.pair()
        with self.assertRaises(TerminalUnauthorized):
            self.service.setup_status(first)
        self.runtime.decommission('phone', confirm=True)
        with self.assertRaises(TerminalUnauthorized):
            self.service.setup_status(second)

    def test_fixed_test_retries_reuse_mission_and_only_own_status_is_visible(self):
        grant, token = self.pair()
        first = self.service.self_test(token, self.request)
        again = self.service.self_test(token, self.request)
        self.assertEqual(first['mission_id'], again['mission_id'])
        self.assertEqual(first['scheduled_at'], 1030)
        self.assertEqual(len(self.missions.list()), 1)
        self.assertEqual(self.service.setup_status(grant)['test']['mission_id'], first['mission_id'])
        _, other = self.pair('other')
        with self.assertRaises(KeyError):
            self.service.self_test(other, self.request, status_only=True)
        with self.assertRaises(ValueError):
            self.service.self_test(token, {**self.request, 'device_id': 'other'})
        with self.assertRaises(ValueError):
            self.service.self_test(token, {**self.request, 'actions': []})
        with self.assertRaises(TerminalConflict):
            self.service.self_test(token, {'request_id': 'b'*32})

    def test_fixed_test_needs_both_granted_capabilities_and_current_policy(self):
        _, limited = self.pair(caps=['notify'])
        with self.assertRaises(PolicyDenied):
            self.service.self_test(limited, self.request)
        _, token = self.pair('allowed')
        self.runtime.hub.policy.config.mode = 'read_only'
        with self.assertRaises(PolicyDenied):
            self.service.self_test(token, self.request)
        self.assertEqual(self.missions.list(), [])

    def test_receipts_advance_test_and_repair_cannot_see_previous_test(self):
        grant, token = self.pair()
        self.service.self_test(token, self.request)
        self.missions.worker.tick()
        self.assertIsNone(self.service.poll(token)['command'])
        self.now += 31
        for capability, result in [('notify', {'published': True}), ('speak', {'completed': True})]:
            self.missions.worker.tick()
            command = self.service.poll(token)['command']
            self.assertEqual(command['capability'], capability)
            self.service.receipt(token, {'command_id': command['command_id'], 'status': 'succeeded', 'result': result})
            self.now += 1
            self.missions.worker.tick()
            self.now += 1
        self.missions.worker.tick()
        status = self.service.self_test(token, self.request, status_only=True)
        self.assertEqual(status['status'], 'verified')
        self.assertEqual(len(status['receipts']), 2)
        self.assertEqual(self.service.setup_status(grant)['test']['status'], 'verified')
        with self.assertRaises(TerminalConflict):
            self.service.self_test(token, {'request_id': 'b'*32})
        self.runtime.decommission('phone', confirm=True)
        new_grant, new_token = self.pair()
        self.assertIsNone(self.service.setup_status(new_grant)['test'])
        with self.assertRaises(KeyError):
            self.service.self_test(new_token, self.request, status_only=True)

    def test_interruption_after_create_before_enqueue_resumes_same_test(self):
        _, token = self.pair()
        from unittest.mock import patch
        with patch.object(self.missions, 'run', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.service.self_test(token, self.request)
        result = self.service.self_test(token, self.request)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(len(self.missions.list()), 1)
