import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'wayland-kiosk'
sys.path.insert(0, str(APP))
from security_config import effective_ha_url, normalize_allowed_origins, validate_options, validate_url
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


class AllowedOriginsTests(unittest.TestCase):
    HA = 'http://ha.test:8123/lovelace'

    def test_default_allowlist_is_exactly_the_ha_origin(self):
        policy = policy_for({}, self.HA)
        self.assertEqual(policy['URLAllowlist'], ['http://ha.test:8123'])
        self.assertEqual(policy['URLBlocklist'], ['*'])
        policy = policy_for({'allowed_origins': []}, self.HA)
        self.assertEqual(policy['URLAllowlist'], ['http://ha.test:8123'])

    def test_entry_with_trailing_slash_is_added(self):
        policy = policy_for({'allowed_origins': ['https://web.weatherwise.app/']}, self.HA)
        self.assertEqual(policy['URLAllowlist'], ['http://ha.test:8123', 'https://web.weatherwise.app'])
        self.assertEqual(policy['URLBlocklist'], ['*'])

    def test_each_reject_case_names_the_entry(self):
        bad = [
            'https://example.com/path', 'https://example.com/a/b/', 'https://example.com?q=1',
            'https://example.com/#frag', 'https://user@example.com', 'https://user:pw@example.com',
            'https://exa mple.com', 'https://example.com\n', 'https://example.com\t',
            'https://*.example.com', 'https://example.*', '.example.com', 'https://.example.com',
            'ftp://example.com', 'file:///etc/passwd', 'javascript:alert(1)', 'example.com',
            'https://example.com:0', 'https://example.com:65536', 'https://example.com:abc',
            'https://', '', 42, None, ['https://example.com'],
        ]
        for entry in bad:
            with self.subTest(entry=entry), self.assertRaises(ValueError) as caught:
                normalize_allowed_origins([entry], True)
            self.assertIn('allowed_origins', str(caught.exception))
            if isinstance(entry, str) and entry:
                self.assertIn(repr(entry), str(caught.exception))

    def test_non_list_value_is_rejected(self):
        for value in ('https://example.com', {'a': 'b'}, 5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_allowed_origins(value, True)

    def test_two_bad_entries_are_both_reported(self):
        with self.assertRaises(ValueError) as caught:
            normalize_allowed_origins(['https://a.example/path', 'https://ok.example', 'https://*.b.example'], True)
        message = str(caught.exception)
        self.assertIn('a.example/path', message)
        self.assertIn('*.b.example', message)
        self.assertNotIn('ok.example', message)

    def test_http_requires_allow_http(self):
        with self.assertRaises(ValueError):
            normalize_allowed_origins(['http://lan.test:8080'], False)
        self.assertEqual(normalize_allowed_origins(['http://lan.test:8080'], True), ['http://lan.test:8080'])
        self.assertEqual(normalize_allowed_origins(['https://lan.test'], False), ['https://lan.test'])

    def test_normalization(self):
        self.assertEqual(normalize_allowed_origins(['HTTPS://Web.WeatherWise.App'], True), ['https://web.weatherwise.app'])
        self.assertEqual(normalize_allowed_origins(['https://a.test:443', 'http://b.test:80/'], True), ['https://a.test', 'http://b.test'])
        self.assertEqual(normalize_allowed_origins(['https://a.test:8443'], True), ['https://a.test:8443'])
        self.assertEqual(normalize_allowed_origins(['https://a.test', 'https://A.test/', 'https://a.test:443'], True), ['https://a.test'])
        self.assertEqual(normalize_allowed_origins(['https://[::1]:8443'], True), ['https://[::1]:8443'])

    def test_ha_origin_is_deduplicated(self):
        self.assertEqual(normalize_allowed_origins(['http://ha.test:8123', 'https://x.test'], True, self.HA), ['https://x.test'])
        policy = policy_for({'allowed_origins': ['http://HA.test:8123/']}, self.HA)
        self.assertEqual(policy['URLAllowlist'], ['http://ha.test:8123'])

    def test_unlocked_ignores_the_list_but_validation_still_runs(self):
        policy = policy_for({'lock_navigation': False, 'allowed_origins': ['https://x.test']}, self.HA)
        self.assertNotIn('URLAllowlist', policy)
        self.assertNotIn('URLBlocklist', policy)
        with self.assertRaises(ValueError):
            validate_options({'lock_navigation': False, 'allowed_origins': ['https://x.test/path']})
        validate_options({'lock_navigation': False, 'allowed_origins': ['https://x.test']})

    def test_validate_options_collects_every_error(self):
        options = {
            'ha_url': 'file:///etc/passwd', 'control_api_enabled': True, 'api_token': 'short',
            'allow_self_signed': True, 'auth_method': 'credentials',
            'allowed_origins': ['https://bad.test/path', 'https://*.bad.test'],
        }
        with self.assertRaises(ValueError) as caught:
            validate_options(options)
        message = str(caught.exception)
        for fragment in ('HTTP and HTTPS', 'api_token', 'self_signed_certificate', 'auth_method', 'bad.test/path', '*.bad.test'):
            self.assertIn(fragment, message)
        self.assertGreaterEqual(message.count('; '), 5)

    def test_credentials_error_names_auth_method(self):
        with self.assertRaises(ValueError) as caught:
            validate_options({'auth_method': 'credentials'})
        self.assertIn('auth_method', str(caught.exception))
        self.assertIn('ha_username', str(caught.exception))

    def test_valid_options_pass_with_defaults(self):
        validate_options({})
        validate_options({'allowed_origins': []})


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
