import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'wayland-kiosk'
sys.path.insert(0, str(APP))
from security_config import effective_ha_url, validate_options, validate_url
from browser_policy import policy_for
from credential_login import login_expression
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class ConfigurationTests(unittest.TestCase):
    def test_legacy_http_loopback_migrates(self):
        self.assertEqual(effective_ha_url({'ha_url': 'http://127.0.0.1:8123'}), 'http://homeassistant:8123')

    def test_http_can_be_disabled(self):
        with self.assertRaises(ValueError):
            validate_url('http://homeassistant:8123', False)

    def test_no_credentials_or_nonweb_schemes(self):
        for url in ('file:///etc/passwd', 'javascript:alert(1)', 'http://user:password@ha/', 'http://ha/\n'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_https_loopback_does_not_silently_change_identity(self):
        with self.assertRaises(ValueError):
            effective_ha_url({'ha_url': 'https://localhost:8123'})

    def test_enabled_api_requires_strong_token(self):
        for token in ('', 'short', 'x' * 31, 'x' * 32 + '\n'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                validate_options({'control_api_enabled': True, 'api_token': token})
        validate_options({'control_api_enabled': True, 'api_token': 'x' * 32})

    def test_self_signed_requires_explicit_certificate(self):
        with self.assertRaises(ValueError):
            validate_options({'allow_self_signed': True})

    def test_audio_toggle_and_navigation_are_independent(self):
        policy = policy_for({'audio_enabled': False, 'lock_navigation': False}, 'http://ha:8123')
        self.assertEqual(policy['DefaultSoundContentSetting'], 2)
        self.assertFalse(policy['AudioCaptureAllowed'])
        self.assertNotIn('URLAllowlist', policy)
        self.assertEqual(policy_for({'audio_enabled': True}, 'http://ha:8123')['DefaultSoundContentSetting'], 1)

    def test_http_policy_when_navigation_is_unlocked(self):
        policy = policy_for({'allow_http': False, 'lock_navigation': False}, 'https://ha/')
        self.assertEqual(policy['URLBlocklist'], ['http://*'])

    def test_credentials_are_json_encoded_with_atomic_origin_guard(self):
        expression = login_expression({'ha_url': 'https://ha.example', 'ha_username': 'test', 'ha_password': '\"; bad() //'})
        self.assertIn('location.origin !== expected', expression)
        self.assertIn("location.pathname !== '/auth/authorize'", expression)
        self.assertIn('\\\"; bad() //', expression)


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        spec = importlib.util.spec_from_file_location('review_server', APP / 'rest_server.py')
        self.server = importlib.util.module_from_spec(spec)
        with patch('security_config.load_options', return_value={'control_api_enabled': True, 'api_token': 'a' * 32}):
            spec.loader.exec_module(self.server)
        self.calls = 0
        async def harmless(data):
            self.calls += 1
            return {'success': True}
        self.server.ROUTES['probe'] = harmless
        app = web.Application()
        app.router.add_post('/api', self.server.api_handler)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_authentication_and_origin(self):
        for headers, expected in [({}, 401), ({'Authorization': 'Bearer ' + 'a' * 32, 'Origin': 'https://foreign.invalid'}, 403), ({'Authorization': 'Bearer ' + 'a' * 32}, 200)]:
            response = await self.client.post('/api', json={'command': 'probe'}, headers=headers)
            self.assertEqual(response.status, expected)
        self.assertEqual(self.calls, 1)

    async def test_body_shape_and_type(self):
        headers = {'Authorization': 'Bearer ' + 'a' * 32}
        response = await self.client.post('/api', data='{"command":"probe"}', headers=headers)
        self.assertEqual(response.status, 415)
        for body in ([], None, {'command': []}, {'command': 42}):
            response = await self.client.post('/api', json=body, headers=headers)
            self.assertIn(response.status, (400, 415))
        self.assertEqual(self.calls, 0)

    async def test_disabled_api_fails_closed(self):
        self.server.OPTIONS['control_api_enabled'] = False
        response = await self.client.post('/api', json={'command': 'probe'})
        self.assertEqual(response.status, 503)
